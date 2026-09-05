#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import formal_result_adjudication_v1 as adjudication

ADJUDICATION = adjudication.install()
import formal_runtime_exact_noop_v1 as exact_noop
EXACT_NOOP = exact_noop.install()

import runtime as rt
import test_runtime as tr

SCHEMA = "football3-fusion-v2-active-exact-head-acceptance-v1"
MIN_EVIDENCE = 3.0


def _clone(state):
    return rt.deserialize_state(rt.serialize_v1_state(state.base), rt.serialize_xg_state(state))


def _trigger_and_formal(state, target: rt.HistoryFixture) -> dict[str, Any]:
    trigger_state = _clone(state)
    xg_rows, _ = trigger_state.predict_batch([target.xg_fixture()], include_matrix=False)
    if len(xg_rows) != 1 or type(xg_rows[0].get("dynamic")) is not dict:
        raise AssertionError("real challenger dynamic metadata unavailable")
    dynamic = xg_rows[0]["dynamic"]
    evidence = [float(x) for x in (dynamic.get("evidence") or [])]
    if len(evidence) != 4:
        raise AssertionError(f"expected four effective evidence values, got {evidence}")

    formal_state = _clone(state)
    prediction = rt._prediction_from_state(formal_state, target.xg_fixture())
    audit = prediction["row"]["audit"]
    return {
        "evidence": evidence,
        "dynamic_fallback_exact_v1": bool(dynamic.get("fallback_exact_v1")),
        "formal": prediction,
        "model_route": str(audit["route"]),
        "fallback_exact_v1": bool(audit["fallback_exact_v1"]),
    }


def _select_real_active_target(history, labels) -> tuple[rt.HistoryFixture, Any, dict[str, Any], int]:
    # Identity/time-only candidate ordering; no target result or score field is read.
    candidates = [
        row for row in history
        if row.competition_id in set(rt.BIG5.values()) and row.season == "2024/25"
    ]
    candidates.sort(key=lambda row: (row.kickoff, row.competition_id, row.fixture_id), reverse=True)
    scanned = 0
    for target in candidates[:40]:
        cutoff = target.kickoff - timedelta(minutes=60)
        state, _ = rt.replay_history_state(history, labels, cutoff)
        audit = _trigger_and_formal(state, target)
        scanned += 1
        if (
            min(audit["evidence"]) >= MIN_EVIDENCE
            and audit["dynamic_fallback_exact_v1"] is False
            and audit["model_route"] == "FUSION_V2_ACTIVE"
            and audit["fallback_exact_v1"] is False
        ):
            return target, cutoff, audit, scanned
    raise AssertionError(f"no real FUSION_V2_ACTIVE target found in deterministic scan n={scanned}")


def _state_exact(a, b) -> bool:
    return rt.serialize_v1_state(a.base) == rt.serialize_v1_state(b.base) and rt.serialize_xg_state(a) == rt.serialize_xg_state(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--expected-runtime-head", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    under = Path(args.understat_db)
    conf = Path(args.confirmation_dir)
    exact_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    if exact_head != args.expected_runtime_head:
        raise AssertionError(f"runtime HEAD mismatch {exact_head} != {args.expected_runtime_head}")

    history, labels, source, identity = tr.production_corpus(repo_root, under, conf)
    target, cutoff, selected_audit, scanned = _select_real_active_target(history, labels)

    with tempfile.TemporaryDirectory(prefix="football3-fusion-active-") as td:
        root = Path(td)
        fixture_payload = tr.target_payload(target)

        # FULL: no usable cache, UNKNOWN delta, production frozen sources only.
        full_bundle = root / "full"
        full_input = tr.runtime_input(target, cutoff, cutoff, [], "UNKNOWN")
        full = rt.resolve_state_for_cutoff(
            full_bundle, full_input, fixture_payload, cutoff,
            repo_root=repo_root, understat_db=under, confirmation_dir=conf,
        )
        if full["path"] != "FULL_REBUILD_PATH":
            raise AssertionError(f"real FULL route mismatch: {full['path']}")
        full_audit = _trigger_and_formal(full["state"], target)

        # FAST: production state at a prior cutoff + verified production delta.
        lower = cutoff - timedelta(days=3)
        base_state, base_source, base_identity = rt.build_production_state_at_cutoff(repo_root, under, conf, lower)
        fast_bundle = root / "fast"
        rt.seal_bundle(base_state, fast_bundle, base_source, base_identity, lower.isoformat(), "FULL_REBUILD_PATH")
        delta = rt.history_delta_events(history, labels, lower, cutoff, target.fixture_id)
        if not delta:
            raise AssertionError("real FAST acceptance requires non-empty verified delta")
        fast_input = tr.runtime_input(target, lower, cutoff, delta, "COMPLETE")
        fast = rt.resolve_state_for_cutoff(
            fast_bundle, fast_input, fixture_payload, cutoff,
            repo_root=repo_root, understat_db=under, confirmation_dir=conf,
        )
        if fast["path"] != "FAST_PATH":
            raise AssertionError(f"real FAST route mismatch: {fast['path']} failure={fast.get('fast_failure')}")
        fast_audit = _trigger_and_formal(fast["state"], target)

        if not _state_exact(fast["state"], full["state"]):
            raise AssertionError("real FUSION FAST/FULL state mismatch")
        equivalence = rt._prediction_equivalent(fast_audit["formal"], full_audit["formal"])
        if equivalence["passed"] is not True:
            raise AssertionError(f"real FUSION FAST/FULL prediction mismatch: {equivalence}")

        for label, audit in (("selected", selected_audit), ("fast", fast_audit), ("full", full_audit)):
            if len(audit["evidence"]) != 4 or min(audit["evidence"]) < MIN_EVIDENCE:
                raise AssertionError(f"{label} effective evidence below formal threshold: {audit['evidence']}")
            if audit["model_route"] != "FUSION_V2_ACTIVE" or audit["fallback_exact_v1"] is not False:
                raise AssertionError(f"{label} not real active Fusion: {audit['model_route']} fallback={audit['fallback_exact_v1']}")

        # Exact-cutoff sealed request twice. No source parameters are supplied to predict_match;
        # any source fetch would therefore fail. Success proves the empty delta is source-silent.
        sealed_bundle = root / "sealed"
        exact_state, exact_source, exact_identity = rt.build_production_state_at_cutoff(repo_root, under, conf, cutoff)
        sealed_before = rt.seal_bundle(exact_state, sealed_bundle, exact_source, exact_identity, cutoff.isoformat(), "FULL_REBUILD_PATH")
        sealed_input = tr.runtime_input(target, cutoff, cutoff, [], "COMPLETE")
        input_path = root / "sealed_input.json"
        input_path.write_bytes(rt._canon_bytes(sealed_input))

        no_op = rt.resolve_state_for_cutoff(sealed_bundle, sealed_input, fixture_payload, cutoff)
        if no_op["path"] != "FAST_PATH" or no_op["delta_result"].get("transition_status") != "NO_OP_DELTA":
            raise AssertionError(f"exact empty delta not source-silent FAST NO_OP: {no_op}")
        if no_op["delta_result"].get("source_refetch_used") is not False or no_op["delta_result"].get("state_resealed") is not False:
            raise AssertionError("exact empty delta refetched source or resealed state")
        if rt.validate_bundle(sealed_bundle)["manifest"] != sealed_before:
            raise AssertionError("exact NO_OP mutated sealed state")

        first = rt.predict_match(
            target.competition_id, target.home_team_name, target.away_team_name,
            target.kickoff, cutoff, sealed_bundle, input_path,
        )
        after_first = rt.validate_bundle(sealed_bundle)["manifest"]
        second = rt.predict_match(
            target.competition_id, target.home_team_name, target.away_team_name,
            target.kickoff, cutoff, sealed_bundle, input_path,
        )
        after_second = rt.validate_bundle(sealed_bundle)["manifest"]
        sha_keys = ("state_sha256", "runtime_input_sha", "prediction_sha")
        sha_equal = {key: first[key] == second[key] for key in sha_keys}
        if not all(sha_equal.values()) or sealed_before != after_first or after_first != after_second:
            raise AssertionError(f"repeated sealed request not deterministic: {sha_equal}")
        if first["model_route"] != "FUSION_V2_ACTIVE" or second["model_route"] != "FUSION_V2_ACTIVE":
            raise AssertionError("repeated sealed request did not invoke active formal Fusion")
        if first["fallback_exact_v1"] or second["fallback_exact_v1"]:
            raise AssertionError("repeated sealed request unexpectedly fell back")

    receipt = {
        "schema_version": SCHEMA,
        "runtime_exact_head": exact_head,
        "formal_model_head": rt.FORMAL_HEAD,
        "current_sha256": rt.CURRENT_SHA256,
        "formal_scope": list(rt.FORMAL_SCOPE),
        "target": {
            "fixture_id": target.fixture_id,
            "competition_id": target.competition_id,
            "season": target.season,
            "kickoff": target.kickoff.isoformat(),
            "home_team_name": target.home_team_name,
            "away_team_name": target.away_team_name,
            "cutoff": cutoff.isoformat(),
            "candidate_scan_n": scanned,
            "target_result_read_or_scored": False,
        },
        "effective_evidence": full_audit["evidence"],
        "effective_evidence_n": 4,
        "formal_min_effective_evidence": MIN_EVIDENCE,
        "all_effective_evidence_gate_passed": min(full_audit["evidence"]) >= MIN_EVIDENCE,
        "model_route": "FUSION_V2_ACTIVE",
        "fallback_class": "NONE",
        "fallback_exact_v1": False,
        "formal_model_invoked": True,
        "formal_model_call_path": "runtime._prediction_from_state -> formal_v2.predict_formal_batch",
        "fast_full": {
            "fast_path": fast["path"],
            "full_path": full["path"],
            "delta_n": len(delta),
            "state_exact": True,
            "prediction_equivalence": equivalence,
        },
        "repeated_sealed_request": {
            "passed": True,
            "first": {key: first[key] for key in sha_keys},
            "second": {key: second[key] for key in sha_keys},
            "sha_equal": sha_equal,
            "state_bundle_unchanged": True,
        },
        "empty_delta": {
            "delta_n": 0,
            "path": no_op["path"],
            "transition_status": no_op["delta_result"]["transition_status"],
            "source_refetch_used": no_op["delta_result"]["source_refetch_used"],
            "state_resealed": no_op["delta_result"]["state_resealed"],
        },
        "result_adjudication": ADJUDICATION,
        "training_or_tuning_used": False,
        "scoring_used": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
        "passed": True,
    }
    receipt["receipt_sha256"] = tr.sha(receipt)
    Path(args.out).write_bytes(tr.canon(receipt))
    print(json.dumps({
        "status": "PASS", "receipt_sha256": receipt["receipt_sha256"],
        "fixture_id": target.fixture_id, "effective_evidence": receipt["effective_evidence"],
        "model_route": receipt["model_route"], "fallback_class": receipt["fallback_class"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
