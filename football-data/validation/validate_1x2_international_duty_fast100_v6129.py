#!/usr/bin/env python3
"""V6.12.9c research-only Fast100: recent international-duty load in expected XI.

Expected XI uses same-season earlier club lineups only. International duty is an actual
national_team_competition appearance strictly before the target. The exposure window is
1-10 days; the final test is the latest 100 exposed matches across 2024/25 and 2025/26,
with each season reported separately. Target actual XI is never used as a feature.
"""
from __future__ import annotations
import csv,gzip,io,json,urllib.request
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_international_duty_fast100_v6129_status.json'
GAMES='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz';APPS='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/appearances.csv.gz'
def fetch(url,timeout=180):
    req=urllib.request.Request(url,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:raw=r.read()
    return gzip.decompress(raw).decode('utf-8-sig',errors='replace'),len(raw)
def pid(x):
    s=str(x or '').strip();return s.rsplit(':',1)[-1] if ':' in s else s
def load_international_appearances():
    gtext,gbytes=fetch(GAMES);grd=csv.DictReader(io.StringIO(gtext));intl_games={};game_rows=0
    for r in grd:
        game_rows+=1
        if str(r.get('competition_type') or '').strip()!='national_team_competition':continue
        d=str(r.get('date') or '')[:10];gid=str(r.get('game_id') or '')
        if gid and d:
            try:intl_games[gid]=date.fromisoformat(d)
            except:pass
    atext,abytes=fetch(APPS,240);ard=csv.DictReader(io.StringIO(atext));by_player=defaultdict(list);app_rows=intl_app_rows=minutes_positive=0;cols=list(ard.fieldnames or [])
    for r in ard:
        app_rows+=1;gid=str(r.get('game_id') or '');d=intl_games.get(gid)
        if d is None:continue
        p=pid(r.get('player_id'))
        if not p:continue
        intl_app_rows+=1
        try:m=float(r.get('minutes_played') or 0.0);minutes_positive+=int(m>0)
        except:m=0.0
        by_player[p].append((d,m,gid))
    for p in by_player:by_player[p].sort(key=lambda x:x[0])
    return by_player,{'games_compressed_bytes':gbytes,'appearances_compressed_bytes':abytes,'games_rows':game_rows,'national_team_games':len(intl_games),'appearance_rows':app_rows,'international_appearance_rows':intl_app_rows,'minutes_positive_rows':minutes_positive,'appearance_columns':cols}
def recent_load(hist,target,max_gap):
    count=0;mins=0.0
    for d,m,_ in reversed(hist):
        gap=(target-d).days
        if gap<=0:continue
        if gap>max_gap:break
        count+=1;mins+=m
    return count,mins
def build_rows(intl):
    matches=fixed.base._load_matches();lineups={cid:fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS};team_hist=defaultdict(list);out=[]
    for r in matches:
        cid=r['competition_id'];season=r['season'];ds=str(r['date'])[:10];target=date.fromisoformat(ds);hk=(cid,season,r['home']);ak=(cid,season,r['away']);hp=fixed.base._predicted_xi(team_hist[hk]);ap=fixed.base._predicted_xi(team_hist[ak])
        if hp is not None and ap is not None:
            vals={}
            for side,xi in (('home',hp[0]),('away',ap[0])):
                for w in (5,10):
                    players=apps=0;mins=0.0
                    for raw in xi:
                        c,m=recent_load(intl.get(pid(raw),[]),target,w)
                        if c:players+=1;apps+=c;mins+=m
                    vals[f'{side}_players_{w}']=players;vals[f'{side}_apps_{w}']=apps;vals[f'{side}_minutes_{w}']=mins
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            for w in (5,10):
                fp=vals[f'home_players_{w}'] if fav=='home' else vals[f'away_players_{w}'] if fav=='away' else 0;dp=vals[f'away_players_{w}'] if fav=='home' else vals[f'home_players_{w}'] if fav=='away' else 0
                vals[f'fav_players_{w}']=fp;vals[f'dog_players_{w}']=dp;vals[f'player_diff_{w}']=fp-dp
            out.append({**r,'date':ds,'fav':fav,**vals})
        hi=lineups[cid].get((season,ds,r['home']));ai=lineups[cid].get((season,ds,r['away']))
        if hi:team_hist[hk].append((ds,tuple(hi['starters'])))
        if ai:team_hist[ak].append((ds,tuple(ai['starters'])))
    return out
def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def metrics(rows):
    return {'all_affected10':stat(rows),'any_1_5d':stat(rows,lambda r:r['home_players_5'] or r['away_players_5']),'favorite_3plus_5d':stat(rows,lambda r:r['fav_players_5']>=3),'favorite_more_by2_5d':stat(rows,lambda r:r['player_diff_5']>=2),'favorite_3plus_10d':stat(rows,lambda r:r['fav_players_10']>=3),'favorite_more_by2_10d':stat(rows,lambda r:r['player_diff_10']>=2),'p_ge_0.58':stat(rows,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav3_5d':stat(rows,lambda r:max(r['opening'])>=0.58 and r['fav_players_5']<3),'p_ge_0.58_exclude_fav3_10d':stat(rows,lambda r:max(r['opening'])>=0.58 and r['fav_players_10']<3),'p_ge_0.60':stat(rows,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav3_5d':stat(rows,lambda r:max(r['opening'])>=0.60 and r['fav_players_5']<3)}
def main():
    intl,source=load_international_appearances();rows=build_rows(intl);scope=[r for r in rows if r['season'] in {'2024/25','2025/26'}];affected=[r for r in scope if r['home_players_10'] or r['away_players_10']]
    if len(affected)<100:raise RuntimeError(f'insufficient two-season 1-10d affected matches: {len(affected)}')
    test=affected[-100:];byseason={s:metrics([r for r in test if r['season']==s]) for s in ('2024/25','2025/26')}
    payload={'schema_version':'V6.12.9c-international-duty-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_INTERNATIONAL_APPEARANCES','governance':{'expected_xi_same_season_prior_only':True,'exposure_pool_1_to_10_days_prior':True,'high_load_1_to_5_days_retained':True,'target_actual_xi_excluded':True,'exposure_selection_outcome_independent':True,'test_matches':100,'cross_season_pool':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'two_season_feature_rows':len(scope),'two_season_affected_1_10d':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date'],'test_by_season':{s:sum(r['season']==s for r in test) for s in ('2024/25','2025/26')}},'test':metrics(test),'by_season':byseason}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'test':payload['test'],'by_season':byseason},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
