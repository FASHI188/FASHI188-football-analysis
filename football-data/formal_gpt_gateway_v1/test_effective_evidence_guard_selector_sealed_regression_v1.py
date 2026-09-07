#!/usr/bin/env python3
"""Permanent regression for governed effective evidence, durable selection, and sealed replay.

This test intentionally uses one frozen pre-kickoff state Artifact as a deterministic
fixture while exercising generic selector/guard/sealed-replay contracts. It does not
modify model parameters, CURRENT, fusion weights, or scientific thresholds.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

import durable_state_contract_v1 as contract
import durable_state_selector_v1 as selector
import formal_effective_evidence_guard_governed_v1 as eff
import runtime as rt


def _write_json(path: pathlib.Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")


def _directed_contract_regression(
    bundle: pathlib.Path,
    meta: dict,
    run: dict,
    target,
    kickoff,
) -> dict:
    loaded = rt.validate_bundle(bundle)
    assert loaded["meta"]["historical_cutoff"] == target.isoformat()

    pre = selector._candidate_from_bundle(meta, run, bundle, target, "GER_Bundesliga", kickoff)
    assert pre["verified"] and pre["pit_ok"] and pre["artifact_available_before_kickoff"]
    assert rt._parse_dt(pre["artifact_created_at"], "artifact created") < kickoff
    assert pre["artifact_available_by_target_cutoff"] is False

    post_meta = dict(meta)
    post_meta["id"] = int(meta["id"]) + 999999
    post_meta["created_at"] = "2026-09-06T13:31:00+00:00"
    post = selector._candidate_from_bundle(post_meta, run, bundle, target, "GER_Bundesliga", kickoff)
    assert post["artifact_available_before_kickoff"] is False and post["pit_ok"] is False
    chosen, evaluated = contract.choose_candidate([pre, post], target, "GER_Bundesliga")
    assert chosen and chosen["artifact_id"] == int(meta["id"])
    assert any("PIT" in row["rejection_reasons"] for row in evaluated if row["artifact_id"] == post_meta["id"])

    bad_verified = dict(pre)
    bad_verified["verified"] = False
    chosen2, evaluated2 = contract.choose_candidate([bad_verified], target, "GER_Bundesliga")
    assert chosen2 is None and "VERIFIED" in evaluated2[0]["rejection_reasons"]

    bad_pit = dict(pre)
    bad_pit["pit_ok"] = False
    chosen3, evaluated3 = contract.choose_candidate([bad_pit], target, "GER_Bundesliga")
    assert chosen3 is None and "PIT" in evaluated3[0]["rejection_reasons"]

    chosen4, _ = contract.choose_candidate([post], target, "GER_Bundesliga")
    assert chosen4 is None

    import entry

    assert entry.FORMAL_STATE_INTEGRITY_COVERAGE_PATCH["installed"] is True
    eg = entry.FORMAL_STATE_INTEGRITY_COVERAGE_PATCH["effective_evidence_guard"]
    assert eg["installed"] is True
    assert eg["prematch_fallback_verdict_source"] == "historical_xg_challenger_v1.dynamic.fallback_exact_v1"
    assert eg["independent_guard_threshold_reimplementation_used"] is False

    saved_classifier = eff._ORIGINAL_CLASSIFY

    def base_audit(reasons=None):
        return {"anomaly_reasons": list(reasons or []), "historical_xg": {}}

    try:
        eff._ORIGINAL_CLASSIFY = lambda *a, **k: base_audit()
        active = eff.classify_state(
            {}, {}, {},
            {"dynamic": {"evidence": [6, 7, 8, 9], "fallback_exact_v1": False}},
            {"fallback_exact_v1": False},
        )
        assert active["status"] == "PASS" and active["fallback_class"] == "NONE"
        assert active["formal_fallback_verdict"]["effective_evidence"] == [6.0, 7.0, 8.0, 9.0]

        eff._ORIGINAL_CLASSIFY = lambda *a, **k: base_audit(["XG_EXPECTED_BUT_EFFECTIVE_EVIDENCE_INSUFFICIENT:legacy"])
        legal = eff.classify_state(
            {}, {}, {},
            {"dynamic": {"evidence": [1.0, 2.0, 1.5, 2.5], "fallback_exact_v1": True}},
            {"fallback_exact_v1": True},
        )
        assert legal["status"] == "PASS" and legal["fallback_class"] == "NORMAL_FALLBACK"
        assert not legal["anomaly_reasons"]

        eff._ORIGINAL_CLASSIFY = lambda *a, **k: base_audit(["FALLBACK_DESPITE_EFFECTIVE_EVIDENCE_THRESHOLD:legacy"])
        unexpected = eff.classify_state(
            {}, {}, {},
            {"dynamic": {"evidence": [5, 5, 5, 5], "fallback_exact_v1": False}},
            {"fallback_exact_v1": True},
        )
        assert unexpected["status"] == "DATA_STATE_ANOMALY"
        assert any(x.startswith("FORMAL_XG_FALLBACK_VERDICT_MISMATCH:") for x in unexpected["anomaly_reasons"])

        eff._ORIGINAL_CLASSIFY = lambda *a, **k: base_audit(["HOME_ESTABLISHED_HISTORY_ZERO"])
        independent = eff.classify_state(
            {}, {}, {},
            {"dynamic": {"evidence": [1, 1, 1, 1], "fallback_exact_v1": True}},
            {"fallback_exact_v1": True},
        )
        assert independent["status"] == "DATA_STATE_ANOMALY"
        assert "HOME_ESTABLISHED_HISTORY_ZERO" in independent["anomaly_reasons"]
    finally:
        eff._ORIGINAL_CLASSIFY = saved_classifier

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="f3-first-reason-"))
    saved_enrich = eff._ORIGINAL_ENRICH
    try:
        (tmp / "prediction_receipt.json").write_text(json.dumps({"failure_reason": "FIRST_AUTH", "fallback_exact_v1": True}))
        eff._ORIGINAL_ENRICH = lambda out, result, audit, mode, binding: {"fallback_reason": "LATER_GUARD", "receipt_sha": "old"}
        result = {}
        enriched = eff._enrich_receipt(tmp, result, {"formal_fallback_verdict": {"source": "test"}}, "FAST", {})
        assert enriched["fallback_reason"] == "FIRST_AUTH"
        assert enriched["first_authoritative_failure_reason"] == "FIRST_AUTH"
        assert enriched["integrity_guard_fallback_reason"] == "LATER_GUARD"
        assert result["fallback_reason"] == "FIRST_AUTH"
    finally:
        eff._ORIGINAL_ENRICH = saved_enrich
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="f3-sealed-recovery-"))
    state = tmp / "state"
    shutil.copytree(bundle, state / "bundle")
    before = rt.validate_bundle(state / "bundle")["manifest"]["state_bundle_sha256"]
    saved_pre = eff._ORIGINAL_CACHE_PREFLIGHT
    saved_clear = eff._ORIGINAL_CLEAR_CACHE
    saved_full = eff._ORIGINAL_FULL_REBUILD
    try:
        eff._ORIGINAL_CACHE_PREFLIGHT = lambda *a, **k: {"fast_eligible": False, "reason": "GENERIC_TRUE_ANOMALY_PRECHECK"}
        eff._ORIGINAL_CLEAR_CACHE = lambda *a, **k: (_ for _ in ()).throw(AssertionError("actual cache clear invoked"))
        eff._ORIGINAL_FULL_REBUILD = lambda *a, **k: (_ for _ in ()).throw(AssertionError("actual live FULL invoked"))
        pr = eff._cache_preflight(
            state, pathlib.Path("."), "GER_Bundesliga", "2026/27",
            "Hamburger SV", "1. FSV Mainz 05", kickoff, target,
        )
        assert pr["sealed_exact_cutoff_replay"] and pr["recovery_suppressed"] and pr["fast_eligible"]
        eff._clear_cache(state)
        rb = eff._build_integrity_base(state / "bundle")
        assert rb["cache_clear_used"] is False and rb["live_full_used"] is False
        assert rb["route"] == "SEALED_EXACT_CUTOFF_REPLAY_RECOVERY_SUPPRESSED"
        after = rt.validate_bundle(state / "bundle")["manifest"]["state_bundle_sha256"]
        assert after == before
    finally:
        eff._ORIGINAL_CACHE_PREFLIGHT = saved_pre
        eff._ORIGINAL_CLEAR_CACHE = saved_clear
        eff._ORIGINAL_FULL_REBUILD = saved_full
        eff._SEALED_EXACT_ROOTS.discard(str(state.resolve()))
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "status": "PASS",
        "selector_prematch_artifact_ceiling": True,
        "postmatch_duplicate_rejected": True,
        "verified_rejection": True,
        "pit_rejection": True,
        "no_prematch_state_fail_closed": True,
        "formal_entry_effective_guard_installed": True,
        "fusion_v2_active_verdict": True,
        "legal_normal_fallback_verdict": True,
        "continuing_club_unexpected_fallback_anomaly": True,
        "sealed_cache_clear_used": False,
        "sealed_live_full_used": False,
        "first_authoritative_failure_reason_preserved": True,
    }


def _build_selection(state_root: pathlib.Path, meta: dict, run: dict, target, kickoff) -> dict:
    cand = selector._candidate_from_bundle(meta, run, state_root / "bundle", target, "GER_Bundesliga", kickoff)
    selected, evaluated = contract.choose_candidate([cand], target, "GER_Bundesliga")
    assert selected is not None and selected["artifact_id"] == int(meta["id"])
    core = {
        "schema_version": contract.SELECTION_SCHEMA,
        "status": "SELECTED",
        "selection_rule": "frozen_prematch_regression_artifact_then_generic_eligibility_contract",
        "target_cutoff": target.isoformat(),
        "prematch_artifact_availability_ceiling": kickoff.isoformat(),
        "competition_id": "GER_Bundesliga",
        "selected": selected,
        "runtime_contract": contract.runtime_contract_payload(),
        "candidate_count": 1,
        "candidates": evaluated,
    }
    audit = {**core, "selection_sha256": contract.sha(core)}
    (state_root / "durable_state_selection_v1.json").write_bytes(contract.canon(audit))
    return audit


def _hamburg_sealed_replay(
    repo_root: pathlib.Path,
    understat_db: pathlib.Path,
    confirmation_dir: pathlib.Path,
    state_root: pathlib.Path,
    meta: dict,
    run: dict,
    target,
    kickoff,
    out_dir: pathlib.Path,
) -> tuple[dict, dict]:
    selection = _build_selection(state_root, meta, run, target, kickoff)
    req = {
        "schema_version": "football3-formal-gpt-gateway-v1",
        "request_id": "hamburg-mainz-frozen-sealed-regression-v1",
        "mode": "predict",
        "match": {
            "competition_id": "GER_Bundesliga",
            "season": "2026/27",
            "home_team_name": "Hamburger SV",
            "away_team_name": "1. FSV Mainz 05",
            "kickoff": kickoff.isoformat(),
            "cutoff": target.isoformat(),
        },
    }
    request_path = out_dir / "hamburg_request.json"
    _write_json(request_path, req)
    prediction_dir = out_dir / "hamburg_out"
    prediction_dir.mkdir(parents=True, exist_ok=True)

    before = rt.validate_bundle(state_root / "bundle")["manifest"]["state_bundle_sha256"]
    env = os.environ.copy()
    env["FOOTBALL3_DURABLE_SELECTOR_REQUIRED"] = "1"
    cmd = [
        sys.executable,
        str(repo_root / "football-data/formal_gpt_gateway_v1/entry.py"),
        "--request", str(request_path),
        "--repo-root", str(repo_root),
        "--understat-db", str(understat_db),
        "--confirmation-dir", str(confirmation_dir),
        "--state-root", str(state_root),
        "--out", str(prediction_dir),
    ]
    proc = subprocess.run(cmd, cwd=repo_root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    (out_dir / "hamburg_stdout.txt").write_text(proc.stdout)
    after = rt.validate_bundle(state_root / "bundle")["manifest"]["state_bundle_sha256"]
    assert before == after

    summary = json.load(open(prediction_dir / "summary.json"))
    receipt = json.load(open(prediction_dir / "prediction_receipt.json"))
    xg = json.load(open(prediction_dir / "xg_trigger_audit.json"))
    assert summary["status"] == "PASS"
    assert summary["gateway_route"] == "SEALED_EXACT_CUTOFF_REPLAY"
    assert summary["source_refetch_used"] is False and summary["state_mutated"] is False
    assert summary["prediction_sha"] and receipt["prediction_sha"] == summary["prediction_sha"]

    evidence = [float(v) for v in xg["dynamic"]["evidence"]]
    assert len(evidence) == 4
    formal_flag = bool(xg["dynamic"]["fallback_exact_v1"])
    assert formal_flag == bool(receipt["fallback_exact_v1"])
    expected_route = "FROZEN_V1_EXACT_FALLBACK" if formal_flag else "FUSION_V2_ACTIVE"
    assert receipt["model_route"] == expected_route
    fv = receipt["formal_fallback_verdict"]
    assert fv["source"] == "historical_xg_challenger_v1.dynamic.fallback_exact_v1"
    assert fv["receipt_verdict_consistent"] is True
    assert fv["effective_evidence"] == evidence

    created = datetime.fromisoformat(str(meta["created_at"]).replace("Z", "+00:00"))
    assert created < kickoff
    assert selection["selected"]["artifact_id"] == int(meta["id"])

    probs = {"home": float(receipt["p_home"]), "draw": float(receipt["p_draw"]), "away": float(receipt["p_away"])}
    result = {
        "status": "PASS",
        "source_run_id": int(run["id"]),
        "source_artifact_id": int(meta["id"]),
        "source_artifact_created_at": meta["created_at"],
        "kickoff": kickoff.isoformat(),
        "cutoff": target.isoformat(),
        "gateway_route": summary["gateway_route"],
        "source_refetch_used": summary["source_refetch_used"],
        "state_mutated": summary["state_mutated"],
        "model_route": receipt["model_route"],
        "fallback_class": receipt.get("fallback_class"),
        "fallback_exact_v1": receipt["fallback_exact_v1"],
        "effective_evidence": evidence,
        "prediction_sha": receipt["prediction_sha"],
        "receipt_sha": receipt["receipt_sha"],
        "p_home": probs["home"],
        "p_draw": probs["draw"],
        "p_away": probs["away"],
        "top1": max(probs, key=probs.get),
        "top3_scores": receipt["top_scores"][:3],
        "state_sha256": receipt["state_sha256"],
        "state_bundle_sha256": receipt["state_bundle_sha"],
        "selection_sha256": selection["selection_sha256"],
        "artifact_digest": meta.get("digest"),
    }
    _write_json(out_dir / "hamburg_sealed_replay.json", result)
    _write_json(out_dir / "hamburg_bound_selection.json", selection)
    return result, req


def _true_anomaly_fail_closed(
    repo_root: pathlib.Path,
    understat_db: pathlib.Path,
    confirmation_dir: pathlib.Path,
    source_bundle: pathlib.Path,
    selection_path: pathlib.Path,
    req: dict,
    out_dir: pathlib.Path,
) -> dict:
    import entry  # noqa: F401
    import gateway

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="f3-sealed-anomaly-"))
    state = tmp / "state"
    prediction_out = tmp / "out"
    shutil.copytree(source_bundle, state / "bundle")
    shutil.copy2(selection_path, state / "durable_state_selection_v1.json")
    prediction_out.mkdir(parents=True, exist_ok=True)
    before = rt.validate_bundle(state / "bundle")["manifest"]["state_bundle_sha256"]

    saved = eff._ORIGINAL_CLASSIFY
    saved_clear = eff._ORIGINAL_CLEAR_CACHE
    saved_full = eff._ORIGINAL_FULL_REBUILD
    calls = {"actual_clear": 0, "actual_full": 0}

    def forced(*args, **kwargs):
        d = saved(*args, **kwargs)
        d["anomaly_reasons"] = list(d.get("anomaly_reasons") or []) + ["GENERIC_FORCED_TRUE_ANOMALY"]
        return d

    def actual_clear(*args, **kwargs):
        calls["actual_clear"] += 1
        raise AssertionError("sealed anomaly invoked actual cache clear")

    def actual_full(*args, **kwargs):
        calls["actual_full"] += 1
        raise AssertionError("sealed anomaly invoked actual live FULL")

    try:
        eff._ORIGINAL_CLASSIFY = forced
        eff._ORIGINAL_CLEAR_CACHE = actual_clear
        eff._ORIGINAL_FULL_REBUILD = actual_full
        result = gateway.normal_request(req, state, prediction_out, repo_root, understat_db, confirmation_dir)
    finally:
        eff._ORIGINAL_CLASSIFY = saved
        eff._ORIGINAL_CLEAR_CACHE = saved_clear
        eff._ORIGINAL_FULL_REBUILD = saved_full

    after = rt.validate_bundle(state / "bundle")["manifest"]["state_bundle_sha256"]
    assert result["status"] == "DATA_STATE_ANOMALY"
    assert result["prediction_sha"] is None and result["receipt_sha"] is None
    assert calls == {"actual_clear": 0, "actual_full": 0}
    assert before == after
    manifest = {
        "status": "PASS",
        "result_status": result["status"],
        "prediction_sha": result["prediction_sha"],
        "actual_cache_clear_calls": 0,
        "actual_live_full_calls": 0,
        "state_bundle_unchanged": True,
    }
    _write_json(out_dir / "sealed_true_anomaly.json", manifest)
    shutil.rmtree(tmp, ignore_errors=True)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--state-root", required=True)
    ap.add_argument("--source-run-json", required=True)
    ap.add_argument("--source-artifact-json", required=True)
    ap.add_argument("--source-artifact-id", type=int, required=True)
    ap.add_argument("--kickoff", required=True)
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo_root = pathlib.Path(args.repo_root).resolve()
    understat_db = pathlib.Path(args.understat_db).resolve()
    confirmation_dir = pathlib.Path(args.confirmation_dir).resolve()
    state_root = pathlib.Path(args.state_root).resolve()
    out_dir = pathlib.Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    run = json.load(open(args.source_run_json))
    meta = json.load(open(args.source_artifact_json))
    assert int(run["id"]) > 0 and run["status"] == "completed" and run["conclusion"] == "success"
    assert int(meta["id"]) == args.source_artifact_id and meta["expired"] is False

    kickoff = rt._parse_dt(args.kickoff, "kickoff")
    target = rt._parse_dt(args.cutoff, "cutoff")
    assert datetime.fromisoformat(str(meta["created_at"]).replace("Z", "+00:00")) < kickoff

    directed = _directed_contract_regression(state_root / "bundle", meta, run, target, kickoff)
    hamburg, req = _hamburg_sealed_replay(
        repo_root, understat_db, confirmation_dir, state_root, meta, run, target, kickoff, out_dir
    )
    anomaly = _true_anomaly_fail_closed(
        repo_root,
        understat_db,
        confirmation_dir,
        state_root / "bundle",
        state_root / "durable_state_selection_v1.json",
        req,
        out_dir,
    )
    result = {
        "schema_version": "football3-effective-evidence-guard-selector-sealed-regression-v1",
        "status": "PASS",
        "directed": directed,
        "sealed_true_anomaly": anomaly,
        "hamburg_mainz_frozen_fixture": hamburg,
        "scientific_logic_changed": False,
    }
    _write_json(out_dir / "selector_guard_sealed_regression.json", result)
    print("SELECTOR_GUARD_SEALED_REGRESSION_PASS", json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
