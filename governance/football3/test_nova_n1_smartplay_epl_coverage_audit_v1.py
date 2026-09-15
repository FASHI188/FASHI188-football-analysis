#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_smartplay_epl_coverage_audit_v1.py')
spec = importlib.util.spec_from_file_location('audit', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def test_raw_csv_parser_handles_quotes_without_decoding_other_fields(self):
        raw = b'2024-25,123,"Manchester, United",True,secret-result\n'
        fields = audit.split_csv_record_raw(raw)
        self.assertEqual(fields[0], b'2024-25')
        self.assertEqual(fields[2], b'Manchester, United')
        self.assertEqual(fields[4], b'secret-result')

    def test_raw_csv_parser_handles_doubled_quote(self):
        fields = audit.split_csv_record_raw(b'1,"A ""quoted"" value",3\n')
        self.assertEqual(fields[1], b'A "quoted" value')

    def test_unterminated_quote_fails_closed(self):
        with self.assertRaises(audit.CoverageAuditError):
            audit.split_csv_record_raw(b'1,"broken,3\n')

    def test_team_aliases_cover_locked_epl(self):
        cases = {
            'Man City': 'Manchester City',
            'Man Utd': 'Manchester United',
            "Nott'm Forest": 'Nottingham Forest',
            'Spurs': 'Tottenham',
            'Wolves': 'Wolverhampton Wanderers',
            'Brighton and Hove Albion': 'Brighton',
            'Ipswich Town': 'Ipswich',
            'Leicester City': 'Leicester',
        }
        for raw, expected in cases.items():
            self.assertEqual(audit.canonical_team(raw), expected)

    def test_unknown_team_fails_closed(self):
        with self.assertRaises(audit.CoverageAuditError):
            audit.canonical_team('Unknown FC')

    def test_kickoff_normalizes_utc(self):
        self.assertEqual(audit.parse_kickoff('2024-08-16T20:00:00+01:00'), '2024-08-16T19:00:00+00:00')
        self.assertEqual(audit.parse_kickoff('2024-08-16T19:00:00Z'), '2024-08-16T19:00:00+00:00')
        with self.assertRaises(audit.CoverageAuditError):
            audit.parse_kickoff('2024-08-16 19:00:00')

    def test_safe_and_forbidden_contract_disjoint(self):
        self.assertFalse(set(x.casefold() for x in audit.SAFE_COLUMNS) & set(x.casefold() for x in audit.FORBIDDEN_COLUMNS))
        self.assertIn('total_points', audit.FORBIDDEN_COLUMNS)
        self.assertIn('us_team_xg', audit.FORBIDDEN_COLUMNS)

    def test_compare_exact_single_fixture(self):
        target = [{
            'fixture_id':'26602','kickoff':'2024-08-16T19:00:00+00:00','home_team':'Manchester United','away_team':'Fulham',
            'home_team_id':'89','away_team_id':'228','league':'EPL','season':2024,
        }]
        source = [{
            'source_fixture_id':'1','kickoff':'2024-08-16T19:00:00+00:00','home_team':'Man Utd','away_team':'Fulham',
            'h_deep':5.0,'a_deep':4.0,'h_ppda':9.0,'a_ppda':10.0,
        }]
        old = audit.TARGET_N
        audit.TARGET_N = 1
        try:
            coverage, projection = audit.compare_to_locked_identity(target, source)
        finally:
            audit.TARGET_N = old
        self.assertEqual(coverage['status'], 'IDENTITY_COVERAGE_QUALIFIED')
        self.assertEqual(coverage['matched_n'], 1)
        self.assertEqual(len(projection), 1)
        self.assertNotIn('result', projection[0])
        self.assertNotIn('goals', projection[0])

    def test_missing_source_stops(self):
        target = [{
            'fixture_id':'26602','kickoff':'2024-08-16T19:00:00+00:00','home_team':'Manchester United','away_team':'Fulham',
            'home_team_id':'89','away_team_id':'228','league':'EPL','season':2024,
        }]
        coverage, projection = audit.compare_to_locked_identity(target, [])
        self.assertEqual(coverage['status'], 'STOP_DATA_COVERAGE')
        self.assertEqual(coverage['missing_n'], 1)
        self.assertEqual(projection, [])

if __name__ == '__main__':
    unittest.main()
