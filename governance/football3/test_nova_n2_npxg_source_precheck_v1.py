import importlib.util, pathlib, unittest
P=pathlib.Path(__file__).with_name('nova_n2_npxg_source_precheck_v1.py')
spec=importlib.util.spec_from_file_location('n2',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class T(unittest.TestCase):
    def payload(self):
        return {'dates':[{'id':'1','isResult':True,'datetime':'2023-05-01 15:00:00','h':{'id':'10','title':'A'},'a':{'id':'20','title':'B'}}],
                'teams':{'10':{'title':'A','history':[{'date':'2023-05-01 15:00:00','h_a':'h','npxG':'1.2','npxGA':'0.7'}]},
                         '20':{'title':'B','history':[{'date':'2023-05-01 15:00:00','h_a':'a','npxG':'0.7','npxGA':'1.2'}]}}}
    def test_projection(self):
        r=m.project_payload(self.payload(),league='EPL',season=2023,expected_matches=1,release_delay_hours=3)[0]
        self.assertEqual(r['home_npxg'],1.2); self.assertEqual(r['away_npxga'],1.2); self.assertTrue(r['release_at'].endswith('18:00:00Z'))
    def test_reciprocal_fail(self):
        p=self.payload(); p['teams']['20']['history'][0]['npxG']='0.8'
        with self.assertRaises(m.SourcePrecheckError): m.project_payload(p,league='EPL',season=2023,expected_matches=1,release_delay_hours=3)
    def test_completed_count_fail(self):
        with self.assertRaises(m.SourcePrecheckError): m.project_payload(self.payload(),league='EPL',season=2023,expected_matches=2,release_delay_hours=3)
    def test_negative_fail(self):
        p=self.payload(); p['teams']['10']['history'][0]['npxG']='-1'
        with self.assertRaises(m.SourcePrecheckError): m.project_payload(p,league='EPL',season=2023,expected_matches=1,release_delay_hours=3)
if __name__=='__main__': unittest.main()
