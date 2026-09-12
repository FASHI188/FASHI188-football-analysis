from __future__ import annotations
import importlib.util,json,unittest
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
P=HERE/'v3_c3_prospective_preflight_v1.py'
spec=importlib.util.spec_from_file_location('c3pf',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class T(unittest.TestCase):
 def test_identity_deterministic(self):
  r={'datetime':'2027-04-12 12:00:00','h':{'title':'A'},'a':{'title':'B'}}
  x=m.identity('EPL',r); self.assertEqual(m.identity_sha(x),m.identity_sha(dict(x)))
 def test_exclusion(self):
  act=datetime(2026,9,12,tzinfo=timezone.utc)
  row={'id':'1','datetime':'2027-04-12 12:00:00','isResult':False,'h':{'title':'A'},'a':{'title':'B'}}
  payload={k:({'dates':[row]},'d') for k in m.LEAGUES}
  inv,_=m.build_inventory(payload,{'1'},act); self.assertEqual(len(inv),0)
 def test_result_rows_not_enrolled(self):
  act=datetime(2026,9,12,tzinfo=timezone.utc)
  row={'id':'1','datetime':'2027-04-12 12:00:00','isResult':True,'h':{'title':'A'},'a':{'title':'B'}}
  payload={k:({'dates':[row]},'d') for k in m.LEAGUES}; inv,_=m.build_inventory(payload,set(),act); self.assertEqual(inv,[])
 def test_past_rows_not_enrolled(self):
  act=datetime(2026,9,12,tzinfo=timezone.utc)
  row={'id':'1','datetime':'2026-09-01 12:00:00','isResult':False,'h':{'title':'A'},'a':{'title':'B'}}
  payload={k:({'dates':[row]},'d') for k in m.LEAGUES}; inv,_=m.build_inventory(payload,set(),act); self.assertEqual(inv,[])
 def test_contract_and_ledger(self):
  c=m.load_contract(HERE/'v3_c3_prospective_confirmation_contract_v1.json'); self.assertEqual(c['cohort_rule']['required_n'],8454)
  e,l=m.load_exclusions(HERE/'v3_c3_consumed_identity_exclusion_ledger_v1.json'); self.assertEqual(len(e),1335); self.assertFalse(l['contains_target_results']); self.assertEqual(l['understat_match_id_count'],1335)
 def test_no_final_cohort_claim(self):
  c=json.loads((HERE/'v3_c3_prospective_confirmation_contract_v1.json').read_text()); self.assertTrue(c['preflight']['initial_inventory_is_not_final_cohort']); self.assertFalse(c['scientific_state']['PROSPECTIVE_PASS'])
if __name__=='__main__': unittest.main()
