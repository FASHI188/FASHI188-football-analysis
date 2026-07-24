#!/usr/bin/env python3
"""V6.16.6 research-only P(D|T,X) challenger using Asian-handicap information as a feature.

AH is NOT imposed as a hard matrix constraint. It only helps predict goal difference D
conditional on a total T. This directly targets the remaining score-allocation problem.

For each test season 2022/23..2025/26:
- train only on complete-market rows from strictly earlier seasons;
- fit one multinomial D classifier per exact total T=1..6 (T=0 is deterministic);
- features: de-vigged 1X2, de-vigged O/U2.5, signed closing AH line, and AH price log-ratio;
- get T from the V6.16.3 joint 1X2+OU IPF score matrix Top-1 total;
- compare exact-score Top-1/Top-3 on identical matches against V6.16.3 IPF matrix;
- separately compare conditional score accuracy only where predicted T equals actual T.

Quarter/integer AH lines are safe here because they are covariates, not interpreted as
binary probabilities or settlement constraints. Historical quotes lack original timestamps,
so formal_weight=0.
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
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT=ROOT/'manifests'/'v6_conditional_margin_ah_v6166_status.json'
SEASONS=('2021/22','2022/23','2023/24','2024/25','2025/26');TESTS=SEASONS[1:];COMPS=joint.COMPS;TOTAL_KEYS=joint.TOTAL_KEYS


def line_value(v):
    try:x=float(str(v).strip())
    except (TypeError,ValueError):return None
    return x if math.isfinite(x) else None


def load_market_rows():
    aliases=load_aliases();rows=[]
    for cid in COMPS:
        d=ROOT/'processed'/cid
        if not d.exists():continue
        for path in sorted(d.glob('*.csv')):
          with path.open('r',encoding='utf-8-sig',newline='') as fh:
            rd=csv.DictReader(fh);fields=set(rd.fieldnames or []);ouchoices=[]
            for cols in (("P>2.5","P<2.5"),("B365>2.5","B365<2.5"),("Avg>2.5","Avg<2.5")):
                if all(c in fields for c in cols):ouchoices.append(cols)
            for r0 in rd:
                r={str(k):'' if v is None else str(v) for k,v in r0.items() if k};season=str(r.get('season') or r.get('Season') or '').strip()
                if season not in SEASONS or not r.get('Date') or not r.get('HomeTeam') or not r.get('AwayTeam'):continue
                try:hg=int(float(r.get('FTHG','')));ag=int(float(r.get('FTAG','')))
                except:continue
                one=None
                for cols in (("PSCH","PSCD","PSCA"),("B365CH","B365CD","B365CA"),("AvgCH","AvgCD","AvgCA"),("PSH","PSD","PSA"),("B365H","B365D","B365A"),("AvgH","AvgD","AvgA")):
                    vals=[ou.fv(r.get(c)) for c in cols]
                    if all(v is not None for v in vals):q=[1/v for v in vals];z=sum(q);one=[v/z for v in q];break
                if one is None:continue
                pov=None
                for cols in ouchoices:
                    o,u=ou.fv(r.get(cols[0])),ou.fv(r.get(cols[1]))
                    if o and u:ro,ru=1/o,1/u;pov=ro/(ro+ru);break
                if pov is None:continue
                line=line_value(r.get('AHCh'))
                if line is None:line=line_value(r.get('AHh'))
                if line is None:continue
                ahh=aha=None
                for cols in (("PCAHH","PCAHA"),("B365CAHH","B365CAHA"),("AvgCAHH","AvgCAHA"),("PAHH","PAHA"),("B365AHH","B365AHA"),("AvgAHH","AvgAHA")):
                    h,a=ou.fv(r.get(cols[0])),ou.fv(r.get(cols[1]))
                    if h and a:ahh,aha=h,a;break
                if ahh is None:continue
                try:di=parse_match_date(r['Date'],season).isoformat()
                except:continue
                home=canonical_team_name(cid,r['HomeTeam'],aliases);away=canonical_team_name(cid,r['AwayTeam'],aliases)
                feat=list(one)+[float(pov),float(line),math.log(float(aha)/float(ahh)),abs(float(line))]
                # fixed one-hot competition identity
                feat += [1.0 if cid==c else 0.0 for c in COMPS]
                rows.append({'competition_id':cid,'season':season,'date':di,'home':home,'away':away,'score':(hg,ag),'total':hg+ag,'d':hg-ag,'x':feat,'one_x_two':one,'p_over25':pov})
    return sorted(rows,key=lambda r:(r['date'],r['competition_id'],r['home'],r['away']))


def season_year(s):return int(s[:4])

def fit_dmodels(rows):
    models={0:('CONST',0)}
    for t in range(1,7):
        sub=[r for r in rows if r['total']==t];classes=sorted({r['d'] for r in sub})
        if len(classes)<2:models[t]=('CONST',classes[0] if classes else 0);continue
        m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=1000,solver='lbfgs'))
        m.fit([r['x'] for r in sub],[r['d'] for r in sub]);models[t]=m
    return models

def rank_scores(x,t,models):
    if t>=7:return []
    m=models.get(t)
    if m is None:return []
    if isinstance(m,tuple):ds=[m[1]]
    else:
        probs=m.predict_proba([x])[0];classes=list(m.named_steps['logisticregression'].classes_);ds=[d for _,d in sorted(zip(probs,classes),reverse=True)]
    out=[]
    for d in ds:
        d=int(d)
        if (t+d)%2:continue
        h=(t+d)//2;a=(t-d)//2
        if h>=0 and a>=0:out.append((h,a))
    return out

def market_lookup(rows,season,cid):return {(r['date'],r['home'],r['away']):r for r in rows if r['season']==season and r['competition_id']==cid}
def total_vec(m):
    x=derive_score_marginals(m)['total_goals'];return [float(x[k]) for k in TOTAL_KEYS]

def summarize(rows):
    n=len(rows)
    if not n:return {'count':0}
    def mean(k):return sum(r[k] for r in rows)/n
    cond=[r for r in rows if r['total_correct'] and r['pred_total']<7];cn=len(cond)
    return {'count':n,'baseline_score_top1':mean('baseline_top1'),'conditional_ah_score_top1':mean('ah_top1'),'score_top1_delta':mean('ah_top1')-mean('baseline_top1'),'baseline_score_top3':mean('baseline_top3'),'conditional_ah_score_top3':mean('ah_top3'),'score_top3_delta':mean('ah_top3')-mean('baseline_top3'),'predicted_total_top1_accuracy':mean('total_correct'),'conditional_total_correct_count':cn,'baseline_top1_given_total_correct':sum(r['baseline_top1'] for r in cond)/cn if cn else None,'ah_top1_given_total_correct':sum(r['ah_top1'] for r in cond)/cn if cn else None,'conditional_top1_delta':(sum(r['ah_top1'] for r in cond)-sum(r['baseline_top1'] for r in cond))/cn if cn else None,'baseline_top3_given_total_correct':sum(r['baseline_top3'] for r in cond)/cn if cn else None,'ah_top3_given_total_correct':sum(r['ah_top3'] for r in cond)/cn if cn else None}


def eval_season(allrows,season,cfg):
    train=[r for r in allrows if season_year(r['season'])<season_year(season)];models=fit_dmodels(train);out=[];meta={}
    for cid in COMPS:
        look=market_lookup(allrows,season,cid);params=ou.params_by_season(cid).get(season)
        if not params:meta[cid]={'reason':'NO_FORMAL_PARAMS','market_rows':len(look)};continue
        ms=[m for m in read_processed_matches(cid) if str(m.season)==season];bd=defaultdict(list)
        for m in ms:bd[m.date].append(m)
        hist=[];hc=Counter();ac=Counter();temp=ou.calibrator(cid,season);used=0
        wc=int(cfg['validation']['warmup_competition_matches']);wt=int(cfg['validation']['warmup_team_matches'])
        for dt in sorted(bd):
          for m in sorted(bd[dt],key=lambda z:(z.home_team,z.away_team)):
            row=look.get((m.date.isoformat(),m.home_team,m.away_team))
            if len(hist)>=wc and hc[m.home_team]>=wt and ac[m.away_team]>=wt and row:
                try:p=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=params,use_team_effects=True)
                except Exception:p=None
                if p:
                    prior=temperature_scale_matrix(p['probabilities']['score_matrix'],temp);ipf,audit=joint.ipf(prior,row['one_x_two'],float(row['p_over25']))
                    if ipf is not None and audit.get('converged'):
                        tv=total_vec(ipf);t=max(range(8),key=lambda i:tv[i]);ranks=rank_scores(row['x'],t,models);actual=(m.home_goals,m.away_goals);used+=1
                        out.append({'season':season,'competition_id':cid,'pred_total':t,'total_correct':t==min(7,m.home_goals+m.away_goals),'baseline_top1':ou.top_score(ipf,1,m.home_goals,m.away_goals),'baseline_top3':ou.top_score(ipf,3,m.home_goals,m.away_goals),'ah_top1':int(actual in ranks[:1]),'ah_top3':int(actual in ranks[:3])})
            hist.append(m);hc[m.home_team]+=1;ac[m.away_team]+=1
        meta[cid]={'market_rows':len(look),'used':used}
    return out,meta

def main():
    rows=load_market_rows();cfg=load_config();by={};meta={};alltest=[]
    for s in TESTS:
        r,m=eval_season(rows,s,cfg);by[s]=summarize(r);meta[s]=m;alltest+=r
    payload={'schema_version':'V6.16.6-conditional-margin-ah-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'test_seasons':list(TESTS),'strictly_prior_training':True,'total_source':'V6.16.3 1X2+OU IPF Top-1 total','conditional_target':'D=home_goals-away_goals given T','ah_role':'feature only, never hard constraint','features':['de-vigged 1X2','de-vigged OU2.5','signed AH line','AH price log-ratio','abs(AH line)','competition one-hot'],'quarter_integer_lines_allowed_as_covariates':True},'by_season':by,'aggregate':summarize(alltest),'replication':{'seasons_top1_improved':sum(1 for s in TESTS if by[s].get('score_top1_delta',0)>0),'seasons_conditional_top1_improved':sum(1 for s in TESTS if by[s].get('conditional_top1_delta',0)>0)},'meta':meta,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'by_season':by,'aggregate':payload['aggregate'],'replication':payload['replication']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
