$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
    scripts/governance/verify_v510_capabilities.py `
    scripts/governance/verify_powershell_acceptance_parity.py

Invoke-Python scripts/governance/v510_workflow_acceptance.py `
    --strict `
    --write-archive-blob-ledger

Invoke-Python scripts/governance/verify_v510_capabilities.py `
    --require-archived `
    --execute-retained

Invoke-Python scripts/governance/verify_powershell_acceptance_parity.py

Write-Output 'FINAL_REPOSITORY_GOVERNANCE_ACCEPTANCE_COMPLETE'
