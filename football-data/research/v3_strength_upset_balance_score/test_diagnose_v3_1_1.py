from __future__ import annotations
import importlib.util, json, pathlib, unittest

ROOT=pathlib.Path(__file__).resolve().parent
SRC=ROOT/"diagnose_v3_1_1.py"
CONTRACT=ROOT/"DIAGNOSTIC_CONTRACT.json"

def load():
    s=importlib.util.spec_from_file_location("diag",SRC)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

class TestDiagnostic(unittest.TestCase):
    def test_contract_post_view_and_frozen_sources(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c["status"],"FROZEN_DIAGNOSTIC_DEFINITIONS_POST_VIEW")
        self.assertEqual(c["classification"],"POST_VIEW_DIAGNOSTIC_ONLY")
        self.assertFalse(c["fresh_confirmation"])
        self.assertEqual(c["source"]["fixture_n"],3504)
        self.assertEqual(c["source"]["formal_v2_head"],"e12f5d1193be5d81f60301cf34ab2140e11712a9")
        self.assertEqual(c["source"]["v3_1_1_stress_head"],"b32944f1e0a973dd5ff3a2e87d72333d85d27051")

    def test_region_conditional_shape_invariant_under_region_scaling(self):
        m=load(); a=[[1.0 for _ in range(15)] for _ in range(15)]
        s=sum(sum(r) for r in a); a=[[x/s for x in r] for r in a]
        b=[[0.0 for _ in range(15)] for _ in range(15)]; scales={0:1.2,1:.8,2:1.0}
        for reg in range(3):
            for i,j in m.REG[reg]: b[i][j]=a[i][j]*scales[reg]
        z=sum(sum(r) for r in b); b=[[x/z for x in r] for r in b]
        self.assertLessEqual(m.conditional_shape_diff(a,b),1e-15)

    def test_outcome(self):
        m=load(); self.assertEqual(m.outcome(2,1),0); self.assertEqual(m.outcome(1,1),1); self.assertEqual(m.outcome(0,2),2)

    def test_binary_metrics(self):
        m=load(); q=m.binary_metrics([0,1,0,1],[.1,.8,.2,.7])
        self.assertEqual(q["n"],4); self.assertGreater(q["roc_auc"],.9); self.assertGreater(q["average_precision"],.9)

    def test_score_rank(self):
        m=load(); a=[[0.0 for _ in range(15)] for _ in range(15)]; a[1][1]=.5; a[0][0]=.3; a[2][1]=.2
        self.assertEqual(m.score_rank(a,1,1),1); self.assertEqual(m.score_rank(a,0,0),2); self.assertEqual(m.score_rank(a,2,1),3)

    def test_strength_definition_does_not_use_result(self):
        c=json.loads(CONTRACT.read_text())
        self.assertIn("Actual result never defines",c["definitions"]["strength_reference"])
        self.assertIn("all fixtures",c["definitions"]["upset_event"])

if __name__=="__main__": unittest.main()
