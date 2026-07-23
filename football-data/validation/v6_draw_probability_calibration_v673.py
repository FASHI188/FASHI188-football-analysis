#!/usr/bin/env python3
"""V6.7.3 draw-probability calibration audit.

Separates 'draw is rarely Top-1' from 'draw probability is miscalibrated'. Fits a one-dimensional
logistic calibrator to the market draw probability on 2022/23+2023/24, selects regularization on
2024/25, and evaluates 2025/26 once. Home/Away conditional ratio is preserved exactly.
"""
from __future__ import annotations
import json, math, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation';ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_direct_outcome_mvp_v600 as base
import v6_market_residual_fusion_v620 as mkt
import v6_multimarket_draw_side_v643 as mm
from platform_core import PlatformError
OUT=ROOT/'manifests'/'v6_draw_probability_calibration_v673_status.json'
SEASONS=("2022/23","2023/24","2024/25","2025/26");SEASON_CODES={"2022/23":"2223","2023/24":"2324","2024/25":"2425","2025/26":"2526"};L2_GRID=(0.1,1.0,10.0,100.0,1000.0);EPS=1e-12

def logit(p):p=min(1-1e-8,max(1e-8,p));return math.log(p/(1-p))
def build():
    out={s:[] for s in SEASONS};audit={}
    for cid,code in mm.LEAGUES.items():
        built=mkt._build_domain_rows_with_identity(cid,list(SEASONS));audit[cid]={}
        for season in SEASONS:
            raw,url=mkt._download_csv(code,SEASON_CODES[season]);matched,stats=mm.match_rows(cid,built[season],raw);rows=[]
            for r in matched:
                q=r['surface']['one'];z=dict(r);z['draw_cal_x']=[1.0,logit(q['draw'])];z['draw_y']=1 if r['actual_result']=='draw' else 0;rows.append(z)
            out[season]+=rows;audit[cid][season]={'url':url,'matched':len(rows),'stats':stats}
    return out,audit

def fit(rows,l2):return base._fit_binary(rows,'draw_cal_x','draw_y',l2)
def qcal(r,model):
    q=r['surface']['one'];pd=min(1-1e-6,max(1e-6,base._predict_binary(model,r['draw_cal_x'])));side=q['home']/max(EPS,q['home']+q['away']);rem=1-pd;return {'home':rem*side,'draw':pd,'away':rem*(1-side)}
def bins(rows,model=None):
    edges=[0,.2,.24,.28,.32,.36,.40,1.01];out=[];ece=0;n=len(rows)
    for lo,hi in zip(edges[:-1],edges[1:]):
        vals=[]
        for r in rows:
            p=(qcal(r,model)['draw'] if model else r['surface']['one']['draw'])
            if lo<=p<hi:vals.append((p,1 if r['actual_result']=='draw' else 0))
        if vals:
            mp=sum(x for x,_ in vals)/len(vals);obs=sum(y for _,y in vals)/len(vals);ece+=len(vals)/n*abs(mp-obs);out.append({'lo':lo,'hi':hi,'count':len(vals),'mean_p':mp,'observed':obs,'gap':obs-mp})
    return {'ece':ece,'bins':out}
def score(rows,model=None):
    n=h=0;b=rps=ll=draw_b=draw_ll=0.;dpred=dhit=adraw=0
    for r in rows:
        q=r['surface']['one'] if model is None else qcal(r,model);t=r['actual_result'];p=max(('home','draw','away'),key=lambda k:q[k]);n+=1;h+=int(p==t);dpred+=int(p=='draw');dhit+=int(p=='draw' and t=='draw');adraw+=int(t=='draw')
        yd=1 if t=='draw' else 0;draw_b+=(q['draw']-yd)**2;draw_ll-=yd*math.log(max(EPS,q['draw']))+(1-yd)*math.log(max(EPS,1-q['draw']))
        for k in ('home','draw','away'):b+=(q[k]-(1 if t==k else 0))**2
        th=1 if t=='home' else 0;td=1 if t=='draw' else 0;c1=q['home']-th;c2=q['home']+q['draw']-th-td;rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,q[t]))
    return {'count':n,'hits':h,'accuracy':h/n,'brier':b/n,'rps':rps/n,'log_loss':ll/n,'draw_binary_brier':draw_b/n,'draw_binary_log_loss':draw_ll/n,'draw_prediction_count':dpred,'draw_hits':dhit,'draw_recall':dhit/adraw if adraw else None,'calibration':bins(rows,model)}
def main():
    d,audit=build();tr=d['2022/23']+d['2023/24'];va=d['2024/25'];ho=d['2025/26']
    if min(len(tr),len(va),len(ho))<700:raise PlatformError('insufficient rows')
    bv=score(va);bh=score(ho);c=[]
    for l2 in L2_GRID:
        m=fit(tr,l2);s=score(va,m);safe=s['draw_binary_brier']<=bv['draw_binary_brier']+1e-12 and s['draw_binary_log_loss']<=bv['draw_binary_log_loss']+1e-12 and s['brier']<=bv['brier']+1e-12 and s['log_loss']<=bv['log_loss']+1e-12;c.append({'l2':l2,'safe':safe,'validation':s})
    e=[x for x in c if x['safe']];e.sort(key=lambda x:(x['validation']['draw_binary_log_loss'],x['validation']['draw_binary_brier'],x['validation']['log_loss']));sel=e[0] if e else None;ch=None;gate=False
    if sel:
        m=fit(tr+va,float(sel['l2']));ch=score(ho,m);gate=bool(ch['draw_binary_brier']<=bh['draw_binary_brier']+1e-12 and ch['draw_binary_log_loss']<=bh['draw_binary_log_loss']+1e-12 and ch['brier']<=bh['brier']+1e-12 and ch['log_loss']<=bh['log_loss']+1e-12)
    out={'schema_version':'V6.7.3-draw-probability-calibration-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'question':'is draw probability itself miscalibrated, independent of Top-1 recall?','calibrator':'logistic intercept+slope on logit market draw probability','home_away_conditional_ratio_preserved':True,'holdout_used_for_selection':False},'source_audit':audit,'baseline_validation':bv,'selected_candidate':sel,'baseline_holdout':bh,'calibrated_holdout':ch,'research_gate_passed':gate,'interpretation':'PASS_DRAW_CALIBRATION_INCREMENT' if gate else 'MARKET_DRAW_PROBABILITY_ALREADY_HARD_TO_IMPROVE','governance':{'research_only':True,'automatic_promotion':False,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
