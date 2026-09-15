#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_cody_big3_coverage_audit_v1.py')
spec = importlib.util.spec_from_file_location('audit', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def test_source_date_and_season(self):
        self.assertEqual(audit.source_date('2024-08-24 13:30:00'), '2024-08-24')
        self.assertEqual(audit.parse_season('2024'), 2024)
        self.assertEqual(audit.parse_season('2024.0'), 2024)

    def test_ppda_numeric_and_dict(self):
        self.assertAlmostEqual(audit.ppda_value('ppda', '12.5'), 12.5)
        self.assertAlmostEqual(audit.ppda_value('ppda', "{'att': 250, 'def': 20}"), 12.5)
        with self.assertRaises(audit.CoverageAuditError):
            audit.ppda_value('ppda', "{'att': 250, 'def': 0}")

    def test_league_aliases(self):
        self.assertEqual(audit.canonical_league('Bundesliga'), 'Bundesliga')
        self.assertEqual(audit.canonical_league('Serie_A'), 'Serie A')
        self.assertEqual(audit.canonical_league('Ligue 1'), 'Ligue 1')
        self.assertIsNone(audit.canonical_league('EPL'))

    def test_date_team_identity_retains_target_kickoff(self):
        old_targets = audit.TARGETS
        old_total = audit.TARGET_TOTAL
        audit.TARGETS = {'Bundesliga': 1}
        audit.TARGET_TOTAL = 1
        target=[{
            'fixture_id':'x1','league':'Bundesliga','season':2024,
            'kickoff':'2024-08-24T13:30:00+00:00','home_team':'Augsburg','away_team':'Werder Bremen',
            'home_team_id':'1','away_team_id':'2'
        }]
        source=[{
            'date':'2024-08-24','league':'Bundesliga','season':2024,
            'home_team':'Augsburg','away_team':'Werder Bremen',
            'h_deep':3.0,'a_deep':4.0,'h_ppda':10.0,'a_ppda':11.0
        }]
        try:
            coverage, projection = audit.compare_to_locked_identity(target, source)
        finally:
            audit.TARGETS = old_targets
            audit.TARGET_TOTAL = old_total
        self.assertEqual(coverage['status'], 'IDENTITY_COVERAGE_QUALIFIED')
        self.assertEqual(projection[0]['kickoff'], '2024-08-24T13:30:00+00:00')

    def test_different_date_stops(self):
        old_targets = audit.TARGETS
        old_total = audit.TARGET_TOTAL
        audit.TARGETS = {'Ligue 1': 1}
        audit.TARGET_TOTAL = 1
        target=[{
            'fixture_id':'x1','league':'Ligue 1','season':2024,
            'kickoff':'2024-08-18T15:00:00+00:00','home_team':'Angers','away_team':'Lens',
            'home_team_id':'1','away_team_id':'2'
        }]
        source=[{
            'date':'2024-08-19','league':'Ligue 1','season':2024,
            'home_team':'Angers','away_team':'Lens',
            'h_deep':3.0,'a_deep':4.0,'h_ppda':10.0,'a_ppda':11.0
        }]
        try:
            coverage, projection = audit.compare_to_locked_identity(target, source)
        finally:
            audit.TARGETS = old_targets
            audit.TARGET_TOTAL = old_total
        self.assertEqual(coverage['status'], 'STOP_DATA_COVERAGE')
        self.assertEqual(coverage['missing_n'], 1)
        self.assertEqual(coverage['extra_n'], 1)
        self.assertEqual(projection, [])

    def test_safe_forbidden_contract_disjoint(self):
        self.assertFalse({x.casefold() for x in audit.SAFE_COLUMNS} & {x.casefold() for x in audit.FORBIDDEN_COLUMNS})
        self.assertEqual(audit.EXPECTED_ARCHIVE_SHA256, '2d77fa250bdac756bdb15cc14bb1b6c2e3a8f925acd32f120318fb015245c4c0')

if __name__=='__main__': unittest.main()
