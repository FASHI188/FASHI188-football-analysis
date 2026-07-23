#!/usr/bin/env python3
"""V6.1.3-R2 audit contract for the immutable pristine-forward lifecycle.

This repairs two audit-contract errors without touching any frozen prediction or settlement:

1) `fixtures_v612.json` is a transient work queue. Once a PREDICTION_FROZEN event has been appended,
   the fixture may be pruned from that queue. Its permanent evidence is the hash-chained prediction
   event plus the independently retained prospective market evidence. Therefore queue absence is not
   itself an invalidation.

2) The processed training-history repository is not the authoritative post-match settlement source.
   A RESULT_SETTLED event is reproduced first from the immutable result-inbox receipt whose SHA-256
   was frozen into the settlement event. The processed repository is only a secondary cross-check.

Historical V6.1.3 findings are never deleted. When an old invalidation was produced solely by one of
those superseded audit assumptions and the current R2 contract proves the sample, an append-only
`AUDIT_FINDING_SUPERSEDED` event explicitly resolves that finding key. Active invalidation therefore
means an unresolved hard finding, not every historical finding ever observed.

Research audit only. Formal CURRENT V5.0.1, frozen predictions, model weights and runtime
probabilities are unchanged.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for path in (VALIDATION, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_pristine_forward_audit_v613 as base
from platform_core import (
    PlatformError,
    atomic_write_json,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    sha256_json,
)

RESULT_INBOX = ROOT / "forward" / "inbox" / "results_v612.json"
CONTRACT_REVISION = "V6.1.3-R2-IMMUTABLE-RECEIPT-LIFECYCLE"
STATUS_SCHEMA = base.STATUS_SCHEMA
DEPRECATED_HARD_KINDS = {
    "FROZEN_FIXTURE_INBOX_MISSING",
    "SETTLED_RESULT_NOT_REPRODUCIBLE",
    "SETTLED_RESULT_AMBIGUOUS",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _result_inbox_map() -> tuple[dict[tuple[str, str], list[dict[str, Any]]], str | None]:
    if not RESULT_INBOX.exists():
        return {}, "result inbox missing"
    data = load_json(RESULT_INBOX)
    if data.get("schema_version") != "V6.1.2-result-inbox-r1" or not isinstance(data.get("results"), list):
        raise PlatformError("invalid V6.1.2 result inbox envelope")
    output: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in data["results"]:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("competition_id") or ""), str(row.get("source_fixture_id") or ""))
        output.setdefault(key, []).append(row)
    return output, None


def _exact_frozen_evidence(
    event: dict[str, Any], evidence: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    payload = event.get("payload") or {}
    identity = payload.get("fixture_identity") or {}
    source = payload.get("fixture_source") or {}
    frozen = payload.get("freeze") or {}
    errors: list[str] = []

    try:
        kickoff = parse_iso_datetime(str(identity.get("kickoff_at") or ""), "kickoff_at")
        source_observed = parse_iso_datetime(str(source.get("observed_at") or ""), "fixture_source.observed_at")
        frozen_at = parse_iso_datetime(
            str(frozen.get("frozen_at_utc") or event.get("event_timestamp_utc") or ""), "frozen_at"
        )
    except Exception as exc:
        return False, {"reason": f"invalid frozen identity/source timestamp: {type(exc).__name__}: {exc}"}

    identity_hash = str(payload.get("fixture_identity_sha256") or "")
    if identity_hash != sha256_json(identity):
        errors.append("fixture_identity_sha256_mismatch")
    inbox_hash = str(payload.get("fixture_inbox_sha256") or "")
    if not HEX64.fullmatch(inbox_hash):
        errors.append("fixture_inbox_sha256_missing_or_invalid")
    if not str(source.get("name") or "").strip():
        errors.append("fixture_source_name_missing")
    if not str(source.get("source_record_id") or "").strip():
        errors.append("fixture_source_record_id_missing")
    if source_observed > frozen_at:
        errors.append("fixture_source_observed_after_freeze")
    if frozen_at >= kickoff:
        errors.append("prediction_not_strictly_pre_kickoff")

    cid = str(identity.get("competition_id") or "")
    home = normalize_team_token(str(identity.get("home_team") or ""))
    away = normalize_team_token(str(identity.get("away_team") or ""))
    provider_token = base._provider_event_token(str(source.get("url") or ""))
    source_record_id = str(source.get("source_record_id") or "")
    matches: list[dict[str, Any]] = []
    for item in evidence:
        if str(item.get("competition_id") or "") != cid:
            continue
        if normalize_team_token(str(item.get("home_team") or "")) != home:
            continue
        if normalize_team_token(str(item.get("away_team") or "")) != away:
            continue
        if item.get("kickoff") != kickoff or item.get("observed") != source_observed:
            continue
        if provider_token and item.get("provider_event_token") != provider_token:
            continue
        raw = item.get("raw") or {}
        raw_record_id = str(raw.get("raw_snapshot_sha256") or raw.get("source_record_id") or "")
        if raw_record_id and source_record_id and raw_record_id != source_record_id:
            continue
        matches.append(item)

    if not matches:
        errors.append("original_prospective_source_snapshot_not_found")
    unique_hashes = sorted({str(item.get("sha256_json") or "") for item in matches if item.get("sha256_json")})
    return not errors, {
        "errors": errors,
        "matching_evidence_paths": [item.get("path") for item in matches[:10]],
        "matching_evidence_unique_hashes": unique_hashes,
        "fixture_identity_sha256": identity_hash,
        "fixture_inbox_sha256": inbox_hash,
        "fixture_source_record_id": source_record_id,
        "source_observed_at": source_observed.isoformat(),
        "frozen_at": frozen_at.isoformat(),
        "kickoff_at": kickoff.isoformat(),
    }


def _audit_predictions_r2(
    now: datetime,
    freeze: dict[str, Any],
    ledger: dict[str, Any],
    fixtures: dict[tuple[str, str], dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings, old_stats = base._audit_predictions(now, freeze, ledger, fixtures, evidence)
    events = base._prediction_events(ledger)
    output: list[dict[str, Any]] = []
    stats = Counter(old_stats)
    stats["legacy_transient_inbox_missing_seen"] = 0
    stats["transient_inbox_pruned_after_freeze_verified"] = 0
    stats["immutable_frozen_receipt_unverified"] = 0

    for finding in findings:
        if finding.get("kind") != "FROZEN_FIXTURE_INBOX_MISSING":
            output.append(finding)
            continue
        stats["legacy_transient_inbox_missing_seen"] += 1
        match_id = str(finding.get("match_id") or "")
        event = events.get(match_id)
        if event is None:
            output.append({
                "kind": "FROZEN_FIXTURE_LEDGER_EVENT_MISSING",
                "match_id": match_id,
                "severity": "BLOCK",
                "invalidates_forward_sample": True,
                "detail": {"legacy_finding": finding},
            })
            stats["immutable_frozen_receipt_unverified"] += 1
            continue
        ok, proof = _exact_frozen_evidence(event, evidence)
        if ok:
            stats["transient_inbox_pruned_after_freeze_verified"] += 1
            continue
        output.append({
            "kind": "FROZEN_FIXTURE_LEDGER_RECEIPT_UNVERIFIED",
            "match_id": match_id,
            "severity": "BLOCK",
            "invalidates_forward_sample": True,
            "detail": proof,
        })
        stats["immutable_frozen_receipt_unverified"] += 1

    stats["findings"] = len(output)
    stats["invalidating_findings"] = sum(1 for row in output if row.get("invalidates_forward_sample"))
    return output, dict(sorted(stats.items()))


def _normalized_source(raw: Any) -> dict[str, Any] | None:
    try:
        value, _ = base.ledgerlib._source_block(raw, field="source")
        return value
    except Exception:
        return None


def _audit_settlements_r2(now: datetime, ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stats: Counter = Counter()
    predictions = base._prediction_events(ledger)
    settlements = base._settlement_events(ledger)
    result_map, result_map_error = _result_inbox_map()
    processed_cache: dict[str, list[Any]] = {}

    for match_id, prediction_event in predictions.items():
        identity = prediction_event["payload"]["fixture_identity"]
        kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
        settlement = settlements.get(match_id)
        if settlement is None:
            if now >= kickoff + base.RESULT_GRACE:
                base._finding(
                    findings,
                    kind="OPEN_RESULT_DELAY",
                    match_id=match_id,
                    severity="WARN",
                    invalidates=False,
                    detail={
                        "kickoff_at": kickoff.isoformat(),
                        "hours_since_kickoff": (now - kickoff).total_seconds() / 3600.0,
                    },
                )
            continue

        stats["settlements_audited"] += 1
        payload = settlement.get("payload") or {}
        recorded = payload.get("result") or {}
        cid = str(identity.get("competition_id") or "")
        source_fixture_id = str(identity.get("source_fixture_id") or "")
        key = (cid, source_fixture_id)
        receipts = result_map.get(key, [])
        if result_map_error or len(receipts) == 0:
            base._finding(
                findings,
                kind="SETTLED_RESULT_RECEIPT_MISSING",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"competition_id": cid, "source_fixture_id": source_fixture_id, "result_inbox_error": result_map_error},
            )
            stats["frozen_result_receipt_missing"] += 1
            continue
        if len(receipts) != 1:
            base._finding(
                findings,
                kind="SETTLED_RESULT_RECEIPT_AMBIGUOUS",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"competition_id": cid, "source_fixture_id": source_fixture_id, "candidate_count": len(receipts)},
            )
            stats["frozen_result_receipt_ambiguous"] += 1
            continue

        receipt = receipts[0]
        current_hash = sha256_json(receipt)
        recorded_hash = str(payload.get("result_inbox_sha256") or "")
        if current_hash != recorded_hash:
            base._finding(
                findings,
                kind="SETTLED_RESULT_RECEIPT_MUTATED",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"recorded_sha256": recorded_hash, "current_sha256": current_hash},
            )
            stats["frozen_result_receipt_hash_mismatch"] += 1
            continue

        try:
            receipt_score = {
                "home_goals_90": int(receipt["home_goals_90"]),
                "away_goals_90": int(receipt["away_goals_90"]),
            }
            recorded_score = {
                "home_goals_90": int(recorded["home_goals_90"]),
                "away_goals_90": int(recorded["away_goals_90"]),
            }
        except Exception as exc:
            base._finding(
                findings,
                kind="SETTLED_RESULT_RECEIPT_INVALID",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
            stats["frozen_result_receipt_invalid"] += 1
            continue

        if receipt_score != recorded_score:
            base._finding(
                findings,
                kind="SETTLED_RESULT_CONFLICT",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"recorded": recorded_score, "frozen_result_receipt": receipt_score},
            )
            stats["frozen_result_receipt_score_conflict"] += 1
            continue

        actual = "home" if receipt_score["home_goals_90"] > receipt_score["away_goals_90"] else "away" if receipt_score["home_goals_90"] < receipt_score["away_goals_90"] else "draw"
        if str(recorded.get("actual_result") or "") != actual:
            base._finding(
                findings,
                kind="SETTLED_RESULT_CONFLICT",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"recorded_actual_result": recorded.get("actual_result"), "receipt_actual_result": actual},
            )
            stats["frozen_result_receipt_outcome_conflict"] += 1
            continue

        settlement_source = payload.get("result_source") or {}
        receipt_source = _normalized_source(receipt.get("source"))
        if receipt_source is None or receipt_source != settlement_source:
            base._finding(
                findings,
                kind="SETTLED_RESULT_SOURCE_REFERENCE_MISMATCH",
                match_id=match_id,
                severity="BLOCK",
                invalidates=True,
                detail={"settlement_source": settlement_source, "receipt_source": receipt_source},
            )
            stats["frozen_result_source_reference_mismatch"] += 1
            continue

        stats["frozen_result_receipt_reproduced"] += 1

        # The processed repository is deliberately secondary. Missing/ambiguous rows are diagnostic
        # only; an actual contradictory score remains a hard conflict.
        if cid not in processed_cache:
            try:
                processed_cache[cid] = base.read_processed_matches(cid)
            except Exception:
                processed_cache[cid] = []
        matches = [
            match
            for match in processed_cache[cid]
            if match.date.date() == kickoff.date()
            and normalize_team_token(match.home_team) == normalize_team_token(identity["home_team"])
            and normalize_team_token(match.away_team) == normalize_team_token(identity["away_team"])
        ]
        if len(matches) == 0:
            stats["processed_crosscheck_missing"] += 1
        elif len(matches) > 1:
            stats["processed_crosscheck_ambiguous"] += 1
        else:
            current = {"home_goals_90": int(matches[0].home_goals), "away_goals_90": int(matches[0].away_goals)}
            if current != recorded_score:
                base._finding(
                    findings,
                    kind="SETTLED_RESULT_CONFLICT",
                    match_id=match_id,
                    severity="BLOCK",
                    invalidates=True,
                    detail={"recorded": recorded_score, "current_processed": current, "source_path": matches[0].source_path},
                )
                stats["processed_crosscheck_conflict"] += 1
            else:
                stats["processed_crosscheck_match"] += 1

    stats["findings"] = len(findings)
    stats["invalidating_findings"] = sum(1 for row in findings if row.get("invalidates_forward_sample"))
    return findings, dict(sorted(stats.items()))


def _resolved_finding_keys(audit_ledger: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    for event in audit_ledger.get("events", []):
        payload = event.get("payload") or {}
        if payload.get("kind") != "AUDIT_FINDING_SUPERSEDED":
            continue
        target = str((payload.get("detail") or {}).get("target_finding_key") or "")
        if target:
            output.add(target)
    return output


def _active_invalidated_ids(audit_ledger: dict[str, Any]) -> list[str]:
    resolved = _resolved_finding_keys(audit_ledger)
    return sorted({
        str(event.get("match_id"))
        for event in audit_ledger.get("events", [])
        if event.get("match_id")
        and bool((event.get("payload") or {}).get("invalidates_forward_sample"))
        and str(event.get("finding_key") or "") not in resolved
    })


def _append_contract_resolutions(
    audit_ledger: dict[str, Any],
    current_findings: list[dict[str, Any]],
    now: datetime,
) -> int:
    current = {(str(row.get("kind") or ""), str(row.get("match_id") or "")) for row in current_findings}
    already_resolved = _resolved_finding_keys(audit_ledger)
    candidates = list(audit_ledger.get("events", []))
    appended = 0
    for event in candidates:
        payload = event.get("payload") or {}
        kind = str(payload.get("kind") or "")
        match_id = str(event.get("match_id") or "")
        finding_key = str(event.get("finding_key") or "")
        if not finding_key or finding_key in already_resolved:
            continue
        if not bool(payload.get("invalidates_forward_sample")) or kind not in DEPRECATED_HARD_KINDS:
            continue
        if (kind, match_id) in current:
            continue
        reason = (
            "Transient fixture inbox absence is not evidence loss after an immutable PREDICTION_FROZEN event; R2 independently verified the frozen event identity/source and retained prospective evidence."
            if kind == "FROZEN_FIXTURE_INBOX_MISSING"
            else "Processed training history is not the authoritative settlement store; R2 reproduced the immutable RESULT_SETTLED event from its frozen result-inbox receipt and source reference."
        )
        detail = {
            "target_finding_key": finding_key,
            "target_kind": kind,
            "target_match_id": match_id,
            "superseded_by_contract_revision": CONTRACT_REVISION,
            "reason": reason,
            "historical_finding_deleted": False,
        }
        if base._append_finding(
            audit_ledger,
            now=now,
            kind="AUDIT_FINDING_SUPERSEDED",
            severity="INFO",
            match_id=match_id or None,
            detail=detail,
            invalidates_forward_sample=False,
        ):
            appended += 1
            already_resolved.add(finding_key)
    return appended


def _run_self_test() -> int:
    ledger = {"schema_version": base.AUDIT_LEDGER_SCHEMA, "events": []}
    now = datetime(2026, 7, 23, 9, 30, tzinfo=timezone.utc)
    base._append_finding(
        ledger,
        now=now,
        kind="FROZEN_FIXTURE_INBOX_MISSING",
        severity="BLOCK",
        match_id="match_test",
        detail={"competition_id": "TEST", "source_fixture_id": "1"},
        invalidates_forward_sample=True,
    )
    if _active_invalidated_ids(ledger) != ["match_test"]:
        raise PlatformError("R2 active invalidation self-test setup failed")
    old_key = ledger["events"][0]["finding_key"]
    base._append_finding(
        ledger,
        now=now,
        kind="AUDIT_FINDING_SUPERSEDED",
        severity="INFO",
        match_id="match_test",
        detail={
            "target_finding_key": old_key,
            "target_kind": "FROZEN_FIXTURE_INBOX_MISSING",
            "target_match_id": "match_test",
            "superseded_by_contract_revision": CONTRACT_REVISION,
            "reason": "self-test",
            "historical_finding_deleted": False,
        },
        invalidates_forward_sample=False,
    )
    if _active_invalidated_ids(ledger):
        raise PlatformError("R2 supersession did not clear active invalidation")
    if base._audit_audit_chain(ledger)["status"] != "PASS":
        raise PlatformError("R2 append-only supersession broke audit chain")
    print(json.dumps({"status": "PASS", "self_test": CONTRACT_REVISION}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _run_self_test()

    now = base._utc_now()
    freeze = load_json(base.FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    prediction_ledger = load_json(base.PREDICTION_LEDGER)
    prediction_chain = base.ledgerlib._audit_chain(prediction_ledger)
    frozen_source_integrity = base.ledgerlib._source_integrity(freeze)
    baseline, baseline_created = base._load_or_create_baseline(now, freeze)
    runtime = base._runtime_drift(baseline)

    audit_ledger = base._load_audit_ledger()
    audit_before = base._audit_audit_chain(audit_ledger)
    if audit_before["status"] != "PASS":
        payload = {
            "schema_version": STATUS_SCHEMA,
            "generated_at_utc": now.isoformat(),
            "status": "FAIL_AUDIT_LEDGER_TAMPERED",
            "audit_contract_revision": CONTRACT_REVISION,
            "prediction_ledger_chain": prediction_chain,
            "frozen_source_integrity": frozen_source_integrity,
            "runtime_dependency_integrity": runtime,
            "audit_ledger": audit_before,
            "invalidated_match_ids": _active_invalidated_ids(audit_ledger),
            "governance": {"automatic_promotion": False, "current_rule_change": False},
        }
        atomic_write_json(base.OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fixtures = base._fixture_inbox_map()
    evidence = base._load_evidence()
    prediction_findings, prediction_stats = _audit_predictions_r2(now, freeze, prediction_ledger, fixtures, evidence)
    settlement_findings, settlement_stats = _audit_settlements_r2(now, prediction_ledger)
    current_findings = prediction_findings + settlement_findings

    new_finding_events = 0
    for finding in current_findings:
        if base._append_finding(
            audit_ledger,
            now=now,
            kind=finding["kind"],
            severity=finding["severity"],
            match_id=finding["match_id"],
            detail=finding["detail"],
            invalidates_forward_sample=finding["invalidates_forward_sample"],
        ):
            new_finding_events += 1

    new_resolution_events = _append_contract_resolutions(audit_ledger, current_findings, now)
    atomic_write_json(base.AUDIT_LEDGER, audit_ledger)
    audit_after = base._audit_audit_chain(audit_ledger)
    invalidated = _active_invalidated_ids(audit_ledger)

    status = "PASS"
    if prediction_chain["status"] != "PASS":
        status = "FAIL_PREDICTION_LEDGER_TAMPERED"
    elif frozen_source_integrity["status"] != "PASS":
        status = "FAIL_FROZEN_SOURCE_CHANGED"
    elif runtime["status"] != "PASS":
        status = "FAIL_RUNTIME_DEPENDENCY_DRIFT"
    elif audit_after["status"] != "PASS":
        status = "FAIL_AUDIT_LEDGER"
    elif current_findings or invalidated:
        status = "PASS_WITH_FINDINGS"

    persistent_counts = base._finding_counts(audit_ledger)
    active_hard_count = sum(
        1 for event in audit_ledger.get("events", [])
        if event.get("match_id") in set(invalidated)
        and bool((event.get("payload") or {}).get("invalidates_forward_sample"))
        and str(event.get("finding_key") or "") not in _resolved_finding_keys(audit_ledger)
    )
    payload = {
        "schema_version": STATUS_SCHEMA,
        "generated_at_utc": now.isoformat(),
        "status": status,
        "audit_contract_revision": CONTRACT_REVISION,
        "baseline_created_this_run": baseline_created,
        "baseline_path": str(base.BASELINE.relative_to(ROOT)),
        "baseline_sha256": base._sha256_file(base.BASELINE),
        "prediction_ledger_chain": prediction_chain,
        "frozen_source_integrity": frozen_source_integrity,
        "runtime_dependency_integrity": runtime,
        "prediction_audit": prediction_stats,
        "settlement_audit": settlement_stats,
        "prospective_evidence_files_audited": len(evidence),
        "current_findings": current_findings,
        "new_audit_finding_events": new_finding_events,
        "new_audit_resolution_events": new_resolution_events,
        "audit_ledger": audit_after,
        "persistent_finding_counts": persistent_counts,
        "historical_superseded_finding_count": len(_resolved_finding_keys(audit_ledger)),
        "active_unresolved_hard_finding_count": active_hard_count,
        "invalidated_match_ids": invalidated,
        "invalidated_match_count": len(invalidated),
        "evaluation_blocked": status.startswith("FAIL_"),
        "governance": {
            "prediction_ledger_mutation": False,
            "audit_findings_append_only": True,
            "audit_supersession_events_append_only": True,
            "historical_findings_deleted": False,
            "transient_fixture_inbox_not_permanent_evidence": True,
            "immutable_prediction_event_plus_prospective_source_is_permanent_fixture_evidence": True,
            "frozen_result_inbox_receipt_is_primary_settlement_reproduction": True,
            "processed_training_repository_is_settlement_crosscheck_only": True,
            "critical_runtime_drift_blocks_evaluation": True,
            "unresolved_hard_findings_excluded_from_forward_metrics": True,
            "historical_input_reconstruction_drift_is_warning_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    atomic_write_json(base.OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not status.startswith("FAIL_") else 1


if __name__ == "__main__":
    raise SystemExit(main())