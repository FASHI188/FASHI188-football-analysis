#!/usr/bin/env python3
"""V6.13.6 research-only Fast100: goalkeeper continuity/stability.

Player position comes from the public Transfermarkt players table. For every target match,
features use only same-season starting lineups strictly earlier than the target. The target
actual goalkeeper/lineup is never used as an input. We test whether recent goalkeeper
switching/instability identifies market-favorite risk.
"""
from __future__ import annotations

import csv,gzip,io,json,urllib.request
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_goalkeeper_stability_fast100_v6136_status.json'
PLAYERS='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/players.csv.gz'
TARGET_SEASONS={'2024/25','2025/26'}

def fetch_players():
    req=urllib.request.Request(PLAYERS,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=120) as r:raw=r.read()
    rd=csv.DictReader(io.StringIO(gzip.decompress(raw).decode('utf-8-sig',errors='replace')));cols=list(rd.fieldnames or []);pos={};rows=keepers=0
    for row in rd:
        rows+=1;pid=str(row.get('player_id') or '').strip();p=str(row.get('position') or '').strip()
        if not pid:continue
        pos[pid]=p
        if p.casefold()=='goalkeeper':keepers+=1
    if not keepers:raise RuntimeError(f'no Goalkeeper positions found; columns={cols}')
    return pos,{'url':PLAYERS,'bytes':len(raw),'rows':rows,'goalkeepers':keepers,'columns':cols}

def pid(v):
    s=str(v or '').strip();return s.rsplit(':',1)[-1] if ':' in s else s

def lineup_gk(starters,pos):
    g=[pid(x) for x in starters if str(pos.get(pid(x),'')).casefold()=='goalkeeper']
    return g[0] if len(g)==1 else None

def build(pos):
    matches=fixed.base._load_matches();lineups={cid:fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS};gh=defaultdict(list);out=[]
    for r in matches:
        cid=r['competition_id'];season=r['season'];ds=str(r['date'])[:10];hk=(cid,season,r['home']);ak=(cid,season,r['away'])
        if season in TARGET_SEASONS and len(gh[hk])>=3 and len(gh[ak])>=3:
            h3=gh[hk][-3:];a3=gh[ak][-3:];h5=gh[hk][-5:];a5=gh[ak][-5:]
            vals={'home_gk_distinct3':len(set(h3)),'away_gk_distinct3':len(set(a3)),'home_gk_distinct5':len(set(h5)),'away_gk_distinct5':len(set(a5)),'home_gk_last_switch':int(len(h3)>=2 and h3[-1]!=h3[-2]),'away_gk_last_switch':int(len(a3)>=2 and a3[-1]!=a3[-2])}
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            if fav=='home':fu3=vals['home_gk_distinct3']>=2;fu5=vals['home_gk_distinct5']>=2;fs=vals['home_gk_last_switch'];du3=vals['away_gk_distinct3']>=2
            elif fav=='away':fu3=vals['away_gk_distinct3']>=2;fu5=vals['away_gk_distinct5']>=2;fs=vals['away_gk_last_switch'];du3=vals['home_gk_distinct3']>=2
            else:fu3=fu5=fs=du3=False
            out.append({**r,'date':ds,'fav':fav,**vals,'fav_gk_unstable3':bool(fu3),'fav_gk_unstable5':bool(fu5),'fav_gk_last_switch':bool(fs),'dog_gk_unstable3':bool(du3)})
        # Update strictly after features from target's observed lineup.
        hi=lineups[cid].get((season,ds,r['home']));ai=lineups[cid].get((season,ds,r['away']))
        if hi:
            g=lineup_gk(hi['starters'],pos)
            if g:gh[hk].append(g)
        if ai:
            g=lineup_gk(ai['starters'],pos)
            if g:gh[ak].append(g)
    return out

def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def metrics(rows):
    return {'all_affected':stat(rows),'favorite_unstable3':stat(rows,lambda r:r['fav_gk_unstable3']),'favorite_unstable5':stat(rows,lambda r:r['fav_gk_unstable5']),'favorite_last_switch':stat(rows,lambda r:r['fav_gk_last_switch']),'underdog_unstable3':stat(rows,lambda r:r['dog_gk_unstable3']),'p_ge_0.58':stat(rows,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav_unstable3':stat(rows,lambda r:max(r['opening'])>=0.58 and not r['fav_gk_unstable3']),'p_ge_0.58_exclude_fav_last_switch':stat(rows,lambda r:max(r['opening'])>=0.58 and not r['fav_gk_last_switch']),'p_ge_0.60':stat(rows,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav_unstable3':stat(rows,lambda r:max(r['opening'])>=0.60 and not r['fav_gk_unstable3'])}
def main():
    pos,source=fetch_players();rows=build(pos);affected=[r for r in rows if r['fav_gk_unstable3'] or r['dog_gk_unstable3']]
    if len(affected)<100:raise RuntimeError(f'insufficient goalkeeper-instability affected matches: {len(affected)}')
    test=affected[-100:];byseason={s:metrics([r for r in test if r['season']==s]) for s in sorted(TARGET_SEASONS)}
    payload={'schema_version':'V6.13.6-goalkeeper-stability-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_LINEUP_GOALKEEPER_HISTORY','governance':{'position_source_current_profile_role_only':True,'same_season_prior_lineups_only':True,'target_actual_lineup_excluded':True,'exposure_selection_outcome_independent':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'feature_rows':len(rows),'affected_rows':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date'],'test_by_season':{s:sum(r['season']==s for r in test) for s in sorted(TARGET_SEASONS)}},'test':metrics(test),'by_season':byseason}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'sample':payload['sample'],'test':payload['test']},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
