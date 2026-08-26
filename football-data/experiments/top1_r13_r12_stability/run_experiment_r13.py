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
R12_DIR = HERE.parent / "top1_r12_draw_features"
sys.path.insert(0, str(R12_DIR))
import run_experiment_r12 as r12  # noqa: E402

r9 = r12.r9


def decorate_probs(model, X):
    pr = model.predict_proba(X)
    classes = list(model[-1].classes_)
    out = []
    for row in pr:
        v = np.zeros(3, dtype=float)
        for cls, prob in zip(classes, row):
            v[int(cls)] = float(prob)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append(r9.decorate(v))
    return out


def rebuild():
    # Reuse the exact R12/R9b hash gate; this creates the same 20k snapshot.
    r12.freeze_gate()
    rows = r9.load()
    base_state = r9.S()
    draw_state = r12.DrawState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = base_state.pred(row)
            dfeat = draw_state.features(row, raw)
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "draw_features": dfeat})
            pending.append((row, raw))
        for row, raw in pending:
            base_state.update(row, raw)
            draw_state.update(row)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = [r["y"] for r in train]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k2 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y)
    k2.fit([r9.feat_k1(r["raw"]) + r["draw_features"] for r in train], y)

    for subset in (val, test):
        p1 = decorate_probs(k1, [r9.feat_k1(r["raw"]) for r in subset])
        p2 = decorate_probs(k2, [r9.feat_k1(r["raw"]) + r["draw_features"] for r in subset])
        for rec, a, b in zip(subset, p1, p2):
            rec["K1"] = a
            rec["K2"] = b
    return train, val, test, k2


def paired_diag(rows):
    from scipy.stats import binomtest

    gain = loss = both_correct = both_wrong = 0
    for r in rows:
        c1 = r["K1"]["top1"] == r["y"]
        c2 = r["K2"]["top1"] == r["y"]
        if c2 and not c1:
            gain += 1
        elif c1 and not c2:
            loss += 1
        elif c1 and c2:
            both_correct += 1
        else:
            both_wrong += 1
    discordant = gain + loss
    p = float(binomtest(gain, discordant, 0.5, alternative="two-sided").pvalue) if discordant else 1.0
    return {
        "k2_correct_k1_wrong": gain,
        "k1_correct_k2_wrong": loss,
        "net_hits": gain - loss,
        "both_correct": both_correct,
        "both_wrong": both_wrong,
        "discordant": discordant,
        "mcnemar_exact_p_two_sided": p,
    }


def date_blocks(rows, nblocks=4):
    cuts = [0]
    n = len(rows)
    for i in range(1, nblocks):
        j = int(round(n * i / nblocks))
        while j < n and rows[j]["date"] == rows[j - 1]["date"]:
            j += 1
        cuts.append(min(j, n))
    cuts.append(n)
    blocks = []
    for i in range(nblocks):
        z = rows[cuts[i]:cuts[i + 1]]
        if not z:
            continue
        m1 = r9.metrics(z, "K1")
        m2 = r9.metrics(z, "K2")
        blocks.append({
            "block": i + 1,
            "first_date": z[0]["date"],
            "last_date": z[-1]["date"],
            "count": len(z),
            "K1": m1,
            "K2": m2,
            "delta_K2_minus_K1": r12.delta(m2, m1),
            "paired": paired_diag(z),
        })
    return blocks


def draw_contrast_coefficients(k2):
    lr = k2[-1]
    classes = list(lr.classes_)
    idx = {int(c): i for i, c in enumerate(classes)}
    coef = lr.coef_
    # Standardized-feature draw-vs-(home+away)/2 contrast.
    draw_vec = coef[idx[1]] - 0.5 * (coef[idx[0]] + coef[idx[2]])
    base_len = len(r9.feat_k1({
        "p_home": 0.4, "p_draw": 0.3, "p_away": 0.3,
        "mu_home": 1.3, "mu_away": 1.2, "mu_total": 2.5,
        "home_history": 1, "away_history": 1, "comp_history": 1,
        "xg_mu_home": 1.3, "xg_mu_away": 1.2, "xg_mu_total": 2.5,
        "xg_home_for": 1.2, "xg_home_against": 1.1,
        "xg_away_for": 1.1, "xg_away_against": 1.2,
        "xg_weight_min": 1.0,
    }))
    vals = []
    for name, v in zip(r12.FEATURE_NAMES, draw_vec[base_len:]):
        vals.append({"feature": name, "draw_contrast_coef": float(v), "abs": float(abs(v))})
    vals.sort(key=lambda x: -x["abs"])
    for x in vals:
        x.pop("abs", None)
    return vals


def run():
    train, val, test, k2 = rebuild()
    v1, v2 = r9.metrics(val, "K1"), r9.metrics(val, "K2")
    t1, t2 = r9.metrics(test, "K1"), r9.metrics(test, "K2")

    if v1["hits"] != 2064 or t1["hits"] != 1877 or v2["hits"] != 2053 or t2["hits"] != 1894:
        raise RuntimeError("R12 reproduction gate failed in R13")

    summary = {
        "schema_version": "football3-top1-r13-r12-stability-audit",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_DIAGNOSTIC_ONLY",
        "formal_weight": 0,
        "governance": {
            "new_model_trained": False,
            "r12_models_reproduced_exactly": True,
            "same_r9b_snapshot": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "test_already_observed_in_r12": True,
            "test_used_only_for_stability_diagnosis": True,
            "formal_promotion_allowed_from_this_run": False,
        },
        "overall": {
            "validation": {
                "K1": v1,
                "K2": v2,
                "delta_K2_minus_K1": r12.delta(v2, v1),
                "paired": paired_diag(val),
            },
            "test": {
                "K1": t1,
                "K2": t2,
                "delta_K2_minus_K1": r12.delta(t2, t1),
                "paired": paired_diag(test),
            },
        },
        "validation_chronological_blocks": date_blocks(val, 4),
        "test_chronological_blocks": date_blocks(test, 4),
        "r12_draw_feature_coefficients": draw_contrast_coefficients(k2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r13.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def verify():
    s = json.loads((OUT / "summary_r13.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["r12_models_reproduced_exactly"] and g["same_r9b_snapshot"]
    assert g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["test_already_observed_in_r12"] and g["test_used_only_for_stability_diagnosis"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert len(s["validation_chronological_blocks"]) == 4
    assert len(s["test_chronological_blocks"]) == 4
    print("R13_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r13.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
