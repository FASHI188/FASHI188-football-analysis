#!/usr/bin/env python3
"""V6.12.7 research-only Fast100 screen: recent sending-off shock.

Target sample: latest 100 Big-5 2025/26 league matches where at least one team had a
straight-red or yellow-red dismissal in its previous three league matches. This is an
outcome-independent exposure definition. Event features use only earlier games.

We deliberately do NOT label the dismissed player as suspended: competition-specific
ban serving rules can vary. This tests only whether recent dismissal disruption is a
useful market-risk flag.
"""
from __future__ import annotations
import csv,gzip,io,json,urllib.request
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import validate_1x2_pit_lineup_increment_v6117c as joins
from platform_core import normalize_team_token

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_1x2_recent_red_card_fast100_v6127_status.json'
GAMES='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz'
EVENTS='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/game_events.csv.gz'
INV={'GB1':'ENG_PremierLeague','L1':'GER_Bundesliga','IT1':'ITA_SerieA','FR1':'FRA_Ligue1','ES1':'ESP_LaLiga'}

def download(url):
    req=urllib.request.Request(url,headers={'User-Agent':'football-analysis-research/1.0'})
    with urllib.request.urlopen(req,timeout=180) as r:raw=r.read()
    return gzip.decompress(raw).decode('utf-8-sig',errors='replace'),len(raw)

def is_dismissal(desc):
    s=str(desc or '').casefold().replace('–','-').replace('—','-')
    return ('red card' in s) or ('yellow-red' in s) or ('yellow red' in s) or ('yellow/red' in s)

def load_tm():
    text,gbytes=download(GAMES); rd=csv.DictReader(io.StringIO(text)); games=[]; gidmeta={}
    for r in rd:
        comp=str(r.get('competition_id') or '')
        if comp not in INV:continue
        try:sy=int(str(r.get('season') or ''))
        except:continue
        season=f'{sy}/{(sy+1)%100:02d}'
        if season not in {'2021/22','2022/23','2023/24','2024/25','2025/26'}:continue
        date=str(r.get('date') or '')[:10]; gid=str(r.get('game_id') or '')
        h=str(r.get('home_club_name') or '');a=str(r.get('away_club_name') or '')
        hc=str(r.get('home_club_id') or '');ac=str(r.get('away_club_id') or '')
        if not gid or not date or not h or not a:continue
        rec={'game_id':gid,'competition_id':INV[comp],'season':season,'date':date,'home_tm':normalize_team_token(h),'away_tm':normalize_team_token(a),'home_club_id':hc,'away_club_id':ac}
        games.append(rec);gidmeta[gid]=rec
    etext,ebytes=download(EVENTS); erd=csv.DictReader(io.StringIO(etext)); reds=defaultdict(int); card_rows=red_rows=0; red_desc=defaultdict(int)
    for r in erd:
        if str(r.get('type') or '')!='Cards':continue
        card_rows+=1;gid=str(r.get('game_id') or '')
        if gid not in gidmeta:continue
        desc=str(r.get('description') or '')
        if is_dismissal(desc):
            red_rows+=1;club=str(r.get('club_id') or '');reds[(gid,club)]+=1;red_desc[desc]+=1
    return games,reds,{'games_compressed_bytes':gbytes,'events_compressed_bytes':ebytes,'card_rows_scanned':card_rows,'dismissal_rows_in_scope':red_rows,'dismissal_descriptions_top':sorted(red_desc.items(),key=lambda kv:(-kv[1],kv[0]))[:20]}

def market():
    rows=joins.base._load_matches();out=[]
    for r in rows:
        x=dict(r);x['date']=str(x['date'])[:10];x['home']=normalize_team_token(x['home']);x['away']=normalize_team_token(x['away']);out.append(x)
    return out

def join_market(mm,tg):
    msets=defaultdict(set);tsets=defaultdict(set)
    for r in mm:msets[(r['competition_id'],r['season'])].update((r['home'],r['away']))
    for r in tg:tsets[(r['competition_id'],r['season'])].update((r['home_tm'],r['away_tm']))
    maps={k:joins._greedy_bijection(v,tsets.get(k,set()))[0] for k,v in msets.items()}
    idx={}
    for r in tg:
        inv={v:k for k,v in maps.get((r['competition_id'],r['season']),{}).items()};h=inv.get(r['home_tm']);a=inv.get(r['away_tm'])
        if h is not None and a is not None:idx[(r['competition_id'],r['season'],r['date'],h,a)]=r
    out=[]
    for r in mm:
        t=idx.get((r['competition_id'],r['season'],r['date'],r['home'],r['away']))
        if t:out.append({**r,**{'game_id':t['game_id'],'home_club_id':t['home_club_id'],'away_club_id':t['away_club_id']}})
    out.sort(key=lambda r:(r['date'],r['competition_id'],r['home'],r['away']));return out

def enrich(rows,reds):
    hist=defaultdict(lambda:deque(maxlen=3));out=[]
    i=0
    while i<len(rows):
        date=rows[i]['date'];j=i
        while j<len(rows) and rows[j]['date']==date:j+=1
        group=rows[i:j]
        for r in group:
            hk=(r['competition_id'],r['home']);ak=(r['competition_id'],r['away'])
            hr=list(hist[hk]);ar=list(hist[ak]);h1=hr[-1] if hr else 0;a1=ar[-1] if ar else 0
            h3=sum(hr);a3=sum(ar);p=r['opening'];fav=('home','draw','away')[int(np.argmax(np.asarray(p)))]
            out.append({**r,'home_red_prev1':h1,'away_red_prev1':a1,'home_red_prev3':h3,'away_red_prev3':a3,'fav':fav,'fav_red_prev1':h1 if fav=='home' else a1 if fav=='away' else 0,'fav_red_prev3':h3 if fav=='home' else a3 if fav=='away' else 0,'dog_red_prev3':a3 if fav=='home' else h3 if fav=='away' else 0})
        for r in group:
            hk=(r['competition_id'],r['home']);ak=(r['competition_id'],r['away'])
            hist[hk].append(1 if reds.get((r['game_id'],r['home_club_id']),0)>0 else 0);hist[ak].append(1 if reds.get((r['game_id'],r['away_club_id']),0)>0 else 0)
        i=j
    return out

def correct(r):return r['fav']==r['actual']
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(correct(r) for r in s);return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None}
def main():
    tg,reds,src=load_tm();rows=enrich(join_market(market(),tg),reds);latest=[r for r in rows if r['season']=='2025/26'];affected=[r for r in latest if r['home_red_prev3'] or r['away_red_prev3']]
    if len(affected)<100:raise RuntimeError(f'insufficient affected matches: {len(affected)}')
    test=affected[-100:]
    payload={'schema_version':'V6.12.7-recent-red-card-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_EVENT_KNOWN_STRICTLY_BEFORE_TARGET','governance':{'exposure_defined_without_target_outcome':True,'same_day_batch_update':True,'dismissal_not_assumed_to_equal_suspension':True,'test_matches':100,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'source':src,'sample':{'joined_2025_26':len(latest),'affected_2025_26':len(affected),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':{'all_affected':stat(test),'favorite_recent_red_1':stat(test,lambda r:r['fav_red_prev1']>0),'favorite_recent_red_3':stat(test,lambda r:r['fav_red_prev3']>0),'underdog_recent_red_3':stat(test,lambda r:r['dog_red_prev3']>0),'p_ge_0.58':stat(test,lambda r:max(r['opening'])>=0.58),'p_ge_0.58_exclude_fav_recent_red3':stat(test,lambda r:max(r['opening'])>=0.58 and r['fav_red_prev3']==0),'p_ge_0.60':stat(test,lambda r:max(r['opening'])>=0.60),'p_ge_0.60_exclude_fav_recent_red3':stat(test,lambda r:max(r['opening'])>=0.60 and r['fav_red_prev3']==0)}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['test'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
