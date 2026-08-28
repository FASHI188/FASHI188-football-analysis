#!/usr/bin/env python3
from __future__ import annotations

import json, math
from collections import Counter
from pathlib import Path

from scipy.optimize import least_squares

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEDGER = ROOT / 'forward' / 'v6_market_first_events_v651.json'
OUT = HERE / 'results' / 'summary_r43i3_frozen_market_ah_ou_score_reconstruction.json'
C = ('home','draw','away')
MAX_GOALS = 14


def load(p):
    return json.loads(p.read_text(encoding='utf-8'))


def devig2(a, b):
    ia, ib = 1.0/float(a), 1.0/float(b)
    s = ia + ib
    return ia/s, ib/s


def is_half_line(x):
    x = abs(float(x))
    return abs((x - math.floor(x)) - 0.5) < 1e-9


def poisson_probs(lam):
    out = [math.exp(-lam)]
    for k in range(1, MAX_GOALS + 1):
        out.append(out[-1] * lam / k)
    s = sum(out)
    return [x/s for x in out]


def matrix(lh, la):
    ph, pa = poisson_probs(lh), poisson_probs(la)
    return [[ph[h]*pa[a] for a in range(MAX_GOALS+1)] for h in range(MAX_GOALS+1)]


def surfaces_from_matrix(mat, ah_line, ou_line):
    p_ah = p_over = 0.0
    p_home = p_draw = p_away = 0.0
    for h, row in enumerate(mat):
        for a, p in enumerate(row):
            if h > a: p_home += p
            elif h == a: p_draw += p
            else: p_away += p
            if h + float(ah_line) > a: p_ah += p
            if h + a > float(ou_line): p_over += p
    return p_ah, p_over, {'home':p_home,'draw':p_draw,'away':p_away}


def reconstruct(ah_line, q_ah, ou_line, q_over):
    # Fixed, untuned inversion: independent Poisson lambdas are the only two unknowns,
    # matched to the two frozen no-push binary surfaces AH and OU.
    def resid(z):
        lh, la = math.exp(float(z[0])), math.exp(float(z[1]))
        p_ah, p_over, _ = surfaces_from_matrix(matrix(lh, la), ah_line, ou_line)
        return [p_ah-q_ah, p_over-q_over]
    total0 = max(0.4, float(ou_line) + 0.05)
    diff0 = -float(ah_line)
    lh0 = max(0.15, (total0 + diff0)/2.0)
    la0 = max(0.15, (total0 - diff0)/2.0)
    fit = least_squares(resid, [math.log(lh0), math.log(la0)], bounds=([math.log(0.05)]*2,[math.log(8.0)]*2), xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=250)
    lh, la = math.exp(float(fit.x[0])), math.exp(float(fit.x[1]))
    p_ah, p_over, p1x2 = surfaces_from_matrix(matrix(lh, la), ah_line, ou_line)
    err = max(abs(p_ah-q_ah), abs(p_over-q_over))
    return p1x2, {'lambda_home':lh,'lambda_away':la,'fit_max_abs_error':err,'success':bool(fit.success)}


def pick(p):
    return max(C, key=lambda k:(float(p[k]), -C.index(k)))


def metrics(rows, field):
    eps = 1e-15
    hits=0; ll=br=rps=0.0; picks=Counter(); hitc=Counter(); actual=Counter(); draw_truth_prob=[]
    for r in rows:
        p=r[field]; y=r['truth']; idx=C.index(y)
        hits += int(pick(p)==y); picks[pick(p)] += 1; hitc[pick(p)] += int(pick(p)==y); actual[y]+=1
        ll += -math.log(max(eps,min(1.0-eps,float(p[y]))))
        br += sum((float(p[k])-(1.0 if k==y else 0.0))**2 for k in C)
        # ordered home-draw-away RPS, same convention used by the research line
        obs1 = 1.0 if idx <= 0 else 0.0
        obs2 = 1.0 if idx <= 1 else 0.0
        c1=float(p['home']); c2=float(p['home'])+float(p['draw'])
        rps += ((c1-obs1)**2 + (c2-obs2)**2)/2.0
        if y=='draw': draw_truth_prob.append(float(p['draw']))
    n=len(rows)
    return {'count':n,'hits':hits,'top1_accuracy':hits/n if n else None,'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,'top1_picks':dict(picks),'top1_hits':dict(hitc),'actuals':dict(actual),'mean_draw_probability_on_actual_draws':sum(draw_truth_prob)/len(draw_truth_prob) if draw_truth_prob else None}


def run():
    x=load(LEDGER); preds={}; settled={}
    for e in x['events']:
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN': preds[str(e['match_id'])]=e
        elif e.get('event_type')=='RESULT_SETTLED': settled[str(e['match_id'])]=e
    rows=[]; rejects=Counter(); fit_errors=[]
    for mid,se in settled.items():
        pe=preds.get(mid)
        if not pe: rejects['settlement_without_prediction']+=1; continue
        fs=pe['payload'].get('frozen_surfaces') or {}; ah=fs.get('asian_handicap'); ou=fs.get('over_under')
        if not isinstance(ah,dict) or not isinstance(ou,dict): rejects['missing_ah_or_ou']+=1; continue
        try:
            ah_line=float(ah['line']); ou_line=float(ou['line'])
            if not is_half_line(ah_line): rejects['ah_not_half_line']+=1; continue
            if not is_half_line(ou_line): rejects['ou_not_half_line']+=1; continue
            qah,_=devig2(ah['home'],ah['away']); qover,_=devig2(ou['over'],ou['under'])
            pg,fit=reconstruct(ah_line,qah,ou_line,qover)
            if (not fit['success']) or fit['fit_max_abs_error']>0.015:
                rejects['fit_not_exact_enough']+=1; fit_errors.append(fit['fit_max_abs_error']); continue
            pm={k:float(pe['payload']['prediction']['probabilities'][k]) for k in C}; sm=sum(pm.values()); pm={k:pm[k]/sm for k in C}
            y=str(se['payload']['result']['actual_result'])
            rows.append({'truth':y,'market':pm,'generated':pg,'fit':fit})
        except Exception:
            rejects['parse_or_fit_error']+=1
    bm=metrics(rows,'market'); gm=metrics(rows,'generated')
    delta={'hits':gm['hits']-bm['hits'],'accuracy_pp':100.0*(gm['top1_accuracy']-bm['top1_accuracy']) if rows else None,'logloss':gm['logloss']-bm['logloss'] if rows else None,'brier':gm['brier']-bm['brier'] if rows else None,'rps':gm['rps']-bm['rps'] if rows else None,'draw_pick_delta':gm['top1_picks'].get('draw',0)-bm['top1_picks'].get('draw',0)}
    signal=bool(len(rows)>=30 and delta['hits']>=0 and delta['logloss']<0 and delta['brier']<0 and delta['rps']<0)
    result={
      'schema_version':'football3-r43i3-frozen-market-ah-ou-score-reconstruction-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_DISCOVERY_ON_PREMATCH_FROZEN_SURFACES',
      'question':'Can a zero-trained independent-Poisson reconstruction from frozen no-push AH+OU surfaces recover better 1X2/draw structure than the frozen direct market 1X2?',
      'governance':{'frozen_surfaces_only':True,'existing_settlements_only':True,'outcomes_not_used_in_reconstruction':True,'model_training':False,'parameter_search':False,'threshold_search':False,'line_filter':'AH half-lines and OU half-lines only; no push/quarter-line approximation','reconstruction':'solve two independent-Poisson lambdas from de-vigged AH cover probability and OU over probability','formal_promotion_allowed':False},
      'coverage':{'settled_market_predictions':len(settled),'eligible_reconstructed':len(rows),'rejects':dict(sorted(rejects.items())),'max_fit_error_seen':max(fit_errors) if fit_errors else None},
      'metrics':{'direct_market_1x2':bm,'ah_ou_generated_1x2':gm},'generated_minus_direct_market':delta,
      'discovery_gate':{'minimum_eligible':30,'top1_hits_must_not_decline':True,'proper_scores_must_all_improve':True,'passed':signal,'action':'LOCK_FORMULA_FOR_NEW_FORWARD_CONFIRMATION' if signal else 'NO_STRUCTURAL_BREAKTHROUGH_DO_NOT_TUNE_ON_SETTLED_SAMPLE'}
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result,ensure_ascii=False,indent=2)); return result


def verify():
    x=load(OUT); assert x['status']=='COMPLETE' and x['formal_weight']==0
    g=x['governance']; assert g['model_training'] is False and g['parameter_search'] is False and g['threshold_search'] is False and g['formal_promotion_allowed'] is False
    print('R43I3 contract verified')

if __name__=='__main__':
    import sys
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
