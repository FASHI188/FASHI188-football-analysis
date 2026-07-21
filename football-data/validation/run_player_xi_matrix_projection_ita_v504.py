#!/usr/bin/env python3
"""Run the ITA V5.0.4 player-XI matrix projection and always persist diagnostics."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
OUT = FOOTBALL / "manifests" / "player_xi_matrix_projection_ita_v504_execution.json"
RESULT = FOOTBALL / "manifests" / "player_xi_matrix_projection_ita_v504_status.json"


def run_stage(name: str, command: list[str], *, enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return {
            "name": name,
            "status": "SKIPPED_UPSTREAM_FAILURE",
            "return_code": None,
            "command": command,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    process = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "name": name,
        "status": "PASS" if process.returncode == 0 else "FAIL",
        "return_code": process.returncode,
        "command": command,
        "stdout_tail": process.stdout[-24000:],
        "stderr_tail": process.stderr[-24000:],
    }


def main() -> int:
    stages: list[dict[str, Any]] = []
    syntax = run_stage(
        "syntax",
        [
            "python",
            "-m",
            "py_compile",
            "football-data/validation/player_xi_matrix_projection_ita_v504.py",
            "football-data/tests/test_player_xi_matrix_projection_v504.py",
        ],
    )
    stages.append(syntax)
    tests = run_stage(
        "projection_invariant_tests",
        ["python", "football-data/tests/test_player_xi_matrix_projection_v504.py"],
        enabled=syntax["status"] == "PASS",
    )
    stages.append(tests)
    projection = run_stage(
        "ita_unified_matrix_oof",
        [
            "python",
            "football-data/validation/player_xi_matrix_projection_ita_v504.py",
            "--print-summary",
        ],
        enabled=tests["status"] == "PASS",
    )
    stages.append(projection)
    overall = "PASS" if all(stage["status"] == "PASS" for stage in stages) else "FAIL"
    result_status = None
    if RESULT.is_file():
        try:
            result_status = json.loads(RESULT.read_text(encoding="utf-8")).get("status")
        except Exception:
            result_status = "RESULT_RECEIPT_UNREADABLE"
    report = {
        "schema_version": "V5.0.4-player-xi-matrix-projection-execution-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "result_receipt_status": result_status,
        "stages": stages,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Execution diagnostics only. The workflow commits this receipt before enforcing success or failure.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": overall,
        "result_receipt_status": result_status,
        "stages": [
            {
                "name": stage["name"],
                "status": stage["status"],
                "return_code": stage["return_code"],
            }
            for stage in stages
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
