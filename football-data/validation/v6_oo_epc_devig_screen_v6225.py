#!/usr/bin/env python3
"""V6.22.5 no-tuning screen of 2026 OO-EPC versus multiplicative de-vigging.

Implements Algorithm 5 from Goto, Takeishi & Yairi (arXiv:2604.17194) exactly for t=1.
Historical processed bookmaker rows are retrospective and lack original quote timestamps,
so this is research-only. No parameter is fitted or selected from outcomes.
"""
from __future__ import annotations
import csv,json,math,random
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_oo_epc_devig_screen_v6225_status.json'
COMPS=('ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga','NED_Eredivisie','POR_PrimeiraLiga','SCO_Premiership')
SEASON='2025/26';SEED=20260725+6225;EPS=1e-15
CHOICES=((('PSCH','PSCD','PSCA'),'Pinnacle_closing'),(('B365CH','B365CD','B365CA'),'Bet365_closing'),(('AvgCH','AvgCD','AvgCA'),'Average_closing'),(('PSH','PSD','PSA'),'Pinnacle'),(('B365H','B365D','B365A'),'Bet365'),(('AvgH','AvgD','AvgA'),'Average'))

def odds(v):
 try:x=float(str(v).strip())
 except:return None
 return x if math.isfinite(x) and x>1 else None

def score_int(r,*names):
 for n in names:
  try:return int(float(str(r.get(n) or '').strip()))
  except:pass
 return None

def multiplicative(x):
 inv=[1/v for v in x];s=sum(inv);return [v/s for v in inv]

def oo_epc(x,t=1.0):
 inv=[1/v for v in x];sig=[math.sqrt(max(0.0,(p*(1-p))/p)) for p in inv];den=sum(sig)
 if den<=0:return multiplicative(x),True
 z=(sum(inv)-t)/den
 if all((s<=0 or z < p/s) for p,s in zip(inv,sig)):
  q=[p-z*s for p,s in zip(inv,sig)]
  if min(q)>=0 and abs(sum(q)-t)<=1e-10:return q,False
 return multiplicative(x),True

def rid(h,a):return 0 if h>a else 1 if h==a else 2

def rows_all():
 out=[];seen=set()
 for cid in COMPS:
  d=ROOT/'processed'/cid
  if not d.exists():continue
  for path in sorted(d.glob('*.csv')):
   with path.open('r',encoding='utf-8-sig',newline='') as fh:
    rd=csv.DictReader(fh)
    for r0 in rd:
     r={str(k):'' if v is None else str(v) for k,v in r0.items() if k};season=str(r.get('season') or r.get('Season') or '').strip()
     if season!=SEASON:continue
     h=score_int(r,'FTHG','HomeGoals','HG');a=score_int(r,'FTAG','AwayGoals','AG')
     if h is None or a is None:continue
     selected=None
     for cols,label in CHOICES:
      vals=[odds(r.get(c)) for c in cols]
      if all(v is not None for v in vals):selected=(vals,label,cols);break
     if selected is None:continue
     vals,label,cols=selected;key=(cid,str(r.get('Date') or ''),str(r.get('HomeTeam') or ''),str(r.get('AwayTeam') or ''),label)
     if key in seen:continue
     seen.add(key);pm=multiplicative(vals);po,fb=oo_epc(vals)
     out.append({'competition_id':cid,'date':key[1],'home_team':key[2],'away_team':key[3],'provider':label,'odds':vals,'actual':rid(h,a),'multiplicative':pm,'oo_epc':po,'fallback':fb})
 return out

def metrics(rows,key):
 n=len(rows);hits=0;brier=ll=rps=0.0
 for r in rows:
  p=r[key];y=r['actual'];hits+=int(max(range(3),key=lambda i:p[i])==y);brier+=sum((p[i]-(1 if i==y else 0))**2 for i in range(3));ll+=-math.log(max(EPS,p[y]));cp1=p[0];cp2=p[0]+p[1];rps+=((cp1-(1 if y==0 else 0))**2+(cp2-(1 if y<=1 else 0))**2)/2
 return {'count':n,'hits':hits,'accuracy':hits/n if n else None,'brier':brier/n if n else None,'logloss':ll/n if n else None,'rps':rps/n if n else None}
def compare(rows):
 m=metrics(rows,'multiplicative');o=metrics(rows,'oo_epc');return {'multiplicative':m,'oo_epc':o,'delta_oo_minus_multiplicative':{'accuracy_pp':(o['accuracy']-m['accuracy'])*100 if rows else None,'brier':o['brier']-m['brier'] if rows else None,'logloss':o['logloss']-m['logloss'] if rows else None,'rps':o['rps']-m['rps'] if rows else None}}
def main():
 rows=rows_all();sample=random.Random(SEED).sample(rows,min(100,len(rows)));by=defaultdict(list)
 for r in rows:by[r['provider']].append(r)
 payload={'schema_version':'V6.22.5-oo-epc-devig-screen-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_HISTORICAL_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','paper_algorithm':{'reference':'Goto, Takeishi & Yairi 2026 arXiv:2604.17194 Algorithm 5','t':1,'fitted_parameters':False,'fallback':'multiplicative when positivity condition fails'},'population':compare(rows),'fixed_seed_random100':compare(sample),'provider_breakdown':{k:compare(v) for k,v in sorted(by.items())},'fallback_count':sum(r['fallback'] for r in rows),'fallback_rate':sum(r['fallback'] for r in rows)/len(rows) if rows else None,'rows_count':len(rows),'sample_count':len(sample),'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_quotes_lack_original_timestamp':True,'test_threshold_tuning':False,'no_model_fit':True}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'population':payload['population'],'fixed100':payload['fixed_seed_random100'],'provider_breakdown':payload['provider_breakdown'],'fallback_rate':payload['fallback_rate']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
