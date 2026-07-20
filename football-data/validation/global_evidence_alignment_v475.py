#!/usr/bin/env python3
"""Validate global evidence routes and the mandatory recent-two-season scope.

Engineering/data-governance only. A mapped route is not acquired data. Missing
paid-provider credentials remain acquisition blockers and never become PASS data.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "platform_registry.json"
ROUTES = ROOT / "config" / "global_evidence_routes_v475.json"
SOURCES = ROOT / "config" / "evidence_sources_v470.json"
STAGES = ROOT / "config" / "stage_format_registry_v470.json"
SCOPE = ROOT / "config" / "recent_two_season_evidence_scope_v476.json"
OUT = ROOT / "manifests" / "global_evidence_alignment_v475_status.json"
LINEUP_ROOT = ROOT / "evidence" / "lineups"
MARKET_ROOT = ROOT / "evidence" / "markets"

COMPLEX_STAGE_DOMAINS = {
    "ARG_Primera", "USA_MLS", "SUI_SuperLeague", "SCO_Premiership",
    "UEFA_ChampionsLeague", "JPN_J1", "KOR_KLeague1",
}
CREDENTIALS = [
    "THE_ODDS_API_KEY", "ODDSPAPI_KEY", "SPORTMONKS_API_TOKEN", "API_FOOTBALL_KEY",
]


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def count_jsonl_rows(root: Path) -> tuple[int, int]:
    files = rows = 0
    if not root.exists():
        return files, rows
    for path in sorted(root.glob("**/*.jsonl")):
        files += 1
        with path.open("r", encoding="utf-8") as handle:
            rows += sum(1 for line in handle if line.strip())
    return files, rows


def audit() -> dict[str, Any]:
    registry = load(REGISTRY)
    routes = load(ROUTES)
    sources = load(SOURCES)
    stages = load(STAGES)
    scope = load(SCOPE)

    registered = [str(item["competition_id"]) for item in registry.get("competitions", [])]
    registered_set = set(registered)
    route_map = routes.get("competitions", {})
    scope_map = scope.get("competitions", {})
    stage_map = stages.get("competitions", {})

    structural_errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if len(registered) != 17 or len(registered_set) != 17:
        structural_errors.append({
            "code": "registered_competition_count_invalid",
            "expected": 17,
            "actual": len(registered),
            "unique": len(registered_set),
        })
    if registered_set != set(route_map):
        structural_errors.append({
            "code": "route_registry_mismatch",
            "missing_routes": sorted(registered_set - set(route_map)),
            "extra_routes": sorted(set(route_map) - registered_set),
        })
    if registered_set != set(scope_map):
        structural_errors.append({
            "code": "two_season_scope_registry_mismatch",
            "missing_scope": sorted(registered_set - set(scope_map)),
            "extra_scope": sorted(set(scope_map) - registered_set),
        })

    per_competition: dict[str, Any] = {}
    for cid in sorted(registered_set):
        route = route_map.get(cid) or {}
        scope_item = scope_map.get(cid) or {}
        errors: list[str] = []
        seasons = scope_item.get("mandatory_backfill_seasons")
        if not route.get("the_odds_api_sport_key"):
            errors.append("missing_the_odds_api_sport_key")
        if not route.get("historical_odds_start_utc"):
            errors.append("missing_historical_odds_start_utc")
        if route.get("market_route") != "mapped":
            errors.append("market_route_not_mapped")
        if not route.get("stage_class"):
            errors.append("missing_stage_class")
        if not isinstance(seasons, list) or len(seasons) != 2 or len(set(map(str, seasons))) != 2:
            errors.append("mandatory_backfill_seasons_must_be_exactly_two_unique_seasons")
        if not scope_item.get("forward_capture_target"):
            errors.append("missing_forward_capture_target")

        if cid in COMPLEX_STAGE_DOMAINS:
            stage = stage_map.get(cid)
            if not stage:
                errors.append("missing_complex_stage_registry_entry")
            elif stage.get("format_verified") is not True:
                errors.append("complex_stage_format_not_verified")

        if errors:
            structural_errors.append({"code": "competition_route_invalid", "competition_id": cid, "errors": errors})

        per_competition[cid] = {
            "market_route": route.get("market_route"),
            "the_odds_api_sport_key": route.get("the_odds_api_sport_key"),
            "historical_odds_start_utc": route.get("historical_odds_start_utc"),
            "mandatory_backfill_seasons": seasons,
            "forward_capture_target": scope_item.get("forward_capture_target"),
            "stage_class": route.get("stage_class"),
            "complex_stage_format_verified": None if cid not in COMPLEX_STAGE_DOMAINS else bool((stage_map.get(cid) or {}).get("format_verified")),
            "structural_status": "PASS" if not errors else "FAIL",
        }

    market_policy = ((sources.get("policies") or {}).get("market") or {})
    lineup_policy = ((sources.get("policies") or {}).get("lineup") or {})
    injury_policy = ((sources.get("policies") or {}).get("injury_suspension") or {})
    if not market_policy.get("primary_historical_bookmaker_route"):
        structural_errors.append({"code": "missing_primary_market_route_policy"})
    if not lineup_policy.get("primary_observed_sources"):
        structural_errors.append({"code": "missing_lineup_route_policy"})
    if not injury_policy.get("primary_sources"):
        structural_errors.append({"code": "missing_injury_route_policy"})

    credential_status = {name: bool(os.environ.get(name)) for name in CREDENTIALS}
    missing_credentials = [name for name, present in credential_status.items() if not present]
    if missing_credentials:
        warnings.append({
            "code": "credential_gated_two_season_backfill_pending",
            "missing_credentials": missing_credentials,
            "reason": "Only the recent-two-season window is mandatory now, but timestamped market and broad PIT injury backfills still require provider access.",
        })

    lineup_files, lineup_rows = count_jsonl_rows(LINEUP_ROOT)
    market_files, market_rows = count_jsonl_rows(MARKET_ROOT)
    actual_acquisition = {
        "mandatory_scope_seasons_per_competition": 2,
        "lineup_jsonl_files": lineup_files,
        "lineup_rows": lineup_rows,
        "market_jsonl_files": market_files,
        "market_rows": market_rows,
        "timestamped_historical_market_two_season_backfill_complete": False,
        "pit_lineup_two_season_backfill_complete": False,
        "pit_injury_suspension_two_season_backfill_complete": False,
    }
    if market_rows == 0:
        warnings.append({"code": "no_normalized_historical_market_rows_present"})
    if lineup_rows == 0:
        warnings.append({"code": "no_public_lineup_rows_present"})

    structural_status = "PASS" if not structural_errors else "FAIL"
    acquisition_status = (
        "TWO_SEASON_ROUTES_ALIGNED_ACQUISITION_PENDING"
        if structural_status == "PASS" and (missing_credentials or not market_rows)
        else "STRUCTURAL_ALIGNMENT_FAILED"
        if structural_status != "PASS"
        else "PARTIAL_EVIDENCE_PRESENT"
    )

    return {
        "schema_version": "V4.7.6-global-evidence-two-season-alignment",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": structural_status,
        "acquisition_status": acquisition_status,
        "mandatory_backfill_window_seasons": 2,
        "older_history_policy": "retain_existing_not_required_for_completion",
        "registered_competition_count": len(registered_set),
        "route_mapped_competition_count": len(set(route_map) & registered_set),
        "scope_mapped_competition_count": len(set(scope_map) & registered_set),
        "complex_stage_domains_expected": sorted(COMPLEX_STAGE_DOMAINS),
        "structural_errors": structural_errors,
        "warnings": warnings,
        "credential_status": credential_status,
        "actual_acquisition": actual_acquisition,
        "competitions": per_competition,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "current_rule_change": False,
        "policy": "Mandatory completion is limited to each competition's recent two-season evidence window. Older history is retained but not a blocker. Actual PIT/timestamped records remain required; missing credentials must never be disguised as completed data.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "acquisition_status": report["acquisition_status"],
            "mandatory_backfill_window_seasons": report["mandatory_backfill_window_seasons"],
            "registered_competition_count": report["registered_competition_count"],
            "route_mapped_competition_count": report["route_mapped_competition_count"],
            "scope_mapped_competition_count": report["scope_mapped_competition_count"],
            "structural_error_count": len(report["structural_errors"]),
            "warning_count": len(report["warnings"]),
            "actual_acquisition": report["actual_acquisition"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
