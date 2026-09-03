#!/usr/bin/env python3
import importlib.util,pathlib,unittest,json
ROOT=pathlib.Path(__file__).parent
S=importlib.util.spec_from_file_location('m',ROOT/'external_evidence_dominance_validation.py');m=importlib.util.module_from_spec(S);S.loader.exec_module(m)
class T(unittest.TestCase):
 def test_norm(self): self.assertAlmostEqual(sum(m.norm([2,3,4])),1.0)
 def test_top1(self): self.assertEqual(m.top1([.4,.35,.25]),0)
 def test_contract_zero_parameter(self):
  c=json.loads((ROOT/'EXTERNAL_EVIDENCE_DOMINANCE_VALIDATION_CONTRACT.json').read_text()); a=c['frozen_zero_parameter_arbiter']; self.assertEqual(a['numeric_thresholds'],'NONE');self.assertEqual(a['learned_parameters'],'NONE');self.assertEqual(a['tv_cutoff'],'NONE')
 def test_frozen_second_cohort(self):
  c=json.loads((ROOT/'EXTERNAL_EVIDENCE_DOMINANCE_VALIDATION_CONTRACT.json').read_text()); self.assertEqual(len(c['untouched_validation_data']['leagues']),6);self.assertEqual(len(c['untouched_validation_data']['seasons']),2)
 def test_symmetric_rule_example(self):
  b=[.40,.35,.25];q=[.30,.45,.25];bt=m.top1(b);t=m.top1(q);self.assertGreaterEqual(q[t]-q[bt],b[bt]-b[t])
if __name__=='__main__':unittest.main()
