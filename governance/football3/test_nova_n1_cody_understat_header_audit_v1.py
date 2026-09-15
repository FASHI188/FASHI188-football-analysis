#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_cody_understat_header_audit_v1.py')
spec = importlib.util.spec_from_file_location('audit', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def test_candidate_contract(self):
        c=audit.CANDIDATE
        self.assertEqual(c['declared_license'],'MIT')
        self.assertEqual(c['target_leagues'], ['Bundesliga','Serie A','Ligue 1'])
        self.assertIn('2024/25', c['declared_seasons'])
    def test_documented_general_game_header_qualifies(self):
        h=['id','fid','h_id','a_id','date','league_id','season','h_goals','a_goals','team_h','team_a','h_xg','a_xg','h_w','h_d','h_l','league','h_shot','a_shot','h_shotOnTarget','a_shotOnTarget','h_deep','a_deep','h_ppda','a_ppda']
        x=audit.base.classify_header(h)
        self.assertTrue(x['locked_feature_schema_complete'])
        self.assertEqual(set(x['forbidden_label_columns_present_in_archive_file']), {'h_goals','a_goals'})
        self.assertEqual(x['data_rows_opened'],0)
        self.assertEqual(x['label_values_read'],0)

if __name__=='__main__': unittest.main()
