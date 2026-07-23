#!/usr/bin/env python3
"""V6.6.16 ingest current managers for deficient-roster Bundesliga teams from Bundesliga.com.

Bundesliga.com explicitly labels 2026/27 squad pages as "Not complete" during the summer window.
Therefore this source is used ONLY for the explicit Head coach field and never for strict roster
eligibility. One official Bundesliga source satisfies the existing V6.6.3 manager evidence gate.

Each manager record's observed_at_utc is bound to the actual HTTP source observation for that team,
not to a batch-start timestamp. This guarantees source_observed_at_utc <= record observed_at_utc.
Research context only; no formal probability or weight changes.
"""
from __future__ import annotations
import html,json,re,time,urllib.request
from datetime import datetime,timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'evidence'/'team_manager_context_weekly'
STATUS=ROOT/'manifests'/'v6_bundesliga_official_manager_v6614_status.json'
BASE='https://www.bundesliga.com/en/bundesliga/clubs/{}'
UA='football-v6.6.16-bundesliga-manager/1.0'
TEAMS={
 'RB Leipzig':'rb-leipzig','VfB Stuttgart':'vfb-stuttgart','TSG Hoffenheim':'tsg-hoffenheim','SC Freiburg':'sport-club-freiburg',
 'Eintracht Frankfurt':'eintracht-frankfurt','FC Augsburg':'fc-augsburg','Mainz':'1-fsv-mainz-05','1. FC Union Berlin':'1-fc-union-berlin',
 'Borussia Mönchengladbach':'borussia-moenchengladbach','FC Cologne':'1-fc-koeln','Werder Bremen':'sv-werder-bremen',
 'Schalke 04':'fc-schalke-04','SV Elversberg':'sv-elversberg','SC Paderborn 07':'sc-paderborn-07'}
class TextCollector(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
 def handle_data(self,data):
  v=re.sub(r'\s+',' ',html.unescape(data)).strip()
  if v:self.parts.append(v)
def fetch(url):
 req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml'})
 with urllib.request.urlopen(req,timeout=30) as r:raw=r.read();charset=r.headers.get_content_charset() or 'utf-8'
 try:text=raw.decode(charset,'strict')
 except Exception:text=raw.decode('utf-8','replace')
 return text,datetime.now(timezone.utc).replace(microsecond=0)
def tokens(markup):p=TextCollector();p.feed(markup);p.close();return p.parts
def parse_manager(markup):
 t=tokens(markup);candidates=[]
 for i,v in enumerate(t):
  if v.strip().casefold()=='head coach':
   for nxt in t[i+1:i+6]:
    n=nxt.strip()
    if n and n.casefold() not in {'stats','news','squad','head coach','advertisement'} and len(n)<=80:candidates.append(n);break
 uniq=[]
 for x in candidates:
  if x not in uniq:uniq.append(x)
 return uniq,t
def prior_managers():
 latest={}
 if not OUTDIR.exists():return {}
 for p in OUTDIR.glob('*.json'):
  try:x=json.loads(p.read_text(encoding='utf-8'))
  except Exception:continue
  rows=x.get('records') if isinstance(x,dict) else None;rows=rows if isinstance(rows,list) else [x] if isinstance(x,dict) else []
  for r in rows:
   if not isinstance(r,dict) or r.get('competition_id')!='GER_Bundesliga':continue
   team=str(r.get('team_name') or '');coach=(r.get('head_coach') or {}).get('name') if isinstance(r.get('head_coach'),dict) else None
   if team not in TEAMS or not coach:continue
   try:stamp=datetime.fromisoformat(str(r.get('observed_at_utc') or '').replace('Z','+00:00'))
   except Exception:continue
   if team not in latest or stamp>latest[team][0]:latest[team]=(stamp,str(coach))
 return {k:v[1] for k,v in latest.items()}
def norm(s):return re.sub(r'\s+',' ',str(s)).strip().casefold()
def main():
 batch_started=datetime.now(timezone.utc).replace(microsecond=0);prior=prior_managers();records=[];audit=[];source_times=[]
 for team,slug in TEAMS.items():
  url=BASE.format(slug);row={'team_name':team,'source_url':url,'status':'FAIL_CLOSED'}
  try:
   markup,source_ts=fetch(url);source_times.append(source_ts);managers,t=parse_manager(markup);incomplete=any(v.strip().casefold()=='not complete' for v in t);row.update({'manager_candidates':managers,'page_explicitly_squad_not_complete':incomplete,'source_observed_at_utc':source_ts.isoformat()})
   if len(managers)==1:
    coach=managers[0];prev=prior.get(team)
    if prev is None:change={'status':'BASELINE_ESTABLISHED','previous_manager':None,'changed_at_utc':None,'note':'First verified official Bundesliga manager baseline in this evidence stream.'}
    elif norm(prev)==norm(coach):change={'status':'UNCHANGED','previous_manager':prev,'changed_at_utc':None,'note':'Current official Bundesliga Head coach matches previous verified record.'}
    else:change={'status':'CHANGED_CONFIRMED','previous_manager':prev,'changed_at_utc':None,'note':'Current official Bundesliga Head coach differs from previous verified record; exact appointment time is not inferred.'}
    records.append({'schema_version':'V6.6.3-team-manager-context-r1','competition_id':'GER_Bundesliga','team_name':team,'observed_at_utc':source_ts.isoformat(),'head_coach':{'name':coach},'manager_change':change,'sources':[{'source_name':'Bundesliga official club page','source_url':url,'source_tier':'tier_1_official','provider_group':'bundesliga_official','source_observed_at_utc':source_ts.isoformat(),'source_role':'current_head_coach'}],'source_metadata':{'season':'2026-2027','page_explicitly_squad_not_complete':incomplete},'governance':{'pit_current':True,'record_time_bound_to_source_observation':True,'manager_only_evidence':True,'strict_roster_eligible_from_this_source':False,'incomplete_squad_never_promoted':True,'research_context_only':True,'formal_probability_use':False}});row['status']='PASS_MANAGER_ONLY';row['manager']=coach
   else:row['error']='manager_not_uniquely_parsed'
  except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
  audit.append(row);time.sleep(0.10)
 completed=max(source_times) if source_times else datetime.now(timezone.utc).replace(microsecond=0);OUTDIR.mkdir(parents=True,exist_ok=True);STATUS.parent.mkdir(parents=True,exist_ok=True);stamp=completed.strftime('%Y%m%dT%H%M%SZ');path=OUTDIR/f'bundesliga_managers__{stamp}.json'
 if records:path.write_text(json.dumps({'schema_version':'V6.6.16-bundesliga-manager-weekly-aggregate-r2','observed_at_utc':completed.isoformat(),'records':records,'governance':{'official_manager_only':True,'record_times_bound_to_per-team_source_observation':True,'incomplete_squad_not_used_for_roster':True,'formal_probability_use':False}},ensure_ascii=False,indent=2),encoding='utf-8')
 payload={'schema_version':'V6.6.16-bundesliga-manager-status-r2','generated_at_utc':completed.isoformat(),'batch_started_at_utc':batch_started.isoformat(),'status':'PASS_COMPLETE' if len(records)==len(TEAMS) else 'WARN_PARTIAL' if records else 'FAIL_NO_VALID_MANAGER','target_count':len(TEAMS),'valid_manager_count':len(records),'evidence_path':str(path.relative_to(ROOT)) if records else None,'audit':audit,'governance':{'official_source_only':True,'record_time_bound_to_source_observation':True,'manager_only':True,'bundesliga_squad_not_complete_is_hard_exclusion_from_strict_roster':True,'research_context_only':True,'formal_probability_change':False,'formal_weight_change':False}}
 STATUS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if records else 2
if __name__=='__main__':raise SystemExit(main())