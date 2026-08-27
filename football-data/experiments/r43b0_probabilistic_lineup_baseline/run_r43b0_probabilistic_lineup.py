#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import urllib.request
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9B_DIR = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf"
R9B_SNAPSHOT = R9B_DIR / "data" / "matches_r9b_xg_20000.csv"

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FP_URL = f"{HF}/fixture_players.parquet?download=true"
FPS_URL = f"{HF}/fixture_players_stats_flat.parquet?download=true"

SOURCE_PARENT = "677875fc21e47d59f29a3e33a9883940d5e99eea"
LOOKBACK_POOL = 20
FEATURE_LOOKBACK = 8
DECAY = 0.78
SMOOTH = 0.50
MIN_HISTORY = 3
MAX_CANDIDATES = 32
BURN_FIXTURES = 4000
TRAIN_FIXTURES = 8000
VAL_FIXTURES = 4000
MODEL_C = 0.25
EPS = 1e-8
ROLES = ("G", "D", "M", "F")

FEATURE_NAMES = [
    "start_last1", "seen_last1", "minutes_last1",
    "start_rate_2", "start_rate_4", "start_rate_8",
    "seen_rate_2", "seen_rate_4", "seen_rate_8",
    "weighted_start_8", "weighted_seen_8",
    "minutes_mean_3", "minutes_mean_8",
    "days_since_seen", "days_since_start", "consecutive_starts",
    "history_n", "team_gap_days", "season_gap_45", "pool_size",
    "role_G", "role_D", "role_M", "role_F", "role_unknown",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, path: Path) -> None:
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43b0/1"})
    with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def norm_role(x) -> str | None:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    s = str(x).strip().upper()
    if not s:
        return None
    c = s[0]
    return c if c in ROLES else None


def load_matches() -> list[dict]:
    if not R9B_SNAPSHOT.exists():
        raise RuntimeError("R9b frozen snapshot missing")
    rows = []
    with R9B_SNAPSHOT.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "date": r["date"],
                "fixture_id": int(r["game_id"]),
                "home_team": int(r["home_team"]),
                "away_team": int(r["away_team"]),
            })
    rows.sort(key=lambda x: (x["date"], x["fixture_id"]))
    if len(rows) != 20000:
        raise RuntimeError(f"expected 20000 R9b rows, got {len(rows)}")
    return rows


def split_dates(rows: list[dict]) -> dict[int, str]:
    # Exact chronological fixture split, moving boundaries to the next date to avoid same-date leakage.
    n = len(rows)
    cuts = [BURN_FIXTURES, BURN_FIXTURES + TRAIN_FIXTURES, BURN_FIXTURES + TRAIN_FIXTURES + VAL_FIXTURES]
    adj = []
    for c in cuts:
        i = min(max(1, c), n - 1)
        while i < n and rows[i]["date"] == rows[i - 1]["date"]:
            i += 1
        adj.append(i)
    b1, b2, b3 = adj
    phase = {}
    for i, r in enumerate(rows):
        if i < b1:
            z = "burn"
        elif i < b2:
            z = "train"
        elif i < b3:
            z = "val"
        else:
            z = "test"
        phase[r["fixture_id"]] = z
    return phase


def prepare_player_rows(fixture_ids: set[int]) -> tuple[dict[tuple[int, int], dict[int, dict]], dict]:
    DATA.mkdir(parents=True, exist_ok=True)
    fp_path = DATA / "fixture_players.parquet"
    fps_path = DATA / "fixture_players_stats_flat.parquet"
    download(FP_URL, fp_path)
    download(FPS_URL, fps_path)

    fp = pd.read_parquet(fp_path, columns=["fixture_id", "team_id", "player_id", "is_starter", "position", "minutes"])
    fp = fp[fp["fixture_id"].isin(fixture_ids)].copy()
    fps = pd.read_parquet(fps_path, columns=["fixture_id", "player_id", "games_minutes", "games_position"])
    fps = fps[fps["fixture_id"].isin(fixture_ids)].drop_duplicates(["fixture_id", "player_id"], keep="last")
    fp = fp.merge(fps, on=["fixture_id", "player_id"], how="left")
    fp["is_starter"] = fp["is_starter"].fillna(False).astype(bool)
    m1 = pd.to_numeric(fp["minutes"], errors="coerce")
    m2 = pd.to_numeric(fp["games_minutes"], errors="coerce")
    fp["best_minutes"] = m1.where(m1.notna(), m2).fillna(0.0).clip(lower=0.0, upper=130.0)
    fp["role"] = fp["position"].map(norm_role)
    fp.loc[fp["role"].isna(), "role"] = fp.loc[fp["role"].isna(), "games_position"].map(norm_role)

    grouped: dict[tuple[int, int], dict[int, dict]] = {}
    duplicate_rows = 0
    for (fid, tid), g in fp.groupby(["fixture_id", "team_id"], sort=False):
        players = {}
        for r in g.itertuples(index=False):
            pid = int(r.player_id)
            if pid in players:
                duplicate_rows += 1
                # Keep a starter row over a nonstarter duplicate; otherwise keep the larger minutes value.
                old = players[pid]
                if bool(r.is_starter) and not old["start"]:
                    players[pid] = {"start": True, "minutes": float(r.best_minutes), "role": r.role}
                elif float(r.best_minutes) > old["minutes"]:
                    old["minutes"] = float(r.best_minutes)
                    if r.role:
                        old["role"] = r.role
            else:
                players[pid] = {"start": bool(r.is_starter), "minutes": float(r.best_minutes), "role": r.role}
        grouped[(int(fid), int(tid))] = players
    meta = {
        "fixture_players_sha256": sha256(fp_path),
        "fixture_players_stats_flat_sha256": sha256(fps_path),
        "source_rows_on_20k": int(len(fp)),
        "grouped_fixture_sides": int(len(grouped)),
        "duplicate_player_rows_collapsed": int(duplicate_rows),
        "starter_rows": int(fp["is_starter"].sum()),
        "starter_minutes_known_positive": int(((fp["is_starter"]) & (fp["best_minutes"] > 0)).sum()),
    }
    return grouped, meta


def player_history_features(hist: deque, pid: int, target_date: date, pool_size: int) -> tuple[list[float], dict]:
    xs = list(hist)
    n = len(xs)
    recent = xs[-FEATURE_LOOKBACK:]

    def cls(entry):
        rec = entry["players"].get(pid)
        return (1.0 if rec and rec["start"] else 0.0, 1.0 if rec else 0.0, float(rec["minutes"]) if rec else 0.0)

    vals = [cls(e) for e in xs]
    last = vals[-1] if vals else (0.0, 0.0, 0.0)

    def mean_start(k):
        q = vals[-k:]
        return float(np.mean([v[0] for v in q])) if q else 0.0

    def mean_seen(k):
        q = vals[-k:]
        return float(np.mean([v[1] for v in q])) if q else 0.0

    def mean_minutes(k):
        q = vals[-k:]
        return float(np.mean([v[2] for v in q])) / 90.0 if q else 0.0

    ws = wseen = wsum = 0.0
    for lag, entry in enumerate(reversed(recent)):
        w = DECAY ** lag
        a, s, _ = cls(entry)
        ws += w * a
        wseen += w * s
        wsum += w
    weighted_start = ws / wsum if wsum else 0.0
    weighted_seen = wseen / wsum if wsum else 0.0

    seen_dates = [e["date"] for e in xs if pid in e["players"]]
    start_dates = [e["date"] for e in xs if pid in e["players"] and e["players"][pid]["start"]]
    dseen = min(365, (target_date - seen_dates[-1]).days) if seen_dates else 365
    dstart = min(365, (target_date - start_dates[-1]).days) if start_dates else 365
    cons = 0
    for e in reversed(xs):
        rec = e["players"].get(pid)
        if rec and rec["start"]:
            cons += 1
        else:
            break
    cons = min(cons, 8)

    role = None
    for e in reversed(xs):
        rec = e["players"].get(pid)
        if rec and rec.get("role"):
            role = rec["role"]
            break
    team_gap = min(365, (target_date - xs[-1]["date"]).days) if xs else 365
    feat = [
        last[0], last[1], last[2] / 90.0,
        mean_start(2), mean_start(4), mean_start(8),
        mean_seen(2), mean_seen(4), mean_seen(8),
        weighted_start, weighted_seen,
        mean_minutes(3), mean_minutes(8),
        dseen / 180.0, dstart / 180.0, cons / 8.0,
        min(n, LOOKBACK_POOL) / LOOKBACK_POOL, team_gap / 180.0, 1.0 if team_gap >= 45 else 0.0, pool_size / MAX_CANDIDATES,
        *[1.0 if role == r else 0.0 for r in ROLES], 1.0 if role not in ROLES else 0.0,
    ]
    if len(feat) != len(FEATURE_NAMES):
        raise RuntimeError("feature length drift")
    diag = {"weighted_start": weighted_start, "weighted_seen": weighted_seen, "role": role}
    return feat, diag


def baseline_start_prob(hist: deque, pid: int) -> float:
    recent = list(hist)[-FEATURE_LOOKBACK:]
    ws = wsum = 0.0
    for lag, e in enumerate(reversed(recent)):
        w = DECAY ** lag
        rec = e["players"].get(pid)
        ws += w * (1.0 if rec and rec["start"] else 0.0)
        wsum += w
    # Beta smoothing fixed before scoring.
    return float((ws + SMOOTH) / (wsum + 2.0 * SMOOTH)) if wsum else 0.5


def project_sum(probs: np.ndarray, target_sum: float = 11.0) -> np.ndarray:
    p = np.clip(np.asarray(probs, dtype=float), EPS, 1.0 - EPS)
    if len(p) < int(target_sum):
        return p
    logits = np.log(p / (1.0 - p))
    lo, hi = -20.0, 20.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        q = 1.0 / (1.0 + np.exp(-(logits + mid)))
        if q.sum() > target_sum:
            hi = mid
        else:
            lo = mid
    q = 1.0 / (1.0 + np.exp(-(logits + (lo + hi) / 2.0)))
    return q


def build_examples(rows: list[dict], player_map: dict, phases: dict[int, str]):
    team_hist = defaultdict(lambda: deque(maxlen=LOOKBACK_POOL))
    examples = []
    sides = []
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)

    for ds in sorted(by_date):
        d = date.fromisoformat(ds)
        pending = []
        for r in sorted(by_date[ds], key=lambda z: z["fixture_id"]):
            fid = r["fixture_id"]
            phase = phases[fid]
            for side, tid in (("home", r["home_team"]), ("away", r["away_team"])):
                actual = player_map.get((fid, tid), {})
                actual_starters = {pid for pid, rec in actual.items() if rec["start"]}
                hist = team_hist[tid]
                if len(actual_starters) != 11 or len(hist) < MIN_HISTORY:
                    continue
                # Candidate identities come only from prior completed team appearances.
                last_seen = {}
                for idx, e in enumerate(hist):
                    for pid in e["players"]:
                        last_seen[pid] = idx
                candidates = sorted(last_seen, key=lambda pid: (last_seen[pid], pid), reverse=True)[:MAX_CANDIDATES]
                if len(candidates) < 11:
                    continue
                unseen = actual_starters - set(candidates)
                base_raw = np.array([baseline_start_prob(hist, pid) for pid in candidates], dtype=float)
                base_p = project_sum(base_raw)
                side_start = len(examples)
                for j, pid in enumerate(candidates):
                    feat, diag = player_history_features(hist, pid, d, len(candidates))
                    examples.append({
                        "date": ds,
                        "fixture_id": fid,
                        "team_id": tid,
                        "side": side,
                        "phase": phase,
                        "player_id": pid,
                        "y": 1 if pid in actual_starters else 0,
                        "features": feat,
                        "baseline_p": float(base_p[j]),
                        "weighted_start": float(diag["weighted_start"]),
                    })
                last_xi = {pid for pid, rec in hist[-1]["players"].items() if rec["start"]}
                sides.append({
                    "date": ds,
                    "fixture_id": fid,
                    "team_id": tid,
                    "side": side,
                    "phase": phase,
                    "example_start": side_start,
                    "example_end": len(examples),
                    "actual_starters": sorted(actual_starters),
                    "unseen_actual_starters": sorted(unseen),
                    "last_xi_overlap": len(last_xi & actual_starters),
                    "history_n": len(hist),
                    "gap_days": int((d - hist[-1]["date"]).days),
                })
            pending.append(r)

        # Same-date matches are all predicted before any same-date history update.
        for r in pending:
            fid = r["fixture_id"]
            d = date.fromisoformat(r["date"])
            for tid in (r["home_team"], r["away_team"]):
                actual = player_map.get((fid, tid), {})
                if actual:
                    team_hist[tid].append({"date": d, "fixture_id": fid, "players": actual})
    return examples, sides


def binary_metrics(examples: list[dict], key: str) -> dict:
    if not examples:
        return {"n": 0}
    ll = br = 0.0
    for x in examples:
        p = float(np.clip(x[key], EPS, 1.0 - EPS))
        y = int(x["y"])
        ll -= y * math.log(p) + (1 - y) * math.log(1 - p)
        br += (p - y) ** 2
    return {"n": len(examples), "logloss": ll / len(examples), "brier": br / len(examples)}


def side_metrics(sides: list[dict], examples: list[dict], key: str) -> dict:
    if not sides:
        return {"n_sides": 0}
    ovs = []
    unseen = []
    exact = 0
    for s in sides:
        xs = examples[s["example_start"]:s["example_end"]]
        ranked = sorted(xs, key=lambda x: (float(x[key]), x["player_id"]), reverse=True)[:11]
        pred = {x["player_id"] for x in ranked}
        actual = set(s["actual_starters"])
        ov = len(pred & actual)
        ovs.append(ov)
        unseen.append(len(s["unseen_actual_starters"]))
        exact += ov == 11
    return {
        "n_sides": len(sides),
        "mean_xi_overlap": float(np.mean(ovs)),
        "median_xi_overlap": float(np.median(ovs)),
        "exact_11_sides": int(exact),
        "exact_11_rate": float(exact / len(sides)),
        "mean_unseen_actual_starters": float(np.mean(unseen)),
        "sides_with_zero_unseen_actual_starters": int(np.sum(np.asarray(unseen) == 0)),
        "zero_unseen_rate": float(np.mean(np.asarray(unseen) == 0)),
    }


def last_xi_metrics(sides: list[dict]) -> dict:
    if not sides:
        return {"n_sides": 0}
    a = np.asarray([s["last_xi_overlap"] for s in sides], dtype=float)
    return {"n_sides": len(sides), "mean_xi_overlap": float(a.mean()), "median_xi_overlap": float(np.median(a)), "exact_11_sides": int(np.sum(a == 11)), "exact_11_rate": float(np.mean(a == 11))}


def time_blocks(test_sides: list[dict], examples: list[dict], nblocks: int = 4) -> list[dict]:
    ordered = sorted(test_sides, key=lambda s: (s["date"], s["fixture_id"], s["team_id"]))
    out = []
    for idxs in np.array_split(np.arange(len(ordered)), nblocks):
        ss = [ordered[int(i)] for i in idxs]
        if not ss:
            continue
        bm = side_metrics(ss, examples, "baseline_p")
        mm = side_metrics(ss, examples, "model_p")
        out.append({
            "first_date": ss[0]["date"], "last_date": ss[-1]["date"], "n_sides": len(ss),
            "baseline_overlap": bm["mean_xi_overlap"], "model_overlap": mm["mean_xi_overlap"],
            "delta_overlap": mm["mean_xi_overlap"] - bm["mean_xi_overlap"],
        })
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_matches()
    phases = split_dates(rows)
    fixture_ids = {r["fixture_id"] for r in rows}
    player_map, source_meta = prepare_player_rows(fixture_ids)
    examples, sides = build_examples(rows, player_map, phases)

    train = [x for x in examples if x["phase"] == "train"]
    val = [x for x in examples if x["phase"] == "val"]
    test = [x for x in examples if x["phase"] == "test"]
    if not train or not val or not test:
        raise RuntimeError("empty chronological split")

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0),
    )
    model.fit(np.asarray([x["features"] for x in train], dtype=float), np.asarray([x["y"] for x in train], dtype=int))

    # Generate model probabilities separately per side, then impose the football identity sum P(start)=11.
    for phase_name in ("val", "test"):
        ss = [s for s in sides if s["phase"] == phase_name]
        for s in ss:
            xs = examples[s["example_start"]:s["example_end"]]
            raw = model.predict_proba(np.asarray([x["features"] for x in xs], dtype=float))[:, 1]
            p = project_sum(raw)
            for x, q in zip(xs, p):
                x["model_p"] = float(q)
    # Train records are not used for reported challenger metrics; populate only to simplify serialization.
    for x in train:
        x["model_p"] = None

    val_sides = [s for s in sides if s["phase"] == "val"]
    test_sides = [s for s in sides if s["phase"] == "test"]
    val_b = binary_metrics(val, "baseline_p")
    val_m = binary_metrics(val, "model_p")
    test_b = binary_metrics(test, "baseline_p")
    test_m = binary_metrics(test, "model_p")
    val_bo = side_metrics(val_sides, examples, "baseline_p")
    val_mo = side_metrics(val_sides, examples, "model_p")
    test_bo = side_metrics(test_sides, examples, "baseline_p")
    test_mo = side_metrics(test_sides, examples, "model_p")
    test_last = last_xi_metrics(test_sides)
    blocks = time_blocks(test_sides, examples)
    pos_blocks = sum(1 for b in blocks if b["delta_overlap"] > 0)
    neg_blocks = sum(1 for b in blocks if b["delta_overlap"] < 0)

    passed = bool(
        len(test_sides) >= 1000
        and test_m["logloss"] < test_b["logloss"]
        and test_m["brier"] < test_b["brier"]
        and test_mo["mean_xi_overlap"] >= test_bo["mean_xi_overlap"]
        and pos_blocks >= 2
        and neg_blocks <= 1
    )

    result = {
        "schema_version": "football3-r43b0-probabilistic-starter-baseline-v1",
        "status": "COMPLETE",
        "classification": "STRICT_CHRONOLOGICAL_LAGGED_LINEUP_PROBABILITY_BASELINE_NO_TARGET_XI_FEATURE_USE",
        "formal_weight": 0,
        "source_parent": SOURCE_PARENT,
        "question": "Can a fixed player-level probabilistic starter model learned only from prior completed lineup/minute history improve next-match starter probabilities and expected-XI overlap versus the frozen R40C-style recency baseline?",
        "governance": {
            "target_result_used_as_feature": False,
            "target_current_match_lineup_used_as_feature": False,
            "target_current_match_lineup_used_only_as_evaluation_label": True,
            "same_date_updates_before_prediction": False,
            "current_injury_status_used": False,
            "retrospective_availability_status_used_as_feature": False,
            "closing_odds_used": False,
            "random_split": False,
            "parameter_search": False,
            "candidate_pool_prior_history_only": True,
            "probability_projection_rule": "single scalar logit offset per team-side so sum_i P(start_i)=11; preserves player ranking",
        },
        "design": {
            "candidate_pool_lookback_matches": LOOKBACK_POOL,
            "candidate_pool_max_players": MAX_CANDIDATES,
            "feature_lookback_matches": FEATURE_LOOKBACK,
            "recency_decay": DECAY,
            "baseline_beta_smoothing": SMOOTH,
            "minimum_prior_team_matches": MIN_HISTORY,
            "model": "StandardScaler + LogisticRegression binary P(start)",
            "model_C": MODEL_C,
            "features": FEATURE_NAMES,
            "note": "This B0 model estimates P(start), not a true P(bench): the current fixture_players source does not prove unused-bench membership. P(bench) waits for a PIT-safe full squad/availability source.",
        },
        "source": source_meta,
        "sample": {
            "candidate_examples_total": len(examples),
            "candidate_examples_train": len(train),
            "candidate_examples_val": len(val),
            "candidate_examples_test": len(test),
            "side_samples_total": len(sides),
            "side_samples_train": sum(s["phase"] == "train" for s in sides),
            "side_samples_val": len(val_sides),
            "side_samples_test": len(test_sides),
            "train_first_date": min(x["date"] for x in train),
            "train_last_date": max(x["date"] for x in train),
            "val_first_date": min(x["date"] for x in val),
            "val_last_date": max(x["date"] for x in val),
            "test_first_date": min(x["date"] for x in test),
            "test_last_date": max(x["date"] for x in test),
        },
        "validation": {
            "baseline_player_probability": val_b,
            "model_player_probability": val_m,
            "delta_logloss": val_m["logloss"] - val_b["logloss"],
            "delta_brier": val_m["brier"] - val_b["brier"],
            "baseline_xi": val_bo,
            "model_xi": val_mo,
            "delta_mean_xi_overlap": val_mo["mean_xi_overlap"] - val_bo["mean_xi_overlap"],
        },
        "test": {
            "baseline_player_probability": test_b,
            "model_player_probability": test_m,
            "delta_logloss": test_m["logloss"] - test_b["logloss"],
            "delta_brier": test_m["brier"] - test_b["brier"],
            "last_completed_xi": test_last,
            "baseline_weighted_xi": test_bo,
            "model_probabilistic_xi": test_mo,
            "delta_mean_xi_overlap_vs_weighted_baseline": test_mo["mean_xi_overlap"] - test_bo["mean_xi_overlap"],
            "delta_mean_xi_overlap_vs_last_xi": test_mo["mean_xi_overlap"] - test_last["mean_xi_overlap"],
            "time_blocks": blocks,
            "positive_overlap_blocks": pos_blocks,
            "negative_overlap_blocks": neg_blocks,
        },
        "gate": {
            "min_test_sides": 1000,
            "require_logloss_improve": True,
            "require_brier_improve": True,
            "require_xi_overlap_nonworse": True,
            "min_positive_overlap_blocks": 2,
            "max_negative_overlap_blocks": 1,
            "passed": passed,
            "action": "PROMOTE_R43B0_START_PROBABILITY_MECHANISM_TO_NEXT_AVAILABILITY_INTEGRATION_STAGE" if passed else "DO_NOT_PROMOTE_R43B0; DIAGNOSE_WITHOUT_TOUCHING_TEST_LABELS_FOR_SELECTION",
        },
        "limitations": [
            "The candidate pool can only contain players observed in prior completed matches; new transfers or newly promoted youth players can be unseen and are reported explicitly.",
            "fixture_players does not establish the full unused bench, so non-starter absence cannot be decomposed into bench vs unavailable in this B0 stage.",
            "This tests lineup probability quality only; no 1X2 probabilities are changed here.",
            "The current frozen R42L forward lock remains untouched.",
        ],
    }
    p = OUT / "summary_r43b0_probabilistic_lineup.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    p = OUT / "summary_r43b0_probabilistic_lineup.json"
    x = json.loads(p.read_text(encoding="utf-8"))
    assert x["status"] == "COMPLETE"
    assert x["formal_weight"] == 0
    assert x["governance"]["target_current_match_lineup_used_as_feature"] is False
    assert x["governance"]["same_date_updates_before_prediction"] is False
    assert x["governance"]["retrospective_availability_status_used_as_feature"] is False
    assert x["governance"]["random_split"] is False
    assert x["sample"]["side_samples_test"] >= 1000
    print("R43B0 probabilistic lineup contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
