$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Force UTF-8 end-to-end on Windows PowerShell / PowerShell 7 so Python JSON and
# Kambi/non-ASCII output cannot fall back to the system ANSI/GBK code page.
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Invoke-Python {
    & python @args
    if ($LASTEXITCODE -ne 0) {
        throw "python $($args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

Invoke-Python -m pip install --disable-pip-version-check -q PyYAML
Invoke-Python -m py_compile `
    scripts/governance/v510_workflow_acceptance.py `
    scripts/governance/verify_consolidated_capabilities.py `
    scripts/governance/verify_powershell_acceptance_parity.py

Invoke-Python scripts/governance/v510_workflow_acceptance.py `
    --strict `
    --write-archive-blob-ledger

Invoke-Python scripts/governance/verify_consolidated_capabilities.py `
    --require-archived `
    --execute-retained

Invoke-Python scripts/governance/verify_powershell_acceptance_parity.py

$EnginePath = 'football-data/engine'
$ValidationPath = 'football-data/validation'
$Separator = [System.IO.Path]::PathSeparator
if ([string]::IsNullOrEmpty($env:PYTHONPATH)) {
    $env:PYTHONPATH = "$EnginePath$Separator$ValidationPath"
} else {
    $env:PYTHONPATH = "$EnginePath$Separator$ValidationPath$Separator$($env:PYTHONPATH)"
}

Invoke-Python -m unittest `
    football-data/tests/test_football_v460_engine.py `
    football-data/tests/test_nested_backtest_v460.py `
    football-data/tests/test_oof_matrix_calibration_v461.py `
    football-data/tests/test_v462_bottom_invariants.py -v

Invoke-Python football-data/validation/activate_selective_direction_runtime_v501.py --check
Invoke-Python football-data/validation/activate_mls_d_conditional_runtime_v470.py --check
Invoke-Python football-data/validation/smoke_formal_ev_lomo_gate_v470.py
Invoke-Python football-data/validation/smoke_formal_governance_runtime_v470.py
Invoke-Python football-data/validation/smoke_selective_direction_runtime_v500.py
Invoke-Python football-data/validation/smoke_promoted_mls_d_runtime_v470.py
Invoke-Python football-data/validation/smoke_formal_next_season_parameter_runtime_v470.py
Invoke-Python football-data/validation/smoke_oof_next_season_runtime_v470.py
Invoke-Python football-data/validation/smoke_dynamic_strength_live_input_contract_v470.py
Invoke-Python football-data/validation/smoke_dynamic_strength_pre_oof_runtime_v470.py

Write-Output 'FINAL_REPOSITORY_GOVERNANCE_ACCEPTANCE_COMPLETE'
