#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from scipy.optimize import least_squares, minimize_scalar

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from platform_core import normalize_team_token

LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
BUNDLES=ROOT/'evidence'/'market_line_bundles_prospective'
OUT=HERE/'results'/'summary_r43k0_frozen_multiline_ou_draw_reconstruction.json'
C=('home','draw','away'); MAX_GOALS=16

def load(p): return json.loads(p.read_text(encoding='utf-8'))
def dt(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00')); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def key(cid,kick,home,away): return (str(cid),dt(kick).astimezone(timezone.utc).replace(microsecond=0).isoformat(),normalize_team_token(str(home)),normalize_team_token(str(away)))
def devig3(o):
    z={k:1.0/float(o[k]) for k in C}; s=sum(z.values()); return {k:z[k]/s for k in C}
def devig2(a,b):
    x,y=1.0/float(a),1.0/float(b); s=x+y; return x/s,y/s
def pick(p): return max(C,key=lambda k:(float(p[k]),-C.index(k)))
def poisson(lam):
    p=[math.exp(-lam)]
    for k in range(1,MAX_GOALS+1): p.append(p[-1]*lam/k)
    s=sum(p); return [x/s for x in p]
def tail_over(lam,line):
    p=poisson(lam); cutoff=math.floor(float(line)); return sum(p[cutoff+1:])
def fit_total(lines):
    def res(z):
        lam=math.exp(float(z[0])); return [tail_over(lam,line)-q for line,q in lines]
    weighted=sum((float(line)+0.5)*q for line,q in lines)/max(1e-9,sum(q for _,q in lines)); init=min(5.0,max(0.4,weighted))
    f=least_squares(res,[math.log(init)],bounds=([math.log(0.1)],[math.log(8.0)]),xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=200)
    lam=math.exp(float(f.x[0])); errs=[abs(tail_over(lam,l)-q) for l,q in lines]
    return lam,max(errs) if errs else None,sum(e*e for e in errs)/len(errs) if errs else None

def score_probs(lam,p_home_goal):
    # Poisson total + Binomial score allocation == independent Poisson split.
    lh=lam*p_home_goal; la=lam*(1-p_home_goal); ph=poisson(lh); pa=poisson(la); out={k:0.0 for k in C}
    for h,x in enumerate(ph):
        for a,y in enumerate(pa):
            z=x*y
            if h>a: out['home']+=z
            elif h==a: out['draw']+=z
            else: out['away']+=z
    s=sum(out.values()); return {k:out[k]/s for k in C}
def fit_share(lam,target_share):
    def obj(p):
        q=score_probs(lam,float(p)); den=q['home']+q['away']; sh=q['home']/den if den>0 else 0.5; return (sh-target_share)**2
    r=minimize_scalar(obj,bounds=(0.02,0.98),method='bounded',options={'xatol':1e-12}); p=float(r.x); q=score_probs(lam,p); den=q['home']+q['away']; err=abs((q['home']/den if den else 0.5)-target_share)
    return p,q,err

def metrics(rows,field):
    eps=1e-15; hits=0; ll=br=rps=0.0; picks=Counter(); hitc=Counter(); actual=Counter(); drawtruth=[]
    for r in rows:
        p=r[field]; y=r['truth']; idx=C.index(y); pk=pick(p); hits+=int(pk==y); picks[pk]+=1; hitc[pk]+=int(pk==y); actual[y]+=1
        ll += -math.log(max(eps,min(1-eps,float(p[y])))); br += sum((float(p[k])-(1.0 if k==y else 0.0))**2 for k in C)
        obs1=1.0 if idx<=0 else 0.0; obs2=1.0 if idx<=1 else 0.0; c1=float(p['home']); c2=c1+float(p['draw']); rps += ((c1-obs1)**2+(c2-obs2)**2)/2
        if y=='draw': drawtruth.append(float(p['draw']))
    n=len(rows); return {'count':n,'hits':hits,'top1_accuracy':hits/n if n else None,'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,'top1_picks':dict(picks),'top1_hits':dict(hitc),'actuals':dict(actual),'mean_draw_probability_on_actual_draws':sum(drawtruth)/len(drawtruth) if drawtruth else None}
def run():
    led=load(LEDGER); predkey={}; truth={}
    for e in led['events']:
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN':
            f=e['payload']['fixture_identity']; predkey[str(e['match_id'])]=key(f['competition_id'],f['kickoff_at'],f['home_team'],f['away_team'])
        elif e.get('event_type')=='RESULT_SETTLED': truth[str(e['match_id'])]=str(e['payload']['result']['actual_result'])
    truth_by_key={predkey[mid]:y for mid,y in truth.items() if mid in predkey}
    chosen={}; rej=Counter()
    for p in BUNDLES.glob('*.json') if BUNDLES.exists() else []:
        try:
            x=load(p); k=key(x['competition_id'],x['kickoff_utc'],x['home_team'],x['away_team']); y=truth_by_key.get(k)
            if y is None: rej['no_settled_overlap']+=1; continue
            obs=dt(x['observed_at_utc']); kick=dt(x['kickoff_utc'])
            if obs>=kick: rej['invalid_timing']+=1; continue
            prev=chosen.get(k)
            if prev is None or obs<prev[0]: chosen[k]=(obs,p,x,y)
        except Exception: rej['bundle_parse_error']+=1
    rows=[]; fitstats=[]
    for k,(obs,p,x,y) in chosen.items():
        try:
            lines=[]
            for r in x.get('over_under_lines') or []:
                line=float(r['line'])
                if abs((line-math.floor(line))-0.5)>1e-9: continue
                q,_=devig2(r['over'],r['under']); lines.append((line,q))
            lines=sorted({line:q for line,q in lines}.items())
            if len(lines)<3: rej['fewer_than_3_half_ou_lines']+=1; continue
            parent=ROOT/str(x['parent_snapshot_path'])
            if not parent.exists(): rej['parent_snapshot_missing']+=1; continue
            px=load(parent); direct=devig3(px['one_x_two']); target=direct['home']/(direct['home']+direct['away'])
            lam,tailmax,tailmse=fit_total(lines); pshare,generated,shareerr=fit_share(lam,target)
            if shareerr>1e-5: rej['direction_share_fit_error']+=1; continue
            rows.append({'truth':y,'direct':direct,'generated':generated})
            fitstats.append({'line_count':len(lines),'lambda_total':lam,'tail_max_abs_error':tailmax,'tail_mse':tailmse,'home_goal_share':pshare,'direction_share_error':shareerr})
        except Exception: rej['reconstruction_error']+=1
    dm=metrics(rows,'direct'); gm=metrics(rows,'generated'); delta={'hits':gm['hits']-dm['hits'],'accuracy_pp':100*(gm['top1_accuracy']-dm['top1_accuracy']) if rows else None,'logloss':gm['logloss']-dm['logloss'] if rows else None,'brier':gm['brier']-dm['brier'] if rows else None,'rps':gm['rps']-dm['rps'] if rows else None,'draw_pick_delta':gm['top1_picks'].get('draw',0)-dm['top1_picks'].get('draw',0)}
    signal=bool(len(rows)>=15 and delta['hits']>=0 and delta['logloss']<0 and delta['brier']<0 and delta['rps']<0)
    result={'schema_version':'football3-r43k0-frozen-multiline-ou-draw-reconstruction-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_DISCOVERY_ON_PROSPECTIVE_MULTILINE_OU_CURVES','question':'Can the full frozen OU curve determine total-goal uncertainty and thereby recover better draw/1X2 probabilities without using the direct-market draw probability?',
      'governance':{'prospective_bundles_only':True,'earliest_bundle_per_fixture':True,'minimum_half_ou_lines':3,'direct_draw_probability_used':False,'direct_home_away_non_draw_ratio_only':True,'model_training':False,'parameter_search':False,'draw_threshold':False,'formal_promotion_allowed':False},
      'coverage':{'bundle_files_seen':sum(1 for _ in BUNDLES.glob('*.json')) if BUNDLES.exists() else 0,'settled_unique_bundles':len(chosen),'eligible_reconstructed':len(rows),'rejects':dict(sorted(rej.items()))},
      'fit_summary':{'median_ou_lines':sorted(z['line_count'] for z in fitstats)[len(fitstats)//2] if fitstats else None,'median_total_lambda':sorted(z['lambda_total'] for z in fitstats)[len(fitstats)//2] if fitstats else None,'max_tail_abs_error':max((z['tail_max_abs_error'] for z in fitstats),default=None)},
      'metrics':{'same_time_direct_1x2':dm,'multiline_ou_generated_1x2':gm},'generated_minus_direct':delta,'discovery_gate':{'minimum_overlap':15,'top1_hits_must_not_decline':True,'proper_scores_must_all_improve':True,'passed':signal,'action':'LOCK_MULTILINE_OU_DRAW_STRUCTURE_FOR_NEW_FORWARD' if signal else 'NO_MULTILINE_OU_BREAKTHROUGH_DO_NOT_TUNE_ON_SETTLED_SAMPLE'}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result,ensure_ascii=False,indent=2)); return result
def verify():
    x=load(OUT); assert x['status']=='COMPLETE' and x['formal_weight']==0; g=x['governance']; assert g['direct_draw_probability_used'] is False and g['model_training'] is False and g['parameter_search'] is False and g['draw_threshold'] is False and g['formal_promotion_allowed'] is False; print('R43K0 contract verified')
if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
