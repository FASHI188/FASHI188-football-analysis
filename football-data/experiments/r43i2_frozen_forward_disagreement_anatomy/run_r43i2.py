#!/usr/bin/env python3
from __future__ import annotations
import json, math, re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
M=ROOT/'forward'/'v6_market_first_events_v651.json'; F=ROOT/'forward'/'v6_pristine_forward_events_v612.json'
OUT=HERE/'results'/'summary_r43i2_frozen_forward_disagreement_anatomy.json'
C=('home','draw','away')

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def nt(x):return re.sub(r'[^a-z0-9]+','',str(x or '').lower().replace('&',' and '))
def nk(x):
 d=datetime.fromisoformat(str(x).replace('Z','+00:00'));d=d if d.tzinfo else d.replace(tzinfo=timezone.utc);return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()
def key(p):
 f=p['fixture_identity'];return(str(f['competition_id']),nk(f['kickoff_at']),nt(f['home_team']),nt(f['away_team']))
def probs(x):
 z={k:float(x[k]) for k in C};s=sum(z.values());return{k:z[k]/s for k in C}
def pick(p):return max(C,key=lambda k:(p[k],-C.index(k)))

def run():
 m=load(M);f=load(F);mp={};st={}
 for e in m['events']:
  if e.get('event_type')=='MARKET_PREDICTION_FROZEN':mp[str(e['match_id'])]=e
  elif e.get('event_type')=='RESULT_SETTLED':st[str(e['match_id'])]=e
 fb={key(e['payload']):e for e in f['events'] if e.get('event_type')=='PREDICTION_FROZEN'}
 rows=[]
 for mid,se in st.items():
  me=mp.get(mid)
  if not me:continue
  fe=fb.get(key(me['payload']))
  if not fe:continue
  pm=probs(me['payload']['prediction']['probabilities']);pf=probs(fe['payload']['prediction']['formal_probabilities']);y=str(se['payload']['result']['actual_result'])
  rows.append({'mid':mid,'y':y,'pm':pm,'pf':pf,'m':pick(pm),'f':pick(pf)})
 dis=[r for r in rows if r['m']!=r['f']];agr=[r for r in rows if r['m']==r['f']]
 def hitwho(items):
  c=Counter()
  for r in items:
   mh=r['m']==r['y'];fh=r['f']==r['y']
   c['market_hit']+=mh;c['football_hit']+=fh;c['market_only']+=mh and not fh;c['football_only']+=fh and not mh;c['both_wrong']+=(not mh and not fh);c['actual_draw']+=r['y']=='draw';c['market_draw_pick']+=r['m']=='draw';c['football_draw_pick']+=r['f']=='draw'
  return dict(c)
 pairs=Counter(f"{r['f']}->{r['m']}" for r in dis); truths=Counter(r['y'] for r in dis)
 true_prob={'market':sum(r['pm'][r['y']] for r in rows)/len(rows) if rows else None,'football':sum(r['pf'][r['y']] for r in rows)/len(rows) if rows else None}
 result={'schema_version':'football3-r43i2-frozen-forward-disagreement-anatomy-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_DIAGNOSTIC_ONLY',
 'governance':{'frozen_predictions_only':True,'existing_settlements_only':True,'parameter_search':False,'threshold_search':False,'decision_rule_created':False,'promotion_allowed':False},
 'coverage':{'matched':len(rows),'agreements':len(agr),'disagreements':len(dis)},'all':hitwho(rows),'agreements_detail':hitwho(agr),'disagreements_detail':hitwho(dis),'disagreement_pick_transitions':dict(sorted(pairs.items())),'disagreement_actuals':dict(truths),'mean_probability_assigned_to_truth':true_prob,
 'interpretation_contract':'Diagnostic evidence may motivate a future preregistered rule but cannot modify or promote any rule on these outcomes.'}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2));return result

def verify():
 x=load(OUT);assert x['status']=='COMPLETE' and x['formal_weight']==0;assert x['governance']['decision_rule_created'] is False and x['governance']['parameter_search'] is False;print('R43I2 contract verified')
if __name__=='__main__':
 import sys; c=sys.argv[1] if len(sys.argv)>1 else 'run'; run() if c=='run' else verify() if c=='verify' else (_ for _ in ()).throw(SystemExit(c))
