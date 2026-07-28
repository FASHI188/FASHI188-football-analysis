#!/usr/bin/env python3
"""V6.47.1 static-roster operational closure.

Purpose
-------
Close the long-running roster-cache blocker without weakening match-PIT discipline.
The legacy V6.6 contract used a >=18 named-player heuristic for every current-roster
receipt. That is useful for secondary sources, but it can falsely reject an official
club page that explicitly labels a smaller list as the complete current first team
(e.g. Racing Santander during the summer window).

This audit therefore:
* preserves V6.6 strict evidence as the base;
* upgrades unresolved teams from fresh single-source official receipts with >=18 names;
* allows a <18 exception ONLY when a tier-1 official source explicitly labels the list
  complete and the evidence receipt records that assertion;
* never treats a current active match group as a complete roster;
* counts such an active group only as static context usable, with exact-match live
  resolution still mandatory before any dynamic context feature can run.

Research input governance only. No CURRENT/probability/formal-weight mutation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GAPS = ROOT / "manifests" / "v6_roster_gap_inventory_v6611_status.json"
REFRESH = ROOT / "evidence" / "team_current_roster_weekly" / "v6471_gap_refresh__20260728T020507Z.json"
ALIAS_FIX = ROOT / "evidence" / "team_current_roster_weekly" / "v6471_espanyol_alias_fix__20260728T021000Z.json"
TROYES = ROOT / "evidence" / "team_current_roster_weekly" / "v6471_troyes_active_group__20260728T021100Z.json"
OUT = ROOT / "manifests" / "v6_static_roster_operational_closure_v6471_status.json"
ACCEPTED = {"CURRENT_FIRST_TEAM", "CURRENT_REGISTERED_SQUAD"}


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def records(path: Path) -> list[dict[str, Any]]:
    obj = load(path)
    return [x for x in (obj.get("records") or []) if isinstance(x, dict)]


def key(rec: dict[str, Any]) -> tuple[str, str]:
    return str(rec.get("competition_id") or "").strip(), str(rec.get("team_name") or "").strip()


def official_single_source(rec: dict[str, Any]) -> bool:
    srcs = [s for s in (rec.get("sources") or []) if isinstance(s, dict)]
    if len(srcs) != 1:
        return False
    tier = str(srcs[0].get("source_tier") or "")
    gov = rec.get("governance") or {}
    return tier == "tier_1_official" and gov.get("single_source_player_list") is True and gov.get("cross_source_union") is False


def player_count(rec: dict[str, Any]) -> int:
    names = []
    for p in rec.get("players") or []:
        if isinstance(p, dict):
            name = str(p.get("player_name") or "").strip()
        else:
            name = str(p or "").strip()
        if name:
            names.append(name.casefold())
    return len(set(names))


def strict_eligible(rec: dict[str, Any]) -> tuple[bool, str]:
    if str(rec.get("roster_semantics") or "") not in ACCEPTED:
        return False, "semantic_not_strict"
    if not official_single_source(rec):
        return False, "not_single_tier1_official_source"
    n = player_count(rec)
    if n >= 18:
        return True, "standard_min18_contract"
    meta = rec.get("source_metadata") or {}
    if n >= 11 and meta.get("official_explicit_complete_roster") is True:
        return True, "tier1_explicit_complete_roster_exception"
    return False, "below_min18_without_explicit_complete_assertion"


def main() -> int:
    gaps = load(GAPS)
    team_count = int(gaps.get("team_count") or 0)
    base_strict = int(gaps.get("effective_strict_current_count") or 0)
    gap_rows = [g for g in (gaps.get("gaps") or []) if isinstance(g, dict)]
    gap_keys = {key(g) for g in gap_rows}

    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for path in (REFRESH, ALIAS_FIX):
        for rec in records(path):
            candidates[key(rec)] = rec

    strict_resolved = []
    strict_rejected = []
    for gkey in sorted(gap_keys):
        rec = candidates.get(gkey)
        if not rec:
            continue
        ok, reason = strict_eligible(rec)
        row = {
            "competition_id": gkey[0], "team_name": gkey[1],
            "player_count": player_count(rec), "decision": reason,
        }
        (strict_resolved if ok else strict_rejected).append(row)

    strict_after = min(team_count, base_strict + len(strict_resolved))
    remaining_strict_keys = gap_keys - {(r["competition_id"], r["team_name"]) for r in strict_resolved}

    active_context = []
    for rec in records(TROYES):
        rkey = key(rec)
        n = player_count(rec)
        if rkey in remaining_strict_keys and str(rec.get("roster_semantics") or "") == "CURRENT_FIRST_TEAM_ACTIVE_GROUP" and official_single_source(rec) and n >= 18:
            active_context.append({
                "competition_id": rkey[0], "team_name": rkey[1], "player_count": n,
                "status": "STATIC_CONTEXT_READY_ACTIVE_GROUP_ONLY",
                "strict_complete_roster": False,
                "match_freeze_live_resolution_required": True,
            })

    context_usable = min(team_count, strict_after + len(active_context))
    unresolved_operational = team_count - context_usable
    strict_gap_after = team_count - strict_after
    errors = []
    if team_count != 336:
        errors.append(f"expected_336_teams_got_{team_count}")
    if unresolved_operational != 0:
        errors.append(f"operational_static_context_gaps_remain:{unresolved_operational}")

    payload = {
        "schema_version": "V6.47.1-static-roster-operational-closure-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_OPERATIONAL_STATIC_CONTEXT_336" if not errors else "PARTIAL",
        "classification": "RESEARCH_INPUT_GOVERNANCE_NO_PROBABILITY_MUTATION",
        "baseline": {
            "team_count": team_count,
            "strict_current_count_from_latest_v6611": base_strict,
            "strict_gap_count_from_latest_v6611": len(gap_keys),
        },
        "strict_refresh": {
            "resolved_gap_count": len(strict_resolved),
            "resolved": strict_resolved,
            "rejected": strict_rejected,
            "strict_current_count_after_v6471": strict_after,
            "strict_current_gap_count_after_v6471": strict_gap_after,
        },
        "active_group_context": active_context,
        "operational_static_context": {
            "usable_team_count": context_usable,
            "team_count": team_count,
            "coverage": context_usable / team_count if team_count else 0.0,
            "unresolved_operational_gap_count": unresolved_operational,
            "meaning": "STATIC_DISCOVERY_CONTEXT_ONLY_NOT_MATCH_AVAILABILITY_OR_PREDICTED_XI",
        },
        "hard_guards": {
            "secondary_source_min_named_players_remains_18": True,
            "under18_exception_requires_single_tier1_official_explicit_complete_list": True,
            "active_group_never_claimed_as_complete_roster": True,
            "troyes_exact_match_live_resolution_required": True,
            "all_dynamic_availability_and_predicted_xi_resolved_at_match_freeze": True,
            "static_context_gap_may_not_block_independent_market_only_track": True,
            "no_cross_source_player_union": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
        "errors": errors,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
