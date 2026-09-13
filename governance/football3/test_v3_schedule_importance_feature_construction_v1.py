#!/usr/bin/env python3
import importlib.util,json,tempfile,hashlib
from pathlib import Path
H=Path(__file__).resolve().parent
S=importlib.util.spec_from_file_location('v',H/'validate_v3_schedule_importance_feature_construction_v1.py')
v=importlib.util.module_from_spec(S);S.loader.exec_module(v)
C=json.loads((H/'v3_schedule_importance_feature_construction_contract_v1.json').read_text())

def row(i,date,h,a,round='Matchday 1',src='2020-21/en.1.json',time='15:00'):
 return {'source_path':src,'row_index':i,'round':round,'date':date,'time':time,'team1':h,'team2':a}

def write(rows):
 f=tempfile.NamedTemporaryFile('w',delete=False,encoding='utf-8');
 for r in rows:f.write(json.dumps(r,separators=(',',':'))+'\n')
 f.close();return Path(f.name)

def with_contract(rows):
 p=write(rows); c=json.loads(json.dumps(C)); c['parent']['sanitized_sha256']=v.sha256_file(p); c['parent']['sanitized_rows']=len(rows); c['output_contract']['row_count']=len(rows); return p,c

def t_rounds():
 assert v.parse_round('Matchday 12')==('REGULAR',12);assert v.parse_round('Championship, Matchday 23')==('CHAMPIONSHIP_SPLIT',23);assert v.parse_round('Relegation, Matchday 24')==('RELEGATION_SPLIT',24);assert v.parse_round('Final')==('PLAYOFF_FINAL',None)
def t_day_pit_and_counts():
 rows=[row(0,'2020-01-01','A','B'),row(1,'2020-01-05','C','A'),row(2,'2020-01-08','A','D')];p,c=with_contract(rows); rr=v.load_rows(p,c);o=v.build(c,rr);z=o[2];assert z['home_league_rest_days']==3 and z['home_league_congestion_7d']==2 and z['home_league_congestion_14d']==2 and z['home_consecutive_away_before']==1
def t_same_day_invisible():
 rows=[row(0,'2020-01-01','A','B'),row(1,'2020-01-01','C','D')];p,c=with_contract(rows);o=v.build(c,v.load_rows(p,c));assert o[0]['home_league_rest_days'] is None and o[1]['home_league_rest_days'] is None
def t_season_reset():
 rows=[row(0,'2020-05-01','A','B',src='2019-20/en.1.json'),row(0,'2020-08-01','C','A',src='2020-21/en.1.json')];p,c=with_contract(rows);o=v.build(c,v.load_rows(p,c));assert o[1]['away_league_rest_days'] is None and o[1]['away_consecutive_away_before']==0
def t_away_streak_resets():
 rows=[row(0,'2020-01-01','X','A'),row(1,'2020-01-05','Y','A'),row(2,'2020-01-10','A','Z'),row(3,'2020-01-15','Q','A')];p,c=with_contract(rows);o=v.build(c,v.load_rows(p,c));assert o[2]['home_consecutive_away_before']==2 and o[3]['away_consecutive_away_before']==0
def t_exact_7_window():
 rows=[row(0,'2020-01-01','A','B'),row(1,'2020-01-08','A','C'),row(2,'2020-01-09','A','D')];p,c=with_contract(rows);o=v.build(c,v.load_rows(p,c));assert o[1]['home_league_congestion_7d']==1 and o[2]['home_league_congestion_7d']==1 and o[2]['home_league_congestion_14d']==2
def t_forbidden_key_stop():
 r=row(0,'2020-01-01','A','B');r['score']={'ft':[1,0]};p,c=with_contract([r]);
 try:v.load_rows(p,c);assert False
 except v.Stop as e:assert str(e)=='STOP_SANITIZED_KEYSET'
def t_same_team_same_day_stop():
 rows=[row(0,'2020-01-01','A','B'),row(1,'2020-01-01','A','C')];p,c=with_contract(rows)
 try:v.load_rows(p,c);assert False
 except v.Stop as e:assert str(e)=='STOP_TEAM_MULTIPLE_MATCHES_SAME_DATE'
def t_unknown_round_stop():
 rows=[row(0,'2020-01-01','A','B',round='Mystery')];p,c=with_contract(rows)
 try:v.load_rows(p,c);assert False
 except v.Stop as e:assert str(e)=='STOP_UNKNOWN_ROUND_STAGE'
def t_contract_boundaries():
 assert C['feature_contract']['coverage_scope']=='LEAGUE_ONLY';assert C['input_contract']['standings_pressure']=='NOT_AUTHORIZED';assert C['input_contract']['training'] is False and C['input_contract']['tuning'] is False;assert C['feature_contract']['same_day_policy'].startswith('ONLY_PRIOR_MATCHES')
T=[t_rounds,t_day_pit_and_counts,t_same_day_invisible,t_season_reset,t_away_streak_resets,t_exact_7_window,t_forbidden_key_stop,t_same_team_same_day_stop,t_unknown_round_stop,t_contract_boundaries]
if __name__=='__main__':
 for f in T:f();print('PASS',f.__name__)
 print(f'{len(T)}/{len(T)} PASS')
