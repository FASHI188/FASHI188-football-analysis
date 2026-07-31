#!/usr/bin/env python3
"""E3g-0C: zero-paid-request budget audit and free forward collection design.

No credentialed API call, no model fit, no secret creation, no schedule deployment.
The only optional network inputs are two public, no-key JSON examples published by
The Odds API, used solely for schema validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import e3f0_pit_feature_coverage_entry as e3f0_entry  # noqa: E402
from platform_core import ROOT  # noqa: E402

e3f0 = e3f0_entry.audit
OUT = ROOT.parent / "artifacts/research/e3g0c_zero_cost_budget_design"
EXPECTED_SAMPLE = 6251
SELECTED_COMPETITION = "ENG_PremierLeague"
SELECTED_SEASON = "2024/25"
SELECTED_SPORT_KEY = "soccer_epl"
LOCAL_ZONE = ZoneInfo("Europe/London")
OFFSETS = {
    "T-72h": timedelta(hours=72),
    "T-24h": timedelta(hours=24),
    "T-6h": timedelta(hours=6),
    "T-90m": timedelta(minutes=90),
    "T-15m": timedelta(minutes=15),
}
CREDIT_LIMIT = 20_000
RETRY_RATE = 0.10
IDENTITY_RESERVE_RATE = 0.05


def repo_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_local_kickoff(raw: dict[str, str]) -> datetime:
    date_token = str(raw.get("Date", "")).strip()
    time_token = str(raw.get("Time", "")).strip()
    if not date_token or not time_token:
        raise ValueError("missing Date/Time")
    parsed = None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(f"{date_token} {time_token}", fmt)
            break
        except ValueError:
            pass
    if parsed is None:
        raise ValueError(f"unsupported kickoff: {date_token} {time_token}")
    return parsed.replace(tzinfo=LOCAL_ZONE).astimezone(timezone.utc)


def describe_ints(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
    }


def budget(unique_queries: int, markets: int, regions: int) -> dict[str, Any]:
    unit = 10 * markets * regions
    retry_requests = math.ceil(unique_queries * RETRY_RATE)
    identity_requests = math.ceil(unique_queries * IDENTITY_RESERVE_RATE)
    base = unique_queries * unit
    retry = retry_requests * unit
    identity = identity_requests * unit
    total = base + retry + identity
    return {
        "unique_query_times": unique_queries,
        "markets": markets,
        "regions": regions,
        "credits_per_request": unit,
        "base_credits": base,
        "retry_reserve_requests": retry_requests,
        "retry_reserve_credits": retry,
        "identity_mapping_reserve_requests": identity_requests,
        "identity_mapping_reserve_credits": identity,
        "reserved_total_credits": total,
        "fits_20000_base": base <= CREDIT_LIMIT,
        "fits_20000_with_reserve": total <= CREDIT_LIMIT,
        "remaining_after_base": CREDIT_LIMIT - base,
        "remaining_after_reserve": CREDIT_LIMIT - total,
    }


def validate_historical_sample(path: Path, require_draw: bool, require_lines: bool) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    for key in ("timestamp", "previous_timestamp", "next_timestamp", "data"):
        assert key in payload
    events = payload["data"] if isinstance(payload["data"], list) else [payload["data"]]
    assert events
    has_draw = False
    has_spreads_point = False
    has_totals_point = False
    bookmaker_updates = 0
    for event in events:
        assert event.get("commence_time")
        for bookmaker in event.get("bookmakers", []):
            if bookmaker.get("last_update"):
                bookmaker_updates += 1
            for market in bookmaker.get("markets", []):
                key = market.get("key")
                outcomes = market.get("outcomes", [])
                if key == "h2h" and any(str(o.get("name", "")).casefold() == "draw" for o in outcomes):
                    has_draw = True
                if key == "spreads" and any("point" in o for o in outcomes):
                    has_spreads_point = True
                if key == "totals" and any("point" in o for o in outcomes):
                    has_totals_point = True
    if require_draw:
        assert has_draw
    if require_lines:
        assert has_spreads_point and has_totals_point
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "timestamp": payload["timestamp"],
        "previous_timestamp": payload["previous_timestamp"],
        "next_timestamp": payload["next_timestamp"],
        "event_count": len(events),
        "bookmaker_last_update_count": bookmaker_updates,
        "has_h2h_draw": has_draw,
        "has_spreads_point": has_spreads_point,
        "has_totals_point": has_totals_point,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--historical-epl-sample", required=True)
    parser.add_argument("--historical-three-market-sample", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows, lineage = e3f0.reconstruct_fixed_sample()
    if len(rows) != EXPECTED_SAMPLE:
        raise RuntimeError(f"fixed sample {len(rows)} != {EXPECTED_SAMPLE}")

    raw_by_comp: dict[str, dict[str, dict[str, str]]] = {}
    completeness = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["competition_id"], row["season"])].append(row)
    for competition_id in sorted({row["competition_id"] for row in rows}):
        raw_by_comp[competition_id], _ = e3f0.load_raw_observations(competition_id)
    for (competition_id, season), group in sorted(grouped.items()):
        raw = raw_by_comp[competition_id]
        matched = sum(row["match_key"] in raw for row in group)
        timed = 0
        parsed = 0
        for row in group:
            source = raw.get(row["match_key"], {})
            if str(source.get("Date", "")).strip() and str(source.get("Time", "")).strip():
                timed += 1
                try:
                    parse_local_kickoff(source)
                    parsed += 1
                except ValueError:
                    pass
        completeness.append({
            "competition_id": competition_id,
            "season": season,
            "fixed_matches": len(group),
            "identity_matches": matched,
            "date_time_present": timed,
            "kickoff_parseable": parsed,
        })

    selected_rows = grouped[(SELECTED_COMPETITION, SELECTED_SEASON)]
    if len(selected_rows) != 340:
        raise RuntimeError(f"selected fixed sample changed: {len(selected_rows)}")
    selected_raw = raw_by_comp[SELECTED_COMPETITION]
    matches = []
    for row in selected_rows:
        source = selected_raw.get(row["match_key"])
        if not source:
            raise RuntimeError(f"identity missing {row['match_key']}")
        kickoff = parse_local_kickoff(source)
        matches.append({
            "match_key": row["match_key"],
            "kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
            "kickoff_dt": kickoff,
            "local_date": kickoff.astimezone(LOCAL_ZONE).date().isoformat(),
        })

    kickoff_groups: dict[datetime, list[str]] = defaultdict(list)
    for match in matches:
        kickoff_groups[match["kickoff_dt"]].append(match["match_key"])
    per_offset: dict[str, dict[str, Any]] = {}
    target_pairs_by_query: dict[datetime, list[tuple[str, str]]] = defaultdict(list)
    for label, offset in OFFSETS.items():
        queries: dict[datetime, list[str]] = defaultdict(list)
        for match in matches:
            target = match["kickoff_dt"] - offset
            queries[target].append(match["match_key"])
            target_pairs_by_query[target].append((match["match_key"], label))
        per_offset[label] = {
            "independent_query_times": len(queries),
            "target_matches_per_query": describe_ints([len(values) for values in queries.values()]),
        }
    union_query_count = len(target_pairs_by_query)

    full_budgets = {
        "h2h_eu": budget(union_query_count, 1, 1),
        "h2h_uk": budget(union_query_count, 1, 1),
        "h2h_eu_uk": budget(union_query_count, 1, 2),
        "three_markets_eu": budget(union_query_count, 3, 1),
        "three_markets_uk": budget(union_query_count, 3, 1),
        "three_markets_eu_uk": budget(union_query_count, 3, 2),
    }

    bundle_defs = {
        "core_T24_T90_T15": ["T-24h", "T-90m", "T-15m"],
        "core_plus_T6": ["T-24h", "T-6h", "T-90m", "T-15m"],
        "full_five": list(OFFSETS),
        "late_T6_T90_T15": ["T-6h", "T-90m", "T-15m"],
        "final_T90_T15": ["T-90m", "T-15m"],
    }
    bundles = {}
    for name, labels in bundle_defs.items():
        query_times = {
            match["kickoff_dt"] - OFFSETS[label]
            for match in matches
            for label in labels
        }
        bundles[name] = {
            "offsets": labels,
            "unique_query_times": len(query_times),
            "h2h_one_region": budget(len(query_times), 1, 1),
            "three_markets_one_region": budget(len(query_times), 3, 1),
            "h2h_two_regions": budget(len(query_times), 1, 2),
            "three_markets_two_regions": budget(len(query_times), 3, 2),
        }

    dates = Counter(match["local_date"] for match in matches)
    max_matches = max(dates.values())
    busiest_dates = sorted(date for date, count in dates.items() if count == max_matches)
    busiest = busiest_dates[0]
    busiest_matches = [match for match in matches if match["local_date"] == busiest]
    busiest_kickoff_groups = len({match["kickoff_dt"] for match in busiest_matches})

    odds_pages = math.ceil(max_matches / 10)
    fixture_list = 1
    odds_3hour_batch = 8 * odds_pages
    injuries_4hour_batch = 6
    lineup_full_per_match = 8 * max_matches
    full_batch_total = fixture_list + odds_3hour_batch + injuries_4hour_batch + lineup_full_per_match
    full_per_fixture_total = fixture_list + (8 + 6 + 8) * max_matches
    retry_reserve_target = 10
    reduced_lineup = 3 * max_matches
    final_odds_group_polls = 2 * busiest_kickoff_groups
    optimized_total = fixture_list + odds_3hour_batch + final_odds_group_polls + injuries_4hour_batch + reduced_lineup
    optimized_with_reserve = optimized_total + retry_reserve_target

    api_football = {
        "selected_league": SELECTED_COMPETITION,
        "selected_season": SELECTED_SEASON,
        "busiest_match_day": busiest,
        "busiest_match_days_tied": busiest_dates,
        "max_matches_on_day": max_matches,
        "independent_kickoff_groups_on_busiest_day": busiest_kickoff_groups,
        "free_daily_limit": 100,
        "free_per_minute_limit": 10,
        "full_requested_design": {
            "fixture_list": fixture_list,
            "odds_every_3h_batch": odds_3hour_batch,
            "injuries_every_4h_batch": injuries_4hour_batch,
            "lineups_T120_to_T15_every_15m_per_fixture": lineup_full_per_match,
            "total": full_batch_total,
            "remaining": 100 - full_batch_total,
            "ten_call_retry_reserve_fits": full_batch_total + retry_reserve_target <= 100,
        },
        "per_fixture_naive_design": {
            "fixture_list": fixture_list,
            "odds": 8 * max_matches,
            "injuries": 6 * max_matches,
            "lineups": 8 * max_matches,
            "total": full_per_fixture_total,
            "exceeds_by": full_per_fixture_total - 100,
        },
        "priority_reduced_design": {
            "fixture_list": fixture_list,
            "odds_every_3h_batch": odds_3hour_batch,
            "exact_final_odds_T90_T15_by_kickoff_group": final_odds_group_polls,
            "injuries_every_4h_batch": injuries_4hour_batch,
            "lineups_T90_T45_T15_per_fixture": reduced_lineup,
            "total": optimized_total,
            "retry_reserve": retry_reserve_target,
            "total_with_reserve": optimized_with_reserve,
            "remaining_after_reserve": 100 - optimized_with_reserve,
            "fits": optimized_with_reserve <= 100,
        },
        "batching_notes": {
            "fixtures": "one league/date request",
            "odds": "league/date batch; check paging.total; ten results per page",
            "injuries": "league/date batch where coverage permits; otherwise fixture fallback consumes one request each",
            "lineups": "fixture-specific; stop polling immediately after confirmed lineup appears",
        },
    }

    sample_audit = {
        "historical_epl_h2h": validate_historical_sample(Path(args.historical_epl_sample), True, False),
        "historical_three_market": validate_historical_sample(Path(args.historical_three_market_sample), False, True),
    }

    report = {
        "schema_version": "1.0",
        "research_id": "E3g-0C",
        "research_status": "PASS",
        "repository_head": repo_head(),
        "scope": "zero_paid_request_budget_and_forward_collection_design_only",
        "formal_weight": 0,
        "credentialed_api_calls": 0,
        "paid_api_calls": 0,
        "subscriptions": 0,
        "secrets_created": 0,
        "schedules_started": 0,
        "model_fits": 0,
        "candidate_probabilities": 0,
        "fixed_sample": {"count": len(rows), "lineage": lineage},
        "selection": {
            "competition_id": SELECTED_COMPETITION,
            "season": SELECTED_SEASON,
            "sport_key": SELECTED_SPORT_KEY,
            "reason": "tied maximum 340 parseable fixed identities; EPL has an official public historical sample and 2024/25 is wholly within the five-minute snapshot era",
            "completeness_table": completeness,
        },
        "schedule": {
            "match_count": len(matches),
            "independent_kickoff_times": len(kickoff_groups),
            "kickoff_matches_per_time": describe_ints([len(values) for values in kickoff_groups.values()]),
            "per_offset": per_offset,
            "all_five_union": {
                "independent_query_times": union_query_count,
                "target_match_offset_pairs_per_query": describe_ints([len(values) for values in target_pairs_by_query.values()]),
                "distinct_target_matches_per_query": describe_ints([
                    len({match_key for match_key, _ in values}) for values in target_pairs_by_query.values()
                ]),
            },
        },
        "the_odds_api": {
            "credit_formula": "10 * regions * markets per historical request",
            "credit_limit": CREDIT_LIMIT,
            "retry_reserve_rate": RETRY_RATE,
            "identity_mapping_reserve_rate": IDENTITY_RESERVE_RATE,
            "full_five_budgets": full_budgets,
            "bundles": bundles,
            "public_sample_audit": sample_audit,
        },
        "api_football": api_football,
        "security_design": {
            "api_key_storage": ["GitHub Secret", "local environment variable"],
            "forbidden": ["repository", "logs", "Artifact"],
            "raw_response_policy": "append-only",
            "required_metadata": ["observed_at_utc", "sha256", "provider_update_time", "request_parameters", "http_status"],
            "freeze_policy": "save final pre-kickoff version separately",
            "late_backfill_policy": "post-match additions never overwrite pre-match missing state",
            "actions_permissions": "contents: read",
            "auto_commit_push": False,
        },
        "verdict": {
            "recommendation": "API_FOOTBALL_FREE_FORWARD_FIRST",
            "reason": "The Odds API full synchronized historical design is credit-constrained and unverified; free forward capture establishes true observed_at with no purchase",
            "formal_assets_changed": 0,
            "start_e3g1": False,
        },
    }

    (out / "e3g0c_zero_cost_budget_design.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = [
        "# E3g-0C Zero-Paid-Request Budget and Forward Collection Design",
        "",
        f"- HEAD: `{report['repository_head']}`",
        f"- Selected: `{SELECTED_COMPETITION}` `{SELECTED_SEASON}` ({len(matches)} matches)",
        f"- Independent kickoffs: {len(kickoff_groups)}",
        f"- All-five independent historical query times: {union_query_count}",
        f"- The Odds API h2h EU with reserve: {full_budgets['h2h_eu']['reserved_total_credits']} credits",
        f"- The Odds API three markets EU with reserve: {full_budgets['three_markets_eu']['reserved_total_credits']} credits",
        f"- API-Football full busiest-day design: {full_batch_total}/100 calls",
        f"- API-Football reduced design with reserve: {optimized_with_reserve}/100 calls",
        "- Credentialed calls/subscriptions/model fits: 0/0/0",
        "- Recommendation: API-Football free forward collection first",
        "",
    ]
    (out / "e3g0c_zero_cost_budget_design.md").write_text("\n".join(markdown), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["research_status"],
            "selected_matches": len(matches),
            "kickoff_times": len(kickoff_groups),
            "query_times": union_query_count,
            "h2h_eu_reserved": full_budgets["h2h_eu"]["reserved_total_credits"],
            "three_eu_reserved": full_budgets["three_markets_eu"]["reserved_total_credits"],
            "api_football_full": full_batch_total,
            "api_football_reduced_reserved": optimized_with_reserve,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
