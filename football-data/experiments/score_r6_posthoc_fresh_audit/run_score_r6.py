#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent
R5=HERE.parent/'score_r5_s60_marginal_anchor'; sys.path.insert(0,str(R5))
import run_score_r5 as r5
r2=r5.r2; r1=r5.r1; r25=r5.r25; r17=r25.r17
OUT=HERE/'results'
R25_COHORT=HERE/'r25_artifact'/'data'/'fresh_cohort_r25.json'
R26_COHORT=HERE/'r26b_artifact'/'data'/'fresh_cohort_r26b.json'


def load_rows(path,expected):
    if not path.exists(): raise RuntimeError(f'missing frozen cohort {path}')
    x=json.loads(path.read_text(encoding='utf-8')); rows=x['rows']
    if len(rows)!=expected: raise RuntimeError(f'cohort count mismatch {path}: {len(rows)} != {expected}')
    rows.sort(key=lambda z:(z['date'],z['game_id'])); return rows

def score_cohort(rows):
    _,_,ss,_,sm,lock=r25.lock_models(); tr,_,_,lock2=r2.splits()
    if lock!=lock2: raise RuntimeError('S60 lock mismatch')
    rho,_=r1.fit_rho(tr); scored=[]; by=defaultdict(list)
    for x in rows: by[x['date']].append(x)
    for d in sorted(by):
        pending=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            raw=ss.pred(x); scored.append({'date':d,'game_id':x['game_id'],'hg':int(x['home_goals']),'ag':int(x['away_goals']),'raw':raw}); pending.append((x,raw))
        for x,raw in pending: r17.goal_only_base_update(ss,x,raw)
    return scored,sm,rho,lock

def pack(rows,sm,rho):
    ip=r1.metrics(rows,None); dc=r1.metrics(rows,rho); an=r5.metrics(rows,sm,rho)
    return {'count':len(rows),'independent_poisson':ip,'dixon_coles':dc,'S60_anchored_dixon_coles':an,'delta_anchor_minus_dc':r2.d(an,dc),'delta_anchor_minus_ip':r2.d(an,ip)}

def main():
    r25rows=load_rows(R25_COHORT,75); a,sm,rho,lock=score_cohort(r25rows); p25=pack(a,sm,rho)
    r26rows=load_rows(R26_COHORT,68); b,sm2,rho2,lock2=score_cohort(r26rows)
    if rho2!=rho or lock2!=lock: raise RuntimeError('locked parameter mismatch across cohorts')
    p26=pack(b,sm2,rho); combined=pack(a+b,sm,rho)
    out={'schema_version':'football3-score-r6-posthoc-fresh-audit','status':'COMPLETE','classification':'POST_HOC_FRESH_SCORE_AUDIT_NOT_PROSPECTIVE','formal_weight':0,
      'governance':{'R5_locked_before_this_score_audit':True,'R5_parameters_changed':False,'S60_1x2_frozen_unchanged':True,'cohorts_loaded_from_original_R25_R26b_artifacts':True,'cohorts_re_downloaded':False,'fresh_score_labels_used_for_parameter_fit':False,'same_date_results_withheld':True,'fresh_xg_used':False,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False,'not_formal_prospective_because_1x2_outcomes_were_previously_observed':True},
      'source_artifacts':{'R25':{'run_id':32941250515,'digest':'sha256:e852e1948a2a02382e6afd064062feb429fdacf4d4541f32c2e895758f68e05c','rows':75},'R26b':{'run_id':32942144995,'digest':'sha256:5f7caa1aa0d14d87d6dd38b3d2ab3e1e9f546048eaa51ee8fe300bd6d13b53ea','rows':68}},
      'locked':{'dc_rho':rho,'S60_lock':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]}},'R25':p25,'R26b':p26,'combined_143':combined}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'summary_score_r6.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2))

def verify():
    s=json.loads((OUT/'summary_score_r6.json').read_text()); g=s['governance']; q=s['locked']['S60_lock']
    assert g['R5_locked_before_this_score_audit'] and not g['R5_parameters_changed'] and g['cohorts_loaded_from_original_R25_R26b_artifacts'] and not g['cohorts_re_downloaded']
    assert not g['fresh_score_labels_used_for_parameter_fit'] and g['same_date_results_withheld'] and not g['fresh_xg_used'] and not g['odds_used'] and not g['market_prices_used']
    assert s['R25']['count']==75 and s['R26b']['count']==68 and s['combined_143']['count']==143
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    print('SCORE_R6_VERIFY_PASS')
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r6.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
