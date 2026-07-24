#!/usr/bin/env python3
"""V6.18.1b controlled increment audit.

V6.18.1 compared:
  calibration = log formal P(T)
  shot        = log formal P(T) + competition one-hot + lagged shot features
Therefore shot-vs-calibration conflated competition fixed effects with shot process.

This audit inserts the missing control:
  A formal
  B calibration: log formal P(T)
  C competition: log formal P(T) + competition one-hot
  D shot:        log formal P(T) + competition one-hot + lagged shots/SOT/corners

Primary increment of interest is D-C. Test season remains 2025/26 and is explicitly
post-hoc diagnostic, not promotion evidence. Hyperparameters are selected only on
2024/25 validation using 2022/23+2023/24 training. A matched-C=0.01 comparison is also
reported because V6.18.1 selected C=0.01 before this control audit.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as fix

OUT = base.ROOT / "manifests" / "v6_total_shot_increment_control_v6181b_status.json"
TRAIN_SEASONS = {"2022/23", "2023/24"}
VALID_SEASON = "2024/25"
TEST_SEASON = "2025/26"
MATCHED_C = 0.01


def xvec(row, mode, shot_names, comps):
    x = [math.log(max(base.EPS, float(v))) for v in row["formal"]]
    if mode in {"competition", "shot"}:
        x.extend(1.0 if row["competition_id"] == c else 0.0 for c in comps)
    if mode == "shot":
        x.extend(float(row["shots"][k]) for k in shot_names)
    return x


def fit(rows, mode, c_value, shot_names, comps):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    X = [xvec(r, mode, shot_names, comps) for r in rows]
    y = [int(r["actual"]) for r in rows]
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=float(c_value), max_iter=4000, solver="lbfgs"),
    )
    model.fit(X, y)
    return model


def probs(model, row, mode, shot_names, comps):
    arr = model.predict_proba([xvec(row, mode, shot_names, comps)])[0]
    classes = list(model.classes_)
    p = [0.0] * (base.TOTAL_CAP + 1)
    for cls, v in zip(classes, arr):
        p[int(cls)] = float(v)
    s = sum(p)
    return [v / s for v in p]


def metrics(rows, getter):
    return base.metrics(rows, getter)


def select(train, valid, mode, shot_names, comps):
    baseline = metrics(valid, lambda r: r["formal"])
    board = []
    for c in base.CANDIDATE_C:
        model = fit(train, mode, c, shot_names, comps)
        m = metrics(valid, lambda r, model=model: probs(model, r, mode, shot_names, comps))
        m["C"] = c
        m["proper_noninferior"] = bool(m["rps"] <= baseline["rps"] and m["logloss"] <= baseline["logloss"])
        board.append(m)
    eligible = [m for m in board if m["proper_noninferior"]]
    chosen = max(eligible, key=lambda m: (m["top1_rate"], -m["rps"], -m["logloss"], -m["C"])) if eligible else None
    return baseline, board, chosen


def delta(a, b):
    return {
        "top1_rate": a["top1_rate"] - b["top1_rate"],
        "rps": a["rps"] - b["rps"],
        "logloss": a["logloss"] - b["logloss"],
    }


def evaluate_selected(train, valid, test, mode, shot_names, comps):
    vb, board, chosen = select(train, valid, mode, shot_names, comps)
    result = {"validation_baseline": vb, "leaderboard": board, "selected": chosen}
    if chosen is None:
        result["status"] = "NO_PROPER_NONINFERIOR_CANDIDATE"
        return result
    model = fit(train + valid, mode, chosen["C"], shot_names, comps)
    tm = metrics(test, lambda r: probs(model, r, mode, shot_names, comps))
    result.update({"status": "PASS", "test": tm})
    return result


def evaluate_matched(train, valid, test, mode, shot_names, comps):
    model = fit(train + valid, mode, MATCHED_C, shot_names, comps)
    return metrics(test, lambda r: probs(model, r, mode, shot_names, comps))


def main():
    raw, _ = base.raw_stat_matches()
    lookup, _ = fix.lagged_shot_lookup_fixed(raw)
    rows, _ = base.formal_rows(lookup)
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == VALID_SEASON]
    test = [r for r in rows if r["season"] == TEST_SEASON]
    shot_names, comps = base.feature_names(rows)
    if min(len(train), len(valid), len(test)) < 1000:
        raise RuntimeError(f"insufficient rows train={len(train)} valid={len(valid)} test={len(test)}")

    formal_test = metrics(test, lambda r: r["formal"])
    selected = {}
    matched = {}
    for mode in ("calibration", "competition", "shot"):
        selected[mode] = evaluate_selected(train, valid, test, mode, shot_names, comps)
        matched[mode] = evaluate_matched(train, valid, test, mode, shot_names, comps)

    matched_deltas = {
        "competition_minus_calibration": delta(matched["competition"], matched["calibration"]),
        "shot_minus_competition": delta(matched["shot"], matched["competition"]),
        "shot_minus_calibration": delta(matched["shot"], matched["calibration"]),
        "shot_minus_formal": delta(matched["shot"], formal_test),
    }
    selected_deltas = {}
    if selected["competition"].get("test") and selected["shot"].get("test"):
        selected_deltas["shot_minus_competition"] = delta(selected["shot"]["test"], selected["competition"]["test"])
    if selected["calibration"].get("test") and selected["competition"].get("test"):
        selected_deltas["competition_minus_calibration"] = delta(selected["competition"]["test"], selected["calibration"]["test"])

    payload = {
        "schema_version": "V6.18.1b-shot-increment-control-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "POST_HOC_CONFOUND_CONTROL_NOT_PROMOTION_EVIDENCE",
        "confound_found": "V6.18.1 shot arm added both competition one-hot and shot features while calibration arm added neither",
        "design": {
            "train_seasons": sorted(TRAIN_SEASONS),
            "validation_season": VALID_SEASON,
            "test_season": TEST_SEASON,
            "candidate_C": list(base.CANDIDATE_C),
            "matched_C": MATCHED_C,
            "primary_increment": "shot minus competition",
            "test_used_for_selection": False,
        },
        "rows": {"train": len(train), "validation": len(valid), "test": len(test)},
        "formal_test": formal_test,
        "selected_arms": selected,
        "selected_deltas": selected_deltas,
        "matched_C_0_01": matched,
        "matched_deltas": matched_deltas,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "v6181_shot_only_causal_claim_forbidden_until_this_control_is_read": True,
            "no_2025_26_result_may_change_parameters": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"formal_test": formal_test, "selected": {k: {"status": v["status"], "selected": v.get("selected"), "test": v.get("test")} for k, v in selected.items()}, "matched": matched, "matched_deltas": matched_deltas}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
