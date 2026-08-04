#!/usr/bin/env bash
set -euo pipefail

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

python -m pip install --disable-pip-version-check -q PyYAML
python -m py_compile \
  scripts/governance/v510_workflow_acceptance.py \
  scripts/governance/verify_v510_capabilities.py \
  scripts/governance/verify_powershell_acceptance_parity.py

python scripts/governance/v510_workflow_acceptance.py \
  --strict \
  --write-archive-blob-ledger

python scripts/governance/verify_v510_capabilities.py \
  --require-archived \
  --execute-retained

python scripts/governance/verify_powershell_acceptance_parity.py

echo 'FINAL_REPOSITORY_GOVERNANCE_ACCEPTANCE_COMPLETE'
