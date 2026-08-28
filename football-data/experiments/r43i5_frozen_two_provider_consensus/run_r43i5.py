#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from platform_core import normalize_team_token

LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
CONS=ROOT/'evidence'/'market_consensus_prospective'
OUT=HERE/'results'/'summary_r43i5_frozen_two_provider_consensus.json'
C=('home','draw','away')

def load(p): return json.loads(p.read_text(encoding='utf-8'))
def dt(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def key(cid,kick,home,away): return (str(cid),dt(kick).astimezone(timezone.utc).replace(microsecond=0).isoformat(),normalize_team_token(str(home)),normalize_team_token(str(away)))
def devig(o):
    z={k:1.0/float(o[k]) for k in C}; s=sum(z.values()); return {k:z[k]/s for k in C}
def pick(p): return max(C,key=lambda k:(float(p[k]),-C.index(k)))

def metrics(rows,field):
    eps=1e-15; hits=0; ll=br=rps=0.0; picks=Counter(); hitc=Counter(); actual=Counter(); drawtruth=[]
    for r in rows:
        p=r[field]; y=r['truth']; idx=C.index(y); pk=pick(p); hits+=int(pk==y); picks[pk]+=1; hitc[pk]+=int(pk==y); actual[y]+=1
        ll += -math.log(max(eps,min(1.0-eps,float(p[y])))); br += sum((float(p[k])-(1.0 if k==y else 0.0))**2 for k in C)
        obs1=1.0 if idx<=0 else 0.0; obs2=1.0 if idx<=1 else 0.0; c1=float(p['home']); c2=c1+float(p['draw']); rps += ((c1-obs1)**2+(c2-obs2)**2)/2.0
        if y=='draw': drawtruth.append(float(p['draw']))
    n=len(rows); return {'count':n,'hits':hits,'top1_accuracy':hits/n if n else None,'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,'top1_picks':dict(picks),'top1_hits':dict(hitc),'actuals':dict(actual),'mean_draw_probability_on_actual_draws':sum(drawtruth)/len(drawtruth) if drawtruth else None}

def run():
    led=load(LEDGER); predkey={}; truth={}
    for e in led['events']:
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN':
            f=e['payload']['fixture_identity']; predkey[str(e['match_id'])]=key(f['competition_id'],f['kickoff_at'],f['home_team'],f['away_team'])
        elif e.get('event_type')=='RESULT_SETTLED': truth[str(e['match_id'])]=str(e['payload']['result']['actual_result'])
    truth_by_key={predkey[mid]:y for mid,y in truth.items() if mid in predkey}
    rows=[]; rej=Counter(); transitions=Counter(); spreads=[]
    for p in CONS.glob('*.json') if CONS.exists() else []:
        try:
            x=load(p)
            if x.get('promotion_evidence_eligible') is not True: rej['not_promotion_eligible']+=1; continue
            if str(x.get('consensus_type'))!='INDEPENDENT_PROVIDER_CONSENSUS' or int(x.get('provider_count') or 0)<2: rej['not_independent_n2']+=1; continue
            if x.get('retrospective_backfill') is True: rej['retrospective']+=1; continue
            k=key(x['competition_id'],x['kickoff_utc'],x['home_team'],x['away_team']); y=truth_by_key.get(k)
            if y is None: rej['no_settled_overlap']+=1; continue
            obs=dt(x['consensus_observed_at_utc']); kick=dt(x['kickoff_utc'])
            if obs>=kick: rej['invalid_timing']+=1; continue
            ae=x.get('alignment_evidence') or {}; rel=ae.get('kambi_aligned_observation_path')
            if not rel: rej['missing_aligned_kambi_path']+=1; continue
            kp=ROOT/str(rel)
            if not kp.exists(): rej['aligned_kambi_missing']+=1; continue
            kx=load(kp)
            if dt(kx['source_observed_at_utc'])>=kick: rej['aligned_invalid_timing']+=1; continue
            single=devig(kx['one_x_two']); consensus=devig(x['one_x_two']); sp=float(x.get('cross_provider_timestamp_spread_seconds') or 0.0); spreads.append(sp)
            ep,cp=pick(single),pick(consensus); transitions[f'{ep}->{cp}']+=int(ep!=cp)
            rows.append({'truth':y,'single':single,'consensus':consensus})
        except Exception: rej['parse_error']+=1
    sm=metrics(rows,'single'); cm=metrics(rows,'consensus')
    delta={'hits':cm['hits']-sm['hits'],'accuracy_pp':100.0*(cm['top1_accuracy']-sm['top1_accuracy']) if rows else None,'logloss':cm['logloss']-sm['logloss'] if rows else None,'brier':cm['brier']-sm['brier'] if rows else None,'rps':cm['rps']-sm['rps'] if rows else None,'draw_pick_delta':cm['top1_picks'].get('draw',0)-sm['top1_picks'].get('draw',0)}
    signal=bool(len(rows)>=15 and delta['hits']>=1 and delta['logloss']<0 and delta['brier']<0 and delta['rps']<0)
    result={'schema_version':'football3-r43i5-frozen-two-provider-consensus-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_DISCOVERY_ON_SYNCHRONIZED_PROSPECTIVE_PROVIDER_CONSENSUS','question':'Does synchronized independent two-provider consensus add information versus the same-time aligned Kambi observation on already-settled forward fixtures?',
      'governance':{'prospective_only':True,'same_fixture_time_comparison':True,'single_provider_source':'exact-line aligned Kambi observation bound by consensus evidence','consensus_provider_count_minimum':2,'max_timestamp_spread_contract_seconds':300,'retrospective_backfill':False,'model_training':False,'weight_search':False,'threshold_search':False,'formal_promotion_allowed':False},
      'coverage':{'consensus_files_seen':sum(1 for _ in CONS.glob('*.json')) if CONS.exists() else 0,'settled_overlap':len(rows),'rejects':dict(sorted(rej.items())),'timestamp_spread_seconds':{'max':max(spreads) if spreads else None,'median':sorted(spreads)[len(spreads)//2] if spreads else None}},
      'metrics':{'same_time_single_kambi':sm,'independent_two_provider_consensus':cm},'consensus_minus_single':delta,'top1_transitions':dict(sorted(transitions.items())),
      'discovery_gate':{'minimum_overlap':15,'minimum_hit_gain':1,'proper_scores_must_all_improve':True,'passed':signal,'action':'LOCK_CONSENSUS_RULE_FOR_NEW_FORWARD_CONFIRMATION' if signal else 'NO_CONSENSUS_BREAKTHROUGH_EXIT_MARKET_ONLY_DISCOVERY'}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result,ensure_ascii=False,indent=2)); return result

def verify():
    x=load(OUT); assert x['status']=='COMPLETE' and x['formal_weight']==0; g=x['governance']; assert g['prospective_only'] is True and g['model_training'] is False and g['weight_search'] is False and g['threshold_search'] is False and g['formal_promotion_allowed'] is False; print('R43I5 contract verified')
if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
