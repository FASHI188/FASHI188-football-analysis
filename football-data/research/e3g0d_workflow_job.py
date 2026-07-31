#!/usr/bin/env python3
"""Thin production orchestrator; all untrusted controls and digests use e3g0d_runtime."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from e3g0d_collect import due
from e3g0d_common import E3Error, iso, packed, utc_now
from e3g0d_runtime import (
    build_plan_index,
    create_reservation,
    final_gate,
    prepare_final_evidence,
    provider_allowed,
    resolve_controls,
    write_github_outputs,
)


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=capture, check=True)


def exact_head(head: str) -> None:
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if actual != head:
        raise E3Error("EVIDENCE_HEAD_MISMATCH")


def _write_no_network_receipt(
    root: Path,
    controls: dict[str, str],
    *,
    schema: str,
    final_status: str,
    due_windows: dict[str, list[dict[str, Any]]] | None = None,
    observed_at_utc: str | None = None,
) -> Path:
    destination = root / "no-network"
    destination.mkdir(parents=True, exist_ok=True)
    counts = {key: len(value) for key, value in (due_windows or {}).items()}
    row: dict[str, Any] = {
        "schema_version": schema,
        "deployment_status": "IMPLEMENTED_NOT_LIVE",
        "mode": controls["mode"],
        "request_day_utc": controls["request_day"],
        "target_date_utc": controls["target"],
        "request_attempts": 0,
        "reservation_count": 0,
        "run_head": controls["evidence_head"],
        "evidence_head": controls["evidence_head"],
        "workflow_run_id": controls["run_id"],
        "workflow_run_attempt": controls["run_attempt"],
        "final_status": final_status,
        "append_only": True,
        "formal_weight": 0,
    }
    if due_windows is not None:
        row["due_window_counts"] = counts
        row["observed_at_utc"] = observed_at_utc
    filename = (
        "no_network_due_receipt.json"
        if schema == "E3G0D-NO-NETWORK-DUE-1.0"
        else "no_network_receipt.json"
    )
    path = destination / filename
    path.write_bytes(packed(row) + b"\n")
    return path


def prepare(
    env: dict[str, Any],
    root: Path,
    *,
    command_runner: Callable[[list[str], bool], subprocess.CompletedProcess[str]] = run,
    head_checker: Callable[[str], None] = exact_head,
    clock: Callable[[], Any] = utc_now,
    due_checker: Callable[[list[dict[str, Any]], Any, int], dict[str, list[dict[str, Any]]]] = due,
) -> dict[str, str]:
    controls = resolve_controls(env)
    head_checker(controls["evidence_head"])
    output = dict(
        controls,
        needs_requests="false",
        used="0",
        reservation_id="",
        reservation_sha256="",
    )
    archive = str(env["ARCHIVE"])
    repository = str(env["GITHUB_REPOSITORY"])

    if controls["mode"] == "status-check":
        command_runner(
            [
                sys.executable,
                archive,
                "status",
                "--repository",
                repository,
                "--archive-root",
                str(root / "status"),
            ],
            False,
        )
        return output

    if controls["ok"] != "true":
        _write_no_network_receipt(
            root,
            controls,
            schema="E3G0D-NO-NETWORK-1.0",
            final_status="LIVE_CONTROLS_BLOCKED_OR_NO_NETWORK",
        )
        return output

    if controls["mode"] != "build-plan":
        args = [
            sys.executable,
            archive,
            "resolve-plan",
            "--repository",
            repository,
            "--archive-root",
            str(root / "plan"),
            "--target-date-utc",
            controls["target"],
            "--league",
            controls["league"],
            "--season",
            controls["season"],
        ]
        if controls["plan_id"]:
            args += ["--artifact-id", controls["plan_id"]]
        if controls["plan_sha"]:
            args += ["--plan-sha256", controls["plan_sha"]]
        command_runner(args, False)

    if controls["mode"] == "lineup-window":
        identity_path = root / "plan" / "selected_plan_identity.json"
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            plan_path = Path(str(identity["selected_plan_path"]))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            fixtures = plan.get("fixtures")
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise E3Error("PLAN_IDENTITY_AMBIGUOUS", "unable to read selected immutable plan") from exc
        if not isinstance(fixtures, list) or any(not isinstance(item, dict) for item in fixtures):
            raise E3Error("IDENTITY_MAPPING_FAILED", "selected plan fixtures are invalid")
        observed = clock()
        windows = due_checker([dict(item) for item in fixtures], observed, 7)
        if not any(windows.values()):
            _write_no_network_receipt(
                root,
                controls,
                schema="E3G0D-NO-NETWORK-DUE-1.0",
                final_status="NO_REQUESTS_DUE",
                due_windows=windows,
                observed_at_utc=iso(observed),
            )
            return output

    quota = command_runner(
        [
            sys.executable,
            archive,
            "quota-used",
            "--repository",
            repository,
            "--archive-root",
            str(root / "quota"),
            "--request-day-utc",
            controls["request_day"],
            "--print-summary",
        ],
        True,
    )
    used = int(json.loads(quota.stdout)["requests_used_today"])
    if not 0 <= used <= 90:
        raise E3Error("QUOTA_STATE_UNTRUSTED")
    reservation = create_reservation(
        dict(
            env,
            MAXIMUM=controls["max"],
            USED=str(used),
            HEAD_VALUE=controls["evidence_head"],
            RUN_VALUE=controls["run_id"],
            ATTEMPT_VALUE=controls["run_attempt"],
            REQUEST_DAY=controls["request_day"],
            MODE_VALUE=controls["mode"],
            TARGET=controls["target"],
            EXPIRES=controls["expires"],
        ),
        root / "reservation",
    )
    output.update(
        needs_requests="true",
        used=str(used),
        reservation_id=reservation["row"]["reservation_id"],
        reservation_sha256=reservation["reservation_sha256"],
    )
    return output


def collect(env: dict[str, Any], root: Path) -> int:
    if not provider_allowed(
        str(env.get("CONTROLS_OK", "")),
        str(env.get("NEEDS_REQUESTS", "")),
        str(env.get("RESERVATION_UPLOAD", "")),
    ):
        raise E3Error("RESERVATION_REQUIRED")
    exact_head(str(env["HEAD_VALUE"]))
    args = [
        sys.executable,
        str(env["COLLECTOR"]),
        "--mode",
        str(env["MODE"]),
        "--output-dir",
        str(root / "out"),
        "--date",
        str(env["TARGET"]),
        "--league",
        str(env["LEAGUE"]),
        "--season",
        str(env["SEASON"]),
        "--fixture-limit",
        str(env["LIMIT"]),
        "--max-requests",
        str(env["MAXIMUM"]),
        "--requests-used-today",
        str(env["USED"]),
        "--dry-run",
        "false",
        "--no-network",
        "false",
        "--upload-artifact",
        "true",
        "--allow-schedule",
        "true",
        "--retention",
        "30",
        "--expires",
        str(env["EXPIRES"]),
        "--expected-request-day-utc",
        str(env["DAY"]),
        "--run-head",
        str(env["HEAD_VALUE"]),
        "--run-id",
        f"{env['RUN_VALUE']}-attempt-{env['ATTEMPT_VALUE']}",
    ]
    if env["MODE"] != "build-plan":
        args += ["--selected-plan-identity", str(root / "plan" / "selected_plan_identity.json")]
    process = subprocess.run(args, text=True)
    receipts = (
        sorted((root / "out" / "run_receipts").glob("*.json"))
        if (root / "out" / "run_receipts").exists()
        else []
    )
    if len(receipts) > 1:
        raise E3Error("EVIDENCE_AMBIGUOUS")
    receipt = receipts[0].as_posix() if receipts else ""
    evidence_env = dict(
        env,
        RECEIPT=receipt,
        COLLECTOR_OUTCOME="success" if process.returncode == 0 else "failure",
    )
    final_row = prepare_final_evidence(evidence_env, root)
    plan_sha = ""
    if receipt:
        plan_sha = str(json.loads(Path(receipt).read_text(encoding="utf-8")).get("plan_sha256") or "")
    write_github_outputs(
        {"receipt": receipt, "plan_sha256": plan_sha, "final_status": final_row["final_status"]},
        str(env["GITHUB_OUTPUT"]),
    )
    return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "collect", "plan-index", "gate"])
    args = parser.parse_args()
    root = Path(os.environ["RUNNER_TEMP"])
    try:
        if args.command == "prepare":
            result = prepare(dict(os.environ), root)
            write_github_outputs(result, os.environ["GITHUB_OUTPUT"])
        elif args.command == "collect":
            return collect(dict(os.environ), root)
        elif args.command == "plan-index":
            build_plan_index(os.environ, root / "index")
        else:
            passed, reason = final_gate(os.environ)
            if not passed:
                print(reason, file=sys.stderr)
                return 2
        return 0
    except (
        E3Error,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(
            f"E3g-0D workflow job failed [{getattr(exc, 'failure_class', 'VALIDATION_FAILED')}]",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
