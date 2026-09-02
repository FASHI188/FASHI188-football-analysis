from __future__ import annotations
import importlib.util, json, pathlib, tempfile, unittest
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('inv',HERE/'sealed_inventory.py'); inv=importlib.util.module_from_spec(spec); spec.loader.exec_module(inv)
CONTRACT=HERE/'SEALED_INVENTORY_CONTRACT.json'

class Tests(unittest.TestCase):
 def setUp(self): self.c=inv.load_contract(CONTRACT)
 def test_power_exact(self):
  p=inv.compute_power(self.c); self.assertEqual(p['recomputed_required_n'],6481)
 def test_terminal_bound_stops_before_labels(self):
  with tempfile.TemporaryDirectory() as d:
   r=inv.run_gate(self.c,d); self.assertEqual(r['status'],'NO_ELIGIBLE_SEALED_SHOT_EVENT_COHORT'); self.assertFalse(r['target_labels_opened']); self.assertFalse((pathlib.Path(d)/'sealed_label_vault.jsonl').exists())
 def test_trusted_physical_split_and_no_feature_label(self):
  row={'fixture_id':'x:1','competition':'EPL','season':'2026/27','home':'A','away':'B','kickoff':'2026-08-20T18:00:00+00:00','release_at':'2026-08-20T21:00:00+00:00','home_goals':1,'away_goals':0,'events':[{'team':'home','xg':.2,'is_penalty':False,'context':'OPEN_PLAY','event_time_seconds':100.0},{'team':'away','xg':.1,'is_penalty':False,'context':'SET_PIECE','event_time_seconds':200.0}]}
  src={'sha256':'a'*64,'field_semantics_sha256':'b'*64}
  with tempfile.TemporaryDirectory() as d:
   r=inv.trusted_split([row],pathlib.Path(d)/'features',pathlib.Path(d)/'vault',src); self.assertEqual(r['n'],1); self.assertFalse(r['labels_returned'])
   ft=(pathlib.Path(d)/'features/feature_pit_store.jsonl').read_text(); self.assertNotIn('home_goals',ft); self.assertNotIn('away_goals',ft)
   self.assertTrue((pathlib.Path(d)/'vault/sealed_label_vault.jsonl').exists())
 def test_identity_conflict_fails(self):
  base={'fixture_id':'x:1','competition':'EPL','season':'2026/27','home':'A','away':'B','kickoff':'2026-08-20T18:00:00+00:00','release_at':'2026-08-20T21:00:00+00:00','home_goals':1,'away_goals':0,'events':[]}
  bad=dict(base); bad['away']='C'
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(inv.InventoryError): inv.trusted_split([base,bad],pathlib.Path(d)/'f',pathlib.Path(d)/'v',{'sha256':'a'*64,'field_semantics_sha256':'b'*64})
 def test_pit_strict_and_same_kickoff(self):
  rows=[{'fixture_id':'h1','release_at':'2026-08-20T21:00:00+00:00'},{'fixture_id':'h2','release_at':'2026-08-21T18:00:00+00:00'}]
  self.assertEqual([r['fixture_id'] for r in inv.pit_eligible_history(rows,'2026-08-21T18:00:00+00:00')],['h1'])
  b=inv.batch_freeze_order([{'fixture_id':'a','kickoff':'k'},{'fixture_id':'b','kickoff':'k'}]); self.assertEqual(b[0]['predict_before_release'],['a','b'])
 def test_event_semantics_fail_closed(self):
  with self.assertRaises(inv.InventoryError): inv.validate_event({'team':'home','xg':.1,'is_penalty':False,'context':'UNKNOWN','event_time_seconds':1})

if __name__=='__main__': unittest.main(verbosity=2)
