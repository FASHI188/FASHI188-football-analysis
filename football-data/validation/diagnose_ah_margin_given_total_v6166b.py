#!/usr/bin/env python3
"""V6.16.6b zero-fit diagnostic: how informative is the primary AH line for D given true T?

This is NOT a deployable predictor because it conditions on the realized total. It only asks
whether AH carries enough margin information to justify a learned P(D|T,X) layer.
Prediction: among legal D=-T,-T+2,...,T choose the D nearest to -AHCh (home handicap
negative implies expected positive home margin). Ties prefer the smaller absolute margin.
No outcomes are used for parameter fitting.
"""
from __future__ import annotations
import csv,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_ah_margin_given_total_v6166b_status.json'
COMPS=('ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga','NED_Eredivisie','POR_PrimeiraLiga','SCO_Premiership')
SEASONS=('2022/23','2023/24','2024/25','2025/26')

def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if math.isfinite(x) else None

def pred_d(t,line):
    target=-line;valid=list(range(-t,t+1,2));return min(valid,key=lambda d:(abs(d-target),abs(d),d))
def score_from(t,d):return ((t+d)//2,(t-d)//2)

def main():
    by=defaultdict(lambda:{'n':0,'hit':0});alln=allh=0
    for cid in COMPS:
      d=ROOT/'processed'/cid
      if not d.exists():continue
      for p in sorted(d.glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as fh:
          for r in csv.DictReader(fh):
            s=str(r.get('season') or r.get('Season') or '').strip()
            if s not in SEASONS:continue
            line=f(r.get('AHCh'))
            if line is None:line=f(r.get('AHh'))
            if line is None:continue
            try:h=int(float(r.get('FTHG','')));a=int(float(r.get('FTAG','')))
            except:continue
            t=h+a
            if t>7:continue
            pd=pred_d(t,line);hit=int(score_from(t,pd)==(h,a));k=(s,cid);by[k]['n']+=1;by[k]['hit']+=hit;alln+=1;allh+=hit
    season={}
    for s in SEASONS:
        n=sum(v['n'] for (ss,_),v in by.items() if ss==s);h=sum(v['hit'] for (ss,_),v in by.items() if ss==s);season[s]={'count':n,'hits':h,'accuracy':h/n if n else None}
    comp={}
    for cid in COMPS:
        n=sum(v['n'] for (_,cc),v in by.items() if cc==cid);h=sum(v['hit'] for (_,cc),v in by.items() if cc==cid);comp[cid]={'count':n,'hits':h,'accuracy':h/n if n else None}
    out={'schema_version':'V6.16.6b-ah-margin-given-total-diagnostic-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','classification':'ORACLE_TOTAL_DIAGNOSTIC_NOT_DEPLOYABLE','design':{'uses_realized_total':True,'fitted_parameters':0,'target_margin':'-AH line','legal_parity_enforced':True},'aggregate':{'count':alln,'hits':allh,'accuracy':allh/alln if alln else None},'by_season':season,'by_competition':comp,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
