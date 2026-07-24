#!/usr/bin/env python3
"""V6.18.5 selective exact-score gate on top of the frozen V6.18.4 shot challenger.

Research question:
Can pre-match confidence structure identify a useful subset where a unique exact-score
Top-1 prediction is materially more accurate, while abstaining elsewhere?

This does NOT change the score matrix. It only decides EXACT_SCORE vs ABSTAIN.
The underlying challenger is fixed at V6.18.4 shot mode, C=0.01, strict daily PIT.

Chronology:
- underlying validation matrix: train 2022/23+2023/24 -> score 2024/25
- selector/threshold selection: 2024/25 only
- underlying test matrix: refit same fixed C=0.01 on 2022/23..2024/25 -> score 2025/26
- the absolute validation-selected threshold is applied unchanged to 2025/26
- no 2025/26 selector, threshold, feature, or model selection is allowed

Pre-registered selector families use only pre-match matrix structure:
1) challenger Top-1 probability
2) challenger Top-1 minus Top-2 probability gap
3) negative challenger entropy
4) formal/challenger agreement + minimum shared Top-1 probability
5) formal/challenger agreement + challenger probability gap
6) joint confidence = challenger Top-1 p * max 1X2 p * peak total-goal p
7) agreement-required joint confidence

Threshold candidates are validation quantiles q in {0.50,0.60,0.70,0.80,0.90}.
A validation gate must cover >=10% of validation rows. Selection maximizes the Wilson
95% lower bound of exact-score hit rate, then hit rate, then coverage.

Research screen on untouched 2025/26:
- coverage >=10%
- exact-score Top-1 hit-rate improvement >=3 percentage points vs all-match challenger
- selected hit-rate Wilson 95% lower bound > all-match challenger Top-1 hit rate
This is not a promotion rule; it only decides whether selective exact score deserves a
future prospective freeze. formal_weight=0.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from statistics import NormalDist

import v6_conditional_score_shot_challenge_v6184 as challenge
import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as datefix
import v6_strict_daily_pit_rows_v6181c as strict
from platform_core import derive_score_marginals

OUT = base.ROOT / "manifests" / "v6_selective_exact_score_gate_v6185_status.json"
FIXED_C = 0.01
TRAIN_SEASONS = {"2022/23", "2023/24"}
VALID_SEASON = "2024/25"
TEST_SEASON = "2025/26"
QUANTILES = (0.50, 0.60, 0.70, 0.80, 0.90)
MIN_VALID_COVERAGE = 0.10
MIN_TEST_COVERAGE = 0.10
MIN_TEST_UPLIFT = 0.03
EPS = 1e-15


def strict_rows():
    raw, _ = base.raw_stat_matches()
    lookup, _ = datefix.lagged_shot_lookup_fixed(raw)
    rows, meta = strict.strict_formal_score_rows(lookup)
    out = []
    for r in rows:
        x = dict(r)
        x["actual_total"] = int(x.pop("actual_total_raw"))
        out.append(x)
    return out, meta


def rank_cells(matrix):
    return sorted(
        matrix,
        key=lambda c: (-float(c["probability"]), int(c["home_goals"]), int(c["away_goals"]))
    )


def entropy(matrix):
    return -sum(float(c["probability"]) * math.log(max(EPS, float(c["probability"]))) for c in matrix)


def score_key(cell):
    return int(cell["home_goals"]), int(cell["away_goals"])


def probability_of(matrix, key):
    return next(
        (float(c["probability"]) for c in matrix if score_key(c) == key),
        0.0,
    )


def total_peak(matrix):
    v = [0.0] * 8
    for c in matrix:
        t = int(c["home_goals"]) + int(c["away_goals"])
        v[min(7, t)] += float(c["probability"])
    return max(v)


def one_x_two_peak(matrix):
    m = derive_score_marginals(matrix)["1x2"]
    return max(float(m["home"]), float(m["draw"]), float(m["away"]))


def observation(row, challenger_matrix):
    cr = rank_cells(challenger_matrix)
    fr = rank_cells(row["formal_matrix"])
    c1 = cr[0]
    c2 = cr[1]
    ck = score_key(c1)
    fk = score_key(fr[0])
    p1 = float(c1["probability"])
    gap = p1 - float(c2["probability"])
    agree = ck == fk
    formal_p_same = probability_of(row["formal_matrix"], ck)
    joint = p1 * one_x_two_peak(challenger_matrix) * total_peak(challenger_matrix)
    actual = (int(row["home_goals"]), int(row["away_goals"]))
    top3 = {score_key(c) for c in cr[:3]}
    return {
        "correct_top1": int(ck == actual),
        "correct_top3": int(actual in top3),
        "top1_probability": p1,
        "gap": gap,
        "neg_entropy": -entropy(challenger_matrix),
        "agree": agree,
        "consensus_min_probability": min(p1, formal_p_same) if agree else None,
        "consensus_gap": gap if agree else None,
        "joint_confidence": joint,
        "consensus_joint_confidence": joint if agree else None,
        "candidate_score": ck,
    }


def build_observations(rows, train_rows, mode="shot"):
    shot_names, comps = challenge.shot_names_and_comps(train_rows + rows)
    models, support = challenge.fit_models(train_rows, mode, FIXED_C, shot_names, comps)
    obs = []
    for r in rows:
        m = challenge.adjusted_matrix(r, models, mode, shot_names, comps)
        obs.append(observation(r, m))
    return obs, support


def wilson_lower(hits, n, confidence=0.95):
    if n <= 0:
        return 0.0
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    p = hits / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre - radius) / denom


def quantile(values, q):
    xs = sorted(values)
    if not xs:
        raise ValueError("empty quantile values")
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def family_value(o, family):
    if family == "top1_probability":
        return o["top1_probability"]
    if family == "gap":
        return o["gap"]
    if family == "neg_entropy":
        return o["neg_entropy"]
    if family == "consensus_min_probability":
        return o["consensus_min_probability"]
    if family == "consensus_gap":
        return o["consensus_gap"]
    if family == "joint_confidence":
        return o["joint_confidence"]
    if family == "consensus_joint_confidence":
        return o["consensus_joint_confidence"]
    raise KeyError(family)


FAMILIES = (
    "top1_probability",
    "gap",
    "neg_entropy",
    "consensus_min_probability",
    "consensus_gap",
    "joint_confidence",
    "consensus_joint_confidence",
)


def summarize(selected, total_count):
    n = len(selected)
    hits = sum(int(o["correct_top1"]) for o in selected)
    top3 = sum(int(o["correct_top3"]) for o in selected)
    return {
        "selected": n,
        "coverage": n / total_count if total_count else 0.0,
        "top1_hits": hits,
        "top1_rate": hits / n if n else None,
        "top1_wilson_lower95": wilson_lower(hits, n) if n else None,
        "top3_hits": top3,
        "top3_rate": top3 / n if n else None,
        "mean_top1_probability": sum(float(o["top1_probability"]) for o in selected) / n if n else None,
        "agreement_rate": sum(int(o["agree"]) for o in selected) / n if n else None,
    }


def choose_gate(valid_obs):
    total = len(valid_obs)
    candidates = []
    for family in FAMILIES:
        finite = [family_value(o, family) for o in valid_obs if family_value(o, family) is not None]
        if not finite:
            continue
        for q in QUANTILES:
            threshold = quantile(finite, q)
            selected = [
                o for o in valid_obs
                if family_value(o, family) is not None and family_value(o, family) >= threshold
            ]
            s = summarize(selected, total)
            s.update({"family": family, "quantile": q, "threshold": threshold})
            if s["coverage"] >= MIN_VALID_COVERAGE:
                candidates.append(s)
    if not candidates:
        return None, []
    best = max(
        candidates,
        key=lambda x: (
            x["top1_wilson_lower95"],
            x["top1_rate"],
            x["coverage"],
            -FAMILIES.index(x["family"]),
            -x["quantile"],
        ),
    )
    return best, candidates


def apply_gate(obs, gate):
    family = gate["family"]
    threshold = float(gate["threshold"])
    return [
        o for o in obs
        if family_value(o, family) is not None and family_value(o, family) >= threshold
    ]


def main():
    challenge.base.derive_score_marginals = derive_score_marginals
    rows, meta = strict_rows()
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == VALID_SEASON]
    test = [r for r in rows if r["season"] == TEST_SEASON]
    if min(len(train), len(valid), len(test)) < 2000:
        raise RuntimeError(f"insufficient rows train={len(train)} valid={len(valid)} test={len(test)}")

    valid_obs, valid_support = build_observations(valid, train)
    test_obs, test_support = build_observations(test, train + valid)
    valid_all = summarize(valid_obs, len(valid_obs))
    test_all = summarize(test_obs, len(test_obs))

    gate, board = choose_gate(valid_obs)
    if gate is None:
        raise RuntimeError("no validation selector satisfied minimum coverage")
    selected_test = apply_gate(test_obs, gate)
    test_selected = summarize(selected_test, len(test_obs))
    uplift = (
        test_selected["top1_rate"] - test_all["top1_rate"]
        if test_selected["top1_rate"] is not None else None
    )
    promising = bool(
        test_selected["coverage"] >= MIN_TEST_COVERAGE
        and uplift is not None and uplift >= MIN_TEST_UPLIFT
        and test_selected["top1_wilson_lower95"] > test_all["top1_rate"]
    )

    agreement_only_valid = summarize([o for o in valid_obs if o["agree"]], len(valid_obs))
    agreement_only_test = summarize([o for o in test_obs if o["agree"]], len(test_obs))

    payload = {
        "schema_version": "V6.18.5-selective-exact-score-gate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_CHRONOLOGICAL_OOS_SELECTIVE_PREDICTION_RESEARCH",
        "underlying": {
            "model": "V6.18.4 shot conditional score",
            "fixed_C": FIXED_C,
            "strict_daily_pit": True,
            "score_matrix_changed_by_gate": False,
            "gate_action": "EXACT_SCORE or ABSTAIN",
        },
        "chronology": {
            "underlying_train_seasons": sorted(TRAIN_SEASONS),
            "selector_validation_season": VALID_SEASON,
            "test_season": TEST_SEASON,
            "test_used_for_selector_or_threshold_selection": False,
        },
        "selector_design": {
            "families": list(FAMILIES),
            "quantiles": list(QUANTILES),
            "minimum_validation_coverage": MIN_VALID_COVERAGE,
            "selection_objective": "maximize validation Wilson95 lower bound of exact-score Top1, then hit rate, then coverage",
            "research_screen": {
                "minimum_test_coverage": MIN_TEST_COVERAGE,
                "minimum_test_top1_uplift_vs_all_match_challenger": MIN_TEST_UPLIFT,
                "selected_wilson_lower95_must_exceed_all_match_test_top1": True,
            },
        },
        "rows": {"train": len(train), "validation": len(valid), "test": len(test)},
        "validation_all": valid_all,
        "validation_selected_gate": gate,
        "validation_candidate_board": board,
        "test_all": test_all,
        "test_selected": test_selected,
        "test_top1_uplift": uplift,
        "research_screen_pass": promising,
        "agreement_only": {"validation": agreement_only_valid, "test": agreement_only_test},
        "model_support": {"validation_model": valid_support, "test_model": test_support},
        "source_meta": meta,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "no_test_tuning": True,
            "no_promotion_from_2025_26": True,
            "future_prospective_freeze_required_if_screen_passes": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "validation_all": valid_all,
        "gate": gate,
        "test_all": test_all,
        "test_selected": test_selected,
        "test_top1_uplift": uplift,
        "research_screen_pass": promising,
        "agreement_only": payload["agreement_only"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
