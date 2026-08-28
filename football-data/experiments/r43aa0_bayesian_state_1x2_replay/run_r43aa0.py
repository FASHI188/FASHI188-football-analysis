#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / 'validation'
ENGINE = ROOT / 'engine'
for p in (VALID, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import bayesian_dynamic_state_oof_v500 as v500

OUT = ROOT / 'experiments' / 'r43aa0_bayesian_state_1x2_replay' / 'results' / 'summary_r43aa0.json'
CLASSES = ('home','draw','away')
SOURCE_BLOB_SHA = '9c302506c49aa1847e60cd7896fc1a80f3b6b457'
FULL_VOLUME_ACCURACY_FLOOR = 0.53
FOLDS = 3
EPS = 1e-15


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def top1(p):
    return max(CLASSES, key=lambda k: (float(p[k]), -CLASSES.index(k)))


def draw_metrics(rows, key):
    if not rows:
        return {'n':0}
    ps=[float(r[key]['draw']) for r in rows]
    ys=[1.0 if r['actual']=='draw' else 0.0 for r in rows]
    ll=sum(-(y*math.log(max(EPS,p))+(1-y)*math.log(max(EPS,1-p))) for p,y in zip(ps,ys))/len(rows)
    br=sum((p-y)**2 for p,y in zip(ps,ys))/len(rows)
    return {'n':len(rows),'mean_pred':sum(ps)/len(ps),'actual_rate':sum(ys)/len(ys),'logloss':ll,'brier':br}


def metrics(rows, key):
    n=len(rows)
    picks=Counter(); hitby=Counter(); actuals=Counter()
    if not n:
        return {'count':0,'hits':0,'accuracy':None,'logloss':None,'brier':None,'rps':None,'top1_picks':{},'top1_hits':{},'actuals':{},'draw_calibration':{'n':0}}
    hits=0; ll=br=rps=0.0
    for r in rows:
        p=r[key]; y=r['actual']; t=top1(p)
        hits += int(t==y); picks[t]+=1; hitby[t]+=int(t==y); actuals[y]+=1
        ll -= math.log(max(EPS,float(p[y])))
        br += sum((float(p[k])-(1.0 if y==k else 0.0))**2 for k in CLASSES)
        ph=float(p['home']); pd=float(p['draw'])
        rps += ((ph-(1.0 if y=='home' else 0.0))**2 + ((ph+pd)-(1.0 if y in {'home','draw'} else 0.0))**2)/2.0
    return {'count':n,'hits':hits,'accuracy':hits/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':dict(picks),'top1_hits':dict(hitby),'actuals':dict(actuals),'draw_calibration':draw_metrics(rows,key)}


def delta(base,cand):
    return {
        'hits':cand['hits']-base['hits'],
        'accuracy_pp':100.0*(cand['accuracy']-base['accuracy']),
        'logloss':cand['logloss']-base['logloss'],
        'brier':cand['brier']-base['brier'],
        'rps':cand['rps']-base['rps'],
        'draw_logloss':cand['draw_calibration']['logloss']-base['draw_calibration']['logloss'],
        'draw_brier':cand['draw_calibration']['brier']-base['draw_calibration']['brier'],
    }


def split3(rows):
    ordered=sorted(rows,key=lambda r:(r['date'],r['competition_id'],r['match_key']))
    q,rem=divmod(len(ordered),FOLDS); out=[]; s=0
    for i in range(FOLDS):
        z=q+(1 if i<rem else 0); out.append(ordered[s:s+z]); s+=z
    return out


def enhanced_metric_row(matrix, match):
    base=v500._ORIGINAL_METRIC_ROW(matrix,match)
    marg=v500.derive_score_marginals(matrix)
    p={k:float(marg['1x2'][k]) for k in CLASSES}
    actual=v500._actual_result(int(match.home_goals),int(match.away_goals))
    base.update({'probabilities':p,'actual':actual,'top1':top1(p)})
    return base


def run():
    # No model change: monkey-patch only adds diagnostic fields to the old metric row.
    if not hasattr(v500,'_ORIGINAL_METRIC_ROW'):
        v500._ORIGINAL_METRIC_ROW=v500._metric_row
    v500._metric_row=enhanced_metric_row

    status=load(v500.FORMAL_STATUS)
    competitions=sorted((status.get('reports') or {}).keys())
    rows=[]; domain_meta={}; failures={}
    for cid in competitions:
        try:
            report=load(v500.REPORT_ROOT/f'{cid}.json')
            seasons=v500._completed_outer_seasons(cid,report)
            if len(seasons)<3:
                raise RuntimeError(f'need >=3 seasons, got {seasons}')
            all_matches=v500.read_processed_matches(cid)
            sims={s:v500._simulate_season(cid,s,all_matches,report) for s in seasons}
            selected_folds=[]
            for target_index in range(2,len(seasons)):
                target=seasons[target_index]; priors=seasons[:target_index]
                scored=[]
                for profile in v500.PROFILES:
                    pid=profile['id']
                    prior_rows=[r for s in priors for r in sims[s]['profiles'][pid]]
                    if len(prior_rows)<v500.MIN_PRIOR_SELECTION_ROWS:
                        continue
                    scored.append({'profile_id':pid,'objective':v500._selection_objective(prior_rows),'selection_rows':len(prior_rows)})
                if not scored:
                    continue
                scored.sort(key=lambda x:(x['objective'],x['profile_id']))
                chosen=scored[0]; pid=chosen['profile_id']
                bm={r['match_key']:r for r in sims[target]['baseline']}
                cm={r['match_key']:r for r in sims[target]['profiles'][pid]}
                keys=sorted(set(bm)&set(cm))
                for k in keys:
                    b=bm[k]; c=cm[k]
                    if b['actual']!=c['actual']:
                        raise RuntimeError(f'actual mismatch {k}')
                    rows.append({'competition_id':cid,'season':target,'date':b['date'],'match_key':k,'selected_profile':pid,'actual':b['actual'],'baseline_prob':b['probabilities'],'candidate_prob':c['probabilities']})
                selected_folds.append({'target_season':target,'selected_profile':pid,'selection_rows':chosen['selection_rows'],'outer_rows':len(keys)})
            domain_meta[cid]={'seasons':seasons,'selected_folds':selected_folds,'rows':sum(1 for r in rows if r['competition_id']==cid)}
        except Exception as exc:
            failures[cid]=f'{type(exc).__name__}: {exc}'

    if failures:
        raise RuntimeError({'domain_failures':failures})
    if not rows:
        raise RuntimeError('no R43AA0 OOS rows')

    b=metrics(rows,'baseline_prob'); c=metrics(rows,'candidate_prob'); d=delta(b,c)
    fold_rows=[]
    for i,f in enumerate(split3(rows),1):
        fb=metrics(f,'baseline_prob'); fc=metrics(f,'candidate_prob'); fd=delta(fb,fc)
        fold_rows.append({'fold':i,'n':len(f),'dates':[f[0]['date'],f[-1]['date']],'baseline':fb,'candidate':fc,'candidate_minus_baseline':fd})
    nonneg_top1=sum(1 for f in fold_rows if f['candidate_minus_baseline']['accuracy_pp']>=-1e-12)
    proper_good=sum(1 for f in fold_rows if f['candidate_minus_baseline']['logloss']<0 and f['candidate_minus_baseline']['brier']<0 and f['candidate_minus_baseline']['rps']<0)

    natural_draw=int(c['top1_picks'].get('draw',0))
    gate={
        'full_volume_accuracy_ge_53pct':bool(c['accuracy']>=FULL_VOLUME_ACCURACY_FLOOR),
        'top1_hits_improve':bool(c['hits']>b['hits']),
        'logloss_improves':bool(d['logloss']<0),
        'brier_improves':bool(d['brier']<0),
        'rps_improves':bool(d['rps']<0),
        'draw_logloss_improves':bool(d['draw_logloss']<0),
        'draw_brier_improves':bool(d['draw_brier']<0),
        'natural_draw_top1_positive':bool(natural_draw>0),
        'at_least_2_of_3_time_folds_top1_nonnegative':bool(nonneg_top1>=2),
        'at_least_2_of_3_time_folds_all_proper_scores_improve':bool(proper_good>=2),
    }
    gate['passed']=bool(all(gate.values()))
    out={
        'schema_version':'football3-r43aa0-bayesian-state-1x2-replay-v1',
        'status':'COMPLETE',
        'classification':'VIEWED_HISTORICAL_STRICT_OOS_REPLAY_FORMAL_WEIGHT_ZERO',
        'formal_weight':0,
        'generated_at_utc':now(),
        'governance':{
            'source_model':'bayesian_dynamic_state_oof_v500',
            'source_model_blob_sha':SOURCE_BLOB_SHA,
            'model_parameters_changed':False,
            'profile_grid_changed':False,
            'profile_selection_rule_changed':False,
            'new_labels_consumed':False,
            'historical_labels_already_viewed':True,
            'promotion_allowed_from_this_replay':False,
            'main_merge':False,
            'publication':False,
        },
        'gate_preregistration':{
            'full_volume_accuracy_floor':FULL_VOLUME_ACCURACY_FLOOR,
            'requirements':['candidate Top1 >=53%','candidate hits > baseline','LogLoss/Brier/RPS improve','draw LogLoss/Brier improve','natural draw Top1 >0','>=2/3 chronological folds nonnegative Top1 delta','>=2/3 chronological folds all proper scores improve'],
        },
        'coverage':{'competition_count':len(competitions),'row_count':len(rows),'domain_meta':domain_meta},
        'aggregate':{'baseline':b,'candidate':c,'candidate_minus_baseline':d},
        'time_folds':fold_rows,
        'fold_consistency':{'nonnegative_top1_folds':nonneg_top1,'all_proper_score_improving_folds':proper_good},
        'gate':gate,
        'action':'HISTORICAL_COMPATIBILITY_SIGNAL_ONLY_FREEZE_ARCHITECTURE_FOR_NEW_FORWARD_CONFIRMATION' if gate['passed'] else 'DO_NOT_PROMOTE_OR_RETUNE_FROM_THIS_VIEWED_REPLAY',
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':out['status'],'coverage':out['coverage']['row_count'],'aggregate':out['aggregate'],'fold_consistency':out['fold_consistency'],'gate':out['gate'],'action':out['action']},ensure_ascii=False,indent=2))
    return out


def verify():
    s=load(OUT); g=s['governance']; gp=s['gate_preregistration']
    assert s['status']=='COMPLETE' and s['formal_weight']==0
    assert g['model_parameters_changed'] is False and g['profile_grid_changed'] is False and g['profile_selection_rule_changed'] is False
    assert g['new_labels_consumed'] is False and g['historical_labels_already_viewed'] is True and g['promotion_allowed_from_this_replay'] is False
    assert abs(float(gp['full_volume_accuracy_floor'])-0.53)<1e-15
    assert len(s['time_folds'])==3
    print('R43AA0 historical Bayesian-state 1X2 replay verified')


if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
