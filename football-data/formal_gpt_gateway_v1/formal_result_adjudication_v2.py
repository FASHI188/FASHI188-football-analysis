#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import runtime as rt
import formal_result_adjudication_v1 as legacy

SCHEMA = "football3-formal-result-adjudication-v2"
_INSTALLED = False
_ORIGINAL_LOAD = rt.load_xg_labels
_ORIGINAL_HISTORY_DELTA = rt.history_delta_events
_ORIGINAL_VALIDATE_DELTA = rt._validate_delta_records
_ORIGINAL_APPLY_EVENTS = rt._apply_events


def _validate_formal_identity(entry: dict[str, Any], fixture: rt.HistoryFixture) -> None:
    expected = (
        str(entry.get("competition_id")),
        str(entry.get("season")),
        str(entry.get("kickoff_date")),
        str(entry.get("home_team")),
        str(entry.get("away_team")),
    )
    actual = (
        fixture.competition_id,
        fixture.season,
        fixture.kickoff.date().isoformat(),
        fixture.home_team_name,
        fixture.away_team_name,
    )
    if actual != expected:
        raise rt.RuntimeGateError(f"adjudication identity mismatch: {fixture.fixture_id}")
    settlement = entry.get("formal_settlement_result") or {}
    expected_score = (int(settlement.get("home_goals", -1)), int(settlement.get("away_goals", -1)))
    if (fixture.home_goals, fixture.away_goals) != expected_score:
        raise rt.RuntimeGateError(f"authoritative settlement result drift: {fixture.fixture_id}")


def _validate_loaded_xg(
    entry: dict[str, Any],
    formal_fixture: rt.HistoryFixture,
    label: rt.XGLabel,
    confirmation_dir: Path,
) -> dict[str, Any]:
    on_field = entry.get("on_field_xg_result") or {}
    expected_xg_score = (int(on_field.get("home_goals", -1)), int(on_field.get("away_goals", -1)))
    actual_xg_score = (int(label.label.home_goals), int(label.label.away_goals))
    if actual_xg_score != expected_xg_score:
        raise rt.RuntimeGateError(f"adjudicated on-field xG result drift: {formal_fixture.fixture_id}")
    if actual_xg_score == (formal_fixture.home_goals, formal_fixture.away_goals):
        raise rt.RuntimeGateError(f"adjudication no longer represents distinct result semantics: {formal_fixture.fixture_id}")

    xgs = entry.get("xg_source") or {}
    expected_sid = f"understat:{str(xgs.get('fixture_id'))}"
    if label.source_fixture_id != expected_sid:
        raise rt.RuntimeGateError(f"adjudicated xG source fixture mismatch: {formal_fixture.fixture_id}")

    identity_path = confirmation_dir / "confirmation_identity.jsonl"
    vault_path = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    identity_sha = rt._sha_file(identity_path)
    vault_sha = rt._sha_file(vault_path)
    if identity_sha != xgs.get("identity_sha256") or vault_sha != xgs.get("vault_sha256"):
        raise rt.RuntimeGateError(f"adjudicated frozen xG source hash mismatch: {formal_fixture.fixture_id}")
    expected_combined = identity_sha + "+" + vault_sha
    if label.source_sha256 != expected_combined:
        raise rt.RuntimeGateError(f"adjudicated xG label provenance mismatch: {formal_fixture.fixture_id}")

    expected_raw_sha = str(xgs.get("raw_page_sha256") or "")
    raw_hits = [
        path for path in sorted((confirmation_dir / "raw_pages").glob("*.json"))
        if rt._sha_file(path) == expected_raw_sha
    ]
    if len(raw_hits) != 1:
        raise rt.RuntimeGateError(f"adjudicated raw xG page identity mismatch: {formal_fixture.fixture_id}")

    availability = entry.get("availability") or {}
    formal_at = rt._parse_dt(str(availability.get("formal_result_available_at") or ""), "formal result available at")
    if formal_at <= formal_fixture.kickoff or formal_at <= label.label.release_at:
        raise rt.RuntimeGateError(f"adjudication PIT availability invalid: {formal_fixture.fixture_id}")
    if availability.get("policy") != "NEXT_UTC_MIDNIGHT_AFTER_DATE_ONLY_OFFICIAL_DFB_RULING_PUBLICATION":
        raise rt.RuntimeGateError(f"adjudication availability policy drift: {formal_fixture.fixture_id}")

    return {
        "fixture_id": formal_fixture.fixture_id,
        "formal_result": [formal_fixture.home_goals, formal_fixture.away_goals],
        "on_field_xg_result": [label.label.home_goals, label.label.away_goals],
        "xg_release_at": label.label.release_at.isoformat(),
        "formal_result_available_at": formal_at.isoformat(),
        "xg_source_fixture_id": label.source_fixture_id,
        "identity_sha256": identity_sha,
        "vault_sha256": vault_sha,
        "raw_page_sha256": expected_raw_sha,
        "semantics": entry["semantics"],
        "transient_xg_semantic_validation_view": True,
        "formal_history_mutated": False,
        "sample_deleted": False,
        "source_score_rewritten": False,
        "conflict_gate_relaxed_without_authority": False,
    }


def _load_xg_labels(history: list[rt.HistoryFixture], understat_db: Path, confirmation_dir: Path):
    _, adjudications = legacy._manifest()
    formal_by_id = {fixture.fixture_id: fixture for fixture in history}
    if len(formal_by_id) != len(history):
        raise rt.RuntimeGateError("duplicate formal fixture id before adjudication")

    # Only the score fields in a transient validation view are replaced, and only
    # for an explicitly authorized semantic split. The original history list and
    # immutable repository/source files remain untouched. The original runtime
    # loader still performs every identity join, source hash check, row count gate
    # and every other result-conflict gate.
    validation_history: list[rt.HistoryFixture] = []
    for fixture in history:
        entry = adjudications.get(fixture.fixture_id)
        if entry is None:
            validation_history.append(fixture)
            continue
        _validate_formal_identity(entry, fixture)
        on_field = entry.get("on_field_xg_result") or {}
        validation_history.append(replace(
            fixture,
            home_goals=int(on_field.get("home_goals", -1)),
            away_goals=int(on_field.get("away_goals", -1)),
        ))

    labels, source = _ORIGINAL_LOAD(validation_history, understat_db, confirmation_dir)
    if len(labels) != rt.EXPECTED_XG_JOIN_N:
        raise rt.RuntimeGateError(f"original xG loader joined {len(labels)} != {rt.EXPECTED_XG_JOIN_N}")

    applied: list[dict[str, Any]] = []
    for fixture_id, entry in adjudications.items():
        formal_fixture = formal_by_id.get(fixture_id)
        label = labels.get(fixture_id)
        if formal_fixture is None or label is None:
            raise rt.RuntimeGateError(f"adjudicated fixture missing after original xG join: {fixture_id}")
        applied.append(_validate_loaded_xg(entry, formal_fixture, label, confirmation_dir))

    source = dict(source)
    source["result_adjudication"] = {
        "schema_version": SCHEMA,
        "manifest_sha256": legacy.MANIFEST_CANONICAL_SHA256,
        "entry_n": len(adjudications),
        "applied": applied,
        "identity_join_implementation": "ORIGINAL_RUNTIME_LOAD_XG_LABELS",
        "transient_xg_semantic_validation_view": True,
        "formal_history_mutated": False,
        "sample_deleted": False,
        "source_score_rewritten": False,
        "conflict_gate_relaxed_without_authority": False,
    }
    return labels, source


def adjudication_entries() -> dict[str, dict[str, Any]]:
    return legacy.adjudication_entries()


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {
            "schema_version": SCHEMA,
            "installed": True,
            "idempotent": True,
            "manifest_sha256": legacy.MANIFEST_CANONICAL_SHA256,
        }
    legacy._manifest()
    rt.load_xg_labels = _load_xg_labels
    # Keep the already-reviewed split event semantics from v1, but do not use its
    # duplicated loader. All identity joining is delegated to the original runtime.
    rt.history_delta_events = legacy._history_delta_events
    rt._validate_delta_records = legacy._validate_delta_records
    rt._apply_events = legacy._apply_events
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "idempotent": True,
        "manifest_sha256": legacy.MANIFEST_CANONICAL_SHA256,
        "entry_n": len(adjudication_entries()),
        "identity_join_implementation": "ORIGINAL_RUNTIME_LOAD_XG_LABELS",
        "semantics": "SPLIT_AUTHORITATIVE_SETTLEMENT_V1_FROM_ON_FIELD_XG",
        "pit_policy": "CONSERVATIVE_DATE_ONLY_AUTHORITY_BOUNDARY",
        "transient_xg_semantic_validation_view": True,
        "formal_history_mutated": False,
        "conflict_gate_relaxed_without_authority": False,
        "sample_deleted": False,
        "source_score_rewritten": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
