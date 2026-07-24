#!/usr/bin/env python3
"""V6.16.3 research-only joint KL/IPF projection of the formal score prior.

Constraints are de-vigged historical 1X2 and O/U2.5 probabilities from the same row.
Starting from the strictly PIT formal score matrix, iterative proportional fitting scales
outcome partitions and total-goal partitions until both market marginals are matched.
No score cell is created; only prior mass is reweighted. Historical quotes lack original
quote timestamps, so formal_weight=0.
"""
from __future__ import annotations
import csv,json,math,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import canonical_team_name,derive_score_marginals,load_aliases,parse_match_date,read_processed_matches

OUT=ROOT/'manifests'/'v6_joint_market_ipf_v6163_status.json'
COMPS=ou.COMPS;SEASON=ou.SEASON;TOTAL_KEYS=ou.TOTAL_KEYS

def market_lookup(cid):
    aliases=load_aliases();out={};d=ROOT/'processed'/cid
    if not d.exists():return out
    for path in sorted(d.glob('*.csv')):
      with path.open('r',encoding='utf-8-sig',newline='') as fh:
        rd=csv.DictReader(fh);fields=set(rd.fieldnames or [])
        ouchoices=[]
        for cols,label in [(("P>2.5","P<2.5"),'Pinnacle'),(("B365>2.5","B365<2.5"),'Bet365'),(("Avg>2.5","Avg<2.5"),'Average')]:
            if all(c in fields for c in cols):ouchoices.append((cols,label))
        for r0 in rd:
            r={str(k):'' if v is None else str(v) for k,v in r0.items() if k};season=str(r.get('season') or r.get('Season') or '').strip()
            if season!=SEASON or not r.get('Date') or not r.get('HomeTeam') or not r.get('AwayTeam'):continue
            one=None;olabel=None
            for cols,label in [(("PSCH","PSCD","PSCA"),'Pinnacle_closing'),(("B365CH","B365CD","B365CA"),'Bet365_closing'),(("AvgCH","AvgCD","AvgCA"),'Average_closing'),(("PSH","PSD","PSA"),'Pinnacle'),(("B365H","B365D","B365A"),'Bet365'),(("AvgH","AvgD","AvgA"),'Average')]:
                vals=[ou.fv(r.get(c)) for c in cols]
                if all(v is not None for v in vals):
                    q=[1/v for v in vals];s=sum(q);one=[v/s for v in q];olabel=label;break
            if one is None:continue
            qover=None;ulabel=None
            for cols,label in ouchoices:
                o,u=ou.fv(r.get(cols[0])),ou.fv(r.get(cols[1]))
                if o is not None and u is not None:
                    ro,ru=1/o,1/u;qover=ro/(ro+ru);ulabel=label;break
            if qover is None:continue
            try:di=parse_match_date(r['Date'],season).isoformat()
            except:continue
            h=canonical_team_name(cid,r['HomeTeam'],aliases);a=canonical_team_name(cid,r['AwayTeam'],aliases)
            out[(di,h,a)]={'one_x_two':one,'p_over25':qover,'provider_1x2':olabel,'provider_ou':ulabel}
    return out

def outcome(cell):
    h=int(cell['home_goals']);a=int(cell['away_goals']);return 0 if h>a else 1 if h==a else 2
def over(cell):return int(cell['home_goals'])+int(cell['away_goals'])>=3

def marginals(m):
    one=[0.,0.,0.];ov=0.;tot=0.
    for c in m:
        p=float(c['probability']);tot+=p;one[outcome(c)]+=p
        if over(c):ov+=p
    return one,ov,tot

def ipf(matrix,target_one,target_over,max_iter=500,tol=1e-12):
    m=[dict(c,probability=max(1e-18,float(c['probability']))) for c in matrix];s=sum(float(c['probability']) for c in m)
    for c in m:c['probability']=float(c['probability'])/s
    converged=False;res=None;iters=0
    for it in range(1,max_iter+1):
        cur,_,_=marginals(m)
        for j in range(3):
            if cur[j]<=0:return None,{'converged':False,'iterations':it,'max_residual':None,'reason':'ZERO_OUTCOME_MASS'}
            scale=target_one[j]/cur[j]
            for c in m:
                if outcome(c)==j:c['probability']*=scale
        _,cov,_=marginals(m);under=1-cov
        if cov<=0 or under<=0:return None,{'converged':False,'iterations':it,'max_residual':None,'reason':'ZERO_OU_MASS'}
        so=target_over/cov;su=(1-target_over)/under
        for c in m:c['probability']*=so if over(c) else su
        one,ov,sm=marginals(m);res=max(max(abs(one[j]-target_one[j]) for j in range(3)),abs(ov-target_over),abs(sm-1))
        iters=it
        if res<tol:converged=True;break
    return m,{'converged':converged,'iterations':iters,'max_residual':res,'probability_sum':marginals(m)[2]}

def one_vec(matrix):
    x=derive_score_marginals(matrix)['1x2'];return [float(x[k]) for k in ('home','draw','away')]
def total_vec(matrix):
    x=derive_score_marginals(matrix)['total_goals'];return [float(x[k]) for k in TOTAL_KEYS]
def result_idx(h,a):return 0 if h>a else 1 if h==a else 2

def summarize(rows):
    n=len(rows)
    if not n:return {'count':0}
    def mean(k):return sum(r[k] for r in rows)/n
    return {'count':n,
      'prior_total_rps':mean('prior_total_rps'),'ipf_total_rps':mean('ipf_total_rps'),'total_rps_delta':mean('ipf_total_rps')-mean('prior_total_rps'),
      'prior_total_top1':mean('prior_total_top1'),'ipf_total_top1':mean('ipf_total_top1'),'total_top1_delta':mean('ipf_total_top1')-mean('prior_total_top1'),
      'prior_score_top1':mean('prior_score_top1'),'ipf_score_top1':mean('ipf_score_top1'),'score_top1_delta':mean('ipf_score_top1')-mean('prior_score_top1'),
      'prior_score_top3':mean('prior_score_top3'),'ipf_score_top3':mean('ipf_score_top3'),'score_top3_delta':mean('ipf_score_top3')-mean('prior_score_top3'),
      'prior_1x2_brier':mean('prior_1x2_brier'),'ipf_1x2_brier':mean('ipf_1x2_brier'),'one_x_two_brier_delta':mean('ipf_1x2_brier')-mean('prior_1x2_brier'),
      'prior_1x2_top1':mean('prior_1x2_top1'),'ipf_1x2_top1':mean('ipf_1x2_top1'),'one_x_two_top1_delta':mean('ipf_1x2_top1')-mean('prior_1x2_top1')}

def eval_comp(cid,config):
    lookup=market_lookup(cid);params=ou.params_by_season(cid).get(SEASON)
    if not params:return [],{'reason':'NO_FORMAL_PARAMS'}
    matches=[m for m in read_processed_matches(cid) if str(m.season)==SEASON];bydate=defaultdict(list)
    for m in matches:bydate[m.date].append(m)
    hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,SEASON);out=[];conv=[]
    warmc=int(config['validation']['warmup_competition_matches']);warmt=int(config['validation']['warmup_team_matches'])
    for dt in sorted(bydate):
      for m in sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team)):
        mk=lookup.get((m.date.isoformat(),m.home_team,m.away_team))
        if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and mk:
            try:p=predict_from_history(hist,cid,SEASON,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
            except Exception:p=None
            if p:
                prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);adj,audit=ipf(prior,mk['one_x_two'],float(mk['p_over25']));conv.append(audit)
                if adj is not None and audit['converged']:
                    pt=total_vec(prior);qt=total_vec(adj);po=one_vec(prior);qo=one_vec(adj);tb=min(7,m.home_goals+m.away_goals);ri=result_idx(m.home_goals,m.away_goals)
                    out.append({'date':m.date.isoformat(),'competition_id':cid,'prior_total_rps':ou.rps(pt,tb),'ipf_total_rps':ou.rps(qt,tb),'prior_total_top1':int(max(range(8),key=lambda i:pt[i])==tb),'ipf_total_top1':int(max(range(8),key=lambda i:qt[i])==tb),'prior_score_top1':ou.top_score(prior,1,m.home_goals,m.away_goals),'ipf_score_top1':ou.top_score(adj,1,m.home_goals,m.away_goals),'prior_score_top3':ou.top_score(prior,3,m.home_goals,m.away_goals),'ipf_score_top3':ou.top_score(adj,3,m.home_goals,m.away_goals),'prior_1x2_brier':ou.brier3(po,ri),'ipf_1x2_brier':ou.brier3(qo,ri),'prior_1x2_top1':int(max(range(3),key=lambda i:po[i])==ri),'ipf_1x2_top1':int(max(range(3),key=lambda i:qo[i])==ri),'iterations':audit['iterations'],'residual':audit['max_residual']})
        hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
    ok=[x for x in conv if x.get('converged')]
    meta={'market_rows':len(lookup),'season_matches':len(matches),'attempted':len(conv),'converged':len(ok),'max_iterations':max((x['iterations'] for x in ok),default=None),'max_residual':max((x['max_residual'] for x in ok),default=None)}
    return out,meta

def main():
    config=load_config();allr=[];by={};meta={}
    for cid in COMPS:
        rows,m=eval_comp(cid,config);allr+=rows;by[cid]=summarize(rows);meta[cid]=m
    ordered=sorted(allr,key=lambda r:(r['date'],r['competition_id']));blocks=[]
    for i in range(0,len(ordered)-99,100):
        s=summarize(ordered[i:i+100]);s.update({'start':i,'stop':i+100,'first_date':ordered[i]['date'],'last_date':ordered[i+99]['date']});blocks.append(s)
    tr=[b['total_rps_delta'] for b in blocks if b.get('count')];sr=[b['score_top1_delta'] for b in blocks if b.get('count')]
    audit={'attempted':sum(m.get('attempted',0) for m in meta.values()),'converged':sum(m.get('converged',0) for m in meta.values()),'max_iterations':max((m.get('max_iterations') or 0 for m in meta.values()),default=0),'max_residual':max((m.get('max_residual') or 0 for m in meta.values()),default=0)}
    payload={'schema_version':'V6.16.3-joint-market-ipf-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'constraints':['de-vigged 1X2','de-vigged O/U2.5 P(T>=3)'],'objective':'KL I-projection via iterative proportional fitting','prior':'strict PIT formal score matrix','tolerance':1e-12,'max_iter':500,'no_fitted_projection_weight':True},'convergence_audit':audit,'competition_meta':meta,'by_competition':by,'aggregate':summarize(ordered),'blocks100':blocks,'block_summary':{'count':len(blocks),'total_rps_improved_blocks':sum(x<0 for x in tr),'total_rps_worsened_blocks':sum(x>0 for x in tr),'score_top1_improved_blocks':sum(x>0 for x in sr),'score_top1_worsened_blocks':sum(x<0 for x in sr)},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'aggregate':payload['aggregate'],'convergence':audit,'block_summary':payload['block_summary']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
