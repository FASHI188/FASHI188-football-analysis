#!/usr/bin/env python3
"""V6.16.4 cross-season replication of V6.16.3 joint 1X2+O/U2.5 IPF.

No parameters are selected or changed. The exact V6.16.3 projection is applied to four
seasons independently. Each formal prior uses its season-specific strictly prior selected
parameters/calibrator. Historical market prices remain retrospective/no timestamp.
"""
from __future__ import annotations
import csv,json,sys
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
OUT=ROOT/'manifests'/'v6_joint_market_ipf_crossseason_v6164_status.json'
SEASONS=('2022/23','2023/24','2024/25','2025/26');COMPS=joint.COMPS;TOTAL_KEYS=joint.TOTAL_KEYS

def market_lookup(cid,season):
    aliases=load_aliases();out={};d=ROOT/'processed'/cid
    if not d.exists():return out
    for path in sorted(d.glob('*.csv')):
      with path.open('r',encoding='utf-8-sig',newline='') as fh:
        rd=csv.DictReader(fh);fields=set(rd.fieldnames or []);ouchoices=[]
        for cols,label in [(("P>2.5","P<2.5"),'Pinnacle'),(("B365>2.5","B365<2.5"),'Bet365'),(("Avg>2.5","Avg<2.5"),'Average')]:
            if all(c in fields for c in cols):ouchoices.append((cols,label))
        for r0 in rd:
            r={str(k):'' if v is None else str(v) for k,v in r0.items() if k};s=str(r.get('season') or r.get('Season') or '').strip()
            if s!=season or not r.get('Date') or not r.get('HomeTeam') or not r.get('AwayTeam'):continue
            one=None
            for cols,label in [(("PSCH","PSCD","PSCA"),'Pinnacle_closing'),(("B365CH","B365CD","B365CA"),'Bet365_closing'),(("AvgCH","AvgCD","AvgCA"),'Average_closing'),(("PSH","PSD","PSA"),'Pinnacle'),(("B365H","B365D","B365A"),'Bet365'),(("AvgH","AvgD","AvgA"),'Average')]:
                vals=[ou.fv(r.get(c)) for c in cols]
                if all(v is not None for v in vals):q=[1/v for v in vals];z=sum(q);one=[v/z for v in q];break
            if one is None:continue
            qover=None
            for cols,label in ouchoices:
                o,u=ou.fv(r.get(cols[0])),ou.fv(r.get(cols[1]))
                if o is not None and u is not None:ro,ru=1/o,1/u;qover=ro/(ro+ru);break
            if qover is None:continue
            try:di=parse_match_date(r['Date'],s).isoformat()
            except:continue
            h=canonical_team_name(cid,r['HomeTeam'],aliases);a=canonical_team_name(cid,r['AwayTeam'],aliases);out[(di,h,a)]={'one_x_two':one,'p_over25':qover}
    return out

def one_vec(m):
    x=derive_score_marginals(m)['1x2'];return [float(x[k]) for k in ('home','draw','away')]
def total_vec(m):
    x=derive_score_marginals(m)['total_goals'];return [float(x[k]) for k in TOTAL_KEYS]
def rid(h,a):return 0 if h>a else 1 if h==a else 2

def eval_comp_season(cid,season,config):
    lookup=market_lookup(cid,season);params=ou.params_by_season(cid).get(season)
    if not params:return [],{'reason':'NO_FORMAL_PARAMS','market_rows':len(lookup)}
    matches=[m for m in read_processed_matches(cid) if str(m.season)==season];bydate=defaultdict(list)
    for m in matches:bydate[m.date].append(m)
    hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,season);rows=[];attempt=conv=0;mi=0;mr=0.
    warmc=int(config['validation']['warmup_competition_matches']);warmt=int(config['validation']['warmup_team_matches'])
    for dt in sorted(bydate):
      for m in sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team)):
        mk=lookup.get((m.date.isoformat(),m.home_team,m.away_team))
        if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and mk:
            try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
            except Exception:p=None
            if p:
                prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);adj,audit=joint.ipf(prior,mk['one_x_two'],float(mk['p_over25']));attempt+=1
                if adj is not None and audit.get('converged'):
                    conv+=1;mi=max(mi,int(audit['iterations']));mr=max(mr,float(audit['max_residual']));pt=total_vec(prior);qt=total_vec(adj);po=one_vec(prior);qo=one_vec(adj);tb=min(7,m.home_goals+m.away_goals);r=rid(m.home_goals,m.away_goals)
                    rows.append({'date':m.date.isoformat(),'competition_id':cid,'season':season,'prior_total_rps':ou.rps(pt,tb),'ipf_total_rps':ou.rps(qt,tb),'prior_total_top1':int(max(range(8),key=lambda i:pt[i])==tb),'ipf_total_top1':int(max(range(8),key=lambda i:qt[i])==tb),'prior_score_top1':ou.top_score(prior,1,m.home_goals,m.away_goals),'ipf_score_top1':ou.top_score(adj,1,m.home_goals,m.away_goals),'prior_score_top3':ou.top_score(prior,3,m.home_goals,m.away_goals),'ipf_score_top3':ou.top_score(adj,3,m.home_goals,m.away_goals),'prior_1x2_brier':ou.brier3(po,r),'ipf_1x2_brier':ou.brier3(qo,r),'prior_1x2_top1':int(max(range(3),key=lambda i:po[i])==r),'ipf_1x2_top1':int(max(range(3),key=lambda i:qo[i])==r)})
        hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
    return rows,{'market_rows':len(lookup),'matches':len(matches),'attempted':attempt,'converged':conv,'max_iterations':mi,'max_residual':mr}

def main():
    cfg=load_config();season_results={};meta={};allrows=[]
    for s in SEASONS:
        sr=[];meta[s]={}
        for cid in COMPS:
            rows,m=eval_comp_season(cid,s,cfg);sr+=rows;meta[s][cid]=m
        season_results[s]=joint.summarize(sr);allrows+=sr
    deltas={s:{'total_rps':season_results[s].get('total_rps_delta'),'total_top1':season_results[s].get('total_top1_delta'),'score_top1':season_results[s].get('score_top1_delta'),'score_top3':season_results[s].get('score_top3_delta'),'one_x_two_top1':season_results[s].get('one_x_two_top1_delta')} for s in SEASONS}
    payload={'schema_version':'V6.16.4-joint-market-ipf-crossseason-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'seasons':list(SEASONS),'same_algorithm_as_v6163':True,'no_parameter_selection':True,'constraints':['de-vigged 1X2','de-vigged O/U2.5'],'objective':'KL I-projection via IPF'},'season_results':season_results,'season_deltas':deltas,'aggregate':joint.summarize(allrows),'replication':{'seasons_total_rps_improved':sum(1 for s in SEASONS if deltas[s]['total_rps'] is not None and deltas[s]['total_rps']<0),'seasons_total_top1_improved':sum(1 for s in SEASONS if deltas[s]['total_top1'] is not None and deltas[s]['total_top1']>0),'seasons_score_top1_improved':sum(1 for s in SEASONS if deltas[s]['score_top1'] is not None and deltas[s]['score_top1']>0),'seasons_score_top3_improved':sum(1 for s in SEASONS if deltas[s]['score_top3'] is not None and deltas[s]['score_top3']>0),'seasons_1x2_top1_improved':sum(1 for s in SEASONS if deltas[s]['one_x_two_top1'] is not None and deltas[s]['one_x_two_top1']>0)},'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'season_results':season_results,'aggregate':payload['aggregate'],'replication':payload['replication']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
