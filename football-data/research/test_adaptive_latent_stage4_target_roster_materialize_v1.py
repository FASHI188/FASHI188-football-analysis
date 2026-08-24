#!/usr/bin/env python3
from __future__ import annotations
import copy,json
from pathlib import Path
from adaptive_latent_stage4_target_roster_materialize_v1 import MaterializeError,EVENT_SCHEMA,SNAP_SCHEMA,materialize,stable_person_id,validate_sources
ROOT=Path(__file__).resolve().parent
COMPS=['ENG_PremierLeague','ESP_LaLiga','GER_Bundesliga','ITA_SerieA','FRA_Ligue1']

def load():
 idx=json.loads((ROOT/'adaptive_latent_stage4_target_roster_sources_index_v1.json').read_text())
 shards=[json.loads((ROOT/f'adaptive_latent_stage4_target_roster_sources_{c}_v1.json').read_text()) for c in COMPS]
 inv=json.loads((ROOT/'target_inventory_candidate_v3.json').read_text())
 return idx,shards,inv

def team(src,name):
 return next(v for v in src['teams'].values() if v['team_name']==name)

def snap(src,name,observed,complete='COMPLETE_ROSTER'):
 t=team(src,name)
 return {'schema':SNAP_SCHEMA,'competition_id':t['competition_id'],'season_id':'2026-27','team_name':name,'team_canonical_id':t['team_canonical_id'],'source_url':t['current_roster_source_url'],'source_document_id':'snap:'+name.lower().replace(' ','-')+':'+observed,'source_observed_at':observed,'completeness_scope':complete,'players':[{'person_official_id':'p1','person_name':'Player One'},{'person_official_id':'p2','person_name':'Player Two'}],'raw_source_payload_persisted':False,'real_labels_read':0}

def event(src,name,url,oid='e1'):
 t=team(src,name)
 return {'schema':EVENT_SCHEMA,'competition_id':t['competition_id'],'season_id':'2026-27','team_name':name,'team_canonical_id':t['team_canonical_id'],'source_url':url,'source_document_id':'event:'+oid,'source_published_at':'2026-08-20T10:00:00Z','source_observed_at':'2026-08-20T12:00:00Z','provable_available_at':'2026-08-20T10:00:00Z','event_type':'TRANSFER_IN','effective_at':'2026-08-20T10:00:00Z','person_official_id':oid,'person_name':'Event Player','raw_source_payload_persisted':False,'real_labels_read':0}

def fail(fn):
 try: fn()
 except MaterializeError: return
 raise AssertionError('expected fail-closed rejection')

def main():
 idx,shards,inv=load(); src=validate_sources(idx,shards,inv)
 assert len(src['teams'])==38
 assert sum(t['current_complete_source_verified'] for t in src['teams'].values())==30
 assert sum(not t['current_complete_source_verified'] for t in src['teams'].values())==8
 assert len(idx['schedule_revision_conflicts'])==1
 assert stable_person_id('ENG_PremierLeague','p1')==stable_person_id('ENG_PremierLeague','p1')
 assert stable_person_id('ENG_PremierLeague','p1')!=stable_person_id('ENG_PremierLeague','p2')
 assert stable_person_id('ENG_PremierLeague','p1')!=stable_person_id('ITA_SerieA','p1')

 # Current snapshots before still-future cutoffs are usable; no labels involved.
 s=[snap(src,'Fulham','2026-08-24T15:20:00Z'),snap(src,'Chelsea','2026-08-24T15:20:00Z'),snap(src,'Bologna','2026-08-24T15:20:00Z'),snap(src,'Lazio','2026-08-24T15:20:00Z')]
 r=materialize(idx,shards,inv,s,[])
 byid={x['fixture_id']:x for x in r['fixtures']}
 fulham=next(x for x in inv['targets'] if x['home_team_id']==team(src,'Fulham')['team_canonical_id'])
 bologna=next(x for x in inv['targets'] if x['home_team_id']==team(src,'Bologna')['team_canonical_id'])
 assert byid[fulham['fixture_id']]['home_roster_status']=='COMPLETE_VERIFIED'
 assert byid[fulham['fixture_id']]['away_roster_status']=='COMPLETE_VERIFIED'
 assert byid[bologna['fixture_id']]['home_roster_status']=='COMPLETE_VERIFIED'
 assert byid[bologna['fixture_id']]['away_roster_status']=='COMPLETE_VERIFIED'

 # Mutable current pages observed after an Aug-23 cutoff cannot backfill history.
 late=[snap(src,'Brighton & Hove Albion','2026-08-24T15:20:00Z'),snap(src,'Aston Villa','2026-08-24T15:20:00Z')]
 r2=materialize(idx,shards,inv,late,[])
 old=next(x for x in r2['fixtures'] if x['competition_id']=='ENG_PremierLeague' and x['prediction_cutoff']=='2026-08-23T12:45:00Z')
 assert old['home_roster_status']=='UNKNOWN' and old['away_roster_status']=='UNKNOWN'

 # Event-chain evidence materializes but never masquerades as a complete roster.
 barca=team(src,'FC Barcelona'); e=event(src,'FC Barcelona',barca['event_source_urls'][0])
 r3=materialize(idx,shards,inv,[],[e])
 assert r3['event_row_count']==1 and r3['complete_fixture_side_count']==0
 # France remains league-official event-chain-only rather than false completeness.
 assert all(not t['current_complete_source_verified'] for t in src['teams'].values() if t['competition_id']=='FRA_Ligue1')

 # Real Serie A /team/<club>/squad family is accepted by target guard.
 sb=snap(src,'Bologna','2026-08-24T15:20:00Z'); assert '/team/bologna/squad' in sb['source_url']; materialize(idx,shards,inv,[sb],[])

 # Fail closed: host/query/extra field/boolean-label/duplicate person/team drift/fixture drift.
 bad=copy.deepcopy(sb); bad['source_url']='https://evil.example/team/bologna/squad'; fail(lambda:materialize(idx,shards,inv,[bad],[]))
 bad=copy.deepcopy(sb); bad['source_url']+='?x=1'; fail(lambda:materialize(idx,shards,inv,[bad],[]))
 bad=copy.deepcopy(sb); bad['result']=None; fail(lambda:materialize(idx,shards,inv,[bad],[]))
 bad=copy.deepcopy(sb); bad['real_labels_read']=False; fail(lambda:materialize(idx,shards,inv,[bad],[]))
 bad=copy.deepcopy(sb); bad['players'].append(copy.deepcopy(bad['players'][0])); fail(lambda:materialize(idx,shards,inv,[bad],[]))
 bad=copy.deepcopy(sb); bad['team_canonical_id']='official-schedule-team:'+'0'*64; fail(lambda:materialize(idx,shards,inv,[bad],[]))
 badinv=copy.deepcopy(inv); badinv['targets'][0]['home_team_id']='official-schedule-team:'+'0'*64; fail(lambda:validate_sources(idx,shards,badinv))
 assert r['real_labels_read']==0 and r['real_match_rows_read']==0 and r['training'] is False and r['tuning'] is False and r['real_scoring'] is False and r['provider_api'] is False and r['secret_access'] is False and r['CURRENT_change'] is False and r['formal_pit_eligible'] is False and r['formal_weight']==0.0 and r['merge'] is False
 print('PASS target roster source coverage + materialization + as-of fail-closed')
if __name__=='__main__': main()
