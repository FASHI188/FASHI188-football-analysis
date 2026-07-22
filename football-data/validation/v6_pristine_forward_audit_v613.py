#!/usr/bin/env python3
"""V6.1.3 pristine-forward integrity, identity-drift and settlement audit.

This layer never edits V6.1.2 prediction/settlement events. It creates one immutable
runtime-dependency baseline, a separate append-only audit hash chain, and a current
audit receipt with persistent invalidated match ids.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_pristine_forward_ledger_v612 as ledgerlib
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
PREDICTION_LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
FIXTURE_INBOX = ROOT / "forward" / "inbox" / "fixtures_v612.json"
EVIDENCE_ROOT = ROOT / "evidence" / "markets_prospective"
BASELINE = ROOT / "manifests" / "v6_pristine_forward_audit_baseline_v613.json"
AUDIT_LEDGER = ROOT / "forward" / "v6_pristine_forward_audit_events_v613.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_audit_v613_status.json"

BASELINE_SCHEMA = "V6.1.3-forward-audit-baseline-r1"
AUDIT_EVENT_SCHEMA = "V6.1.3-forward-audit-event-r1"
AUDIT_LEDGER_SCHEMA = "V6.1.3-forward-audit-ledger-r1"
STATUS_SCHEMA = "V6.1.3-forward-audit-status-r1"
GENESIS = "GENESIS"
RESULT_GRACE = timedelta(hours=12)
IDENTITY_SEARCH_WINDOW = timedelta(days=14)

CRITICAL_RUNTIME_FILES = (
    "validation/v6_pristine_forward_ledger_v612.py",
    "validation/v6_direct_outcome_mvp_v600.py",
    "validation/v6_direct_outcome_draw_boundary_v601.py",
    "validation/backtest_last_complete_season_all_domains_v470.py",
    "engine/football_v460_engine.py",
    "engine/oof_matrix_calibration.py",
    "engine/platform_core.py",
    "manifests/v6_pristine_forward_freeze_v610_status.json",
)

HARD_INVALIDATING_KINDS = {
    "FROZEN_FIXTURE_INBOX_MISSING",
    "FROZEN_FIXTURE_INBOX_MUTATED",
    "FROZEN_PROSPECTIVE_EVIDENCE_MISSING",
    "FROZEN_PROSPECTIVE_EVIDENCE_MUTATED",
    "FIXTURE_IDENTITY_DRIFT",
    "FIXTURE_CANCELLED_OR_POSTPONED",
    "PREDICTION_FREEZE_HASH_MISMATCH",
    "PREDICTION_FROZEN_SOURCE_HASH_MISMATCH",
    "SETTLED_RESULT_NOT_REPRODUCIBLE",
    "SETTLED_RESULT_AMBIGUOUS",
    "SETTLED_RESULT_CONFLICT",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _critical_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in CRITICAL_RUNTIME_FILES:
        path = ROOT / relative
        if not path.exists():
            raise PlatformError(f"critical runtime file missing: {relative}")
        hashes[relative] = _sha256_file(path)
    return hashes


def _load_or_create_baseline(now: datetime, freeze: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if BASELINE.exists():
        baseline = load_json(BASELINE)
        if baseline.get("schema_version") != BASELINE_SCHEMA:
            raise PlatformError("unexpected V6.1.3 baseline schema")
        return baseline, False
    baseline = {
        "schema_version": BASELINE_SCHEMA,
        "created_at_utc": now.isoformat(),
        "status": "FROZEN",
        "critical_runtime_sha256": _critical_hashes(),
        "freeze_bundle_sha256": _sha256_file(FREEZE),
        "frozen_probability_model_sha256": sha256_json(freeze["frozen_probability_model"]),
        "frozen_arms_sha256": sha256_json(freeze["frozen_arms"]),
        "governance": {
            "baseline_mutation_allowed": False,
            "runtime_dependency_change_requires_new_test_epoch": True,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    atomic_write_json(BASELINE, baseline)
    return baseline, True


def _runtime_drift(baseline: dict[str, Any]) -> dict[str, Any]:
    expected = baseline.get("critical_runtime_sha256") or {}
    actual = _critical_hashes()
    mismatches = {
        key: {"expected": expected.get(key), "actual": actual.get(key)}
        for key in sorted(set(expected) | set(actual))
        if expected.get(key) != actual.get(key)
    }
    return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches, "current_sha256": actual}


def _event_hash(event: dict[str, Any]) -> str:
    return sha256_json({key: value for key, value in event.items() if key != "event_hash"})


def _load_audit_ledger() -> dict[str, Any]:
    if not AUDIT_LEDGER.exists():
        return {"schema_version": AUDIT_LEDGER_SCHEMA, "events": []}
    data = load_json(AUDIT_LEDGER)
    if data.get("schema_version") != AUDIT_LEDGER_SCHEMA or not isinstance(data.get("events"), list):
        raise PlatformError("invalid V6.1.3 audit ledger envelope")
    return data


def _audit_audit_chain(ledger: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    previous = GENESIS
    keys: set[str] = set()
    last_at: datetime | None = None
    for index, event in enumerate(ledger.get("events", []), start=1):
        if event.get("schema_version") != AUDIT_EVENT_SCHEMA:
            errors.append(f"schema mismatch at {index}")
        if event.get("sequence") != index:
            errors.append(f"sequence mismatch at {index}")
        if event.get("previous_event_hash") != previous:
            errors.append(f"previous hash mismatch at {index}")
        if event.get("event_hash") != _event_hash(event):
            errors.append(f"event hash mismatch at {index}")
        key = str(event.get("finding_key") or "")
        if not key or key in keys:
            errors.append(f"missing or duplicate finding_key at {index}")
        keys.add(key)
        try:
            at = parse_iso_datetime(str(event.get("event_timestamp_utc") or ""), "audit_event_timestamp")
            if last_at is not None and at < last_at:
                errors.append(f"audit timestamp regression at {index}")
            last_at = at
        except Exception as exc:
            errors.append(f"invalid audit timestamp at {index}: {exc}")
        previous = str(event.get("event_hash") or "")
    return {
        "status": "PASS" if not errors else "FAIL",
        "event_count": len(ledger.get("events", [])),
        "tip_hash": previous,
        "errors": errors,
    }


def _finding_key(kind: str, match_id: str | None, detail: dict[str, Any]) -> str:
    return sha256_json({"kind": kind, "match_id": match_id, "detail": detail})


def _append_finding(
    ledger: dict[str, Any], *, now: datetime, kind: str, severity: str,
    match_id: str | None, detail: dict[str, Any], invalidates_forward_sample: bool,
) -> bool:
    key = _finding_key(kind, match_id, detail)
    if any(str(event.get("finding_key")) == key for event in ledger["events"]):
        return False
    previous = ledger["events"][-1]["event_hash"] if ledger["events"] else GENESIS
    event = {
        "schema_version": AUDIT_EVENT_SCHEMA,
        "sequence": len(ledger["events"]) + 1,
        "event_timestamp_utc": now.isoformat(),
        "event_type": "AUDIT_FINDING_OBSERVED",
        "finding_key": key,
        "previous_event_hash": previous,
        "match_id": match_id,
        "payload": {
            "kind": kind,
            "severity": severity,
            "invalidates_forward_sample": bool(invalidates_forward_sample),
            "detail": detail,
        },
    }
    event["event_hash"] = _event_hash(event)
    ledger["events"].append(event)
    return True


def _fixture_inbox_map() -> dict[tuple[str, str], dict[str, Any]]:
    if not FIXTURE_INBOX.exists():
        return {}
    data = load_json(FIXTURE_INBOX)
    if data.get("schema_version") != "V6.1.2-fixture-inbox-r1" or not isinstance(data.get("fixtures"), list):
        raise PlatformError("invalid V6.1.2 fixture inbox")
    return {
        (str(item.get("competition_id") or ""), str(item.get("source_fixture_id") or "")): item
        for item in data["fixtures"]
    }


def _provider_event_token(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/event/([A-Za-z0-9_-]+)", str(url))
    return match.group(1) if match else None


def _load_evidence() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not EVIDENCE_ROOT.exists():
        return records
    for path in sorted(EVIDENCE_ROOT.glob("*.json")):
        try:
            raw = load_json(path)
            cid = str(raw.get("competition_id") or "").strip()
            kickoff = parse_iso_datetime(str(raw.get("kickoff_utc") or ""), "kickoff_utc")
            observed = parse_iso_datetime(
                str(raw.get("source_observed_at_utc") or raw.get("freeze_utc") or raw.get("accessed_at_utc") or ""),
                "source_observed_at_utc",
            )
            home = canonical_team_name(cid, str(raw.get("home_team") or ""))
            away = canonical_team_name(cid, str(raw.get("away_team") or ""))
            records.append({
                "path": str(path.relative_to(ROOT)),
                "sha256_json": sha256_json(raw),
                "raw": raw,
                "competition_id": cid,
                "kickoff": kickoff,
                "observed": observed,
                "home_team": home,
                "away_team": away,
                "home_token": normalize_team_token(home),
                "away_token": normalize_team_token(away),
                "provider_event_token": _provider_event_token(str(raw.get("source_url") or "")),
            })
        except Exception:
            continue
    return records


def _prediction_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(event["match_id"]): event
        for event in ledger.get("events", [])
        if event.get("event_type") == "PREDICTION_FROZEN"
    }


def _settlement_events(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(event["match_id"]): event
        for event in ledger.get("events", [])
        if event.get("event_type") == "RESULT_SETTLED"
    }


def _history_reconstruction_hash(identity: dict[str, Any]) -> tuple[str | None, int | None]:
    try:
        kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
        all_matches = sorted(
            read_processed_matches(identity["competition_id"]),
            key=lambda match: (match.date, match.home_team, match.away_team),
        )
        history = [match for match in all_matches if match.date.date() < kickoff.date()]
        return ledgerlib._history_digest(history), len(history)
    except Exception:
        return None, None


def _finding(
    findings: list[dict[str, Any]], *, kind: str, match_id: str | None,
    severity: str, detail: dict[str, Any], invalidates: bool | None = None,
) -> None:
    findings.append({
        "kind": kind,
        "match_id": match_id,
        "severity": severity,
        "invalidates_forward_sample": kind in HARD_INVALIDATING_KINDS if invalidates is None else bool(invalidates),
        "detail": detail,
    })


def _audit_predictions(
    now: datetime, freeze: dict[str, Any], ledger: dict[str, Any],
    fixtures: dict[tuple[str, str], dict[str, Any]], evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stats: Counter = Counter()
    expected_freeze_sha = _sha256_file(FREEZE)
    expected_model_sha = sha256_json(freeze["frozen_probability_model"])
    expected_arms_sha = sha256_json(freeze["frozen_arms"])
    expected_source_integrity = {
        key: value for key, value in (freeze.get("source_integrity") or {}).items()
        if key.endswith("_code_sha256")
    }

    evidence_by_path = {item["path"]: item for item in evidence}
    by_provider_token: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in evidence:
        if item["provider_event_token"]:
            by_provider_token[(item["competition_id"], item["provider_event_token"])].append(item)
        by_pair[(item["competition_id"], item["home_token"], item["away_token"])].append(item)

    for match_id, event in _prediction_events(ledger).items():
        stats["predictions_audited"] += 1
        payload = event.get("payload") or {}
        identity = payload.get("fixture_identity") or {}
        frozen = payload.get("freeze") or {}
        cid = str(identity.get("competition_id") or "")
        source_fixture_id = str(identity.get("source_fixture_id") or "")
        kickoff = parse_iso_datetime(str(identity.get("kickoff_at") or ""), "kickoff_at")
        frozen_at = parse_iso_datetime(str(frozen.get("frozen_at_utc") or event.get("event_timestamp_utc") or ""), "frozen_at")
        source = payload.get("fixture_source") or {}
        source_observed = parse_iso_datetime(str(source.get("observed_at") or ""), "fixture_source.observed_at")

        if (
            frozen.get("freeze_receipt_sha256") != expected_freeze_sha
            or frozen.get("frozen_model_sha256") != expected_model_sha
            or frozen.get("frozen_arms_sha256") != expected_arms_sha
        ):
            _finding(
                findings, kind="PREDICTION_FREEZE_HASH_MISMATCH", match_id=match_id, severity="BLOCK",
                detail={
                    "recorded_freeze_receipt_sha256": frozen.get("freeze_receipt_sha256"),
                    "expected_freeze_receipt_sha256": expected_freeze_sha,
                    "recorded_model_sha256": frozen.get("frozen_model_sha256"),
                    "expected_model_sha256": expected_model_sha,
                    "recorded_arms_sha256": frozen.get("frozen_arms_sha256"),
                    "expected_arms_sha256": expected_arms_sha,
                },
            )

        recorded_integrity = frozen.get("source_integrity") or {}
        mismatched_sources = {
            key: {"recorded": recorded_integrity.get(key), "expected": value}
            for key, value in expected_source_integrity.items()
            if recorded_integrity.get(key) != value
        }
        if mismatched_sources:
            _finding(
                findings, kind="PREDICTION_FROZEN_SOURCE_HASH_MISMATCH", match_id=match_id, severity="BLOCK",
                detail={"mismatches": mismatched_sources},
            )

        inbox_item = fixtures.get((cid, source_fixture_id))
        if inbox_item is None:
            _finding(
                findings, kind="FROZEN_FIXTURE_INBOX_MISSING", match_id=match_id, severity="BLOCK",
                detail={"competition_id": cid, "source_fixture_id": source_fixture_id},
            )
        else:
            current_inbox_sha = sha256_json(inbox_item)
            if current_inbox_sha != payload.get("fixture_inbox_sha256"):
                _finding(
                    findings, kind="FROZEN_FIXTURE_INBOX_MUTATED", match_id=match_id, severity="BLOCK",
                    detail={"recorded_sha256": payload.get("fixture_inbox_sha256"), "current_sha256": current_inbox_sha},
                )
            autofeed = inbox_item.get("autofeed") or {}
            evidence_path = str(autofeed.get("evidence_path") or "")
            expected_evidence_sha = str(autofeed.get("evidence_sha256") or "")
            if evidence_path:
                item = evidence_by_path.get(evidence_path)
                if item is None:
                    _finding(
                        findings, kind="FROZEN_PROSPECTIVE_EVIDENCE_MISSING", match_id=match_id, severity="BLOCK",
                        detail={"evidence_path": evidence_path},
                    )
                elif expected_evidence_sha and item["sha256_json"] != expected_evidence_sha:
                    _finding(
                        findings, kind="FROZEN_PROSPECTIVE_EVIDENCE_MUTATED", match_id=match_id, severity="BLOCK",
                        detail={
                            "evidence_path": evidence_path,
                            "recorded_sha256": expected_evidence_sha,
                            "current_sha256": item["sha256_json"],
                        },
                    )

        provider_token = _provider_event_token(str(source.get("url") or ""))
        if provider_token:
            candidates = by_provider_token.get((cid, provider_token), [])
            exact_provider_match = True
        else:
            pair_key = (
                cid,
                normalize_team_token(str(identity.get("home_team") or "")),
                normalize_team_token(str(identity.get("away_team") or "")),
            )
            candidates = [
                item for item in by_pair.get(pair_key, [])
                if abs(item["kickoff"] - kickoff) <= IDENTITY_SEARCH_WINDOW
            ]
            exact_provider_match = False

        for item in candidates:
            if item["observed"] <= source_observed:
                continue
            raw = item["raw"]
            later_identity = {
                "kickoff_at": item["kickoff"].isoformat(),
                "home_team": item["home_team"],
                "away_team": item["away_team"],
                "season": str(raw.get("season") or ""),
                "stage": str(raw.get("stage") or "stage_unverified"),
            }
            changed: dict[str, Any] = {}
            for key in ("kickoff_at", "home_team", "away_team", "season", "stage"):
                recorded = str(identity.get(key) or "")
                observed = str(later_identity.get(key) or "")
                unequal = (
                    normalize_team_token(recorded) != normalize_team_token(observed)
                    if key in {"home_team", "away_team"}
                    else recorded != observed
                )
                if unequal:
                    changed[key] = {"frozen": recorded, "later_observed": observed}
            if changed:
                _finding(
                    findings, kind="FIXTURE_IDENTITY_DRIFT", match_id=match_id, severity="BLOCK",
                    detail={
                        "exact_provider_event_match": exact_provider_match,
                        "later_evidence_path": item["path"],
                        "later_observed_at": item["observed"].isoformat(),
                        "changes": changed,
                    },
                )
                break
            status_token = str(raw.get("event_status") or raw.get("match_status") or raw.get("status") or "").strip().lower()
            if status_token in {"cancelled", "canceled", "postponed", "abandoned", "suspended"}:
                _finding(
                    findings, kind="FIXTURE_CANCELLED_OR_POSTPONED", match_id=match_id, severity="BLOCK",
                    detail={
                        "status": status_token,
                        "later_evidence_path": item["path"],
                        "later_observed_at": item["observed"].isoformat(),
                    },
                )
                break

        history = (payload.get("prediction") or {}).get("history") or {}
        current_history_sha, current_history_count = _history_reconstruction_hash(identity)
        recorded_history_sha = history.get("history_sha256")
        if current_history_sha is not None and recorded_history_sha and current_history_sha != recorded_history_sha:
            _finding(
                findings, kind="HISTORY_RECONSTRUCTION_DRIFT", match_id=match_id, severity="WARN", invalidates=False,
                detail={
                    "recorded_sha256": recorded_history_sha,
                    "current_sha256": current_history_sha,
                    "recorded_row_count": history.get("row_count"),
                    "current_row_count": current_history_count,
                    "note": "Frozen prediction remains PIT-valid; current repository no longer exactly reconstructs its historical input set.",
                },
            )

        if frozen_at >= kickoff:
            _finding(
                findings, kind="LATE_PREDICTION", match_id=match_id, severity="BLOCK", invalidates=True,
                detail={"frozen_at": frozen_at.isoformat(), "kickoff_at": kickoff.isoformat()},
            )

    stats["findings"] = len(findings)
    stats["invalidating_findings"] = sum(1 for item in findings if item["invalidates_forward_sample"])
    return findings, dict(sorted(stats.items()))


def _audit_settlements(now: datetime, ledger: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    stats: Counter = Counter()
    predictions = _prediction_events(ledger)
    settlements = _settlement_events(ledger)
    cache: dict[str, list[Any]] = {}

    for match_id, prediction_event in predictions.items():
        identity = prediction_event["payload"]["fixture_identity"]
        kickoff = parse_iso_datetime(identity["kickoff_at"], "kickoff_at")
        settlement = settlements.get(match_id)
        if settlement is None:
            if now >= kickoff + RESULT_GRACE:
                _finding(
                    findings, kind="OPEN_RESULT_DELAY", match_id=match_id, severity="WARN", invalidates=False,
                    detail={
                        "kickoff_at": kickoff.isoformat(),
                        "hours_since_kickoff": (now - kickoff).total_seconds() / 3600.0,
                    },
                )
            continue

        stats["settlements_audited"] += 1
        cid = identity["competition_id"]
        if cid not in cache:
            try:
                cache[cid] = read_processed_matches(cid)
            except Exception:
                cache[cid] = []
        matches = [
            match for match in cache[cid]
            if match.date.date() == kickoff.date()
            and normalize_team_token(match.home_team) == normalize_team_token(identity["home_team"])
            and normalize_team_token(match.away_team) == normalize_team_token(identity["away_team"])
        ]
        if not matches:
            _finding(
                findings, kind="SETTLED_RESULT_NOT_REPRODUCIBLE", match_id=match_id, severity="BLOCK",
                detail={"competition_id": cid, "kickoff_date": kickoff.date().isoformat()},
            )
            continue
        if len(matches) != 1:
            _finding(
                findings, kind="SETTLED_RESULT_AMBIGUOUS", match_id=match_id, severity="BLOCK",
                detail={"candidate_count": len(matches), "competition_id": cid},
            )
            continue
        match = matches[0]
        recorded = settlement["payload"]["result"]
        current = {"home_goals_90": int(match.home_goals), "away_goals_90": int(match.away_goals)}
        expected = {"home_goals_90": int(recorded["home_goals_90"]), "away_goals_90": int(recorded["away_goals_90"])}
        if current != expected:
            _finding(
                findings, kind="SETTLED_RESULT_CONFLICT", match_id=match_id, severity="BLOCK",
                detail={"recorded": expected, "current_processed": current, "source_path": match.source_path},
            )

    stats["findings"] = len(findings)
    stats["invalidating_findings"] = sum(1 for item in findings if item["invalidates_forward_sample"])
    return findings, dict(sorted(stats.items()))


def _persistent_invalidated_ids(audit_ledger: dict[str, Any]) -> list[str]:
    return sorted({
        str(event.get("match_id"))
        for event in audit_ledger.get("events", [])
        if event.get("match_id") and bool((event.get("payload") or {}).get("invalidates_forward_sample"))
    })


def _finding_counts(audit_ledger: dict[str, Any]) -> dict[str, int]:
    counter = Counter(str((event.get("payload") or {}).get("kind") or "UNKNOWN") for event in audit_ledger.get("events", []))
    return dict(sorted(counter.items()))


def _run_self_test() -> int:
    with tempfile.TemporaryDirectory():
        ledger = {"schema_version": AUDIT_LEDGER_SCHEMA, "events": []}
        now = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
        appended = _append_finding(
            ledger, now=now, kind="TEST", severity="WARN", match_id="match_test",
            detail={"x": 1}, invalidates_forward_sample=False,
        )
        if not appended or _audit_audit_chain(ledger)["status"] != "PASS":
            raise PlatformError("valid audit chain self-test failed")
        if _append_finding(
            ledger, now=now, kind="TEST", severity="WARN", match_id="match_test",
            detail={"x": 1}, invalidates_forward_sample=False,
        ):
            raise PlatformError("finding idempotency self-test failed")
        tampered = json.loads(json.dumps(ledger))
        tampered["events"][0]["payload"]["detail"]["x"] = 2
        if _audit_audit_chain(tampered)["status"] != "FAIL":
            raise PlatformError("audit tamper self-test failed")
    print(json.dumps({"status": "PASS", "self_test": "V6.1.3 append-only audit chain"}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return _run_self_test()

    now = _utc_now()
    freeze = load_json(FREEZE)
    if freeze.get("status") != "PASS":
        raise PlatformError("V6.1.0 freeze receipt must be PASS")
    prediction_ledger = load_json(PREDICTION_LEDGER)
    prediction_chain = ledgerlib._audit_chain(prediction_ledger)
    frozen_source_integrity = ledgerlib._source_integrity(freeze)
    baseline, baseline_created = _load_or_create_baseline(now, freeze)
    runtime = _runtime_drift(baseline)

    audit_ledger = _load_audit_ledger()
    audit_before = _audit_audit_chain(audit_ledger)
    if audit_before["status"] != "PASS":
        payload = {
            "schema_version": STATUS_SCHEMA,
            "generated_at_utc": now.isoformat(),
            "status": "FAIL_AUDIT_LEDGER_TAMPERED",
            "baseline_created_this_run": baseline_created,
            "prediction_ledger_chain": prediction_chain,
            "frozen_source_integrity": frozen_source_integrity,
            "runtime_dependency_integrity": runtime,
            "audit_ledger": audit_before,
            "invalidated_match_ids": _persistent_invalidated_ids(audit_ledger),
            "governance": {"automatic_promotion": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fixtures = _fixture_inbox_map()
    evidence = _load_evidence()
    prediction_findings, prediction_stats = _audit_predictions(now, freeze, prediction_ledger, fixtures, evidence)
    settlement_findings, settlement_stats = _audit_settlements(now, prediction_ledger)
    current_findings = prediction_findings + settlement_findings

    new_events = 0
    for finding in current_findings:
        if _append_finding(
            audit_ledger, now=now, kind=finding["kind"], severity=finding["severity"],
            match_id=finding["match_id"], detail=finding["detail"],
            invalidates_forward_sample=finding["invalidates_forward_sample"],
        ):
            new_events += 1

    atomic_write_json(AUDIT_LEDGER, audit_ledger)
    audit_after = _audit_audit_chain(audit_ledger)
    invalidated = _persistent_invalidated_ids(audit_ledger)

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

    payload = {
        "schema_version": STATUS_SCHEMA,
        "generated_at_utc": now.isoformat(),
        "status": status,
        "baseline_created_this_run": baseline_created,
        "baseline_path": str(BASELINE.relative_to(ROOT)),
        "baseline_sha256": _sha256_file(BASELINE),
        "prediction_ledger_chain": prediction_chain,
        "frozen_source_integrity": frozen_source_integrity,
        "runtime_dependency_integrity": runtime,
        "prediction_audit": prediction_stats,
        "settlement_audit": settlement_stats,
        "prospective_evidence_files_audited": len(evidence),
        "current_findings": current_findings,
        "new_audit_events": new_events,
        "audit_ledger": audit_after,
        "persistent_finding_counts": _finding_counts(audit_ledger),
        "invalidated_match_ids": invalidated,
        "invalidated_match_count": len(invalidated),
        "evaluation_blocked": status.startswith("FAIL_"),
        "governance": {
            "prediction_ledger_mutation": False,
            "audit_findings_append_only": True,
            "critical_runtime_drift_blocks_evaluation": True,
            "hard_findings_excluded_from_forward_metrics": True,
            "historical_input_reconstruction_drift_is_warning_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not status.startswith("FAIL_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
