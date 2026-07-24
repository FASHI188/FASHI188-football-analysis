#!/usr/bin/env python3
"""V6.13.4 research-only Fast100: expected-XI recent actual club minutes load.

For every target Big-5 league match, expected XI is generated only from same-season prior
league lineups. For each expected player, sum actual minutes from ALL club competitions
strictly 1-7 calendar days before the target. National-team games are excluded.
Target actual XI and target-match appearances are never used as inputs.
"""
from __future__ import annotations

import csv,gzip,io,json,urllib.request
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_player_minutes_load_fast100_v6134_status.json'
GAMES='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz'
APPS='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/appearances.csv.gz'

def fetch(url,timeout=240):
    req=urllib.request.Request(url,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r: raw=r.read()
    return gzip.decompress(raw).decode('utf-8-sig',errors='replace'),len(raw)

def pid(v):
    s=str(v or '').strip(); return s.rsplit(':',1)[-1] if ':' in s else s

def load_club_appearances():
    gtext,gbytes=fetch(GAMES);grd=csv.DictReader(io.StringIO(gtext));club_games={};types=defaultdict(int);grows=0
    for r in grd:
        grows+=1;typ=str(r.get('competition_type') or '').strip();gid=str(r.get('game_id') or '');ds=str(r.get('date') or '')[:10]
        if typ=='national_team_competition' or not gid or not ds: continue
        try: d=date.fromisoformat(ds)
        except: continue
        club_games[gid]=(d,typ,str(r.get('competition_id') or ''));types[typ]+=1
    atext,abytes=fetch(APPS);ard=csv.DictReader(io.StringIO(atext));hist=defaultdict(list);arows=used=positive=0;cols=list(ard.fieldnames or [])
    for r in ard:
        arows+=1;gid=str(r.get('game_id') or '');meta=club_games.get(gid)
        if meta is None: continue
        p=pid(r.get('player_id'))
        if not p: continue
        try: m=float(r.get('minutes_played') or 0.0)
        except: m=0.0
        used+=1;positive+=int(m>0);hist[p].append((meta[0],m,gid,meta[1],meta[2]))
    for p in hist:hist[p].sort(key=lambda x:x[0])
    return hist,{'games_compressed_bytes':gbytes,'appearances_compressed_bytes':abytes,'games_rows':grows,'club_games':len(club_games),'club_game_types':dict(types),'appearance_rows':arows,'club_appearance_rows':used,'minutes_positive_rows':positive,'appearance_columns':cols}

def recent_minutes(h,target,maxgap=7):
    mins=0.0;apps=0
    for d,m,*_ in reversed(h):
        gap=(target-d).days
        if gap<=0:continue
        if gap>maxgap:break
        apps+=1;mins+=m
    return apps,mins

def build(hist):
    matches=fixed.base._load_matches();lineups={cid:fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS};team_hist=defaultdict(list);out=[]
    for r in matches:
        cid=r['competition_id'];season=r['season'];ds=str(r['date'])[:10]
        try:target=date.fromisoformat(ds)
        except:continue
        hk=(cid,season,r['home']);ak=(cid,season,r['away']);hp=fixed.base._predicted_xi(team_hist[hk]);ap=fixed.base._predicted_xi(team_hist[ak])
        if hp is not None and ap is not None and season=='2025/26':
            vals={}
            for side,xi in (('home',hp[0]),('away',ap[0])):
                total=0.0;players=0;apps=0;heavy=0
                for raw in xi:
                    c,m=recent_minutes(hist.get(pid(raw),[]),target,7)
                    if c:players+=1;apps+=c;total+=m;heavy+=int(m>=90)
                vals[f'{side}_minutes7']=total;vals[f'{side}_players7']=players;vals[f'{side}_apps7']=apps;vals[f'{side}_heavy_players7']=heavy
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            fm=vals['home_minutes7'] if fav=='home' else vals['away_minutes7'] if fav=='away' else 0.0;dm=vals['away_minutes7'] if fav=='home' else vals['home_minutes7'] if fav=='away' else 0.0
            fp=vals['home_heavy_players7'] if fav=='home' else vals['away_heavy_players7'] if fav=='away' else 0;dp=vals['away_heavy_players7'] if fav=='home' else vals['home_heavy_players7'] if fav=='away' else 0
            out.append({**r,'date':ds,'fav':fav,**vals,'fav_minutes7':fm,'dog_minutes7':dm,'minute_diff7':fm-dm,'fav_heavy_players7':fp,'dog_heavy_players7':dp,'heavy_diff7':fp-dp})
        hi=lineups[cid].get((season,ds,r['home']));ai=lineups[cid].get((season,ds,r['away']))
        if hi:team_hist[hk].append((ds,tuple(hi['starters'])))
        if ai:team_hist[ak].append((ds,tuple(ai['starters'])))
    return out

def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def main():
    hist,source=load_club_appearances();rows=build(hist)
    if len(rows)<100:raise RuntimeError(f'insufficient 2025/26 feature rows: {len(rows)}')
    test=rows[-100:]
    payload={'schema_version':'V6.13.4-player-minutes-load-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_ACTUAL_CLUB_MINUTES','governance':{'expected_xi_same_season_prior_only':True,'club_minutes_window_days':[1,7],'national_team_excluded':True,'target_actual_xi_excluded':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'feature_rows_2025_26':len(rows),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':{'all':stat(test),'favorite_minutes_ge_990':stat(test,lambda r:r['fav_minutes7']>=990),'favorite_minutes_ge_1200':stat(test,lambda r:r['fav_minutes7']>=1200),'favorite_load_diff_ge_360':stat(test,lambda r:r['minute_diff7']>=360),'favorite_7plus_heavy_players':stat(test,lambda r:r['fav_heavy_players7']>=7),'p_ge_0.58':stat(test,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav_minutes1200':stat(test,lambda r:max(r['opening'])>=0.58 and r['fav_minutes7']<1200),'p_ge_0.58_exclude_diff360':stat(test,lambda r:max(r['opening'])>=0.58 and r['minute_diff7']<360),'p_ge_0.60':stat(test,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav_minutes1200':stat(test,lambda r:max(r['opening'])>=0.60 and r['fav_minutes7']<1200)}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['test'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
