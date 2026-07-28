#!/usr/bin/env python3
"""V6.47.0 team-context source-of-truth reconciliation.

This audit fixes a bookkeeping defect where later weekly/PIT reports could surface an
older or narrower roster count than the validated current-roster overlay. Static
roster evidence and match-bound PIT evidence are deliberately separate:

* static current roster cache -> latest validated V6.6 overlay + gap inventory;
* match-specific availability / predicted XI -> V6.31 match-bound PIT ledger;
* current manager -> weekly/overlay cache, refreshed at match freeze when stale/missing.

No probability, threshold, research weight, or CURRENT rule is changed here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "manifests" / "v6_current_roster_overlay_v669_status.json"
GAPS = ROOT / "manifests" / "v6_roster_gap_inventory_v6611_status.json"
PIT_TEAM = ROOT / "manifests" / "v6_pit_team_availability_ledger_v6300_status.json"
MATCH_PIT = ROOT / "manifests" / "v6_match_context_pit_v6310_status.json"
WEEKLY = ROOT / "manifests" / "team_configuration_weekly_scan_20260727_status.json"
OUT = ROOT / "manifests" / "v6_team_context_source_of_truth_v6470_status.json"

ACCEPTED_CURRENT_ROSTER_SEMANTICS = {"CURRENT_FIRST_TEAM", "CURRENT_REGISTERED_SQUAD"}


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def main() -> int:
    overlay = load(OVERLAY)
    gaps = load(GAPS)
    v630 = load(PIT_TEAM)
    v631 = load(MATCH_PIT)
    weekly = load(WEEKLY)

    # V6.6.15 is the authoritative static-roster ledger. Do not let the narrower
    # V6.30 semantic audit or an older weekly receipt overwrite these counters.
    team_count = int(overlay.get("team_baseline_count") or gaps.get("team_count") or 0)
    strict = int(overlay.get("effective_strict_current_roster_count") or 0)
    gap_count = int(gaps.get("unresolved_strict_current_gap_count") or 0)

    registered_overlay_rows = []
    for row in overlay.get("matched_overlays") or []:
        if not isinstance(row, dict):
            continue
        semantics = str(row.get("roster_semantics") or "")
        count = int(row.get("overlay_player_count") or 0)
        if semantics == "CURRENT_REGISTERED_SQUAD" and count >= 18:
            registered_overlay_rows.append({
                "competition_id": row.get("competition_id"),
                "team_name": row.get("resolved_team_name") or row.get("source_team_name"),
                "player_count": count,
                "observed_at_utc": row.get("observed_at_utc"),
            })

    v630_strict = 0
    axis = v630.get("axis_counts") or {}
    for grade, count in (axis.get("roster") or {}).items():
        if str(grade).startswith("STRICT_CURRENT"):
            v630_strict += int(count or 0)

    weekly_v630 = int(((weekly.get("strict_semantic_reaudit") or {}).get("strict_current_roster_count_under_v630")) or 0)

    errors = []
    if team_count != 336:
        errors.append(f"team_baseline_expected_336_got_{team_count}")
    if strict + gap_count != team_count:
        errors.append(f"roster_conservation_failed:{strict}+{gap_count}!={team_count}")
    if not overlay:
        errors.append("missing_validated_current_roster_overlay")
    if not gaps:
        errors.append("missing_roster_gap_inventory")
    if not v631:
        errors.append("missing_match_bound_pit_ledger_run")

    payload = {
        "schema_version": "V6.47.0-team-context-source-of-truth-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS" if not errors else "FAIL",
        "classification": "RESEARCH_INPUT_GOVERNANCE_NO_PROBABILITY_MUTATION",
        "source_of_truth": {
            "static_current_roster": {
                "manifest": str(OVERLAY.relative_to(ROOT)),
                "effective_strict_current_count": strict,
                "team_count": team_count,
                "unresolved_gap_count": gap_count,
                "gap_manifest": str(GAPS.relative_to(ROOT)),
                "accepted_semantics": sorted(ACCEPTED_CURRENT_ROSTER_SEMANTICS),
            },
            "match_bound_availability_and_predicted_xi": {
                "manifest": str(MATCH_PIT.relative_to(ROOT)),
                "status": v631.get("status") if v631 else "MISSING",
                "rule": "ONLY_EXACT_MATCH_ID_AND_PRE_FREEZE_EVIDENCE_IS_AUTHORITATIVE",
            },
            "manager": {
                "rule": "CACHE_IS_CONTEXT_ONLY; STALE_OR_MISSING_MANAGER_MUST_BE_REFRESHED_AT_MATCH_FREEZE",
            },
        },
        "detected_semantic_drift": {
            "v630_current_roster_overlay_predicate_was_narrower_than_v66_contract": bool(registered_overlay_rows),
            "registered_squad_rows_wrongly_excluded_by_v630_predicate": registered_overlay_rows,
            "latest_validated_static_strict_count": strict,
            "v630_reported_strict_count": v630_strict or weekly_v630,
            "weekly_reported_v630_strict_count": weekly_v630,
            "decision": "V630_OR_WEEKLY_COUNTS_MAY_NOT_OVERRIDE_LATEST_VALIDATED_STATIC_ROSTER_LEDGER",
        },
        "hard_runtime_policy": {
            "static_roster_cache_is_not_match_specific_availability": True,
            "weekly_legacy_full_context_never_satisfies_match_pit_gate": True,
            "current_registered_squad_is_valid_static_current_roster_semantics": True,
            "current_first_team_is_valid_static_current_roster_semantics": True,
            "missing_or_stale_match_context_triggers_live_pre_freeze_lookup": True,
            "post_freeze_or_post_kickoff_evidence_rejected": True,
            "actual_lineup_never_backfills_predicted_xi": True,
            "no_artificial_player_or_availability_inference": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
        },
        "errors": errors,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
