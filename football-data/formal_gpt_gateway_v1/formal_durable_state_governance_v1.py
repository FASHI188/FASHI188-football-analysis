#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import durable_state_contract_v1 as contract
import formal_state_integrity_guard_v1 as guard
import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

SCHEMA = "football3-formal-durable-state-governance-v1"
MIN_XG_EVIDENCE = 3.0


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contract.canon(obj))


def _read_obj(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return obj if type(obj) is dict else None


def _gap(out: Path, reason: str, *, fixture: dict[str, Any] | None = None, cutoff: str | None = None) -> dict[str, Any]:
    gap = {
        "schema_version": SCHEMA,
        "status": "DATA_STATE_ANOMALY",
        "fallback_class": "DATA_STATE_ANOMALY",
        "fallback_reason": reason,
        "fixture": fixture,
        "cutoff": cutoff,
        "prediction_sha": None,
        "receipt_sha": None,
        "manual_or_auxiliary_fallback_used": False,
        "fail_closed": True,
    }
    _write(out / "formal_gap.json", gap)
    _write(out / "durable_state_governance.json", gap)
    return gap


def _selection_preflight(state_root: Path, out: Path, repo_root: Path, m: dict[str, Any]):
    comp = str(m.get("competition_id") or "")
    season = str(m.get("season") or "")
    home = str(m.get("home_team_name") or "").strip()
    away = str(m.get("away_team_name") or "").strip()
    kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "kickoff")
    cutoff = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
    if comp not in rt.FORMAL_SCOPE:
        raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")

    loaded = rt.validate_bundle(state_root / "bundle")
    selection = _read_obj(state_root / "durable_state_selection_v1.json")
    selector_required = os.environ.get("FOOTBALL3_DURABLE_SELECTOR_REQUIRED", "0") == "1"
    if selector_required and (selection is None or selection.get("status") != "SELECTED"):
        raise rt.RuntimeGateError("DURABLE_SELECTOR_MISSING_OR_NOT_SELECTED")
    if selection is not None:
        selected = selection.get("selected") or {}
        if selection.get("status") != "SELECTED":
            raise rt.RuntimeGateError("DURABLE_SELECTOR_NOT_SELECTED")
        if rt._parse_dt(str(selection.get("target_cutoff")), "selector target cutoff") != cutoff:
            raise rt.RuntimeGateError("DURABLE_SELECTOR_TARGET_CUTOFF_MISMATCH")
        if selection.get("competition_id") != comp:
            raise rt.RuntimeGateError("DURABLE_SELECTOR_COMPETITION_MISMATCH")
        if selected.get("state_sha256") != loaded["manifest"]["state_sha256"]:
            raise rt.RuntimeGateError("DURABLE_SELECTOR_STATE_SHA_MISMATCH")
        if selected.get("state_bundle_sha256") != loaded["manifest"]["state_bundle_sha256"]:
            raise rt.RuntimeGateError("DURABLE_SELECTOR_BUNDLE_SHA_MISMATCH")
        if selected.get("formal_head") != rt.FORMAL_HEAD or selected.get("current_sha256") != rt.CURRENT_SHA256:
            raise rt.RuntimeGateError("DURABLE_SELECTOR_MODEL_CURRENT_MISMATCH")
        if selected.get("runtime_contract_sha256") != contract.runtime_contract_payload()["runtime_contract_sha256"]:
            raise rt.RuntimeGateError("DURABLE_SELECTOR_RUNTIME_CONTRACT_MISMATCH")

    base_cutoff = rt._parse_dt(str(loaded["meta"]["historical_cutoff"]), "base state cutoff")
    max_source = contract.max_source_observed_at(loaded)
    if base_cutoff > cutoff:
        raise rt.RuntimeGateError("DURABLE_SELECTOR_FUTURE_STATE")
    if rt._parse_dt(max_source, "max source observed at") > base_cutoff:
        raise rt.RuntimeGateError("DURABLE_SELECTOR_PIT_VIOLATION")

    fixture, ident = identity_bridge.resolve_fixture(repo_root, loaded["state"], comp, season, home, away, kickoff)
    expected_binding = guard._binding_payload(loaded, comp, season, cutoff, ident)
    binding_path = state_root / "fast_cache_binding_v1.json"
    actual = _read_obj(binding_path)
    if actual is not None and actual != expected_binding:
        raise rt.RuntimeGateError("STALE_CACHE_BINDING_MISMATCH")
    if actual is None:
        if selection is None or selection.get("status") != "SELECTED":
            raise rt.RuntimeGateError("UNBOUND_CACHE_WITHOUT_VERIFIED_DURABLE_SELECTION")
        guard._write_json(binding_path, expected_binding)
        guard._write_json(out / "fast_cache_binding_v1.json", expected_binding)

    artifact_created_at = None
    if selection is not None:
        artifact_created_at = (selection.get("selected") or {}).get("artifact_created_at")
    return {
        "loaded": loaded,
        "selection": selection,
        "fixture": fixture,
        "identity": ident,
        "cutoff": cutoff,
        "base_cutoff": base_cutoff,
        "base_state_sha": loaded["manifest"]["state_sha256"],
        "artifact_created_at": artifact_created_at,
        "max_source_observed_at": max_source,
    }


def _delta_from_output(out: Path, base_cutoff, target_cutoff):
    inp = _read_obj(out / "runtime_input.json")
    if inp is None:
        raise rt.RuntimeGateError("runtime input missing after prediction")
    delta = inp.get("model_delta")
    coverage = inp.get("delta_coverage")
    if type(delta) is not list or type(coverage) is not dict:
        raise rt.RuntimeGateError("runtime delta receipt missing")
    if coverage.get("status") != "COMPLETE" or coverage.get("verification") != "VERIFIED_COMPLETE":
        raise rt.RuntimeGateError("runtime delta not verified complete")
    dfrom = rt._parse_dt(str(coverage.get("from")), "delta from")
    dto = rt._parse_dt(str(coverage.get("to")), "delta to")
    if dto != target_cutoff:
        raise rt.RuntimeGateError("delta target cutoff mismatch")
    if dfrom not in (base_cutoff, target_cutoff):
        raise rt.RuntimeGateError("discontinuous delta interval")
    if coverage.get("records_sha256") != rt._sha_bytes(rt._canon_bytes(delta)):
        raise rt.RuntimeGateError("delta records SHA mismatch after gateway")
    return inp, coverage, delta, dfrom, dto


def _fallback_gate(receipt: dict[str, Any]) -> None:
    if not bool(receipt.get("fallback_exact_v1")):
        return
    counts = receipt.get("history_counts") or {}
    evidence = counts.get("xg_effective_evidence")
    if type(evidence) is not list or not evidence:
        raise rt.RuntimeGateError("FALLBACK_WITHOUT_FORMAL_EFFECTIVE_EVIDENCE")
    values = [float(x) for x in evidence]
    if min(values) >= MIN_XG_EVIDENCE:
        raise rt.RuntimeGateError("FALLBACK_DESPITE_SUFFICIENT_EFFECTIVE_EVIDENCE")


def install(gateway_module) -> dict[str, Any]:
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        m = req.get("match")
        if type(m) is not dict:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        try:
            pre = _selection_preflight(state_root, out, repo_root, m)
        except rt.RuntimeGateError as exc:
            return _gap(out, str(exc), cutoff=str(m.get("cutoff") or ""))

        result = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        if result.get("status") != "PASS":
            return result
        try:
            target_cutoff = pre["cutoff"]
            first_input, coverage, delta, dfrom, dto = _delta_from_output(out, pre["base_cutoff"], target_cutoff)
            target_loaded = rt.validate_bundle(state_root / "bundle")
            target_state_cutoff = rt._parse_dt(str(target_loaded["meta"]["historical_cutoff"]), "target state cutoff")
            if target_state_cutoff != target_cutoff:
                raise rt.RuntimeGateError("TARGET_STATE_CUTOFF_MISMATCH")
            if rt._parse_dt(contract.max_source_observed_at(target_loaded), "target max source observed") > target_cutoff:
                raise rt.RuntimeGateError("TARGET_STATE_PIT_VIOLATION")

            transition = contract.transition_receipt(
                base_state_sha=pre["base_state_sha"],
                base_cutoff=pre["base_cutoff"].isoformat(),
                delta_from=dfrom.isoformat(),
                delta_to=dto.isoformat(),
                delta=delta,
                target_cutoff=target_cutoff.isoformat(),
                target_state_sha=target_loaded["manifest"]["state_sha256"],
                artifact_created_at=pre["artifact_created_at"],
                max_source_observed_at_value=contract.max_source_observed_at(target_loaded),
                route=str(result.get("model_route") or result.get("gateway_route") or "UNKNOWN"),
            )

            # Canonicalize a first-time empty transition into the same exact-cutoff sealed
            # request used by every subsequent identical invocation. This makes the final
            # state/input/prediction SHA deterministic without re-fetching FULL history.
            if transition["transition_status"] == contract.NO_OP_DELTA and pre["base_cutoff"] < target_cutoff:
                rerun = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
                if rerun.get("status") != "PASS":
                    raise rt.RuntimeGateError("NO_OP_DELTA_CANONICAL_RERUN_FAILED")
                result = rerun
                _delta_from_output(out, target_cutoff, target_cutoff)

            receipt = _read_obj(out / "prediction_receipt.json")
            if receipt is None:
                raise rt.RuntimeGateError("prediction receipt missing after durable governance")
            _fallback_gate(receipt)
            final_loaded = rt.validate_bundle(state_root / "bundle")
            cache = contract.cache_key(
                fixture_identity={
                    "fixture_id": pre["fixture"]["fixture_id"],
                    "competition_id": pre["fixture"]["competition_id"],
                    "season": pre["fixture"]["season"],
                    "home_team_id": pre["fixture"]["home_team_id"],
                    "away_team_id": pre["fixture"]["away_team_id"],
                    "kickoff": pre["fixture"]["kickoff"],
                },
                cutoff=target_cutoff.isoformat(),
                base_state_sha=pre["base_state_sha"],
                target_state_sha=final_loaded["manifest"]["state_sha256"],
                delta_sha=transition["delta_sha"],
                model_head=rt.FORMAL_HEAD,
                route=str(result.get("model_route") or receipt.get("model_route") or "UNKNOWN"),
            )
            _write(out / "state_transition_receipt.json", transition)
            _write(state_root / "state_transition_receipt.json", transition)
            _write(out / "durable_cache_key_v1.json", cache)
            _write(state_root / "durable_cache_key_v1.json", cache)

            old_receipt_sha = receipt.pop("receipt_sha", None)
            receipt["artifact_created_at"] = pre["artifact_created_at"]
            receipt["base_state_cutoff"] = pre["base_cutoff"].isoformat()
            receipt["target_cutoff"] = target_cutoff.isoformat()
            receipt["max_source_observed_at"] = transition["max_source_observed_at"]
            receipt["state_transition_receipt_sha256"] = transition["transition_receipt_sha256"]
            receipt["durable_cache_key_sha256"] = cache["cache_key_sha256"]
            receipt["runtime_contract_sha256"] = contract.runtime_contract_payload()["runtime_contract_sha256"]
            receipt["receipt_sha_before_durable_governance"] = old_receipt_sha
            receipt["receipt_sha"] = rt._sha_bytes(rt._canon_bytes(receipt))
            _write(out / "prediction_receipt.json", receipt)

            result["receipt_sha"] = receipt["receipt_sha"]
            result["state_transition_receipt_sha256"] = transition["transition_receipt_sha256"]
            result["durable_cache_key_sha256"] = cache["cache_key_sha256"]
            result["transition_status"] = transition["transition_status"]
            result["artifact_created_at"] = pre["artifact_created_at"]
            result["base_state_cutoff"] = pre["base_cutoff"].isoformat()
            result["target_cutoff"] = target_cutoff.isoformat()
            result["max_source_observed_at"] = transition["max_source_observed_at"]
            _write(out / "durable_state_governance.json", {
                "schema_version": SCHEMA,
                "status": "PASS",
                "selection_sha256": (pre["selection"] or {}).get("selection_sha256"),
                "transition_receipt_sha256": transition["transition_receipt_sha256"],
                "cache_key_sha256": cache["cache_key_sha256"],
                "no_op_delta_full_rebuild_forbidden": True,
                "fallback_requires_effective_evidence_below_threshold": True,
                "model_parameters_or_weights_changed": False,
                "formal_current_or_production_pointer_changed": False,
            })
            return result
        except rt.RuntimeGateError as exc:
            try:
                if (out / "prediction_receipt.json").exists():
                    (out / "prediction_receipt.json").rename(out / "rejected_prediction_receipt.json")
            except OSError:
                pass
            return _gap(out, str(exc), fixture=pre.get("fixture"), cutoff=pre["cutoff"].isoformat())

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "selector": "cutoff-aware eligibility then max state_cutoff; created_at tie-break only",
        "separate_clocks": ["artifact_created_at", "base_state_cutoff", "target_cutoff", "max_source_observed_at"],
        "transition_receipt_schema": contract.TRANSITION_SCHEMA,
        "zero_delta_status": contract.NO_OP_DELTA,
        "cache_key_schema": contract.CACHE_KEY_SCHEMA,
        "fail_closed_anomalies": ["selector", "cache", "identity", "PIT", "manifest", "delta_continuity"],
        "fallback_only_on_effective_evidence_insufficiency": True,
        "target_specific_logic": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
