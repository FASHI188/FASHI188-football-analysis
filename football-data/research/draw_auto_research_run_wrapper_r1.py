#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
from typing import Any, Sequence


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


def append_jsonl(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _stop_reason(exit_code: int) -> str:
    return "CONTROLLER_EXIT_2_SAFETY_FAILURE" if exit_code == 2 else f"CONTROLLER_EXIT_{exit_code}_RUNTIME_FAILURE"


def write_failure_receipt(state_dir: pathlib.Path, exit_code: int, error: str, command: Sequence[str]) -> pathlib.Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "DRAW-AUTO-RUN-FAILURE-R1.4",
        "status": "FAILED",
        "exit_code": int(exit_code),
        "stop_reason": _stop_reason(int(exit_code)),
        "error": error,
        "command": list(command),
        "recorded_at": utc_now(),
        "data_status": "VIEWED_DEVELOPMENT_DATA",
        "checkpoint_present": (state_dir / "checkpoint.json").is_file(),
        "formal_weight": 0,
        "repository_writeback": 0,
    }
    path = state_dir / "run_failure_receipt.json"
    atomic_json(path, receipt)
    append_jsonl(state_dir / "ledger.jsonl", {"record_type": "RUN_FAILURE", **receipt})
    return path


def terminalize_existing_checkpoint(state_dir: pathlib.Path, receipt: dict[str, Any]) -> dict[str, Any] | None:
    checkpoint_path = state_dir / "checkpoint.json"
    if not checkpoint_path.is_file():
        return None
    checkpoint = read_json(checkpoint_path)
    checkpoint["status"] = "FAILED_SAFETY" if int(receipt["exit_code"]) == 2 else "FAILED_RUNTIME"
    checkpoint["stop_reason"] = receipt["stop_reason"]
    checkpoint["terminal_failure"] = {
        "exit_code": int(receipt["exit_code"]),
        "receipt": "run_failure_receipt.json",
        "recorded_at": receipt["recorded_at"],
    }
    checkpoint["updated_at"] = utc_now()
    atomic_json(checkpoint_path, checkpoint)
    return checkpoint


def rebuild_manifest(state_dir: pathlib.Path, checkpoint: dict[str, Any] | None) -> None:
    from draw_auto_research_controller_r1 import IDENTITY_PATH, SPEC_PATH, build_manifest, read_json as controller_read_json

    spec = controller_read_json(SPEC_PATH)
    identity = controller_read_json(IDENTITY_PATH)
    build_manifest(state_dir, checkpoint, spec, identity)


def record_terminal_failure(state_dir: pathlib.Path, exit_code: int, error: str, command: Sequence[str]) -> pathlib.Path:
    receipt_path = write_failure_receipt(state_dir, exit_code, error, command)
    receipt = read_json(receipt_path)
    checkpoint = terminalize_existing_checkpoint(state_dir, receipt)
    rebuild_manifest(state_dir, checkpoint)
    return receipt_path


def run_and_capture(command: Sequence[str], state_dir: pathlib.Path, exit_code_file: pathlib.Path) -> int:
    try:
        completed = subprocess.run(list(command), text=True)
        code = int(completed.returncode)
        if code != 0:
            record_terminal_failure(state_dir, code, f"controller exited {code}", command)
    except Exception as exc:
        code = 1
        try:
            record_terminal_failure(state_dir, code, f"wrapper exception: {exc}", command)
        except Exception as evidence_exc:
            state_dir.mkdir(parents=True, exist_ok=True)
            emergency = {
                "schema_version": "DRAW-AUTO-RUN-FAILURE-EMERGENCY-R1.4",
                "status": "FAILED_EVIDENCE_PATH",
                "exit_code": 1,
                "error": f"wrapper exception: {exc}; evidence failure: {evidence_exc}",
                "recorded_at": utc_now(),
            }
            atomic_json(state_dir / "run_failure_receipt.json", emergency)
    exit_code_file.parent.mkdir(parents=True, exist_ok=True)
    exit_code_file.write_text(str(code) + "\n", encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--exit-code-file", type=pathlib.Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        record_terminal_failure(args.state_dir, 1, "missing controller command", [])
        args.exit_code_file.parent.mkdir(parents=True, exist_ok=True)
        args.exit_code_file.write_text("1\n", encoding="utf-8", newline="\n")
        return 0
    return run_and_capture(command, args.state_dir, args.exit_code_file)


if __name__ == "__main__":
    raise SystemExit(main())
