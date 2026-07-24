#!/usr/bin/env python3
"""V6.12.9 research-only Fast100: recent international-duty load in expected XI.

For each Big-5 club match, expected XI is built only from same-season earlier club
lineups. A player is internationally loaded only if the public appearances table records
an appearance in a national_team_competition strictly 1-5 calendar days before the club
match. Target actual XI is never used as a feature.

Target sample is the latest 100 2025/26 club matches with at least one expected-XI
player carrying such international duty. Exposure selection does not use target outcome.
"""
from __future__ import annotations
import bisect,csv,gzip,io,json,urllib.request
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as fixed

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_international_duty_fast100_v6129_status.json'
GAMES='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz'
APPS='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/appearances.csv.gz'

def fetch(url,timeout=180):
    req=urllib.request.Request(url,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:raw=r.read()
    return gzip.decompress(raw).decode('utf-8-sig',errors='replace'),len(raw)

def pid(x):
    s=str(x or '').strip()
    if ':' in s:s=s.rsplit(':',1)[-1]
    return s

def load_international_appearances():
    gtext,gbytes=fetch(GAMES); grd=csv.DictReader(io.StringIO(gtext)); intl_games={}; game_rows=0
    for r in grd:
        game_rows+=1
        if str(r.get('competition_type') or '').strip()!='national_team_competition':continue
        d=str(r.get('date') or '')[:10];gid=str(r.get('game_id') or '')
        if gid and d:
            try:intl_games[gid]=date.fromisoformat(d)
            except:pass
    atext,abytes=fetch(APPS,240); ard=csv.DictReader(io.StringIO(atext)); by_player=defaultdict(list); app_rows=0;intl_app_rows=0;minutes_available=0
    cols=list(ard.fieldnames or [])
    for r in ard:
        app_rows+=1;gid=str(r.get('game_id') or '')
        d=intl_games.get(gid)
        if d is None:continue
        p=pid(r.get('player_id'))
        if not p:continue
        intl_app_rows+=1
        try:m=float(r.get('minutes_played') or 0.0);minutes_available+=int(m>0)
        except:m=0.0
        by_player[p].append((d,m,gid))
    for p in by_player:by_player[p].sort(key=lambda x:x[0])
    return by_player,{'games_compressed_bytes':gbytes,'appearances_compressed_bytes':abytes,'games_rows':game_rows,'national_team_games':len(intl_games),'appearance_rows':app_rows,'international_appearance_rows':intl_app_rows,'minutes_positive_rows':minutes_available,'appearance_columns':cols}

def recent_load(hist,target):
    # Hist is small; 1-5 days strictly before target.
    count=0;mins=0.0;latest_gap=None
    for d,m,_ in reversed(hist):
        gap=(target-d).days
        if gap<=0:continue
        if gap>5:break
        count+=1;mins+=m;latest_gap=gap if latest_gap is None else min(latest_gap,gap)
    return count,mins,latest_gap

def build_rows(intl):
    matches=fixed.base._load_matches(); lineups={cid:fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS}
    team_hist=defaultdict(list);out=[]
    # matches already normalized/date-only by fixed wrapper
    for r in matches:
        cid=r['competition_id'];season=r['season'];ds=str(r['date'])[:10];target=date.fromisoformat(ds)
        hk=(cid,season,r['home']);ak=(cid,season,r['away'])
        hp=fixed.base._predicted_xi(team_hist[hk]);ap=fixed.base._predicted_xi(team_hist[ak])
        if hp is not None and ap is not None:
            hxi=hp[0];axi=ap[0]
            hc=hm=ac=am=0.0; hplayers=aplayers=0; hmin_gap=amin_gap=None
            for raw in hxi:
                c,m,g=recent_load(intl.get(pid(raw),[]),target)
                if c:hplayers+=1;hc+=c;hm+=m;hmin_gap=g if hmin_gap is None else min(hmin_gap,g)
            for raw in axi:
                c,m,g=recent_load(intl.get(pid(raw),[]),target)
                if c:aplayers+=1;ac+=c;am+=m;amin_gap=g if amin_gap is None else min(amin_gap,g)
            p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            fav_players=hplayers if fav=='home' else aplayers if fav=='away' else 0
            dog_players=aplayers if fav=='home' else hplayers if fav=='away' else 0
            fav_minutes=hm if fav=='home' else am if fav=='away' else 0.0
            dog_minutes=am if fav=='home' else hm if fav=='away' else 0.0
            out.append({**r,'date':ds,'fav':fav,'home_intl_players':hplayers,'away_intl_players':aplayers,'home_intl_minutes':hm,'away_intl_minutes':am,'fav_intl_players':fav_players,'dog_intl_players':dog_players,'fav_intl_minutes':fav_minutes,'dog_intl_minutes':dog_minutes,'intl_player_diff':fav_players-dog_players,'intl_minute_diff':fav_minutes-dog_minutes})
        hi=lineups[cid].get((season,ds,r['home']));ai=lineups[cid].get((season,ds,r['away']))
        if hi:team_hist[hk].append((ds,tuple(hi['starters'])))
        if ai:team_hist[ak].append((ds,tuple(ai['starters'])))
    return out

def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def main():
    intl,source=load_international_appearances();rows=build_rows(intl);latest=[r for r in rows if r['season']=='2025/26'];affected=[r for r in latest if r['home_intl_players'] or r['away_intl_players']]
    if len(affected)<100:raise RuntimeError(f'insufficient international-duty affected matches: {len(affected)}')
    test=affected[-100:]
    payload={'schema_version':'V6.12.9-international-duty-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_STRICTLY_PRIOR_INTERNATIONAL_APPEARANCES','governance':{'expected_xi_same_season_prior_only':True,'international_appearance_1_to_5_days_prior_only':True,'target_actual_xi_excluded':True,'exposure_selection_outcome_independent':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':source,'sample':{'feature_rows_2025_26':len(latest),'affected_2025_26':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':{'all_affected':stat(test),'favorite_more_intl_players':stat(test,lambda r:r['intl_player_diff']>=2),'favorite_3plus_intl_players':stat(test,lambda r:r['fav_intl_players']>=3),'underdog_3plus_intl_players':stat(test,lambda r:r['dog_intl_players']>=3),'p_ge_0.58':stat(test,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav_3plus':stat(test,lambda r:max(r['opening'])>=0.58 and r['fav_intl_players']<3),'p_ge_0.58_exclude_fav_diff2':stat(test,lambda r:max(r['opening'])>=0.58 and r['intl_player_diff']<2),'p_ge_0.60':stat(test,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav_3plus':stat(test,lambda r:max(r['opening'])>=0.60 and r['fav_intl_players']<3)}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['test'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
