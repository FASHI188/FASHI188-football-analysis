#!/usr/bin/env python3
"""V6.13.8b research-only Fast100 using raw prior-date Transfermarkt formation evidence.

The unified historical lineup route does not retain formation. This fallback reads the
raw Transfermarkt lineup evidence, but accepts a formation only from a calendar date
strictly earlier than the target date. Same-date records are never used. Research only.
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed
from platform_core import normalize_team_token

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_formation_stability_fast100_v6138b_status.json'
SEASONS={'2024/25','2025/26'}

def norm_form(v):
    s=' '.join(str(v or '').strip().casefold().split());return s or None

def load_raw_formations(matches):
    market_sets=defaultdict(set)
    for r in matches:
        if r['season'] in SEASONS:market_sets[(r['competition_id'],r['season'])].update((normalize_team_token(r['home']),normalize_team_token(r['away'])))
    raw=[];raw_sets=defaultdict(set)
    for cid in fixed.base.COMPETITIONS:
        path=ROOT/'evidence'/'lineups'/cid/'transfermarkt_datasets.jsonl'
        if not path.exists():continue
        with path.open('r',encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():continue
                try:r=json.loads(line)
                except:continue
                season=str(r.get('season') or '').strip();form=norm_form(r.get('formation'));ko=str(r.get('kickoff_utc') or '')[:10];team=normalize_team_token(r.get('team') or r.get('team_token') or '')
                if season not in SEASONS or not form or not ko or not team:continue
                raw.append({'competition_id':cid,'season':season,'date':ko,'team_raw':team,'formation':form});raw_sets[(cid,season)].add(team)
    maps={}
    for key,left in market_sets.items():maps[key]=fixed._greedy_bijection(left,raw_sets.get(key,set()))[0]
    inv={key:{v:k for k,v in mp.items()} for key,mp in maps.items()}
    idx={}
    for r in raw:
        team=inv.get((r['competition_id'],r['season']),{}).get(r['team_raw'])
        if team is not None:idx[(r['competition_id'],r['season'],r['date'],team)]=r['formation']
    return idx,{'raw_formation_rows':len(raw),'mapped_rows':len(idx),'competition_seasons':len(maps)}

def build():
    matches=fixed.base._load_matches();fm,src=load_raw_formations(matches);hist=defaultdict(list);out=[]
    for r0 in matches:
        r=dict(r0);r['date']=str(r['date'])[:10];r['home']=normalize_team_token(r['home']);r['away']=normalize_team_token(r['away']);cid=r['competition_id'];season=r['season'];hk=(cid,season,r['home']);ak=(cid,season,r['away'])
        if season=='2025/26' and len(hist[hk])>=3 and len(hist[ak])>=3:
            h3=hist[hk][-3:];a3=hist[ak][-3:];h5=hist[hk][-5:];a5=hist[ak][-5:]
            vals={'home_distinct3':len(set(h3)),'away_distinct3':len(set(a3)),'home_distinct5':len(set(h5)),'away_distinct5':len(set(a5)),'home_last_switch':int(h3[-1]!=h3[-2]),'away_last_switch':int(a3[-1]!=a3[-2])}
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            if fav=='home':fu3=vals['home_distinct3']>=2;fc5=vals['home_distinct5']>=3;fs=vals['home_last_switch'];du3=vals['away_distinct3']>=2
            elif fav=='away':fu3=vals['away_distinct3']>=2;fc5=vals['away_distinct5']>=3;fs=vals['away_last_switch'];du3=vals['home_distinct3']>=2
            else:fu3=fc5=fs=du3=False
            out.append({**r,'fav':fav,**vals,'fav_form_unstable3':bool(fu3),'fav_form_churn5':bool(fc5),'fav_form_last_switch':bool(fs),'dog_form_unstable3':bool(du3)})
        # Only target-date formation enters future history after feature creation.
        hf=fm.get((cid,season,r['date'],r['home']));af=fm.get((cid,season,r['date'],r['away']))
        if hf:hist[hk].append(hf)
        if af:hist[ak].append(af)
    return out,src

def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def main():
    rows,src=build();affected=[r for r in rows if r['fav_form_unstable3'] or r['dog_form_unstable3']]
    if len(affected)<100:raise RuntimeError(f'insufficient raw-formation instability matches: {len(affected)}; source={src}')
    test=affected[-100:];m={'all_affected':stat(test),'favorite_unstable3':stat(test,lambda r:r['fav_form_unstable3']),'favorite_churn5':stat(test,lambda r:r['fav_form_churn5']),'favorite_last_switch':stat(test,lambda r:r['fav_form_last_switch']),'p_ge_0.58':stat(test,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav_unstable3':stat(test,lambda r:max(r['opening'])>=0.58 and not r['fav_form_unstable3']),'p_ge_0.60':stat(test,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav_unstable3':stat(test,lambda r:max(r['opening'])>=0.60 and not r['fav_form_unstable3'])}
    payload={'schema_version':'V6.13.8b-formation-stability-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_PRIOR_CALENDAR_DATE_RAW_FORMATION','governance':{'raw_formation_same_date_excluded':True,'target_formation_excluded':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':src,'sample':{'feature_rows':len(rows),'affected_rows':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':m}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
