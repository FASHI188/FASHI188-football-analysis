#!/usr/bin/env python3
"""V6.25.1 exact-total selective confidence gate audit.

Diagnostic only; no probabilities are changed. Measures exact-total Top-1
accuracy conditional on pre-match confidence signals so low-margin mode ties
are not mistaken for strong exact-total forecasts.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import FORMAL_STATUS  # noqa: E402
from platform_core import load_json  # noqa: E402
from v6_team_regime_state_random100_v6240 import _collect_competition  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _total_distribution  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_exact_selective_gate_v6251_status.json"
MARGIN_THRESHOLDS = (0.0, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08)
PROB_THRESHOLDS = (0.20, 0.22, 0.24, 0.26, 0.28, 0.30)


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _summarize(rows: list[dict[str, Any]], predicate) -> dict[str, Any]:
    selected = [r for r in rows if predicate(r)]
    hits = sum(int(r["pick"] == r["actual"]) for r in selected)
    picks = Counter(r["pick"] for r in selected)
    return {
        "count": len(selected),
        "coverage": len(selected) / len(rows) if rows else 0.0,
        "hits": hits,
        "accuracy": hits / len(selected) if selected else None,
        "pick_counts": dict(picks),
    }


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    rows: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for cid in competitions:
        try:
            comp_rows, _ = _collect_competition(cid)
            for row in comp_rows:
                dist = _total_distribution(row["baseline_matrix"])
                ranking = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
                actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
                rows.append({
                    "competition_id": cid,
                    "pick": ranking[0][0],
                    "actual": actual,
                    "top1_probability": float(ranking[0][1]),
                    "margin": float(ranking[0][1] - ranking[1][1]),
                })
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if not rows:
        raise RuntimeError("no eligible rows")

    margin_table = {str(t): _summarize(rows, lambda r, t=t: r["margin"] >= t) for t in MARGIN_THRESHOLDS}
    probability_table = {str(t): _summarize(rows, lambda r, t=t: r["top1_probability"] >= t) for t in PROB_THRESHOLDS}
    combined = {}
    for margin in (0.02, 0.03, 0.04, 0.05):
        for prob in (0.24, 0.26, 0.28):
            combined[f"margin>={margin:.2f}|p>={prob:.2f}"] = _summarize(
                rows, lambda r, m=margin, p=prob: r["margin"] >= m and r["top1_probability"] >= p
            )

    payload = {
        "schema_version": "V6.25.1-exact-total-selective-gate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_DIAGNOSTIC_FORMAL_WEIGHT_0",
        "eligible_prediction_count": len(rows),
        "all_predictions": _summarize(rows, lambda r: True),
        "margin_thresholds": margin_table,
        "top1_probability_thresholds": probability_table,
        "combined_thresholds": combined,
        "failures": failures,
        "governance": {
            "probability_model_changed": False,
            "post_result_selection": False,
            "confidence_inputs_pre_match_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "eligible_prediction_count": len(rows),
        "all_predictions": payload["all_predictions"],
        "margin_thresholds": margin_table,
        "top1_probability_thresholds": probability_table,
        "combined_thresholds": combined,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
