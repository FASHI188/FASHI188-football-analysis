#!/usr/bin/env python3
"""V6.4.3 synchronized 1X2 + Asian handicap + over/under challenger.

Purpose: test whether genuinely independent market surfaces improve the two diagnosed weak
classes (draw and away) beyond closing 1X2 alone. Five major leagues are used because
Football-Data provides synchronized historical match, total-goals and AH odds there.

Train: 2022/23 + 2023/24. Select: 2024/25. Holdout: 2025/26.
No holdout tuning; research only.
"""
from __future__ import annotations
import difflib, json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; VALIDATION=ROOT/'validation'; ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import v6_market_residual_fusion_v620 as mkt
import v6_direct_outcome_mvp_v600 as base
import v6_direct_outcome_draw_boundary_v601 as v601
from platform_core import PlatformError, canonical_team_name, normalize_team_token
OUT=ROOT/'manifests'/'v6_multimarket_draw_side_v643_status.json'
LEAGUES=mkt.LEAGUES
SEASON_CODES={'2022/23':'2223','2023/24':'2324','2024/25':'2425','2025/26':'2526'}
SEASONS=list(SEASON_CODES)
L2_GRID=(1.0,10.0,100.0,1000.0)
DRAW_RATIO_GRID=(0.70,0.75,0.80,0.85,0.90,0.95,1.0)
EPS=1e-12

def fnum(row,key,allow_any=False):
    try: x=float(str(row.get(key) or '').strip())
    except Exception: return None
    if not math.isfinite(x): return None
    return x if allow_any or x>1 else None

def two_way(row,families):
    for a,b,label in families:
        oa,ob=fnum(row,a),fnum(row,b)
        if oa and ob:
            ia,ib=1/oa,1/ob;s=ia+ib;return ia/s,ib/s,label
    return None,None,None

def multi_surface(raw):
    one,fam=mkt._closing_market(raw)
    if one is None: return None
    over,under,oufam=two_way(raw,(("AvgC>2.5","AvgC<2.5","average_closing"),("B365C>2.5","B365C<2.5","bet365_closing"),("MaxC>2.5","MaxC<2.5","maximum_closing"),("Avg>2.5","Avg<2.5","average_fallback")))
    line=None
    for k in ('AHCh','AHh'):
        line=fnum(raw,k,allow_any=True)
        if line is not None: break
    ahh,aha,ahfam=two_way(raw,(("AvgCAHH","AvgCAHA","average_closing"),("B365CAHH","B365CAHA","bet365_closing"),("MaxCAHH","MaxCAHA","maximum_closing"),("AvgAHH","AvgAHA","average_fallback")))
    if over is None or line is None or ahh is None: return None
    return {'one':one,'ou_line':2.5,'over_prob':over,'under_prob':under,'ah_line':line,'ah_home_prob':ahh,'ah_away_prob':aha,'families':{'1x2':fam,'ou':oufam,'ah':ahfam}}

def same(a,b): return normalize_team_token(a)==normalize_team_token(b)
def match_rows(cid,model_rows,raw_rows):
    by=defaultdict(list)
    for r in model_rows: by[r['date']].append(r)
    used=set();out=[];stats=Counter()
    for raw in raw_rows:
        surf=multi_surface(raw)
        if surf is None: stats['missing_multimarket']+=1;continue
        try: date=mkt._parse_date(str(raw.get('Date') or ''))
        except Exception: stats['bad_date']+=1;continue
        cand=by.get(date,[])
        if not cand: stats['date_unmatched']+=1;continue
        hr=str(raw.get('HomeTeam') or '').strip();ar=str(raw.get('AwayTeam') or '').strip()
        try: h=canonical_team_name(cid,hr);a=canonical_team_name(cid,ar)
        except Exception: h,a=hr,ar
        ex=[r for r in cand if same(r['home_team'],h) and same(r['away_team'],a)]
        chosen=ex[0] if len(ex)==1 else None
        if chosen is None:
            ranked=[]
            for r in cand:
                hs=difflib.SequenceMatcher(None,normalize_team_token(r['home_team']),normalize_team_token(h)).ratio();as_=difflib.SequenceMatcher(None,normalize_team_token(r['away_team']),normalize_team_token(a)).ratio();ranked.append(((hs+as_)/2,r))
            ranked.sort(key=lambda x:x[0],reverse=True)
            if ranked and ranked[0][0]>=.82 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=.08): chosen=ranked[0][1]
        if chosen is None or id(chosen) in used: stats['identity_unmatched_or_duplicate']+=1;continue
        used.add(id(chosen));x=dict(chosen);x['surface']=surf;out.append(x);stats['matched']+=1
    return out,dict(stats)

def logit(p): p=min(1-1e-8,max(1e-8,p));return math.log(p/(1-p))
def features(r):
    s=r['surface'];one=s['one'];side_gap=abs(math.log(max(EPS,one['home']))-math.log(max(EPS,one['away'])))
    draw_x=[1.0,logit(one['draw']),side_gap,s['under_prob']-.5,s['ah_line'],s['ah_home_prob']-.5,side_gap*(s['under_prob']-.5)]
    hc=one['home']/(one['home']+one['away'])
    side_x=[1.0,logit(hc),s['ah_line'],s['ah_home_prob']-.5,s['under_prob']-.5]
    x=dict(r);x['mm_draw_x']=draw_x;x['mm_side_x']=side_x;x['draw_y']=1 if r['actual_result']=='draw' else 0;x['side_y']=1 if r['actual_result']=='home' else 0;x['is_decisive']=r['actual_result']!='draw';return x

def fit(rows,l2):
    dm=base._fit_binary(rows,'mm_draw_x','draw_y',l2);dec=[r for r in rows if r['is_decisive']];sm=base._fit_binary(dec,'mm_side_x','side_y',l2);return {'draw':dm,'side':sm,'l2':l2}
def prob(r,models):
    pd=min(1-1e-6,max(1e-6,base._predict_binary(models['draw'],r['mm_draw_x'])));ph=min(1-1e-6,max(1e-6,base._predict_binary(models['side'],r['mm_side_x'])));rem=1-pd;return {'home':rem*ph,'draw':pd,'away':rem*(1-ph)}
def score(rows,models=None,ratio=1.0):
    n=h=0;b=rps=ll=0.;pred=Counter();act=Counter();conf={p:{t:0 for t in base.CLASSES} for p in base.CLASSES}
    for r in rows:
        q=r['surface']['one'] if models is None else prob(r,models);p=v601._pick(q,ratio);t=r['actual_result'];hit=int(p==t);n+=1;h+=hit;pred[p]+=1;act[t]+=1;conf[p][t]+=1;b+=sum((q[k]-(1 if t==k else 0))**2 for k in base.CLASSES);tv={'home':(1,0,0),'draw':(0,1,0),'away':(0,0,1)}[t];c1=q['home']-tv[0];c2=q['home']+q['draw']-tv[0]-tv[1];rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,q[t]))
    dh=conf['draw']['draw'];dp=sum(conf['draw'].values());da=act['draw'];ah=conf['away']['away'];ap=sum(conf['away'].values())
    return {'count':n,'hits':h,'accuracy':h/n if n else None,'mean_brier':b/n if n else None,'mean_rps':rps/n if n else None,'mean_log_loss':ll/n if n else None,'predicted_direction_counts':dict(pred),'actual_direction_counts':dict(act),'draw_precision':dh/dp if dp else None,'draw_recall':dh/da if da else None,'away_precision':ah/ap if ap else None,'confusion':conf}

def main():
    roles={};built={};source={};all_by_season={s:[] for s in SEASONS}
    for cid,code in LEAGUES.items():
        b=mkt._build_domain_rows_with_identity(cid,SEASONS);built[cid]=b;source[cid]={}
        for s in SEASONS:
            raw,url=mkt._download_csv(code,SEASON_CODES[s]);matched,stats=match_rows(cid,b[s],raw);fx=[features(r) for r in matched];all_by_season[s]+=fx;source[cid][s]={'url':url,'model_rows':len(b[s]),'csv_rows':len(raw),'matched':len(fx),'stats':stats}
    fit_rows=all_by_season['2022/23']+all_by_season['2023/24'];valid=all_by_season['2024/25'];hold=all_by_season['2025/26']
    if min(len(fit_rows),len(valid),len(hold))<700: raise PlatformError(f'insufficient multimarket rows {len(fit_rows)}/{len(valid)}/{len(hold)}')
    bv=score(valid);bh=score(hold);cands=[]
    for l2 in L2_GRID:
        models=fit(fit_rows,l2)
        for ratio in DRAW_RATIO_GRID:
            m=score(valid,models,ratio);proper=m['mean_brier']<=bv['mean_brier']+1e-12 and m['mean_rps']<=bv['mean_rps']+1e-12 and m['mean_log_loss']<=bv['mean_log_loss']+1e-12;cands.append({'l2':l2,'draw_ratio':ratio,'proper_nonworse':proper,'validation':m})
    elig=[c for c in cands if c['proper_nonworse']] or cands;elig.sort(key=lambda c:(-c['validation']['accuracy'],-(c['validation']['draw_recall'] or 0),c['validation']['mean_log_loss']));sel=elig[0];refit=fit(fit_rows+valid,float(sel['l2']));mh=score(hold,refit,float(sel['draw_ratio']));guard={'brier_nonworse':mh['mean_brier']<=bh['mean_brier']+1e-12,'rps_nonworse':mh['mean_rps']<=bh['mean_rps']+1e-12,'log_loss_nonworse':mh['mean_log_loss']<=bh['mean_log_loss']+1e-12}
    out={'schema_version':'V6.4.3-multimarket-draw-side-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','row_counts':{'fit':len(fit_rows),'validation':len(valid),'holdout':len(hold)},'source_audit':source,'baseline_market_validation':bv,'selected_candidate':sel,'baseline_market_holdout':bh,'multimarket_holdout':mh,'accuracy_gain_pp_vs_market':100*(mh['accuracy']-bh['accuracy']),'draw_recall_gain_pp':100*((mh['draw_recall'] or 0)-(bh['draw_recall'] or 0)),'away_precision_gain_pp':100*((mh['away_precision'] or 0)-(bh['away_precision'] or 0)),'proper_score_guard':guard,'research_gate_passed':mh['accuracy']>bh['accuracy'] and all(guard.values()),'governance':{'holdout_used_for_selection':False,'research_only':True,'automatic_promotion':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
