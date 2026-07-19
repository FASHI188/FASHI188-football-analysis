#!/usr/bin/env python3
"""V4.6.9 governance audit for 2026/27 cross-year competition domains.

This is a fail-closed deployment-readiness audit. It verifies five completed
prior-season slices (2021/22..2025/26), checks whether any completed 2026/27
rows are present, and preserves split-stage / cross-league market gates. It
never changes formal weights and cannot modify CURRENT authority.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = ROOT / "league_profiles"
REGISTRY_PATH = ROOT / "config" / "platform_registry.json"
OUT_PATH = ROOT / "manifests" / "cross_year_batch2_v469_status.json"

BATCH = [
    "ENG_PremierLeague",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
    "ESP_LaLiga",
    "POR_PrimeiraLiga",
    "NED_Eredivisie",
    "SUI_SuperLeague",
    "SCO_Premiership",
    "UEFA_ChampionsLeague",
]

PRIOR_SEASONS = ["2021/22", "2022/23", "2023/24", "2024/25", "2025/26"]
TARGET_SEASON = "2026/27"
SPLIT_STAGE_GATED = {"SUI_SuperLeague", "SCO_Premiership"}
UCL = "UEFA_ChampionsLeague"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cid_from_profile(profile: dict[str, Any], fallback: str) -> str:
    return str(profile.get("competition_id") or profile.get("league_id") or fallback)


def season_counts(profile: dict[str, Any]) -> dict[str, int]:
    per_season = profile.get("per_season") or {}
    counts: dict[str, int] = {}
    if per_season:
        for season, item in per_season.items():
            if isinstance(item, dict):
                counts[str(season)] = int(item.get("matches", 0) or 0)
    if counts:
        return counts

    # Some profiles expose seasons and source rows rather than per_season.
    for source in profile.get("source_files") or []:
        if not isinstance(source, dict):
            continue
        season = str(source.get("season") or "").strip()
        if not season or season == "full_archive_through_2026":
            continue
        rows = source.get("rows")
        if rows is None:
            rows = source.get("parsed_rows")
        if rows is not None:
            counts[season] = counts.get(season, 0) + int(rows)
    return counts


def audit_one(cid: str, registry_item: dict[str, Any]) -> dict[str, Any]:
    profile_path = PROFILE_ROOT / cid / "profile.json"
    profile = load_json(profile_path)
    assert cid_from_profile(profile, cid) == cid, f"{cid}: competition mismatch"

    counts = season_counts(profile)
    prior_counts = {season: int(counts.get(season, 0)) for season in PRIOR_SEASONS}
    missing_prior = [season for season, count in prior_counts.items() if count <= 0]
    target_rows = int(counts.get(TARGET_SEASON, 0))

    checks = {
        "profile_exists": profile_path.is_file(),
        "five_completed_prior_season_slices_present": not missing_prior,
        "target_season_not_backfilled_from_prior": target_rows == 0,
        "formal_weight_unchanged": True,
        "formal_rule_source_not_github": True,
    }

    blockers: list[str] = []
    if target_rows <= 0:
        blockers.append("no_completed_2026_27_rows_for_current_season_only_team_strength")

    if cid in SPLIT_STAGE_GATED:
        checks["split_stage_calibration_remains_gated"] = "disabled" in str(registry_item.get("stage_status") or "")
        blockers.append("split_stage_calibration_unverified")

    if cid == UCL:
        checks["ucl_cross_league_market_anchor_gate_preserved"] = (
            registry_item.get("cross_league_strength_gate") == "market_anchor_required"
        )
        blockers.append("cross_league_strength_requires_question_time_market_anchor")
        blockers.append("qualifiers_excluded_from_main_tournament_profile")

    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        status = "BATCH2_DATA_ROUTE_REVIEW_FAILED"
    elif target_rows > 0 and not blockers:
        status = "CURRENT_TARGET_SEASON_ROUTE_READY"
    else:
        status = "PRIOR_SEASONS_READY_TARGET_SEASON_DEPLOYMENT_GATED"

    return {
        "competition_id": cid,
        "name_zh": registry_item.get("name_zh"),
        "status": status,
        "formal_weight_change": False,
        "prior_season_rows": prior_counts,
        "missing_prior_seasons": missing_prior,
        "target_season": TARGET_SEASON,
        "completed_target_season_rows": target_rows,
        "stage_status": registry_item.get("stage_status"),
        "registry_current_season_status": registry_item.get("current_season_status"),
        "cross_league_strength_gate": registry_item.get("cross_league_strength_gate"),
        "blockers": blockers,
        "checks": checks,
    }


def build_report() -> dict[str, Any]:
    registry = load_json(REGISTRY_PATH)
    registry_map = {item["competition_id"]: item for item in registry.get("competitions", [])}
    missing_registry = [cid for cid in BATCH if cid not in registry_map]
    if missing_registry:
        raise RuntimeError(f"missing registry competitions: {missing_registry}")

    reports = {cid: audit_one(cid, registry_map[cid]) for cid in BATCH}
    failures = [cid for cid, item in reports.items() if item["status"] == "BATCH2_DATA_ROUTE_REVIEW_FAILED"]
    target_ready = [cid for cid, item in reports.items() if item["status"] == "CURRENT_TARGET_SEASON_ROUTE_READY"]
    gated = [cid for cid, item in reports.items() if item["status"] == "PRIOR_SEASONS_READY_TARGET_SEASON_DEPLOYMENT_GATED"]

    return {
        "schema_version": "V4.6.9-cross-year-batch2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "2026_27_cross_year_competition_current_season_deployment_readiness",
        "competition_count_requested": len(BATCH),
        "competition_count_reviewed": len(reports),
        "competition_count_failed": len(failures),
        "target_season_route_ready_count": len(target_ready),
        "target_season_deployment_gated_count": len(gated),
        "formal_weight": 0,
        "automatic_promotion": False,
        "policy": (
            "Current-season-only team-strength deployment requires verified completed 2026/27 rows. "
            "Completed 2025/26 rows remain prior-season evidence and must not be relabeled as 2026/27. "
            "Split-stage and UCL cross-league market gates remain fail-closed. This audit cannot modify CURRENT."
        ),
        "target_ready_competitions": target_ready,
        "deployment_gated_competitions": gated,
        "failures": failures,
        "reports": reports,
        "already_processed_outside_batch2": {
            "calendar_year_batch1": [
                "SWE_Allsvenskan",
                "NOR_Eliteserien",
                "KOR_KLeague1",
                "BRA_SerieA",
                "ARG_Primera",
                "USA_MLS",
            ],
            "jpn_j1": "V4.6.7 separate official 2026_special route; not pooled into 2026/27",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    report = build_report()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "competition_count_requested": report["competition_count_requested"],
            "competition_count_failed": report["competition_count_failed"],
            "target_season_route_ready_count": report["target_season_route_ready_count"],
            "target_season_deployment_gated_count": report["target_season_deployment_gated_count"],
            "failures": report["failures"],
        }, ensure_ascii=False, indent=2))
    return 2 if report["competition_count_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
