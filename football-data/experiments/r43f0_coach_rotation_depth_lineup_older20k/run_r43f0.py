#!/usr/bin/env python3
from __future__ import annotations

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
B0_DIR = ROOT / "football-data" / "experiments" / "r43b0_probabilistic_lineup_baseline"
R1_DIR = ROOT / "football-data" / "experiments" / "r43b0r1_probabilistic_lineup_eligible_split"
for p in (B0_DIR, R1_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
import run_r43b0_probabilistic_lineup as b0  # noqa: E402
import run_r43b0r1 as r1  # noqa: E402

SOURCE_R43E2_HEAD = "d1d161c0afb3070ef4dce1bc32c81a5e2e2d8e91"
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
LINEUPS_URL = f"{HF}/fixture_lineups.parquet?download=true"
EXPECTED_FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
EXPECTED_FP_SHA = "a315191ffac285a11597758c859cd88b97ea8aba89374a8fb299ee754a2f76ad"
EXPECTED_FPS_SHA = "e112f61265639d4271b368c0908fa2d0e2b04084e9667deefcbf5c3f730779e6"
EXPECTED_LINEUPS_SHA = "dcd9181f54df52193877ffd8a41b5d1097b404eb46b4890069c0e4d1c8c13abd"
MODEL_C = b0.MODEL_C
COACH_ROT_LOOKBACK = 12
DEPTH_LOOKBACK = 8
CHURN_LOOKBACK = 4

FATIGUE_NAMES = [
    "player_minutes_3d", "player_minutes_7d", "player_minutes_14d", "player_minutes_21d",
    "player_starts_7d", "player_starts_14d", "player_starts_21d",
    "player_appearances_7d", "player_appearances_14d",
    "team_matches_7d", "team_matches_14d", "team_matches_21d",
    "short_turnaround_le3", "dense_schedule_4plus_14d", "player_workload_share_14d",
]
CONTEXT_NAMES = [
    "depth_distinct_starters_8", "depth_distinct_seen_8", "bench_minutes_share_8",
    "xi_churn_mean_4", "xi_churn_last_1",
    "coach_rotation_mean", "coach_rotation_known", "coach_experience_log", "team_coach_tenure_log",
    "mins14_x_coach_rotation", "dense14_x_coach_rotation", "weighted_start_x_dense14",
    "start_last1_x_coach_rotation", "depth_seen_x_dense14", "workload_share14_x_depth_seen",
    "mins7_x_short_turnaround",
]
ALL_NAMES = b0.FEATURE_NAMES + FATIGUE_NAMES + CONTEXT_NAMES


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43f0/1"})
    with urllib.request.urlopen(req, timeout=600) as resp, path.open("wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_older20k_rows() -> tuple[list[dict], dict]:
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    download(FIX_URL, fp)
    download(STAT_URL, sp)
    if sha256(fp) != EXPECTED_FIX_SHA:
        raise RuntimeError("fixtures source drift")
    if sha256(sp) != EXPECTED_STAT_SHA:
        raise RuntimeError("match_stats source drift")
    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"],
    )
    st = pd.read_parquet(sp, columns=["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"])
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["kick"] = pd.to_datetime(df["date_utc"], utc=True)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > df["kick"]) & (df["home_xg"].between(0, 6)) & (df["away_xg"].between(0, 6))]
    df["date"] = df["kick"].dt.date.astype(str)
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    if len(df) < 80000:
        raise RuntimeError(f"need >=80000 valid rows, got {len(df)}")
    sl = df.iloc[-80000:-60000].copy()
    rows = [
        {
            "date": str(x.date),
            "fixture_id": int(x.id),
            "home_team": int(x.home_team_id),
            "away_team": int(x.away_team_id),
        }
        for x in sl.itertuples(index=False)
    ]
    return rows, {
        "fixtures_sha256": sha256(fp),
        "match_stats_sha256": sha256(sp),
        "valid_joined_rows": int(len(df)),
        "slice": "[-80000:-60000]",
        "rows": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
    }


def load_coach_map(fixture_ids: set[int]) -> tuple[dict[tuple[int, int], str], dict]:
    lp = DATA / "fixture_lineups.parquet"
    download(LINEUPS_URL, lp)
    if sha256(lp) != EXPECTED_LINEUPS_SHA:
        raise RuntimeError("fixture_lineups source drift")
    z = pd.read_parquet(lp, columns=["fixture_id", "team_id", "coach_name", "coach_api_id"])
    z = z[z["fixture_id"].isin(fixture_ids)].copy()
    out: dict[tuple[int, int], str] = {}
    for x in z.itertuples(index=False):
        if pd.notna(x.coach_api_id):
            cid = f"id:{int(x.coach_api_id)}"
        elif pd.notna(x.coach_name) and str(x.coach_name).strip():
            cid = "name:" + str(x.coach_name).strip().casefold()
        else:
            continue
        out[(int(x.fixture_id), int(x.team_id))] = cid
    return out, {
        "fixture_lineups_sha256": sha256(lp),
        "matched_coach_team_rows": len(out),
    }


def fatigue_features(hist: deque, pid: int, target_date: date) -> tuple[list[float], dict]:
    entries = []
    for e in hist:
        days = (target_date - e["date"]).days
        if days <= 0:
            continue
        entries.append((days, e["players"].get(pid)))

    def pmins(window: int) -> float:
        return sum(float(rec["minutes"]) for days, rec in entries if days <= window and rec is not None) / 90.0

    def pstarts(window: int) -> float:
        return float(sum(1 for days, rec in entries if days <= window and rec is not None and rec["start"]))

    def pappear(window: int) -> float:
        return float(sum(1 for days, rec in entries if days <= window and rec is not None))

    def tmatches(window: int) -> float:
        return float(sum(1 for e in hist if 0 < (target_date - e["date"]).days <= window))

    m3, m7, m14, m21 = pmins(3), pmins(7), pmins(14), pmins(21)
    t7, t14, t21 = tmatches(7), tmatches(14), tmatches(21)
    gap = min(365, (target_date - hist[-1]["date"]).days) if hist else 365
    share14 = m14 / max(t14, 1.0)
    vals = [
        m3, m7, m14, m21,
        pstarts(7), pstarts(14), pstarts(21), pappear(7), pappear(14),
        t7, t14, t21,
        1.0 if gap <= 3 else 0.0,
        1.0 if t14 >= 4 else 0.0,
        share14,
    ]
    return vals, {"m7": m7, "m14": m14, "t14": t14, "short": float(gap <= 3), "dense14": float(t14 >= 4), "share14": share14}


def team_context(hist: deque) -> dict:
    recent = list(hist)[-DEPTH_LOOKBACK:]
    starters = set()
    seen = set()
    nonstarter_minutes = 0.0
    total_minutes = 0.0
    xis = []
    for e in recent:
        xi = {pid for pid, rec in e["players"].items() if rec["start"]}
        xis.append(xi)
        starters.update(xi)
        seen.update(e["players"])
        for rec in e["players"].values():
            m = float(rec["minutes"])
            total_minutes += m
            if not rec["start"]:
                nonstarter_minutes += m
    churns = []
    for a, b in zip(xis[:-1], xis[1:]):
        if len(a) == 11 and len(b) == 11:
            churns.append(11 - len(a & b))
    churns = churns[-CHURN_LOOKBACK:]
    return {
        "depth_starters": len(starters) / 22.0 if recent else 0.5,
        "depth_seen": len(seen) / 32.0 if recent else 0.5,
        "bench_share": nonstarter_minutes / max(total_minutes, 1.0),
        "churn_mean": (float(np.mean(churns)) / 11.0) if churns else 0.0,
        "churn_last": (float(churns[-1]) / 11.0) if churns else 0.0,
    }


def build_examples(rows: list[dict], player_map: dict, coach_map: dict):
    team_hist = defaultdict(lambda: deque(maxlen=b0.LOOKBACK_POOL))
    team_last_coach: dict[int, str] = {}
    team_coach_tenure = defaultdict(int)
    coach_rot_hist = defaultdict(lambda: deque(maxlen=COACH_ROT_LOOKBACK))
    coach_exp = defaultdict(int)
    examples = []
    sides = []
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)

    for ds in sorted(by_date):
        target_date = date.fromisoformat(ds)
        pending = []
        for row in sorted(by_date[ds], key=lambda z: z["fixture_id"]):
            fid = row["fixture_id"]
            for side, tid in (("home", row["home_team"]), ("away", row["away_team"])):
                actual = player_map.get((fid, tid), {})
                actual_starters = {pid for pid, rec in actual.items() if rec["start"]}
                hist = team_hist[tid]
                if len(actual_starters) != 11 or len(hist) < b0.MIN_HISTORY:
                    continue
                last_seen = {}
                for idx, entry in enumerate(hist):
                    for pid in entry["players"]:
                        last_seen[pid] = idx
                candidates = sorted(last_seen, key=lambda pid: (last_seen[pid], pid), reverse=True)[:b0.MAX_CANDIDATES]
                if len(candidates) < 11:
                    continue
                unseen = actual_starters - set(candidates)
                ctx = team_context(hist)
                cid = team_last_coach.get(tid)
                rot = float(np.mean(coach_rot_hist[cid])) if cid and coach_rot_hist[cid] else 0.0
                rot_known = float(bool(cid and coach_rot_hist[cid]))
                cexp = math.log1p(coach_exp[cid]) if cid else 0.0
                tenure = math.log1p(team_coach_tenure[tid]) if cid else 0.0
                side_start = len(examples)
                for pid in candidates:
                    base_feat, diag = b0.player_history_features(hist, pid, target_date, len(candidates))
                    fat, fd = fatigue_features(hist, pid, target_date)
                    weighted_start = float(diag["weighted_start"])
                    start_last1 = float(base_feat[0])
                    extra = [
                        ctx["depth_starters"], ctx["depth_seen"], ctx["bench_share"],
                        ctx["churn_mean"], ctx["churn_last"],
                        rot, rot_known, cexp, tenure,
                        fd["m14"] * rot,
                        fd["dense14"] * rot,
                        weighted_start * fd["dense14"],
                        start_last1 * rot,
                        ctx["depth_seen"] * fd["dense14"],
                        fd["share14"] * ctx["depth_seen"],
                        fd["m7"] * fd["short"],
                    ]
                    feat = list(base_feat) + fat + extra
                    if len(feat) != len(ALL_NAMES):
                        raise RuntimeError("feature length drift")
                    examples.append({
                        "date": ds,
                        "fixture_id": fid,
                        "team_id": tid,
                        "side": side,
                        "phase": "pool",
                        "player_id": pid,
                        "y": int(pid in actual_starters),
                        "base_features": list(base_feat),
                        "context_features": feat,
                    })
                last_xi = {pid for pid, rec in hist[-1]["players"].items() if rec["start"]}
                sides.append({
                    "date": ds,
                    "fixture_id": fid,
                    "team_id": tid,
                    "side": side,
                    "phase": "pool",
                    "example_start": side_start,
                    "example_end": len(examples),
                    "actual_starters": sorted(actual_starters),
                    "unseen_actual_starters": sorted(unseen),
                    "last_xi_overlap": len(last_xi & actual_starters),
                    "history_n": len(hist),
                    "gap_days": int((target_date - hist[-1]["date"]).days),
                })
            pending.append(row)

        # Strict PIT: target lineup and target coach are revealed only after every prediction on the same date.
        for row in pending:
            fid = row["fixture_id"]
            for tid in (row["home_team"], row["away_team"]):
                players = player_map.get((fid, tid))
                if not players:
                    continue
                hist = team_hist[tid]
                current_xi = {pid for pid, rec in players.items() if rec["start"]}
                previous_xi = {pid for pid, rec in hist[-1]["players"].items() if rec["start"]} if hist else set()
                cid = coach_map.get((fid, tid))
                if cid:
                    if len(current_xi) == 11 and len(previous_xi) == 11:
                        coach_rot_hist[cid].append((11 - len(current_xi & previous_xi)) / 11.0)
                    coach_exp[cid] += 1
                    if team_last_coach.get(tid) == cid:
                        team_coach_tenure[tid] += 1
                    else:
                        team_last_coach[tid] = cid
                        team_coach_tenure[tid] = 1
                hist.append({"date": target_date, "players": players})
    return examples, sides


def fit_model(train: list[dict], key: str):
    model = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    model.fit(np.asarray([x[key] for x in train], dtype=float), np.asarray([x["y"] for x in train], dtype=int))
    return model


def score_sides(model, sides: list[dict], examples: list[dict], feature_key: str, prob_key: str) -> None:
    for side in sides:
        xs = examples[side["example_start"]:side["example_end"]]
        raw = model.predict_proba(np.asarray([x[feature_key] for x in xs], dtype=float))[:, 1]
        probs = b0.project_sum(raw)
        for x, q in zip(xs, probs):
            x[prob_key] = float(q)


def binary_metrics(xs: list[dict], key: str) -> dict:
    y = np.asarray([x["y"] for x in xs], dtype=float)
    p = np.clip(np.asarray([x[key] for x in xs], dtype=float), 1e-12, 1.0 - 1e-12)
    return {
        "n": len(xs),
        "logloss": float(np.mean(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))),
        "brier": float(np.mean((p - y) ** 2)),
    }


def side_metrics(sides: list[dict], examples: list[dict], key: str) -> dict:
    overlaps = []
    exact = 0
    unseen = []
    for side in sides:
        xs = examples[side["example_start"]:side["example_end"]]
        pred = {x["player_id"] for x in sorted(xs, key=lambda z: (z[key], z["player_id"]), reverse=True)[:11]}
        actual = set(side["actual_starters"])
        ov = len(pred & actual)
        overlaps.append(ov)
        exact += int(ov == 11)
        unseen.append(len(side["unseen_actual_starters"]))
    return {
        "n_sides": len(sides),
        "mean_xi_overlap": float(np.mean(overlaps)),
        "median_xi_overlap": float(np.median(overlaps)),
        "exact_11_sides": exact,
        "exact_11_rate": exact / len(sides),
        "mean_unseen_actual_starters": float(np.mean(unseen)),
    }


def time_blocks(sides: list[dict], examples: list[dict], base_key: str, cand_key: str, nblocks: int = 4) -> list[dict]:
    ordered = sorted(sides, key=lambda s: (s["date"], s["fixture_id"], s["team_id"]))
    out = []
    for idx in np.array_split(np.arange(len(ordered)), nblocks):
        ss = [ordered[int(i)] for i in idx]
        bm = side_metrics(ss, examples, base_key)
        cm = side_metrics(ss, examples, cand_key)
        out.append({
            "first_date": ss[0]["date"], "last_date": ss[-1]["date"], "n_sides": len(ss),
            "base_overlap": bm["mean_xi_overlap"], "candidate_overlap": cm["mean_xi_overlap"],
            "delta_overlap": cm["mean_xi_overlap"] - bm["mean_xi_overlap"],
        })
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, raw_meta = load_older20k_rows()
    fixture_ids = {r["fixture_id"] for r in rows}
    player_map, player_meta = b0.prepare_player_rows(fixture_ids)
    if player_meta["fixture_players_sha256"] != EXPECTED_FP_SHA:
        raise RuntimeError("fixture_players source drift")
    if player_meta["fixture_players_stats_flat_sha256"] != EXPECTED_FPS_SHA:
        raise RuntimeError("fixture_players_stats source drift")
    coach_map, coach_meta = load_coach_map(fixture_ids)
    examples, sides = build_examples(rows, player_map, coach_map)
    split = r1.assign_eligible_side_phases(sides, examples)

    train = [x for x in examples if x["phase"] == "train"]
    val = [x for x in examples if x["phase"] == "val"]
    test = [x for x in examples if x["phase"] == "test"]
    val_sides = [s for s in sides if s["phase"] == "val"]
    test_sides = [s for s in sides if s["phase"] == "test"]
    if not train or not val or not test or len(test_sides) < 1000:
        raise RuntimeError("undersized chronological eligible split")

    base_model = fit_model(train, "base_features")
    context_model = fit_model(train, "context_features")
    for ss in (val_sides, test_sides):
        score_sides(base_model, ss, examples, "base_features", "base_p")
        score_sides(context_model, ss, examples, "context_features", "context_p")

    vb, vc = binary_metrics(val, "base_p"), binary_metrics(val, "context_p")
    tb, tc = binary_metrics(test, "base_p"), binary_metrics(test, "context_p")
    vbo, vco = side_metrics(val_sides, examples, "base_p"), side_metrics(val_sides, examples, "context_p")
    tbo, tco = side_metrics(test_sides, examples, "base_p"), side_metrics(test_sides, examples, "context_p")
    blocks = time_blocks(test_sides, examples, "base_p", "context_p")
    pos = sum(x["delta_overlap"] > 0 for x in blocks)
    neg = sum(x["delta_overlap"] < 0 for x in blocks)
    gate = bool(
        tc["logloss"] < tb["logloss"]
        and tc["brier"] < tb["brier"]
        and tco["mean_xi_overlap"] > tbo["mean_xi_overlap"]
        and pos >= 3
        and neg <= 1
    )

    result = {
        "schema_version": "football3-r43f0-coach-rotation-depth-lineup-older20k-v1",
        "status": "COMPLETE",
        "classification": "FOURTH_DISJOINT_20K_COACH_ROTATION_DEPTH_FATIGUE_LINEUP_TEST",
        "formal_weight": 0,
        "source_r43e2_head": SOURCE_R43E2_HEAD,
        "governance": {
            "source_overlap_with_r43e0_e1_e2_scored_blocks": False,
            "target_result_used_as_feature": False,
            "target_current_match_lineup_used_as_feature": False,
            "target_current_match_lineup_used_only_as_evaluation_label": True,
            "target_current_match_coach_used_for_prediction": False,
            "same_date_updates_before_prediction": False,
            "future_schedule_used": False,
            "next_match_importance_used": False,
            "odds_used": False,
            "parameter_search": False,
            "feature_set_fixed_before_test": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "base_feature_count": len(b0.FEATURE_NAMES),
            "fatigue_feature_count": len(FATIGUE_NAMES),
            "context_feature_count": len(CONTEXT_NAMES),
            "candidate_feature_count": len(ALL_NAMES),
            "fatigue_features": FATIGUE_NAMES,
            "context_features": CONTEXT_NAMES,
            "coach_proxy": "last observed coach from strictly prior completed fixture",
            "coach_rotation": "mean realized XI changes versus prior team XI over coach prior completed fixtures only",
            "depth": "strict prior 8-match distinct starters/seen players and bench-minute share",
            "model": "StandardScaler + LogisticRegression binary P(start)",
            "model_C": MODEL_C,
            "probability_projection": "sum_i P(start_i)=11 per team-side; ranking preserved",
            "comparison": "same base starter model refit on identical train split versus fixed fatigue x coach-rotation x depth context stack",
        },
        "source": {**raw_meta, **player_meta, **coach_meta},
        "split": split,
        "sample": {
            "candidate_examples_train": len(train), "candidate_examples_val": len(val), "candidate_examples_test": len(test),
            "side_samples_test": len(test_sides),
        },
        "validation": {
            "base": vb, "candidate": vc,
            "delta_logloss": vc["logloss"] - vb["logloss"], "delta_brier": vc["brier"] - vb["brier"],
            "base_xi": vbo, "candidate_xi": vco,
            "delta_mean_xi_overlap": vco["mean_xi_overlap"] - vbo["mean_xi_overlap"],
        },
        "test": {
            "base": tb, "candidate": tc,
            "delta_logloss": tc["logloss"] - tb["logloss"], "delta_brier": tc["brier"] - tb["brier"],
            "base_xi": tbo, "candidate_xi": tco,
            "delta_mean_xi_overlap": tco["mean_xi_overlap"] - tbo["mean_xi_overlap"],
            "time_blocks": blocks, "positive_overlap_blocks": pos, "negative_overlap_blocks": neg,
        },
        "gate": {
            "require_test_logloss_improve": True,
            "require_test_brier_improve": True,
            "require_test_xi_overlap_improve": True,
            "min_positive_overlap_blocks": 3,
            "max_negative_overlap_blocks": 1,
            "passed": gate,
            "action": "KEEP_R43F0_CONTEXT_LINEUP_LAYER_FOR_SEPARATE_1X2_TRANSLATION" if gate else "DO_NOT_PROMOTE_R43F0_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "This is historical evidence on a fourth disjoint 20k era, not forward confirmation.",
            "Current-match coach identity is intentionally unavailable until a completed prior fixture establishes it.",
            "Future-match importance is excluded because historical prematch publication-time provenance for schedules/priority was not established.",
            "This stage changes no 1X2 probabilities and leaves the frozen R42L lock untouched.",
        ],
    }
    outp = OUT / "summary_r43f0_coach_rotation_depth_lineup.json"
    outp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43f0_coach_rotation_depth_lineup.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["source_overlap_with_r43e0_e1_e2_scored_blocks"] is False
    assert g["target_current_match_lineup_used_as_feature"] is False
    assert g["target_current_match_coach_used_for_prediction"] is False
    assert g["same_date_updates_before_prediction"] is False
    assert g["future_schedule_used"] is False
    assert g["parameter_search"] is False
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    assert d["sample"]["side_samples_test"] >= 1000
    print("R43F0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
