#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import durable_state_contract_v1 as contract
import formal_durable_state_governance_v1 as governance
import runtime as rt

UTC = timezone.utc


def dt(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def eligible(artifact_id: int, state_cutoff: str, created_at: str, comp: str = "ESP_LaLiga"):
    return {
        "artifact_id": artifact_id,
        "artifact_name": f"formal-gpt-runner-state-{artifact_id}",
        "artifact_created_at": created_at,
        "artifact_role_ok": True,
        "verified": True,
        "schema_ok": True,
        "runtime_ok": True,
        "model_current_ok": True,
        "competition_scope_ok": True,
        "pit_ok": True,
        "competition_id": comp,
        "state_cutoff": state_cutoff,
    }


def betis_four_cutoff_regression():
    # Identity-only regression fixture. No result, label, score, xG result or post-match field exists here.
    fixture = {
        "competition_id": "ESP_LaLiga",
        "home_team_name": "Real Betis",
        "away_team_name": "Real Madrid",
        "result_fields_present": False,
    }
    candidates = [
        eligible(828, "2026-09-04T08:28:00Z", "2026-09-04T08:40:00Z"),
        eligible(1319, "2026-09-04T13:19:44Z", "2026-09-04T13:30:00Z"),
        # Newer upload carrying an older state must never win.
        eligible(9999, "2026-09-04T08:28:00Z", "2026-09-04T18:30:00Z"),
    ]
    expected = {
        "2026-09-04T08:28:00Z": "2026-09-04T08:28:00+00:00",
        "2026-09-04T13:19:44Z": "2026-09-04T13:19:44+00:00",
        "2026-09-04T16:00:00Z": "2026-09-04T13:19:44+00:00",
        "2026-09-04T18:00:00Z": "2026-09-04T13:19:44+00:00",
    }
    rows = []
    for target, state in expected.items():
        selected, _ = contract.choose_candidate(candidates, dt(target), fixture["competition_id"])
        assert selected is not None
        assert dt(selected["state_cutoff"]).isoformat() == state
        rows.append({"target_cutoff": target, "selected_state_cutoff": state, "artifact_id": selected["artifact_id"]})
    return {"fixture": fixture, "rows": rows, "passed": True}


def selection_order_and_negative_gates():
    target = dt("2026-09-04T18:00:00Z")
    comp = "ESP_LaLiga"
    rows = [
        eligible(1, "2026-09-04T13:00:00Z", "2026-09-04T19:00:00Z"),
        eligible(2, "2026-09-04T14:00:00Z", "2026-09-04T14:01:00Z"),
        eligible(3, "2026-09-04T14:00:00Z", "2026-09-04T14:02:00Z"),
    ]
    selected, _ = contract.choose_candidate(rows, target, comp)
    assert selected and selected["artifact_id"] == 3, selected

    negatives = []
    cases = {
        "future_state": {**eligible(10, "2026-09-04T18:00:01Z", "2026-09-04T17:00:00Z")},
        "wrong_competition": {**eligible(11, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z", "ENG_PremierLeague")},
        "wrong_role": {**eligible(12, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "artifact_role_ok": False},
        "unverified": {**eligible(13, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "verified": False},
        "bad_schema": {**eligible(14, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "schema_ok": False},
        "runtime_drift": {**eligible(15, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "runtime_ok": False},
        "model_current_drift": {**eligible(16, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "model_current_ok": False},
        "scope_mismatch": {**eligible(17, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "competition_scope_ok": False},
        "pit_violation": {**eligible(18, "2026-09-04T14:00:00Z", "2026-09-04T14:00:01Z"), "pit_ok": False},
    }
    for name, row in cases.items():
        sel, evaluated = contract.choose_candidate([row], target, comp)
        assert sel is None
        assert evaluated[0]["rejection_reasons"]
        negatives.append({"case": name, "reasons": evaluated[0]["rejection_reasons"]})
    return {"created_at_tiebreak_same_cutoff": True, "negative_cases": negatives, "passed": True}


def transition_and_cache_contracts():
    delta: list[dict] = []
    kwargs = dict(
        base_state_sha="a" * 64,
        base_cutoff="2026-09-04T13:19:44Z",
        delta_from="2026-09-04T13:19:44Z",
        delta_to="2026-09-04T16:00:00Z",
        delta=delta,
        target_cutoff="2026-09-04T16:00:00Z",
        target_state_sha="b" * 64,
        artifact_created_at="2026-09-04T16:10:00Z",
        max_source_observed_at_value="2026-09-04T13:19:44Z",
        route="FUSION_V2_ACTIVE",
    )
    a = contract.transition_receipt(**kwargs)
    b = contract.transition_receipt(**kwargs)
    assert a == b
    assert a["transition_status"] == contract.NO_OP_DELTA
    assert a["delta_n"] == 0
    assert a["artifact_created_at"] != a["base_cutoff"]
    assert a["base_cutoff"] != a["target_cutoff"]

    fixture = {
        "fixture_id": "generic-fixture",
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "home_team_id": "h",
        "away_team_id": "a",
        "kickoff": "2026-09-04T19:00:00+00:00",
    }
    cache1 = contract.cache_key(
        fixture_identity=fixture, cutoff=a["target_cutoff"], base_state_sha=a["base_state_sha"],
        target_state_sha=a["target_state_sha"], delta_sha=a["delta_sha"], model_head=rt.FORMAL_HEAD,
        route="FUSION_V2_ACTIVE",
    )
    cache2 = contract.cache_key(
        fixture_identity=fixture, cutoff=a["target_cutoff"], base_state_sha=a["base_state_sha"],
        target_state_sha=a["target_state_sha"], delta_sha=a["delta_sha"], model_head=rt.FORMAL_HEAD,
        route="FUSION_V2_ACTIVE",
    )
    assert cache1 == cache2
    assert all(k in cache1 for k in (
        "fixture_identity", "cutoff", "base_state_sha", "target_state_sha", "delta_sha",
        "model_head", "runtime_contract_sha256", "route", "cache_key_sha256",
    ))
    return {"no_op_transition_sha256": a["transition_receipt_sha256"], "cache_key_sha256": cache1["cache_key_sha256"], "passed": True}


def fallback_gate_regression():
    governance._fallback_gate({"fallback_exact_v1": False})
    governance._fallback_gate({"fallback_exact_v1": True, "history_counts": {"xg_effective_evidence": [2.99, 5.0]}})
    rejected = []
    for receipt in (
        {"fallback_exact_v1": True, "history_counts": {"xg_effective_evidence": [3.0, 5.0]}},
        {"fallback_exact_v1": True, "history_counts": {}},
    ):
        try:
            governance._fallback_gate(receipt)
        except rt.RuntimeGateError as exc:
            rejected.append(str(exc))
        else:
            raise AssertionError("illegal fallback not rejected")
    return {"legal_only_below_effective_evidence_threshold": True, "rejected": rejected, "passed": True}


def deep_fail_closed_regression():
    rejected = {}
    with tempfile.TemporaryDirectory(prefix="football3-durable-negative-") as td:
        root = Path(td)

        # Corrupt manifest must never be treated as a usable state.
        bundle = root / "bundle"
        state = rt.formal_v2.new_candidate_state()
        rt.seal_bundle(state, bundle, {}, {}, "2024-01-01T00:00:00+00:00", "FULL_REBUILD_PATH")
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["state_bundle_sha256"] = "0" * 64
        manifest_path.write_bytes(rt._canon_bytes(manifest))
        try:
            rt.validate_bundle(bundle)
        except rt.RuntimeGateError as exc:
            rejected["corrupt_manifest"] = str(exc)
        else:
            raise AssertionError("corrupt manifest not rejected")

        # Discontinuous delta interval must fail before any model call.
        fixture = {
            "fixture_id": "generic-discontinuous",
            "competition_id": "ESP_LaLiga",
            "season": "2026/27",
            "kickoff": "2026-09-04T19:00:00+00:00",
            "home_team_id": "h", "away_team_id": "a",
            "home_team_name": "H", "away_team_name": "A",
        }
        empty = []
        inp = {
            "schema_version": rt.INPUT_SCHEMA,
            "fixture": fixture,
            "cutoff": "2026-09-04T16:00:00+00:00",
            "delta_coverage": {
                "schema_version": rt.DELTA_SCHEMA,
                "status": "COMPLETE", "verification": "VERIFIED_COMPLETE",
                "v1_status": "COMPLETE", "xg_status": "COMPLETE",
                "from": "2026-09-04T14:00:00+00:00", "to": "2026-09-04T16:00:00+00:00",
                "records_sha256": rt._sha_bytes(rt._canon_bytes(empty)),
                "source_set_sha256": "verified-source-set",
            },
            "model_delta": empty,
        }
        try:
            rt.validate_runtime_input(inp, fixture, dt("2026-09-04T13:00:00Z"), dt("2026-09-04T16:00:00Z"))
        except rt.RuntimeGateError as exc:
            rejected["discontinuous_delta"] = str(exc)
        else:
            raise AssertionError("discontinuous delta not rejected")

        # A present-but-mismatched cache binding is stale and must fail closed. A freshly
        # selected durable artifact may create a missing binding, but it may never overwrite stale identity.
        state_root = root / "state"
        out = root / "out"
        state_root.mkdir(parents=True)
        fake_loaded = {
            "meta": {"historical_cutoff": "2026-09-04T13:19:44+00:00"},
            "manifest": {"state_sha256": "s" * 64, "state_bundle_sha256": "b" * 64},
            "state": object(),
        }
        selection = {
            "schema_version": contract.SELECTION_SCHEMA,
            "status": "SELECTED",
            "target_cutoff": "2026-09-04T16:00:00+00:00",
            "competition_id": "ESP_LaLiga",
            "selected": {
                "state_sha256": "s" * 64,
                "state_bundle_sha256": "b" * 64,
                "formal_head": rt.FORMAL_HEAD,
                "current_sha256": rt.CURRENT_SHA256,
                "runtime_contract_sha256": contract.runtime_contract_payload()["runtime_contract_sha256"],
                "artifact_created_at": "2026-09-04T16:10:00+00:00",
            },
        }
        (state_root / "durable_state_selection_v1.json").write_bytes(contract.canon(selection))
        (state_root / "fast_cache_binding_v1.json").write_bytes(contract.canon({"binding_sha256": "stale"}))
        expected = {"binding_sha256": "expected"}
        fake_fixture = {
            "fixture_id": "generic", "competition_id": "ESP_LaLiga", "season": "2026/27",
            "home_team_id": "h", "away_team_id": "a", "home_team_name": "H", "away_team_name": "A",
            "kickoff": "2026-09-04T19:00:00+00:00",
        }
        originals = (
            governance.rt.validate_bundle,
            governance.contract.max_source_observed_at,
            governance.identity_bridge.resolve_fixture,
            governance.guard._binding_payload,
        )
        try:
            governance.rt.validate_bundle = lambda _: fake_loaded
            governance.contract.max_source_observed_at = lambda _: "2026-09-04T13:19:44+00:00"
            governance.identity_bridge.resolve_fixture = lambda *args, **kwargs: (fake_fixture, {"home": {}, "away": {}})
            governance.guard._binding_payload = lambda *args, **kwargs: expected
            m = {
                "competition_id": "ESP_LaLiga", "season": "2026/27",
                "home_team_name": "H", "away_team_name": "A",
                "kickoff": "2026-09-04T19:00:00+00:00", "cutoff": "2026-09-04T16:00:00+00:00",
            }
            try:
                governance._selection_preflight(state_root, out, root, m)
            except rt.RuntimeGateError as exc:
                rejected["stale_cache"] = str(exc)
            else:
                raise AssertionError("stale cache binding not rejected")
        finally:
            (
                governance.rt.validate_bundle,
                governance.contract.max_source_observed_at,
                governance.identity_bridge.resolve_fixture,
                governance.guard._binding_payload,
            ) = originals

    required = {"corrupt_manifest", "discontinuous_delta", "stale_cache"}
    assert set(rejected) == required, rejected
    return {"rejected": rejected, "passed": True}


def all_formal_scope_smoke():
    target = dt("2026-09-04T18:00:00Z")
    passed = []
    for i, comp in enumerate(rt.FORMAL_SCOPE, 1):
        row = eligible(i, "2026-09-04T13:19:44Z", "2026-09-04T14:00:00Z", comp)
        selected, evaluated = contract.choose_candidate([row], target, comp)
        assert selected is not None and not evaluated[0]["rejection_reasons"]
        passed.append(comp)
    assert set(passed) == set(rt.FORMAL_SCOPE)
    return {"competitions": passed, "n": len(passed), "passed": True}


def no_target_specific_implementation():
    root = Path(__file__).parent
    implementation = [
        root / "durable_state_contract_v1.py",
        root / "durable_state_selector_v1.py",
        root / "formal_durable_state_governance_v1.py",
    ]
    forbidden = ("real betis", "betis", "real madrid")
    hits = {}
    for path in implementation:
        text = path.read_text(encoding="utf-8").lower()
        found = [token for token in forbidden if token in text]
        if found:
            hits[path.name] = found
    assert not hits, hits
    return {"implementation_files": [p.name for p in implementation], "forbidden_hits": hits, "passed": True}


def main() -> int:
    receipt = {
        "schema_version": "football3-durable-state-governance-regression-v1",
        "betis_four_cutoff_generic_regression": betis_four_cutoff_regression(),
        "selection_and_fail_closed": selection_order_and_negative_gates(),
        "transition_and_cache": transition_and_cache_contracts(),
        "fallback_gate": fallback_gate_regression(),
        "deep_fail_closed": deep_fail_closed_regression(),
        "all_formal_scope_selector_smoke": all_formal_scope_smoke(),
        "no_target_specific_logic": no_target_specific_implementation(),
        "formal_head": rt.FORMAL_HEAD,
        "current_sha256": rt.CURRENT_SHA256,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    receipt["passed"] = all(v.get("passed", False) for v in receipt.values() if type(v) is dict)
    receipt["runtime_contract"] = contract.runtime_contract_payload()
    receipt["receipt_sha256"] = contract.sha(receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
