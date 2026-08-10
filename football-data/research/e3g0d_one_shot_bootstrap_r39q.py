#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import e3g0d_api_football_forward_collector as collector
from e3g0d_common import E3Error, expiry, request_day_utc

HERE = Path(__file__).resolve().parent
CONTRACT_PATH = HERE / "e3g0d_r39q_one_shot_contract.json"
EXPECTED_BRANCH = "refs/heads/research/e3g0d-one-shot-live-r39q"


def fail(code: str) -> None:
    raise E3Error(code)


def load_contract() -> dict:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert value["schema_version"] == "R39Q-ONE-SHOT-FORWARD-BOOTSTRAP-1.0"
    assert value["status"] == "AUTHORIZED_ONE_SHOT_RESEARCH_BOOTSTRAP"
    assert value["branch"] == "research/e3g0d-one-shot-live-r39q"
    assert value["event"] == "push"
    assert value["run_attempt_required"] == 1
    assert value["provider"] == "API-Football"
    assert value["endpoint"] == "fixtures"
    assert value["mode"] == "build-plan"
    assert value["competition_id"] == 39
    assert value["season_id"] == 2026
    assert value["target_date_utc"] == "2026-08-21"
    assert value["fixture_limit"] == 1
    assert value["max_provider_requests"] == 1
    assert value["retries"] == 0
    assert value["hard_limits"]["formal_weight"] == 0
    assert value["hard_limits"]["schedule_activation"] is False
    assert value["hard_limits"]["model_fit"] == 0
    assert value["hard_limits"]["target_labels_accessed"] == 0
    return value


def exact_head() -> str:
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    expected = str(os.environ.get("GITHUB_SHA", ""))
    if not expected or actual != expected:
        fail("R39Q_EXACT_HEAD_MISMATCH")
    return actual


def validate_runtime(contract: dict) -> tuple[str, int]:
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        fail("R39Q_EVENT_NOT_AUTHORIZED")
    if os.environ.get("GITHUB_REF") != EXPECTED_BRANCH:
        fail("R39Q_BRANCH_NOT_AUTHORIZED")
    if os.environ.get("R39Q_ONE_SHOT_BOOTSTRAP") != "true":
        fail("R39Q_BOOTSTRAP_MARKER_MISSING")
    if str(os.environ.get("GITHUB_RUN_ATTEMPT", "")) != "1":
        fail("R39Q_RERUN_PROHIBITED")
    if os.environ.get("RESERVATION_UPLOAD") != "success":
        fail("R39Q_QUOTA_RESERVATION_REQUIRED")
    if os.environ.get("CONSUMED_UPLOAD") != "success":
        fail("R39Q_AUTHORIZATION_CONSUMPTION_REQUIRED")
    if not str(os.environ.get("API_FOOTBALL_KEY", "")).strip():
        fail("R39Q_API_KEY_MISSING")
    try:
        used = int(os.environ.get("REQUESTS_USED_TODAY", "-1"))
    except ValueError:
        fail("R39Q_QUOTA_STATE_INVALID")
    if used < 0 or used + contract["max_provider_requests"] > contract["daily_project_safety_cap"]:
        fail("R39Q_QUOTA_RESERVE_REACHED")
    return exact_head(), used


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args0 = parser.parse_args()
    contract = load_contract()
    try:
        head, used = validate_runtime(contract)
        run_id = str(os.environ["GITHUB_RUN_ID"])
        run_attempt = str(os.environ["GITHUB_RUN_ATTEMPT"])
        ns = argparse.Namespace(
            mode="build-plan",
            date=contract["target_date_utc"],
            league=contract["competition_id"],
            season=contract["season_id"],
            timezone="UTC",
            fixture_limit=contract["fixture_limit"],
            max_requests=contract["max_provider_requests"],
            requests_used_today=used,
            timeout=float(contract["timeout_seconds"]),
            retries=int(contract["retries"]),
            backoff=8.0,
            tolerance=7,
            dry_run=False,
            no_network=False,
            upload_artifact=True,
            allow_schedule=False,
            retention=30,
            expires=expiry(30),
            run_head=head,
            run_id=f"{run_id}-attempt-{run_attempt}",
            selected_plan_identity=None,
            expected_request_day_utc=request_day_utc(),
        )
        guard = {
            "deployment_status": "R39Q_ONE_SHOT_RESEARCH_BOOTSTRAP",
            "authorization_nonce": contract["authorization_nonce"],
            "collector_enabled": True,
            "schedule_enabled": False,
            "event_name": "push",
            "github_ref": EXPECTED_BRANCH,
            "network_requested": True,
            "dry_run": False,
            "no_network": False,
            "max_provider_requests": 1,
            "formal_weight": 0,
        }
        args0.output_dir.mkdir(parents=True, exist_ok=True)
        row = collector.execute(ns, args0.output_dir, guard, collector.liveop(ns))
        if row.get("outcome") != "SUCCESS":
            fail("R39Q_PROVIDER_RUN_NOT_SUCCESSFUL")
        if int(row.get("request_attempts", -1)) != 1:
            fail("R39Q_REQUEST_COUNT_VIOLATION")
        if int(row.get("snapshot_count", -1)) != 1:
            fail("R39Q_SNAPSHOT_COUNT_VIOLATION")
        if int(row.get("plan_fixture_count", -1)) < 1:
            fail("R39Q_NO_TARGET_FIXTURE_RETURNED")
        if row.get("candidate_probabilities") != 0 or row.get("model_fits") != 0 or row.get("formal_weight") != 0:
            fail("R39Q_RESEARCH_BOUNDARY_VIOLATION")
        summary = {
            "schema_version": "R39Q-ONE-SHOT-RESULT-1.0",
            "status": "PASS_R39Q_FIRST_AUTHENTIC_FORWARD_FIXTURE_OBSERVATION",
            "authorization_nonce": contract["authorization_nonce"],
            "run_head": head,
            "workflow_run_id": run_id,
            "workflow_run_attempt": int(run_attempt),
            "request_day_utc": row["request_day_utc"],
            "target_date_utc": row["target_date_utc"],
            "request_attempts": row["request_attempts"],
            "requests_used_today_before_run": row["requests_used_today_before_run"],
            "snapshot_count": row["snapshot_count"],
            "plan_fixture_count": row["plan_fixture_count"],
            "plan_sha256": row["plan_sha256"],
            "plan_path": row["plan_path"],
            "snapshot": row["snapshots"][0],
            "target_labels_accessed": 0,
            "model_fits": 0,
            "candidate_probabilities": 0,
            "formal_weight": 0,
            "schedule_enabled": False,
            "formal_assets_mutated": False,
        }
        (args0.output_dir / "r39q_bootstrap_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps({
            "status": summary["status"],
            "request_attempts": summary["request_attempts"],
            "plan_fixture_count": summary["plan_fixture_count"],
            "plan_sha256": summary["plan_sha256"],
            "formal_weight": 0,
        }, sort_keys=True))
        return 0
    except E3Error as exc:
        print(f"R39Q bootstrap error [{exc.failure_class}]", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
