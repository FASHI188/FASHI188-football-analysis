#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from platform_core import normalize_team_token

LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
EVIDENCE=ROOT/'evidence'/'markets_prospective'
OUT=HERE/'results'/'summary_r43i4_frozen_prospective_t1h_market_refresh.json'
C=('home','draw','away')
MIN_LEAD=timedelta(hours=1)
MAX_LEAD=timedelta(hours=72)


def load(p): return json.loads(p.read_text(encoding='utf-8'))
def dt(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def key(cid,kick,home,away):
    return (str(cid),dt(kick).astimezone(timezone.utc).replace(microsecond=0).isoformat(),normalize_team_token(str(home)),normalize_team_token(str(away)))
def devig(o):
    x={k:1.0/float(o[k]) for k in C}; s=sum(x.values()); return {k:x[k]/s for k in C}
def pick(p): return max(C,key=lambda k:(float(p[k]),-C.index(k)))

def metrics(rows, field):
    eps=1e-15; hits=0; ll=br=rps=0.0; picks=Counter(); hitc=Counter(); actual=Counter(); drawtruth=[]
    for r in rows:
        p=r[field]; y=r['truth']; idx=C.index(y); pk=pick(p)
        hits+=int(pk==y); picks[pk]+=1; hitc[pk]+=int(pk==y); actual[y]+=1
        ll += -math.log(max(eps,min(1.0-eps,float(p[y]))))
        br += sum((float(p[k])-(1.0 if k==y else 0.0))**2 for k in C)
        obs1=1.0 if idx<=0 else 0.0; obs2=1.0 if idx<=1 else 0.0
        c1=float(p['home']); c2=c1+float(p['draw']); rps += ((c1-obs1)**2+(c2-obs2)**2)/2.0
        if y=='draw': drawtruth.append(float(p['draw']))
    n=len(rows)
    return {'count':n,'hits':hits,'top1_accuracy':hits/n if n else None,'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,'top1_picks':dict(picks),'top1_hits':dict(hitc),'actuals':dict(actual),'mean_draw_probability_on_actual_draws':sum(drawtruth)/len(drawtruth) if drawtruth else None}

def run():
    led=load(LEDGER); preds={}; settlements={}
    for e in led['events']:
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN': preds[str(e['match_id'])]=e
        elif e.get('event_type')=='RESULT_SETTLED': settlements[str(e['match_id'])]=e
    snaps=defaultdict(list); scan=Counter()
    if EVIDENCE.exists():
        for p in EVIDENCE.glob('*.json'):
            scan['files_seen']+=1
            try:
                x=load(p)
                if str(x.get('provider_group') or '').lower()!='kambi': scan['non_kambi']+=1; continue
                if not isinstance(x.get('one_x_two'),dict): scan['missing_1x2']+=1; continue
                obs=dt(x.get('source_observed_at_utc') or x.get('freeze_utc'))
                kick=dt(x['kickoff_utc']); lead=kick-obs
                if not (MIN_LEAD <= lead <= MAX_LEAD): scan['outside_t1h_72h_window']+=1; continue
                sem=x.get('observation_semantics') or {}
                if sem.get('retrospective_backfill') is True or x.get('retrospective_backfill') is True: scan['retrospective_rejected']+=1; continue
                k=key(x['competition_id'],x['kickoff_utc'],x['home_team'],x['away_team'])
                q=devig(x['one_x_two']); snaps[k].append((obs,p,q))
                scan['eligible_snapshots']+=1
            except Exception:
                scan['parse_rejected']+=1
    rows=[]; rejects=Counter(); movement=Counter(); leads=[]
    for mid,se in settlements.items():
        pe=preds.get(mid)
        if not pe: rejects['no_prediction']+=1; continue
        fi=pe['payload']['fixture_identity']; k=key(fi['competition_id'],fi['kickoff_at'],fi['home_team'],fi['away_team'])
        cand=snaps.get(k) or []
        if not cand: rejects['no_t1h_snapshot']+=1; continue
        obs,path,late=max(cand,key=lambda z:z[0])
        kick=dt(fi['kickoff_at']); lead=(kick-obs).total_seconds()/60.0; leads.append(lead)
        early={c:float(pe['payload']['prediction']['probabilities'][c]) for c in C}; s=sum(early.values()); early={c:early[c]/s for c in C}
        truth=str(se['payload']['result']['actual_result'])
        ep,lp=pick(early),pick(late)
        movement['top1_changed']+=int(ep!=lp); movement[f'{ep}->{lp}']+=int(ep!=lp)
        if ep!=lp:
            movement['changed_early_correct']+=int(ep==truth); movement['changed_late_correct']+=int(lp==truth); movement['changed_both_wrong']+=int(ep!=truth and lp!=truth)
        movement['late_draw_pick']+=int(lp=='draw'); movement['early_draw_pick']+=int(ep=='draw')
        rows.append({'truth':truth,'early':early,'t1h':late})
    em=metrics(rows,'early'); lm=metrics(rows,'t1h')
    delta={'hits':lm['hits']-em['hits'],'accuracy_pp':100.0*(lm['top1_accuracy']-em['top1_accuracy']) if rows else None,'logloss':lm['logloss']-em['logloss'] if rows else None,'brier':lm['brier']-em['brier'] if rows else None,'rps':lm['rps']-em['rps'] if rows else None,'draw_pick_delta':lm['top1_picks'].get('draw',0)-em['top1_picks'].get('draw',0)}
    signal=bool(len(rows)>=50 and delta['hits']>=0 and delta['logloss']<0 and delta['brier']<0 and delta['rps']<0)
    result={'schema_version':'football3-r43i4-frozen-prospective-t1h-market-refresh-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_DISCOVERY_ON_PROSPECTIVELY_CAPTURED_PREMATCH_SNAPSHOTS','question':'Does a fixed T-1h prediction horizon using the latest already-captured Kambi snapshot add stable information versus the earliest frozen market-first snapshot?',
      'governance':{'prospective_snapshots_only':True,'provider_group':'kambi','prediction_horizon':'latest snapshot with 60m <= lead <= 72h','later_than_t1h_not_used':True,'earlier_prediction_not_rewritten':True,'outcomes_not_used_to_select_snapshot':True,'weight_search':False,'threshold_search':False,'model_training':False,'formal_promotion_allowed':False},
      'coverage':{'market_settled_count':len(settlements),'paired_t1h_count':len(rows),'rejects':dict(sorted(rejects.items())),'snapshot_scan':dict(sorted(scan.items())),'selected_lead_minutes':{'min':min(leads) if leads else None,'median':sorted(leads)[len(leads)//2] if leads else None,'max':max(leads) if leads else None}},
      'metrics':{'earliest_frozen_market':em,'fixed_t1h_latest_market':lm},'t1h_minus_earliest':delta,'movement_anatomy':dict(sorted(movement.items())),
      'discovery_gate':{'minimum_paired':50,'top1_hits_must_not_decline':True,'proper_scores_must_all_improve':True,'passed':signal,'action':'LOCK_T1H_HORIZON_FOR_NEW_FORWARD_CONFIRMATION' if signal else 'NO_T1H_BREAKTHROUGH_DO_NOT_TUNE_HORIZON_ON_SETTLED_SAMPLE'}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result,ensure_ascii=False,indent=2)); return result

def verify():
    x=load(OUT); assert x['status']=='COMPLETE' and x['formal_weight']==0
    g=x['governance']; assert g['outcomes_not_used_to_select_snapshot'] is True and g['threshold_search'] is False and g['weight_search'] is False and g['model_training'] is False and g['formal_promotion_allowed'] is False
    print('R43I4 contract verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
