import importlib.util,json,tempfile
from pathlib import Path
P=Path(__file__).with_name('run_v3_discipline_prior_postview_v1.py');s=importlib.util.spec_from_file_location('m',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def test_contract():
 c=m.check_contract(Path(__file__).with_name('v3_discipline_prior_postview_contract_v1.json'));assert not c['independent'] and not c['promotion'] and not c['referee_allowed']
def test_prior_expanding_and_global_fallback():
 q=m.Prior();assert q.feat('h','a')==[0.0]*6;q.add('h',{'yellow_cards':2,'red_cards':0,'fouls':10});f=q.feat('h','a');assert f[:3]==[2,0,10] and f[3:]==[2,0,10];q.add('a',{'yellow_cards':4,'red_cards':1,'fouls':14});f=q.feat('h','a');assert f==[2,0,10,4,1,14]
def test_missing_field_does_not_become_zero_observation():
 q=m.Prior();q.add('h',{'yellow_cards':2,'red_cards':None,'fouls':10});q.add('h',{'yellow_cards':4,'red_cards':1,'fouls':12});assert q.mean('h','yellow_cards')==3 and q.mean('h','red_cards')==1
def test_delta_sign():
 a={'logloss':1,'brier':2,'rps':3,'top1_accuracy':.5};b={'logloss':.9,'brier':1.8,'rps':2.9,'top1_accuracy':.51};d=m.delta(a,b);assert d['logloss']<0 and d['top1_accuracy']>0
def test_bootstrap_deterministic():
 rows=[]
 for i in range(10):rows.append({'date':f'd{i//2}','y':0,'K1':{'p_home':.5,'p_draw':.25,'p_away':.25},'D1':{'p_home':.55,'p_draw':.225,'p_away':.225}})
 assert m.block_ci(rows,100,7)==m.block_ci(rows,100,7)
def test_gate_names_locked():
 c=json.loads(Path(__file__).with_name('v3_discipline_prior_postview_contract_v1.json').read_text());assert len(c['pass_gates'])==8 and c['pass_gates'][-1]=='TEST_LOGLOSS_CI_UPPER_LT_0'
