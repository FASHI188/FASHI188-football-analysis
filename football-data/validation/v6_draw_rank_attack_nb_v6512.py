#!/usr/bin/env python3
"""V6.51.2 rank/attack-defence enriched draw specialist.

Final structured draw challenge after V6.51.1. Adds genuinely new pre-match state that
was absent from the prior residual feature vector and is specifically motivated by the
published draw-NB literature: current league classification, prior-season classification,
and separated attacking/defensive production rather than only PPG/GD/total-goal summaries.

All state is computed strictly from matches completed before the current match day.
All same-day fixtures are featurized before any result from that day updates state.
Supervised discretization/NB is trained <2024; the veto + draw execution threshold is
selected on calendar 2024 only; V6.50.6's exact 2025/2025-26 target is evaluated once.

Research only. formal_weight=0. CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import bisect
import json
import math
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/'validation',ROOT/'engine'):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_draw_residual_net_gain_v6507 as r7
import v6_draw_triage_decision_v6508 as tri
import v6_draw_risk_operating_point_v6509 as op
import v6_draw_discrete_nb_v6511 as nb0
import v6_fullseason_2025_replay_v6506 as base
import evaluate_direct_total_margin_matrix_v6477 as core
from diagnose_1x2_market_anchor_v697 import _extract_odds

OUT=ROOT/'manifests'/'v6_draw_rank_attack_nb_v6512_status.json'
FREEZE=ROOT/'manifests'/'v6_hierarchical_selector_forward_v6475_freeze.json'
D=('home','draw','away')
TRAIN_END='2024-01-01'; VALID_END='2025-01-01'; MIN_RETENTION=0.75
TOP_FEATURES=12; MIN_BIN=100; LAPLACE=1.0


class RichState:
    def __init__(self)->None:
        self.base=r7.ContextState()
        self.current_season:dict[str,str]={}
        self.season_stats:dict[str,dict[str,dict[str,float]]]=defaultdict(dict)
        self.prev_rank:dict[tuple[str,str],float]={}
        self.gf_recent=defaultdict(lambda:deque(maxlen=10)); self.ga_recent=defaultdict(lambda:deque(maxlen=10))
        self.gf_home=defaultdict(lambda:deque(maxlen=8)); self.ga_home=defaultdict(lambda:deque(maxlen=8))
        self.gf_away=defaultdict(lambda:deque(maxlen=8)); self.ga_away=defaultdict(lambda:deque(maxlen=8))

    def _finalize(self,cid:str)->None:
        st=self.season_stats.get(cid,{})
        if not st: return
        ordered=sorted(st.items(),key=lambda kv:(-kv[1]['pts'],-(kv[1]['gf']-kv[1]['ga']),-kv[1]['gf'],kv[0]))
        n=len(ordered)
        for i,(team,_s) in enumerate(ordered):
            self.prev_rank[(cid,team)]=i/(n-1) if n>1 else 0.5

    def prepare(self,cid:str,season:str)->None:
        old=self.current_season.get(cid)
        if old is None:
            self.current_season[cid]=season; return
        if old!=season:
            self._finalize(cid); self.season_stats[cid]={}; self.current_season[cid]=season

    @staticmethod
    def _avg(q:deque, default:float)->float:
        return sum(q)/len(q) if q else default

    def _team_season(self,cid:str,team:str)->dict[str,float]:
        return self.season_stats[cid].get(team,{'g':0.0,'pts':0.0,'gf':0.0,'ga':0.0})

    def _rank_map(self,cid:str)->dict[str,float]:
        st=self.season_stats.get(cid,{})
        if not st: return {}
        ordered=sorted(st.items(),key=lambda kv:(-kv[1]['pts'],-(kv[1]['gf']-kv[1]['ga']),-kv[1]['gf'],kv[0]))
        n=len(ordered)
        return {team:(i/(n-1) if n>1 else 0.5) for i,(team,_s) in enumerate(ordered)}

    def features(self,r:dict[str,Any],q:dict[str,float],pick:str)->list[float]:
        cid=str(r['competition_id']); season=str(r['season']); h=str(r['home_team']); a=str(r['away_team'])
        self.prepare(cid,season)
        x=list(self.base.features(r,q,pick))
        sign=1.0 if pick=='home' else -1.0
        ranks=self._rank_map(cid); crh=ranks.get(h,0.5); cra=ranks.get(a,0.5)
        prh=self.prev_rank.get((cid,h),0.5); pra=self.prev_rank.get((cid,a),0.5)
        hs=self._team_season(cid,h); aas=self._team_season(cid,a)
        def rates(s):
            g=max(1.0,s['g']); return s['pts']/g,s['gf']/g,s['ga']/g,(s['gf']-s['ga'])/g
        hppg,hgf,hga,hgd=rates(hs); appg,agf,aga,agd=rates(aas)
        hrgf=self._avg(self.gf_recent[(cid,h)],1.3); hrga=self._avg(self.ga_recent[(cid,h)],1.3)
        argf=self._avg(self.gf_recent[(cid,a)],1.3); arga=self._avg(self.ga_recent[(cid,a)],1.3)
        hhgf=self._avg(self.gf_home[(cid,h)],1.4); hhga=self._avg(self.ga_home[(cid,h)],1.2)
        aagf=self._avg(self.gf_away[(cid,a)],1.1); aaga=self._avg(self.ga_away[(cid,a)],1.4)
        rich=[
            sign*(cra-crh), abs(crh-cra), crh, cra,
            sign*(pra-prh), abs(prh-pra), prh, pra,
            sign*(hppg-appg), abs(hppg-appg),
            sign*(hgf-agf), sign*(hga-aga), abs(hgd-agd),
            sign*(hrgf-argf), sign*(hrga-arga),
            sign*(hhgf-aagf), sign*(hhga-aaga),
            (hrgf+argf)/2.0, (hrga+arga)/2.0,
            (hhgf+aagf+hhga+aaga)/4.0,
            abs(hrgf-argf)+abs(hrga-arga),
            min(hs['g'],aas['g'])/10.0,
        ]
        return x+rich

    def update_day(self,rows:list[dict[str,Any]])->None:
        # Season switches are finalized before updates, preserving previous-season classification.
        for r in rows: self.prepare(str(r['competition_id']),str(r['season']))
        self.base.update_day(rows)
        for r in rows:
            cid=str(r['competition_id']); h=str(r['home_team']); a=str(r['away_team']); hg=int(r['hg']); ag=int(r['ag'])
            for team in (h,a): self.season_stats[cid].setdefault(team,{'g':0.0,'pts':0.0,'gf':0.0,'ga':0.0})
            H=self.season_stats[cid][h]; A=self.season_stats[cid][a]
            H['g']+=1; A['g']+=1; H['gf']+=hg; H['ga']+=ag; A['gf']+=ag; A['ga']+=hg
            if hg>ag: H['pts']+=3
            elif ag>hg: A['pts']+=3
            else: H['pts']+=1; A['pts']+=1
            self.gf_recent[(cid,h)].append(float(hg)); self.ga_recent[(cid,h)].append(float(ag))
            self.gf_recent[(cid,a)].append(float(ag)); self.ga_recent[(cid,a)].append(float(hg))
            self.gf_home[(cid,h)].append(float(hg)); self.ga_home[(cid,h)].append(float(ag))
            self.gf_away[(cid,a)].append(float(ag)); self.ga_away[(cid,a)].append(float(hg))


def build_records(freeze:dict[str,Any])->tuple[list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],dict[str,Any]]:
    rows,meta=core.read_rows(); raw_cache,raw_meta=base.load_raw_row_cache(rows); by_day=defaultdict(list)
    for r in rows: by_day[r7.date_key(r['date'])].append(r)
    state=RichState(); train=[]; valid=[]; target=[]; market=selected=0
    for day in sorted(by_day):
        day_rows=sorted(by_day[day],key=lambda z:(str(z['competition_id']),str(z['home_team']),str(z['away_team'])))
        frozen=[]
        for r in day_rows:
            raw=raw_cache.get((str(r['source_file']),int(r['row_index'])))
            if raw is None: continue
            ex=_extract_odds(raw)
            if ex is None: continue
            q,_=ex; q={d:float(q[d]) for d in D}; s=sum(q.values())
            if not math.isfinite(s) or s<=0: continue
            q={d:q[d]/s for d in D}; market+=1
            dec=base.selector_decision(q,str(r['competition_id']),freeze)
            if not bool(dec['selected']) or str(dec['pick'])=='draw': continue
            selected+=1; pick=str(dec['pick']); actual=base.result_actual(int(r['hg']),int(r['ag']))
            x=state.features(r,q,pick)
            frozen.append({'date':day,'competition_id':str(r['competition_id']),'season':str(r['season']),'pick':pick,'actual':actual,'x':x,'q':q})
        for rec in frozen:
            if rec['date']<TRAIN_END: train.append(rec)
            elif rec['date']<VALID_END: valid.append(rec)
            if rec['season']==base.TARGET_SEASONS.get(rec['competition_id']): target.append(rec)
        state.update_day(day_rows)
    return train,valid,target,{'source_meta':meta,'raw_meta':raw_meta,'market_rows':market,'selected_h_a_rows':selected}


def learn_disc(rows:list[dict[str,Any]],j:int)->dict[str,Any]:
    vals=[float(r['x'][j]) for r in rows]; y=[1 if r['actual']=='draw' else 0 for r in rows]
    cand=sorted(set(round(nb0.quantile(vals,q),12) for q in (0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9)))
    best=[]; score=0.0
    for c in cand:
        s=nb0.split_score(vals,y,[c])
        if s>score: score=s; best=[c]
    for i in range(len(cand)):
        for k in range(i+1,len(cand)):
            cuts=[cand[i],cand[k]]; s=nb0.split_score(vals,y,cuts)
            if s>score: score=s; best=cuts
    return {'feature_index':j,'cuts':best,'information_gain_nats':score}


def fit_nb(rows:list[dict[str,Any]])->dict[str,Any]:
    width=len(rows[0]['x']); disc=[learn_disc(rows,j) for j in range(1,width)]
    disc=sorted(disc,key=lambda z:(-z['information_gain_nats'],z['feature_index']))[:TOP_FEATURES]
    dn=sum(r['actual']=='draw' for r in rows); nn=len(rows)-dn; tables={}
    for d in disc:
        j=d['feature_index']; cuts=d['cuts']; B=len(cuts)+1; tab={'draw':[0]*B,'not_draw':[0]*B}
        for r in rows:
            b=bisect.bisect_right(cuts,float(r['x'][j])); tab['draw' if r['actual']=='draw' else 'not_draw'][b]+=1
        tables[str(j)]={'cuts':cuts,'counts':tab,'gain':d['information_gain_nats']}
    compd=Counter(); compn=Counter(); pickd=Counter(); pickn=Counter()
    for r in rows:
        if r['actual']=='draw': compd[r['competition_id']]+=1; pickd[r['pick']]+=1
        else: compn[r['competition_id']]+=1; pickn[r['pick']]+=1
    return {'draw_n':dn,'not_draw_n':nn,'feature_tables':tables,'selected_features':disc,'comp_draw':dict(compd),'comp_non':dict(compn),'pick_draw':dict(pickd),'pick_non':dict(pickn),'competition_count':len(set(r['competition_id'] for r in rows))}


def posterior(model:dict[str,Any],r:dict[str,Any])->float:
    dn=model['draw_n']; nn=model['not_draw_n']; n=dn+nn
    ld=math.log((dn+LAPLACE)/(n+2*LAPLACE)); ln=math.log((nn+LAPLACE)/(n+2*LAPLACE))
    for sj,t in model['feature_tables'].items():
        j=int(sj); cuts=t['cuts']; b=bisect.bisect_right(cuts,float(r['x'][j])); B=len(cuts)+1
        ld+=math.log((t['counts']['draw'][b]+LAPLACE)/(dn+LAPLACE*B)); ln+=math.log((t['counts']['not_draw'][b]+LAPLACE)/(nn+LAPLACE*B))
    cid=r['competition_id']; pick=r['pick']; C=max(1,model['competition_count'])
    ld+=math.log((model['comp_draw'].get(cid,0)+LAPLACE)/(dn+LAPLACE*C)); ln+=math.log((model['comp_non'].get(cid,0)+LAPLACE)/(nn+LAPLACE*C))
    ld+=math.log((model['pick_draw'].get(pick,0)+LAPLACE)/(dn+2*LAPLACE)); ln+=math.log((model['pick_non'].get(pick,0)+LAPLACE)/(nn+2*LAPLACE))
    m=max(ld,ln); a=math.exp(ld-m); b=math.exp(ln-m); return a/(a+b)


def eval_rule(rows:list[dict[str,Any]],veto:float,draw_th:float|None)->dict[str,Any]:
    ex=hits=dn=dh=ab=0; dec=Counter(); pool=0
    for r in rows:
        risky=r['p_error_model']>=veto
        if not risky: ex+=1; hits+=int(r['actual']==r['pick']); dec[r['pick']]+=1; continue
        pool+=1
        if draw_th is not None and r['p_draw_nb']>=draw_th:
            ex+=1; dn+=1; ok=r['actual']=='draw'; dh+=int(ok); hits+=int(ok); dec['draw']+=1
        else: ab+=1; dec['abstain']+=1
    return {'count':len(rows),'executed_count':ex,'hits':hits,'accuracy':hits/ex if ex else None,'retention_vs_base':ex/len(rows) if rows else None,'veto_pool_count':pool,'draw_pick_count':dn,'draw_hits':dh,'draw_precision':dh/dn if dn else None,'abstain_count':ab,'decision_counts':dict(dec)}


def main()->int:
    freeze=json.loads(FREEZE.read_text(encoding='utf-8')); train,valid,target,data=build_records(freeze)
    err=tri.fit_label(train,lambda r:r['actual']!=r['pick'])
    for rows in (valid,target):
        for r in rows: r['p_error_model']=r7.predict(err,r['x'][:44])  # error model uses original V6.50.8 feature width only
    # Reconstruct V6.50.9 0.40 operating point from validation rather than hard-code outcome knowledge.
    base_err=sum(r['actual']!=r['pick'] for r in valid)/len(valid); base_draw=sum(r['actual']=='draw' for r in valid)/len(valid); vc=[]
    for k in range(25,71):
        th=k/100.0; m=eval_rule(valid,th,None); rem=m['abstain_count']; removed=[r for r in valid if r['p_error_model']>=th]
        rem_err=sum(r['actual']!=r['pick'] for r in removed)/rem if rem else 0; rem_draw=sum(r['actual']=='draw' for r in removed)/rem if rem else 0
        elig=rem>=20 and m['retention_vs_base']>=MIN_RETENTION and m['accuracy']>=(sum(r['actual']==r['pick'] for r in valid)/len(valid)) and rem_err>base_err and rem_draw>base_draw
        vc.append({**m,'veto_threshold':th,'removed_error_rate':rem_err,'removed_draw_rate':rem_draw,'eligible':elig})
    ve=[m for m in vc if m['eligible']]
    if not ve: raise RuntimeError('V6.50.9 veto unavailable')
    cv=max(ve,key=lambda m:(m['accuracy'],m['retention_vs_base'],-m['veto_threshold'])); veto=float(cv['veto_threshold'])
    model=fit_nb(train)
    for rows in (valid,target):
        for r in rows: r['p_draw_nb']=posterior(model,r)
    vv=eval_rule(valid,veto,None); curve=[]
    for k in range(5,96):
        th=k/100.0; m=eval_rule(valid,veto,th); m['draw_threshold']=th
        m['eligible']=bool(m['draw_pick_count']>=20 and (m['draw_precision'] or 0)>=0.50 and (m['accuracy'] or 0)>=0.70 and m['executed_count']>vv['executed_count'])
        curve.append(m)
    eligible=[m for m in curve if m['eligible']]; chosen=max(eligible,key=lambda m:(m['executed_count'],m['accuracy'],m['draw_precision'])) if eligible else None
    diag=[m for m in curve if m['draw_pick_count']>=5]; best=max(diag,key=lambda m:(m['draw_precision'] or 0,m['draw_pick_count'])) if diag else None
    dt=float(chosen['draw_threshold']) if chosen else None; tv=eval_rule(target,veto,None); tr=eval_rule(target,veto,dt) if dt is not None else tv
    payload={'schema_version':'V6.51.2-rank-attack-draw-nb-r1','generated_at_utc':r7.now(),'formal_current_version':'V5.0.1','status':'PASS_RESEARCH_CHALLENGE' if chosen else 'REJECT_NO_VALIDATION_SAFE_RANK_ATTACK_DRAW_REENTRY','classification':'PRE2024_RANK_ATTACK_DEFENCE_DISCRETE_NB_2024_THRESHOLD_2025_TARGET_FORMAL_WEIGHT_0','method':{'new_pre_match_features':['current league rank relationship','previous-season rank relationship','season PPG/GF/GA/GD','recent GF/GA','home GF/GA','away GF/GA'],'supervised_discretization':'pre-2024 only; class information gain','selected_feature_count':TOP_FEATURES,'draw_threshold_selected_only_on_2024':True,'target_results_used_for_training_or_threshold':False,'same_day_policy':'predict all then update all','market_probabilities_mutated':False},'data':{**data,'train_n':len(train),'validation_n':len(valid),'target_n':len(target)},'model':{'selected_features':model['selected_features'],'training_draw_rate':model['draw_n']/(model['draw_n']+model['not_draw_n'])},'validation':{'chosen_veto':cv,'veto_only':vv,'draw_threshold_curve':curve,'chosen_draw':chosen,'best_draw_precision_diagnostic_min5':best},'target_2025':{'veto_only':tv,'with_rank_attack_draw_reentry':tr},'gates':{'target_accuracy_ge_70':bool((tr['accuracy'] or 0)>=0.70),'target_draw_picks_positive':bool(tr['draw_pick_count']>0),'target_draw_precision_ge_50':bool((tr['draw_precision'] or 0)>=0.50),'target_coverage_improved_vs_veto_only':bool(tr['executed_count']>tv['executed_count']),'formal_promotion_allowed':False},'governance':{'research_only':True,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False,'formal_probability_change':False,'formal_selector_threshold_change':False,'historical_replay_cannot_promote':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps({'status':payload['status'],'chosen':chosen,'best':best,'target':tr,'gates':payload['gates']},ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
