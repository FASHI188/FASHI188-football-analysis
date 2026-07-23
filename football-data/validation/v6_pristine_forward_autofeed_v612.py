#!/usr/bin/env python3
"""V6.1.2 automatic fixture and result feeder for the pristine forward ledger.

Fixture candidates are derived only from already-frozen prospective market evidence. A prediction
becomes eligible when kickoff is between 1 and 72 hours away.

Result settlement is deliberately decoupled from the training-history repository. Once a frozen
prediction is at least two hours past kickoff, the feeder first queries ESPN's public soccer
scoreboard for a completed matching event and extracts the 90-minute score. The processed historical
repository is only an independent fallback/cross-check. This prevents a stale training warehouse from
blocking prospective settlement.

Already-frozen fixtures are removed from the transient fixture inbox before the immutable ledger is
re-run. Their original PREDICTION_FROZEN events remain untouched in the hash chain. This prevents a
later post-kickoff maintenance run from re-validating an already-frozen prediction against the current
clock and incorrectly labelling it as a late prediction.

This feeder never computes probabilities and never edits ledger events directly.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import (
    PlatformError,
    atomic_write_json,
    canonical_team_name,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    sha256_json,
)

FREEZE = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"
LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
FIXTURE_INBOX = ROOT / "forward" / "inbox" / "fixtures_v612.json"
RESULT_INBOX = ROOT / "forward" / "inbox" / "results_v612.json"
EVIDENCE_ROOT = ROOT / "evidence" / "markets_prospective"
OUT = ROOT / "manifests" / "v6_pristine_forward_autofeed_v612_status.json"
MIN_FIXTURE_LEAD = timedelta(hours=1)
MAX_FIXTURE_LEAD = timedelta(hours=72)
MIN_RESULT_AGE = timedelta(hours=2)
RESULT_KICKOFF_TOLERANCE = timedelta(hours=6)
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
UA = "football-v6.1.2-forward-settlement/2.0"

DOMAINS = {
    "ARG_Primera": "arg.1",
    "BRA_SerieA": "bra.1",
    "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1",
    "FRA_Ligue1": "fra.1",
    "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1",
    "JPN_J1": "jpn.1",
    "KOR_KLeague1": "kor.1",
    "NED_Eredivisie": "ned.1",
    "NOR_Eliteserien": "nor.1",
    "POR_PrimeiraLiga": "por.1",
    "SCO_Premiership": "sco.1",
    "SUI_SuperLeague": "sui.1",
    "SWE_Allsvenskan": "swe.1",
    "UEFA_ChampionsLeague": "uefa.champions",
    "USA_MLS": "usa.1",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _load_array(path: Path, schema: str, key: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": schema, key: []}
    data = load_json(path)
    if data.get("schema_version") != schema or not isinstance(data.get(key), list):
        raise PlatformError(f"invalid inbox envelope: {path}")
    return data


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise PlatformError("result source returned non-object JSON")
    return payload


def _auto_source_fixture_id(competition_id: str, kickoff: str, home: str, away: str) -> str:
    identity = {
        "competition_id": competition_id,
        "kickoff_at": kickoff,
        "home_team": normalize_team_token(home),
        "away_team": normalize_team_token(away),
    }
    return "auto_" + sha256_json(identity)[:24]


def _prospective_candidates(now: datetime, competition_ids: set[str], forward_start: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    stats: Counter = Counter()
    if not EVIDENCE_ROOT.exists():
        return [], {"evidence_directory_missing": 1}
    for path in sorted(EVIDENCE_ROOT.glob("*.json")):
        stats["files_seen"] += 1
        try:
            raw = load_json(path)
            cid = str(raw.get("competition_id") or "").strip()
            if cid not in competition_ids:
                stats["outside_frozen_domains"] += 1
                continue
            kickoff = parse_iso_datetime(str(raw.get("kickoff_utc") or ""), "kickoff_utc")
            observed = parse_iso_datetime(
                str(raw.get("source_observed_at_utc") or raw.get("freeze_utc") or raw.get("accessed_at_utc") or ""),
                "source_observed_at_utc",
            )
            if observed > now or observed >= kickoff:
                stats["invalid_observation_time"] += 1
                continue
            if kickoff.date().isoformat() < forward_start:
                stats["before_forward_start"] += 1
                continue
            lead = kickoff - now
            if lead < MIN_FIXTURE_LEAD:
                stats["too_close_or_started"] += 1
                continue
            if lead > MAX_FIXTURE_LEAD:
                stats["outside_72h_window"] += 1
                continue
            scope = str(raw.get("settlement_scope") or "")
            if scope not in {"90m_including_stoppage", "90_minutes_including_stoppage"}:
                stats["wrong_settlement_scope"] += 1
                continue
            home = canonical_team_name(cid, str(raw.get("home_team") or ""))
            away = canonical_team_name(cid, str(raw.get("away_team") or ""))
            season = str(raw.get("season") or "").strip()
            if not home or not away or not season:
                stats["missing_identity"] += 1
                continue
            kickoff_iso = kickoff.isoformat()
            source_fixture_id = _auto_source_fixture_id(cid, kickoff_iso, home, away)
            candidate = {
                "competition_id": cid,
                "source_fixture_id": source_fixture_id,
                "season": season,
                "stage": str(raw.get("stage") or "stage_unverified"),
                "kickoff_at": kickoff_iso,
                "home_team": home,
                "away_team": away,
                "status": "scheduled",
                "source": {
                    "name": str(raw.get("provider_name") or raw.get("provider_group") or "prospective_market_evidence"),
                    "url": str(raw.get("source_url") or "") or None,
                    "observed_at": observed.isoformat(),
                    "source_record_id": str(raw.get("raw_snapshot_sha256") or path.name),
                },
                "autofeed": {
                    "schema_version": "V6.1.2-autofeed-fixture-r1",
                    "evidence_path": str(path.relative_to(ROOT)),
                    "evidence_sha256": sha256_json(raw),
                    "lead_hours_at_autofeed": lead.total_seconds() / 3600.0,
                },
            }
            key = (cid, source_fixture_id)
            previous = selected.get(key)
            if previous is None or observed > previous[0]:
                selected[key] = (observed, candidate)
                stats["candidate_latest_snapshot_selected"] += 1
            else:
                stats["older_duplicate_snapshot"] += 1
        except Exception:
            stats["files_rejected"] += 1
    candidates = [item[1] for item in sorted(selected.values(), key=lambda pair: (pair[1]["kickoff_at"], pair[1]["competition_id"], pair[1]["home_team"]))]
    stats["fixture_candidates"] = len(candidates)
    return candidates, dict(sorted(stats.items()))


def _frozen_fixture_keys(ledger: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for event in ledger.get("events", []):
        if event.get("event_type") != "PREDICTION_FROZEN":
            continue
        identity = ((event.get("payload") or {}).get("fixture_identity") or {})
        cid = str(identity.get("competition_id") or "")
        source_fixture_id = str(identity.get("source_fixture_id") or "")
        if cid and source_fixture_id:
            keys.add((cid, source_fixture_id))
    return keys


def _merge_fixtures(
    existing: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    frozen_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats: Counter = Counter()
    merged: list[dict[str, Any]] = []
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing:
        key = (str(item.get("competition_id") or ""), str(item.get("source_fixture_id") or ""))
        if key in frozen_keys:
            stats["already_frozen_removed_from_inbox"] += 1
            continue
        merged.append(item)
        index[key] = item
    for item in generated:
        key = (item["competition_id"], item["source_fixture_id"])
        if key in frozen_keys:
            stats["already_frozen_skipped_from_regeneration"] += 1
            continue
        if key in index:
            stats["already_in_inbox"] += 1
            continue
        merged.append(item)
        index[key] = item
        stats["added"] += 1
    merged.sort(key=lambda item: (str(item.get("kickoff_at") or ""), str(item.get("competition_id") or ""), str(item.get("source_fixture_id") or "")))
    return merged, dict(sorted(stats.items()))


def _open_predictions(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    settled: set[str] = set()
    for event in ledger.get("events", []):
        if event.get("event_type") == "PREDICTION_FROZEN":
            predictions[str(event["match_id"])] = event
        elif event.get("event_type") == "RESULT_SETTLED":
            settled.add(str(event["match_id"]))
    return [event for match_id, event in predictions.items() if match_id not in settled]


def _score_value(raw: Any) -> int | None:
    if isinstance(raw, dict):
        raw = raw.get("value") if raw.get("value") is not None else raw.get("displayValue")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or abs(value - round(value)) > 1e-9:
        return None
    return int(round(value))


def _competitor_name_candidates(competitor: dict[str, Any]) -> list[str]:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    values = [team.get("displayName"), team.get("shortDisplayName"), team.get("name"), team.get("location")]
    return [str(value).strip() for value in values if value]


def _team_matches(cid: str, competitor: dict[str, Any], expected: str) -> bool:
    expected_token = normalize_team_token(expected)
    for raw in _competitor_name_candidates(competitor):
        try:
            canonical = canonical_team_name(cid, raw)
        except Exception:
            canonical = raw
        if normalize_team_token(canonical) == expected_token or normalize_team_token(raw) == expected_token:
            return True
    return False


def _regulation_score(competition: dict[str, Any], event: dict[str, Any]) -> tuple[int, int, str] | None:
    competitors = competition.get("competitors") or []
    if not isinstance(competitors, list):
        return None
    home = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "home"), None)
    away = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "away"), None)
    if not isinstance(home, dict) or not isinstance(away, dict):
        return None
    status = competition.get("status") if isinstance(competition.get("status"), dict) else event.get("status") or {}
    type_block = status.get("type") if isinstance(status.get("type"), dict) else {}
    completed = bool(type_block.get("completed")) or str(type_block.get("state") or "").lower() == "post"
    if not completed:
        return None
    try:
        period = int(status.get("period") or event.get("status", {}).get("period") or 0)
    except (TypeError, ValueError):
        period = 0
    label = " ".join(
        str(value or "").upper()
        for value in (type_block.get("name"), type_block.get("description"), type_block.get("detail"))
    )
    extra_time_or_shootout = period > 2 or any(token in label for token in ("EXTRA", "PENALT", "SHOOTOUT"))
    if not extra_time_or_shootout:
        home_score = _score_value(home.get("score"))
        away_score = _score_value(away.get("score"))
        if home_score is None or away_score is None:
            return None
        return home_score, away_score, "completed_final_score_regulation"
    home_lines = home.get("linescores") or []
    away_lines = away.get("linescores") or []
    if not isinstance(home_lines, list) or not isinstance(away_lines, list) or len(home_lines) < 2 or len(away_lines) < 2:
        return None
    home_first_two = [_score_value(row) for row in home_lines[:2]]
    away_first_two = [_score_value(row) for row in away_lines[:2]]
    if any(value is None for value in home_first_two + away_first_two):
        return None
    return int(sum(home_first_two)), int(sum(away_first_two)), "sum_first_two_period_linescores"


def _espn_result(
    identity: dict[str, Any],
    kickoff: datetime,
    now: datetime,
    cache: dict[tuple[str, str], tuple[dict[str, Any] | None, str | None, str]],
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    cid = str(identity["competition_id"])
    league = DOMAINS.get(cid)
    if not league:
        return None, "espn_domain_unmapped", {}
    date_token = kickoff.strftime("%Y%m%d")
    key = (cid, date_token)
    if key not in cache:
        query = urllib.parse.urlencode({"dates": date_token, "limit": 1000})
        url = f"{ESPN_BASE}/{league}/scoreboard?{query}"
        try:
            cache[key] = (_get_json(url), None, url)
        except Exception as exc:
            cache[key] = (None, f"{type(exc).__name__}: {exc}", url)
    payload, fetch_error, url = cache[key]
    if payload is None:
        return None, "espn_fetch_failed", {"error": fetch_error, "url": url}
    matches: list[tuple[dict[str, Any], dict[str, Any], datetime]] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        try:
            event_kickoff = parse_iso_datetime(str(event.get("date") or ""), "espn_event_date")
        except Exception:
            continue
        if abs(event_kickoff - kickoff) > RESULT_KICKOFF_TOLERANCE:
            continue
        competitions = event.get("competitions") or []
        if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
            continue
        competition = competitions[0]
        competitors = competition.get("competitors") or []
        if not isinstance(competitors, list):
            continue
        home = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "home"), None)
        away = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "away"), None)
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue
        if _team_matches(cid, home, str(identity["home_team"])) and _team_matches(cid, away, str(identity["away_team"])):
            matches.append((event, competition, event_kickoff))
    if len(matches) == 0:
        return None, "espn_identity_not_found", {"url": url}
    if len(matches) > 1:
        return None, "espn_identity_ambiguous", {"url": url, "match_count": len(matches)}
    event, competition, event_kickoff = matches[0]
    score = _regulation_score(competition, event)
    if score is None:
        status = competition.get("status") if isinstance(competition.get("status"), dict) else event.get("status") or {}
        return None, "espn_not_completed_or_regulation_score_unavailable", {"url": url, "event_id": event.get("id"), "status": status}
    home_goals, away_goals, extraction = score
    candidate = {
        "competition_id": cid,
        "source_fixture_id": identity["source_fixture_id"],
        "status": "final_90",
        "settlement_scope": "90_minutes_including_stoppage",
        "home_goals_90": home_goals,
        "away_goals_90": away_goals,
        "source": {
            "name": "ESPN public soccer scoreboard API",
            "url": url,
            "observed_at": now.isoformat(),
            "source_record_id": str(event.get("id") or "") or None,
        },
        "autofeed": {
            "schema_version": "V6.1.2-autofeed-result-r2",
            "result_provider": "espn_public_site_api",
            "event_id": event.get("id"),
            "event_kickoff_at": event_kickoff.isoformat(),
            "regulation_score_extraction": extraction,
            "kickoff_difference_seconds": abs((event_kickoff - kickoff).total_seconds()),
        },
    }
    return candidate, "espn_result_available", {"url": url, "event_id": event.get("id"), "extraction": extraction}


def _processed_result(
    identity: dict[str, Any],
    kickoff: datetime,
    cache: dict[str, list[Any]],
) -> tuple[tuple[int, int, Any] | None, str]:
    cid = str(identity["competition_id"])
    if cid not in cache:
        try:
            cache[cid] = read_processed_matches(cid)
        except Exception:
            cache[cid] = []
    matches = [
        match
        for match in cache[cid]
        if match.date.date() == kickoff.date()
        and normalize_team_token(match.home_team) == normalize_team_token(identity["home_team"])
        and normalize_team_token(match.away_team) == normalize_team_token(identity["away_team"])
    ]
    if len(matches) == 0:
        return None, "processed_result_missing"
    if len(matches) > 1:
        return None, "processed_result_ambiguous"
    match = matches[0]
    return (int(match.home_goals), int(match.away_goals), match), "processed_result_available"


def _result_candidates(now: datetime, ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats: Counter = Counter()
    candidates: list[dict[str, Any]] = []
    processed_cache: dict[str, list[Any]] = {}
    espn_cache: dict[tuple[str, str], tuple[dict[str, Any] | None, str | None, str]] = {}
    for event in _open_predictions(ledger):
        stats["open_predictions_seen"] += 1
        identity = event["payload"]["fixture_identity"]
        kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
        if now < kickoff + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1
            continue
        espn_candidate, espn_status, _espn_audit = _espn_result(identity, kickoff, now, espn_cache)
        stats[espn_status] += 1
        processed, processed_status = _processed_result(identity, kickoff, processed_cache)
        stats[processed_status] += 1
        if espn_candidate is not None:
            if processed is not None:
                ph, pa, match = processed
                if ph != int(espn_candidate["home_goals_90"]) or pa != int(espn_candidate["away_goals_90"]):
                    stats["result_source_conflict"] += 1
                    continue
                espn_candidate["autofeed"]["processed_crosscheck"] = {
                    "status": "MATCH",
                    "processed_source_path": match.source_path,
                }
                stats["processed_crosscheck_match"] += 1
            else:
                espn_candidate["autofeed"]["processed_crosscheck"] = {"status": processed_status}
            candidates.append(espn_candidate)
            stats["result_candidates"] += 1
            stats["result_candidates_espn"] += 1
            continue
        if processed is not None:
            ph, pa, match = processed
            candidate = {
                "competition_id": identity["competition_id"],
                "source_fixture_id": identity["source_fixture_id"],
                "status": "final_90",
                "settlement_scope": "90_minutes_including_stoppage",
                "home_goals_90": ph,
                "away_goals_90": pa,
                "source": {
                    "name": "processed_results_repository",
                    "url": None,
                    "observed_at": now.isoformat(),
                    "source_record_id": f"{match.source_path}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}",
                },
                "autofeed": {
                    "schema_version": "V6.1.2-autofeed-result-r2",
                    "result_provider": "processed_results_repository_fallback",
                    "processed_source_path": match.source_path,
                    "espn_status": espn_status,
                },
            }
            candidates.append(candidate)
            stats["result_candidates"] += 1
            stats["result_candidates_processed_fallback"] += 1
            continue
        stats["result_unresolved"] += 1
    candidates.sort(key=lambda item: (item["competition_id"], item["source_fixture_id"]))
    return candidates, dict(sorted(stats.items()))


def _merge_results(existing: list[dict[str, Any]], generated: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged = list(existing)
    index = {
        (str(item.get("competition_id") or ""), str(item.get("source_fixture_id") or "")): item
        for item in existing
    }
    stats: Counter = Counter()
    for item in generated:
        key = (item["competition_id"], item["source_fixture_id"])
        if key in index:
            existing_item = index[key]
            comparable_existing = (existing_item.get("home_goals_90"), existing_item.get("away_goals_90"))
            comparable_new = (item.get("home_goals_90"), item.get("away_goals_90"))
            if comparable_existing != comparable_new:
                stats["conflicting_existing_result"] += 1
            else:
                stats["already_in_inbox"] += 1
            continue
        merged.append(item)
        index[key] = item
        stats["added"] += 1
    merged.sort(key=lambda item: (str(item.get("competition_id") or ""), str(item.get("source_fixture_id") or "")))
    return merged, dict(sorted(stats.items()))


def main() -> int:
    now = _utc_now()
    freeze = load_json(FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    domains = set((freeze.get("domain_freeze") or {}).keys())
    if len(domains) != 17:
        raise PlatformError("V6.1.2 autofeed requires 17 frozen domains")

    fixture_envelope = _load_array(FIXTURE_INBOX, "V6.1.2-fixture-inbox-r1", "fixtures")
    result_envelope = _load_array(RESULT_INBOX, "V6.1.2-result-inbox-r1", "results")
    ledger = load_json(LEDGER) if LEDGER.exists() else {"schema_version": "V6.1.2-forward-ledger-r1", "events": []}
    frozen_keys = _frozen_fixture_keys(ledger)

    fixture_candidates, fixture_scan = _prospective_candidates(now, domains, str(freeze["forward_start_date_utc"]))
    merged_fixtures, fixture_merge = _merge_fixtures(fixture_envelope["fixtures"], fixture_candidates, frozen_keys)
    result_candidates, result_scan = _result_candidates(now, ledger)
    merged_results, result_merge = _merge_results(result_envelope["results"], result_candidates)

    fixture_envelope["fixtures"] = merged_fixtures
    result_envelope["results"] = merged_results
    atomic_write_json(FIXTURE_INBOX, fixture_envelope)
    atomic_write_json(RESULT_INBOX, result_envelope)

    payload = {
        "schema_version": "V6.1.2-forward-autofeed-status-r2",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "freeze_window": {
            "minimum_hours_before_kickoff": MIN_FIXTURE_LEAD.total_seconds() / 3600.0,
            "maximum_hours_before_kickoff": MAX_FIXTURE_LEAD.total_seconds() / 3600.0,
            "minimum_hours_after_kickoff_for_result": MIN_RESULT_AGE.total_seconds() / 3600.0,
            "result_identity_kickoff_tolerance_hours": RESULT_KICKOFF_TOLERANCE.total_seconds() / 3600.0,
        },
        "fixture_scan": fixture_scan,
        "fixture_merge": fixture_merge,
        "fixture_inbox_count": len(merged_fixtures),
        "frozen_fixture_key_count": len(frozen_keys),
        "result_scan": result_scan,
        "result_merge": result_merge,
        "result_inbox_count": len(merged_results),
        "governance": {
            "prospective_evidence_only": True,
            "prediction_computation_in_autofeed": False,
            "ledger_event_mutation": False,
            "already_frozen_fixture_revalidation_against_current_clock": False,
            "training_history_and_settlement_source_decoupled": True,
            "espn_public_scoreboard_primary_settlement_source": True,
            "processed_repository_fallback_or_crosscheck_only": True,
            "extra_time_requires_first_two_period_regulation_extraction": True,
            "ambiguous_or_conflicting_result_fail_closed": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())