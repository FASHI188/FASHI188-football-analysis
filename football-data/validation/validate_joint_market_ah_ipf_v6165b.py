#!/usr/bin/env python3
"""V6.16.5b clean AH-margin extension of joint market IPF.

Only nonredundant half-goal closing AH lines (|h|>=1.5, h ends .5) are used, so the
home-cover event is binary with no push/half-settlement ambiguity. Compare on the exact
same matches: V6.16.3 constraints (1X2+O/U2.5) versus 1X2+O/U2.5+AH cover probability.
No weights are fitted. Historical quotes lack original timestamps; research only.
"""
from __future__ import annotations
import csv,json,math,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import validate_joint_market_ipf_v6163 as joint
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import canonical_team_name,derive_score_marginals,load_aliases,parse_match_date,read_processed_matches
OUT=ROOT/'manifests'/'v6_joint_market_ah_ipf_v6165b_status.json'
SEASONS=('2022/23','2023/24','2024/25','2025/26');COMPS=joint.COMPS;TOTAL_KEYS=joint.TOTAL_KEYS

def market_lookup(cid,season):
    aliases=load_aliases();out={};d=ROOT/'processed'/cid
    if not d.exists():return out
    for path in sorted(d.glob('*.csv')):
      with path.open('r',encoding='utf-8-sig',newline='') as fh:
        rd=csv.DictReader(fh);fields=set(rd.fieldnames or []);ouchoices=[]
        for cols in (("P>2.5","P<2.5"),("B365>2.5","B365<2.5"),("Avg>2.5","Avg<2.5")):
            if all(c in fields for c in cols):ouchoices.append(cols)
        for r0 in rd:
            r={str(k):'' if v is None else str(v) for k,v in r0.items() if k};s=str(r.get('season') or r.get('Season') or '').strip()
            if s!=season or not r.get('Date') or not r.get('HomeTeam') or not r.get('AwayTeam'):continue
            line=ou.fv(r.get('AHCh'))
            if line is None or abs(line)<1.49 or abs(abs(line-round(line))-.5)>.02:continue
            ah=None
            for cols in (("PCAHH","PCAHA"),("B365CAHH","B365CAHA"),("AvgCAHH","AvgCAHA")):
                h,a=ou.fv(r.get(cols[0])),ou.fv(r.get(cols[1]))
                if h and a:q=[1/h,1/a];z=sum(q);ah=q[0]/z;break
            if ah is None:continue
            one=None
            for cols in (("PSCH","PSCD","PSCA"),("B365CH","B365CD","B365CA"),("AvgCH","AvgCD","AvgCA"),("PSH","PSD","PSA"),("B365H","B365D","B365A"),("AvgH","AvgD","AvgA")):
                vals=[ou.fv(r.get(c)) for c in cols]
                if all(v is not None for v in vals):q=[1/v for v in vals];z=sum(q);one=[v/z for v in q];break
            if one is None:continue
            qover=None
            for cols in ouchoices:
                o,u=ou.fv(r.get(cols[0])),ou.fv(r.get(cols[1]))
                if o and u:ro,ru=1/o,1/u;qover=ro/(ro+ru);break
            if qover is None:continue
            try:di=parse_match_date(r['Date'],s).isoformat()
            except:continue
            h=canonical_team_name(cid,r['HomeTeam'],aliases);a=canonical_team_name(cid,r['AwayTeam'],aliases);out[(di,h,a)]={'one_x_two':one,'p_over25':qover,'ah_line':line,'p_home_cover':ah}
    return out

def cover(cell,line):return int(cell['home_goals'])-int(cell['away_goals'])+line>0

def ah_ipf(matrix,one_target,over_target,line,cover_target,max_iter=500,tol=1e-12):
    m=[dict(c,probability=max(1e-18,float(c['probability']))) for c in matrix];s=sum(c['probability'] for c in m)
    for c in m:c['probability']/=s
    for it in range(1,max_iter+1):
        cur,_,_=joint.marginals(m)
        for j in range(3):
            if cur[j]<=0:return None,{'converged':False,'iterations':it,'reason':'ZERO_OUTCOME'}
            sc=one_target[j]/cur[j]
            for c in m:
                if joint.outcome(c)==j:c['probability']*=sc
        _,ov,_=joint.marginals(m);un=1-ov
        if ov<=0 or un<=0:return None,{'converged':False,'iterations':it,'reason':'ZERO_OU'}
        so=over_target/ov;su=(1-over_target)/un
        for c in m:c['probability']*=so if joint.over(c) else su
        cc=sum(float(c['probability']) for c in m if cover(c,line));nc=1-cc
        if cc<=0 or nc<=0:return None,{'converged':False,'iterations':it,'reason':'ZERO_AH'}
        sh=cover_target/cc;sn=(1-cover_target)/nc
        for c in m:c['probability']*=sh if cover(c,line) else sn
        one,ov,sm=joint.marginals(m);cc=sum(float(c['probability']) for c in m if cover(c,line));res=max(max(abs(one[j]-one_target[j]) for j in range(3)),abs(ov-over_target),abs(cc-cover_target),abs(sm-1))
        if res<tol:return m,{'converged':True,'iterations':it,'max_residual':res,'probability_sum':sm}
    return m,{'converged':False,'iterations':max_iter,'max_residual':res,'probability_sum':joint.marginals(m)[2]}

def one_vec(m):
    x=derive_score_marginals(m)['1x2'];return [float(x[k]) for k in ('home','draw','away')]
def total_vec(m):
    x=derive_score_marginals(m)['total_goals'];return [float(x[k]) for k in TOTAL_KEYS]
def rid(h,a):return 0 if h>a else 1 if h==a else 2

def summarize(rows):
    n=len(rows)
    if not n:return {'count':0}
    def mean(k):return sum(r[k] for r in rows)/n
    return {'count':n,'baseline_total_rps':mean('base_total_rps'),'ah_total_rps':mean('ah_total_rps'),'total_rps_delta':mean('ah_total_rps')-mean('base_total_rps'),'baseline_total_top1':mean('base_total_top1'),'ah_total_top1':mean('ah_total_top1'),'total_top1_delta':mean('ah_total_top1')-mean('base_total_top1'),'baseline_score_top1':mean('base_score_top1'),'ah_score_top1':mean('ah_score_top1'),'score_top1_delta':mean('ah_score_top1')-mean('base_score_top1'),'baseline_score_top3':mean('base_score_top3'),'ah_score_top3':mean('ah_score_top3'),'score_top3_delta':mean('ah_score_top3')-mean('base_score_top3'),'baseline_1x2_brier':mean('base_1x2_brier'),'ah_1x2_brier':mean('ah_1x2_brier'),'one_x_two_brier_delta':mean('ah_1x2_brier')-mean('base_1x2_brier')}

def eval_cs(cid,season,cfg):
    lookup=market_lookup(cid,season);params=ou.params_by_season(cid).get(season)
    if not params:return [],{'reason':'NO_FORMAL_PARAMS','market_rows':len(lookup)}
    ms=[m for m in read_processed_matches(cid) if str(m.season)==season];bd=defaultdict(list)
    for m in ms:bd[m.date].append(m)
    hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,season);rows=[];attempt=conv=0;infeasible=0;mi=0;mr=0.
    wc=int(cfg['validation']['warmup_competition_matches']);wt=int(cfg['validation']['warmup_team_matches'])
    for dt in sorted(bd):
      for m in sorted(bd[dt],key=lambda x:(x.home_team,x.away_team)):
        mk=lookup.get((m.date.isoformat(),m.home_team,m.away_team))
        if len(hist)>=wc and hc[m.home_team]>=wt and ac[m.away_team]>=wt and mk:
            try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
            except Exception:p=None
            if p:
                prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);base,a0=joint.ipf(prior,mk['one_x_two'],float(mk['p_over25']));adj,a1=ah_ipf(prior,mk['one_x_two'],float(mk['p_over25']),float(mk['ah_line']),float(mk['p_home_cover']));attempt+=1
                if base is not None and a0.get('converged') and adj is not None and a1.get('converged'):
                    conv+=1;mi=max(mi,int(a1['iterations']));mr=max(mr,float(a1['max_residual']));bt=total_vec(base);at=total_vec(adj);bo=one_vec(base);ao=one_vec(adj);tb=min(7,m.home_goals+m.away_goals);ri=rid(m.home_goals,m.away_goals)
                    rows.append({'season':season,'competition_id':cid,'line':mk['ah_line'],'base_total_rps':ou.rps(bt,tb),'ah_total_rps':ou.rps(at,tb),'base_total_top1':int(max(range(8),key=lambda i:bt[i])==tb),'ah_total_top1':int(max(range(8),key=lambda i:at[i])==tb),'base_score_top1':ou.top_score(base,1,m.home_goals,m.away_goals),'ah_score_top1':ou.top_score(adj,1,m.home_goals,m.away_goals),'base_score_top3':ou.top_score(base,3,m.home_goals,m.away_goals),'ah_score_top3':ou.top_score(adj,3,m.home_goals,m.away_goals),'base_1x2_brier':ou.brier3(bo,ri),'ah_1x2_brier':ou.brier3(ao,ri)})
                else:infeasible+=1
        hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
    return rows,{'market_rows':len(lookup),'attempted':attempt,'converged':conv,'failed_or_infeasible':infeasible,'max_iterations':mi,'max_residual':mr}

def main():
    cfg=load_config();allr=[];byseason={};meta={}
    for s in SEASONS:
        sr=[];meta[s]={}
        for cid in COMPS:
            r,m=eval_cs(cid,s,cfg);sr+=r;meta[s][cid]=m
        byseason[s]=summarize(sr);allr+=sr
    payload={'schema_version':'V6.16.5b-joint-market-ah-ipf-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'baseline':'V6.16.3 1X2+OU IPF on identical matches','added_constraint':'de-vigged closing AH home-cover probability','eligible_lines':'nonredundant half-goal |h|>=1.5 only','no_push_or_quarter_handicap':True,'no_fitted_weight':True},'by_season':byseason,'aggregate':summarize(allr),'replication':{'seasons_score_top1_improved':sum(1 for s in SEASONS if byseason[s].get('score_top1_delta',0)>0),'seasons_score_top3_improved':sum(1 for s in SEASONS if byseason[s].get('score_top3_delta',0)>0),'seasons_total_rps_improved':sum(1 for s in SEASONS if byseason[s].get('total_rps_delta',0)<0)},'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'by_season':byseason,'aggregate':payload['aggregate'],'replication':payload['replication']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
