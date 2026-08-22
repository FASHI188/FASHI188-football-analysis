#!/usr/bin/env python3
"""Research-only quarantine for prospective xG projections.

This module has no network, Provider, file, label-dataset, training, scoring, or
CURRENT access. It accepts only an already-sanitized exact-field xG projection
bound to a pre-frozen target identity. Completion is intentionally unknown and
cannot be inferred from elapsed time here.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from adaptive_latent_identity_lock_v1 import IdentityLockError, build_identity_lock


class QuarantineError(ValueError):
    pass


COLLECTION_NOT_BEFORE = timedelta(hours=3)
PROJECTION_SCHEMA = "football3_xg_statistics_projection_v1"
PROJECTION_SOURCE_KIND = "unverified_match_xg_projection"
SNAPSHOT_SOURCE_KIND = "unverified_match_xg_snapshot"
UNKNOWN_COMPLETION = "UNKNOWN_NOT_ACCESSED"
UNKNOWN_FINAL_XG = "UNKNOWN_NOT_ADJUDICATED"

FROZEN_TARGET_KEYS = frozenset({
    "competition_id",
    "fixture_id",
    "provider_event_id",
    "kickoff_at",
    "home_team_id",
    "away_team_id",
    "prediction_cutoff",
})

XG_PROJECTION_KEYS = frozenset({
    "schema",
    "source_kind",
    "source_identity",
    "source_url",
    "payload_sha256",
    "collector_run_id",
    "provider_event_id",
    "target_identity_sha256",
    "home_xg",
    "away_xg",
    "collector_first_observed_at",
    "retrieved_at",
    "ingested_at",
    "raw_provider_payload_persisted",
    "real_labels_read",
    "completion_status",
    "final_xg_status",
    "formal_pit_eligible",
    "eligible_for_latent_update",
    "formal_weight",
    "research_only",
})

PAIR_KEYS = frozenset({"target", "projection"})


def _exact_keys(obj: Any, allowed: frozenset[str], path: str) -> dict[str, Any]:
    if type(obj) is not dict:
        raise QuarantineError(f"{path} must be plain object")
    keys = set(obj)
    if keys != set(allowed):
        missing = sorted(set(allowed) - keys)
        unknown = sorted(keys - set(allowed))
        raise QuarantineError(f"{path} exact-key contract failed missing={missing} unknown={unknown}")
    return obj


def _nonempty(value: Any, field: str) -> str:
    if type(value) is not str:
        raise QuarantineError(f"{field} must be plain string")
    text = value.strip()
    if not text:
        raise QuarantineError(f"{field} must be non-empty")
    return text


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise QuarantineError(f"{field} must be positive integer")
    return value


def _zero_int(value: Any, field: str) -> int:
    if type(value) is not int or value != 0:
        raise QuarantineError(f"{field} must be integer zero")
    return value


def _zero_weight(value: Any) -> float:
    if type(value) not in (int, float):
        raise QuarantineError("formal_weight must be numeric zero")
    number = float(value)
    if not math.isfinite(number) or number != 0.0:
        raise QuarantineError("formal_weight must be numeric zero")
    return number


def _nonnegative_finite(value: Any, field: str) -> float:
    if type(value) not in (int, float):
        raise QuarantineError(f"{field} must be finite numeric >= 0")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise QuarantineError(f"{field} must be finite numeric >= 0")
    return number


def _sha256_hex(value: Any, field: str) -> str:
    text = _nonempty(value, field)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise QuarantineError(f"{field} must be 64 lowercase hex")
    return text


def _parse_canonical_utc_z(value: Any, field: str) -> datetime:
    text = _nonempty(value, field)
    if not text.endswith("Z"):
        raise QuarantineError(f"{field} must be canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise QuarantineError(f"{field} invalid timestamp") from exc
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != text:
        raise QuarantineError(f"{field} must be canonical UTC Z timestamp")
    return parsed.astimezone(timezone.utc)


def _https_source_url(value: Any) -> str:
    text = _nonempty(value, "source_url")
    parts = urlsplit(text)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or bool(parts.query)
        or bool(parts.fragment)
        or not parts.path.startswith("/")
    ):
        raise QuarantineError("source_url must be credential-free HTTPS URL without query/fragment")
    return text


def _identity_lock_hash(row: dict[str, Any]) -> str:
    try:
        lock = build_identity_lock([{
            "competition_id": row["competition_id"],
            "fixture_id": row["fixture_id"],
            "kickoff_at": _parse_canonical_utc_z(row["kickoff_at"], "kickoff_at"),
            "home_team_id": row["home_team_id"],
            "away_team_id": row["away_team_id"],
            "prediction_cutoff": _parse_canonical_utc_z(row["prediction_cutoff"], "prediction_cutoff"),
        }])
    except IdentityLockError as exc:
        raise QuarantineError(f"target identity-lock contract failed: {exc}") from exc
    lines = lock["identity_csv"].splitlines()
    if len(lines) != 2 or lines[0] != "identity_sha256":
        raise QuarantineError("identity-lock output shape unexpected")
    return _sha256_hex(lines[1], "target_identity_sha256")


def canonical_target(target: dict[str, Any]) -> dict[str, Any]:
    row = _exact_keys(target, FROZEN_TARGET_KEYS, "target")
    event_id = _positive_int(row["provider_event_id"], "provider_event_id")
    fixture_id = _nonempty(row["fixture_id"], "fixture_id")
    expected_fixture_id = f"fixture-projection:{event_id}"
    if fixture_id != expected_fixture_id:
        raise QuarantineError("fixture_id/provider_event_id mismatch")
    competition_id = _nonempty(row["competition_id"], "competition_id")
    kickoff = _parse_canonical_utc_z(row["kickoff_at"], "kickoff_at")
    cutoff = _parse_canonical_utc_z(row["prediction_cutoff"], "prediction_cutoff")
    if kickoff - cutoff != timedelta(minutes=15):
        raise QuarantineError("prediction_cutoff must equal kickoff_at - 15 minutes exactly")
    home_team_id = _nonempty(row["home_team_id"], "home_team_id")
    away_team_id = _nonempty(row["away_team_id"], "away_team_id")
    if home_team_id == away_team_id:
        raise QuarantineError("home_team_id and away_team_id must differ")
    return {
        "competition_id": competition_id,
        "fixture_id": fixture_id,
        "provider_event_id": event_id,
        "kickoff_at": row["kickoff_at"],
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "prediction_cutoff": row["prediction_cutoff"],
    }


def canonical_xg_projection(projection: dict[str, Any]) -> dict[str, Any]:
    row = _exact_keys(projection, XG_PROJECTION_KEYS, "projection")
    if row["schema"] != PROJECTION_SCHEMA:
        raise QuarantineError("projection.schema mismatch")
    if row["source_kind"] != PROJECTION_SOURCE_KIND:
        raise QuarantineError("projection.source_kind mismatch")
    if row["completion_status"] != UNKNOWN_COMPLETION:
        raise QuarantineError("completion_status must remain UNKNOWN_NOT_ACCESSED")
    if row["final_xg_status"] != UNKNOWN_FINAL_XG:
        raise QuarantineError("final_xg_status must remain UNKNOWN_NOT_ADJUDICATED")
    if row["raw_provider_payload_persisted"] is not False:
        raise QuarantineError("raw_provider_payload_persisted must be false")
    _zero_int(row["real_labels_read"], "real_labels_read")
    if row["formal_pit_eligible"] is not False:
        raise QuarantineError("formal_pit_eligible must be false")
    if row["eligible_for_latent_update"] is not False:
        raise QuarantineError("eligible_for_latent_update must be false")
    _zero_weight(row["formal_weight"])
    if row["research_only"] is not True:
        raise QuarantineError("research_only must be true")

    event_id = _positive_int(row["provider_event_id"], "provider_event_id")
    first_observed = _parse_canonical_utc_z(row["collector_first_observed_at"], "collector_first_observed_at")
    retrieved = _parse_canonical_utc_z(row["retrieved_at"], "retrieved_at")
    ingested = _parse_canonical_utc_z(row["ingested_at"], "ingested_at")
    if first_observed > retrieved:
        raise QuarantineError("collector_first_observed_at must be <= retrieved_at")
    if retrieved > ingested:
        raise QuarantineError("retrieved_at must be <= ingested_at")

    return {
        "schema": PROJECTION_SCHEMA,
        "source_kind": PROJECTION_SOURCE_KIND,
        "source_identity": _nonempty(row["source_identity"], "source_identity"),
        "source_url": _https_source_url(row["source_url"]),
        "payload_sha256": _sha256_hex(row["payload_sha256"], "payload_sha256"),
        "collector_run_id": _nonempty(row["collector_run_id"], "collector_run_id"),
        "provider_event_id": event_id,
        "target_identity_sha256": _sha256_hex(row["target_identity_sha256"], "target_identity_sha256"),
        "home_xg": _nonnegative_finite(row["home_xg"], "home_xg"),
        "away_xg": _nonnegative_finite(row["away_xg"], "away_xg"),
        "collector_first_observed_at": row["collector_first_observed_at"],
        "retrieved_at": row["retrieved_at"],
        "ingested_at": row["ingested_at"],
        "raw_provider_payload_persisted": False,
        "real_labels_read": 0,
        "completion_status": UNKNOWN_COMPLETION,
        "final_xg_status": UNKNOWN_FINAL_XG,
        "formal_pit_eligible": False,
        "eligible_for_latent_update": False,
        "formal_weight": 0.0,
        "research_only": True,
    }


def materialize_quarantine_snapshot(target: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    frozen = canonical_target(target)
    xg = canonical_xg_projection(projection)
    target_hash = _identity_lock_hash(frozen)
    if xg["provider_event_id"] != frozen["provider_event_id"]:
        raise QuarantineError("projection provider_event_id does not match frozen target")
    if xg["target_identity_sha256"] != target_hash:
        raise QuarantineError("projection target_identity_sha256 does not match frozen target")

    kickoff = _parse_canonical_utc_z(frozen["kickoff_at"], "kickoff_at")
    first_observed = _parse_canonical_utc_z(xg["collector_first_observed_at"], "collector_first_observed_at")
    if first_observed < kickoff + COLLECTION_NOT_BEFORE:
        raise QuarantineError("xG projection observed before quarantine collection floor")

    snapshot_material = {
        "target_identity_sha256": target_hash,
        "payload_sha256": xg["payload_sha256"],
        "collector_first_observed_at": xg["collector_first_observed_at"],
        "home_xg": xg["home_xg"],
        "away_xg": xg["away_xg"],
    }
    snapshot_sha256 = hashlib.sha256(
        json.dumps(snapshot_material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return {
        "schema": "football3_adaptive_latent_xg_quarantine_snapshot_v2",
        "source_kind": SNAPSHOT_SOURCE_KIND,
        **frozen,
        "target_identity_sha256": target_hash,
        "source_identity": xg["source_identity"],
        "source_url": xg["source_url"],
        "payload_sha256": xg["payload_sha256"],
        "collector_run_id": xg["collector_run_id"],
        "collector_first_observed_at": xg["collector_first_observed_at"],
        "retrieved_at": xg["retrieved_at"],
        "ingested_at": xg["ingested_at"],
        "home_xg": xg["home_xg"],
        "away_xg": xg["away_xg"],
        "xg_total": xg["home_xg"] + xg["away_xg"],
        "xg_margin": xg["home_xg"] - xg["away_xg"],
        "completion_status": UNKNOWN_COMPLETION,
        "final_xg_status": UNKNOWN_FINAL_XG,
        "quarantine_snapshot_sha256": snapshot_sha256,
        "raw_provider_payload_persisted": False,
        "real_labels_read": 0,
        "formal_pit_eligible": False,
        "eligible_for_latent_update": False,
        "formal_weight": 0.0,
        "research_only": True,
        "time_based_completion_inference_used": False,
        "latent_adapter_compatible": False,
        "promotion_gate": "REQUIRES_SEPARATE_AUTHORIZED_COMPLETION_AND_FINAL_XG_ADJUDICATION",
    }


def materialize_quarantine_batch(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if type(pairs) is not list or not pairs:
        raise QuarantineError("pairs must be non-empty list")
    rows: list[dict[str, Any]] = []
    seen_fixture: set[tuple[str, str]] = set()
    seen_provider_event: set[int] = set()
    for index, raw_pair in enumerate(pairs):
        pair = _exact_keys(raw_pair, PAIR_KEYS, f"pairs[{index}]")
        frozen = canonical_target(pair["target"])
        fixture_key = (frozen["competition_id"], frozen["fixture_id"])
        if fixture_key in seen_fixture:
            raise QuarantineError(f"duplicate frozen fixture identity: {fixture_key}")
        if frozen["provider_event_id"] in seen_provider_event:
            raise QuarantineError(f"duplicate provider_event_id: {frozen['provider_event_id']}")
        seen_fixture.add(fixture_key)
        seen_provider_event.add(frozen["provider_event_id"])
        rows.append(materialize_quarantine_snapshot(frozen, pair["projection"]))

    rows.sort(key=lambda r: (r["kickoff_at"], r["competition_id"], r["fixture_id"]))
    ordered = "\n".join(r["quarantine_snapshot_sha256"] for r in rows).encode("utf-8")
    return {
        "schema": "football3_adaptive_latent_xg_quarantine_batch_v2",
        "status": "PASS_UNVERIFIED_XG_QUARANTINE_EXACT_WHITELIST",
        "row_count": len(rows),
        "rows": rows,
        "ordered_snapshot_sha256": hashlib.sha256(ordered).hexdigest(),
        "completed_match_xg_row_count": 0,
        "completion_witness_count": 0,
        "real_labels_read": 0,
        "formal_pit_eligible_row_count": 0,
        "eligible_for_latent_update_row_count": 0,
        "formal_weight": 0.0,
        "research_only": True,
    }
