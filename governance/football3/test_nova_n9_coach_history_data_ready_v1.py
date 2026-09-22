from __future__ import annotations
import unittest
from datetime import datetime, timezone
from nova_n9_coach_history_data_ready_v1 import norm_name, sim, manager_events, state_at, bind_targets

class N9CoachTests(unittest.TestCase):
    def test_name_normalization(self):
        self.assertGreater(sim('Paris Saint Germain','Paris Saint-Germain'),.95)
        self.assertGreater(sim('Bayern Munich','Bayern München'),.70)
        self.assertEqual(norm_name('Società Sportiva Lazio S.p.A.'),'lazio')
        self.assertEqual(norm_name('Club Atlético de Madrid S.A.D.'),'atleticomadrid')
        self.assertEqual(norm_name('RasenBallsport Leipzig'),norm_name('RB Leipzig'))
        self.assertGreater(sim('Rennes','Stade Rennais Football Club'),.70)
        self.assertGreater(sim('Brest','Stade Brestois 29'),.60)
    def test_target_manager_direct_excluded_by_48h(self):
        games=[{'game_id':'1','date':'2022-08-01','home_club_id':'10','away_club_id':'20','home_club_manager_name':'M1','away_club_manager_name':'M2'}]
        ev,_=manager_events(games,48)
        cutoff=datetime(2022,8,1,20,tzinfo=timezone.utc)
        self.assertFalse(state_at(ev[10],cutoff)['available'])
    def test_prior_manager_becomes_available(self):
        games=[{'game_id':'1','date':'2022-08-01','home_club_id':'10','away_club_id':'20','home_club_manager_name':'M1','away_club_manager_name':'M2'}]
        ev,_=manager_events(games,48)
        cutoff=datetime(2022,8,4,12,tzinfo=timezone.utc)
        s=state_at(ev[10],cutoff); self.assertTrue(s['available']); self.assertEqual(s['manager'],'M1')
    def test_bind_uses_names_not_results(self):
        targets=[
          {'fixture_id':'u1','date':'2022-12-29','league':'La_liga','home_name':'Atletico Madrid','away_name':'Elche'},
          {'fixture_id':'u2','date':'2023-04-30','league':'Ligue_1','home_name':'Strasbourg','away_name':'Lyon'}]
        games=[
          {'game_id':'9','competition_id':'ES1','season':'2022','date':'2022-12-30','home_club_name':'Club Atlético de Madrid S.A.D.','away_club_name':'Elche Club de Fútbol S.A.D.','home_club_id':'1','away_club_id':'2'},
          {'game_id':'10','competition_id':'ES1','season':'2022','date':'2022-12-29','home_club_name':'Real Betis Balompié','away_club_name':'Athletic Bilbao','home_club_id':'3','away_club_id':'4'},
          {'game_id':'19','competition_id':'FR1','season':'2022','date':'2023-04-28','home_club_name':'Racing Club de Strasbourg Alsace','away_club_name':'Olympique Lyon','home_club_id':'5','away_club_id':'6'},
          {'game_id':'20','competition_id':'FR1','season':'2022','date':'2023-04-29','home_club_name':'LOSC Lille','away_club_name':'AC Ajaccio','home_club_id':'7','away_club_id':'8'}]
        cfg={'minimum_side_similarity':.45,'minimum_pair_similarity':1.15,'minimum_margin':.08,'maximum_calendar_date_offset_days':2}
        b,d=bind_targets(targets,games,{'La_liga':'ES1','Ligue_1':'FR1'},cfg)
        self.assertEqual(len(b),2); self.assertFalse(d)
        self.assertEqual([x['tm_game_id'] for x in b],[9,19])
        self.assertEqual([x['identity_date_offset_days'] for x in b],[1,-2])
    def test_ambiguous_binding_fails_closed(self):
        targets=[{'fixture_id':'u1','date':'2022-08-05','league':'EPL','home_name':'Alpha','away_name':'Beta'}]
        games=[
          {'game_id':'1','competition_id':'GB1','season':'2022','date':'2022-08-05','home_club_name':'Alpha','away_club_name':'Beta','home_club_id':'1','away_club_id':'2'},
          {'game_id':'2','competition_id':'GB1','season':'2022','date':'2022-08-05','home_club_name':'Alpha','away_club_name':'Beta','home_club_id':'3','away_club_id':'4'}]
        cfg={'minimum_side_similarity':.45,'minimum_pair_similarity':1.15,'minimum_margin':.08,'maximum_calendar_date_offset_days':2}
        b,d=bind_targets(targets,games,{'EPL':'GB1'},cfg); self.assertEqual(len(b),0); self.assertEqual(len(d),1)
    def test_norm_is_deterministic(self):
        self.assertEqual(norm_name('1. FC Köln'),norm_name('1 FC Koln'))

if __name__=='__main__': unittest.main()
