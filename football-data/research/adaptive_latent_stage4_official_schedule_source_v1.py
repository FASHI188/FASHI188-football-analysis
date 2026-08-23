#!/usr/bin/env python3
"""Zero-label official-schedule projection for football3 Stage4 target identity.

No network, provider, label dataset, training, scoring, or CURRENT capability.
Accepts only a pre-extracted exact-field projection from explicitly whitelisted
league-official schedule pages and freezes target identities before labels.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from adaptive_latent_identity_lock_v1 import IdentityLockError, build_identity_lock


class OfficialScheduleError(ValueError):
    pass


SCHEMA = "football3_official_schedule_projection_v1"
MIN_LEAD = timedelta(hours=1)
MAX_LEAD = timedelta(days=14)
MIN_TOTAL_TARGETS = 20

SOURCE_KEYS = frozenset({
    "schema",
    "competition_id",
    "authority",
    "source_url",
    "source_observed_at",
    "source_timezone",
    "fixtures",
    "raw_source_payload_persisted",
    "real_labels_read",
})
FIXTURE_KEYS = frozenset({"round_ref", "scheduled_kickoff", "home_team_name", "away_team_name"})

COMPETITIONS: dict[str, dict[str, str]] = {
    "ENG_PremierLeague": {
        "authority": "Premier League",
        "host": "www.premierleague.com",
        "path": "/en/news/4675097/all-380-fixtures-for-202627-premier-league-season/",
        "timezone": "Europe/London",
    },
    "ESP_LaLiga": {
        "authority": "LALIGA",
        "host": "www.laliga.com",
        "path": "/noticias/horarios-de-la-tercera-jornada-de-laliga-ea-sports-2026-27",
        "timezone": "Europe/Madrid",
    },
    "GER_Bundesliga": {
        "authority": "Bundesliga",
        "host": "products.bundesliga.com",
        "path": "/fixtures",
        "timezone": "Europe/Berlin",
    },
    "ITA_SerieA": {
        "authority": "Lega Serie A",
        "host": "www.legaseriea.it",
        "path": "/serie-a/news/date-orari-e-programmazione-tv-delle-prime-cinque-giornate",
        "timezone": "Europe/Rome",
    },
    "FRA_Ligue1": {
        "authority": "Ligue 1 / LFP Media",
        "host": "ligue1.com",
        "path": "/fr/articles/l1_article_5435-programmation-tv-des-2-premieres-journees-de-ligue-1-mc-donald-s-2627",
        "timezone": "Europe/Paris",
    },
}


def _exact_keys(obj: Any, allowed: frozenset[str], path: str) -> dict[str, Any]:
    if type(obj) is not dict:
        raise OfficialScheduleError(f"{path} must be plain object")
    keys = set(obj)
    if keys != set(allowed):
        missing = sorted(set(allowed) - keys)
        unknown = sorted(keys - set(allowed))
        raise OfficialScheduleError(f"{path} exact-key contract failed missing={missing} unknown={unknown}")
    return obj


def _plain_string(value: Any, field: str) -> str:
    if type(value) is not str:
        raise OfficialScheduleError(f"{field} must be plain string")
    text = value.strip()
    if not text:
        raise OfficialScheduleError(f"{field} must be non-empty")
    return text


def _zero_int(value: Any, field: str) -> int:
    if type(value) is not int or value != 0:
        raise OfficialScheduleError(f"{field} must be integer zero")
    return value


def _canonical_utc_z(value: Any, field: str) -> datetime:
    text = _plain_string(value, field)
    if not text.endswith("Z"):
        raise OfficialScheduleError(f"{field} must be canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise OfficialScheduleError(f"{field} invalid timestamp") from exc
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != text:
        raise OfficialScheduleError(f"{field} must be canonical UTC Z timestamp")
    return parsed.astimezone(timezone.utc)


def _scheduled_kickoff(value: Any, expected_tz: str, field: str) -> datetime:
    text = _plain_string(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise OfficialScheduleError(f"{field} invalid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfficialScheduleError(f"{field} must include UTC offset")
    try:
        zone = ZoneInfo(expected_tz)
    except ZoneInfoNotFoundError as exc:
        raise OfficialScheduleError(f"unknown source timezone {expected_tz}") from exc
    local = parsed.astimezone(zone)
    # The explicit offset supplied by the projection must be the official zone's
    # actual offset at that instant; otherwise DST/offset provenance is ambiguous.
    if local.utcoffset() != parsed.utcoffset():
        raise OfficialScheduleError(f"{field} offset does not match source_timezone")
    canonical_local = local.isoformat()
    if canonical_local != text:
        raise OfficialScheduleError(f"{field} must be canonical ISO offset timestamp")
    return parsed.astimezone(timezone.utc)


def _canonical_name(value: Any, field: str) -> str:
    raw = _plain_string(value, field)
    normalized = unicodedata.normalize("NFKC", raw)
    if any(unicodedata.category(ch).startswith("C") for ch in normalized):
        raise OfficialScheduleError(f"{field} contains forbidden control/format characters")
    text = " ".join(normalized.split())
    if not text:
        raise OfficialScheduleError(f"{field} canonical name empty")
    if len(text) > 120:
        raise OfficialScheduleError(f"{field} too long")
    return text


def _source_url(value: Any, spec: dict[str, str]) -> str:
    text = _plain_string(value, "source_url")
    if text != value:
        raise OfficialScheduleError("source_url must not contain surrounding whitespace")
    parts = urlsplit(text)
    if (
        parts.scheme != "https"
        or parts.netloc != spec["host"]
        or parts.hostname != spec["host"]
        or parts.path != spec["path"]
        or parts.username is not None
        or parts.password is not None
        or bool(parts.query)
        or bool(parts.fragment)
    ):
        raise OfficialScheduleError("source_url is not the exact whitelisted official schedule URL")
    return text


def _stable_team_id(competition_id: str, team_name: str) -> str:
    material = json.dumps(
        {"competition_id": competition_id, "team_name": team_name},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "official-schedule-team:" + hashlib.sha256(material).hexdigest()


def _stable_fixture_id(competition_id: str, kickoff_at: str, home_id: str, away_id: str) -> str:
    material = json.dumps(
        {
            "competition_id": competition_id,
            "kickoff_at": kickoff_at,
            "home_team_id": home_id,
            "away_team_id": away_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "official-schedule-fixture:" + hashlib.sha256(material).hexdigest()




def _target_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["kickoff_at"], row["competition_id"], row["fixture_id"])


def canonical_source_projection(source: dict[str, Any]) -> dict[str, Any]:
    row = _exact_keys(source, SOURCE_KEYS, "source")
    schema = _plain_string(row["schema"], "schema")
    if schema != SCHEMA:
        raise OfficialScheduleError("source.schema mismatch")
    competition_id = _plain_string(row["competition_id"], "competition_id")
    if competition_id not in COMPETITIONS:
        raise OfficialScheduleError("competition_id not whitelisted")
    spec = COMPETITIONS[competition_id]
    authority = _plain_string(row["authority"], "authority")
    source_timezone = _plain_string(row["source_timezone"], "source_timezone")
    if authority != spec["authority"]:
        raise OfficialScheduleError("authority mismatch")
    if source_timezone != spec["timezone"]:
        raise OfficialScheduleError("source_timezone mismatch")
    source_url = _source_url(row["source_url"], spec)
    observed = _canonical_utc_z(row["source_observed_at"], "source_observed_at")
    if row["raw_source_payload_persisted"] is not False:
        raise OfficialScheduleError("raw_source_payload_persisted must be false")
    _zero_int(row["real_labels_read"], "real_labels_read")
    if type(row["fixtures"]) is not list or not row["fixtures"]:
        raise OfficialScheduleError("fixtures must be non-empty list")

    fixtures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for i, raw in enumerate(row["fixtures"]):
        f = _exact_keys(raw, FIXTURE_KEYS, f"fixtures[{i}]")
        round_ref = _plain_string(f["round_ref"], f"fixtures[{i}].round_ref")
        home = _canonical_name(f["home_team_name"], f"fixtures[{i}].home_team_name")
        away = _canonical_name(f["away_team_name"], f"fixtures[{i}].away_team_name")
        if home == away:
            raise OfficialScheduleError(f"fixtures[{i}] home and away collide")
        kickoff_utc = _scheduled_kickoff(f["scheduled_kickoff"], spec["timezone"], f"fixtures[{i}].scheduled_kickoff")
        lead = kickoff_utc - observed
        if lead < MIN_LEAD or lead > MAX_LEAD:
            raise OfficialScheduleError(f"fixtures[{i}] outside frozen lead window")
        key = (kickoff_utc.isoformat(), home, away)
        if key in seen:
            raise OfficialScheduleError(f"duplicate fixture tuple at fixtures[{i}]")
        seen.add(key)
        fixtures.append({
            "round_ref": round_ref,
            "scheduled_kickoff": f["scheduled_kickoff"],
            "home_team_name": home,
            "away_team_name": away,
        })

    return {
        "schema": SCHEMA,
        "competition_id": competition_id,
        "authority": spec["authority"],
        "source_url": source_url,
        "source_observed_at": row["source_observed_at"],
        "source_timezone": spec["timezone"],
        "fixtures": fixtures,
        "raw_source_payload_persisted": False,
        "real_labels_read": 0,
    }


def materialize_target_inventory(sources: list[dict[str, Any]]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if type(sources) is not list or not sources:
        raise OfficialScheduleError("sources must be non-empty list")
    if len(sources) != len(COMPETITIONS):
        raise OfficialScheduleError("exactly one source projection per required competition is required")

    canonical: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(sources):
        source = canonical_source_projection(raw)
        cid = source["competition_id"]
        if cid in canonical:
            raise OfficialScheduleError(f"duplicate competition source: {cid}")
        canonical[cid] = source
    if set(canonical) != set(COMPETITIONS):
        missing = sorted(set(COMPETITIONS) - set(canonical))
        extra = sorted(set(canonical) - set(COMPETITIONS))
        raise OfficialScheduleError(f"competition coverage mismatch missing={missing} extra={extra}")

    targets: list[dict[str, Any]] = []
    lock_rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    global_fixture_ids: set[str] = set()
    for cid in COMPETITIONS:
        source = canonical[cid]
        source_rows: list[dict[str, Any]] = []
        for fixture in source["fixtures"]:
            kickoff_utc = _scheduled_kickoff(fixture["scheduled_kickoff"], source["source_timezone"], "scheduled_kickoff")
            kickoff_at = kickoff_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            home_id = _stable_team_id(cid, fixture["home_team_name"])
            away_id = _stable_team_id(cid, fixture["away_team_name"])
            fixture_id = _stable_fixture_id(cid, kickoff_at, home_id, away_id)
            if fixture_id in global_fixture_ids:
                raise OfficialScheduleError("derived fixture_id collision")
            global_fixture_ids.add(fixture_id)
            prediction_cutoff_utc = kickoff_utc - timedelta(minutes=15)
            item = {
                "competition_id": cid,
                "fixture_id": fixture_id,
                "kickoff_at": kickoff_at,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "prediction_cutoff": prediction_cutoff_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
            lock_rows.append({
                "competition_id": cid,
                "fixture_id": fixture_id,
                "kickoff_at": kickoff_utc.replace(microsecond=0),
                "home_team_id": home_id,
                "away_team_id": away_id,
                "prediction_cutoff": prediction_cutoff_utc.replace(microsecond=0),
            })
            source_rows.append(item)
            targets.append(item)
        projection_material = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        receipts.append({
            "competition_id": cid,
            "authority": source["authority"],
            "source_url": source["source_url"],
            "source_observed_at": source["source_observed_at"],
            "source_projection_sha256": hashlib.sha256(projection_material).hexdigest(),
            "selected_target_count": len(source_rows),
            "raw_source_payload_persisted": False,
            "real_labels_read": 0,
        })

    if len(targets) < MIN_TOTAL_TARGETS:
        raise OfficialScheduleError(f"coverage below minimum: {len(targets)} < {MIN_TOTAL_TARGETS}")
    try:
        lock = build_identity_lock(lock_rows)
    except IdentityLockError as exc:
        raise OfficialScheduleError(f"identity-lock contract failed: {exc}") from exc

    targets.sort(key=_target_sort_key)
    generated = max(_canonical_utc_z(r["source_observed_at"], "source_observed_at") for r in receipts)
    inventory = {
        "schema": "football3_adaptive_latent_stage4_zero_label_target_inventory_v3",
        "status": "PASS_ZERO_LABEL_OFFICIAL_SCHEDULE_IDENTITY_LOCK_PROVENANCE_CANDIDATE",
        "generated_at_utc": generated.isoformat().replace("+00:00", "Z"),
        "target_row_count": len(targets),
        "required_competitions": list(COMPETITIONS),
        "provider_event_id_present": False,
        "provider_mapping_status": "UNRESOLVED_SEPARATE_GATE",
        "source_revision_status": "EXTRACTED_EXACT_FIELD_PROJECTION_ONLY",
        "availability_proof_status": "PENDING_FORMAL_PIT_ADJUDICATION",
        "provider_receipts": receipts,
        "targets": targets,
        "identity_lock_sha256": lock["identity_lock_sha256"],
        "ordered_identity_sha256": lock["ordered_identity_sha256"],
        "label_fields_persisted": 0,
        "real_target_values_read": 0,
        "market_values_persisted": 0,
        "raw_source_payload_persisted": False,
        "formal_pit_eligible": False,
        "formal_weight": 0.0,
        "research_only": True,
    }
    return inventory, lock["identity_csv"], lock
