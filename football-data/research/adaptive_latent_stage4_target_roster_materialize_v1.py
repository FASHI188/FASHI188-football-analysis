#!/usr/bin/env python3
"""football3 Stage4 target-roster zero-label source/materialization guard."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

IDENTITY='8bfa3c98c1895085d94c1e6f1b5b7b80b5d56d6e1eefd615b137126604d45b11'
ORDERED='499e462f6cce50f05e575a0232c73c8d8b5234ad06c70551cf49b09353bbca76'
INDEX_SCHEMA='football3_adaptive_latent_stage4_target_roster_sources_v1'
SHARD_SCHEMA='football3_target_roster_source_shard_v1'
SNAP_SCHEMA='football3_target_roster_snapshot_source_v1'
EVENT_SCHEMA='football3_target_roster_event_source_v1'
POLICY={
 'ENG_PremierLeague':(('www.premierleague.com',),('/en/clubs/','/en/news/')),
 'ESP_LaLiga':(('www.laliga.com',),('/clubes/','/fichajes/','/noticias/')),
 'GER_Bundesliga':(('www.bundesliga.com',),('/en/bundesliga/player','/en/bundesliga/news/')),
 'ITA_SerieA':(('www.legaseriea.it',),('/team/','/serie-a/calciomercato','/serie-a/news/')),
 'FRA_Ligue1':(('ligue1.com','www.ligue1.com'),('/fr/articles/','/articles/','/clubs/')),
}
EVENT_TYPES=frozenset({'TRANSFER_IN','TRANSFER_OUT','LOAN_START','LOAN_END','CONTRACT_START','CONTRACT_END','REGISTRATION_EVENT'})
INDEX_KEYS=frozenset({'schema','project_id','status','source_verified_at_utc','identity_lock_sha256','ordered_identity_sha256','target_row_count','target_team_count','required_competitions','schedule_revision_conflicts','hard_boundaries'})
SHARD_KEYS=frozenset({'schema','competition_id','teams'})
TEAM_KEYS=frozenset({'competition_id','team_name','team_canonical_id','current_roster_source_url','current_complete_source_verified','event_source_urls'})
SNAP_KEYS=frozenset({'schema','competition_id','season_id','team_name','team_canonical_id','source_url','source_document_id','source_observed_at','completeness_scope','players','raw_source_payload_persisted','real_labels_read'})
PLAYER_KEYS=frozenset({'person_official_id','person_name'})
EVENT_KEYS=frozenset({'schema','competition_id','season_id','team_name','team_canonical_id','source_url','source_document_id','source_published_at','source_observed_at','provable_available_at','event_type','effective_at','person_official_id','person_name','raw_source_payload_persisted','real_labels_read'})

class MaterializeError(ValueError): pass

def _exact(x:Any, keys:frozenset[str], path:str)->dict[str,Any]:
 if type(x) is not dict or set(x)!=set(keys): raise MaterializeError(path+': exact-key contract failed')
 return x

def _s(x:Any,f:str)->str:
 if type(x) is not str or not x or x!=x.strip(): raise MaterializeError(f+': plain string required')
 return x

def _utc(x:Any,f:str)->datetime:
 s=_s(x,f)
 if not s.endswith('Z'): raise MaterializeError(f+': UTC Z required')
 try: d=datetime.fromisoformat(s[:-1]+'+00:00').astimezone(timezone.utc)
 except ValueError as e: raise MaterializeError(f+': invalid timestamp') from e
 if d.isoformat().replace('+00:00','Z')!=s: raise MaterializeError(f+': noncanonical timestamp')
 return d

def _zero(x:Any,f:str)->None:
 if type(x) is not int or x!=0: raise MaterializeError(f+': integer zero required')

def _url(x:Any,cid:str)->str:
 s=_s(x,'source_url'); p=urlsplit(s)
 if cid not in POLICY: raise MaterializeError('competition denied')
 hosts,prefixes=POLICY[cid]
 if p.scheme!='https' or p.hostname not in hosts or p.netloc!=p.hostname or p.username is not None or p.password is not None or p.query or p.fragment: raise MaterializeError('source_url authority/query denied')
 if not any(p.path.startswith(q) for q in prefixes): raise MaterializeError('source_url path denied')
 return s

def stable_person_id(cid:str,oid:str)->str:
 c=_s(cid,'competition_id'); o=_s(oid,'person_official_id')
 if c not in POLICY: raise MaterializeError('competition denied')
 b=json.dumps({'competition_id':c,'person_official_id':o},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
 return 'official-person:'+hashlib.sha256(b).hexdigest()

def validate_sources(index:Any, shards:Any, inventory:Any)->dict[str,Any]:
 i=_exact(index,INDEX_KEYS,'index')
 if i['schema']!=INDEX_SCHEMA or i['project_id']!='football3' or i['identity_lock_sha256']!=IDENTITY or i['ordered_identity_sha256']!=ORDERED or i['target_row_count']!=21 or i['target_team_count']!=38: raise MaterializeError('index identity drift')
 _utc(i['source_verified_at_utc'],'source_verified_at_utc')
 if i['required_competitions']!=list(POLICY) or type(shards) is not list or len(shards)!=5: raise MaterializeError('competition coverage drift')
 hb=i['hard_boundaries']
 if type(hb) is not dict or hb.get('real_labels_read')!=0 or hb.get('real_match_rows_read')!=0 or hb.get('training') is not False or hb.get('tuning') is not False or hb.get('real_scoring') is not False or hb.get('provider_api') is not False or hb.get('secret_access') is not False or hb.get('CURRENT_change') is not False or hb.get('formal_pit_eligible') is not False or hb.get('formal_weight')!=0.0 or hb.get('merge') is not False: raise MaterializeError('scientific isolation drift')
 if type(inventory) is not dict or inventory.get('target_row_count')!=21 or inventory.get('identity_lock_sha256')!=IDENTITY or inventory.get('ordered_identity_sha256')!=ORDERED or inventory.get('real_target_values_read')!=0 or inventory.get('formal_pit_eligible') is not False or inventory.get('formal_weight')!=0.0: raise MaterializeError('target inventory drift')
 teams={}; comps=set()
 for raw in shards:
  sh=_exact(raw,SHARD_KEYS,'shard')
  cid=_s(sh['competition_id'],'competition_id'); comps.add(cid)
  if sh['schema']!=SHARD_SCHEMA or cid not in POLICY or type(sh['teams']) is not list: raise MaterializeError('shard denied')
  for tr in sh['teams']:
   t=_exact(tr,TEAM_KEYS,'team')
   if t['competition_id']!=cid or type(t['current_complete_source_verified']) is not bool: raise MaterializeError('team contract drift')
   name=_s(t['team_name'],'team_name'); tid=_s(t['team_canonical_id'],'team_canonical_id')
   cur=t['current_roster_source_url']
   if cur is not None: _url(cur,cid)
   if t['current_complete_source_verified'] and cur is None: raise MaterializeError('complete current source missing')
   if type(t['event_source_urls']) is not list or not t['event_source_urls']: raise MaterializeError('event sources missing')
   for u in t['event_source_urls']: _url(u,cid)
   k=(cid,tid)
   if k in teams: raise MaterializeError('duplicate team')
   teams[k]=dict(t)
 if comps!=set(POLICY) or len(teams)!=38: raise MaterializeError('team coverage drift')
 target_team_ids=set()
 targets=inventory.get('targets')
 if type(targets) is not list or len(targets)!=21: raise MaterializeError('target list drift')
 fixture_ids=set()
 for t in targets:
  if type(t) is not dict or t.get('competition_id') not in POLICY: raise MaterializeError('target denied')
  cid=t['competition_id']; fixture_ids.add(t.get('fixture_id'))
  for side in ('home_team_id','away_team_id'):
   tid=t.get(side); target_team_ids.add((cid,tid))
   if (cid,tid) not in teams: raise MaterializeError('target team source missing')
  cutoff=_utc(t.get('prediction_cutoff'),'prediction_cutoff'); kick=_utc(t.get('kickoff_at'),'kickoff_at')
  if (kick-cutoff).total_seconds()!=900: raise MaterializeError('cutoff drift')
 if target_team_ids!=set(teams): raise MaterializeError('manifest/target team mismatch')
 conflicts=i['schedule_revision_conflicts']
 if type(conflicts) is not list or len(conflicts)!=1 or conflicts[0] not in fixture_ids: raise MaterializeError('schedule revision evidence drift')
 if sum(1 for t in teams.values() if t['current_complete_source_verified'])!=30: raise MaterializeError('complete-current count drift')
 return {'index':dict(i),'teams':teams,'targets':targets}

def snapshot_rows(raw:Any, sources:dict[str,Any])->list[dict[str,Any]]:
 d=_exact(raw,SNAP_KEYS,'snapshot'); _zero(d['real_labels_read'],'real_labels_read')
 if d['schema']!=SNAP_SCHEMA or d['raw_source_payload_persisted'] is not False: raise MaterializeError('snapshot isolation failed')
 cid=_s(d['competition_id'],'competition_id'); tid=_s(d['team_canonical_id'],'team_canonical_id'); team=sources['teams'].get((cid,tid))
 if team is None or d['team_name']!=team['team_name'] or not team['current_complete_source_verified']: raise MaterializeError('snapshot team/source denied')
 u=_url(d['source_url'],cid)
 if u!=team['current_roster_source_url']: raise MaterializeError('snapshot source mismatch')
 obs=_utc(d['source_observed_at'],'source_observed_at')
 if d['completeness_scope'] not in ('COMPLETE_ROSTER','PARTIAL_ROSTER') or type(d['players']) is not list or not d['players']: raise MaterializeError('snapshot completeness denied')
 seen=set(); out=[]
 for rawp in d['players']:
  p=_exact(rawp,PLAYER_KEYS,'player'); oid=_s(p['person_official_id'],'person_official_id')
  if oid in seen: raise MaterializeError('duplicate official person id')
  seen.add(oid); name=_s(p['person_name'],'person_name')
  out.append({'competition_id':cid,'team_canonical_id':tid,'person_official_id':oid,'person_name':name,'person_canonical_id':stable_person_id(cid,oid),'evidence_type':'CURRENT_ROSTER_SNAPSHOT','source_document_id':_s(d['source_document_id'],'source_document_id'),'source_url':u,'source_observed_at':obs.isoformat().replace('+00:00','Z'),'provable_available_at':obs.isoformat().replace('+00:00','Z'),'effective_from':obs.isoformat().replace('+00:00','Z'),'completeness_scope':d['completeness_scope'],'real_labels_read':0,'formal_pit_eligible':False,'formal_weight':0.0})
 return out

def event_row(raw:Any,sources:dict[str,Any])->dict[str,Any]:
 d=_exact(raw,EVENT_KEYS,'event'); _zero(d['real_labels_read'],'real_labels_read')
 if d['schema']!=EVENT_SCHEMA or d['raw_source_payload_persisted'] is not False: raise MaterializeError('event isolation failed')
 cid=_s(d['competition_id'],'competition_id'); tid=_s(d['team_canonical_id'],'team_canonical_id'); team=sources['teams'].get((cid,tid))
 if team is None or d['team_name']!=team['team_name']: raise MaterializeError('event team denied')
 u=_url(d['source_url'],cid)
 if not any(u.startswith(x) for x in team['event_source_urls']): raise MaterializeError('event source mismatch')
 et=_s(d['event_type'],'event_type')
 if et not in EVENT_TYPES: raise MaterializeError('event type denied')
 pub=_utc(d['source_published_at'],'source_published_at'); obs=_utc(d['source_observed_at'],'source_observed_at'); av=_utc(d['provable_available_at'],'provable_available_at'); eff=_utc(d['effective_at'],'effective_at')
 if pub>av or av>obs: raise MaterializeError('event bitemporal order denied')
 oid=_s(d['person_official_id'],'person_official_id')
 return {'competition_id':cid,'team_canonical_id':tid,'person_official_id':oid,'person_name':_s(d['person_name'],'person_name'),'person_canonical_id':stable_person_id(cid,oid),'evidence_type':et,'source_document_id':_s(d['source_document_id'],'source_document_id'),'source_url':u,'source_published_at':pub.isoformat().replace('+00:00','Z'),'source_observed_at':obs.isoformat().replace('+00:00','Z'),'provable_available_at':av.isoformat().replace('+00:00','Z'),'effective_at':eff.isoformat().replace('+00:00','Z'),'real_labels_read':0,'formal_pit_eligible':False,'formal_weight':0.0}

def materialize(index:Any,shards:Any,inventory:Any,snapshots:Any,events:Any)->dict[str,Any]:
 s=validate_sources(index,shards,inventory)
 if type(snapshots) is not list or type(events) is not list: raise MaterializeError('documents must be lists')
 docs=[]; rows=[]
 for x in snapshots:
  rr=snapshot_rows(x,s); rows.extend(rr); docs.append({'competition_id':rr[0]['competition_id'],'team_canonical_id':rr[0]['team_canonical_id'],'source_observed_at':rr[0]['source_observed_at'],'completeness_scope':rr[0]['completeness_scope'],'members':sorted(r['person_canonical_id'] for r in rr)})
 erows=[event_row(x,s) for x in events]
 byteam={}
 for d in docs: byteam.setdefault((d['competition_id'],d['team_canonical_id']),[]).append(d)
 fixtures=[]; complete=partial=unknown=conflict=0
 for t in s['targets']:
  sideout={}
  for side in ('home','away'):
   cid=t['competition_id']; tid=t[side+'_team_id']; cutoff=_utc(t['prediction_cutoff'],'prediction_cutoff')
   cand=[d for d in byteam.get((cid,tid),[]) if _utc(d['source_observed_at'],'source_observed_at')<=cutoff]
   if not cand: state='UNKNOWN'; unknown+=1
   else:
    latest=max(_utc(d['source_observed_at'],'source_observed_at') for d in cand); best=[d for d in cand if _utc(d['source_observed_at'],'source_observed_at')==latest]
    if len(best)!=1: state='CONFLICT'; conflict+=1
    elif best[0]['completeness_scope']=='COMPLETE_ROSTER': state='COMPLETE_VERIFIED'; complete+=1
    else: state='PARTIAL_VERIFIED'; partial+=1
   sideout[side+'_roster_status']=state
  fixtures.append({'fixture_id':t['fixture_id'],'competition_id':t['competition_id'],'prediction_cutoff':t['prediction_cutoff'],**sideout})
 return {'schema':'football3_adaptive_latent_stage4_target_roster_materialization_report_v1','status':'ENGINEERING_TARGET_ROSTER_MATERIALIZATION_PASS_FORMAL_PIT_FALSE','target_count':21,'unique_target_team_count':38,'source_coverage_team_count':38,'current_complete_source_team_count':30,'event_chain_only_team_count':8,'snapshot_document_count':len(docs),'snapshot_person_row_count':len(rows),'event_row_count':len(erows),'complete_fixture_side_count':complete,'partial_fixture_side_count':partial,'unknown_fixture_side_count':unknown,'conflict_fixture_side_count':conflict,'schedule_revision_conflict_count':len(s['index']['schedule_revision_conflicts']),'fixtures':fixtures,'identity_lock_sha256':IDENTITY,'ordered_identity_sha256':ORDERED,'real_labels_read':0,'real_match_rows_read':0,'training':False,'tuning':False,'real_scoring':False,'provider_api':False,'secret_access':False,'CURRENT_change':False,'formal_pit_eligible':False,'formal_weight':0.0,'merge':False}
