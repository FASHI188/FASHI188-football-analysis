#!/usr/bin/env python3
"""Execute the V5.0.2 public-lineup pilot and always persist an audit receipt.

The orchestrator returns success after writing its receipt so the workflow can
commit diagnostic evidence even when an internal stage fails. A later workflow
gate reads the receipt and fails when status != PASS.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
OUT = FOOTBALL / "manifests" / "player_xi_pilot_execution_v502_status.json"


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
        "stdout_tail": process.stdout[-16000:],
        "stderr_tail": process.stderr[-16000:],
    }


def main() -> int:
    python = "python"
    stages: list[dict[str, Any]] = []

    syntax = run_stage(
        "syntax",
        [
            python,
            "-m",
            "py_compile",
            "football-data/engine/ingest_transfermarkt_lineups_v502.py",
            "football-data/validation/probable_lineup_route_v502.py",
            "football-data/validation/player_xi_data_readiness_v502.py",
            "football-data/validation/lineup_match_identity_audit_v502.py",
            "football-data/tests/test_player_xi_v502.py",
        ],
    )
    stages.append(syntax)

    tests = run_stage(
        "leakage_regression_tests",
        [python, "football-data/tests/test_player_xi_v502.py"],
        enabled=syntax["status"] == "PASS",
    )
    stages.append(tests)

    ingest = run_stage(
        "public_lineup_ingest",
        [
            python,
            "football-data/engine/ingest_transfermarkt_lineups_v502.py",
            "--min-source-season",
            "2021",
            "--print-summary",
        ],
        enabled=tests["status"] == "PASS",
    )
    stages.append(ingest)

    validation = run_stage(
        "probable_lineup_shadow_validation",
        [
            python,
            "football-data/validation/probable_lineup_route_v502.py",
            "--print-summary",
        ],
        enabled=ingest["status"] == "PASS",
    )
    stages.append(validation)

    readiness = run_stage(
        "player_xi_readiness",
        [
            python,
            "football-data/validation/player_xi_data_readiness_v502.py",
            "--write-receipt",
            "--print-summary",
        ],
        enabled=validation["status"] == "PASS",
    )
    stages.append(readiness)

    identity = run_stage(
        "lineup_match_identity_bridge",
        [
            python,
            "football-data/validation/lineup_match_identity_audit_v502.py",
            "--print-summary",
        ],
        enabled=readiness["status"] == "PASS",
    )
    stages.append(identity)

    overall = "PASS" if all(stage["status"] == "PASS" for stage in stages) else "FAIL"
    report = {
        "schema_version": "V5.0.2-player-xi-pilot-execution-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "stages": stages,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Execution receipt only. PASS permits identity-linked lineup-only shadow research, not formal probability influence.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": overall,
        "stages": [
            {"name": stage["name"], "status": stage["status"], "return_code": stage["return_code"]}
            for stage in stages
        ],
        "receipt": OUT.relative_to(ROOT).as_posix(),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
