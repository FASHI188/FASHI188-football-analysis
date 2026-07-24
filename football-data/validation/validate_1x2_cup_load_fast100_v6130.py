#!/usr/bin/env python3
"""V6.13.0 research-only Fast100: midweek cup/continental actual-player load.

Expected XI is formed only from same-season prior league lineups. For each target league
match, count expected-XI players who actually appeared in a non-league club competition
(domestic cup, international cup, qualifier/super-cup classified as other) 1-5 days earlier.
National-team competitions are excluded here and tested separately.
"""
from __future__ import annotations
import csv,gzip,io,json,urllib.request
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_cup_load_fast100_v6130_status.json'
GAMES='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz';APPS='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/appearances.csv.gz'
def fetch(url,timeout=240):
    req=urllib.request.Request(url,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:raw=r.read()
    return gzip.decompress(raw).decode('utf-8-sig',errors='replace'),len(raw)
def pid(x):
    s=str(x or '').strip();return s.rsplit(':',1)[-1] if ':' in s else s
def load_duty():
    gtext,gbytes=fetch(GAMES);grd=csv.DictReader(io.StringIO(gtext));duty_games={};types=defaultdict(int);game_rows=0
    for r in grd:
        game_rows+=1;typ=str(r.get('competition_type') or '').strip();gid=str(r.get('game_id') or '');ds=str(r.get('date') or '')[:10]
        if typ in {'domestic_league','national_team_competition',''}:continue
        if not gid or not ds:continue
        try:duty_games[gid]=(date.fromisoformat(ds),typ,str(r.get('competition_id') or ''));types[typ]+=1
        except:pass
    atext,abytes=fetch(APPS);ard=csv.DictReader(io.StringIO(atext));by_player=defaultdict(list);app_rows=duty_apps=positive=0;cols=list(ard.fieldnames or [])
    for r in ard:
        app_rows+=1;gid=str(r.get('game_id') or '');meta=duty_games.get(gid)
        if meta is None:continue
        p=pid(r.get('player_id'))
        if not p:continue
        duty_apps+=1
        try:m=float(r.get('minutes_played') or 0.0);positive+=int(m>0)
        except:m=0.0
        by_player[p].append((meta[0],m,gid,meta[1],meta[2]))
    for p in by_player:by_player[p].sort(key=lambda x:x[0])
    return by_player,{'games_compressed_bytes':gbytes,'appearances_compressed_bytes':abytes,'games_rows':game_rows,'duty_games':len(duty_games),'duty_game_types':dict(types),'appearance_rows':app_rows,'duty_appearance_rows':duty_apps,'minutes_positive_rows':positive,'appearance_columns':cols}
def recent(hist,target,maxgap=5):
    c=0;mins=0.0
    for d,m,*_ in reversed(hist):
        gap=(target-d).days
        if gap<=0:continue
        if gap>maxgap:break
        c+=1;mins+=m
    return c,mins
def build(duty):
    matches=fixed.base._load_matches();lineups={cid:fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS};hist=defaultdict(list);out=[]
    for r in matches:
        cid=r['competition_id'];season=r['season'];ds=str(r['date'])[:10];target=date.fromisoformat(ds);hk=(cid,season,r['home']);ak=(cid,season,r['away']);hp=fixed.base._predicted_xi(hist[hk]);ap=fixed.base._predicted_xi(hist[ak])
        if hp is not None and ap is not None:
            vals={}
            for side,xi in (('home',hp[0]),('away',ap[0])):
                players=apps=0;mins=0.0
                for raw in xi:
                    c,m=recent(duty.get(pid(raw),[]),target,5)
                    if c:players+=1;apps+=c;mins+=m
                vals[f'{side}_cup_players']=players;vals[f'{side}_cup_apps']=apps;vals[f'{side}_cup_minutes']=mins
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            fp=vals['home_cup_players'] if fav=='home' else vals['away_cup_players'] if fav=='away' else 0;dp=vals['away_cup_players'] if fav=='home' else vals['home_cup_players'] if fav=='away' else 0
            fm=vals['home_cup_minutes'] if fav=='home' else vals['away_cup_minutes'] if fav=='away' else 0.0;dm=vals['away_cup_minutes'] if fav=='home' else vals['home_cup_minutes'] if fav=='away' else 0.0
            out.append({**r,'date':ds,'fav':fav,**vals,'fav_cup_players':fp,'dog_cup_players':dp,'cup_player_diff':fp-dp,'fav_cup_minutes':fm,'dog_cup_minutes':dm,'cup_minute_diff':fm-dm})
        hi=lineups[cid].get((season,ds,r['home']));ai=lineups[cid].get((season,ds,r['away']))
        if hi:hist[hk].append((ds,tuple(hi['starters'])))
        if ai:hist[ak].append((ds,tuple(ai['starters'])))
    return out
def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def main():
    duty,source=load_duty();rows=build(duty);latest=[r for r in rows if r['season']=='2025/26'];affected=[r for r in latest if r['home_cup_players'] or r['away_cup_players']]
    if len(affected)<100:raise RuntimeError(f'insufficient cup-load affected matches: {len(affected)}')
    test=affected[-100:]
    payload={'schema_version':'V6.13.0-cup-load-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_ACTUAL_CUP_APPEARANCES','governance':{'expected_xi_same_season_prior_only':True,'duty_window_days':[1,5],'national_team_excluded':True,'target_actual_xi_excluded':True,'exposure_selection_outcome_independent':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'feature_rows_2025_26':len(latest),'affected_2025_26':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':{'all_affected':stat(test),'favorite_5plus_cup_players':stat(test,lambda r:r['fav_cup_players']>=5),'favorite_7plus_cup_players':stat(test,lambda r:r['fav_cup_players']>=7),'favorite_more_by3_players':stat(test,lambda r:r['cup_player_diff']>=3),'favorite_450plus_minutes':stat(test,lambda r:r['fav_cup_minutes']>=450),'p_ge_0.58':stat(test,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav5':stat(test,lambda r:max(r['opening'])>=0.58 and r['fav_cup_players']<5),'p_ge_0.58_exclude_diff3':stat(test,lambda r:max(r['opening'])>=0.58 and r['cup_player_diff']<3),'p_ge_0.60':stat(test,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav5':stat(test,lambda r:max(r['opening'])>=0.60 and r['fav_cup_players']<5)}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['test'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
