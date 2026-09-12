#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,json,math
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any

import runtime as rt
from new_engine_v1 import formal_fusion_v2 as formal_v2
from new_engine_v1 import pure_engine as v1_engine
from historical_xg_challenger_v1 import historical_xg_challenger as hxg

SCHEMA='football3-promoted-cold-start-v2-1-evaluation-v1'
BIG5=('ENG_PremierLeague','ESP_LaLiga','GER_Bundesliga','ITA_SerieA','FRA_Ligue1')
TARGET_SEASONS=('2022/23','2023/24','2024/25','2025/26')
DEV=set(TARGET_SEASONS[:3]); SELECT='2025/26'
LOWER_CODE={'ENG_PremierLeague':'E1','ESP_LaLiga':'SP2','GER_Bundesliga':'D2','ITA_SerieA':'I2','FRA_Ligue1':'F2'}
SEASON_CODE={'2021/22':'2122','2022/23':'2223','2023/24':'2324','2024/25':'2425','2025/26':'2526'}
PRIOR=8.0; HALF_LIFE=210.0; CROSS_SHRINK=0.58; SCALE=0.5
EPS=1e-15


def prev_season(s:str)->str:
    a,b=rt._season_years(s)
    if a is None or b is None or a==b: raise RuntimeError(f'unsupported season {s}')
    return f'{a-1}/{str(a)[-2:]}'

def sha_file(p:Path)->str:
    h=hashlib.sha256();
    with p.open('rb') as f:
        for ch in iter(lambda:f.read(1<<20),b''): h.update(ch)
    return h.hexdigest()

def parse_csv_date(s:str)->datetime:
    s=str(s or '').strip()
    for fmt in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc,hour=12)
        except ValueError: pass
    raise RuntimeError(f'unparseable lower-tier date {s!r}')
def matrix_dict(pred:dict[str,Any])->dict[tuple[int,int],float]:
    return {(int(c['home_goals']),int(c['away_goals'])):float(c['probability']) for c in pred['score_matrix']}
def matrix_pred(identity:dict[str,str],m:dict[tuple[int,int],float])->dict[str,Any]:
    z=math.fsum(m.values()); cells=[]
    if not math.isfinite(z) or z<=0: raise RuntimeError('invalid matrix mass')
    m={k:v/z for k,v in m.items()}
    for (h,a),p in sorted(m.items()): cells.append({'home_goals':h,'away_goals':a,'probability':p})
    ph=math.fsum(p for (h,a),p in m.items() if h>a); pd=math.fsum(p for (h,a),p in m.items() if h==a); pa=1-ph-pd
    return {**identity,'score_matrix':cells,'p_home':ph,'p_draw':pd,'p_away':pa}
def tilt(pred:dict[str,Any],delta:float)->dict[str,Any]:
    base=matrix_dict(pred); logs=[]
    mx=-1e300
    for (h,a),p in base.items():
        lp=math.log(max(EPS,p))+delta*(h-a); logs.append(((h,a),lp)); mx=max(mx,lp)
    w={k:math.exp(lp-mx) for k,lp in logs}; ident={k:pred[k] for k in ('fixture_id','competition_id','season','kickoff','home_team_id','away_team_id')}
    return matrix_pred(ident,w)
def exp_goal_diff(pred:dict[str,Any])->float:
    return math.fsum((int(c['home_goals'])-int(c['away_goals']))*float(c['probability']) for c in pred['score_matrix'])
def metric_one(pred:dict[str,Any],hg:int,ag:int)->dict[str,float]:
    p=[float(pred['p_home']),float(pred['p_draw']),float(pred['p_away'])]; yi=0 if hg>ag else 1 if hg==ag else 2
    top=int(max(range(3),key=lambda i:p[i])==yi)
    ll=-math.log(max(EPS,p[yi])); b=sum((p[i]-(1.0 if i==yi else 0.0))**2 for i in range(3))
    ycum=[1.0 if yi<=0 else 0.0,1.0 if yi<=1 else 0.0]; pcum=[p[0],p[0]+p[1]]
    rps=sum((pcum[i]-ycum[i])**2 for i in range(2))/2.0
    m=matrix_dict(pred); sp=max(EPS,m.get((hg,ag),0.0)); sll=-math.log(sp)
    mode=max(m,key=m.get); exact=int(mode==(hg,ag)); et=math.fsum((h+a)*q for (h,a),q in m.items())
    over=math.fsum(q for (h,a),q in m.items() if h+a>=3); yover=1.0 if hg+ag>=3 else 0.0
    return {'Top1':float(top),'LogLoss':ll,'Brier':b,'RPS':rps,'score_logloss':sll,'exact_score_top1_accuracy':float(exact),'total_goals_mae':abs(et-(hg+ag)),'over_under_2_5_brier':(over-yover)**2}
def aggregate(items:list[dict[str,float]])->dict[str,float]:
    if not items:return {}
    return {k:math.fsum(x[k] for x in items)/len(items) for k in items[0]}

def load_lower(repo:Path,sources:Path,comp:str,season:str)->dict[str,Any]:
    code=LOWER_CODE[comp]; sc=SEASON_CODE[season]; p=sources/sc/f'{code}.csv'
    if not p.is_file(): raise RuntimeError(f'missing lower source {p}')
    aliases=rt._read_aliases(repo); rows=[]; team_names=defaultdict(set)
    with p.open('r',encoding='utf-8-sig',newline='') as f:
        for raw in csv.DictReader(f):
            if not str(raw.get('HomeTeam') or '').strip() or not str(raw.get('AwayTeam') or '').strip(): continue
            if str(raw.get('FTHG') or '').strip()=='' or str(raw.get('FTAG') or '').strip()=='': continue
            dt=parse_csv_date(raw.get('Date'))
            hn=rt._canonical_team(comp,str(raw['HomeTeam']).strip(),aliases); an=rt._canonical_team(comp,str(raw['AwayTeam']).strip(),aliases)
            hid=rt._global_team_id(hn); aid=rt._global_team_id(an)
            team_names[hid].add(str(raw['HomeTeam']).strip()); team_names[aid].add(str(raw['AwayTeam']).strip())
            rows.append((dt,hid,aid,int(float(raw['FTHG'])),int(float(raw['FTAG']))))
    if not rows: raise RuntimeError(f'empty lower source {p}')
    return {'path':str(p),'sha256':sha_file(p),'rows':rows,'team_names':{k:sorted(v) for k,v in team_names.items()},'team_ids':set(team_names)}
def lower_strength(lower:dict[str,Any],team_id:str,cutoff:datetime)->tuple[float,dict[str,Any]]:
    gf=ga=w=lg=lw=0.0
    for dt,h,a,hg,ag in lower['rows']:
        if dt>=cutoff: continue
        days=max(0.0,(cutoff-dt).total_seconds()/86400.0); wt=math.exp(-math.log(2)*days/HALF_LIFE)*CROSS_SHRINK
        lg+=wt*(hg+ag); lw+=2*wt
        if h==team_id: gf+=wt*hg; ga+=wt*ag; w+=wt
        elif a==team_id: gf+=wt*ag; ga+=wt*hg; w+=wt
    if w<=0 or lw<=0: raise RuntimeError(f'lower-tier strength unavailable for {team_id}')
    avg=lg/lw; gfr=(gf+PRIOR*avg)/(w+PRIOR); gar=(ga+PRIOR*avg)/(w+PRIOR)
    raw=math.log(max(EPS,gfr/avg))-math.log(max(EPS,gar/avg)); s=SCALE*raw
    return s,{'effective_match_weight':w,'decayed_gf':gf,'decayed_ga':ga,'league_goal_rate':avg,'raw_strength':raw,'scaled_strength':s}
def promoted_sets(history:list[Any])->dict[tuple[str,str],dict[str,str]]:
    rosters=defaultdict(dict)
    for r in history:
        if r.competition_id in BIG5: rosters[(r.competition_id,r.season)][r.home_team_id]=r.home_team_name; rosters[(r.competition_id,r.season)][r.away_team_id]=r.away_team_name
    out={}
    for comp in BIG5:
        for season in TARGET_SEASONS:
            cur=rosters.get((comp,season),{}); prev=rosters.get((comp,prev_season(season)),{})
            if not cur or not prev: raise RuntimeError(f'top roster missing {comp} {season}')
            out[(comp,season)]={tid:name for tid,name in cur.items() if tid not in prev}
            if not out[(comp,season)]: raise RuntimeError(f'no promoted set {comp} {season}')
    return out

def cohort(history:list[Any],ps:dict[tuple[str,str],dict[str,str]])->list[Any]:
    chosen={}
    by_team=defaultdict(int)
    for r in sorted(history,key=lambda x:(x.kickoff,x.competition_id,x.fixture_id)):
        key=(r.competition_id,r.season)
        if key not in ps: continue
        promoted=ps[key]
        hits=[tid for tid in (r.home_team_id,r.away_team_id) if tid in promoted]
        if not hits: continue
        need=any(by_team[(key,tid)]<4 for tid in hits)
        if not need: continue
        chosen[r.fixture_id]=r
        for tid in hits:
            if by_team[(key,tid)]<4: by_team[(key,tid)]+=1
    for key,prom in ps.items():
        for tid in prom:
            if by_team[(key,tid)]<4: raise RuntimeError(f'cohort <4 {key} {prom[tid]} {by_team[(key,tid)]}')
    return sorted(chosen.values(),key=lambda x:(x.kickoff,x.competition_id,x.fixture_id))
def validate_lower(ps:dict[tuple[str,str],dict[str,str]],lowers:dict[tuple[str,str],dict[str,Any]])->list[dict[str,Any]]:
    out=[]
    for (comp,season),teams in sorted(ps.items()):
        ls=prev_season(season); lower=lowers[(comp,ls)]
        for tid,name in sorted(teams.items()):
            hits=lower['team_names'].get(tid,[])
            if not hits: raise RuntimeError(f'promoted team missing in prior lower tier: {comp} {season} {name} {tid}')
            out.append({'competition_id':comp,'top_season':season,'previous_lower_season':ls,'top_team_name':name,'team_id':tid,'lower_source_names':hits,'lower_source_sha256':lower['sha256']})
    return out

def replay_targets(history:list[Any],xg_labels:dict[str,Any],targets:list[Any],ps,lowers)->list[dict[str,Any]]:
    target_ids={r.fixture_id for r in targets}; by_cut=defaultdict(list)
    for r in targets: by_cut[r.kickoff-timedelta(minutes=60)].append(r)
    events=rt.history_delta_events(history,xg_labels,None,max(by_cut)+timedelta(minutes=1),None)
    ei=0; state=formal_v2.new_candidate_state(); predicted=set(); rows=[]
    for cut in sorted(by_cut):
        pending=[]
        while ei<len(events) and rt._parse_dt(str(events[ei]['event_at']),'event_at')<=cut:
            e=events[ei]; ei+=1
            if e['event_type']=='FIXTURE_FREEZE' and e['fixture_id'] in predicted: continue
            pending.append(e)
        if pending: rt._apply_events(state,pending,cut)
        batch=sorted(by_cut[cut],key=lambda r:(r.competition_id,r.fixture_id))
        fixtures=[hxg.FixtureRow(r.fixture_id,r.competition_id,r.season,r.kickoff,r.home_team_id,r.away_team_id,r.home_team_name,r.away_team_name) for r in batch]
        xgps,v1ps=state.predict_batch(fixtures,include_matrix=True)
        for r,xgp,v1p in zip(batch,xgps,v1ps):
            dynamic=xgp.get('dynamic') or {}; fallback=bool(dynamic.get('fallback_exact_v1'))
            formal=v1p if fallback else formal_v2.blend_active_predictions(v1p,xgp)
            promoted=ps[(r.competition_id,r.season)]; ph=r.home_team_id in promoted; pa=r.away_team_id in promoted
            lower=lowers[(r.competition_id,prev_season(r.season))]
            hs,hmeta=(0.0,None); as_,ameta=(0.0,None)
            if ph: hs,hmeta=lower_strength(lower,r.home_team_id,cut)
            if pa: as_,ameta=lower_strength(lower,r.away_team_id,cut)
            rows.append({'fixture':r,'cutoff':cut,'v1':v1p,'formal':formal,'formal_route':'FROZEN_V1_EXACT_FALLBACK' if fallback else 'FUSION_V2_ACTIVE','xg_dynamic':dynamic,'home_promoted':ph,'away_promoted':pa,'home_strength':hs,'away_strength':as_,'home_strength_meta':hmeta,'away_strength_meta':ameta})
            predicted.add(r.fixture_id)
    return rows

def delta_for(row:dict[str,Any],gamma:float,use_strength=True,use_gap=True)->float:
    hs=row['home_strength'] if use_strength else 0.0; aw=row['away_strength'] if use_strength else 0.0
    c=(1 if row['home_promoted'] else 0)-(1 if row['away_promoted'] else 0)
    return hs-aw-(gamma*c if use_gap else 0.0)
def gamma_derivative(rows:list[dict[str,Any]],gamma:float)->float:
    total=0.0
    for row in rows:
        c=(1 if row['home_promoted'] else 0)-(1 if row['away_promoted'] else 0)
        if c==0: continue
        pred=tilt(row['v1'],delta_for(row,gamma,True,True)); d_obs=row['fixture'].home_goals-row['fixture'].away_goals
        total+=c*(d_obs-exp_goal_diff(pred))
    return total
def fit_gamma(rows:list[dict[str,Any]])->tuple[float,dict[str,Any]]:
    lo,hi=-0.25,0.25; dlo,dhi=gamma_derivative(rows,lo),gamma_derivative(rows,hi); expanded=0
    while dlo*dhi>0 and expanded<7:
        lo*=2; hi*=2; dlo,dhi=gamma_derivative(rows,lo),gamma_derivative(rows,hi); expanded+=1
    if dlo*dhi>0: raise RuntimeError(f'unbounded/unsupported gamma MLE derivative lo={dlo} hi={dhi}')
    for _ in range(80):
        mid=(lo+hi)/2; dm=gamma_derivative(rows,mid)
        if dm==0: lo=hi=mid; break
        if dlo*dm<=0: hi=mid; dhi=dm
        else: lo=mid; dlo=dm
    g=(lo+hi)/2
    return g,{'solver':'convex_score_logloss_derivative_bisection','iterations':80,'expansions':expanded,'final_derivative':gamma_derivative(rows,g)}
def evaluate_rows(rows:list[dict[str,Any]],gamma:float)->dict[str,Any]:
    models=('frozen_v1','formal_baseline','strength_only','tier_gap_only','candidate')
    vals={m:[] for m in models}; per=defaultdict(lambda:{m:[] for m in models}); detail=[]
    for row in rows:
        f=row['fixture']; preds={'frozen_v1':row['v1'],'formal_baseline':row['formal'],'strength_only':tilt(row['v1'],delta_for(row,gamma,True,False)),'tier_gap_only':tilt(row['v1'],delta_for(row,gamma,False,True)),'candidate':tilt(row['v1'],delta_for(row,gamma,True,True))}
        md={}
        for m,p in preds.items():
            x=metric_one(p,f.home_goals,f.away_goals); vals[m].append(x); per[f.competition_id][m].append(x); md[m]=x
        detail.append({'fixture_id':f.fixture_id,'competition_id':f.competition_id,'season':f.season,'kickoff':f.kickoff.isoformat(),'home':f.home_team_name,'away':f.away_team_name,'score':[f.home_goals,f.away_goals],'formal_route':row['formal_route'],'xg_dynamic':row['xg_dynamic'],'home_promoted':row['home_promoted'],'away_promoted':row['away_promoted'],'home_strength':row['home_strength'],'away_strength':row['away_strength'],'candidate_delta':delta_for(row,gamma),'metrics':md})
    return {'n':len(rows),'metrics':{m:aggregate(vals[m]) for m in models},'per_league':{c:{m:aggregate(v[m]) for m in models} for c,v in sorted(per.items())},'details':detail}
def adjudicate(sel:dict[str,Any])->tuple[bool,list[str]]:
    m=sel['metrics']; c=m['candidate']; bases=(m['frozen_v1'],m['formal_baseline']); fails=[]
    for metric,need in [('LogLoss',0.01),('Brier',0.005),('RPS',0.003)]:
        best=min(b[metric] for b in bases)
        if not c[metric] <= best-need+1e-12: fails.append(f'{metric}_GAIN')
    if c['Top1'] < max(b['Top1'] for b in bases)-0.01-1e-12: fails.append('TOP1_DROP')
    if c['score_logloss'] > min(b['score_logloss'] for b in bases)+0.01+1e-12: fails.append('SCORE_LOSS_REGRESSION')
    if c['over_under_2_5_brier'] > min(b['over_under_2_5_brier'] for b in bases)+0.005+1e-12: fails.append('OU25_REGRESSION')
    for comp,x in sel['per_league'].items():
        best=min(x['frozen_v1']['LogLoss'],x['formal_baseline']['LogLoss'])
        if x['candidate']['LogLoss']>best+0.03+1e-12: fails.append(f'{comp}_LOGLOSS_REGRESSION')
    if sel['n']<30: fails.append('SELECTION_N_LT_30')
    if set(sel['per_league'])!=set(BIG5): fails.append('SELECTION_MISSING_LEAGUE')
    return not fails,fails

def current_audit(path:Path,lowers:dict[tuple[str,str],dict[str,Any]],probe_manifest:Path)->dict[str,Any]:
    d=json.load(path.open()); pm=json.load(probe_manifest.open()); fixture_by_club={}
    for fx in pm['fixtures']:
        for club in fx['promoted_clubs']:
            fixture_by_club[club]=fx
    out=[]
    for comp in BIG5:
        dom=d['domains'][comp]
        promoted=set(dom['promoted_entering_clubs'])
        for t in dom['teams']:
            if t['current_name'] in promoted:
                out.append({k:t.get(k) for k in ('current_name','current_team_id','canonical_global_id','historical_state_team_id','previous_season_identity','participation_classification','historical_match_count','historical_matches_by_season','linked_xg_count','strength_state_local','strength_state_global','legal_xg_coverage_available','evidence_status','state_status','mapping_provenance')})
                out[-1]['competition_id']=comp
                tid=out[-1]['historical_state_team_id']
                lower=lowers[(comp,'2025/26')]
                names=lower['team_names'].get(tid,[])
                if not names:
                    raise RuntimeError(f'current promoted lower-tier identity not exact: {comp} {t["current_name"]} {tid}')
                fx=fixture_by_club.get(t['current_name'])
                if fx is None: raise RuntimeError(f'current promoted formal probe missing: {comp} {t["current_name"]}')
                cut=rt._parse_dt(fx['kickoff'],'probe kickoff')-timedelta(minutes=60)
                strength,smeta=lower_strength(lower,tid,cut)
                out[-1]['previous_lower_tier_season']='2025/26'; out[-1]['previous_lower_tier_source_names']=names
                out[-1]['previous_lower_tier_source_sha256']=lower['sha256']; out[-1]['cold_start_strength_at_probe']=strength; out[-1]['cold_start_strength_meta']=smeta
                out[-1]['formal_probe_fixture']={**fx,'cutoff':cut.isoformat()}
        if dom['missing'] or dom['extra'] or dom['ambiguous'] or dom['duplicate_canonical_identity']: raise RuntimeError(f'current identity hard gate failed {comp}')
    if len(out)!=14: raise RuntimeError(f'expected 14 promoted current clubs got {len(out)}')
    return {'team_count':len(out),'teams':out}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',required=True); ap.add_argument('--sources',required=True); ap.add_argument('--understat-db',required=True); ap.add_argument('--confirmation-dir',required=True); ap.add_argument('--current-audit',required=True); ap.add_argument('--probe-manifest',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    repo=Path(a.repo_root); sources=Path(a.sources); out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True)
    history,v1src=rt.load_frozen_v1_history(repo); xgl,xgsrc=rt.load_xg_labels(history,Path(a.understat_db),Path(a.confirmation_dir))
    ps=promoted_sets(history); lowers={}
    needed=sorted({(c,prev_season(s)) for c in BIG5 for s in TARGET_SEASONS} | {(c,'2025/26') for c in BIG5})
    for c,s in needed: lowers[(c,s)]=load_lower(repo,sources,c,s)
    lower_links=validate_lower(ps,lowers); targets=cohort(history,ps); replay=replay_targets(history,xgl,targets,ps,lowers)
    dev=[r for r in replay if r['fixture'].season in DEV]; selrows=[r for r in replay if r['fixture'].season==SELECT]
    gamma,solver=fit_gamma(dev); devres=evaluate_rows(dev,gamma); sel=evaluate_rows(selrows,gamma); passed,fails=adjudicate(sel)
    cur=current_audit(Path(a.current_audit),lowers,Path(a.probe_manifest))
    payload={'schema_version':SCHEMA,'status':'PASS' if passed else 'STOP_NO_CANDIDATE','exact_base':'0bfcd824d855161595226f3a67b33c4cfa9ab8a3','formal_head':rt.FORMAL_HEAD,'current_sha256':rt.CURRENT_SHA256,'source_identity':{'v1':v1src,'xg':xgsrc,'lower_tier_files':[{'competition_id':c,'season':s,'path':lowers[(c,s)]['path'],'sha256':lowers[(c,s)]['sha256']} for c,s in needed]},'historical_promoted_identity_links':lower_links,'gamma':gamma,'gamma_solver':solver,'development':devres,'selection':sel,'current_promoted_audit':cur,'scientific_pass':passed,'failed_gates':fails,'formal_or_current_modified':False,'club_specific_patch_count':0,'note':'Historical candidate adjudication only. Formal activation remains prohibited and fresh 2026/27 prospective confirmation is still required even on PASS.'}
    out.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({'status':payload['status'],'gamma':gamma,'dev_n':devres['n'],'selection_n':sel['n'],'failed_gates':fails},ensure_ascii=False))
if __name__=='__main__': main()
