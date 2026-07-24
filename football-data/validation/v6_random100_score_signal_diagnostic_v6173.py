#!/usr/bin/env python3
"""V6.17.3 fixed-seed random-100 diagnostic of direct prematch Kambi score signals.

Research-only retrospective audit. Eligibility requires immutable Kambi raw envelope with
observed_at strictly before kickoff, fixture at least two hours old, mapped calendar-year domain,
and one unique processed 90m result identity. Fixed seed prevents hand-picking.
"""
from __future__ import annotations
import json, math, random, re, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine'; VALID=ROOT/'validation'
for p in (ENGINE,VALID):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from platform_core import load_json, normalize_team_token, parse_iso_datetime, read_processed_matches
import v6_multiline_research_forward_v6853 as v6853
RAW=ROOT/'evidence'/'direct_provider_probes'/'kambi'
OUT=ROOT/'manifests'/'v6_random100_score_signal_diagnostic_v6173_status.json'
SEED=6173; SAMPLE_N=100; MIN_AGE=timedelta(hours=2)
BRA_SUFFIX=re.compile(r"(?:[-\s])(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)$",re.I)

def eng(o:Any)->str:
    if not isinstance(o,dict): return ''
    return str(o.get('englishLabel') or o.get('englishName') or o.get('label') or o.get('name') or '').strip()

def oddsv(raw:Any)->float|None:
    try: v=float(raw)/1000.0
    except Exception: return None
    return v if math.isfinite(v) and v>1.0 else None

def norm_source(name:str)->str:
    return normalize_team_token(BRA_SUFFIX.sub('',str(name).strip()))

def resolve_team(source:str,names:set[str])->str|None:
    t=normalize_team_token(source); x=[n for n in names if normalize_team_token(n)==t]
    if len(x)==1: return x[0]
    t=norm_source(source); x=[n for n in names if norm_source(n)==t]
    if len(x)==1: return x[0]
    close=[]
    for n in names:
        a,b=t,norm_source(n)
        if (a.startswith(b) or b.startswith(a)) and abs(len(a)-len(b))<=3: close.append(n)
    return close[0] if len(close)==1 else None

def score_pair(out:dict[str,Any])->tuple[int,int]|None:
    text=' '.join(str(out.get(k) or '') for k in ('englishLabel','label','participant')).strip(); low=text.casefold()
    if any(tok in low for tok in ('other','anders','overig','rest')): return None
    nums=[int(x) for x in re.findall(r'(?<!\d)(\d{1,2})(?!\d)',text)]
    return (nums[0],nums[1]) if len(nums)>=2 and nums[0]<=20 and nums[1]<=20 else None

def correct_score_market(offers:list[dict[str,Any]])->dict[str,Any]|None:
    rows=[]
    for o in offers:
        if eng(o.get('criterion') or {})!='Correct Score' or eng(o.get('betOfferType') or {})!='Correct Score': continue
        for out in o.get('outcomes') or []:
            if not isinstance(out,dict): continue
            pair=score_pair(out); pr=oddsv(out.get('odds'))
            if pair is not None and pr is not None: rows.append((1.0/pr,pair,pr))
    if not rows: return None
    best={}
    for inv,pair,pr in rows:
        if pair not in best or pr<best[pair][1]: best[pair]=(inv,pr)
    z=sum(v[0] for v in best.values()); ranked=sorted(((v[0]/z,pair,v[1]) for pair,v in best.items()),reverse=True)
    return {'ranked':ranked,'explicit_score_count':len(ranked),'inverse_sum':z}

def btts_market(offers:list[dict[str,Any]])->dict[str,float]|None:
    for o in offers:
        if eng(o.get('criterion') or {})!='Both Teams To Score' or eng(o.get('betOfferType') or {})!='Yes/No': continue
        vals={}
        for out in o.get('outcomes') or []:
            if not isinstance(out,dict): continue
            typ=str(out.get('type') or '').upper(); lab=eng(out).casefold(); pr=oddsv(out.get('odds'))
            if pr is None: continue
            if typ=='OT_YES' or lab=='yes': vals['yes']=1/pr
            elif typ=='OT_NO' or lab=='no': vals['no']=1/pr
        if set(vals)=={'yes','no'}:
            s=sum(vals.values()); return {k:v/s for k,v in vals.items()}
    return None

def team_total_lines(offers:list[dict[str,Any]],team_source:str)->list[tuple[float,dict[str,float]]]:
    prefix=f'Total Goals by {team_source}'; rows={}
    for o in offers:
        c=eng(o.get('criterion') or {}); typ=eng(o.get('betOfferType') or {})
        if c!=prefix or typ not in {'Over/Under','Asian Over/Under'}: continue
        outs=[x for x in (o.get('outcomes') or []) if isinstance(x,dict)]
        ov=next((x for x in outs if str(x.get('type'))=='OT_OVER'),None); un=next((x for x in outs if str(x.get('type'))=='OT_UNDER'),None)
        if not ov or not un: continue
        try: line=float(ov.get('line') if ov.get('line') is not None else un.get('line'))/1000.0
        except Exception: continue
        if abs((line-0.5)-round(line-0.5))>1e-9: continue
        po,pu=oddsv(ov.get('odds')),oddsv(un.get('odds'))
        if po is None or pu is None: continue
        io,iu=1/po,1/pu; s=io+iu; rows[line]={'over':io/s,'under':iu/s}
    return sorted(rows.items())

def cdf_distribution(lines:list[tuple[float,dict[str,float]]],max_exact:int=6)->dict[int,float]|None:
    by={round(line,1):target for line,target in lines}; needed=[x+0.5 for x in range(max_exact+1)]
    if not all(round(x,1) in by for x in needed): return None
    cdf=[]; prev=0.0
    for x in needed:
        u=float(by[round(x,1)]['under'])
        if u+0.03<prev: return None
        u=max(prev,min(1.0,u)); cdf.append(u); prev=u
    probs={0:cdf[0]}
    for k in range(1,max_exact+1): probs[k]=max(0.0,cdf[k]-cdf[k-1])
    probs[max_exact+1]=max(0.0,1.0-cdf[-1]); s=sum(probs.values())
    return {k:v/s for k,v in probs.items()} if s>0 else None

def main():
    now=datetime.now(timezone.utc).replace(microsecond=0); caches={}; candidates=[]; reject=Counter()
    for p in sorted(RAW.rglob('*.json')) if RAW.exists() else []:
        try:
            env=load_json(p); ident=env.get('list_event_identity') or {}; observed=parse_iso_datetime(str(env.get('observed_at_utc') or ''),'observed'); kickoff=parse_iso_datetime(str(ident.get('start') or ''),'kickoff')
        except Exception: reject['parse_or_time']+=1; continue
        if observed>=kickoff: reject['not_prematch_snapshot']+=1; continue
        if kickoff+MIN_AGE>now: reject['not_settled_by_time']+=1; continue
        cid=v6853.COMP_MAP.get(str(ident.get('group') or '').strip())
        if not cid: reject['competition_unmapped']+=1; continue
        try: season=v6853.season_for(cid,kickoff)
        except Exception: reject['season_unmapped']+=1; continue
        key=(cid,season)
        if key not in caches:
            try: caches[key]=[m for m in read_processed_matches(cid) if str(m.season)==season]
            except Exception: caches[key]=[]
        season_rows=caches[key]; names={m.home_team for m in season_rows}|{m.away_team for m in season_rows}
        hs=str(ident.get('homeName') or ''); aas=str(ident.get('awayName') or ''); hc=resolve_team(hs,names); ac=resolve_team(aas,names)
        if not hc or not ac: reject['team_identity_unresolved']+=1; continue
        ms=[m for m in season_rows if m.home_team==hc and m.away_team==ac and abs((m.date.date()-kickoff.date()).days)<=1]
        if len(ms)!=1: reject['result_identity_not_unique']+=1; continue
        m=ms[0]; offers=((env.get('payload') or {}).get('betOffers') or []) if isinstance(env.get('payload'),dict) else []
        cs=correct_score_market(offers); bt=btts_market(offers); htl=team_total_lines(offers,hs); atl=team_total_lines(offers,aas)
        if cs is None or bt is None: reject['direct_market_missing']+=1; continue
        candidates.append({'event_id':str(env.get('event_id') or ''),'competition_id':cid,'kickoff_utc':kickoff.isoformat(),'observed_at_utc':observed.isoformat(),'home_source':hs,'away_source':aas,'home_goals':int(m.home_goals),'away_goals':int(m.away_goals),'correct_score':cs,'btts':bt,'home_team_total_lines':htl,'away_team_total_lines':atl})
    candidates.sort(key=lambda r:(r['kickoff_utc'],r['event_id'])); rng=random.Random(SEED); sample=rng.sample(candidates,SAMPLE_N) if len(candidates)>=SAMPLE_N else list(candidates)
    metrics=Counter(); cs_actual_ps=[]; tt_home_n=tt_away_n=tt_home_hit=tt_away_hit=0; rows_out=[]
    for r in sample:
        hg,ag=r['home_goals'],r['away_goals']; actual=(hg,ag); ranked=r['correct_score']['ranked']; top1=ranked[0][1]; top3={x[1] for x in ranked[:3]}; explicit={x[1] for x in ranked}
        metrics['count']+=1; metrics['cs_top1_hits']+=int(actual==top1); metrics['cs_top3_hits']+=int(actual in top3); metrics['cs_actual_listed']+=int(actual in explicit)
        if actual in explicit: cs_actual_ps.append(next(x[0] for x in ranked if x[1]==actual))
        actual_b='yes' if hg>0 and ag>0 else 'no'; pick_b=max(r['btts'],key=r['btts'].get); metrics['btts_hits']+=int(pick_b==actual_b)
        hd=cdf_distribution(r['home_team_total_lines']); ad=cdf_distribution(r['away_team_total_lines']); hpick=apick=None
        if hd is not None:
            tt_home_n+=1; hpick=max(hd,key=hd.get); tt_home_hit+=int((hg if hg<=6 else 7)==hpick)
        if ad is not None:
            tt_away_n+=1; apick=max(ad,key=ad.get); tt_away_hit+=int((ag if ag<=6 else 7)==apick)
        rows_out.append({'event_id':r['event_id'],'competition_id':r['competition_id'],'kickoff_utc':r['kickoff_utc'],'observed_at_utc':r['observed_at_utc'],'home':r['home_source'],'away':r['away_source'],'actual_score':[hg,ag],'correct_score_top1':list(top1),'correct_score_top1_probability':ranked[0][0],'correct_score_top3':[list(x[1]) for x in ranked[:3]],'actual_score_listed':actual in explicit,'btts_probability':r['btts'],'btts_pick':pick_b,'home_team_total_half_lines':[x[0] for x in r['home_team_total_lines']],'away_team_total_half_lines':[x[0] for x in r['away_team_total_lines']],'home_exact_goals_pick_if_fully_identified':hpick,'away_exact_goals_pick_if_fully_identified':apick})
    n=metrics['count']; report={'schema_version':'V6.17.3-random100-score-signal-diagnostic-r1','generated_at_utc':now.isoformat(),'status':'PASS' if n==SAMPLE_N else 'INSUFFICIENT_SETTLED_ELIGIBLE_FOR_100','classification':'RETROSPECTIVE_RESEARCH_USING_ORIGINAL_PREMATCH_TIMESTAMPED_KAMBI_SNAPSHOTS','seed':SEED,'requested_sample_size':SAMPLE_N,'eligible_count':len(candidates),'sample_count':n,'reject_counts':dict(sorted(reject.items())),'metrics':{'correct_score_top1_accuracy':metrics['cs_top1_hits']/n if n else None,'correct_score_top3_accuracy':metrics['cs_top3_hits']/n if n else None,'correct_score_actual_listed_rate':metrics['cs_actual_listed']/n if n else None,'correct_score_mean_actual_probability_when_listed':sum(cs_actual_ps)/len(cs_actual_ps) if cs_actual_ps else None,'btts_top1_accuracy':metrics['btts_hits']/n if n else None,'home_team_exact_goal_identified_count':tt_home_n,'home_team_exact_goal_accuracy':tt_home_hit/tt_home_n if tt_home_n else None,'away_team_exact_goal_identified_count':tt_away_n,'away_team_exact_goal_accuracy':tt_away_hit/tt_away_n if tt_away_n else None},'sample':rows_out,'governance':{'research_only':True,'fixed_seed_no_hand_selection':True,'result_used_only_for_scoring_not_market_ranking':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in report.items() if k!='sample'},ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
