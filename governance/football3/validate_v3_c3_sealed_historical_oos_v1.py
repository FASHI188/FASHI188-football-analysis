from __future__ import annotations
import json
from pathlib import Path
root=Path(__file__).resolve().parents[2]
c=json.loads((root/'governance/football3/v3_c3_sealed_historical_oos_contract_v1.json').read_text())
a=json.loads((root/'governance/football3/v3_c3_historical_oos_coverage_audit_v1.json').read_text())
assert c['classification']=='SEALED_HISTORICAL_OOS_CONFIRMATION'
assert c['paused_future_confirmation']['future_enrollment_allowed'] is False
assert c['decision']['labels_opened']==0 and a['labels_opened']==0
assert c['decision']['terminal']=='STOP_DATA_COVERAGE' and a['decision']=='STOP_DATA_COVERAGE'
assert a['usable_n'] < a['required_n']
assert all(c['forbidden_changes'].values())
print(json.dumps({'status':'PASS_ZERO_LABEL_COVERAGE_STOP','usable_n':a['usable_n'],'required_n':a['required_n'],'labels_opened':0},sort_keys=True))
