from __future__ import annotations
import unittest
from datetime import datetime, timezone
import pit_schedule_audit as m

class TestPitScheduleAudit(unittest.TestCase):
    def test_norm_aliases(self):
        self.assertEqual(m.norm('FC Bayern München'), 'bayern munich')
        self.assertEqual(m.norm('Paris Saint-Germain FC'), 'psg')
        self.assertEqual(m.norm('Internazionale'), 'inter')

    def test_parse_unplayed_and_finished(self):
        text='''= Test 2020/21\n[Sat Sep/12]\n  16.00  Alpha FC               -  Beta FC\n[Sat Sep/19]\n  Gamma FC  2-1 (1-0)  Delta FC\n'''
        rows=m.parse_snapshot(text,2020)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['date'],'2020-09-12')
        self.assertEqual(rows[0]['home_key'],'alpha')
        self.assertEqual(rows[1]['away_key'],'delta')

    def test_year_rollover(self):
        rows=m.parse_snapshot('[Sat Jan/2]\n  A FC               -  B FC\n',2020)
        self.assertEqual(rows[0]['date'],'2021-01-02')

    def test_commit_before_strict(self):
        tl=[(datetime(2020,1,1,tzinfo=timezone.utc),'a'),(datetime(2020,2,1,tzinfo=timezone.utc),'b')]
        self.assertEqual(m.commit_before(tl,datetime(2020,2,1,tzinfo=timezone.utc))[1],'a')
        self.assertEqual(m.commit_before(tl,datetime(2020,2,2,tzinfo=timezone.utc))[1],'b')

    def test_season_token(self):
        self.assertEqual(m.season_token(2020),'2020-21')
        self.assertEqual(m.season_token(2022),'2022-23')

if __name__=='__main__': unittest.main()
