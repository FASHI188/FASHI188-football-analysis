#!/usr/bin/env python3
"""V5.0.9 StatsBomb Open Data readiness audit for the 17 project domains.

The audit downloads the official Hudl/StatsBomb Open Data competition registry
and every matching season's match list. It distinguishes:

- a competition-season directory existing;
- enough matches to resemble broad season coverage;
- enough chronological seasons and matches for model validation;
- point-in-time replay safety.

The open-data ``match_available`` timestamp is the public dataset availability
time, not proof that the same event feed was available at the historical target
freeze. Therefore retrospective content coverage never automatically becomes a
formal point-in-time training route.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
OUT = FOOTBALL / "manifests" / "statsbomb_open_event_readiness_v509_status.json"

BASE = "https://raw.githubusercontent.com/hudl/open-data/master/data"
COMPETITIONS_URL = f"{BASE}/competitions.json"
USER_AGENT = "FASHI188-football-analysis/5.0.9 event-readiness"

PROJECT_MAP = {
    "ENG_PremierLeague": [("England", "Premier League")],
    "GER_Bundesliga": [("Germany", "1. Bundesliga")],
    "ITA_SerieA": [("Italy", "Serie A")],
    "FRA_Ligue1": [("France", "Ligue 1")],
    "ESP_LaLiga": [("Spain", "La Liga")],
    "POR_PrimeiraLiga": [("Portugal", "Primeira Liga"), ("Portugal", "Liga NOS")],
    "NED_Eredivisie": [("Netherlands", "Eredivisie")],
    "SUI_SuperLeague": [("Switzerland", "Super League")],
    "SCO_Premiership": [("Scotland", "Premiership")],
    "SWE_Allsvenskan": [("Sweden", "Allsvenskan")],
    "NOR_Eliteserien": [("Norway", "Eliteserien")],
    "JPN_J1": [("Japan", "J1 League")],
    "KOR_KLeague1": [("South Korea", "K League 1")],
    "BRA_SerieA": [("Brazil", "Serie A")],
    "ARG_Primera": [("Argentina", "Liga Profesional")],
    "USA_MLS": [("United States of America", "Major League Soccer"), ("United States", "Major League Soccer")],
    "UEFA_ChampionsLeague": [("Europe", "Champions League")],
}

DOMESTIC_BROAD_SEASON_MATCHES = 200
UCL_BROAD_SEASON_MATCHES = 80
MIN_BROAD_SEASONS = 3
MIN_TOTAL_MATCHES = 1000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def download_json(url: str) -> tuple[Any, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
    return json.loads(data.decode("utf-8")), {
        "url": url,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def season_threshold(competition_id: str) -> int:
    return UCL_BROAD_SEASON_MATCHES if competition_id == "UEFA_ChampionsLeague" else DOMESTIC_BROAD_SEASON_MATCHES


def matches_project_domain(item: dict[str, Any], expected: list[tuple[str, str]]) -> bool:
    if str(item.get("competition_gender") or "").lower() != "male":
        return False
    country = str(item.get("country_name") or "").strip().lower()
    name = str(item.get("competition_name") or "").strip().lower()
    return any(country == left.lower() and name == right.lower() for left, right in expected)


def audit_domain(
    competition_id: str,
    expected: list[tuple[str, str]],
    registry: list[dict[str, Any]],
) -> dict[str, Any]:
    entries = [item for item in registry if matches_project_domain(item, expected)]
    seasons = []
    failures = []
    threshold = season_threshold(competition_id)
    for item in sorted(entries, key=lambda value: str(value.get("season_name") or "")):
        competition_source_id = int(item["competition_id"])
        season_id = int(item["season_id"])
        url = f"{BASE}/matches/{competition_source_id}/{season_id}.json"
        try:
            matches, source = download_json(url)
            if not isinstance(matches, list):
                raise ValueError("matches payload is not a list")
        except Exception as exc:
            failures.append({
                "competition_source_id": competition_source_id,
                "season_id": season_id,
                "season_name": item.get("season_name"),
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        match_ids = [int(match["match_id"]) for match in matches if isinstance(match, dict) and match.get("match_id") is not None]
        public_available = parse_iso(item.get("match_available"))
        seasons.append({
            "competition_source_id": competition_source_id,
            "season_id": season_id,
            "season_name": str(item.get("season_name") or ""),
            "match_count": len(matches),
            "unique_match_id_count": len(set(match_ids)),
            "broad_season_coverage_proxy": len(matches) >= threshold,
            "match_updated": item.get("match_updated"),
            "match_available": item.get("match_available"),
            "match_available_360": item.get("match_available_360"),
            "public_availability_timestamp_present": public_available is not None,
            "matches_source": source,
        })

    total_matches = sum(int(item["match_count"]) for item in seasons)
    broad = [item for item in seasons if item["broad_season_coverage_proxy"]]
    coverage_gate = len(broad) >= MIN_BROAD_SEASONS and total_matches >= MIN_TOTAL_MATCHES
    public_timestamps_complete = bool(seasons) and all(item["public_availability_timestamp_present"] for item in seasons)
    formal_pit_safe = False
    if coverage_gate:
        status = "RETROSPECTIVE_COVERAGE_GATE_PASS_PIT_UNPROVEN"
    elif seasons:
        status = "RETROSPECTIVE_PARTIAL_COVERAGE_BELOW_GATE"
    else:
        status = "NO_MATCHING_OPEN_EVENT_ROUTE"
    return {
        "competition_id": competition_id,
        "status": status,
        "expected_source_names": [list(item) for item in expected],
        "registry_entry_count": len(entries),
        "season_file_count": len(seasons),
        "total_match_count": total_matches,
        "broad_season_count": len(broad),
        "broad_season_names": [item["season_name"] for item in broad],
        "coverage_gate_pass": coverage_gate,
        "public_availability_timestamps_complete": public_timestamps_complete,
        "formal_point_in_time_replay_safe": formal_pit_safe,
        "formal_pit_blocker": (
            "Open-data publication/update timestamps do not establish that the event feed was available at each historical pre-match freeze."
            if seasons
            else "No mapped event data."
        ),
        "season_broad_match_threshold": threshold,
        "seasons": seasons,
        "failures": failures,
        "formal_weight": 0,
    }


def run(*, write: bool) -> dict[str, Any]:
    registry, registry_source = download_json(COMPETITIONS_URL)
    if not isinstance(registry, list):
        raise RuntimeError("competition registry payload is not a list")
    reports = {
        competition_id: audit_domain(competition_id, expected, registry)
        for competition_id, expected in PROJECT_MAP.items()
    }
    retrospective_coverage = sorted(
        competition_id
        for competition_id, report in reports.items()
        if report["coverage_gate_pass"]
    )
    formal_pit_ready = sorted(
        competition_id
        for competition_id, report in reports.items()
        if report["formal_point_in_time_replay_safe"]
    )
    partial = sorted(
        competition_id
        for competition_id, report in reports.items()
        if report["season_file_count"] > 0 and not report["coverage_gate_pass"]
    )
    payload = {
        "schema_version": "V5.0.9-statsbomb-open-event-readiness-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS",
        "source_repository": "https://github.com/hudl/open-data",
        "license_notice": "Use subject to the Hudl/StatsBomb Open Data repository terms and attribution requirements.",
        "competition_registry_source": registry_source,
        "competition_count_audited": len(reports),
        "retrospective_coverage_gate_pass_domains": retrospective_coverage,
        "retrospective_partial_domains": partial,
        "formal_point_in_time_ready_domains": formal_pit_ready,
        "reports": reports,
        "gates": {
            "minimum_broad_seasons": MIN_BROAD_SEASONS,
            "minimum_total_matches": MIN_TOTAL_MATCHES,
            "domestic_broad_season_match_proxy": DOMESTIC_BROAD_SEASON_MATCHES,
            "ucl_broad_season_match_proxy": UCL_BROAD_SEASON_MATCHES,
            "historical_freeze_availability_proof_required": True,
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Open-event source audit only. Retrospective content coverage does not authorize historical PIT OOF, formal probability influence or provider-definition pooling."
    }
    if write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    payload = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps({
            "status": payload["status"],
            "retrospective_coverage_gate_pass_domains": payload["retrospective_coverage_gate_pass_domains"],
            "retrospective_partial_domains": payload["retrospective_partial_domains"],
            "formal_point_in_time_ready_domains": payload["formal_point_in_time_ready_domains"],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
