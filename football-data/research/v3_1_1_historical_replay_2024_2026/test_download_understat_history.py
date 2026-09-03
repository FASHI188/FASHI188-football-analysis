from __future__ import annotations
import gzip, importlib.util, json, pathlib, unittest

ROOT=pathlib.Path(__file__).resolve().parent
CONTRACT=ROOT/'HISTORICAL_REPLAY_CONTRACT.json'
SRC=ROOT/'download_understat_history.py'

def load():
    s=importlib.util.spec_from_file_location('hist_dl',SRC); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

class TestHistoricalReplayDownload(unittest.TestCase):
    def test_contract_is_post_view_completed_only(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['status'],'FROZEN_BEFORE_REPLAY_DOWNLOAD_AND_SCORING')
        self.assertTrue(c['target_population']['completed_only'])
        self.assertFalse(c['fresh_confirmation_claim'])
        self.assertEqual(c['target_population']['season_start_years'],[2024,2025])
        self.assertFalse(c['target_population']['selection_by_result_or_performance'])

    def test_candidate_and_formal_heads_frozen(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['candidate']['head'],'a90762a97515f3edd564e8ad204db0d0d4231494')
        self.assertFalse(c['candidate']['candidate_modification_allowed'])
        self.assertEqual(c['formal_v2']['head'],'e12f5d1193be5d81f60301cf34ab2140e11712a9')
        self.assertTrue(c['formal_v2']['must_remain_unchanged'])

    def test_understat_identity_bridge_is_exact(self):
        m=load()
        self.assertEqual(m.LEAGUES['EPL']['league_id'],1)
        self.assertEqual(m.LEAGUES['Serie A']['league_id'],2)
        self.assertEqual(m.LEAGUES['Bundesliga']['league_id'],3)
        self.assertEqual(m.LEAGUES['La liga']['league_id'],4)
        self.assertEqual(m.LEAGUES['Ligue 1']['league_id'],5)

    def test_gzip_decode(self):
        m=load(); raw=b'{"x":1}'; gz=gzip.compress(raw)
        self.assertEqual(m.decode_body(gz,'gzip'),raw)
        self.assertEqual(m.decode_body(gz,''),raw)
        self.assertEqual(m.decode_body(raw,''),raw)

    def test_shot_aggregation_semantics(self):
        m=load()
        obj={'shots':{'h':[{'xG':'0.2','situation':'OpenPlay','h_a':'h'},{'xG':'0.7','situation':'Penalty','h_a':'h'},{'xG':'0.1','situation':'FromCorner','h_a':'h'}],'a':[{'xG':'0.3','situation':'SetPiece','h_a':'a'},{'xG':'0.1','situation':'OpenPlay','h_a':'a'}]}}
        x=m.validate_shots(obj); h=m.side_agg(x['h']); a=m.side_agg(x['a'])
        self.assertAlmostEqual(h['npxg'],0.3); self.assertEqual(h['nonpenalty_shots'],2)
        self.assertAlmostEqual(h['open_play_share'],0.5); self.assertAlmostEqual(h['set_piece_share'],0.5)
        self.assertAlmostEqual(a['npxg'],0.4); self.assertEqual(a['nonpenalty_shots'],2)

    def test_unknown_shot_situation_fails_closed(self):
        m=load()
        with self.assertRaises(m.DataError):
            m.validate_shots({'shots':{'h':[{'xG':'0.1','situation':'Unknown','h_a':'h'}],'a':[{'xG':'0.1','situation':'OpenPlay','h_a':'a'}]}})

    def test_completed_row_identity_and_labels(self):
        m=load()
        row={'id':'123','datetime':'2025-01-02 20:00:00','isResult':True,'h':{'id':'10','title':'H'},'a':{'id':'20','title':'A'},'goals':{'h':'2','a':'2'},'xG':{'h':'1.5','a':'1.2'}}
        self.assertTrue(m.truthy(row['isResult'])); self.assertEqual(m._id(row['h'],'home'),10)
        self.assertEqual(m._goal(row['goals'],'h'),2); self.assertEqual(m.outcome(2,2),'draw')

if __name__=='__main__': unittest.main()
