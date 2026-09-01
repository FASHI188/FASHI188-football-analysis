from __future__ import annotations
import unittest
from datetime import datetime,timezone
import pit_roster_data as pr

class PITRosterTests(unittest.TestCase):
    def test_exact_team_news_boundary(self):
        page='''<html><head><script type="application/ld+json">{"datePublished":"2024-02-02T17:22:00+00:00","dateModified":"2025-06-04T23:44:00+01:00"}</script></head><body><div>FT 2 2</div><h1>Preview: Wolfsburg vs Hoffenheim - prediction, team news, lineups</h1><h2>Team News</h2><p>Wolfsburg will be without Kevin Wimmer due to injury.</p><p>Wolfsburg possible starting lineup: Casteels; Maehle, Lacroix, Jenz, Rogerio; Svanberg, Vranckx; Cerny, Majer, Paredes; Wind</p><p>Hoffenheim possible starting lineup: Baumann; Kabak, Brooks, Akpoguma; Kaderabek, Promel, Stach, Nsoki; Kramaric; Weghorst, Beier</p><h2>We say: Wolfsburg 1-1 Hoffenheim</h2></body></html>'''
        sec=pr.extract_team_news(page);self.assertIsNotNone(sec);raw,text=sec;self.assertNotIn('FT 2 2',text);self.assertNotIn('1-1',text);lu=pr.parse_lineups(text,'VfL Wolfsburg','TSG 1899 Hoffenheim');self.assertIsNotNone(lu);self.assertEqual(len(lu[0]),11);self.assertEqual(len(lu[1]),11);self.assertLess(pr.publication_time(page),datetime(2024,2,4,14,30,tzinfo=timezone.utc))
    def test_url_pair_matching(self):
        urls=['https://www.sportsmole.co.uk/football/wolfsburg/preview/wolfsburg-vs-hoffenheim-prediction-team-news-lineups_535813.html','https://www.sportsmole.co.uk/football/mainz/preview/mainz-vs-hoffenheim-prediction-team-news-lineups_x.html'];z=pr.candidate_urls(urls,'VfL Wolfsburg','TSG 1899 Hoffenheim');self.assertEqual(len(z),1);self.assertIn('wolfsburg-vs-hoffenheim',z[0])
    def test_no_probability_invention_contract(self):
        p={'source_name':'X','player_id':'1','starting_probability':None,'expected_minutes':None};self.assertIsNone(p['starting_probability']);self.assertIsNone(p['expected_minutes'])
    def test_timestamps_require_timezone(self):
        with self.assertRaises(pr.PITRosterError):pr.dt('2024-02-02T17:22:00')
if __name__=='__main__':unittest.main()
