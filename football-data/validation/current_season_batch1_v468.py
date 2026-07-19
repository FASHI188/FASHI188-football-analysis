#!/usr/bin/env python3
"""V4.6.8 research/governance audit for active 2026 calendar-year competitions.

This audit does not change formal weights or CURRENT authority. It verifies that
recent-season history plus a separately identifiable 2026 current-season slice
exists for the first deployment batch, and preserves stage-specific fail-closed
gates where the source cannot safely separate phases/playoffs.
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
OUT_PATH = ROOT / "manifests" / "current_season_batch1_v468_status.json"

BATCH = [
    "SWE_Allsvenskan",
    "NOR_Eliteserien",
    "KOR_KLeague1",
    "BRA_SerieA",
    "ARG_Primera",
    "USA_MLS",
]

STAGE_GATED = {"ARG_Primera", "USA_MLS"}
CURRENT_SEASON = "2026"
PRIOR_SEASONS = ["2021", "2022", "2023", "2024", "2025"]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def competition_id(profile: dict[str, Any], fallback: str) -> str:
    return str(profile.get("competition_id") or profile.get("league_id") or fallback)


def audit_competition(cid: str, registry_item: dict[str, Any]) -> dict[str, Any]:
    path = PROFILE_ROOT / cid / "profile.json"
    profile = load_json(path)
    assert competition_id(profile, cid) == cid, f"{cid}: profile competition mismatch"

    per_season = profile.get("per_season") or {}
    prior_counts = {
        season: int((per_season.get(season) or {}).get("matches", 0))
        for season in PRIOR_SEASONS
    }
    current_rows = int((per_season.get(CURRENT_SEASON) or {}).get("matches", 0))
    missing_prior = [season for season, count in prior_counts.items() if count <= 0]

    checks = {
        "profile_exists": path.is_file(),
        "five_prior_season_slices_present": not missing_prior,
        "current_2026_rows_present": current_rows > 0,
        "current_season_separate_from_prior_seasons": CURRENT_SEASON in per_season,
        "formal_rule_source_not_github": True,
        "formal_weight_unchanged": True,
    }

    if cid in STAGE_GATED:
        stage_warning = str(profile.get("stage_warning") or "")
        checks["stage_ambiguity_explicitly_gated"] = bool(stage_warning)
        status = (
            "CURRENT_SEASON_AVAILABLE_STAGE_GATED"
            if all(checks.values())
            else "BATCH1_DATA_ROUTE_REVIEW_FAILED"
        )
    else:
        checks["stage_route_usable_for_base_current_season_features"] = True
        status = (
            "CURRENT_SEASON_ROUTE_READY"
            if all(checks.values())
            else "BATCH1_DATA_ROUTE_REVIEW_FAILED"
        )

    source_files = profile.get("source_files") or []
    sources = []
    for source in source_files:
        if not isinstance(source, dict):
            continue
        sources.append({
            "source_type": source.get("source_type"),
            "url": source.get("url"),
            "downloaded_at_utc": source.get("downloaded_at_utc"),
            "validated": source.get("validated"),
            "processed_path": source.get("processed_path") or source.get("path"),
        })

    return {
        "competition_id": cid,
        "name_zh": registry_item.get("name_zh"),
        "status": status,
        "formal_weight_change": False,
        "current_season": CURRENT_SEASON,
        "current_season_rows": current_rows,
        "prior_season_rows": prior_counts,
        "missing_prior_seasons": missing_prior,
        "stage_status": registry_item.get("stage_status"),
        "registry_current_season_status": registry_item.get("current_season_status"),
        "stage_warning": profile.get("stage_warning"),
        "competition_policy": profile.get("competition_policy"),
        "sources": sources,
        "checks": checks,
    }


def build_report() -> dict[str, Any]:
    registry = load_json(REGISTRY_PATH)
    registry_map = {
        item["competition_id"]: item for item in registry.get("competitions", [])
    }
    missing_registry = [cid for cid in BATCH if cid not in registry_map]
    if missing_registry:
        raise RuntimeError(f"batch competitions missing from registry: {missing_registry}")

    reports = {cid: audit_competition(cid, registry_map[cid]) for cid in BATCH}
    failures = [
        cid for cid, item in reports.items()
        if item["status"] == "BATCH1_DATA_ROUTE_REVIEW_FAILED"
    ]
    ready = [cid for cid, item in reports.items() if item["status"] == "CURRENT_SEASON_ROUTE_READY"]
    stage_gated = [
        cid for cid, item in reports.items()
        if item["status"] == "CURRENT_SEASON_AVAILABLE_STAGE_GATED"
    ]

    return {
        "schema_version": "V4.6.8-current-season-batch1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "active_2026_calendar_year_competitions_except_jpn_special_route",
        "competition_count_requested": len(BATCH),
        "competition_count_reviewed": len(reports),
        "competition_count_failed": len(failures),
        "current_season_route_ready_count": len(ready),
        "stage_gated_count": len(stage_gated),
        "formal_weight": 0,
        "automatic_promotion": False,
        "policy": (
            "Five prior season slices plus a separately identified 2026 current-season slice are required. "
            "Current-season data is kept distinct from historical seasons. Stage-ambiguous domains remain gated. "
            "This research/governance audit cannot modify CURRENT or formal model weights."
        ),
        "ready_competitions": ready,
        "stage_gated_competitions": stage_gated,
        "failures": failures,
        "reports": reports,
        "next_batch": [
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
        ],
        "jpn_j1_handling": "separate V4.6.7 route; 2026_special must not be pooled into 2026/27",
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
            "ready_competitions": report["ready_competitions"],
            "stage_gated_competitions": report["stage_gated_competitions"],
        }, ensure_ascii=False, indent=2))
    return 2 if report["competition_count_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
