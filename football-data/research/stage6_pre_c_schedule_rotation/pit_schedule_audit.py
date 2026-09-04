#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_DEV_N = 5478
SEASONS = (2020, 2021, 2022)
MONTH = {m:i+1 for i,m in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'))}
LEAGUE_COUNTRY = {'EPL':'england','Bundesliga':'germany','Serie A':'italy','La liga':'spain','Ligue 1':'france'}
DOMESTIC = {
    'england': ('england', ('{season}/1-premierleague.txt','{season}/facup.txt','{season}/eflcup.txt')),
    'germany': ('deutschland', ('{season}/1-bundesliga.txt','{season}/cup.txt')),
    'italy': ('italy', ('{season}/1-seriea.txt','{season}/cup.txt')),
    'spain': ('espana', ('{season}/1-liga.txt','{season}/cup.txt')),
}
EURO = {2020: ('cl.txt','el.txt'), 2021: ('cl.txt','el.txt','conf.txt'), 2022: ('cl.txt','el.txt','conf.txt')}
ALIASES = {
    'internazionale':'inter','internazionale milano':'inter','inter milan':'inter',
    'ac milan':'milan','milan ac':'milan',
    'bayern munchen':'bayern munich','fc bayern munchen':'bayern munich',
    'borussia monchengladbach':'borussia m gladbach','borussia mgladbach':'borussia m gladbach',
    'paris saint germain':'psg','paris sg':'psg',
    'athletic bilbao':'athletic club','athletic de bilbao':'athletic club',
    'real sociedad de futbol':'real sociedad',
    'deportivo alaves':'alaves','rcd espanyol':'espanyol',
    '1 fc koln':'koln','fc koln':'koln','koln':'koln',
    'rb leipzig':'rasenballsport leipzig',
    'hertha bsc':'hertha berlin','hertha bsc berlin':'hertha berlin',
    'olympique de marseille':'marseille','olympique marseille':'marseille',
    'olympique lyonnais':'lyon','as saint etienne':'saint etienne',
}

class AuditError(RuntimeError): pass

def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def season_token(y: int) -> str:
    return f'{y}-{str(y+1)[-2:]}'

def norm(name: str) -> str:
    s = unicodedata.normalize('NFKD', str(name)).encode('ascii','ignore').decode().lower()
    s = s.replace('&',' and ')
    s = re.sub(r"[^a-z0-9]+", ' ', s).strip()
    # Only remove generic club-form tokens at boundaries; do not remove city/saint/real/etc.
    toks = [t for t in s.split() if t not in {'fc','cf','ssc','afc'}]
    s = ' '.join(toks)
    return ALIASES.get(s, s)

def parse_snapshot(text: str, start_year: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    current: date | None = None
    for raw in text.splitlines():
        line = raw.split('#',1)[0].strip()
        if not line: continue
        dm = re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/(\d{1,2})\b', line)
        if dm and (line.startswith('[') or not re.search(r'\d+\s*-\s*\d+', line)):
            mo = MONTH[dm.group(1)]; yr = start_year if mo >= 7 else start_year + 1
            try: current = date(yr, mo, int(dm.group(2)))
            except ValueError: current = None
            if line.startswith('['): continue
        if current is None: continue
        x = re.sub(r'^\d{1,2}[.:]\d{2}\s+', '', line).strip()
        # Finished match: HOME 2-1 (1-0) AWAY. Score is used only to split names, never semantically.
        m = re.match(r'^(.+?)\s+(\d+)\s*-\s*(\d+)(?:\s+\([^)]*\))?\s+(.+?)$', x)
        if m:
            home, away = m.group(1).strip(), m.group(4).strip()
        else:
            # Unplayed fixture: HOME  -  AWAY (requires whitespace around hyphen).
            m = re.match(r'^(.+?)\s{2,}-\s{2,}(.+?)$', x)
            if not m: continue
            home, away = m.group(1).strip(), m.group(2).strip()
        home = re.sub(r'\s+\[[^]]+\]\s*$', '', home).strip()
        away = re.sub(r'\s+\[[^]]+\]\s*$', '', away).strip()
        if home and away:
            out.append({'date': current.isoformat(), 'home': home, 'away': away, 'home_key': norm(home), 'away_key': norm(away)})
    return out

def git(repo: Path, *args: str, check: bool=True) -> str:
    p = subprocess.run(['git','-C',str(repo),*args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0: raise AuditError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()

def path_timeline(repo: Path, path: str) -> list[tuple[datetime,str]]:
    raw = git(repo, 'log','--format=%cI%x09%H','--reverse','--',path, check=False)
    out=[]
    for line in raw.splitlines():
        if '\t' not in line: continue
        ts, sha = line.split('\t',1)
        try: dt=datetime.fromisoformat(ts.replace('Z','+00:00'))
        except ValueError: continue
        out.append((dt.astimezone(timezone.utc),sha))
    return out

def commit_before(timeline: list[tuple[datetime,str]], cutoff: datetime) -> tuple[datetime,str] | None:
    best=None
    for item in timeline:
        if item[0] < cutoff: best=item
        else: break
    return best

def show_path(repo: Path, sha: str, path: str) -> str | None:
    p=subprocess.run(['git','-C',str(repo),'show',f'{sha}:{path}'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout if p.returncode == 0 else None

def load_targets(db: Path) -> list[dict[str, Any]]:
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    q="""select fid,date,league,season,team_h,team_a from general_game_stats
         where league in ('EPL','Bundesliga','Serie A','La liga','Ligue 1') and season between 2020 and 2022
         order by date,fid"""
    rows=[dict(r) for r in con.execute(q)]; con.close()
    if len(rows)!=EXPECTED_DEV_N: raise AuditError(f'target identity count drift {len(rows)}')
    return rows

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--repos',required=True); ap.add_argument('--out',required=True); ap.add_argument('--min-coverage',type=int,default=1500)
    args=ap.parse_args(); out=Path(args.out); out.mkdir(parents=True,exist_ok=True); repos=Path(args.repos)
    targets=load_targets(Path(args.db))
    repo_dirs={'england':repos/'england','deutschland':repos/'deutschland','italy':repos/'italy','espana':repos/'espana','champions-league':repos/'champions-league'}
    for k,p in repo_dirs.items():
        if not (p/'.git').exists(): raise AuditError(f'missing repo {k}: {p}')

    # Prebuild path histories; France is deliberately unsupported because the auditable source set lacks Coupe de France.
    path_meta: dict[tuple[str,str], list[tuple[datetime,str]]] = {}
    paths_by_season: dict[int,list[tuple[str,str,str]]] = defaultdict(list)
    for y in SEASONS:
        tok=season_token(y)
        for country,(repo_name,patterns) in DOMESTIC.items():
            for pat in patterns:
                path=pat.format(season=tok); paths_by_season[y].append((country,repo_name,path)); path_meta[(repo_name,path)]=path_timeline(repo_dirs[repo_name],path)
        for fn in EURO[y]:
            path=f'{tok}/{fn}'; paths_by_season[y].append(('europe','champions-league',path)); path_meta[('champions-league',path)]=path_timeline(repo_dirs['champions-league'],path)

    cache: dict[tuple[str,str,str], dict[str,Any]] = {}
    def snapshot(repo_name: str,path: str,cutoff: datetime,start_year:int) -> dict[str,Any] | None:
        cb=commit_before(path_meta[(repo_name,path)],cutoff)
        if cb is None:return None
        ts,sha=cb; ck=(repo_name,path,sha)
        if ck not in cache:
            text=show_path(repo_dirs[repo_name],sha,path)
            if text is None:return None
            matches=parse_snapshot(text,start_year)
            cache[ck]={'published_at':ts.isoformat(),'commit':sha,'matches':matches,'max_date':max((m['date'] for m in matches),default=None)}
        return cache[ck]

    coverage=[]; missing=Counter(); per=Counter(); unmatched=Counter(); examples=[]
    for r in targets:
        y=int(r['season']); country=LEAGUE_COUNTRY[str(r['league'])]; fid=int(r['fid'])
        if country=='france':
            missing['france_cup_source_unavailable']+=1; per[(y,country,'total')]+=1; continue
        cutoff=datetime.fromisoformat(str(r['date']).replace('Z','+00:00'))
        if cutoff.tzinfo is None: cutoff=cutoff.replace(tzinfo=timezone.utc)
        cutoff=cutoff.astimezone(timezone.utc)
        hk,ak=norm(r['team_h']),norm(r['team_a']); target_day=cutoff.date()
        specs=[x for x in paths_by_season[y] if x[0] in (country,'europe')]
        snaps=[]; absent=False
        for _,repo_name,path in specs:
            s=snapshot(repo_name,path,cutoff,y)
            if s is None:
                missing['source_snapshot_absent']+=1; absent=True; break
            snaps.append((repo_name,path,s))
        per[(y,country,'total')]+=1
        if absent: continue
        all_matches=[(repo_name,path,m,s) for repo_name,path,s in snaps for m in s['matches']]
        def next_for(key:str):
            cand=[]
            for repo_name,path,m,s in all_matches:
                md=date.fromisoformat(m['date'])
                if md>target_day and key in (m['home_key'],m['away_key']): cand.append((md,repo_name,path,m,s))
            return min(cand,key=lambda z:z[0]) if cand else None
        nh,na=next_for(hk),next_for(ak)
        if nh is None: unmatched[f'{country}:home:{hk}']+=1
        if na is None: unmatched[f'{country}:away:{ak}']+=1
        if nh is None or na is None:
            missing['team_next_fixture_not_published']+=1; continue
        # Completeness: every relevant competition snapshot must publish through the team's candidate next date.
        def complete(cand) -> bool:
            nd=cand[0]
            for _,_,s in snaps:
                if s['max_date'] is None or date.fromisoformat(s['max_date']) < nd: return False
            return True
        if not complete(nh) or not complete(na):
            missing['competition_horizon_shorter_than_candidate']+=1; continue
        rec={'fixture_id':f'understat:{fid}','season':y,'league':r['league'],'target_kickoff':cutoff.isoformat(),
             'home_team':r['team_h'],'away_team':r['team_a'],
             'home_next_date':nh[0].isoformat(),'away_next_date':na[0].isoformat(),
             'home_next_source':{'repo':nh[1],'path':nh[2],'commit':nh[4]['commit'],'source_published_at':nh[4]['published_at']},
             'away_next_source':{'repo':na[1],'path':na[2],'commit':na[4]['commit'],'source_published_at':na[4]['published_at']},
             'relevant_source_count':len(snaps),'pit_complete':True}
        coverage.append(rec); per[(y,country,'covered')]+=1
        if len(examples)<25: examples.append(rec)

    n=len(coverage); status='STAGE6_PRE_C_PIT_COVERAGE_PASS_READY_FOR_CONSUMED_DEV_SCORING' if n>=args.min_coverage else 'STAGE6_PRE_C_STOP_PIT_SCHEDULE_COVERAGE'
    summary={
      'schema_version':'football3-stage6-pre-c-pit-audit-v1','status':status,'research_only':True,'zero_label_only':True,
      'target_n':len(targets),'pit_complete_n':n,'pit_complete_fraction':n/len(targets),'minimum_required_n':args.min_coverage,
      '2023_opened':False,'result_or_goal_fields_read':False,'final_schedule_backfill_used':False,'final_lineup_or_minutes_used':False,
      'france_policy':'MISSING: no historically versioned Coupe de France file in the frozen public source registry; no inference of no-cup match',
      'missing_reasons':dict(missing),'per_season_country':{f'{y}:{c}':{'total':per[(y,c,'total')],'covered':per[(y,c,'covered')]} for y in SEASONS for c in ('england','germany','italy','spain','france')},
      'unmatched_top':unmatched.most_common(40),'snapshot_cache_entries':len(cache),'examples':examples,
      'governance':{'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_weights_changed':False,'promotion_allowed':False}
    }
    write_json(out/'pit_coverage.json',summary)
    write_json(out/'pit_covered_fixture_ids.json',{'fixture_ids':[r['fixture_id'] for r in coverage]})
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
