#!/usr/bin/env python3
"""V6.52.1 independence audit for V6.25.20 exact-total forward rankings.

Research/audit only; formal_weight=0.

V6.25.20 intentionally stores every immutable Active-Kambi freeze snapshot. That
is useful for lead-time diagnostics, but repeated snapshots of the same fixture
must not be treated as independent matches when deciding whether a >=100-match
review gate has been reached. This audit leaves every frozen prediction intact
and reports both snapshot-level and fixture-clustered metrics.

No model is trained, no prediction is changed, and no historical fixture is
backfilled.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_active_correct_score_total_forward_v62520 as base  # noqa: E402

PREDICTIONS = ROOT / "forward" / "v6_active_correct_score_total_predictions_v62520.json"
OUT = ROOT / "manifests" / "v6_active_correct_score_total_independence_audit_v6521_status.json"
ARM_NAMES = ("top_score_total", "numeric_mass_total", "partial_cdf_total", "consensus_total")


def _fixture_key(pred: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(pred.get("competition_id") or ""),
        str(pred.get("home_team") or ""),
        str(pred.get("away_team") or ""),
        str(pred.get("kickoff_at") or ""),
    )


def _settled_rows(preds: dict[str, Any]) -> list[dict[str, Any]]:
    results = base._result_lookup()
    rows: list[dict[str, Any]] = []
    for pred in preds.get("predictions") or []:
        key = _fixture_key(pred)
        score = results.get(key)
        if score is None:
            continue
        actual = base._bucket(score[0] + score[1])
        row = {
            "fixture_key": list(key),
            "competition_id": pred["competition_id"],
            "home_team": pred["home_team"],
            "away_team": pred["away_team"],
            "kickoff_at": pred["kickoff_at"],
            "market_freeze_at_utc": pred["market_freeze_at_utc"],
            "lead_hours": pred["lead_hours"],
            "lead_bucket": str(pred["lead_bucket"]),
            "actual_total_bucket": actual,
            "arms": {},
        }
        for name in ARM_NAMES:
            pick = (pred.get("arms") or {}).get(name)
            if pick is None:
                continue
            row["arms"][name] = {
                "pick": int(pick),
                "hit": int(int(pick) == actual),
            }
        rows.append(row)
    return rows


def _clustered_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = ["ALL_1_72", "H0_12", "H12_24", "H24_72", "OUTSIDE"]
    output: dict[str, Any] = {}
    for group in groups:
        if group == "ALL_1_72":
            group_rows = [r for r in rows if r["lead_bucket"] != "OUTSIDE"]
        else:
            group_rows = [r for r in rows if r["lead_bucket"] == group]

        group_fixture_keys = {tuple(r["fixture_key"]) for r in group_rows}
        arm_output: dict[str, Any] = {}
        for arm in ARM_NAMES:
            arm_rows = [r for r in group_rows if arm in r["arms"]]
            snapshot_count = len(arm_rows)
            snapshot_hits = sum(int(r["arms"][arm]["hit"]) for r in arm_rows)
            per_fixture: dict[tuple[str, str, str, str], Counter] = defaultdict(Counter)
            for r in arm_rows:
                key = tuple(r["fixture_key"])
                per_fixture[key]["snapshots"] += 1
                per_fixture[key]["hits"] += int(r["arms"][arm]["hit"])
            fixture_rates = [c["hits"] / c["snapshots"] for c in per_fixture.values() if c["snapshots"]]
            arm_output[arm] = {
                "snapshot_count": snapshot_count,
                "snapshot_hits": snapshot_hits,
                "snapshot_accuracy": snapshot_hits / snapshot_count if snapshot_count else None,
                "unique_fixture_count": len(per_fixture),
                "fixture_equal_weight_accuracy": sum(fixture_rates) / len(fixture_rates) if fixture_rates else None,
            }
        output[group] = {
            "settled_snapshot_count": len(group_rows),
            "unique_settled_fixture_count": len(group_fixture_keys),
            "arms": arm_output,
        }
    return output


def main() -> int:
    preds = base._load(PREDICTIONS)
    rows = _settled_rows(preds)
    by_fixture = Counter(tuple(r["fixture_key"]) for r in rows)
    duplicate_fixture_counts = [n for n in by_fixture.values() if n > 1]
    unique_1_72 = {
        tuple(r["fixture_key"])
        for r in rows
        if r["lead_bucket"] != "OUTSIDE"
    }
    metrics = _clustered_metrics(rows)
    review_ready = len(unique_1_72) >= 100

    payload = {
        "schema_version": "V6.52.1-active-score-total-independence-audit-r1",
        "generated_at_utc": base._utcnow().isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS",
        "classification": "RESEARCH_EVALUATION_AUDIT_FORMAL_WEIGHT_0",
        "source_prediction_file": str(PREDICTIONS.relative_to(ROOT)),
        "source_status_file": str(base.OUT.relative_to(ROOT)),
        "settled_snapshot_count": len(rows),
        "unique_settled_fixture_count": len(by_fixture),
        "unique_settled_fixture_count_1_72h": len(unique_1_72),
        "fixtures_with_repeated_settled_snapshots": len(duplicate_fixture_counts),
        "max_settled_snapshots_per_fixture": max(by_fixture.values(), default=0),
        "metrics_by_lead_bucket": metrics,
        "review_state": "REVIEW_READY_100_UNIQUE_FIXTURES_PLUS" if review_ready else "PENDING_100_UNIQUE_SETTLED_FIXTURES_1_72H",
        "audit_interpretation": (
            "V6.25.20 stores immutable freeze snapshots. Snapshot rows are correlated when multiple freezes belong to the same fixture; "
            "therefore snapshot_count must not be described as an independent match count. The >=100 review gate is evaluated on unique "
            "settled fixture identity within the preregistered 1-72h window. Snapshot metrics remain diagnostic only."
        ),
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "no_model_training": True,
            "no_prediction_change": True,
            "no_historical_backfill": True,
            "snapshot_rows_are_not_independent_matches": True,
            "review_gate_uses_unique_fixture_identity": True,
            "fixture_equal_weight_metric_is_cluster_robust_descriptive": True,
            "automatic_promotion": False,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False
        }
    }
    base._write(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
