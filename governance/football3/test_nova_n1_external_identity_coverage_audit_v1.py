#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_external_identity_coverage_audit_v1.py')
spec = importlib.util.spec_from_file_location('audit', P)
audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)

class Tests(unittest.TestCase):
    def test_team_aliases(self):
        self.assertEqual(audit.canonical_team('Athletic Bilbao'), 'Athletic Club')
        self.assertEqual(audit.canonical_team('Deportivo Alavés'), 'Alaves')
        self.assertEqual(audit.canonical_team('RCD Espanyol de Barcelona'), 'Espanyol')
        self.assertEqual(audit.canonical_team('Real Betis Balompié'), 'Real Betis')

    def test_fail_closed_unmapped_team(self):
        with self.assertRaises(audit.CoverageAuditError):
            audit.canonical_team('Unknown Club')

    def test_date_contract(self):
        self.assertEqual(audit.parse_date('2024-08-15T17:00:00+00:00'), '2024-08-15')
        self.assertEqual(audit.parse_date('15/08/2024'), '2024-08-15')
        with self.assertRaises(audit.CoverageAuditError):
            audit.parse_date('08/15/24')

    def test_safe_parser_does_not_decode_forbidden_fields(self):
        vals = ['123','2024-08-15','Athletic Club','Getafe','SENTINEL','SENTINEL','SENTINEL','SENTINEL','SENTINEL','SENTINEL','SENTINEL','SENTINEL','10','11','8.2','9.1','SENTINEL','SENTINEL','2024']
        safe = audit._decode_safe_fields((','.join(vals)+'\n').encode())
        self.assertNotIn('SENTINEL', safe)
        self.assertEqual(len(safe), len(audit.SAFE_INDEXES))

    def test_quoted_row_fails_closed(self):
        with self.assertRaises(audit.CoverageAuditError):
            audit._decode_safe_fields(b'1,2024-08-15,"Athletic Club",Getafe,0,0,0,0,0,0,0,0,1,1,1,1,0,0,2024\n')

    def test_compare_exact_coverage(self):
        target=[{'fixture_id':'1','kickoff':'2024-08-15T17:00:00+00:00','home_team':'Athletic Club','away_team':'Getafe','home_team_id':'147','away_team_id':'142'}]
        source=[{'date':'2024-08-15','home_team':'Athletic Club','away_team':'Getafe','home_deep':1.0,'away_deep':2.0,'home_ppda':3.0,'away_ppda':4.0}]
        old=audit.TARGET_N; audit.TARGET_N=1
        try:
            coverage, projection=audit.compare_coverage(target, source)
        finally:
            audit.TARGET_N=old
        self.assertEqual(coverage['status'],'IDENTITY_COVERAGE_QUALIFIED')
        self.assertEqual(len(projection),1)
        self.assertNotIn('result',projection[0])

    def test_compare_missing_stops(self):
        target=[{'fixture_id':'1','kickoff':'2024-08-15T17:00:00+00:00','home_team':'Athletic Club','away_team':'Getafe','home_team_id':'147','away_team_id':'142'}]
        coverage,_=audit.compare_coverage(target,[])
        self.assertEqual(coverage['status'],'STOP_DATA_COVERAGE')
        self.assertEqual(coverage['missing_n'],1)

if __name__=='__main__': unittest.main()
