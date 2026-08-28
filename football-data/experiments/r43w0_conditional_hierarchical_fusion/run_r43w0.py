#!/usr/bin/env python3
from __future__ import annotations
import json,math,sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
for p in (ROOT/'experiments'/'r43t0_dynamic_bivariate_residual_state',ROOT/'experiments'/'r43u0_fixed_diagonal_inflation',ROOT/'experiments'/'r43q0_sharp_market_score_base'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import run_r43t0 as t0
import run_r43u0 as u0
import run_r43q0 as q0
OUT=HERE/'results'/'summary_r43w0_conditional_hierarchical_fusion.json'
SEED_N=18;FOLDS=3;LOW_TOTAL=2.50;RIDGE_G=30.;RIDGE_L=60.;RIDGE_C=120.;BOUND=.35;BETA_BOUND=.50;TARGET=.53
CLASSES=('home','draw','away')
def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def build_rows():
    rows=t0.build_rows();groups=t0.group_rows(rows);x=np.zeros(2);P=np.eye(2)*t0.INITIAL_VAR;warm=[];scored=[];active=False
    for g in groups:
        xp=t0.STATE_AR*x;Pp=(t0.STATE_AR**2)*P+np.eye(2)*t0.PROCESS_VAR
        if not active and len(warm)>=t0.WARMUP_MIN:active=True
        for r in g:
            h,a=t0.project_lambdas(float(r['lambda_home']),float(r['lambda_away']),xp)
            r['u0']=q0.matrix_1x2(u0.inflate(q0.score_matrix(h,a)));r['dynamic_total']=h+a
            (scored if active else warm).append(r)
        x,P=t0.simultaneous_update(xp,Pp,g)
    return warm,scored
def mix(pu,pm,b):
    z={k:math.log(max(pu[k],1e-15))+b*(math.log(max(pm[k],1e-15))-math.log(max(pu[k],1e-15))) for k in CLASSES};m=max(z.values());e={k:math.exp(z[k]-m) for k in CLASSES};s=sum(e.values());return {k:e[k]/s for k in CLASSES}
def beta(th,r,ci):
    b=float(th[0])+float(th[1])*(float(r['dynamic_total'])<LOW_TOTAL)+float(th[2+ci[str(r['competition_id'])]])
    return float(np.clip(b,-BETA_BOUND,BETA_BOUND))
def fit(train,comps):
    ci={c:i for i,c in enumerate(comps)};n=2+len(comps)
    def obj(th):
        loss=sum(-math.log(max(mix(r['u0'],r['market'],beta(th,r,ci))[r['y']],1e-15)) for r in train)
        return float(loss+.5*RIDGE_G*th[0]**2+.5*RIDGE_L*th[1]**2+.5*RIDGE_C*np.sum(th[2:]**2))
    res=minimize(obj,np.zeros(n),method='L-BFGS-B',bounds=[(-BOUND,BOUND)]*n,options={'maxiter':400,'ftol':1e-12})
    if not np.isfinite(res.fun):raise RuntimeError('fusion optimizer failed')
    return np.asarray(res.x),ci
def bs(rows):
    v=np.array([r['fusion_beta'] for r in rows]);return {'mean':float(v.mean()),'min':float(v.min()),'max':float(v.max()),'mean_abs':float(np.abs(v).mean())}
def run():
    warm,rows=build_rows();comps=sorted({str(r['competition_id']) for r in rows});seed=rows[:SEED_N];test=rows[SEED_N:];history=list(seed);out=[];fr=[]
    for i,f in enumerate(t0.time_folds(test,FOLDS),1):
        th,ci=fit(history,comps)
        for r in f:r['fusion_beta']=beta(th,r,ci);r['fusion']=mix(r['u0'],r['market'],r['fusion_beta'])
        mm=t0.metrics(f,'market');bm=t0.metrics(f,'u0');fm=t0.metrics(f,'fusion')
        fr.append({'fold':i,'train_n':len(history),'test_n':len(f),'dates':[f[0]['kickoff_utc'],f[-1]['kickoff_utc']],'theta':{'global':float(th[0]),'low_total':float(th[1]),'competition':{c:float(th[2+j]) for j,c in enumerate(comps)}},'beta_summary':bs(f),'market':mm,'u0':bm,'fusion':fm,'fusion_minus_u0':t0.delta(bm,fm),'fusion_minus_market':t0.delta(mm,fm)})
        out+=f;history+=f
    mm=t0.metrics(out,'market');bm=t0.metrics(out,'u0');fm=t0.metrics(out,'fusion');db=t0.delta(bm,fm);dm=t0.delta(mm,fm)
    nonneg=sum(x['fusion_minus_u0']['accuracy_pp']>=-1e-12 for x in fr);posll=sum(x['fusion_minus_u0']['logloss']<0 for x in fr)
    gate=bool(db['accuracy_pp']>=0 and db['logloss']<0 and db['brier']<0 and db['rps']<0 and db['draw_logloss']<=0 and db['draw_brier']<=0 and nonneg>=2 and posll>=2 and fm['top1_picks']['draw']>=bm['top1_picks']['draw'])
    r={'schema_version':'football3-r43w0-conditional-hierarchical-fusion-v1','status':'COMPLETE','classification':'POSTVIEW_DEVELOPMENT_ON_EXISTING_PREMATCH_FROZEN_MARKETS','formal_weight':0,'governance':{'inherits_r43u0_unchanged':True,'market_same_frozen_snapshot':True,'outcomes_used_only_in_prior_training':True,'fixed_50_50_fusion':False,'parameter_search':False,'ridge_search':False,'cutoff_search':False,'threshold_search':False,'draw_override':False,'draw_count_forced':False,'unified_argmax':True,'main_merge':False,'publication':False},'design':{'formula':'normalize(P_U0*exp(beta_i*(log(P_market)-log(P_U0))))','beta_hierarchy':['global','I(dynamic_total<2.50)','competition deviation'],'low_total_cutoff':LOW_TOTAL,'global_ridge':RIDGE_G,'low_total_ridge':RIDGE_L,'competition_ridge':RIDGE_C,'component_bound':BOUND,'final_beta_bound':BETA_BOUND,'seed_n':SEED_N,'folds':FOLDS,'full_volume_target_accuracy_floor':TARGET},'coverage':{'u0_total':len(rows),'seed_n':len(seed),'scored_n':len(out),'competition_count':len(comps)},'aggregate':{'market':mm,'u0':bm,'conditional_fusion':fm,'fusion_minus_u0':db,'fusion_minus_market':dm,'nonnegative_top1_folds_vs_u0':int(nonneg),'positive_logloss_folds_vs_u0':int(posll),'beta_summary':bs(out)},'folds':fr,'gate':{'architecture_passed':gate,'full_volume_53pct_target_met':bool(fm['top1_accuracy']>=TARGET),'action':'FREEZE_CONDITIONAL_HIERARCHICAL_FUSION_FOR_NEW_FORWARD_CONFIRMATION' if gate else 'RETAIN_R43U0_AND_DO_NOT_RETUNE_FUSION_ON_THESE_MATCHES'}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(r,ensure_ascii=False,indent=2));return r
def verify():
    x=load(OUT);g=x['governance'];assert x['formal_weight']==0 and g['inherits_r43u0_unchanged'] and not g['fixed_50_50_fusion'] and not g['parameter_search'] and not g['ridge_search'] and not g['cutoff_search'] and not g['threshold_search'] and not g['draw_count_forced'];assert x['design']['low_total_cutoff']==LOW_TOTAL;print('R43W0 contract verified')
if __name__=='__main__':
    c=sys.argv[1] if len(sys.argv)>1 else 'run'
    run() if c=='run' else verify() if c=='verify' else (_ for _ in ()).throw(SystemExit(c))
