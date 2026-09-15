#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_smartplay_epl_coverage_audit_v2.py')
spec = importlib.util.spec_from_file_location('audit_v2', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def _target(self, kickoff='2024-08-16T19:00:00+00:00'):
        return [{
            'fixture_id':'26602','kickoff':kickoff,'home_team':'Manchester United','away_team':'Fulham',
            'home_team_id':'89','away_team_id':'228','league':'EPL','season':2024,
        }]

    def _source(self, kickoff='2024-08-16T19:00:00+00:00'):
        return [{
            'source_fixture_id':'1','kickoff':kickoff,'home_team':'Man Utd','away_team':'Fulham',
            'h_deep':5.0,'a_deep':4.0,'h_ppda':9.0,'a_ppda':10.0,
        }]

    def test_exact_identity_qualifies(self):
        old = audit.v1.TARGET_N; audit.v1.TARGET_N = 1
        try:
            coverage, projection = audit.compare_to_locked_identity(self._target(), self._source())
        finally:
            audit.v1.TARGET_N = old
        self.assertEqual(coverage['status'], 'IDENTITY_COVERAGE_QUALIFIED')
        self.assertEqual(coverage['kickoff_mismatch_n'], 0)
        self.assertEqual(projection[0]['kickoff'], '2024-08-16T19:00:00+00:00')

    def test_same_date_team_with_revised_kickoff_qualifies_and_retains_target(self):
        old = audit.v1.TARGET_N; audit.v1.TARGET_N = 1
        try:
            coverage, projection = audit.compare_to_locked_identity(
                self._target('2025-02-19T15:00:00+00:00'),
                self._source('2025-02-19T19:30:00+00:00'),
            )
        finally:
            audit.v1.TARGET_N = old
        self.assertEqual(coverage['status'], 'IDENTITY_COVERAGE_QUALIFIED')
        self.assertEqual(coverage['kickoff_mismatch_n'], 1)
        self.assertEqual(coverage['max_abs_kickoff_delta_seconds'], 16200)
        self.assertEqual(coverage['kickoff_drift_exceeded_n'], 0)
        self.assertEqual(projection[0]['kickoff'], '2025-02-19T15:00:00+00:00')

    def test_kickoff_drift_over_six_hours_stops(self):
        old = audit.v1.TARGET_N; audit.v1.TARGET_N = 1
        try:
            coverage, projection = audit.compare_to_locked_identity(
                self._target('2024-08-16T12:00:00+00:00'),
                self._source('2024-08-16T19:00:01+00:00'),
            )
        finally:
            audit.v1.TARGET_N = old
        self.assertEqual(coverage['status'], 'STOP_DATA_COVERAGE')
        self.assertEqual(coverage['kickoff_drift_exceeded_n'], 1)
        self.assertEqual(projection, [])

    def test_different_calendar_day_does_not_match(self):
        old = audit.v1.TARGET_N; audit.v1.TARGET_N = 1
        try:
            coverage, projection = audit.compare_to_locked_identity(
                self._target('2024-08-16T23:30:00+00:00'),
                self._source('2024-08-17T00:30:00+00:00'),
            )
        finally:
            audit.v1.TARGET_N = old
        self.assertEqual(coverage['status'], 'STOP_DATA_COVERAGE')
        self.assertEqual(coverage['missing_n'], 1)
        self.assertEqual(coverage['extra_n'], 1)
        self.assertEqual(projection, [])

    def test_duplicate_day_team_key_fails_closed(self):
        target = self._target() * 2
        with self.assertRaises(audit.IdentityCoverageError):
            audit.compare_to_locked_identity(target, self._source())

if __name__ == '__main__':
    unittest.main()
