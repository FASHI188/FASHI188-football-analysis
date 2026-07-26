#!/usr/bin/env python3
"""V6.31.0 match-bound PIT availability / predicted-XI ledger.

Purpose
-------
Bind team availability and predicted/expected XI evidence to the exact prospective market ``match_id``
and question-time freeze already stored by V6.5.1. This closes the semantic gap identified by V6.30:
a current roster or manager record is useful context, but it is not evidence of who is available or
expected to start for a specific match.

Governance
----------
- Evidence must be observed at or before the frozen market event timestamp and before kickoff.
- Post-freeze injury news, official lineups, results, and other future information are rejected.
- Empty availability is verified only with ``explicit_no_absences=true`` from a dedicated source role.
- A predicted XI requires at least 11 unique named players from a dedicated predicted-lineup source.
- This ledger never changes formal probabilities, CURRENT, thresholds, or research weights.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v6_match_context_pit_v6310.json"
FORWARD = ROOT / "forward" / "v6_market_first_events_v651.json"
EVIDENCE_ROOT = ROOT / "evidence" / "match_context_pre_kickoff"
OUT = ROOT / "manifests" / "v6_match_context_pit_v6310_status.json"


class ContractError(RuntimeError):
    pass


def load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ContractError(f"missing {field}")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"invalid {field}: {text}") from exc
    if dt.tzinfo is None:
        raise ContractError(f"naive {field}: {text}")
    return dt.astimezone(timezone.utc)


def normalize_role(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def player_key(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("player_id", "player_name", "name"):
            token = str(value.get(key) or "").strip()
            if token:
                return token.casefold()
        return ""
    return str(value or "").strip().casefold()


def unique_player_count(values: Any) -> int:
    if not isinstance(values, list):
        return 0
    return len({player_key(v) for v in values if player_key(v)})


def frozen_matches(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    events = payload.get("events")
    if not isinstance(events, list):
        raise ContractError("forward ledger missing events list")

    out: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "MARKET_PREDICTION_FROZEN":
            continue
        match_id = str(event.get("match_id") or "").strip()
        p = event.get("payload") or {}
        fixture = p.get("fixture_identity") or {}
        if not match_id:
            raise ContractError("frozen market event missing match_id")
        freeze_at = parse_ts(event.get("event_timestamp_utc"), "event_timestamp_utc")
        kickoff_at = parse_ts(fixture.get("kickoff_at"), "fixture_identity.kickoff_at")
        if freeze_at >= kickoff_at:
            raise ContractError(f"{match_id}: market freeze is not pre-kickoff")
        row = {
            "match_id": match_id,
            "competition_id": str(fixture.get("competition_id") or "").strip(),
            "season": str(fixture.get("season") or "").strip(),
            "home_team": str(fixture.get("home_team") or "").strip(),
            "away_team": str(fixture.get("away_team") or "").strip(),
            "freeze_at_utc": freeze_at,
            "kickoff_at_utc": kickoff_at,
            "market_source_observed_at_utc": parse_ts(
                (p.get("market_source") or {}).get("source_observed_at_utc"),
                "market_source.source_observed_at_utc",
            ),
        }
        if not all((row["competition_id"], row["home_team"], row["away_team"])):
            raise ContractError(f"{match_id}: incomplete fixture identity")
        previous = out.get(match_id)
        if previous is None or row["freeze_at_utc"] < previous["freeze_at_utc"]:
            out[match_id] = row
    return out


def evidence_documents() -> tuple[list[tuple[Path, dict[str, Any]]], list[dict[str, Any]]]:
    docs: list[tuple[Path, dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    if not EVIDENCE_ROOT.exists():
        return docs, errors
    for path in sorted(EVIDENCE_ROOT.glob("*.json")):
        try:
            payload = load(path)
            if not isinstance(payload, dict):
                raise ContractError("document is not a JSON object")
            docs.append((path, payload))
        except Exception as exc:
            errors.append({"file": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"})
    return docs, errors


def role_matches(role: str, allowed: list[str]) -> bool:
    return any(token in role for token in allowed)


def adjudicate_source(
    source: dict[str, Any],
    match: dict[str, Any],
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    violations: list[str] = []
    team = str(source.get("team_name") or "").strip()
    role = normalize_role(source.get("source_role"))
    source_name = str(source.get("source_name") or "").strip()
    source_url = str(source.get("source_url") or "").strip()
    source_tier = str(source.get("source_tier") or "").strip()

    if team not in {match["home_team"], match["away_team"]}:
        violations.append("team_identity_mismatch")

    try:
        observed = parse_ts(source.get("source_observed_at_utc"), "source_observed_at_utc")
    except ContractError:
        observed = None
        violations.append("missing_or_invalid_source_timestamp")

    if observed is not None and observed > match["freeze_at_utc"]:
        violations.append("post_freeze_source")
    if observed is not None and observed >= match["kickoff_at_utc"]:
        violations.append("post_or_at_kickoff_source")
    if not source_name:
        violations.append("missing_source_name")
    if not source_url:
        violations.append("missing_source_url")
    if not source_tier:
        violations.append("missing_source_tier")
    if not role:
        violations.append("missing_source_role")

    availability_roles = [normalize_role(x) for x in cfg.get("availability_source_role_tokens", [])]
    xi_roles = [normalize_role(x) for x in cfg.get("predicted_xi_source_role_tokens", [])]

    availability_records = source.get("availability")
    if not isinstance(availability_records, list):
        availability_records = []
    explicit_no_absences = source.get("explicit_no_absences") is True
    availability_role = role_matches(role, availability_roles)
    availability_verified = (
        not violations
        and availability_role
        and (bool(availability_records) or explicit_no_absences)
    )

    predicted_xi = source.get("predicted_xi")
    xi_count = unique_player_count(predicted_xi)
    xi_role = role_matches(role, xi_roles)
    predicted_xi_verified = not violations and xi_role and xi_count >= int(cfg.get("minimum_predicted_xi_players", 11))

    return {
        "team_name": team,
        "source_role": role,
        "source_name": source_name,
        "source_url": source_url,
        "source_tier": source_tier,
        "source_observed_at_utc": observed.isoformat() if observed else None,
        "availability_record_count": len(availability_records),
        "explicit_no_absences": explicit_no_absences,
        "availability_verified": availability_verified,
        "predicted_xi_unique_player_count": xi_count,
        "predicted_xi_verified": predicted_xi_verified,
        "accepted": not violations,
    }, violations


def main() -> int:
    cfg = load(CONFIG)
    if not isinstance(cfg, dict):
        raise ContractError("missing V6.31 config")
    forward = load(FORWARD)
    if not isinstance(forward, dict):
        raise ContractError("missing V6.5.1 forward ledger")

    matches = frozen_matches(forward)
    docs, malformed_docs = evidence_documents()
    by_match_docs: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    orphan_docs: list[dict[str, Any]] = []
    for path, doc in docs:
        match_id = str(doc.get("match_id") or "").strip()
        if match_id in matches:
            by_match_docs[match_id].append((path, doc))
        else:
            orphan_docs.append({"file": str(path.relative_to(ROOT)), "match_id": match_id or None})

    rows: list[dict[str, Any]] = []
    hard_violations: list[dict[str, Any]] = []
    accepted_source_count = 0
    rejected_source_count = 0

    for match_id, match in sorted(matches.items(), key=lambda item: (item[1]["kickoff_at_utc"], item[0])):
        team_sources: dict[str, list[dict[str, Any]]] = {match["home_team"]: [], match["away_team"]: []}
        evidence_files: list[str] = []

        for path, doc in by_match_docs.get(match_id, []):
            evidence_files.append(str(path.relative_to(ROOT)))
            fixture = doc.get("fixture_identity") or {}
            doc_identity = (
                str(fixture.get("competition_id") or "").strip(),
                str(fixture.get("home_team") or "").strip(),
                str(fixture.get("away_team") or "").strip(),
            )
            expected_identity = (match["competition_id"], match["home_team"], match["away_team"])
            if doc_identity != expected_identity:
                hard_violations.append({
                    "file": str(path.relative_to(ROOT)),
                    "match_id": match_id,
                    "violation": "document_fixture_identity_mismatch",
                    "expected": expected_identity,
                    "actual": doc_identity,
                })
            for source in doc.get("sources") or []:
                if not isinstance(source, dict):
                    hard_violations.append({
                        "file": str(path.relative_to(ROOT)),
                        "match_id": match_id,
                        "violation": "non_object_source_record",
                    })
                    rejected_source_count += 1
                    continue
                audit, violations = adjudicate_source(source, match, cfg)
                audit["evidence_file"] = str(path.relative_to(ROOT))
                if violations:
                    rejected_source_count += 1
                    for violation in violations:
                        hard_violations.append({
                            "file": str(path.relative_to(ROOT)),
                            "match_id": match_id,
                            "team_name": audit["team_name"],
                            "violation": violation,
                        })
                else:
                    accepted_source_count += 1
                    team_sources[audit["team_name"]].append(audit)

        def team_summary(team: str) -> dict[str, Any]:
            items = team_sources[team]
            return {
                "team_name": team,
                "accepted_source_count": len(items),
                "availability_verified": any(x["availability_verified"] for x in items),
                "predicted_xi_verified": any(x["predicted_xi_verified"] for x in items),
                "sources": items,
            }

        home = team_summary(match["home_team"])
        away = team_summary(match["away_team"])
        both_availability = home["availability_verified"] and away["availability_verified"]
        both_xi = home["predicted_xi_verified"] and away["predicted_xi_verified"]
        full_context = both_availability and both_xi

        rows.append({
            "match_id": match_id,
            "competition_id": match["competition_id"],
            "season": match["season"],
            "kickoff_at_utc": match["kickoff_at_utc"].isoformat(),
            "freeze_at_utc": match["freeze_at_utc"].isoformat(),
            "home_team": match["home_team"],
            "away_team": match["away_team"],
            "evidence_files": evidence_files,
            "home": home,
            "away": away,
            "both_teams_availability_verified": both_availability,
            "both_teams_predicted_xi_verified": both_xi,
            "full_match_context": full_context,
            "formal_probability_eligible": False,
        })

    payload = {
        "schema_version": "V6.31.0-match-context-pit-ledger-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_MATCH_BOUND_PIT_CONTEXT_LEDGER_NO_PROBABILITY_MUTATION",
        "status": "FAIL" if hard_violations or malformed_docs else "PASS",
        "frozen_match_count": len(rows),
        "evidence_document_count": len(docs),
        "accepted_source_count": accepted_source_count,
        "rejected_source_count": rejected_source_count,
        "malformed_document_count": len(malformed_docs),
        "orphan_document_count": len(orphan_docs),
        "coverage": {
            "matches_with_both_teams_availability": sum(r["both_teams_availability_verified"] for r in rows),
            "matches_with_both_teams_predicted_xi": sum(r["both_teams_predicted_xi_verified"] for r in rows),
            "matches_with_full_context": sum(r["full_match_context"] for r in rows),
        },
        "rows": rows,
        "hard_violations": hard_violations,
        "malformed_documents": malformed_docs,
        "orphan_documents": orphan_docs,
        "hard_semantics": {
            "bind_to_existing_prospective_market_match_id": True,
            "all_source_observed_at_must_be_at_or_before_freeze": True,
            "all_source_observed_at_must_be_pre_kickoff": True,
            "official_actual_lineup_after_freeze_is_future_information": True,
            "empty_availability_requires_explicit_no_absences": True,
            "predicted_xi_requires_dedicated_source_and_11_unique_players": True,
            "missing_match_context_is_data_gap_not_algorithm_failure": True,
        },
        "next_action": [
            "freeze availability evidence at the user's question-time for each active market match_id",
            "freeze predicted/expected XI evidence at the same question-time when publicly available",
            "accumulate settled forward rows before fitting any player/availability effect",
        ],
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "probability_generation": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "frozen_match_count": payload["frozen_match_count"],
        "evidence_document_count": payload["evidence_document_count"],
        "coverage": payload["coverage"],
        "hard_violation_count": len(hard_violations),
        "malformed_document_count": len(malformed_docs),
    }, ensure_ascii=False, indent=2))
    return 2 if payload["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
