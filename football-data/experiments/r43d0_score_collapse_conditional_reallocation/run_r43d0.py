#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9_DIR = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

SOURCE_R43C0_HEAD = "bb33a5a8df28fe634092b39e7dd77a6c2de87e9e"
MAXG = 12
TOTAL_CAP = 7
KAPPAS = (5.0, 20.0, 80.0, 320.0)
EPS = 1e-15


def score_matrix(mu_h: float, mu_a: float) -> dict[tuple[int, int], float]:
    hp = [math.exp(-mu_h)]
    ap = [math.exp(-mu_a)]
    for k in range(1, MAXG + 1):
        hp.append(hp[-1] * mu_h / k)
        ap.append(ap[-1] * mu_a / k)
    m = {(h, a): hp[h] * ap[a] for h in range(MAXG + 1) for a in range(MAXG + 1)}
    s = sum(m.values())
    return {k: v / s for k, v in m.items()}


def result_key(h: int, a: int) -> str:
    return "H" if h > a else "D" if h == a else "A"


def group_key(h: int, a: int) -> tuple[int, str]:
    return min(TOTAL_CAP, h + a), result_key(h, a)


def adjusted(prior: dict, counts: Counter, kappa: float) -> dict:
    grouped = defaultdict(list)
    for (h, a), p in prior.items():
        grouped[group_key(h, a)].append((h, a, p))
    out = {}
    for g, cells in grouped.items():
        mass = sum(p for _, _, p in cells)
        n = sum(counts[(g, h, a)] for h, a, _ in cells)
        if n <= 0:
            for h, a, p in cells:
                out[(h, a)] = p
            continue
        den = n + kappa
        for h, a, p in cells:
            cond_prior = p / mass
            c = counts[(g, h, a)]
            out[(h, a)] = mass * ((c + kappa * cond_prior) / den)
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def marginals(m: dict) -> tuple[dict, dict]:
    r = {"H": 0.0, "D": 0.0, "A": 0.0}
    t = {i: 0.0 for i in range(TOTAL_CAP + 1)}
    for (h, a), p in m.items():
        r[result_key(h, a)] += p
        t[min(TOTAL_CAP, h + a)] += p
    return r, t


def residual(prior: dict, cand: dict) -> tuple[float, float]:
    r0, t0 = marginals(prior)
    r1, t1 = marginals(cand)
    return max(abs(r0[k] - r1[k]) for k in r0), max(abs(t0[k] - t1[k]) for k in t0)


def one_metric(m: dict, hg: int, ag: int) -> dict:
    ranked = sorted(((p, h, a) for (h, a), p in m.items()), reverse=True)
    top1 = (ranked[0][1], ranked[0][2])
    top3 = {(h, a) for _, h, a in ranked[:3]}
    pactual = m.get((hg, ag), EPS)
    return {
        "top1_hit": int(top1 == (hg, ag)),
        "top3_hit": int((hg, ag) in top3),
        "logloss": -math.log(max(EPS, pactual)),
        "p_actual": float(pactual),
        "top1_score": f"{top1[0]}-{top1[1]}",
        "top1_is_1_1": int(top1 == (1, 1)),
        "actual_is_1_1": int((hg, ag) == (1, 1)),
    }


def aggregate(rows: list[dict], key: str) -> dict:
    n = len(rows)
    if not n:
        return {}
    ms = [r[key] for r in rows]
    top_counts = Counter(m["top1_score"] for m in ms)
    top1_11 = sum(m["top1_is_1_1"] for m in ms) / n
    actual_11 = sum(m["actual_is_1_1"] for m in ms) / n
    return {
        "n": n,
        "top1_accuracy": sum(m["top1_hit"] for m in ms) / n,
        "top3_accuracy": sum(m["top3_hit"] for m in ms) / n,
        "mean_logloss": sum(m["logloss"] for m in ms) / n,
        "mean_actual_probability": sum(m["p_actual"] for m in ms) / n,
        "top1_1_1_share": top1_11,
        "actual_1_1_rate": actual_11,
        "one_one_excess": top1_11 - actual_11,
        "abs_one_one_gap": abs(top1_11 - actual_11),
        "top1_score_counts": dict(top_counts.most_common(12)),
    }


def boundary(rows: list[dict], target: int) -> int:
    i = min(max(1, target), len(rows) - 1)
    while i < len(rows) and rows[i]["date"] == rows[i - 1]["date"]:
        i += 1
    return i


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    matches = r9.load()
    state = r9.S()
    counts = Counter()
    recs = []
    overcap = 0
    max_r1 = 0.0
    max_rt = 0.0
    by_date = defaultdict(list)
    for row in matches:
        by_date[row["date"]].append(row)

    for ds in sorted(by_date):
        pending = []
        for row in sorted(by_date[ds], key=lambda x: x["game_id"]):
            pred = state.pred(row)
            prior = score_matrix(float(pred["mu_home"]), float(pred["mu_away"]))
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            if hg > MAXG or ag > MAXG:
                overcap += 1
            rec = {"date": ds, "actual": [hg, ag], "baseline": one_metric(prior, hg, ag)}
            for k in KAPPAS:
                cand = adjusted(prior, counts, k)
                r1, rt = residual(prior, cand)
                max_r1 = max(max_r1, r1)
                max_rt = max(max_rt, rt)
                rec[f"k{k:g}"] = one_metric(cand, hg, ag)
            recs.append(rec)
            pending.append((row, pred))

        # Same-date outcomes are invisible until every match on that date has been predicted.
        for row, pred in pending:
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            if hg <= MAXG and ag <= MAXG:
                counts[(group_key(hg, ag), hg, ag)] += 1
            state.update(row, pred)

    b1 = boundary(recs, 4000)
    b2 = boundary(recs, b1 + 8000)
    b3 = boundary(recs, b2 + 4000)
    train = recs[b1:b2]
    val = recs[b2:b3]
    test = recs[b3:]

    train_base = aggregate(train, "baseline")
    train_candidates = {str(k): aggregate(train, f"k{k:g}") for k in KAPPAS}
    eligible = []
    for k in KAPPAS:
        m = train_candidates[str(k)]
        if m["mean_logloss"] <= train_base["mean_logloss"] + 0.002 and m["top3_accuracy"] >= train_base["top3_accuracy"]:
            eligible.append(k)
    if eligible:
        selected = min(
            eligible,
            key=lambda k: (
                train_candidates[str(k)]["abs_one_one_gap"],
                train_candidates[str(k)]["mean_logloss"],
                -train_candidates[str(k)]["top1_accuracy"],
            ),
        )
    else:
        selected = min(KAPPAS, key=lambda k: train_candidates[str(k)]["mean_logloss"])

    skey = f"k{selected:g}"
    val_base = aggregate(val, "baseline")
    val_sel = aggregate(val, skey)
    test_base = aggregate(test, "baseline")
    test_sel = aggregate(test, skey)
    val_pass = bool(
        val_sel["mean_logloss"] <= val_base["mean_logloss"]
        and val_sel["abs_one_one_gap"] < val_base["abs_one_one_gap"]
        and val_sel["top1_accuracy"] >= val_base["top1_accuracy"]
    )
    test_pass = bool(
        val_pass
        and test_sel["mean_logloss"] < test_base["mean_logloss"]
        and test_sel["abs_one_one_gap"] < test_base["abs_one_one_gap"]
        and test_sel["top1_accuracy"] >= test_base["top1_accuracy"]
        and max_r1 <= 1e-10
        and max_rt <= 1e-10
    )

    result = {
        "schema_version": "football3-r43d0-score-collapse-conditional-reallocation-v1",
        "status": "COMPLETE",
        "classification": "STRICT_CHRONOLOGICAL_EXACT_SCORE_COLLAPSE_AUDIT_AND_MARGINAL_PRESERVING_CHALLENGER",
        "formal_weight": 0,
        "source_r43c0_head": SOURCE_R43C0_HEAD,
        "governance": {
            "target_result_used_before_prediction": False,
            "same_date_outcome_update_before_prediction": False,
            "odds_used": False,
            "handicap_used": False,
            "manual_one_one_penalty": False,
            "manual_draw_override": False,
            "candidate_kappas_predeclared": list(KAPPAS),
            "selection_uses_test": False,
            "selection_uses_train_only": True,
            "validation_gate_before_test_promotion": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "baseline": "independent Poisson exact-score matrix from strictly prior R9b mu_home/mu_away",
            "challenger": "historical conditional score-frequency shrinkage within fixed (total bucket, 1X2 result) partitions",
            "invariant": "challenger preserves baseline 1X2 and total-goals 0..6,7+ marginals by construction",
            "max_goal_axis": MAXG,
            "total_bucket_cap": TOTAL_CAP,
            "kappas": list(KAPPAS),
            "selection_rule": "on train only: proper-score near-noninferiority and top3 nonworse, then minimize absolute 1-1 top-pick gap; no test search",
        },
        "split": {
            "burn": [0, b1],
            "train": [b1, b2],
            "validation": [b2, b3],
            "test": [b3, len(recs)],
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "validation_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
            "date_safe": True,
        },
        "audit": {
            "overcap_actual_scores": overcap,
            "max_1x2_marginal_residual": max_r1,
            "max_total_bucket_marginal_residual": max_rt,
        },
        "train": {
            "baseline": train_base,
            "candidates": train_candidates,
            "selected_kappa": selected,
        },
        "validation": {
            "baseline": val_base,
            "selected": val_sel,
            "passed": val_pass,
            "delta_logloss": val_sel["mean_logloss"] - val_base["mean_logloss"],
            "delta_top1": val_sel["top1_accuracy"] - val_base["top1_accuracy"],
            "delta_abs_one_one_gap": val_sel["abs_one_one_gap"] - val_base["abs_one_one_gap"],
        },
        "test": {
            "baseline": test_base,
            "selected": test_sel,
            "delta_logloss": test_sel["mean_logloss"] - test_base["mean_logloss"],
            "delta_top1": test_sel["top1_accuracy"] - test_base["top1_accuracy"],
            "delta_top3": test_sel["top3_accuracy"] - test_base["top3_accuracy"],
            "delta_abs_one_one_gap": test_sel["abs_one_one_gap"] - test_base["abs_one_one_gap"],
        },
        "gate": {
            "passed": test_pass,
            "action": "PROMOTE_CONDITIONAL_SCORE_REALLOCATION_TO_FINAL_MATRIX_INTEGRATION_TEST" if test_pass else "DO_NOT_PROMOTE_R43D0_AND_DO_NOT_HAND_EDIT_1_1",
        },
        "limitations": [
            "This stage isolates exact-score allocation and deliberately does not alter 1X2 probabilities.",
            "The challenger uses global historical score frequencies; competition/regime-specific conditioning is a later challenger only if this mechanism survives untouched test.",
            "This is historical development evidence, not forward evidence.",
            "No manual suppression of 1-1 is ever applied.",
        ],
    }
    path = OUT / "summary_r43d0_score_collapse.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43d0_score_collapse.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["governance"]["manual_one_one_penalty"] is False
    assert d["governance"]["selection_uses_test"] is False
    assert d["split"]["date_safe"] is True
    assert d["audit"]["max_1x2_marginal_residual"] <= 1e-10
    assert d["audit"]["max_total_bucket_marginal_residual"] <= 1e-10
    print("R43D0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
