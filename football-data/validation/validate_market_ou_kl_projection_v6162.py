#!/usr/bin/env python3
"""V6.16.2 research-only O/U2.5 KL projection onto the formal historical prior.

For each strictly PIT formal historical prediction, de-vigged O/U2.5 supplies one
constraint q=P(T>=3). The I-projection under this single binary partition has a closed
form: rescale prior masses T<=2 to 1-q and T>=3 to q, preserving all within-partition
relative probabilities. This adjusts an existing prior only; it never invents a score
structure. Historical market quotes lack original timestamps, so formal_weight=0.
"""
from __future__ import annotations
import csv,json,math,sys
from collections import Counter,defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1];ENGINE=ROOT/'engine';VALID=ROOT/'validation'
for p in (ENGINE,VALID):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
from football_v460_engine import load_config,predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import canonical_team_name,derive_score_marginals,load_aliases,load_json,parse_match_date,read_processed_matches
from total_goals_joint_integration_v466 import _replace_total_marginal

OUT=ROOT/'manifests'/'v6_market_ou_kl_projection_v6162_status.json'
COMPS=('ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga','NED_Eredivisie','POR_PrimeiraLiga','SCO_Premiership')
SEASON='2025/26';TOTAL_KEYS=('0','1','2','3','4','5','6','7+')
FORMAL_REPORT_ROOT=ROOT/'validation'/'reports'/'formal_core_v460';CAL_ROOT=ROOT/'models'/'formal_core_v460'
EPS=1e-15

def fv(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 and math.isfinite(x) else None

def market_lookup(cid):
    aliases=load_aliases();out={}
    d=ROOT/'processed'/cid
    if not d.exists():return out
    for path in sorted(d.glob('*.csv')):
        with path.open('r',encoding='utf-8-sig',newline='') as fh:
            rd=csv.DictReader(fh);fields=set(rd.fieldnames or [])
            choices=[]
            for cols,label in [(("P>2.5","P<2.5"),'Pinnacle'),(("B365>2.5","B365<2.5"),'Bet365'),(("Avg>2.5","Avg<2.5"),'Average')]:
                if all(c in fields for c in cols):choices.append((cols,label))
            for r0 in rd:
                r={str(k):'' if v is None else str(v) for k,v in r0.items() if k}
                season=str(r.get('season') or r.get('Season') or '').strip()
                if season!=SEASON or not r.get('Date') or not r.get('HomeTeam') or not r.get('AwayTeam'):continue
                q=None;label=None
                for cols,lab in choices:
                    o,u=fv(r.get(cols[0])),fv(r.get(cols[1]))
                    if o is not None and u is not None:
                        ro,ru=1/o,1/u;q=ro/(ro+ru);label=lab;break
                if q is None:continue
                try:di=parse_match_date(r['Date'],season).isoformat()
                except:continue
                h=canonical_team_name(cid,r['HomeTeam'],aliases);a=canonical_team_name(cid,r['AwayTeam'],aliases)
                out[(di,h,a)]={'p_over25':q,'provider':label}
    return out

def params_by_season(cid):
    p=FORMAL_REPORT_ROOT/f'{cid}.json'
    if not p.exists():return {}
    rep=load_json(p);o={}
    for fold in rep.get('folds') or []:
        s=fold.get('outer_season');pr=fold.get('selected_parameters')
        if s and isinstance(pr,dict):o[str(s)]=dict(pr)
    return o

def calibrator(cid,season):
    p=CAL_ROOT/cid/'oof_matrix_calibrator.json'
    if not p.exists():return 1.0
    a=load_json(p);m=a.get('season_calibrators') or {};return float((m.get(season) or {}).get('temperature',1.0))

def rps(p,idx):
    cp=0;co=0;s=0
    for i in range(7):
        cp+=p[i];co+=1 if idx==i else 0;s+=(cp-co)**2
    return s/7

def brier3(p,idx):return sum((p[i]-(1 if i==idx else 0))**2 for i in range(3))
def one(matrix):
    m=derive_score_marginals(matrix)['1x2'];return [float(m[k]) for k in ('home','draw','away')]
def top_score(matrix,k,h,a):
    z=sorted(matrix,key=lambda c:(-float(c['probability']),int(c['home_goals']),int(c['away_goals'])))[:k]
    return int(any(int(c['home_goals'])==h and int(c['away_goals'])==a for c in z))
def project(total,q):
    p=[float(total[k]) for k in TOTAL_KEYS];lo=sum(p[:3]);hi=sum(p[3:])
    if lo<=EPS or hi<=EPS:return None
    z=[x*(1-q)/lo for x in p[:3]]+[x*q/hi for x in p[3:]];s=sum(z);z=[x/s for x in z]
    return {k:z[i] for i,k in enumerate(TOTAL_KEYS)}
def summarize(rows):
    n=len(rows)
    if not n:return {'count':0}
    def mean(k):return sum(r[k] for r in rows)/n
    return {'count':n,'prior_total_rps':mean('prior_total_rps'),'projected_total_rps':mean('proj_total_rps'),'total_rps_delta':mean('proj_total_rps')-mean('prior_total_rps'),
            'prior_total_top1':mean('prior_total_top1'),'projected_total_top1':mean('proj_total_top1'),'total_top1_delta':mean('proj_total_top1')-mean('prior_total_top1'),
            'prior_score_top1':mean('prior_score_top1'),'projected_score_top1':mean('proj_score_top1'),'score_top1_delta':mean('proj_score_top1')-mean('prior_score_top1'),
            'prior_score_top3':mean('prior_score_top3'),'projected_score_top3':mean('proj_score_top3'),'score_top3_delta':mean('proj_score_top3')-mean('prior_score_top3'),
            'prior_1x2_brier':mean('prior_1x2_brier'),'projected_1x2_brier':mean('proj_1x2_brier'),'one_x_two_brier_delta':mean('proj_1x2_brier')-mean('prior_1x2_brier')}

def eval_comp(cid,config):
    lookup=market_lookup(cid);params=params_by_season(cid).get(SEASON)
    if not params:return [],{'reason':'NO_FORMAL_PARAMS'}
    matches=[m for m in read_processed_matches(cid) if str(m.season)==SEASON];bydate=defaultdict(list)
    for m in matches:bydate[m.date].append(m)
    hist=[];out=[];temp=calibrator(cid,SEASON);warmc=int(config['validation']['warmup_competition_matches']);warmt=int(config['validation']['warmup_team_matches'])
    homec=Counter();awayc=Counter()
    for dt in sorted(bydate):
        for m in sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team)):
            key=(m.date.isoformat(),m.home_team,m.away_team);mk=lookup.get(key)
            if len(hist)>=warmc and homec[m.home_team]>=warmt and awayc[m.away_team]>=warmt and mk:
                try:pred=predict_from_history(hist,cid,SEASON,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
                except Exception:pred=None
                if pred:
                    prior=temp_scale=temperature_scale_matrix(pred['probabilities']['score_matrix'],temp);marg=derive_score_marginals(prior);target=project(marg['total_goals'],float(mk['p_over25']))
                    if target:
                        proj=_replace_total_marginal(prior,target);pm=derive_score_marginals(proj);pt=[float(marg['total_goals'][k]) for k in TOTAL_KEYS];qt=[float(pm['total_goals'][k]) for k in TOTAL_KEYS]
                        act=min(7,m.home_goals+m.away_goals);res=0 if m.home_goals>m.away_goals else 1 if m.home_goals==m.away_goals else 2
                        out.append({'date':m.date.isoformat(),'competition_id':cid,'provider':mk['provider'],'p_over25':mk['p_over25'],'prior_total_rps':rps(pt,act),'proj_total_rps':rps(qt,act),'prior_total_top1':int(max(range(8),key=lambda i:pt[i])==act),'proj_total_top1':int(max(range(8),key=lambda i:qt[i])==act),'prior_score_top1':top_score(prior,1,m.home_goals,m.away_goals),'proj_score_top1':top_score(proj,1,m.home_goals,m.away_goals),'prior_score_top3':top_score(prior,3,m.home_goals,m.away_goals),'proj_score_top3':top_score(proj,3,m.home_goals,m.away_goals),'prior_1x2_brier':brier3(one(prior),res),'proj_1x2_brier':brier3(one(proj),res)})
            hist.append(m);homec[m.home_team]+=1;awayc[m.away_team]+=1
    return out,{'market_rows':len(lookup),'season_matches':len(matches),'temperature':temp}

def main():
    config=load_config();allrows=[];comps={};meta={}
    for cid in COMPS:
        rows,m=eval_comp(cid,config);allrows+=rows;comps[cid]=summarize(rows);meta[cid]=m
    ordered=sorted(allrows,key=lambda r:(r['date'],r['competition_id']));blocks=[]
    for i in range(0,len(ordered)-99,100):
        s=summarize(ordered[i:i+100]);s.update({'start':i,'stop':i+100,'first_date':ordered[i]['date'],'last_date':ordered[i+99]['date']});blocks.append(s)
    d=[b['total_rps_delta'] for b in blocks if b.get('count')]
    payload={'schema_version':'V6.16.2-market-ou-kl-projection-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'season':SEASON,'constraint':'de-vigged P(T>=3) from O/U2.5','projection':'closed-form KL I-projection preserving within <=2 and >=3 relative mass','no_fitted_projection_weight':True,'formal_prior_reused':True},'competition_meta':meta,'by_competition':comps,'aggregate':summarize(ordered),'blocks100':blocks,'block_summary':{'count':len(blocks),'rps_improved_blocks':sum(x<0 for x in d),'rps_worsened_blocks':sum(x>0 for x in d),'worst_rps_delta':max(d) if d else None,'best_rps_delta':min(d) if d else None},'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'aggregate':payload['aggregate'],'block_summary':payload['block_summary'],'by_competition':comps},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
