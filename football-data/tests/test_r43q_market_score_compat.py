from __future__ import annotations
import importlib.util, os, sys
from pathlib import Path
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from components import r43q_market_score_core as new

def load_original():
    p=os.environ.get('R43Q_SOURCE_FILE')
    if not p: raise RuntimeError('R43Q_SOURCE_FILE required')
    spec=importlib.util.spec_from_file_location('r43q_original',p);m=importlib.util.module_from_spec(spec);assert spec and spec.loader;spec.loader.exec_module(m);return m

class QCompat(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.old=load_original()
    def test_source_identity_and_disabled(self):
        self.assertEqual(new.SOURCE_BLOB_SHA,'299b86ed07e49af0b9ec5c7632f519e91e836158');self.assertFalse(new.R43QMarketScoreCore.enabled)
    def test_devig_exact(self):
        odds={'home':1.84,'draw':3.25,'away':4.7};self.assertEqual(new.devig_1x2(odds),self.old.devig_1x2(odds))
    def test_score_matrix_bitwise(self):
        for lh,la in ((1.2,0.9),(2.4,1.3),(0.4,0.5)):
            self.assertTrue(np.array_equal(new.score_matrix(lh,la),self.old.score_matrix(lh,la)))
    def test_settlement_primitives_exact(self):
        for x in [(-1,-0.25,1.9),(0,-0.5,1.8),(2,0.75,2.1)]:self.assertEqual(new.asian_return_for_margin(*x),self.old.asian_return_for_margin(*x))
        for x in [(2,2.25,1.9,True),(1,1.5,2.0,False),(3,3.0,1.8,True)]:self.assertEqual(new.ou_return_for_total(*x),self.old.ou_return_for_total(*x))
    def test_infer_lambdas_exact(self):
        market=new.devig_1x2({'home':1.84,'draw':3.25,'away':4.7});ah={'line':-0.5,'home':1.81,'away':1.94};ou={'line':1.5,'over':1.52,'under':2.48}
        self.assertEqual(new.infer_lambdas(ah,ou,market),self.old.infer_lambdas(ah,ou,market))
    def test_draw_cal_and_matrix_reconstruction_exact(self):
        rows=[]
        for i,(pm,pr,y) in enumerate([(.28,.24,'home'),(.31,.27,'draw'),(.22,.20,'away'),(.30,.25,'draw'),(.26,.23,'home'),(.34,.29,'draw')]):
            market={'home':(1-pm)*.6,'draw':pm,'away':(1-pm)*.4};latent={'home':(1-pr)*.58,'draw':pr,'away':(1-pr)*.42};m=new.score_matrix(1.4+.03*i,1.0+.02*i);rows.append({'market':market,'latent_raw':latent,'matrix_raw':m,'y':y})
        ab_new=new.fit_draw_cal(rows);ab_old=self.old.fit_draw_cal(rows);self.assertEqual(ab_new,ab_old)
        p1,m1=new.apply_draw_cal(rows[-1],ab_new);p2,m2=self.old.apply_draw_cal(rows[-1],ab_old);self.assertEqual(p1,p2);self.assertTrue(np.array_equal(m1,m2))
if __name__=='__main__':unittest.main()
