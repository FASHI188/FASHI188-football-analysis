#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MARKET_LEDGER = ROOT / 'forward' / 'v6_market_first_events_v651.json'
FOOTBALL_LEDGER = ROOT / 'forward' / 'v6_pristine_forward_events_v612.json'
OUT = HERE / 'results' / 'summary_r43i1_frozen_forward_market_football_fusion.json'
CLASSES = ('home','draw','away')


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def norm_team(x: str) -> str:
    s = str(x or '').lower().strip()
    s = s.replace('&',' and ')
    return re.sub(r'[^a-z0-9]+','',s)


def norm_kickoff(x: str) -> str:
    dt = datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def identity(payload: dict, market: bool) -> tuple[str,str,str,str]:
    f = payload['fixture_identity']
    k = f.get('kickoff_at') if not market else f.get('kickoff_at')
    return (str(f['competition_id']), norm_kickoff(k), norm_team(f['home_team']), norm_team(f['away_team']))


def probs(d: dict) -> dict[str,float]:
    v = {k: float(d[k]) for k in CLASSES}
    if any((not math.isfinite(x) or x <= 0) for x in v.values()):
        raise ValueError('invalid probability')
    s = sum(v.values())
    return {k: v[k]/s for k in CLASSES}


def geo_half(a: dict[str,float], b: dict[str,float]) -> dict[str,float]:
    z = {k: math.sqrt(a[k]*b[k]) for k in CLASSES}
    s = sum(z.values())
    return {k:z[k]/s for k in CLASSES}


def top1(p: dict[str,float]) -> str:
    return max(CLASSES, key=lambda k:(p[k], -CLASSES.index(k)))


def metrics(rows: list[dict], key: str) -> dict:
    n=len(rows); hits=0; ll=0.0; br=0.0; rps=0.0
    picks={k:0 for k in CLASSES}; pick_hits={k:0 for k in CLASSES}; actuals={k:0 for k in CLASSES}
    for r in rows:
        p=r[key]; y=r['y']; t=top1(p)
        hits += int(t==y); picks[t]+=1; pick_hits[t]+=int(t==y); actuals[y]+=1
        ll -= math.log(max(p[y],1e-15))
        br += sum((p[k]-(1.0 if y==k else 0.0))**2 for k in CLASSES)
        ph=p['home']; pd=p['draw']
        rps += ((ph-(1.0 if y=='home' else 0.0))**2 + ((ph+pd)-(1.0 if y in {'home','draw'} else 0.0))**2)/2.0
    return {
        'count':n,'hits':hits,'top1_accuracy':hits/n if n else None,
        'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,
        'top1_picks':picks,'top1_hits':pick_hits,'actuals':actuals,
    }


def delta(a: dict, b: dict) -> dict:
    # b-a; negative proper-score deltas are improvements.
    return {
        'hits': b['hits']-a['hits'],
        'accuracy_pp': 100.0*(b['top1_accuracy']-a['top1_accuracy']),
        'logloss': b['logloss']-a['logloss'],
        'brier': b['brier']-a['brier'],
        'rps': b['rps']-a['rps'],
    }


def run() -> dict:
    m=load(MARKET_LEDGER); f=load(FOOTBALL_LEDGER)
    market_pred={}; settled={}
    for e in m.get('events',[]):
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN':
            market_pred[str(e['match_id'])]=e
        elif e.get('event_type')=='RESULT_SETTLED':
            settled[str(e['match_id'])]=e

    football_by_key={}; football_duplicate_keys=[]
    for e in f.get('events',[]):
        if e.get('event_type')!='PREDICTION_FROZEN':
            continue
        key=identity(e['payload'],False)
        if key in football_by_key:
            football_duplicate_keys.append(key)
        else:
            football_by_key[key]=e

    rows=[]; unmatched=[]; bad=[]
    for mid,se in sorted(settled.items()):
        mp=market_pred.get(mid)
        if mp is None:
            bad.append({'match_id':mid,'reason':'settlement_without_market_prediction'})
            continue
        key=identity(mp['payload'],True)
        fp=football_by_key.get(key)
        if fp is None:
            unmatched.append({'match_id':mid,'key':list(key)})
            continue
        kickoff=datetime.fromisoformat(key[1])
        mt=datetime.fromisoformat(str(mp['event_timestamp_utc']).replace('Z','+00:00')).astimezone(timezone.utc)
        ft=datetime.fromisoformat(str(fp['event_timestamp_utc']).replace('Z','+00:00')).astimezone(timezone.utc)
        if not (mt < kickoff and ft < kickoff):
            bad.append({'match_id':mid,'reason':'prediction_not_before_kickoff'})
            continue
        y=str(se['payload']['result']['actual_result'])
        if y not in CLASSES:
            bad.append({'match_id':mid,'reason':'invalid_truth'})
            continue
        pm=probs(mp['payload']['prediction']['probabilities'])
        pf=probs(fp['payload']['prediction']['formal_probabilities'])
        pg=geo_half(pf,pm)
        rows.append({'match_id':mid,'key':list(key),'y':y,'football':pf,'market':pm,'fusion':pg,
                     'football_event_hash':fp['event_hash'],'market_event_hash':mp['event_hash'],'settlement_event_hash':se['event_hash']})

    fm=metrics(rows,'football'); mm=metrics(rows,'market'); gm=metrics(rows,'fusion')
    d_f=delta(fm,gm) if rows else None
    d_m=delta(mm,gm) if rows else None
    n=len(rows)
    discovery_gate=bool(
        n>=30 and gm['top1_accuracy'] >= max(fm['top1_accuracy'],mm['top1_accuracy']) + 0.01
        and gm['logloss'] < min(fm['logloss'],mm['logloss'])
        and gm['brier'] < min(fm['brier'],mm['brier'])
        and gm['rps'] < min(fm['rps'],mm['rps'])
    ) if n else False
    result={
        'schema_version':'football3-r43i1-frozen-forward-market-football-fusion-v1',
        'status':'COMPLETE',
        'classification':'POST_OUTCOME_DISCOVERY_ON_PREMATCH_FROZEN_PREDICTIONS',
        'formal_weight':0,
        'question':'Do independently frozen football-formal and market-first prematch probabilities contain complementary information under a fixed untuned 50/50 geometric pool?',
        'governance':{
            'market_predictions_recomputed':False,'football_predictions_recomputed':False,'settlements_recomputed':False,
            'only_existing_market_settlements_used':True,'only_existing_frozen_predictions_used':True,
            'identity_match':'exact competition + normalized UTC kickoff + normalized home/away tokens',
            'fusion_rule':'normalize(sqrt(P_football_formal * P_market))','fusion_weight_search':False,
            'threshold_search':False,'feature_search':False,'outcome_override':False,'draw_override':False,
            'designed_after_outcomes_existed':True,'formal_promotion_allowed':False,
        },
        'coverage':{
            'market_prediction_count':len(market_pred),'market_settled_count':len(settled),
            'football_frozen_prediction_count':len(football_by_key),'matched_settled_count':n,
            'unmatched_settled_count':len(unmatched),'bad_count':len(bad),'football_duplicate_identity_keys':len(football_duplicate_keys),
        },
        'metrics':{'football_formal':fm,'market_first':mm,'fixed_geo_50_50':gm},
        'fusion_minus_football':d_f,'fusion_minus_market':d_m,
        'discovery_gate':{
            'minimum_overlap':30,'required_accuracy_gain_over_both_pp':1.0,'proper_scores_must_beat_both':True,
            'passed':discovery_gate,
            'action':'FREEZE_FIXED_FUSION_FOR_NEW_FORWARD_CONFIRMATION' if discovery_gate else 'NO_BREAKTHROUGH_SIGNAL_DO_NOT_TUNE_ON_THIS_SETTLED_OVERLAP',
        },
        'unmatched_sample':unmatched[:20], 'bad':bad[:20],
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def verify():
    x=load(OUT)
    assert x['status']=='COMPLETE' and x['formal_weight']==0
    g=x['governance']
    assert g['market_predictions_recomputed'] is False and g['football_predictions_recomputed'] is False
    assert g['only_existing_market_settlements_used'] is True and g['fusion_weight_search'] is False
    assert g['threshold_search'] is False and g['outcome_override'] is False and g['draw_override'] is False
    assert x['coverage']['bad_count']==0
    print('R43I1 contract verified')

if __name__=='__main__':
    import sys
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
