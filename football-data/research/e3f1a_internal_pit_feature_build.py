#!/usr/bin/env python3
"""E3f-1A: construct and audit internally derivable PIT features only."""
from __future__ import annotations
import argparse,csv,json,math,subprocess,sys
from collections import defaultdict,deque
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
HERE=Path(__file__).resolve().parent; FD=HERE.parent
for p in (FD/'engine',FD/'validation',HERE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import big5_high_completeness_b100 as b100
import e3f0_pit_feature_coverage_entry as e3f0_entry
from platform_core import ROOT,read_processed_matches
e3f0=e3f0_entry.audit; OUT=ROOT.parent/'artifacts/research/e3f1a_internal_pit_feature_build'
EXPECTED=6251; WINDOWS=(5,10); STYLE=('shots_for','shots_against','sot_for','sot_against','corners_for','corners_against','cards_for','cards_against')
FORBIDDEN=('actual_','fthg','ftag','ftr','hthg','htag','htr','current_match','same_match')

def head():
    try:return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT.parent,text=True,stderr=subprocess.DEVNULL).strip()
    except Exception:return None

def num(v):
    try:x=float(str(v).strip())
    except (TypeError,ValueError):return None
    return x if math.isfinite(x) else None

def observation(raw,hg,ag):
    vals={k:num(raw.get(k)) for k in ('HS','AS','HST','AST','HC','AC','HY','AY')}
    ht=(num(raw.get('HTHG')),num(raw.get('HTAG')))
    out={'style':all(v is not None for v in vals.values()),'state':all(v is not None for v in ht),'hg':hg,'ag':ag}
    if out['style']:
        out['home']={'shots_for':vals['HS'],'shots_against':vals['AS'],'sot_for':vals['HST'],'sot_against':vals['AST'],'corners_for':vals['HC'],'corners_against':vals['AC'],'cards_for':vals['HY'],'cards_against':vals['AY']}
        out['away']={'shots_for':vals['AS'],'shots_against':vals['HS'],'sot_for':vals['AST'],'sot_against':vals['HST'],'corners_for':vals['AC'],'corners_against':vals['HC'],'cards_for':vals['AY'],'cards_against':vals['HY']}
    if out['state']:out['hth'],out['hta']=map(int,ht)
    return out

def state_inc(obs,side):
    if not obs['state']:return {}
    hf,ha,ff,fa=(obs['hth'],obs['hta'],obs['hg'],obs['ag']) if side=='home' else (obs['hta'],obs['hth'],obs['ag'],obs['hg'])
    d={'lead_n':0,'lead_hold':0,'trail_n':0,'trail_recover':0,'draw_n':0,'draw_finish':0}
    if hf>ha:d['lead_n']=1;d['lead_hold']=int(ff>fa)
    elif hf<ha:d['trail_n']=1;d['trail_recover']=int(ff>=fa)
    else:d['draw_n']=1;d['draw_finish']=int(ff==fa)
    return d

def avg(hist,key,w):
    a=list(hist)[-w:]
    return (mean(x[key] for x in a),len(a)) if a else (0.0,0)

def rate(s,n):return (s/n,1) if n else (0.5,0)

def build(cid,keys,raws,reverse=False):
    bydate=defaultdict(list)
    for m in read_processed_matches(cid):bydate[m.date].append(m)
    table=defaultdict(lambda:{'n':0,'pts':0,'gd':0});last={};recent=defaultdict(deque)
    style=defaultdict(lambda:deque(maxlen=10)); states=defaultdict(lambda:{'lead_n':0,'lead_hold':0,'trail_n':0,'trail_recover':0,'draw_n':0,'draw_finish':0})
    out={}
    for date in sorted(bydate):
        day=sorted(bydate[date],key=lambda m:(m.home_team,m.away_team),reverse=reverse)
        for m in day:
            key=f'{m.season}|{m.date.date().isoformat()}|{m.home_team}|{m.away_team}'
            if key not in keys:continue
            for t in (m.home_team,m.away_team):
                while recent[t] and (date-recent[t][0]).days>14:recent[t].popleft()
            h,a=table[(m.season,m.home_team)],table[(m.season,m.away_team)];hl,al=last.get(m.home_team),last.get(m.away_team)
            f={'home_played':h['n'],'away_played':a['n'],'home_points':h['pts'],'away_points':a['pts'],'points_gap':h['pts']-a['pts'],'home_gd':h['gd'],'away_gd':a['gd'],'gd_gap':h['gd']-a['gd'],'home_ppg':h['pts']/h['n'] if h['n'] else 0.0,'away_ppg':a['pts']/a['n'] if a['n'] else 0.0,'home_ppg_available':int(h['n']>0),'away_ppg_available':int(a['n']>0),'home_rest':(date-hl).days if hl else 0,'away_rest':(date-al).days if al else 0,'home_rest_available':int(hl is not None),'away_rest_available':int(al is not None),'rest_gap':((date-hl).days-(date-al).days) if hl and al else 0,'home_7d':sum((date-x).days<=7 for x in recent[m.home_team]),'away_7d':sum((date-x).days<=7 for x in recent[m.away_team]),'home_14d':len(recent[m.home_team]),'away_14d':len(recent[m.away_team])}
            f['gap_7d']=f['home_7d']-f['away_7d'];f['gap_14d']=f['home_14d']-f['away_14d']
            for w in WINDOWS:
                hc=ac=0
                for metric in STYLE:
                    hv,hc=avg(style[m.home_team],metric,w);av,ac=avg(style[m.away_team],metric,w)
                    f[f'home_{metric}_{w}']=hv;f[f'away_{metric}_{w}']=av;f[f'{metric}_gap_{w}']=hv-av
                f[f'home_style_count_{w}']=hc;f[f'away_style_count_{w}']=ac
            for pre,t in (('home',m.home_team),('away',m.away_team)):
                s=states[t]
                for label,success,trials in (('lead_hold',s['lead_hold'],s['lead_n']),('trail_recover',s['trail_recover'],s['trail_n']),('draw_finish',s['draw_finish'],s['draw_n'])):
                    r,av=rate(success,trials);f[f'{pre}_{label}_rate']=r;f[f'{pre}_{label}_trials']=trials;f[f'{pre}_{label}_available']=av
            out[key]={'match_key':key,'competition_id':cid,'season':m.season,'date':m.date.date().isoformat(),'home_team':m.home_team,'away_team':m.away_team,'features':f}
        for m in sorted(day,key=lambda x:(x.home_team,x.away_team)):
            key=f'{m.season}|{m.date.date().isoformat()}|{m.home_team}|{m.away_team}';hg,ag=int(m.home_goals),int(m.away_goals)
            h,a=table[(m.season,m.home_team)],table[(m.season,m.away_team)];h['n']+=1;a['n']+=1;h['pts']+=3 if hg>ag else 1 if hg==ag else 0;a['pts']+=3 if ag>hg else 1 if hg==ag else 0;h['gd']+=hg-ag;a['gd']+=ag-hg
            last[m.home_team]=date;last[m.away_team]=date;recent[m.home_team].append(date);recent[m.away_team].append(date)
            o=observation(raws.get(key,{}),hg,ag)
            if o['style']:style[m.home_team].append(o['home']);style[m.away_team].append(o['away'])
            if o['state']:
                for side,t in (('home',m.home_team),('away',m.away_team)):
                    for k,v in state_inc(o,side).items():states[t][k]+=v
    return out

def b100_keys(rows):
    chosen=set()
    for cid in b100.BIG5:
        lr=[r for r in rows if r['competition_id']==cid];meta=b100.raw_rows(cid)
        seasons=sorted({r['season'] for r in lr},key=lambda s:min(r['date'] for r in lr if r['season']==s))
        for s in reversed(seasons):
            c=[r['match_key'] for r in lr if r['season']==s and meta.get(r['match_key'],{}).get('quality',{}).get('passed')]
            if len(c)>=20:chosen.update(sorted(c,key=lambda k:b100.deterministic_rank(cid,k))[:20]);break
    if len(chosen)!=100:raise RuntimeError(f'B100={len(chosen)}')
    return chosen

def cov(rows):
    return {'count':len(rows),'both_rest':sum(r['features']['home_rest_available'] and r['features']['away_rest_available'] for r in rows),'both_ppg':sum(r['features']['home_ppg_available'] and r['features']['away_ppg_available'] for r in rows),'both_style5':sum(r['features']['home_style_count_5']>=5 and r['features']['away_style_count_5']>=5 for r in rows),'both_style10':sum(r['features']['home_style_count_10']>=10 and r['features']['away_style_count_10']>=10 for r in rows),'both_lead':sum(r['features']['home_lead_hold_available'] and r['features']['away_lead_hold_available'] for r in rows),'both_trail':sum(r['features']['home_trail_recover_available'] and r['features']['away_trail_recover_available'] for r in rows),'both_htdraw':sum(r['features']['home_draw_finish_available'] and r['features']['away_draw_finish_available'] for r in rows)}

def markdown(r):
    lines=['# E3f-1A Internal PIT Feature Build Audit','',f"- HEAD: `{r['repository_head']}`",f"- Status: `{r['research_status']}`",f"- Sample/B100/features: {r['sample_count']}/{r['b100_count']}/{r['feature_count']}",'- Candidate model fit: 0','- Threshold tuning: 0',f"- Same-day order invariant: {r['audit']['same_day_order_invariant']}",'']
    for section in ('full','b100'):
        x=r['coverage'][section];lines+=['## '+section,'']+[f'- {k}: {v}/{x["count"]} ({v/x["count"]:.4%})' for k,v in x.items() if k!='count']+['']
    lines+=['No model training is authorized by this stage.',''];return '\n'.join(lines)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',default=str(OUT));ap.add_argument('--print-summary',action='store_true');a=ap.parse_args();out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    rows,recon=e3f0.reconstruct_fixed_sample()
    if len(rows)!=EXPECTED:raise RuntimeError('sample contract')
    raws={};source={}
    for cid in b100.BIG5:raws[cid],source[cid]=e3f0.load_raw_observations(cid)
    fwd={};rev={}
    for cid in b100.BIG5:
        keys={r['match_key'] for r in rows if r['competition_id']==cid};fwd.update(build(cid,keys,raws[cid],False));rev.update(build(cid,keys,raws[cid],True))
    if set(fwd)!={r['match_key'] for r in rows}:raise RuntimeError('identity')
    invariant=all(fwd[k]['features']==rev[k]['features'] for k in fwd);records=[fwd[r['match_key']] for r in rows]
    names=sorted(records[0]['features']);forbidden=[n for n in names if any(t in n.lower() for t in FORBIDDEN)]
    flat=[]
    for r in records:
        if sorted(r['features'])!=names:raise RuntimeError('schema drift')
        row={k:r[k] for k in ('match_key','competition_id','season','date','home_team','away_team')}
        for n in names:
            v=r['features'][n]
            if not isinstance(v,(int,float)) or not math.isfinite(float(v)):raise RuntimeError(f'nonfinite {n}')
            row[n]=v
        flat.append(row)
    keys=b100_keys(rows);bset=[r for r in records if r['match_key'] in keys]
    with (out/'e3f1a_internal_pit_features.csv').open('w',encoding='utf-8',newline='') as h:w=csv.DictWriter(h,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    report={'schema_version':'1.0','research_id':'E3f-1A','research_status':'PASS' if invariant and not forbidden else 'FAIL','repository_head':head(),'scope':'pure_HDA_feature_build_only','formal_weight':0,'candidate_model_fit_count':0,'threshold_tuning_count':0,'sample_count':len(records),'b100_count':len(bset),'feature_count':len(names),'feature_names':names,'reconstruction':recon,'source_audit':source,'coverage':{'full':cov(records),'b100':cov(bset),'per_league':{cid:cov([r for r in records if r['competition_id']==cid]) for cid in b100.BIG5}},'audit':{'unique_keys':len({r['match_key'] for r in records})==len(records),'same_day_order_invariant':invariant,'all_finite':True,'forbidden_feature_names':forbidden,'standings_reset_by_season':True,'prior_completed_only':True,'current_match_fields_excluded':True,'formal_assets_changed':0},'next_step':'E3f-1B external PIT source contract; no model training'}
    (out/'e3f1a_internal_pit_feature_build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');(out/'e3f1a_internal_pit_feature_build.md').write_text(markdown(report),encoding='utf-8')
    if a.print_summary:print(json.dumps({'status':report['research_status'],'sample':len(records),'b100':len(bset),'features':len(names),'order_invariant':invariant,'model_fit':0},sort_keys=True))
    return 0 if report['research_status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
