import base64, importlib.util, json, math, pathlib, re, sys, unittest, zlib
P=pathlib.Path(__file__).with_name('historical_fusion_v3_1.py')
C=pathlib.Path(__file__).with_name('V3_1_RESEARCH_CONTRACT.json')
spec=importlib.util.spec_from_file_location('v31',P); v31=importlib.util.module_from_spec(spec); sys.modules['v31']=v31; spec.loader.exec_module(v31)

def source_bytes():
    t=P.read_text(); m=re.search(r"_PAYLOAD=b'''(.*?)'''",t,re.S)
    return zlib.decompress(base64.b85decode(m.group(1).encode())) if m else t.encode()

class T(unittest.TestCase):
    def test_power_reference(self): self.assertEqual(v31.power_required_n(0.003517010047029627,0.10105899397420998),6481)
    def test_power_invalid(self): self.assertIsNone(v31.power_required_n(0.0,0.1)); self.assertIsNone(v31.power_required_n(-.1,.1))
    def test_reconstruct_weak_exact(self):
        b=[.5,.3,.2]; q=v31.reconstruct(b,0,2,.2); self.assertEqual(q[2],b[2]); self.assertAlmostEqual(sum(q),1.0,14)
    def test_reconstruct_zero_is_base(self):
        b=[.5,.3,.2]; q=v31.reconstruct(b,0,2,0.0); self.assertTrue(all(abs(a-c)<1e-14 for a,c in zip(b,q)))
    def test_fallback_exact(self):
        class U:
            pvec=staticmethod(lambda r:list(r['p']))
            oriented_feature_vector=staticmethod(lambda r,p:(0,2,[1.0]))
        class M:
            beta=[.4]
            transform=lambda self,v:[2.0]
        r={'p':[.5,.3,.2],'fallback_exact_v1':True}; self.assertEqual(v31.predict_variant(U,M(),r,{},'V3.1-A',{'residual_scale':.25}),r['p'])
    def test_a_preserves_weak(self):
        class U:
            pvec=staticmethod(lambda r:list(r['p']))
            oriented_feature_vector=staticmethod(lambda r,p:(0,2,[1.0]))
        class M:
            beta=[.4]
            transform=lambda self,v:[2.0]
        r={'p':[.5,.3,.2],'fallback_exact_v1':False}; q=v31.predict_variant(U,M(),r,{},'V3.1-A',{'residual_scale':.25}); self.assertEqual(q[2],.2); self.assertAlmostEqual(sum(q),1.0,14)
    def test_b_default_v2_outside_window(self):
        class U:
            pvec=staticmethod(lambda r:list(r['p']))
            oriented_feature_vector=staticmethod(lambda r,p:(0,2,[1.0]))
        class M:
            beta=[.4]
            transform=lambda self,v:[2.0]
        r={'p':[.75,.15,.10],'fallback_exact_v1':False}; q=v31.predict_variant(U,M(),r,{},'V3.1-B',{'entropy_min':.9,'entropy_max':1.03,'gap_max':None,'residual_scale':.25}); self.assertEqual(q,r['p'])
    def test_c_cap_bounds_logit_change(self):
        class U:
            pvec=staticmethod(lambda r:list(r['p']))
            oriented_feature_vector=staticmethod(lambda r,p:(0,2,[1.0]))
        class M:
            beta=[.4]
            transform=lambda self,v:[2.0]
        r={'p':[.5,.3,.2],'fallback_exact_v1':False}; q=v31.predict_variant(U,M(),r,{},'V3.1-C',{'residual_scale':1.0,'abs_logit_cap':.1}); off=math.log(.5/.3); new=math.log(q[0]/q[1]); self.assertLessEqual(abs(new-off),.100000000001); self.assertEqual(q[2],.2)
    def test_unknown_candidate_error(self):
        class U:
            pvec=staticmethod(lambda r:list(r['p']))
            oriented_feature_vector=staticmethod(lambda r,p:(0,2,[1.0]))
        class M:
            beta=[.4]
            transform=lambda self,v:[2.0]
        with self.assertRaises(v31.ResearchError): v31.predict_variant(U,M(),{'p':[.5,.3,.2],'fallback_exact_v1':False},{},'BAD',{})
    def test_candidate_grid_counts(self):
        c=json.loads(C.read_text()); d={x['id']:x for x in c['candidates']}; self.assertEqual(len(v31.param_options(d['V3.1-A'])),6); self.assertEqual(len(v31.param_options(d['V3.1-B'])),480); self.assertEqual(len(v31.param_options(d['V3.1-C'])),40)
    def test_selection_holdout_separation(self):
        c=json.loads(C.read_text()); self.assertEqual(c['family_selection']['score_seasons'],[2020,2021,2022]); self.assertTrue(c['family_selection']['selected_family_frozen_before_2023_score']); self.assertEqual(c['data_roles']['2023'],'POST_VIEW_CANDIDATE_FIXED_RESEARCH_HOLDOUT')
    def test_source_same_kickoff_and_no_patches(self):
        s=source_bytes().decode(); self.assertIn("release=ko+timedelta(hours=3)",s); self.assertIn("while queue and queue[0][0]<=ko",s); self.assertNotIn('scoreline_patch',s); self.assertNotIn('fixed_draw_boost =',s)

if __name__=='__main__': unittest.main()
