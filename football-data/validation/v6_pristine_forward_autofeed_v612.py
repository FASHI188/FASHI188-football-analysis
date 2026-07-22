#!/usr/bin/env python3
"""V6.1.2 automatic fixture and result feeder for the pristine forward ledger.

Fixture candidates are derived only from already-frozen prospective market evidence.
A prediction becomes eligible when kickoff is between 1 and 72 hours away. Results are
proposed only for previously frozen ledger matches at least two hours after kickoff and
only when a matching completed 90-minute row exists in the processed result repository.

This feeder never computes probabilities and never edits ledger events directly.
"""
from __future__ import annotations

import json
import sys
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _load_array(path: Path, schema: str, key: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": schema, key: []}
    data = load_json(path)
    if data.get("schema_version") != schema or not isinstance(data.get(key), list):
        raise PlatformError(f"invalid inbox envelope: {path}")
    return data


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


def _merge_fixtures(existing: list[dict[str, Any]], generated: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged = list(existing)
    index = {
        (str(item.get("competition_id") or ""), str(item.get("source_fixture_id") or "")): item
        for item in existing
    }
    stats: Counter = Counter()
    for item in generated:
        key = (item["competition_id"], item["source_fixture_id"])
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


def _result_candidates(now: datetime, ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats: Counter = Counter()
    candidates: list[dict[str, Any]] = []
    cache: dict[str, list[Any]] = {}
    for event in _open_predictions(ledger):
        stats["open_predictions_seen"] += 1
        identity = event["payload"]["fixture_identity"]
        kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
        if now < kickoff + MIN_RESULT_AGE:
            stats["not_old_enough"] += 1
            continue
        cid = identity["competition_id"]
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
        if len(matches) != 1:
            stats["completed_result_not_unique"] += 1
            continue
        match = matches[0]
        candidate = {
            "competition_id": cid,
            "source_fixture_id": identity["source_fixture_id"],
            "status": "final_90",
            "settlement_scope": "90_minutes_including_stoppage",
            "home_goals_90": int(match.home_goals),
            "away_goals_90": int(match.away_goals),
            "source": {
                "name": "processed_results_repository",
                "url": None,
                "observed_at": now.isoformat(),
                "source_record_id": f"{match.source_path}|{match.date.date().isoformat()}|{match.home_team}|{match.away_team}",
            },
            "autofeed": {
                "schema_version": "V6.1.2-autofeed-result-r1",
                "processed_source_path": match.source_path,
            },
        }
        candidates.append(candidate)
        stats["result_candidates"] += 1
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

    fixture_candidates, fixture_scan = _prospective_candidates(now, domains, str(freeze["forward_start_date_utc"]))
    merged_fixtures, fixture_merge = _merge_fixtures(fixture_envelope["fixtures"], fixture_candidates)
    result_candidates, result_scan = _result_candidates(now, ledger)
    merged_results, result_merge = _merge_results(result_envelope["results"], result_candidates)

    fixture_envelope["fixtures"] = merged_fixtures
    result_envelope["results"] = merged_results
    atomic_write_json(FIXTURE_INBOX, fixture_envelope)
    atomic_write_json(RESULT_INBOX, result_envelope)

    payload = {
        "schema_version": "V6.1.2-forward-autofeed-status-r1",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "freeze_window": {
            "minimum_hours_before_kickoff": MIN_FIXTURE_LEAD.total_seconds() / 3600.0,
            "maximum_hours_before_kickoff": MAX_FIXTURE_LEAD.total_seconds() / 3600.0,
            "minimum_hours_after_kickoff_for_result": MIN_RESULT_AGE.total_seconds() / 3600.0,
        },
        "fixture_scan": fixture_scan,
        "fixture_merge": fixture_merge,
        "fixture_inbox_count": len(merged_fixtures),
        "result_scan": result_scan,
        "result_merge": result_merge,
        "result_inbox_count": len(merged_results),
        "governance": {
            "prospective_evidence_only": True,
            "prediction_computation_in_autofeed": False,
            "ledger_event_mutation": False,
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
