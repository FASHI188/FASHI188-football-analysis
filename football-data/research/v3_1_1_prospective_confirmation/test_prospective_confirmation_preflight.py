from __future__ import annotations
import gzip, hashlib, importlib.util, json, pathlib, unittest

ROOT=pathlib.Path(__file__).resolve().parent
CONTRACT=ROOT/'PROSPECTIVE_CONFIRMATION_CONTRACT.json'
STATE=ROOT/'FROZEN_CANDIDATE_STATE.json'
SRC=ROOT/'understat_source_preflight.py'

def load_source():
    s=importlib.util.spec_from_file_location('source_preflight',SRC)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def canon(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

class TestProspectiveConfirmation(unittest.TestCase):
    def test_contract_is_frozen_before_enrollment(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['status'],'FROZEN_BEFORE_TARGET_ENROLLMENT')
        self.assertEqual(c['target_cutoff_utc'],'2026-09-02T17:00:00Z')
        self.assertEqual(c['required_n'],1603)
        self.assertEqual(c['frozen_candidate']['head'],'a90762a97515f3edd564e8ad204db0d0d4231494')
        self.assertFalse(c['frozen_candidate']['candidate_modification_allowed'])

    def test_population_and_spillover_are_mechanical(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['target_population']['competitions'],['EPL','La liga','Bundesliga','Serie A','Ligue 1'])
        self.assertEqual(c['target_population']['season_order'],['2026/27','2027/28'])
        self.assertFalse(c['target_population']['selection_by_result_or_performance'])
        self.assertFalse(c['target_population']['competition_expansion_allowed'])
        self.assertIn('Chronological first 1603',c['target_population']['enrollment'])

    def test_no_interim_scoring_or_reconstruction(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['label_policy']['interim_labels_for_scoring'],'FORBIDDEN')
        self.assertEqual(c['label_policy']['interim_aggregate_performance'],'FORBIDDEN')
        self.assertEqual(c['prediction_freeze']['post_kickoff_reconstruction'],'FORBIDDEN')
        self.assertEqual(c['final_scoring']['n_must_equal'],1603)
        self.assertTrue(c['final_scoring']['no_tuning_after_result'])

    def test_frozen_state_canonical_hash(self):
        s=json.loads(STATE.read_text())
        got=hashlib.sha256(canon(s['payload'])).hexdigest()
        self.assertEqual(got,s['canonical_payload_sha256'])
        self.assertEqual(got,'22332a2053451a1a749b6dbc2818fa12cb21d68295510097a87dbbdb20d9f8ea')
        self.assertEqual(s['payload']['training_seasons'],[2018,2019,2020,2021,2022])
        self.assertEqual(s['payload']['residual_scale'],0.25)
        self.assertEqual(s['payload']['lambda'],48.0)

    def test_frozen_state_feature_contract(self):
        s=json.loads(STATE.read_text())['payload']
        self.assertEqual(len(s['feature_names']),14)
        self.assertEqual(s['active_columns'],[0,1,2,3,5,7,8,9,10,11,12,13])
        self.assertEqual(len(s['beta']),12)
        self.assertIn('favorite_minus_weak_rolling_open_play_share',s['feature_names'])
        self.assertIn('favorite_minus_weak_rolling_set_piece_share',s['feature_names'])

    def test_ajax_transport_reference_is_pinned(self):
        m=load_source()
        self.assertEqual(m.UNDERSTAT_API_REFERENCE_COMMIT,'d1252d9734e94ba98c681d2e41d467f1edb7aaf5')
        self.assertEqual(m.AJAX_HEADERS['X-Requested-With'],'XMLHttpRequest')
        self.assertEqual(m.LEAGUES['La liga'],'La_Liga')

    def test_gzip_ajax_body_decodes_by_header_and_magic(self):
        m=load_source(); raw=b'{"dates":[]}' ; gz=gzip.compress(raw)
        self.assertEqual(m.decode_http_body(gz,'gzip'),raw)
        self.assertEqual(m.decode_http_body(gz,''),raw)
        self.assertEqual(m.decode_http_body(raw,''),raw)

    def test_ajax_league_payload_synthetic(self):
        m=load_source()
        obj={'teams':{},'players':[],'dates':[{'id':'123','isResult':True,'datetime':'2026-08-30 15:00:00','h':{'title':'A'},'a':{'title':'B'}}]}
        rows=m.league_dates(obj)
        self.assertEqual(rows[0]['id'],'123')
        self.assertEqual(m.row_identity(rows[0])['home_team'],'A')

    def test_ajax_shot_schema_synthetic_and_unknown_fails_closed(self):
        m=load_source()
        obj={'shots':{'h':[{'xG':'0.1','situation':'OpenPlay','h_a':'h'}],'a':[{'xG':'0.2','situation':'FromCorner','h_a':'a'}]}}
        r=m.validate_shots(m.match_shots(obj))
        self.assertEqual(r['shot_n'],2); self.assertEqual(r['sides_seen'],['a','h'])
        with self.assertRaises(RuntimeError):
            m.validate_shots([{'xG':'0.1','situation':'Unknown','h_a':'h'},{'xG':'0.1','situation':'OpenPlay','h_a':'a'}])

if __name__=='__main__': unittest.main()
