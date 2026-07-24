#!/usr/bin/env python3
"""V6.14.5 research-only Fast100: data-driven market-state exact-score challenger.

No scoreline is hand-assigned. Score distributions are learned from strictly earlier
seasons by market state. Historical 1X2 and O/U2.5 prices are de-vigged.

Training: 2021/22-2024/25.
Test: final 100 complete-market matches of 2025/26.
Variants are fixed ex ante and all reported; there is no test-based model selection:
  A league-only empirical score distribution;
  B league + 1X2 market pick;
  C league + 1X2 pick + O/U2.5 pick;
  D league + 1X2 pick + O/U2.5 pick + fixed 1X2 confidence bin.
Hierarchical backoff is deterministic when a cell has <30 training matches.
Historical odds lack original quote timestamps, so research only.
"""
from __future__ import annotations

import csv,json,math
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

from platform_core import canonical_team_name,load_aliases,parse_match_date

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_score_market_state_fast100_v6145_status.json'
COMPS=['ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga']
TRAIN={'2021/22','2022/23','2023/24','2024/25'}
TEST='2025/26'
MIN_CELL=30

def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 and math.isfinite(x) else None

def devig3(h,d,a):
    q=[1/h,1/d,1/a];s=sum(q);return tuple(x/s for x in q)
def devig2(o,u):
    q=[1/o,1/u];s=sum(q);return tuple(x/s for x in q)
def pick3(p):return ('H','D','A')[max(range(3),key=lambda i:p[i])]
def confbin(p):
    c=max(p)
    if c<0.45:return 'lt45'
    if c<0.55:return '45_55'
    if c<0.65:return '55_65'
    return 'ge65'

def load():
    aliases=load_aliases();out={}
    for cid in COMPS:
        d=ROOT/'processed'/cid
        if not d.exists():continue
        for path in sorted(d.glob('*.csv')):
            with path.open('r',encoding='utf-8-sig',newline='') as fh:
                rd=csv.DictReader(fh);fields=set(rd.fieldnames or [])
                has_ou='P>2.5' in fields and 'P<2.5' in fields
                has_b365_ou='B365>2.5' in fields and 'B365<2.5' in fields
                has_avg_ou='Avg>2.5' in fields and 'Avg<2.5' in fields
                for r0 in rd:
                    r={str(k):'' if v is None else str(v) for k,v in r0.items() if k}
                    season=str(r.get('season') or r.get('Season') or '').strip()
                    if season not in TRAIN|{TEST} or not r.get('HomeTeam') or not r.get('AwayTeam') or not r.get('Date'):continue
                    try:hg=int(float(r.get('FTHG','')));ag=int(float(r.get('FTAG','')))
                    except:continue
                    # 1X2 provider preference.
                    one=None
                    for cols in (('PSH','PSD','PSA'),('B365H','B365D','B365A'),('AvgH','AvgD','AvgA')):
                        vals=[f(r.get(c)) for c in cols]
                        if all(v is not None for v in vals):one=devig3(*vals);break
                    if one is None:continue
                    two=None
                    for ok,cols in ((has_ou,('P>2.5','P<2.5')),(has_b365_ou,('B365>2.5','B365<2.5')),(has_avg_ou,('Avg>2.5','Avg<2.5'))):
                        if not ok:continue
                        vals=[f(r.get(c)) for c in cols]
                        if all(v is not None for v in vals):two=devig2(*vals);break
                    if two is None:continue
                    try:dt=parse_match_date(r['Date'],season)
                    except:continue
                    home=canonical_team_name(cid,r['HomeTeam'],aliases);away=canonical_team_name(cid,r['AwayTeam'],aliases)
                    out[(cid,dt.isoformat(),home,away)]={'competition_id':cid,'season':season,'date':dt.isoformat(),'score':(hg,ag),'result_pick':pick3(one),'ou_pick':'O' if two[0]>=0.5 else 'U','conf_bin':confbin(one),'one_x_two':one,'p_over':two[0]}
    return sorted(out.values(),key=lambda r:(r['date'],r['competition_id']))

def topk(counter,k):
    return [score for score,_ in sorted(counter.items(),key=lambda kv:(-kv[1],kv[0][0]+kv[0][1],kv[0]))[:k]]

def train_counters(rows):
    c0=defaultdict(Counter);c1=defaultdict(Counter);c2=defaultdict(Counter);c3=defaultdict(Counter);globalc=Counter()
    for r in rows:
        sc=r['score'];cid=r['competition_id'];globalc[sc]+=1;c0[(cid,)][sc]+=1;c1[(cid,r['result_pick'])][sc]+=1;c2[(cid,r['result_pick'],r['ou_pick'])][sc]+=1;c3[(cid,r['result_pick'],r['ou_pick'],r['conf_bin'])][sc]+=1
    return globalc,c0,c1,c2,c3

def choose(counter_chain):
    for c in counter_chain:
        if sum(c.values())>=MIN_CELL:return c
    return counter_chain[-1]

def eval_variant(test,counters,variant):
    globalc,c0,c1,c2,c3=counters;hits1=hits3=hits5=0;used=Counter();rows=[]
    for r in test:
        cid=r['competition_id']
        if variant=='league': chain=[c0[(cid,)],globalc]
        elif variant=='result': chain=[c1[(cid,r['result_pick'])],c0[(cid,)],globalc]
        elif variant=='result_ou': chain=[c2[(cid,r['result_pick'],r['ou_pick'])],c1[(cid,r['result_pick'])],c0[(cid,)],globalc]
        else: chain=[c3[(cid,r['result_pick'],r['ou_pick'],r['conf_bin'])],c2[(cid,r['result_pick'],r['ou_pick'])],c1[(cid,r['result_pick'])],c0[(cid,)],globalc]
        c=choose(chain);used[sum(c.values())]+=1;t1=topk(c,1);t3=topk(c,3);t5=topk(c,5);sc=r['score'];hits1+=int(sc in t1);hits3+=int(sc in t3);hits5+=int(sc in t5)
        rows.append({'actual':list(sc),'top1':list(t1[0]) if t1 else None,'top3':[list(x) for x in t3]})
    n=len(test);return {'count':n,'top1_hits':hits1,'top1_accuracy':hits1/n if n else None,'top3_hits':hits3,'top3_accuracy':hits3/n if n else None,'top5_hits':hits5,'top5_accuracy':hits5/n if n else None,'training_cell_sizes_used':dict(sorted(used.items()))}

def main():
    rows=load();train=[r for r in rows if r['season'] in TRAIN];test_all=[r for r in rows if r['season']==TEST]
    if len(test_all)<100:raise RuntimeError(f'insufficient 2025/26 complete-market rows: {len(test_all)}')
    test=test_all[-100:];counters=train_counters(train)
    variants={name:eval_variant(test,counters,name) for name in ('league','result','result_ou','result_ou_conf')}
    payload={'schema_version':'V6.14.5-score-market-state-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','governance':{'no_hand_assigned_scoreline':True,'training_seasons':sorted(TRAIN),'test_season':TEST,'test_matches':100,'fixed_min_cell':MIN_CELL,'fixed_variants_report_all_no_test_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'rows':len(rows),'train_rows':len(train),'test_season_rows':len(test_all),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':variants}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(variants,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
