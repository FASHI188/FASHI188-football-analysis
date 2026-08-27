#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
EXP = HERE.parent
S91_DIR = EXP / "batch003_s91_robust_side_draw_head"
sys.path.insert(0, str(S91_DIR))

import run_batch003_s91 as s91  # noqa: E402

s90 = s91.s90
s80 = s91.s80
s2 = s91.s2
s70 = s90.s70
r9 = s91.r9

HISTORY_CAP = 60000
TRAIN_N = 24123
VALID_MIN = 5000
HARD_CUTOFF_DATE = "2025-01-01"
THRESHOLDS = [round(0.20 + 0.01 * i, 2) for i in range(21)]
N_CHUNKS = 5


def side_class(p70):
    return 0 if float(p70["p_home"]) >= float(p70["p_away"]) else 2


def gate_class(pd_draw, p70, threshold):
    return 1 if pd_draw >= threshold else side_class(p70)


def summarize_decisions(rows, key):
    n = len(rows)
    hits = sum(int(r[key] == r["y"]) for r in rows)
    draw_picks = sum(int(r[key] == 1) for r in rows)
    draw_hits = sum(int(r[key] == 1 and r["y"] == 1) for r in rows)
    actual_draws = sum(int(r["y"] == 1) for r in rows)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n,
        "draw_picks": draw_picks,
        "draw_hits": draw_hits,
        "draw_recall": draw_hits / actual_draws if actual_draws else None,
        "draw_precision": draw_hits / draw_picks if draw_picks else None,
        "false_draw_picks": draw_picks - draw_hits,
        "actual_draws": actual_draws,
    }


def chunk_metrics(rows, key):
    n = len(rows)
    out = []
    for i in range(N_CHUNKS):
        lo = math.floor(i * n / N_CHUNKS)
        hi = math.floor((i + 1) * n / N_CHUNKS)
        z = rows[lo:hi]
        m = summarize_decisions(z, key)
        m["chunk"] = i + 1
        m["first_date"] = z[0]["date"] if z else None
        m["last_date"] = z[-1]["date"] if z else None
        out.append(m)
    return out


def run():
    pool = s2.load_frozen_pool()
    eligible = [x for x in pool if x["date"] < HARD_CUTOFF_DATE]
    eligible.sort(key=lambda x: (x["date"], x["game_id"]))
    available = len(eligible)
    if available < TRAIN_N + VALID_MIN:
        raise RuntimeError(f"insufficient pre-2025 pool rows for train+validation: {available}")

    history_n = min(HISTORY_CAP, available)
    window = [{k: v for k, v in x.items() if k != "_known"} for x in eligible[-history_n:]]
    hp, _state, _robust_hist, _draw_state = s80.history_joint(window)
    if len(hp) != history_n:
        raise RuntimeError(f"history replay mismatch: {len(hp)}/{history_n}")

    # Keep whole dates intact so no same-day result can cross the train/validation boundary.
    validation_start_date = hp[-VALID_MIN]["date"]
    pre = [r for r in hp if r["date"] < validation_start_date]
    valid = [r for r in hp if r["date"] >= validation_start_date]
    if len(pre) < TRAIN_N or len(valid) < VALID_MIN:
        raise RuntimeError(f"split insufficient train={len(pre)} valid={len(valid)}")
    train = pre[-TRAIN_N:]

    draw_head, feature_count = s91.fit_draw_head(train)
    model70 = s70.fit(train)

    rows = []
    for rec in valid:
        p70 = s70.predict(model70, rec["raw"], rec["robust"])
        pd_draw = s91.positive_prob(draw_head, s91.draw_feat(rec))
        p91 = s91.compose_s91(draw_head, rec["raw"], rec["compact_draw"], p70)
        q = np.asarray([p91["p_home"], p91["p_draw"], p91["p_away"]], dtype=float)
        row = {
            "date": rec["date"],
            "game_id": rec["game_id"],
            "y": int(rec["y"]),
            "p_draw": float(pd_draw),
            "S70_argmax": int(np.argmax([p70["p_home"], p70["p_draw"], p70["p_away"]])),
            "S91_argmax": int(np.argmax(q)),
        }
        for t in THRESHOLDS:
            row[f"gate_{t:.2f}"] = gate_class(pd_draw, p70, t)
        rows.append(row)

    baseline70 = summarize_decisions(rows, "S70_argmax")
    baseline91 = summarize_decisions(rows, "S91_argmax")
    sweeps = []
    for t in THRESHOLDS:
        key = f"gate_{t:.2f}"
        m = summarize_decisions(rows, key)
        chunks = chunk_metrics(rows, key)
        m.update({
            "threshold": t,
            "chunks": chunks,
            "worst_chunk_accuracy": min(x["accuracy"] for x in chunks),
            "mean_chunk_accuracy": sum(x["accuracy"] for x in chunks) / len(chunks),
        })
        sweeps.append(m)

    # Predeclared selection: maximize total hits; tie -> maximize worst chunk accuracy;
    # tie -> higher draw precision (None treated as -1); tie -> higher threshold (conservative).
    ranked = sorted(
        sweeps,
        key=lambda x: (
            x["hits"],
            x["worst_chunk_accuracy"],
            -1.0 if x["draw_precision"] is None else x["draw_precision"],
            x["threshold"],
        ),
        reverse=True,
    )
    best = ranked[0]
    supported = best["hits"] > baseline91["hits"]

    out = {
        "schema_version": "football3-s92-draw-gate-history-validation-v1",
        "status": "S92_HISTORY_GATE_VALIDATION_COMPLETE",
        "classification": "PRE_BATCH001_PRE_BATCH002_PRE_BATCH003_HISTORICAL_HOLDOUT",
        "governance": {
            "hard_cutoff_date_exclusive": HARD_CUTOFF_DATE,
            "history_cap_predeclared": HISTORY_CAP,
            "history_window_adjusted_only_for_pre_cutoff_data_availability": history_n < HISTORY_CAP,
            "Batch001_results_used": False,
            "Batch002_results_used": False,
            "Batch003_results_used": False,
            "market_used": False,
            "odds_used": False,
            "threshold_grid_predeclared": THRESHOLDS,
            "selection_rule_predeclared": "max total hits; tie max worst-chunk accuracy; tie max draw precision; tie highest threshold",
            "candidate_must_be_locked_on_fresh_batch_before_scoring": True,
        },
        "pre_cutoff_available_rows": available,
        "history_rows": history_n,
        "train_rows": len(train),
        "validation_rows": len(valid),
        "train_first_date": train[0]["date"],
        "train_last_date": train[-1]["date"],
        "validation_first_date": valid[0]["date"],
        "validation_last_date": valid[-1]["date"],
        "draw_head_feature_count": feature_count,
        "S70_argmax": baseline70,
        "S91_argmax": baseline91,
        "threshold_sweep": sweeps,
        "selected": {
            "supported": supported,
            "threshold": best["threshold"] if supported else None,
            "best_history_result": best,
            "gain_hits_vs_S91_argmax": best["hits"] - baseline91["hits"],
            "gain_pp_vs_S91_argmax": 100 * (best["accuracy"] - baseline91["accuracy"]),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_s92_history_gate.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": out["status"],
        "pre_cutoff_available_rows": available,
        "history_rows": history_n,
        "train_rows": out["train_rows"],
        "validation_rows": out["validation_rows"],
        "S70_argmax": baseline70,
        "S91_argmax": baseline91,
        "selected": out["selected"],
    }, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_s92_history_gate.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "S92_HISTORY_GATE_VALIDATION_COMPLETE"
    assert s["history_rows"] <= HISTORY_CAP
    assert s["pre_cutoff_available_rows"] >= s["history_rows"] >= TRAIN_N + VALID_MIN
    assert s["train_rows"] == TRAIN_N and s["validation_rows"] >= VALID_MIN
    assert s["validation_last_date"] < HARD_CUTOFF_DATE
    assert not g["Batch001_results_used"] and not g["Batch002_results_used"] and not g["Batch003_results_used"]
    assert not g["market_used"] and not g["odds_used"]
    assert g["threshold_grid_predeclared"] == THRESHOLDS
    if s["selected"]["supported"]:
        assert s["selected"]["threshold"] in THRESHOLDS
        assert s["selected"]["gain_hits_vs_S91_argmax"] > 0
    print("S92_HISTORY_GATE_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_s92_history_gate.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
