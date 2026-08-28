#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "r43n0_c072k2_low_score_1x2_marginal_audit.json"
RECEIPT = HERE / "c072k2_confirmation_result.json"

sys.path.insert(0, str(HERE))
import evaluate_c072k2_joint_low_score_confirm as k2  # noqa: E402

SOURCE_K2_BLOB = "cefad42f4913221b46f76763e80f7dd018b9ba57"
SOURCE_K2_PASS_COMMIT = "a2b269dbe65b531d874620880694dd6ced0042cc"
RESULT_NAMES = ["home", "draw", "away"]


def outcome_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or len(y) != len(p):
        raise RuntimeError("bad 1X2 shape")
    if np.max(np.abs(p.sum(axis=1) - 1.0)) > 1e-10 or np.any(p <= 0):
        raise RuntimeError("invalid 1X2 probabilities")
    idx = np.arange(len(y))
    one = np.zeros_like(p)
    one[idx, y] = 1.0
    pred = np.argmax(p, axis=1)
    ll = -np.log(np.clip(p[idx, y], 1e-15, 1.0))
    br = np.sum((p - one) ** 2, axis=1)
    # RPS for ordered H-D-A classes.
    cp = np.cumsum(p[:, :2], axis=1)
    co = np.cumsum(one[:, :2], axis=1)
    rps = np.mean(np.sum((cp - co) ** 2, axis=1) / 2.0)
    picks = Counter(RESULT_NAMES[int(i)] for i in pred)
    hits = Counter(RESULT_NAMES[int(y[i])] for i in range(len(y)) if pred[i] == y[i])
    actual = Counter(RESULT_NAMES[int(i)] for i in y)
    draw_mask = y == 1
    return {
        "count": int(len(y)),
        "hits": int(np.sum(pred == y)),
        "top1_accuracy": float(np.mean(pred == y)),
        "logloss": float(np.mean(ll)),
        "brier": float(np.mean(br)),
        "rps": float(rps),
        "top1_picks": {k: int(picks.get(k, 0)) for k in RESULT_NAMES},
        "top1_hits": {k: int(hits.get(k, 0)) for k in RESULT_NAMES},
        "actuals": {k: int(actual.get(k, 0)) for k in RESULT_NAMES},
        "mean_draw_probability_on_actual_draws": float(np.mean(p[draw_mask, 1])) if np.any(draw_mask) else None,
    }


def delta(base: dict, cand: dict) -> dict:
    return {
        "hits": int(cand["hits"] - base["hits"]),
        "accuracy_pp": 100.0 * float(cand["top1_accuracy"] - base["top1_accuracy"]),
        "logloss": float(cand["logloss"] - base["logloss"]),
        "brier": float(cand["brier"] - base["brier"]),
        "rps": float(cand["rps"] - base["rps"]),
        "draw_pick_delta": int(cand["top1_picks"]["draw"] - base["top1_picks"]["draw"]),
        "draw_hit_delta": int(cand["top1_hits"]["draw"] - base["top1_hits"]["draw"]),
    }


def market_from_features(z) -> np.ndarray:
    os = z["open_strength"].to_numpy(float)
    od = z["open_drawness"].to_numpy(float)
    eh = np.exp(os)
    ed = np.exp(od + 0.5 * os)
    den = eh + ed + 1.0
    return np.column_stack([eh / den, ed / den, 1.0 / den])


def result_head_from_matrix(mat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mat = np.asarray(mat, dtype=float)
    known = np.zeros((len(mat), 3), dtype=float)
    for j, (h, a) in enumerate(k2.CELLS):
        r = 0 if h > a else 1 if h == a else 2
        known[:, r] += mat[:, j]
    tail = mat[:, k2.TAIL_INDEX].copy()
    return known, tail


def conditional_low_head(mat: np.ndarray) -> np.ndarray:
    known, _tail = result_head_from_matrix(mat)
    den = known.sum(axis=1, keepdims=True)
    if np.any(den <= 0):
        raise RuntimeError("zero known low-score mass")
    q = known / den
    q = np.clip(q, 1e-15, 1.0)
    q = q / q.sum(axis=1, keepdims=True)
    return q


def robust_top1(known: np.ndarray, tail: np.ndarray, y: np.ndarray) -> dict:
    calls = []
    for i in range(len(known)):
        winner = int(np.argmax(known[i]))
        other = [j for j in range(3) if j != winner]
        # Winner is invariant to every possible allocation of unresolved tail mass.
        if all(float(known[i, winner]) > float(known[i, j] + tail[i]) for j in other):
            calls.append((i, winner))
    picks = Counter(RESULT_NAMES[w] for _, w in calls)
    hits = Counter(RESULT_NAMES[int(y[i])] for i, w in calls if w == int(y[i]))
    return {
        "robust_count": int(len(calls)),
        "robust_coverage": float(len(calls) / len(y)) if len(y) else None,
        "robust_hits": int(sum(w == int(y[i]) for i, w in calls)),
        "robust_accuracy": float(np.mean([w == int(y[i]) for i, w in calls])) if calls else None,
        "robust_picks": {k: int(picks.get(k, 0)) for k in RESULT_NAMES},
        "robust_hits_by_class": {k: int(hits.get(k, 0)) for k in RESULT_NAMES},
    }


def run() -> dict:
    capture: dict = {}

    def tracer(frame, event, arg):
        if frame.f_code.co_name == "main" and frame.f_globals.get("SCHEMA") == k2.SCHEMA and event == "return":
            for key in ("z", "mats", "yhy", "low", "hys", "lows"):
                if key in frame.f_locals:
                    capture[key] = frame.f_locals[key]
        return tracer

    sys.settrace(tracer)
    try:
        k2.main()
    finally:
        sys.settrace(None)

    if not all(k in capture for k in ("z", "mats", "yhy", "low")):
        raise RuntimeError(f"C072K2 capture incomplete: {sorted(capture)}")
    z = capture["z"]
    mats = capture["mats"]
    yhy = np.asarray(capture["yhy"], dtype=int)
    low = np.asarray(capture["low"], dtype=bool)
    if len(z) != 1006 or int(np.sum(low)) != 990:
        raise RuntimeError(f"C072K2 cohort drift n={len(z)} low={int(np.sum(low))}")

    # Reproduce the committed PASS receipt before using any new marginal audit.
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    for name, key in (("BASE", "hybrid_BASE"), ("BOTH", "hybrid_BOTH")):
        m = k2.summarize(k2.generic_metrics(yhy, mats[name]))
        exp = receipt[key]
        for a, b in (("log_loss", "log_loss"), ("brier", "brier"), ("top1", "top1"), ("top3", "top3"), ("mean_rank", "mean_rank")):
            if not math.isclose(float(m[a]), float(exp[b]), rel_tol=0.0, abs_tol=1e-12):
                raise RuntimeError(f"source reproduction drift {name} {a}: {m[a]} != {exp[b]}")

    # Actual result class is used only after all frozen K2 probabilities are reconstructed.
    y_result = np.asarray([
        0 if int(r.home_goals) > int(r.away_goals) else 1 if int(r.home_goals) == int(r.away_goals) else 2
        for _, r in z.iterrows()
    ], dtype=int)
    market = market_from_features(z)

    low_idx = np.flatnonzero(low)
    low_metrics = {"market": outcome_metrics(y_result[low], market[low])}
    for name in ("BASE", "PT_ONLY", "D_ONLY", "BOTH"):
        p = conditional_low_head(mats[name])
        low_metrics[name] = outcome_metrics(y_result[low], p[low])

    robust = {}
    for name in ("BASE", "PT_ONLY", "D_ONLY", "BOTH"):
        known, tail = result_head_from_matrix(mats[name])
        robust[name] = robust_top1(known, tail, y_result)
        robust[name]["mean_unresolved_tail_probability"] = float(np.mean(tail))
        robust[name]["max_unresolved_tail_probability"] = float(np.max(tail))

    both_vs_base = delta(low_metrics["BASE"], low_metrics["BOTH"])
    both_vs_market = delta(low_metrics["market"], low_metrics["BOTH"])
    d_vs_base = delta(low_metrics["BASE"], low_metrics["D_ONLY"])
    pt_vs_base = delta(low_metrics["BASE"], low_metrics["PT_ONLY"])

    signal_present = bool(
        both_vs_base["hits"] >= 0
        and both_vs_base["logloss"] < 0
        and both_vs_base["brier"] < 0
        and both_vs_base["rps"] < 0
        and low_metrics["BOTH"]["top1_picks"]["draw"] > 0
    )

    out = {
        "schema_version": "football3-r43n0-c072k2-low-score-1x2-marginal-audit-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "classification": "POST_OUTCOME_DIAGNOSTIC_MARGINALIZATION_OF_ALREADY_FROZEN_C072K2_JOINT_SCORE_MATRICES",
        "question": "When the already-confirmed C072K2 T<=6 joint score cells are integrated into H/D/A, does the newer D|T structure actually improve low-score 1X2 and activate draw without assigning the unresolved 7+ tail?",
        "governance": {
            "source_k2_blob": SOURCE_K2_BLOB,
            "source_k2_pass_commit": SOURCE_K2_PASS_COMMIT,
            "source_exact_score_pass_reproduced_before_audit": True,
            "model_refit_changed": False,
            "features_changed": False,
            "parameter_search": False,
            "threshold_search": False,
            "draw_override": False,
            "tail7plus_allocated_to_result": False,
            "low_score_head_conditioned_on_T_le_6": True,
            "low_score_actual_subset_is_post_outcome_diagnostic": True,
            "formal_promotion_allowed": False,
        },
        "coverage": {
            "hybrid_rows": int(len(z)),
            "actual_T_le_6_rows": int(np.sum(low)),
            "unresolved_tail_definition": "C072K2 TAIL_7PLUS; never split across H/D/A in this audit",
        },
        "low_score_conditional_1x2": low_metrics,
        "contrasts": {
            "BOTH_minus_BASE": both_vs_base,
            "D_ONLY_minus_BASE": d_vs_base,
            "PT_ONLY_minus_BASE": pt_vs_base,
            "BOTH_minus_market": both_vs_market,
        },
        "tail_allocation_invariant_top1": robust,
        "diagnostic_gate": {
            "signal_present": signal_present,
            "requirements": "BOTH vs BASE: Top1 hits nonnegative and LL/Brier/RPS all improve, with at least one natural draw Top1 call; diagnostic only",
            "action": "DESIGN_NEW_PRELABEL_FULL_SUPPORT_RESULT_MARGINAL_FORWARD_TEST" if signal_present else "C072K2_SCORE_GAIN_DOES_NOT_TRANSLATE_TO_1X2_STOP_THIS_JOINT_LINE",
        },
        "limitations": [
            "The same C072K2 2025/26 confirmation outcomes are used for this post-outcome marginal audit; formal_weight is zero.",
            "T>=7 remains an aggregate tail exactly as frozen by C072K2, so no complete 1X2 probability is fabricated.",
            "Conditional T<=6 metrics are mechanism diagnostics, not deployable full-match probabilities.",
            "Any surviving mechanism must be frozen before a new independent target batch.",
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def verify() -> None:
    d = json.loads(OUT.read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["source_exact_score_pass_reproduced_before_audit"] is True
    assert g["model_refit_changed"] is False and g["features_changed"] is False
    assert g["parameter_search"] is False and g["threshold_search"] is False and g["draw_override"] is False
    assert g["tail7plus_allocated_to_result"] is False and g["formal_promotion_allowed"] is False
    assert d["coverage"]["hybrid_rows"] == 1006 and d["coverage"]["actual_T_le_6_rows"] == 990
    print("R43N0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(cmd)
