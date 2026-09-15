#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_smartplay_epl_header_audit_v1.py')
spec = importlib.util.spec_from_file_location('audit', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def test_required_header_qualifies_with_documented_columns(self):
        header = [
            'season','fixture','team_name','opponent_team','is_home','match_date',
            'us_ppda','us_opp_ppda','us_deep','us_deep_allowed',
            'total_points','team_h_score','team_a_score','us_team_xG'
        ]
        x = audit.classify_header(header)
        self.assertTrue(x['locked_feature_schema_complete'])
        self.assertEqual(x['missing_required_roles'], [])
        self.assertEqual(x['ambiguous_required_roles'], [])
        self.assertIn('total_points', x['forbidden_result_or_label_columns_present_in_dataset'])
        self.assertIn('team_h_score', x['forbidden_result_or_label_columns_present_in_dataset'])
        self.assertEqual(x['data_rows_parsed'], 0)
        self.assertEqual(x['label_values_read'], 0)
        self.assertEqual(x['result_values_read'], 0)

    def test_redundant_aliases_choose_preferred_deterministically(self):
        header = [
            'season','fixture','team_name','us_opponent','opponent_team','is_home','match_date','kickoff_time',
            'us_ppda','us_opp_ppda','us_deep','us_deep_allowed'
        ]
        x = audit.classify_header(header)
        self.assertTrue(x['locked_feature_schema_complete'])
        self.assertEqual(x['bindings']['opponent']['column'], 'opponent_team')
        self.assertEqual(x['bindings']['match_time']['column'], 'kickoff_time')
        self.assertEqual(x['redundant_aliases_present']['opponent'], ['us_opponent'])
        self.assertEqual(x['redundant_aliases_present']['match_time'], ['match_date'])

    def test_alias_fallback_when_preferred_missing(self):
        header = [
            'season','fixture','team_name','us_opponent','is_home','match_date',
            'us_ppda','us_opp_ppda','us_deep','us_deep_allowed'
        ]
        x = audit.classify_header(header)
        self.assertTrue(x['locked_feature_schema_complete'])
        self.assertEqual(x['bindings']['opponent']['column'], 'us_opponent')
        self.assertEqual(x['bindings']['match_time']['column'], 'match_date')

    def test_missing_feature_fails_closed(self):
        header = ['season','fixture','team_name','opponent_team','is_home','match_date','us_ppda','us_deep']
        x = audit.classify_header(header)
        self.assertFalse(x['locked_feature_schema_complete'])
        self.assertEqual(set(x['missing_required_roles']), {'opponent_ppda','opponent_deep'})

    def test_duplicate_selected_alias_fails_closed(self):
        header = [
            'season','fixture','team_name','opponent_team','opponent_team','is_home','kickoff_time',
            'us_ppda','us_opp_ppda','us_deep','us_deep_allowed'
        ]
        x = audit.classify_header(header)
        self.assertFalse(x['locked_feature_schema_complete'])
        self.assertEqual(x['ambiguous_required_roles'], ['opponent'])

    def test_parse_first_record_only(self):
        raw = (
            b'season,fixture,team_name,opponent_team,is_home,match_date,us_ppda,us_opp_ppda,us_deep,us_deep_allowed,total_points\n'
            b'2024-25,1,A,B,True,2024-08-16,9.0,10.0,8,7,99\n'
        )
        header = audit.parse_first_csv_record(raw)
        self.assertEqual(header[0], 'season')
        self.assertEqual(header[-1], 'total_points')
        self.assertNotIn('99', header)

    def test_report_never_claims_label_reads(self):
        prefix = b'season,fixture,team_name,opponent_team,is_home,match_date,us_ppda,us_opp_ppda,us_deep,us_deep_allowed,total_points\n2024-25,1,A,B,True,2024-08-16,9,10,8,7,99\n'
        report = audit.build_report(prefix, {'prefix_bytes_read': len(prefix)})
        self.assertEqual(report['status'], 'HEADER_SCHEMA_QUALIFIED')
        self.assertFalse(report['raw_dataset_persisted'])
        self.assertEqual(report['data_rows_parsed'], 0)
        self.assertEqual(report['label_values_read'], 0)
        self.assertFalse(report['test_result_vault_opened'])
        self.assertFalse(report['test_labels_read'])

if __name__ == '__main__':
    unittest.main()
