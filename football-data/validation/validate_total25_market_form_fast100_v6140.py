#!/usr/bin/env python3
"""V6.14.0 research-only Fast100: O/U 2.5 market anchor plus strictly lagged team goal state.

Question: can same-season, strictly-prior goal/total state add stable O/U 2.5 accuracy over
the de-vigged two-sided market price?

Chronology:
- 2021/22-2024/25 are training history;
- the 200 matches immediately before the final 100 of 2025/26 are validation only;
- the final 100 2025/26 matches are untouched test;
- team features are updated only AFTER each target match.
Historical odds lack original quote timestamps, so this is retrospective research only.
"""
from __future__ import annotations

import csv,json,math
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from platform_core import canonical_team_name,load_aliases,parse_match_date

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_total25_market_form_fast100_v6140_status.json'
COMPS=['ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga']
SEASONS={'2021/22','2022/23','2023/24','2024/25','2025/26'}
TRAIN_SEASONS={'2021/22','2022/23','2023/24','2024/25'}
CS=(0.001,0.003,0.01,0.03,0.1)
GATES=(0.02,0.04,0.06,0.08,0.10,0.12)


def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 and math.isfinite(x) else None

def detect_ou25(fieldnames):
    fields=set(fieldnames or [])
    preferred=[('P>2.5','P<2.5','Pinnacle'),('B365>2.5','B365<2.5','Bet365'),('Avg>2.5','Avg<2.5','Average'),('Max>2.5','Max<2.5','Max')]
    for o,u,name in preferred:
        if o in fields and u in fields:return o,u,name
    overs=sorted(x for x in fields if '>2.5' in x)
    unders=sorted(x for x in fields if '<2.5' in x)
    for o in overs:
        prefix=o.replace('>2.5','')
        u=prefix+'<2.5'
        if u in fields:return o,u,prefix or 'detected'
    return None

def devig_pair(o,u):
    qo,qu=1/o,1/u;s=qo+qu;return qo/s,qu/s

def load_matches():
    aliases=load_aliases();out={};source_counts=defaultdict(int);header_pairs={}
    for cid in COMPS:
        d=ROOT/'processed'/cid
        if not d.exists():continue
        for path in sorted(d.glob('*.csv')):
            with path.open('r',encoding='utf-8-sig',newline='') as fh:
                rd=csv.DictReader(fh);pair=detect_ou25(rd.fieldnames or [])
                header_pairs[str(path.relative_to(ROOT))]=pair
                if pair is None:continue
                oc,uc,provider=pair
                for r0 in rd:
                    r={str(k):'' if v is None else str(v) for k,v in r0.items() if k}
                    season=str(r.get('season') or r.get('Season') or '').strip()
                    if season not in SEASONS or not r.get('HomeTeam') or not r.get('AwayTeam') or not r.get('Date'):continue
                    try:hg=int(float(r.get('FTHG','')));ag=int(float(r.get('FTAG','')))
                    except:continue
                    ov=f(r.get(oc));un=f(r.get(uc))
                    if ov is None or un is None:continue
                    try:dt=parse_match_date(r['Date'],season)
                    except:continue
                    home=canonical_team_name(cid,r['HomeTeam'],aliases);away=canonical_team_name(cid,r['AwayTeam'],aliases)
                    po,pu=devig_pair(ov,un);total=hg+ag;actual=1 if total>=3 else 0
                    key=(cid,dt.isoformat(),home,away)
                    out[key]={'competition_id':cid,'season':season,'date':dt.isoformat(),'home':home,'away':away,'hg':hg,'ag':ag,'total':total,'actual_over':actual,'p_over':po,'p_under':pu,'ou_provider':provider}
                    source_counts[provider]+=1
    return sorted(out.values(),key=lambda r:(r['date'],r['competition_id'],r['home'],r['away'])),dict(source_counts),header_pairs

def avg(seq,default):return sum(seq)/len(seq) if seq else default

def build_features(matches):
    hist=defaultdict(lambda:deque(maxlen=5));league_hist=defaultdict(lambda:deque(maxlen=100));out=[]
    for r in matches:
        cid=r['competition_id'];season=r['season'];hk=(cid,season,r['home']);ak=(cid,season,r['away']);lk=(cid,season)
        hh=list(hist[hk]);ah=list(hist[ak]);lh=list(league_hist[lk]);league_total=avg([x['total'] for x in lh],2.6)
        if len(hh)>=3 and len(ah)>=3:
            ph=r['p_over'];feature=[ph,abs(ph-0.5),avg([x['total'] for x in hh],league_total),avg([x['total'] for x in ah],league_total),avg([x['gf'] for x in hh],1.3),avg([x['ga'] for x in hh],1.3),avg([x['gf'] for x in ah],1.3),avg([x['ga'] for x in ah],1.3),league_total]
            feature += [1.0 if cid==x else 0.0 for x in COMPS]
            out.append({**r,'feature':feature})
        hist[hk].append({'gf':r['hg'],'ga':r['ag'],'total':r['total']});hist[ak].append({'gf':r['ag'],'ga':r['hg'],'total':r['total']});league_hist[lk].append({'total':r['total']})
    return out

def market_pick(r):return 1 if r['p_over']>=0.5 else 0

def stats(rows,picker):
    hits=sum(int(picker(r)==r['actual_over']) for r in rows);return {'count':len(rows),'hits':hits,'accuracy':hits/len(rows) if rows else None}

def fit(rows):
    test_candidates=[r for r in rows if r['season']=='2025/26']
    if len(test_candidates)<300:raise RuntimeError(f'insufficient 2025/26 feature rows: {len(test_candidates)}')
    test=test_candidates[-100:];val=test_candidates[-300:-100]
    cutoff=val[0]['date'];train=[r for r in rows if r['season'] in TRAIN_SEASONS or (r['season']=='2025/26' and r['date']<cutoff)]
    Xtr=np.asarray([r['feature'] for r in train]);ytr=np.asarray([r['actual_over'] for r in train]);Xv=np.asarray([r['feature'] for r in val]);yv=np.asarray([r['actual_over'] for r in val]);Xt=np.asarray([r['feature'] for r in test]);yt=np.asarray([r['actual_over'] for r in test])
    board=[]
    for C in CS:
        model=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=2000))
        model.fit(Xtr,ytr);pv=model.predict(Xv);board.append((float((pv==yv).mean()),C,model))
    board.sort(key=lambda x:(x[0],-x[1]),reverse=True);vacc,C,model=board[0];ptest=model.predict_proba(Xt)[:,1];direct=(ptest>=0.5).astype(int)
    direct_stats={'count':len(test),'hits':int((direct==yt).sum()),'accuracy':float((direct==yt).mean())}
    # Select a conservative override gate on validation only.
    pvprob=model.predict_proba(Xv)[:,1];best_gate=None;best_acc=-1;best_overrides=0
    for gate in GATES:
        hits=0;overrides=0
        for i,r in enumerate(val):
            mp=market_pick(r);modelpick=int(pvprob[i]>=0.5);pick=mp
            market_conf=max(r['p_over'],r['p_under']);model_conf=max(pvprob[i],1-pvprob[i])
            if modelpick!=mp and model_conf-market_conf>=gate:pick=modelpick;overrides+=1
            hits+=int(pick==r['actual_over'])
        acc=hits/len(val)
        if (acc, -overrides, -gate)>(best_acc,-best_overrides,-(best_gate or 99)):
            best_acc=acc;best_gate=gate;best_overrides=overrides
    thits=0;tover=0
    for i,r in enumerate(test):
        mp=market_pick(r);modelpick=int(ptest[i]>=0.5);pick=mp;market_conf=max(r['p_over'],r['p_under']);model_conf=max(ptest[i],1-ptest[i])
        if modelpick!=mp and model_conf-market_conf>=best_gate:pick=modelpick;tover+=1
        thits+=int(pick==r['actual_over'])
    return train,val,test,{'selected_C':C,'validation_direct_accuracy':vacc,'test_direct_model':direct_stats,'override_gate':{'selected_advantage':best_gate,'validation_accuracy':best_acc,'validation_overrides':best_overrides,'test_count':len(test),'test_hits':thits,'test_accuracy':thits/len(test),'test_overrides':tover}}

def main():
    matches,source_counts,headers=load_matches();rows=build_features(matches);train,val,test,result=fit(rows);market=stats(test,market_pick)
    selective={}
    for c in (0.54,0.56,0.58,0.60):
        sel=[r for r in test if max(r['p_over'],r['p_under'])>=c];selective[f'confidence_ge_{c:.2f}']=stats(sel,market_pick)
    payload={'schema_version':'V6.14.0-total25-market-form-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','governance':{'same_season_team_features_strictly_prior':True,'test_matches':100,'validation_matches':200,'test_untouched_for_model_gate_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':{'ou_provider_counts':source_counts,'files_with_detected_ou_pair':sum(v is not None for v in headers.values()),'files_scanned':len(headers)},'sample':{'market_matches':len(matches),'feature_rows':len(rows),'train_rows':len(train),'validation_rows':len(val),'test_rows':len(test),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':{'market_only':market,'market_plus_lagged_goal_state':result,'market_selective':selective}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['test'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
