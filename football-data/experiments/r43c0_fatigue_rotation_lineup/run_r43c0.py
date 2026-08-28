#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
B0_DIR = ROOT / "football-data" / "experiments" / "r43b0_probabilistic_lineup_baseline"
R1_DIR = ROOT / "football-data" / "experiments" / "r43b0r1_probabilistic_lineup_eligible_split"
sys.path.insert(0, str(B0_DIR))
sys.path.insert(0, str(R1_DIR))
import run_r43b0_probabilistic_lineup as b0  # noqa: E402
import run_r43b0r1 as r1  # noqa: E402

SOURCE_R43B0R1_HEAD = "41536640b5b47dc91db524856eebc08c5a316d97"
MODEL_C = b0.MODEL_C

FATIGUE_FEATURE_NAMES = [
    "player_minutes_eq_3d",
    "player_minutes_eq_7d",
    "player_minutes_eq_14d",
    "player_minutes_eq_21d",
    "player_starts_7d",
    "player_starts_14d",
    "player_starts_21d",
    "player_appearances_7d",
    "player_appearances_14d",
    "team_matches_7d",
    "team_matches_14d",
    "team_matches_21d",
    "short_turnaround_le3",
    "dense_schedule_4plus_14d",
    "player_workload_share_14d",
]
ALL_FEATURE_NAMES = b0.FEATURE_NAMES + FATIGUE_FEATURE_NAMES


def fatigue_features(hist: deque, pid: int, target_date: date) -> list[float]:
    entries = []
    for e in hist:
        days = (target_date - e["date"]).days
        if days <= 0:
            continue
        rec = e["players"].get(pid)
        entries.append((days, rec))

    def pmins(window: int) -> float:
        return sum(float(rec["minutes"]) for days, rec in entries if days <= window and rec is not None) / 90.0

    def pstarts(window: int) -> float:
        return float(sum(1 for days, rec in entries if days <= window and rec is not None and rec["start"]))

    def pappear(window: int) -> float:
        return float(sum(1 for days, rec in entries if days <= window and rec is not None))

    def tmatches(window: int) -> float:
        return float(sum(1 for e in hist if 0 < (target_date - e["date"]).days <= window))

    m14 = pmins(14)
    t14 = tmatches(14)
    team_gap = min(365, (target_date - hist[-1]["date"]).days) if hist else 365
    workload_share = m14 / max(t14, 1.0)
    return [
        pmins(3), pmins(7), m14, pmins(21),
        pstarts(7), pstarts(14), pstarts(21),
        pappear(7), pappear(14),
        tmatches(7), t14, tmatches(21),
        1.0 if team_gap <= 3 else 0.0,
        1.0 if t14 >= 4 else 0.0,
        workload_share,
    ]


def build_examples(rows: list[dict], player_map: dict):
    team_hist = defaultdict(lambda: deque(maxlen=b0.LOOKBACK_POOL))
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
            for side, tid in (("home", r["home_team"]), ("away", r["away_team"])):
                actual = player_map.get((fid, tid), {})
                actual_starters = {pid for pid, rec in actual.items() if rec["start"]}
                hist = team_hist[tid]
                if len(actual_starters) != 11 or len(hist) < b0.MIN_HISTORY:
                    continue
                last_seen = {}
                for idx, e in enumerate(hist):
                    for pid in e["players"]:
                        last_seen[pid] = idx
                candidates = sorted(last_seen, key=lambda pid: (last_seen[pid], pid), reverse=True)[:b0.MAX_CANDIDATES]
                if len(candidates) < 11:
                    continue
                unseen = actual_starters - set(candidates)
                side_start = len(examples)
                for pid in candidates:
                    base_feat, _ = b0.player_history_features(hist, pid, d, len(candidates))
                    extra = fatigue_features(hist, pid, d)
                    examples.append({
                        "date": ds,
                        "fixture_id": fid,
                        "team_id": tid,
                        "side": side,
                        "phase": "pool",
                        "player_id": pid,
                        "y": 1 if pid in actual_starters else 0,
                        "base_features": base_feat,
                        "fatigue_features": base_feat + extra,
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
                    "gap_days": int((d - hist[-1]["date"]).days),
                })
            pending.append(r)

        # Same-date discipline: all predictions/examples are formed before updating any team history.
        for r in pending:
            fid = r["fixture_id"]
            for tid in (r["home_team"], r["away_team"]):
                players = player_map.get((fid, tid))
                if players:
                    team_hist[tid].append({"date": d, "players": players})
    return examples, sides


def fit_model(train: list[dict], key: str):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    m.fit(np.asarray([x[key] for x in train], dtype=float), np.asarray([x["y"] for x in train], dtype=int))
    return m


def score_sides(model, sides: list[dict], examples: list[dict], feature_key: str, prob_key: str):
    for s in sides:
        xs = examples[s["example_start"]:s["example_end"]]
        raw = model.predict_proba(np.asarray([x[feature_key] for x in xs], dtype=float))[:, 1]
        probs = b0.project_sum(raw)
        for x, q in zip(xs, probs):
            x[prob_key] = float(q)


def binary_metrics(xs: list[dict], key: str) -> dict:
    y = np.asarray([x["y"] for x in xs], dtype=float)
    p = np.clip(np.asarray([x[key] for x in xs], dtype=float), 1e-12, 1 - 1e-12)
    ll = float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))
    br = float(np.mean((p - y) ** 2))
    return {"n": int(len(xs)), "logloss": ll, "brier": br}


def side_metrics(sides: list[dict], examples: list[dict], key: str) -> dict:
    overlaps = []
    exact = 0
    unseen = []
    for s in sides:
        xs = examples[s["example_start"]:s["example_end"]]
        pred = {x["player_id"] for x in sorted(xs, key=lambda z: (z[key], z["player_id"]), reverse=True)[:11]}
        actual = set(s["actual_starters"])
        ov = len(pred & actual)
        overlaps.append(ov)
        exact += int(ov == 11)
        unseen.append(len(s["unseen_actual_starters"]))
    a = np.asarray(overlaps, dtype=float)
    return {
        "n_sides": len(sides),
        "mean_xi_overlap": float(a.mean()) if len(a) else None,
        "median_xi_overlap": float(np.median(a)) if len(a) else None,
        "exact_11_sides": exact,
        "exact_11_rate": exact / len(sides) if sides else None,
        "mean_unseen_actual_starters": float(np.mean(unseen)) if unseen else None,
    }


def time_blocks(sides: list[dict], examples: list[dict], base_key: str, cand_key: str, nblocks: int = 4) -> list[dict]:
    z = sorted(sides, key=lambda s: (s["date"], s["fixture_id"], s["team_id"]))
    out = []
    for chunk in np.array_split(np.arange(len(z)), nblocks):
        ss = [z[int(i)] for i in chunk]
        bm = side_metrics(ss, examples, base_key)
        cm = side_metrics(ss, examples, cand_key)
        out.append({
            "first_date": ss[0]["date"],
            "last_date": ss[-1]["date"],
            "n_sides": len(ss),
            "base_overlap": bm["mean_xi_overlap"],
            "candidate_overlap": cm["mean_xi_overlap"],
            "delta_overlap": cm["mean_xi_overlap"] - bm["mean_xi_overlap"],
        })
    return out


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = b0.load_matches()
    fixture_ids = {r["fixture_id"] for r in rows}
    player_map, source_meta = b0.prepare_player_rows(fixture_ids)
    examples, sides = build_examples(rows, player_map)
    split = r1.assign_eligible_side_phases(sides, examples)

    train = [x for x in examples if x["phase"] == "train"]
    val = [x for x in examples if x["phase"] == "val"]
    test = [x for x in examples if x["phase"] == "test"]
    val_sides = [s for s in sides if s["phase"] == "val"]
    test_sides = [s for s in sides if s["phase"] == "test"]
    if not train or not val or not test or len(test_sides) < 1000:
        raise RuntimeError("undersized chronological split")

    base_model = fit_model(train, "base_features")
    fatigue_model = fit_model(train, "fatigue_features")
    for ss in (val_sides, test_sides):
        score_sides(base_model, ss, examples, "base_features", "base_model_p")
        score_sides(fatigue_model, ss, examples, "fatigue_features", "fatigue_model_p")

    vb = binary_metrics(val, "base_model_p")
    vc = binary_metrics(val, "fatigue_model_p")
    tb = binary_metrics(test, "base_model_p")
    tc = binary_metrics(test, "fatigue_model_p")
    vbo = side_metrics(val_sides, examples, "base_model_p")
    vco = side_metrics(val_sides, examples, "fatigue_model_p")
    tbo = side_metrics(test_sides, examples, "base_model_p")
    tco = side_metrics(test_sides, examples, "fatigue_model_p")
    blocks = time_blocks(test_sides, examples, "base_model_p", "fatigue_model_p")
    pos = sum(x["delta_overlap"] > 0 for x in blocks)
    neg = sum(x["delta_overlap"] < 0 for x in blocks)

    passed = bool(
        tc["logloss"] < tb["logloss"]
        and tc["brier"] < tb["brier"]
        and tco["mean_xi_overlap"] >= tbo["mean_xi_overlap"]
        and pos >= 2
        and neg <= 1
    )

    result = {
        "schema_version": "football3-r43c0-fatigue-rotation-lineup-v1",
        "status": "COMPLETE",
        "classification": "STRICT_CHRONOLOGICAL_PAST_WORKLOAD_ROTATION_SIGNAL_TEST",
        "formal_weight": 0,
        "source_r43b0r1_head": SOURCE_R43B0R1_HEAD,
        "governance": {
            "target_result_used_as_feature": False,
            "target_current_match_lineup_used_as_feature": False,
            "target_current_match_lineup_used_only_as_evaluation_label": True,
            "same_date_updates_before_prediction": False,
            "future_schedule_used": False,
            "next_match_importance_used": False,
            "injury_status_used": False,
            "odds_used": False,
            "random_split": False,
            "parameter_search": False,
            "model_C_changed_from_r43b0r1": False,
            "fatigue_feature_set_fixed_before_test_run": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "base_feature_count": len(b0.FEATURE_NAMES),
            "fatigue_feature_count": len(FATIGUE_FEATURE_NAMES),
            "fatigue_features": FATIGUE_FEATURE_NAMES,
            "candidate_feature_count": len(ALL_FEATURE_NAMES),
            "windows_days": [3, 7, 14, 21],
            "workload_source": "strictly prior fixture player minutes/start history only",
            "model": "StandardScaler + LogisticRegression binary P(start)",
            "model_C": MODEL_C,
            "probability_projection": "sum_i P(start_i)=11 per team-side; ranking preserved",
            "comparison": "R43B0R1 feature model re-fit on identical train split versus same model plus fixed fatigue/rotation features",
        },
        "source": source_meta,
        "split": split,
        "sample": {
            "candidate_examples_train": len(train),
            "candidate_examples_val": len(val),
            "candidate_examples_test": len(test),
            "side_samples_test": len(test_sides),
        },
        "validation": {
            "r43b0r1_refit": vb,
            "fatigue_candidate": vc,
            "delta_logloss": vc["logloss"] - vb["logloss"],
            "delta_brier": vc["brier"] - vb["brier"],
            "base_xi": vbo,
            "candidate_xi": vco,
            "delta_mean_xi_overlap": vco["mean_xi_overlap"] - vbo["mean_xi_overlap"],
        },
        "test": {
            "r43b0r1_refit": tb,
            "fatigue_candidate": tc,
            "delta_logloss": tc["logloss"] - tb["logloss"],
            "delta_brier": tc["brier"] - tb["brier"],
            "base_xi": tbo,
            "candidate_xi": tco,
            "delta_mean_xi_overlap": tco["mean_xi_overlap"] - tbo["mean_xi_overlap"],
            "time_blocks": blocks,
            "positive_overlap_blocks": pos,
            "negative_overlap_blocks": neg,
        },
        "gate": {
            "require_test_logloss_improve": True,
            "require_test_brier_improve": True,
            "require_test_xi_overlap_nonworse": True,
            "min_positive_overlap_blocks": 2,
            "max_negative_overlap_blocks": 1,
            "passed": passed,
            "action": "PROMOTE_PAST_WORKLOAD_FATIGUE_SIGNAL_TO_R43_AVAILABILITY_STACK" if passed else "DO_NOT_PROMOTE_R43C0_AND_DO_NOT_RETUNE_ON_TEST",
        },
        "limitations": [
            "This tests whether past minutes and schedule density improve starter/rotation probability, not direct physical performance degradation.",
            "No future schedule or next-match importance is used because historical publication-time provenance for schedules was not established in this stage.",
            "No injury layer is used; injury availability remains a separate PIT integration task.",
            "This stage changes no 1X2 probabilities and leaves R42L untouched.",
        ],
    }
    p = OUT / "summary_r43c0_fatigue_rotation_lineup.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43c0_fatigue_rotation_lineup.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["target_result_used_as_feature"] is False
    assert g["target_current_match_lineup_used_as_feature"] is False
    assert g["same_date_updates_before_prediction"] is False
    assert g["future_schedule_used"] is False
    assert g["parameter_search"] is False
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    assert d["sample"]["side_samples_test"] >= 1000
    print("R43C0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
