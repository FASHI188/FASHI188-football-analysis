#!/usr/bin/env python3
"""V6.16.7 full P(T) * P(D|T,X) joint-score challenger.

This fixes the hard-Top1-total failure of V6.16.6. For every test match:
1) obtain the V6.16.3 1X2+O/U IPF matrix and retain its full total-goal marginal P(T);
2) use strictly-prior trained AH-informed conditional models to predict P(D|T,X) for
   every exact T=0..6;
3) map each valid (T,D) to score cells, preserving the V6.16.3 conditional structure for
   the 7+ bucket;
4) re-run the same 1X2+O/U IPF market coordination on this new prior;
5) audit convergence, probability conservation and score/total/1X2 metrics.

Thus no single T is selected before score formation. Historical odds have no original quote
timestamps, so this is research-only, formal_weight=0.
"""
from __future__ import annotations
import json,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import validate_conditional_margin_ah_v6166 as cond
import validate_joint_market_ipf_v6163 as joint
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals,read_processed_matches
OUT=ROOT/'manifests'/'v6_full_joint_td_ah_v6167_status.json'
TESTS=cond.TESTS;COMPS=cond.COMPS;TOTAL_KEYS=cond.TOTAL_KEYS


def conditional_d_probs(x,t,models):
    if t==0:return {0:1.0}
    m=models.get(t)
    if m is None:return {}
    if isinstance(m,tuple):return {int(m[1]):1.0}
    probs=m.predict_proba([x])[0];classes=list(m.named_steps['logisticregression'].classes_)
    out={}
    for p,d in zip(probs,classes):
        d=int(d)
        if (t+d)%2==0 and (t+d)//2>=0 and (t-d)//2>=0:out[d]=float(p)
    s=sum(out.values());return {d:p/s for d,p in out.items()} if s>0 else {}


def build_td_prior(base_matrix,x,models):
    marg=derive_score_marginals(base_matrix);tm={k:float(marg['total_goals'][k]) for k in TOTAL_KEYS}
    cells=[dict(c,probability=0.0) for c in base_matrix]
    index={(int(c['home_goals']),int(c['away_goals'])):i for i,c in enumerate(cells)}
    # Exact totals 0..6 from learned conditional D.
    for t in range(7):
        pd=conditional_d_probs(x,t,models)
        if not pd:
            # conservative fallback to base conditional at this exact total
            idx=[i for i,c in enumerate(base_matrix) if int(c['home_goals'])+int(c['away_goals'])==t]
            mass=sum(float(base_matrix[i]['probability']) for i in idx)
            if mass>0:
                for i in idx:cells[i]['probability']=tm[str(t)]*float(base_matrix[i]['probability'])/mass
            continue
        for d,p in pd.items():
            h=(t+d)//2;a=(t-d)//2;i=index.get((h,a))
            if i is not None:cells[i]['probability']+=tm[str(t)]*p
    # Preserve base conditional structure within T>=7 because 7+ is not an exact T.
    idx=[i for i,c in enumerate(base_matrix) if int(c['home_goals'])+int(c['away_goals'])>=7]
    mass=sum(float(base_matrix[i]['probability']) for i in idx)
    if mass>0:
        for i in idx:cells[i]['probability']=tm['7+']*float(base_matrix[i]['probability'])/mass
    s=sum(float(c['probability']) for c in cells)
    if s<=0:return None
    for c in cells:c['probability']=float(c['probability'])/s
    return cells


def one_vec(m):
    x=derive_score_marginals(m)['1x2'];return [float(x[k]) for k in ('home','draw','away')]
def total_vec(m):
    x=derive_score_marginals(m)['total_goals'];return [float(x[k]) for k in TOTAL_KEYS]
def rid(h,a):return 0 if h>a else 1 if h==a else 2

def summarize(rows):
    n=len(rows)
    if not n:return {'count':0}
    def mean(k):return sum(r[k] for r in rows)/n
    return {'count':n,'baseline_total_rps':mean('base_total_rps'),'td_total_rps':mean('td_total_rps'),'total_rps_delta':mean('td_total_rps')-mean('base_total_rps'),'baseline_total_top1':mean('base_total_top1'),'td_total_top1':mean('td_total_top1'),'total_top1_delta':mean('td_total_top1')-mean('base_total_top1'),'baseline_score_top1':mean('base_score_top1'),'td_score_top1':mean('td_score_top1'),'score_top1_delta':mean('td_score_top1')-mean('base_score_top1'),'baseline_score_top3':mean('base_score_top3'),'td_score_top3':mean('td_score_top3'),'score_top3_delta':mean('td_score_top3')-mean('base_score_top3'),'baseline_1x2_brier':mean('base_1x2_brier'),'td_1x2_brier':mean('td_1x2_brier'),'one_x_two_brier_delta':mean('td_1x2_brier')-mean('base_1x2_brier')}


def eval_season(allrows,season,cfg):
    train=[r for r in allrows if cond.season_year(r['season'])<cond.season_year(season)];models=cond.fit_dmodels(train);out=[];meta={}
    for cid in COMPS:
        look=cond.market_lookup(allrows,season,cid);params=ou.params_by_season(cid).get(season)
        if not params:meta[cid]={'reason':'NO_FORMAL_PARAMS','market_rows':len(look)};continue
        ms=[m for m in read_processed_matches(cid) if str(m.season)==season];bd=defaultdict(list)
        for m in ms:bd[m.date].append(m)
        hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,season);attempt=conv=0;maxit=0;maxres=0.
        wc=int(cfg['validation']['warmup_competition_matches']);wt=int(cfg['validation']['warmup_team_matches'])
        for dt in sorted(bd):
          for m in sorted(bd[dt],key=lambda z:(z.home_team,z.away_team)):
            row=look.get((m.date.isoformat(),m.home_team,m.away_team))
            if len(hist)>=wc and hc[m.home_team]>=wt and ac[m.away_team]>=wt and row:
                try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
                except Exception:p=None
                if p:
                    formal=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);base,a0=joint.ipf(formal,row['one_x_two'],float(row['p_over25']))
                    if base is not None and a0.get('converged'):
                        prior=build_td_prior(base,row['x'],models);attempt+=1
                        if prior is not None:
                            td,a1=joint.ipf(prior,row['one_x_two'],float(row['p_over25']))
                            if td is not None and a1.get('converged'):
                                conv+=1;maxit=max(maxit,int(a1['iterations']));maxres=max(maxres,float(a1['max_residual']));bt=total_vec(base);tt=total_vec(td);bo=one_vec(base);to=one_vec(td);tb=min(7,m.home_goals+m.away_goals);ri=rid(m.home_goals,m.away_goals)
                                out.append({'season':season,'competition_id':cid,'base_total_rps':ou.rps(bt,tb),'td_total_rps':ou.rps(tt,tb),'base_total_top1':int(max(range(8),key=lambda i:bt[i])==tb),'td_total_top1':int(max(range(8),key=lambda i:tt[i])==tb),'base_score_top1':ou.top_score(base,1,m.home_goals,m.away_goals),'td_score_top1':ou.top_score(td,1,m.home_goals,m.away_goals),'base_score_top3':ou.top_score(base,3,m.home_goals,m.away_goals),'td_score_top3':ou.top_score(td,3,m.home_goals,m.away_goals),'base_1x2_brier':ou.brier3(bo,ri),'td_1x2_brier':ou.brier3(to,ri)})
            hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
        meta[cid]={'market_rows':len(look),'attempted':attempt,'converged':conv,'max_iterations':maxit,'max_residual':maxres}
    return out,meta

def main():
    rows=cond.load_market_rows();cfg=load_config();by={};meta={};alltest=[]
    for s in TESTS:
        r,m=eval_season(rows,s,cfg);by[s]=summarize(r);meta[s]=m;alltest+=r
    payload={'schema_version':'V6.16.7-full-joint-TD-AH-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'strictly_prior_conditional_training':True,'total_marginal_source':'V6.16.3 full P(T)','conditional_source':'V6.16.6 P(D|T,X)','score_mapping':'H=(T+D)/2,A=(T-D)/2 with parity/nonnegative gate','7plus':'preserve V6.16.3 conditional structure','final_market_coordination':'same 1X2+OU IPF as V6.16.3','single_total_selected_before_score':False},'by_season':by,'aggregate':summarize(alltest),'replication':{'seasons_score_top1_improved':sum(1 for s in TESTS if by[s].get('score_top1_delta',0)>0),'seasons_score_top3_improved':sum(1 for s in TESTS if by[s].get('score_top3_delta',0)>0),'seasons_total_rps_nonworse':sum(1 for s in TESTS if by[s].get('total_rps_delta',1)<=1e-12)},'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'by_season':by,'aggregate':payload['aggregate'],'replication':payload['replication']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
