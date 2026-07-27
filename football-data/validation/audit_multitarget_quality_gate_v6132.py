#!/usr/bin/env python3
"""V6.13.2 multi-target candidate quality gate.

Prevents a candidate from being described as an improvement merely because one hit-rate
metric rises. A candidate may advance only when the designated hit metric improves (or
is non-worse where configured) AND proper scores do not worsen. Small forward samples
remain HOLD, never promotion evidence.

Research governance only; no formal probability changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_multitarget_quality_gate_v6132_status.json"
CASES = [
    {
        "name": "V6.17 multiline_vs_singleline",
        "path": ROOT / "manifests" / "v6170_formalprior_multiline_forward_status.json",
        "reference": "singleline",
        "candidate": "multiline",
        "minimum_settled": 100,
        "hit_metrics": ("score_top1", "score_top3", "total_top1", "total_top2"),
        "proper_lower_is_better": ("joint_log", "total_rps"),
    },
    {
        "name": "V6.22.4 coherent_vs_multiline",
        "path": ROOT / "manifests" / "v6_coherent_special_market_forward_v6224_status.json",
        "reference": "multiline",
        "candidate": "coherent",
        "minimum_settled": 100,
        "hit_metrics": ("score_top1", "score_top3", "total_top1", "total_top2"),
        "proper_lower_is_better": ("joint_log", "total_rps"),
    },
]
EPS = 1e-12


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(case: dict[str, Any]) -> dict[str, Any]:
    payload = load(case["path"])
    arms = payload.get("arms") or {}
    ref = arms.get(case["reference"]) or {}
    cand = arms.get(case["candidate"]) or {}
    n = min(int(ref.get("count") or 0), int(cand.get("count") or 0))

    deltas = {}
    hit_improvements = []
    hit_regressions = []
    for key in case["hit_metrics"]:
        rv, cv = ref.get(key), cand.get(key)
        if rv is None or cv is None:
            deltas[key] = None
            continue
        delta = float(cv) - float(rv)
        deltas[key] = delta
        if delta > EPS:
            hit_improvements.append(key)
        elif delta < -EPS:
            hit_regressions.append(key)

    proper_regressions = []
    proper_improvements = []
    for key in case["proper_lower_is_better"]:
        rv, cv = ref.get(key), cand.get(key)
        if rv is None or cv is None:
            deltas[key] = None
            continue
        delta = float(cv) - float(rv)
        deltas[key] = delta
        if delta > EPS:
            proper_regressions.append(key)
        elif delta < -EPS:
            proper_improvements.append(key)

    sample_ready = n >= int(case["minimum_settled"])
    proper_nonworse = not proper_regressions
    any_hit_improved = bool(hit_improvements)
    no_hit_regression = not hit_regressions

    if not sample_ready:
        decision = "HOLD_LOW_SAMPLE"
        if proper_regressions:
            decision = "HOLD_LOW_SAMPLE_WITH_PROPER_SCORE_REGRESSION"
    elif proper_regressions:
        decision = "REJECT_PROPER_SCORE_REGRESSION"
    elif not any_hit_improved:
        decision = "REJECT_NO_HIT_METRIC_IMPROVEMENT"
    elif not no_hit_regression:
        decision = "HOLD_MIXED_HIT_METRICS"
    else:
        decision = "PASS_FAST_SCREEN_ONLY"

    return {
        "name": case["name"],
        "source": str(case["path"].relative_to(ROOT)),
        "source_generated_at_utc": payload.get("generated_at_utc"),
        "reference_arm": case["reference"],
        "candidate_arm": case["candidate"],
        "paired_count": n,
        "minimum_settled": case["minimum_settled"],
        "sample_ready": sample_ready,
        "deltas_candidate_minus_reference": deltas,
        "hit_improvements": hit_improvements,
        "hit_regressions": hit_regressions,
        "proper_score_improvements": proper_improvements,
        "proper_score_regressions": proper_regressions,
        "proper_scores_nonworse": proper_nonworse,
        "decision": decision,
        "promotion_allowed": False,
    }


def main() -> int:
    results = [evaluate(c) for c in CASES]
    payload = {
        "schema_version": "V6.13.2-multitarget-quality-gate-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "policy": {
            "top1_accuracy_is_not_sufficient": True,
            "proper_score_nonworsening_required": True,
            "minimum_forward_sample_required": True,
            "small_sample_cannot_promote": True,
            "automatic_promotion": False,
        },
        "cases": results,
        "summary": {
            "case_count": len(results),
            "proper_score_regression_cases": sum(bool(r["proper_score_regressions"]) for r in results),
            "sample_ready_cases": sum(bool(r["sample_ready"]) for r in results),
            "promotion_allowed_cases": 0,
        },
        "governance": {
            "research_only": True,
            "formal_weight_change": False,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
