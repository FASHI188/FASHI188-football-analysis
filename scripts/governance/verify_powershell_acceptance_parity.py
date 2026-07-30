#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = ROOT / "scripts" / "governance" / "run_final_repository_governance_acceptance.sh"
POWERSHELL = ROOT / "scripts" / "governance" / "run_final_repository_governance_acceptance.ps1"

REQUIRED_SHARED_TOKENS = [
    "scripts/governance/final_workflow_acceptance.py",
    "--strict",
    "--write-archive-blob-ledger",
    "scripts/governance/verify_consolidated_capabilities.py",
    "--require-archived",
    "--execute-retained",
    "scripts/governance/verify_powershell_acceptance_parity.py",
    "football-data/tests/test_football_v460_engine.py",
    "football-data/tests/test_nested_backtest_v460.py",
    "football-data/tests/test_oof_matrix_calibration_v461.py",
    "football-data/tests/test_v462_bottom_invariants.py",
    "football-data/validation/activate_selective_direction_runtime_v501.py",
    "football-data/validation/activate_mls_d_conditional_runtime_v470.py",
    "football-data/validation/smoke_formal_ev_lomo_gate_v470.py",
    "football-data/validation/smoke_formal_governance_runtime_v470.py",
    "football-data/validation/smoke_selective_direction_runtime_v500.py",
    "football-data/validation/smoke_promoted_mls_d_runtime_v470.py",
    "football-data/validation/smoke_formal_next_season_parameter_runtime_v470.py",
    "football-data/validation/smoke_oof_next_season_runtime_v470.py",
    "football-data/validation/smoke_dynamic_strength_live_input_contract_v470.py",
    "football-data/validation/smoke_dynamic_strength_pre_oof_runtime_v470.py",
    "FINAL_REPOSITORY_GOVERNANCE_ACCEPTANCE_COMPLETE",
]

REQUIRED_POWERSHELL_UTF8_TOKENS = [
    "System.Text.UTF8Encoding",
    "[Console]::InputEncoding",
    "[Console]::OutputEncoding",
    "$OutputEncoding",
    "$env:PYTHONIOENCODING = 'utf-8'",
    "$env:PYTHONUTF8 = '1'",
]


def main() -> int:
    errors: list[str] = []
    if not BASH.is_file():
        errors.append("bash acceptance entry missing")
    if not POWERSHELL.is_file():
        errors.append("PowerShell acceptance entry missing")

    bash_text = BASH.read_text(encoding="utf-8") if BASH.is_file() else ""
    ps_text = POWERSHELL.read_text(encoding="utf-8") if POWERSHELL.is_file() else ""

    for token in REQUIRED_SHARED_TOKENS:
        if token not in bash_text:
            errors.append(f"bash missing required acceptance token: {token}")
        if token not in ps_text:
            errors.append(f"PowerShell missing Bash-equivalent acceptance token: {token}")

    for token in REQUIRED_POWERSHELL_UTF8_TOKENS:
        if token not in ps_text:
            errors.append(f"PowerShell UTF-8 guard missing: {token}")

    forbidden = ["git commit", "git push", "persist_generated_worktree", "persist_files_v474.py"]
    for token in forbidden:
        if token.lower() in ps_text.lower():
            errors.append(f"PowerShell acceptance contains forbidden persistence token: {token}")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "bash_path": BASH.relative_to(ROOT).as_posix(),
        "powershell_path": POWERSHELL.relative_to(ROOT).as_posix(),
        "shared_acceptance_tokens_checked": len(REQUIRED_SHARED_TOKENS),
        "powershell_utf8_guards_checked": len(REQUIRED_POWERSHELL_UTF8_TOKENS),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
