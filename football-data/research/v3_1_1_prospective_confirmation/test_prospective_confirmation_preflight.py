from __future__ import annotations
import hashlib, importlib.util, json, pathlib, unittest

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

    def test_understat_dates_parser_synthetic(self):
        m=load_source()
        html=("<script>var datesData = JSON.parse('\\x7b\\x22dates\\x22\\x3a\\x5b\\x7b\\x22id\\x22\\x3a\\x22123\\x22\\x2c\\x22isResult\\x22\\x3atrue\\x2c\\x22datetime\\x22\\x3a\\x222026-08-30 15\\x3a00\\x3a00\\x22\\x7d\\x5d\\x7d');</script>").encode()
        self.assertEqual(m.dates_list(m.extract_var(html,'datesData'))[0]['id'],'123')

    def test_understat_shot_schema_synthetic(self):
        m=load_source()
        html=("<script>var shotsData = JSON.parse('\\x7b\\x22shots\\x22\\x3a\\x7b\\x22h\\x22\\x3a\\x5b\\x7b\\x22xG\\x22\\x3a\\x220.1\\x22\\x2c\\x22situation\\x22\\x3a\\x22OpenPlay\\x22\\x2c\\x22h_a\\x22\\x3a\\x22h\\x22\\x7d\\x5d\\x2c\\x22a\\x22\\x3a\\x5b\\x7b\\x22xG\\x22\\x3a\\x220.2\\x22\\x2c\\x22situation\\x22\\x3a\\x22FromCorner\\x22\\x2c\\x22h_a\\x22\\x3a\\x22a\\x22\\x7d\\x5d\\x7d\\x7d');</script>").encode()
        r=m.validate_shots(m.shots_list(m.extract_var(html,'shotsData')))
        self.assertEqual(r['shot_n'],2); self.assertEqual(r['sides_seen'],['a','h'])

    def test_unknown_shot_situation_fails_closed(self):
        m=load_source()
        with self.assertRaises(RuntimeError):
            m.validate_shots([{'xG':'0.1','situation':'Unknown','h_a':'h'},{'xG':'0.1','situation':'OpenPlay','h_a':'a'}])

if __name__=='__main__': unittest.main()
