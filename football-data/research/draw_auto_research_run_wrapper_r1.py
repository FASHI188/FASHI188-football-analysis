#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
from typing import Sequence


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def append_jsonl(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def write_failure_receipt(state_dir: pathlib.Path, exit_code: int, error: str, command: Sequence[str]) -> pathlib.Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "DRAW-AUTO-RUN-FAILURE-R1.4", "status": "FAILED",
        "exit_code": int(exit_code), "error": error, "command": list(command),
        "recorded_at": utc_now(), "data_status": "VIEWED_DEVELOPMENT_DATA",
        "checkpoint_present": (state_dir / "checkpoint.json").is_file(),
        "formal_weight": 0, "repository_writeback": 0,
    }
    path = state_dir / "run_failure_receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_jsonl(state_dir / "ledger.jsonl", {"record_type": "RUN_FAILURE", **receipt})
    return path


def run_and_capture(command: Sequence[str], state_dir: pathlib.Path, exit_code_file: pathlib.Path) -> int:
    try:
        completed = subprocess.run(list(command), text=True)
        code = int(completed.returncode)
        if code != 0:
            write_failure_receipt(state_dir, code, f"controller exited {code}", command)
    except Exception as exc:
        code = 1
        write_failure_receipt(state_dir, code, f"wrapper exception: {exc}", command)
    exit_code_file.parent.mkdir(parents=True, exist_ok=True)
    exit_code_file.write_text(str(code) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--exit-code-file", type=pathlib.Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        write_failure_receipt(args.state_dir, 1, "missing controller command", [])
        args.exit_code_file.write_text("1\n")
        return 0
    return run_and_capture(command, args.state_dir, args.exit_code_file)


if __name__ == "__main__":
    raise SystemExit(main())
