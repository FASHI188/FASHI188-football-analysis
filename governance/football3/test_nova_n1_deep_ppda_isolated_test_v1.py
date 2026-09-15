from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import nova_n1_deep_ppda_isolated_test_v1 as m


class IsolatedTests(unittest.TestCase):
    def test_prefix_does_not_touch_isolated_sentinel(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'labels.jsonl'
            p.write_text('{"fixture_id":"a"}\n{"fixture_id":"b"}\nNOT_JSON_ISOLATED\n',encoding='utf-8')
            self.assertEqual([x['fixture_id'] for x in m.read_prefix(p,2)],['a','b'])

    def test_suffix_skips_development_and_reads_exact_isolated(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'labels.jsonl'
            p.write_text('DEV_NOT_JSON\nDEV_NOT_JSON\n{"fixture_id":"c"}\n{"fixture_id":"d"}\nAFTER_NOT_TOUCHED',encoding='utf-8')
            self.assertEqual([x['fixture_id'] for x in m.read_suffix(p,2,2)],['c','d'])

    def test_same_kickoff_atomic(self):
        rows=[
          {'fixture_id':'1','kickoff':'2024-01-01T12:00:00Z','release_at':'2024-01-01T15:00:00Z','home_team_id':'A','away_team_id':'B','home_ppda':2,'away_ppda':4,'home_deep':8,'away_deep':6},
          {'fixture_id':'2','kickoff':'2024-01-01T12:00:00Z','release_at':'2024-01-01T15:00:00Z','home_team_id':'A','away_team_id':'C','home_ppda':20,'away_ppda':40,'home_deep':80,'away_deep':60},
          {'fixture_id':'3','kickoff':'2024-01-02T12:00:00Z','release_at':'2024-01-02T15:00:00Z','home_team_id':'A','away_team_id':'B','home_ppda':3,'away_ppda':5,'home_deep':9,'away_deep':7}]
        f=m.r2_features(rows)
        self.assertIsNone(f[0][0]); self.assertIsNone(f[1][0])
        self.assertAlmostEqual(f[2][0],11.0); self.assertAlmostEqual(f[2][2],44.0)
        self.assertAlmostEqual(f[2][4],math.log1p(2))

    def test_zero_residual_reproduces_formal(self):
        base=[.52,.27,.21]
        got=m.softmax_offset(base,[1.,-2.],[[0.,0.,0.],[0.,0.,0.]])
        for a,b in zip(base,got): self.assertAlmostEqual(a,b,12)

    def test_rps_normalization(self):
        r=m.metrics([[1.,0.,0.]],[2])
        self.assertAlmostEqual(r['rps'],1.0); self.assertAlmostEqual(r['brier'],2.0)

    def test_paired_effect_positive_when_candidate_improves(self):
        vals=m.effects([[0.4,0.3,0.3]],[[0.5,0.25,0.25]],[0])
        self.assertGreater(vals[0],0.0)


if __name__=='__main__':
    unittest.main()
