import importlib.util,sys,unittest,tempfile,sqlite3,pathlib,math,json
from datetime import datetime,timezone,timedelta

P=pathlib.Path(__file__).with_name('historical_fusion_v3.py')
spec=importlib.util.spec_from_file_location('v3_under_test',P);v3=importlib.util.module_from_spec(spec);sys.modules['v3_under_test']=v3;spec.loader.exec_module(v3)

class V3Tests(unittest.TestCase):
 def row(self,fallback=False):
  return {'fixture_id':'understat:1','home_goals':1,'away_goals':1,'fallback_exact_v1':fallback,'cold_start_bucket':'established','fusion':{'p_home':.4,'p_draw':.3,'p_away':.3,'mean_home':1.3,'mean_away':1.1},'v1':{'p_home':.4,'p_draw':.3,'p_away':.3,'mean_home':1.3,'mean_away':1.1},'xg':{'p_home':.4,'p_draw':.3,'p_away':.3,'mean_home':1.3,'mean_away':1.1},'league':'EPL','season':2018,'kickoff':'2018-01-01T12:00:00+00:00'}
 def test_poisson_cells_normalize(self):
  s=sum(v3.pois_cell(1.4,1.1,i,j) for i in range(15) for j in range(15));self.assertAlmostEqual(s,1.0,12)
 def test_score_cell_mixture_identity(self):
  r=self.row();self.assertAlmostEqual(v3.score_cell(r,1,1),v3.pois_cell(1.3,1.1,1,1),12)
 def test_metrics(self):
  r=self.row();m=v3.metrics([r],lambda x:v3.pvec(x));self.assertAlmostEqual(m['logloss'],-math.log(.3),12);self.assertEqual(m['top1'],0.0)
 def test_regime_onehot(self):
  x=v3.regime_features(self.row());self.assertEqual(len(x),15);self.assertEqual(sum(x[-5:]),1.0)
 def test_invalid_probability_fail_closed(self):
  r=self.row();r['fusion']['p_draw']=-.1
  with self.assertRaises(v3.V3Error):v3.regime_features(r)
 def test_teamstate_half_life(self):
  s=v3.TeamState();t=datetime(2020,1,1,tzinfo=timezone.utc);s.add(t,{'x':2.0});w,z=s.snap(t+timedelta(days=90));self.assertAlmostEqual(w,.5,10);self.assertAlmostEqual(z['x'],1.0,10)
 def test_teamstate_time_reversal_fail_closed(self):
  s=v3.TeamState();t=datetime(2020,1,1,tzinfo=timezone.utc);s.add(t,{'x':1.0})
  with self.assertRaises(v3.V3Error):s.snap(t-timedelta(seconds=1))
 def test_fallback_exact_formal_v2(self):
  r=self.row(True);m=v3.Model('B',12.0,[0.0]*15,[1.0]*15,[0.0]*30,list(range(15)));self.assertEqual(v3.predict_model(m,r,{}),v3.pvec(r))
 def test_unknown_candidate_fail_closed(self):
  r=self.row(False);m=v3.Model('Z',1.0,[],[],[],[])
  with self.assertRaises(v3.V3Error):v3.predict_model(m,r,{})
 def test_solver(self):
  x=v3.solve([[2.0,0.0],[0.0,4.0]],[6.0,8.0]);self.assertAlmostEqual(x[0],3.0);self.assertAlmostEqual(x[1],2.0)
 def test_same_kickoff_predict_before_release(self):
  with tempfile.TemporaryDirectory() as td:
   db=pathlib.Path(td)/'x.db';c=sqlite3.connect(db)
   c.execute('create table general_game_stats(id integer,fid integer,date text,league text,season integer,h_id integer,a_id integer,h_goals integer,a_goals integer,h_xg real,a_xg real,h_shot integer,a_shot integer)')
   c.execute('create table game_events(match_id integer,h_a text,situation text,xG real)')
   mid=1
   for day in (20,23,26,29):
    c.execute('insert into general_game_stats values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(mid,1000+mid,f'2017-12-{day:02d} 12:00:00','EPL',2017,1,2,1,0,1.1,.8,2,2))
    for ha,xg in [('h',.5),('h',.6),('a',.3),('a',.5)]:c.execute('insert into game_events values(?,?,?,?)',(mid,ha,'OpenPlay',xg))
    mid+=1
   for fid in (2001,2002):
    c.execute('insert into general_game_stats values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(mid,fid,'2018-01-02 12:00:00','EPL',2018,1,2,2,1,1.5,1.0,2,2))
    for ha,xg in [('h',.7),('h',.8),('a',.4),('a',.6)]:c.execute('insert into game_events values(?,?,?,?)',(mid,ha,'OpenPlay',xg))
    mid+=1
   c.commit();c.close()
   rows=[{'fixture_id':'understat:2001'},{'fixture_id':'understat:2002'}]
   f,meta=v3.load_process_features(db,rows);self.assertTrue(f['understat:2001']['valid']);self.assertEqual(f['understat:2001']['values'],f['understat:2002']['values'])

if __name__=='__main__':unittest.main()
