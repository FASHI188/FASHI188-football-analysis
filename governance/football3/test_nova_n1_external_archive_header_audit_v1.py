#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_external_archive_header_audit_v1.py')
spec = importlib.util.spec_from_file_location('audit', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def test_locked_schema_complete(self):
        h=['date','home_team','away_team','h_deep','a_deep','h_ppda','a_ppda','h_goals']
        x=audit.classify_header(h)
        self.assertTrue(x['locked_feature_schema_complete'])
        self.assertEqual(x['forbidden_label_columns_present_in_archive_file'], ['h_goals'])
        self.assertEqual(x['data_rows_opened'],0); self.assertEqual(x['label_values_read'],0)
    def test_missing_deep_fails_header_gate(self):
        h=['date','home_team','away_team','h_ppda','a_ppda']
        x=audit.classify_header(h)
        self.assertFalse(x['locked_feature_schema_complete'])
        self.assertEqual(set(x['missing_locked_roles']), {'home_deep','away_deep'})
    def test_header_aliases(self):
        h=['Match Date','HomeTeam','AwayTeam','Deep Home','Deep Away','PPDA Home','PPDA Away']
        self.assertTrue(audit.classify_header(h)['locked_feature_schema_complete'])
    def test_parse_header_only(self):
        raw=b'date,home_team,away_team,h_deep,a_deep,h_ppda,a_ppda,h_goals\n2024-08-15,A,B,1,2,3,4,9\n'
        self.assertEqual(audit.parse_header(raw)[0], 'date')
        self.assertEqual(len(audit.parse_header(raw)),8)

if __name__=='__main__': unittest.main()
