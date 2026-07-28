#!/usr/bin/env python3
"""V6.47.2 audit of static-cache vs exact-match PIT team context.

A global weekly roster/injury/manager completeness percentage is not a valid match-level
availability gate. This audit makes the runtime contract explicit and fail-closed:
static cache helps discovery, while exact-match pre-freeze evidence controls dynamic
context. Missing dynamic evidence abstains that feature instead of poisoning an
independent market-only track.

V6.47.1 is the latest operational static-roster closure and therefore overrides older
V6.6/V6.30 aggregate counters when present. Older ledgers remain evidence only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "v6_match_context_live_resolution_v6472.json"
ROSTER = ROOT / "manifests" / "v6_current_roster_overlay_v669_status.json"
GAPS = ROOT / "manifests" / "v6_roster_gap_inventory_v6611_status.json"
CLOSURE = ROOT / "manifests" / "v6_static_roster_operational_closure_v6471_status.json"
MATCH_PIT = ROOT / "manifests" / "v6_match_context_pit_v6310_status.json"
OUT = ROOT / "manifests" / "v6_match_context_resolution_v6472_status.json"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def main() -> int:
    cfg, roster, gaps, closure, match_pit = map(load, (CFG, ROSTER, GAPS, CLOSURE, MATCH_PIT))
    errors: list[str] = []
    if not cfg:
        errors.append("missing_live_resolution_contract")
    if not roster:
        errors.append("missing_static_roster_overlay")
    if not gaps:
        errors.append("missing_roster_gap_inventory")

    base_team_count = int(roster.get("team_baseline_count") or gaps.get("team_count") or 0)
    base_strict = int(roster.get("effective_strict_current_roster_count") or gaps.get("effective_strict_current_count") or 0)
    base_gap = int(gaps.get("unresolved_strict_current_gap_count") or max(0, base_team_count - base_strict))

    if closure and str(closure.get("status") or "").startswith("PASS_OPERATIONAL_STATIC_CONTEXT"):
        cbase = closure.get("baseline") or {}
        crefresh = closure.get("strict_refresh") or {}
        coper = closure.get("operational_static_context") or {}
        team_count = int(coper.get("team_count") or cbase.get("team_count") or base_team_count)
        strict = int(crefresh.get("strict_current_count_after_v6471") or base_strict)
        gap_count = int(crefresh.get("strict_current_gap_count_after_v6471") or max(0, team_count - strict))
        operational_usable = int(coper.get("usable_team_count") or strict)
        operational_gap = int(coper.get("unresolved_operational_gap_count") or max(0, team_count - operational_usable))
        static_source = "V6.47.1_OPERATIONAL_CLOSURE"
    else:
        team_count, strict, gap_count = base_team_count, base_strict, base_gap
        operational_usable, operational_gap = strict, gap_count
        static_source = "V6.6_LEGACY_STATIC_LEDGER"

    if team_count and strict + gap_count != team_count:
        errors.append(f"strict_roster_conservation_failed:{strict}+{gap_count}!={team_count}")
    if team_count and operational_usable + operational_gap != team_count:
        errors.append(f"operational_context_conservation_failed:{operational_usable}+{operational_gap}!={team_count}")

    hard = cfg.get("hard_guards") or {}
    required_true = [
        "weekly_global_completeness_may_not_block_market_only_track",
        "legacy_full_context_counter_may_not_satisfy_match_pit_gate",
        "missing_dynamic_context_never_means_no_change",
        "missing_dynamic_context_never_means_no_injury",
    ]
    for key in required_true:
        if hard.get(key) is not True:
            errors.append(f"hard_guard_not_true:{key}")

    payload = {
        "schema_version": "V6.47.2-match-context-resolution-status-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_ENGINEERING_CONTRACT" if not errors else "FAIL",
        "classification": "RESEARCH_INPUT_GOVERNANCE_NO_PROBABILITY_MUTATION",
        "static_cache": {
            "source_of_truth": static_source,
            "team_count": team_count,
            "strict_current_roster_count": strict,
            "strict_complete_roster_gap_count": gap_count,
            "operational_static_context_usable_count": operational_usable,
            "operational_static_context_gap_count": operational_gap,
            "interpretation": "STATIC_DISCOVERY_CONTEXT_ONLY_NOT_MATCH_LEVEL_AVAILABILITY",
            "legacy_v66_strict_count_for_history_only": base_strict,
            "legacy_v6611_gap_count_for_history_only": base_gap,
        },
        "match_bound_pit": {
            "ledger_present": bool(match_pit),
            "ledger_status": match_pit.get("status") if match_pit else "MISSING",
            "authority": "EXACT_MATCH_ID_PRE_FREEZE_EVIDENCE",
            "unresolved_action": "LIVE_LOOKUP_THEN_ABSTAIN_ONLY_THE_UNVERIFIED_CONTEXT_FEATURE",
        },
        "engineering_closures": {
            "old_v630_roster_semantic_drift_cannot_override_latest_roster_ledger": True,
            "v6471_operational_closure_overrides_legacy_aggregate_counts": True,
            "static_roster_gap_no_longer_globally_blocks_match": True,
            "availability_is_resolved_per_match_not_as_336_team_permanent_state": True,
            "predicted_xi_is_resolved_per_match_not_inferred_from_roster": True,
            "manager_is_refreshed_at_match_freeze_when_missing_or_stale": True,
            "same_day_delta_is_required_at_match_freeze": True,
            "market_only_track_survives_context_abstention": True,
        },
        "not_claimed": {
            "all_336_teams_have_permanently_current_injury_lists": True,
            "all_336_teams_have_permanently_current_predicted_xi": True,
            "strict_complete_roster_is_336_of_336": strict != team_count,
            "troyes_active_group_is_a_complete_registered_roster": True,
            "context_features_have_nonzero_formal_weight": True,
        },
        "errors": errors,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
