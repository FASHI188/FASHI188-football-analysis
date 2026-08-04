#!/usr/bin/env bash
set -euo pipefail

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

python -m pip install --disable-pip-version-check -q PyYAML
python -m py_compile \
  scripts/governance/v510_workflow_acceptance.py \
  scripts/governance/verify_consolidated_capabilities.py \
  scripts/governance/verify_powershell_acceptance_parity.py

python scripts/governance/v510_workflow_acceptance.py \
  --strict \
  --write-archive-blob-ledger

python scripts/governance/verify_consolidated_capabilities.py \
  --require-archived \
  --execute-retained

python scripts/governance/verify_powershell_acceptance_parity.py

export PYTHONPATH="football-data/engine:football-data/validation${PYTHONPATH:+:$PYTHONPATH}"
python -m unittest \
  football-data/tests/test_football_v460_engine.py \
  football-data/tests/test_nested_backtest_v460.py \
  football-data/tests/test_oof_matrix_calibration_v461.py \
  football-data/tests/test_v462_bottom_invariants.py -v

python football-data/validation/activate_selective_direction_runtime_v501.py --check
python football-data/validation/activate_mls_d_conditional_runtime_v470.py --check
python football-data/validation/smoke_formal_ev_lomo_gate_v470.py
python football-data/validation/smoke_formal_governance_runtime_v470.py
python football-data/validation/smoke_selective_direction_runtime_v500.py
python football-data/validation/smoke_promoted_mls_d_runtime_v470.py
python football-data/validation/smoke_formal_next_season_parameter_runtime_v470.py
python football-data/validation/smoke_oof_next_season_runtime_v470.py
python football-data/validation/smoke_dynamic_strength_live_input_contract_v470.py
python football-data/validation/smoke_dynamic_strength_pre_oof_runtime_v470.py

echo 'FINAL_REPOSITORY_GOVERNANCE_ACCEPTANCE_COMPLETE'
