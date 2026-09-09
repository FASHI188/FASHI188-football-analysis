#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-score-blind-fixture-identity-provider-chain-v1"

PROVIDER_FIELDS = (
    "competition",
    "fixture_identity",
    "kickoff",
    "home_identity",
    "away_identity",
    "source_identity",
    "observed_at",
    "content_sha",
)
FORBIDDEN_IDENTITY_FIELDS = {
    "score", "scores", "home_score", "away_score", "homescore", "awayscore",
    "result", "winner", "points", "outcome", "status", "state", "full_time_score",
    "fulltimescore", "homepoints", "awaypoints",
}
HTTP_RE = re.compile(r"HTTP(?: Error)?\s+(\d{3})")


class FixtureIdentityProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    priority: int
    loader: Callable[[], Iterable[dict[str, Any]]]


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _norm_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def http_status(error: BaseException | str | None) -> int | None:
    if error is None:
        return None
    match = HTTP_RE.search(str(error))
    return int(match.group(1)) if match else None


def validate_record(record: dict[str, Any], expected_competition: str) -> dict[str, str]:
    if not isinstance(record, dict):
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_PROVIDER_RECORD_INVALID")
    extras = set(record) - set(PROVIDER_FIELDS)
    missing = set(PROVIDER_FIELDS) - set(record)
    if extras:
        raise FixtureIdentityProviderError(f"FIXTURE_IDENTITY_PROVIDER_FIELD_FORBIDDEN:{sorted(extras)}")
    if missing:
        raise FixtureIdentityProviderError(f"FIXTURE_IDENTITY_PROVIDER_FIELD_MISSING:{sorted(missing)}")
    for key in record:
        if _norm_key(key) in FORBIDDEN_IDENTITY_FIELDS:
            raise FixtureIdentityProviderError(f"FIXTURE_IDENTITY_RESULT_FIELD_FORBIDDEN:{key}")
    out = {key: str(record[key] or "").strip() for key in PROVIDER_FIELDS}
    if out["competition"] != expected_competition:
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_COMPETITION_MISMATCH")
    if any(not out[key] for key in PROVIDER_FIELDS):
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_PROVIDER_VALUE_EMPTY")
    if len(out["content_sha"]) != 64 or any(c not in "0123456789abcdef" for c in out["content_sha"].lower()):
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_CONTENT_SHA_INVALID")
    if out["home_identity"] == out["away_identity"]:
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_SIDES_COLLIDE")
    return out


def stable_records(records: Iterable[dict[str, Any]], expected_competition: str) -> list[dict[str, str]]:
    by_fixture: dict[str, dict[str, str]] = {}
    for raw in records:
        row = validate_record(raw, expected_competition)
        fid = row["fixture_identity"]
        old = by_fixture.get(fid)
        if old is not None and old != row:
            raise FixtureIdentityProviderError("FIXTURE_IDENTITY_DUPLICATE_CONFLICT")
        by_fixture[fid] = row
    return sorted(
        by_fixture.values(),
        key=lambda x: (x["competition"], x["kickoff"], x["home_identity"], x["away_identity"], x["fixture_identity"], x["source_identity"]),
    )


def inventory_sha(records: Iterable[dict[str, Any]], expected_competition: str) -> str:
    rows = stable_records(records, expected_competition)
    projection = [
        {
            "competition": r["competition"],
            "fixture_identity": r["fixture_identity"],
            "kickoff": r["kickoff"],
            "home_identity": r["home_identity"],
            "away_identity": r["away_identity"],
            "source_identity": r["source_identity"],
            "content_sha": r["content_sha"],
        }
        for r in rows
    ]
    return hashlib.sha256(_canon(projection)).hexdigest()


def _slot_map(rows: list[dict[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        slot = (row["competition"], row["kickoff"])
        sides = (row["home_identity"], row["away_identity"])
        previous = out.get(slot)
        if previous is not None and previous != sides:
            raise FixtureIdentityProviderError("FIXTURE_IDENTITY_INTERNAL_SLOT_CONFLICT")
        out[slot] = sides
    return out


def assert_provider_compatible(left: list[dict[str, str]], right: list[dict[str, str]]) -> None:
    lmap = _slot_map(left)
    rmap = _slot_map(right)
    for slot in sorted(set(lmap) & set(rmap)):
        if lmap[slot] != rmap[slot]:
            raise FixtureIdentityProviderError(
                f"FIXTURE_IDENTITY_PROVIDER_CONFLICT:{slot[0]}:{slot[1]}:{lmap[slot]}!={rmap[slot]}"
            )


def resolve(
    competition: str,
    providers: Iterable[Provider],
    audit: dict[str, Any],
    *,
    error_factory: Callable[[str], BaseException] = FixtureIdentityProviderError,
) -> list[dict[str, str]]:
    ordered = sorted(providers, key=lambda p: (p.priority, p.name))
    successes: list[tuple[Provider, list[dict[str, str]]]] = []
    domain_attempts: list[dict[str, Any]] = []
    for provider in ordered:
        try:
            rows = stable_records(provider.loader(), competition)
            status = "SUCCESS" if rows else "INSUFFICIENT"
            item = {
                "competition": competition,
                "provider": provider.name,
                "priority": provider.priority,
                "outcome": status,
                "resolved_fixture_count": len(rows),
                "inventory_sha": inventory_sha(rows, competition) if rows else None,
                "http_status": None,
            }
            domain_attempts.append(item)
            if rows:
                successes.append((provider, rows))
        except Exception as exc:
            domain_attempts.append({
                "competition": competition,
                "provider": provider.name,
                "priority": provider.priority,
                "outcome": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "http_status": http_status(exc),
            })

    audit.setdefault("fixture_identity_provider_attempts", []).extend(domain_attempts)
    audit.setdefault("fixture_identity_provider_inventory", {})[competition] = domain_attempts
    if not successes:
        audit["failed_domain"] = competition
        audit["first_authoritative_failure"] = audit.get("first_authoritative_failure") or {
            "stage": "SCORE_BLIND_FIXTURE_IDENTITY_PROVIDER_CHAIN",
            "competition": competition,
            "error": "ALL_LEGAL_PROVIDERS_UNAVAILABLE",
        }
        raise error_factory(f"FIXTURE_IDENTITY_ALL_PROVIDERS_UNAVAILABLE:{competition}")

    try:
        for i, (_lp, lrows) in enumerate(successes):
            for _rp, rrows in successes[i + 1:]:
                assert_provider_compatible(lrows, rrows)
    except FixtureIdentityProviderError as exc:
        audit["failed_domain"] = competition
        audit["first_authoritative_failure"] = audit.get("first_authoritative_failure") or {
            "stage": "SCORE_BLIND_FIXTURE_IDENTITY_PROVIDER_CHAIN",
            "competition": competition,
            "error": str(exc),
        }
        raise error_factory(str(exc))

    selected_provider, selected_rows = successes[0]
    inv = inventory_sha(selected_rows, competition)
    audit.setdefault("fixture_identity_selected_provider", {})[competition] = selected_provider.name
    audit.setdefault("fixture_identity_inventory_sha", {})[competition] = inv
    audit.setdefault("fixture_identity_provider_order", {})[competition] = [p.name for p in ordered]
    audit["fallback_attempted"] = bool(audit.get("fallback_attempted")) or bool(selected_provider.priority > ordered[0].priority)
    return selected_rows
