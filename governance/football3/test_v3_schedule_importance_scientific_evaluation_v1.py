#!/usr/bin/env python3
import importlib.util,json,tempfile
from pathlib import Path
import numpy as np
H=Path(__file__).resolve().parent
S=importlib.util.spec_from_file_location('ev',H/'run_v3_schedule_importance_scientific_evaluation_v1.py');ev=importlib.util.module_from_spec(S);S.loader.exec_module(ev)

def row(src='2020-21/de.1.json',i=0,h='H',a='A',stage='REGULAR',hr=7,ar=7):
 return {'source_path':src,'row_index':i,'date':'2020-01-01','round':'Matchday 1','round_stage':stage,'round_number':1,'team1':h,'team2':a,
 'home_league_rest_days':hr,'away_league_rest_days':ar,'home_league_congestion_7d':0,'away_league_congestion_7d':0,
 'home_league_congestion_14d':0,'away_league_congestion_14d':0,'home_league_congestion_21d':0,'away_league_congestion_21d':0,
 'home_consecutive_away_before':0,'away_consecutive_away_before':0}

def t_parse():
 r=row();assert ev.season(r)=='2020-21' and ev.comp(r)=='de.1'
def t_base():
 assert ev.basefeat(row(h='Bayern',a='Dortmund'))=={'c=de.1':1.,'h=Bayern':1.,'a=Dortmund':1.}
def t_prep_train_only():
 a=row(src='2019-20/de.1.json',hr=None,ar=8);b=row(src='2019-20/de.1.json',i=1,hr=6,ar=None);s=ev.prep([a,b]);assert s['home_league_rest_days'][0]==6 and s['away_league_rest_days'][0]==8
def t_stage_reference():
 r=row(stage='REGULAR');s=ev.prep([r]);d=ev.candfeat(r,s);assert not any(k.startswith('stage=') or k.startswith('ix=') for k in d)
def t_stage_interaction():
 a=row(stage='CHAMPIONSHIP_SPLIT',hr=5);b=row(i=1,stage='REGULAR',hr=9);s=ev.prep([a,b]);d=ev.candfeat(a,s);assert d['stage=CHAMPIONSHIP_SPLIT']==1 and any(k.startswith('ix=') for k in d)
def t_metrics():
 y=np.array([0,1,2]);p=np.eye(3)*.98+.02/3;m=ev.metr(p,y);assert np.all(m['hit']==1) and np.all(m['ll']<.02)
def t_summary():
 b={'ll':np.array([1.,1.]),'br':np.array([.8,.8]),'rps':np.array([.4,.4]),'hit':np.array([0,1])};c={'ll':np.array([.9,.9]),'br':np.array([.7,.7]),'rps':np.array([.3,.3]),'hit':np.array([1,1])};s=ev.summary(b,c);assert s['logloss']['delta']<0 and s['top1']['candidate_hits']==2
def t_bootstrap():
 p=['a','a','b','b','c','c'];d=np.array([-.1,-.1,-.2,-.2,-.3,-.3]);x=ev.bootstrap(p,d);y=ev.bootstrap(p,d);assert x==y and x['upper']<0 and x['replicates']==5000
def t_gate_pass():
 pool={'logloss':{'delta':-.01},'brier':{'delta':-.01},'rps':{'delta':-.01},'top1':{'candidate_hits':100,'baseline_hits':99}};g,d=ev.gate(pool,[-.01,-.01,-.01,-.01,.001],{'upper':-.001},[{'delta':-.01},{'delta':-.02},{'delta':.001}]);assert all(g.values()) and d=='DEVELOPMENT_SIGNAL_PASS_RESEARCH_ONLY_NO_PROMOTION'
def t_gate_fail():
 pool={'logloss':{'delta':.001},'brier':{'delta':-.01},'rps':{'delta':-.01},'top1':{'candidate_hits':100,'baseline_hits':99}};g,d=ev.gate(pool,[-.01,-.01,-.01,-.01,.001],{'upper':-.001},[{'delta':-.01},{'delta':-.02},{'delta':.001}]);assert not g['G1_primary_pooled_logloss'] and d=='SCIENTIFIC_EVALUATION_FAIL_CLOSE_NO_RETUNE'
def t_labels():
 td=Path(tempfile.mkdtemp());p=td/'2020-21';p.mkdir();ms=[{'date':'2020-01-01','round':'Matchday 1','team1':'A','team2':'B','score':{'ft':[2,1]}},{'date':'2020-01-01','round':'Matchday 1','team1':'C','team2':'D','score':{'ft':[0,0]}},{'date':'2020-01-01','round':'Matchday 1','team1':'E','team2':'F','score':{'ft':[1,3]}}];(p/'de.1.json').write_text(json.dumps({'matches':ms}));rr=[row(i=i,h=m['team1'],a=m['team2']) for i,m in enumerate(ms)];y,c=ev.labels(rr,td);assert [y[('2020-21/de.1.json',i)] for i in range(3)]==['H','D','A'] and c=={'H':1,'D':1,'A':1}
def t_label_identity_stop():
 td=Path(tempfile.mkdtemp());p=td/'2020-21';p.mkdir();(p/'de.1.json').write_text(json.dumps({'matches':[{'date':'2020-01-01','round':'Matchday 1','team1':'X','team2':'B','score':{'ft':[1,0]}}]}));
 try:ev.labels([row()],td);assert False
 except ev.Stop as e:assert str(e)=='STOP_LABEL_IDENTITY'
def t_fit():
 tr=[];y={}
 for i in range(90):
  r=row(src='2019-20/de.1.json',i=i,h=f'H{i%6}',a=f'A{i%7}',stage='REGULAR' if i%5 else 'CHAMPIONSHIP_SPLIT',hr=4+i%5,ar=5+i%4);tr.append(r);y[(r['source_path'],i)]=ev.INV[i%3]
 te=[]
 for i in range(15):
  r=row(src='2020-21/de.1.json',i=i,h=f'H{i%6}',a=f'A{i%7}',hr=5,ar=6);te.append(r);y[(r['source_path'],i)]=ev.INV[i%3]
 for cand in (False,True):
  p=ev.predict(tr,te,y,cand);assert p.shape==(15,3) and np.allclose(p.sum(1),1)
T=[t_parse,t_base,t_prep_train_only,t_stage_reference,t_stage_interaction,t_metrics,t_summary,t_bootstrap,t_gate_pass,t_gate_fail,t_labels,t_label_identity_stop,t_fit]
if __name__=='__main__':
 for f in T:f();print('PASS',f.__name__)
 print(f'{len(T)}/{len(T)} PASS')
