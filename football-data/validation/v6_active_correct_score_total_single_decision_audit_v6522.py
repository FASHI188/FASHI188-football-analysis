#!/usr/bin/env python3
"""V6.52.2 one-decision-per-fixture audit for V6.25.20 total rankings.

Research/audit only; formal_weight=0.

V6.52.1 corrected correlated freeze-snapshot counting. This audit goes one step
further and evaluates deterministic one-snapshot-per-fixture timing policies on
the already-frozen prospective prediction ledger. It is a retrospective timing
sensitivity diagnostic, not fresh promotion evidence: outcomes already exist at
audit design time, so no policy is promoted or selected as a winner here.

No model is trained, no prediction is changed, and no historical prediction is
backfilled.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_active_correct_score_total_forward_v62520 as base  # noqa: E402

PREDICTIONS = ROOT / "forward" / "v6_active_correct_score_total_predictions_v62520.json"
OUT = ROOT / "manifests" / "v6_active_correct_score_total_single_decision_audit_v6522_status.json"
ARM_NAMES = ("top_score_total", "numeric_mass_total", "partial_cdf_total", "consensus_total")


def _fixture_key(pred: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(pred.get("competition_id") or ""),
        str(pred.get("home_team") or ""),
        str(pred.get("away_team") or ""),
        str(pred.get("kickoff_at") or ""),
    )


def _eligible(pred: dict[str, Any], lo: float = 1.0, hi: float = 72.0) -> bool:
    try:
        lead = float(pred.get("lead_hours"))
    except (TypeError, ValueError):
        return False
    return lo <= lead <= hi


def _settled_groups(preds: dict[str, Any]) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    results = base._result_lookup()
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for pred in preds.get("predictions") or []:
        if not _eligible(pred):
            continue
        key = _fixture_key(pred)
        score = results.get(key)
        if score is None:
            continue
        row = dict(pred)
        row["actual_total_bucket"] = base._bucket(score[0] + score[1])
        groups[key].append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: (float(r["lead_hours"]), str(r["market_freeze_at_utc"])))
    return groups


def _choose_latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return min(rows, key=lambda r: (float(r["lead_hours"]), str(r["market_freeze_at_utc"]))) if rows else None


def _choose_earliest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda r: (float(r["lead_hours"]), str(r["market_freeze_at_utc"]))) if rows else None


def _window(lo: float, hi: float, chooser: Callable[[list[dict[str, Any]]], dict[str, Any] | None]):
    def select(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return chooser([r for r in rows if _eligible(r, lo, hi)])
    return select


POLICIES: dict[str, Callable[[list[dict[str, Any]]], dict[str, Any] | None]] = {
    "LATEST_1_72": _window(1.0, 72.0, _choose_latest),
    "EARLIEST_1_72": _window(1.0, 72.0, _choose_earliest),
    "LATEST_1_12": _window(1.0, 12.0, _choose_latest),
    "EARLIEST_1_12": _window(1.0, 12.0, _choose_earliest),
}


def _policy_metrics(groups: dict[tuple[str, str, str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for policy, selector in POLICIES.items():
        selected = [row for rows in groups.values() if (row := selector(rows)) is not None]
        arms: dict[str, Any] = {}
        for arm in ARM_NAMES:
            rows = [r for r in selected if (r.get("arms") or {}).get(arm) is not None]
            hits = sum(int(int(r["arms"][arm]) == int(r["actual_total_bucket"])) for r in rows)
            arms[arm] = {
                "count": len(rows),
                "hits": hits,
                "accuracy": hits / len(rows) if rows else None,
            }
        output[policy] = {
            "selected_fixture_count": len(selected),
            "arms": arms,
        }
    return output


def _stability(groups: dict[tuple[str, str, str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for arm in ARM_NAMES:
        multi = 0
        stable = 0
        earliest_latest_comparable = 0
        earliest_latest_agree = 0
        pick_cardinality_hist: dict[str, int] = defaultdict(int)
        for rows in groups.values():
            arm_rows = [r for r in rows if (r.get("arms") or {}).get(arm) is not None]
            if not arm_rows:
                continue
            picks = {int(r["arms"][arm]) for r in arm_rows}
            pick_cardinality_hist[str(len(picks))] += 1
            if len(arm_rows) >= 2:
                multi += 1
                if len(picks) == 1:
                    stable += 1
                earliest = _choose_earliest(arm_rows)
                latest = _choose_latest(arm_rows)
                if earliest is not None and latest is not None:
                    earliest_latest_comparable += 1
                    earliest_latest_agree += int(int(earliest["arms"][arm]) == int(latest["arms"][arm]))
        output[arm] = {
            "multi_snapshot_fixture_count": multi,
            "all_snapshots_same_pick_count": stable,
            "all_snapshots_same_pick_rate": stable / multi if multi else None,
            "earliest_latest_comparable": earliest_latest_comparable,
            "earliest_latest_agree": earliest_latest_agree,
            "earliest_latest_agreement_rate": earliest_latest_agree / earliest_latest_comparable if earliest_latest_comparable else None,
            "unique_pick_cardinality_histogram": dict(sorted(pick_cardinality_hist.items())),
        }
    return output


def main() -> int:
    preds = base._load(PREDICTIONS)
    groups = _settled_groups(preds)
    payload = {
        "schema_version": "V6.52.2-active-score-total-single-decision-audit-r1",
        "generated_at_utc": base._utcnow().isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS",
        "classification": "RETROSPECTIVE_TIMING_SENSITIVITY_DIAGNOSTIC_FORMAL_WEIGHT_0",
        "unique_settled_fixture_count_1_72h": len(groups),
        "policies": _policy_metrics(groups),
        "within_fixture_pick_stability": _stability(groups),
        "interpretation": (
            "Each policy chooses at most one already-frozen 1-72h snapshot per settled fixture. These timing policies are evaluated only to expose "
            "sensitivity to freeze choice. Because outcomes were already available when V6.52.2 was designed, policy comparisons are post-hoc "
            "diagnostics and cannot support promotion, threshold tuning or a formal execution-policy change."
        ),
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "one_snapshot_per_fixture_per_policy": True,
            "strict_lead_window_hours": [1, 72],
            "no_model_training": True,
            "no_prediction_change": True,
            "no_historical_prediction_backfill": True,
            "posthoc_policy_diagnostic": True,
            "not_promotion_evidence": True,
            "no_policy_winner_selection": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    base._write(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
