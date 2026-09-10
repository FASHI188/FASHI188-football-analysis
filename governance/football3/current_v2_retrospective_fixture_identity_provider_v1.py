#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-score-blind-fixture-identity-provider-chain-v2"

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
    def __init__(self, code: str, evidence: dict[str, Any] | None = None):
        self.code = code
        self.evidence = dict(evidence or {})
        super().__init__(code)


@dataclass(frozen=True)
class Provider:
    name: str
    priority: int
    loader: Callable[[], Iterable[dict[str, Any]]]


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _norm_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _utc_identity(value: str, field: str) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FixtureIdentityProviderError(f"FIXTURE_IDENTITY_{field.upper()}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FixtureIdentityProviderError(f"FIXTURE_IDENTITY_{field.upper()}_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_fixture_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row["competition"],
        row["home_identity"],
        row["away_identity"],
        row["kickoff"],
    )


def _slot_key(row: dict[str, str]) -> tuple[str, str]:
    return (row["competition"], row["kickoff"])


def _conflict(code: str, **evidence: Any) -> FixtureIdentityProviderError:
    return FixtureIdentityProviderError(code, evidence)


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
    content_sha = out["content_sha"].lower()
    if len(content_sha) != 64 or any(c not in "0123456789abcdef" for c in content_sha):
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_CONTENT_SHA_INVALID")
    out["content_sha"] = content_sha
    out["kickoff"] = _utc_identity(out["kickoff"], "KICKOFF")
    out["observed_at"] = _utc_identity(out["observed_at"], "OBSERVED_AT")
    if out["home_identity"] == out["away_identity"]:
        raise FixtureIdentityProviderError("FIXTURE_IDENTITY_SIDES_COLLIDE")
    return out


def stable_records(records: Iterable[dict[str, Any]], expected_competition: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    exact_seen: set[tuple[str, ...]] = set()
    source_fixture_claims: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for raw in records:
        row = validate_record(raw, expected_competition)
        fixture_key = _canonical_fixture_key(row)
        source_fixture = (row["source_identity"], row["fixture_identity"])
        previous_key = source_fixture_claims.get(source_fixture)
        if previous_key is not None and previous_key != fixture_key:
            raise _conflict(
                "FIXTURE_IDENTITY_SOURCE_ID_REUSED_FOR_DISTINCT_CANONICAL_FIXTURES",
                source_identity=row["source_identity"],
                fixture_identity=row["fixture_identity"],
                first_canonical_fixture_key=list(previous_key),
                second_canonical_fixture_key=list(fixture_key),
            )
        source_fixture_claims[source_fixture] = fixture_key
        exact_key = tuple(row[key] for key in PROVIDER_FIELDS)
        if exact_key in exact_seen:
            continue
        exact_seen.add(exact_key)
        rows.append(row)
    return sorted(
        rows,
        key=lambda x: (
            x["competition"], x["kickoff"], x["home_identity"], x["away_identity"],
            x["source_identity"], x["fixture_identity"], x["observed_at"], x["content_sha"],
        ),
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


def _slot_map(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], dict[tuple[str, str, str, str], list[dict[str, str]]]]:
    """Bucket by competition/kickoff; a slot is a container, never a unique fixture identity."""
    out: dict[tuple[str, str], dict[tuple[str, str, str, str], list[dict[str, str]]]] = {}
    for row in rows:
        slot = _slot_key(row)
        fixture_key = _canonical_fixture_key(row)
        fixture_rows = out.setdefault(slot, {}).setdefault(fixture_key, [])

        # Only after the strict canonical fixture key proves these observations are the
        # same fixture may source-specific identity disagreement fail closed. Different
        # providers may legitimately use different fixture IDs, so this is scoped to
        # the same underlying source_identity.
        same_source_ids = {
            existing["fixture_identity"]
            for existing in fixture_rows
            if existing["source_identity"] == row["source_identity"]
        }
        if same_source_ids and row["fixture_identity"] not in same_source_ids:
            raise _conflict(
                "FIXTURE_IDENTITY_SOURCE_ID_CONFLICT_AFTER_CANONICAL_BIND",
                canonical_fixture_key=list(fixture_key),
                source_identity=row["source_identity"],
                fixture_identities=sorted(same_source_ids | {row["fixture_identity"]}),
                evidence=[
                    {
                        "source_identity": x["source_identity"],
                        "fixture_identity": x["fixture_identity"],
                        "observed_at": x["observed_at"],
                        "content_sha": x["content_sha"],
                    }
                    for x in fixture_rows + [row]
                    if x["source_identity"] == row["source_identity"]
                ],
            )
        fixture_rows.append(row)
    return out


def assert_provider_compatible(
    left: list[dict[str, str]],
    right: list[dict[str, str]],
    left_provider: str | None = None,
    right_provider: str | None = None,
) -> None:
    lmap = _slot_map(left)
    rmap = _slot_map(right)
    for slot in sorted(set(lmap) & set(rmap)):
        # Different canonical home/away pairs in the same competition/kickoff slot are
        # separate fixtures and legally coexist. Cross-source binding happens only on
        # the complete strict canonical fixture key; source-specific fixture IDs are
        # evidence only and never establish cross-source identity.
        for fixture_key in sorted(set(lmap[slot]) & set(rmap[slot])):
            combined = lmap[slot][fixture_key] + rmap[slot][fixture_key]
            by_source: dict[str, set[str]] = {}
            for row in combined:
                if _canonical_fixture_key(row) != fixture_key:
                    raise _conflict(
                        "FIXTURE_IDENTITY_CANONICAL_BIND_INTERNAL_CONFLICT",
                        canonical_fixture_key=list(fixture_key),
                        observed_canonical_fixture_key=list(_canonical_fixture_key(row)),
                        left_provider=left_provider,
                        right_provider=right_provider,
                    )
                by_source.setdefault(row["source_identity"], set()).add(row["fixture_identity"])
            for source_identity, fixture_ids in sorted(by_source.items()):
                if len(fixture_ids) > 1:
                    raise _conflict(
                        "FIXTURE_IDENTITY_SOURCE_ID_CONFLICT_AFTER_CANONICAL_BIND",
                        canonical_fixture_key=list(fixture_key),
                        source_identity=source_identity,
                        fixture_identities=sorted(fixture_ids),
                        left_provider=left_provider,
                        right_provider=right_provider,
                        evidence=[
                            {
                                "source_identity": x["source_identity"],
                                "fixture_identity": x["fixture_identity"],
                                "observed_at": x["observed_at"],
                                "content_sha": x["content_sha"],
                            }
                            for x in combined
                            if x["source_identity"] == source_identity
                        ],
                    )


def _record_conflict(audit: dict[str, Any], exc: FixtureIdentityProviderError, **context: Any) -> None:
    if not exc.evidence:
        return
    item = {"error": exc.code, **context, "evidence": exc.evidence}
    audit.setdefault("fixture_identity_conflicts", []).append(item)


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
            # Build the multi-fixture slot container here too so same-source identity
            # conflicts are rejected before provider selection.
            _slot_map(rows)
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
            item = {
                "competition": competition,
                "provider": provider.name,
                "priority": provider.priority,
                "outcome": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "http_status": http_status(exc),
            }
            if isinstance(exc, FixtureIdentityProviderError) and exc.evidence:
                item["conflict_evidence"] = exc.evidence
                _record_conflict(audit, exc, competition=competition, provider=provider.name)
            domain_attempts.append(item)

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
        for i, (lp, lrows) in enumerate(successes):
            for rp, rrows in successes[i + 1:]:
                assert_provider_compatible(lrows, rrows, lp.name, rp.name)
    except FixtureIdentityProviderError as exc:
        _record_conflict(audit, exc, competition=competition)
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
