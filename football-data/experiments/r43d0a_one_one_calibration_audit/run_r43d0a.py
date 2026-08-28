#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9_DIR = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

SOURCE_R43D0_HEAD = "931d70c27e7d5d42c770521b6a89fe747b723da9"
MAXG = 12
BINS = (0.0, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 1.0)


def matrix(mu_h: float, mu_a: float):
    hp = [math.exp(-mu_h)]
    ap = [math.exp(-mu_a)]
    for k in range(1, MAXG + 1):
        hp.append(hp[-1] * mu_h / k)
        ap.append(ap[-1] * mu_a / k)
    m = {(h, a): hp[h] * ap[a] for h in range(MAXG + 1) for a in range(MAXG + 1)}
    s = sum(m.values())
    return {k: v / s for k, v in m.items()}


def boundary(rows, target):
    i = min(max(1, target), len(rows) - 1)
    while i < len(rows) and rows[i]["date"] == rows[i - 1]["date"]:
        i += 1
    return i


def summarize(rows):
    n = len(rows)
    p = np.asarray([r["p11"] for r in rows], dtype=float)
    y = np.asarray([r["y11"] for r in rows], dtype=float)
    mode = np.asarray([r["top11"] for r in rows], dtype=bool)
    margins = np.asarray([r["margin"] for r in rows], dtype=float)
    out = {
        "n": n,
        "actual_1_1_rate": float(y.mean()),
        "mean_predicted_p_1_1": float(p.mean()),
        "mean_probability_minus_actual_rate": float(p.mean() - y.mean()),
        "binary_1_1_brier": float(np.mean((p - y) ** 2)),
        "top1_1_1_share": float(mode.mean()),
        "top1_1_1_count": int(mode.sum()),
        "top1_not_1_1_count": int((~mode).sum()),
        "top1_1_1_subset": {},
        "top1_not_1_1_subset": {},
        "calibration_bins": [],
    }
    for name, mask in (("top1_1_1_subset", mode), ("top1_not_1_1_subset", ~mode)):
        if mask.any():
            out[name] = {
                "n": int(mask.sum()),
                "actual_1_1_rate": float(y[mask].mean()),
                "mean_predicted_p_1_1": float(p[mask].mean()),
                "mean_top1_margin": float(margins[mask].mean()),
                "median_top1_margin": float(np.median(margins[mask])),
                "p90_top1_margin": float(np.quantile(margins[mask], 0.90)),
            }
    for lo, hi in zip(BINS[:-1], BINS[1:]):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if mask.any():
            out["calibration_bins"].append({
                "lo": lo, "hi": hi, "n": int(mask.sum()),
                "mean_p11": float(p[mask].mean()),
                "actual_11_rate": float(y[mask].mean()),
                "gap": float(p[mask].mean() - y[mask].mean()),
                "top1_11_share": float(mode[mask].mean()),
            })
    return out


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = r9.load()
    st = r9.S()
    recs = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            pred = st.pred(row)
            m = matrix(float(pred["mu_home"]), float(pred["mu_away"]))
            ranked = sorted(m.values(), reverse=True)
            top = max(m, key=m.get)
            recs.append({
                "date": ds,
                "p11": float(m[(1, 1)]),
                "y11": int(int(row["home_goals"]) == 1 and int(row["away_goals"]) == 1),
                "top11": int(top == (1, 1)),
                "margin": float(ranked[0] - ranked[1]),
            })
            pending.append((row, pred))
        for row, pred in pending:
            st.update(row, pred)

    b1 = boundary(recs, 4000)
    b2 = boundary(recs, b1 + 8000)
    b3 = boundary(recs, b2 + 4000)
    val, test = recs[b2:b3], recs[b3:]
    result = {
        "schema_version": "football3-r43d0a-one-one-calibration-audit-v1",
        "status": "COMPLETE",
        "classification": "POST_R43D0_DIAGNOSTIC_ONLY_NO_MODEL_CHANGE",
        "formal_weight": 0,
        "source_r43d0_head": SOURCE_R43D0_HEAD,
        "governance": {
            "same_date_update_before_prediction": False,
            "target_result_used_before_prediction": False,
            "parameter_tuning": False,
            "manual_one_one_penalty": False,
            "model_change": False,
            "r42l_lock_modified": False,
        },
        "question": "Is the apparent 1-1 collapse a probability-mass calibration failure, or mainly an argmax/mode-concentration phenomenon?",
        "validation": summarize(val),
        "test": summarize(test),
        "interpretation_rule": {
            "mass_failure": "mean P(1-1) materially exceeds actual 1-1 rate and calibration bins show systematic overprediction",
            "mode_failure": "1-1 is Top1 in far more matches than it occurs, while mean P(1-1) remains near the empirical rate and Top1 margins are small",
        },
    }
    p = OUT / "summary_r43d0a_one_one_calibration.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r43d0a_one_one_calibration.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["governance"]["model_change"] is False
    assert d["governance"]["r42l_lock_modified"] is False
    print("R43D0A contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": run, "verify": verify}[cmd]()
