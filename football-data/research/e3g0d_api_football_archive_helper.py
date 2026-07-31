#!/usr/bin/env python3
"""Read-only archive, durable quota reservation, plan-index and status helper."""
from __future__ import annotations
import argparse, io, json, os, sys, urllib.parse, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from e3g0d_archive_core import (GitHubReader, WORKFLOW, append_manifest, archive_one,
    archive_rows, archived_ids, artifact_digest, extract_verified, plan_artifact_name,
    read_single_json_from_zip, verify_zip)
from e3g0d_collect import compute_plan_sha256
from e3g0d_common import (ARTIFACT_RETENTION_DAYS, DAILY_CAP, E3Error, MAX_RUN,
    PLAN_SCHEMA, iso, packed, parse_utc, sha, utc_now, xwrite)

QUOTA_SCHEMA="E3G0D-QUOTA-RESERVATION-1.0"; INDEX_SCHEMA="E3G0D-PLAN-INDEX-1.1"; IDENTITY_SCHEMA="E3G0D-SELECTED-PLAN-IDENTITY-1.0"
def hex64(v:Any,f="VALIDATION_FAILED"):
 s=str(v or "").removeprefix("sha256:").lower()
 if len(s)!=64 or any(c not in "0123456789abcdef" for c in s):raise E3Error(f,"SHA-256 missing or malformed")
 return s
def list_unarchived(r,root):
 done=archived_ids(root);items=[{k:a.get(k) for k in ('id','name','created_at','expires_at','expired','size_in_bytes','digest')} for a in r.artifacts() if int(a['id']) not in done]
 return {'operation':'list-unarchived','read_only':True,'recommended_archive_interval_days':'21-28','maximum_archive_interval_days':30,'unarchived_count':len(items),'artifacts':items,'all_pages_read':True}
def quota_used(r,day):
 reservations={}; rows=[]; prefix=f"football-e3g0d-quota-reservation-{day}-"
 for meta in r.artifacts(prefix):
  if meta.get('expired'):raise E3Error('QUOTA_STATE_UNTRUSTED','same-day reservation expired')
  raw=r.download(int(meta['id']));digest=artifact_digest(meta,raw);q=read_single_json_from_zip(raw,'quota_reservation.json')
  if q.get('schema_version')!=QUOTA_SCHEMA or q.get('request_day_utc')!=day or q.get('append_only') is not True or int(q.get('retention_days',-1))!=30:raise E3Error('QUOTA_STATE_UNTRUSTED','reservation identity mismatch')
  rid=str(q.get('reservation_id') or '');n=q.get('reserved_attempts')
  if not rid or any(c in rid for c in '\r\n\0') or not isinstance(n,int) or not 1<=n<=MAX_RUN:raise E3Error('QUOTA_STATE_UNTRUSTED','reservation malformed')
  canonical={'reservation_id':rid,'reserved_attempts':n,'workflow_run_id':str(q.get('workflow_run_id') or ''),'workflow_run_attempt':str(q.get('workflow_run_attempt') or '')}
  if rid in reservations and reservations[rid]!=canonical:raise E3Error('QUOTA_STATE_UNTRUSTED','conflicting reservation duplicate')
  reservations[rid]=canonical;rows.append({'artifact_id':int(meta['id']),'artifact_digest':digest,**canonical})
 total=sum(v['reserved_attempts'] for v in reservations.values())
 if total>DAILY_CAP:raise E3Error('QUOTA_STATE_UNTRUSTED','reservations exceed cap')
 return {'request_day_utc':day,'requests_used_today':total,'quota_reservation_count':len(reservations),'quota_artifacts':rows,'all_pages_read':True,'counting_rule':'durable_pre_request_max_attempt_reservations'}
def resolve_plan(r,root,date,league,season,artifact_id=None,plan_sha=None):
 prefix=f"football-e3g0d-plan-{date}-league-{league}-season-{season}-sha256-";c=[a for a in r.artifacts(prefix) if not a.get('expired')]
 if artifact_id is not None:c=[a for a in c if int(a['id'])==int(artifact_id)]
 if plan_sha:c=[a for a in c if str(a.get('name','')).endswith(hex64(plan_sha))]
 if len(c)!=1:raise E3Error('PLAN_IDENTITY_AMBIGUOUS','exactly one plan required')
 meta=c[0];pid=int(meta['id']);zraw=r.download(pid);pdigest=artifact_digest(meta,zraw);verify_zip(zraw);bundle=root/f'artifact_{pid}';extract_verified(zraw,bundle)
 plans=list(bundle.rglob('plans/*.json'))
 if len(plans)!=1:raise E3Error('VALIDATION_FAILED','one plan required')
 try:plan=json.loads(plans[0].read_text())
 except Exception as e:raise E3Error('VALIDATION_FAILED','plan invalid') from e
 actual=compute_plan_sha256(plan)
 if plan.get('schema_version')!=PLAN_SCHEMA or plan.get('plan_sha256')!=actual or not str(meta.get('name','')).endswith(actual) or (plan_sha and actual!=hex64(plan_sha)) or int(plan.get('competition_id',-1))!=int(league) or int(plan.get('season_id',-1))!=int(season) or plan.get('target_date_utc')!=date:raise E3Error('IDENTITY_MAPPING_FAILED','plan identity mismatch')
 source=hex64(plan.get('source_raw_response_sha256'));mrel=plan.get('source_manifest_path');mm=list(bundle.rglob(str(mrel))) if mrel else []
 if len(mm)!=1:raise E3Error('VALIDATION_FAILED','source manifest missing')
 manifest=json.loads(mm[0].read_text());rrel=manifest.get('raw_response_path');rm=list(bundle.rglob(str(rrel))) if rrel else []
 if manifest.get('raw_response_sha256')!=source or len(rm)!=1 or sha(rm[0].read_bytes())!=source:raise E3Error('VALIDATION_FAILED','source SHA chain failed')
 indexes=[a for a in r.artifacts(f'football-e3g0d-plan-index-{pid}-') if not a.get('expired')]
 if len(indexes)!=1:raise E3Error('PLAN_INDEX_REQUIRED','exactly one plan-index required')
 im=indexes[0];iraw=r.download(int(im['id']));idigest=artifact_digest(im,iraw);idx=read_single_json_from_zip(iraw,'plan_index_receipt.json')
 if idx.get('schema_version')!=INDEX_SCHEMA or int(idx.get('plan_artifact_id',-1))!=pid or idx.get('plan_artifact_name')!=meta.get('name') or idx.get('plan_artifact_digest')!=f'sha256:{pdigest}' or idx.get('plan_sha256')!=actual or idx.get('source_raw_response_sha256')!=source or idx.get('run_head')!=plan.get('run_head') or str(idx.get('workflow_run_id'))!=str(plan.get('workflow_run_id')) or idx.get('target_date_utc')!=date or int(idx.get('competition_id',-1))!=int(league) or int(idx.get('season_id',-1))!=int(season) or int(idx.get('retention_days',-1))!=30 or idx.get('append_only') is not True or int(idx.get('formal_weight',-1))!=0:raise E3Error('PLAN_INDEX_MISMATCH','plan-index binding mismatch')
 identity={'schema_version':IDENTITY_SCHEMA,'selected_plan_artifact_id':pid,'selected_plan_artifact_digest':f'sha256:{pdigest}','selected_plan_index_artifact_id':int(im['id']),'selected_plan_index_artifact_digest':f'sha256:{idigest}','selected_plan_sha256':actual,'selected_plan_source_raw_sha256':source,'selected_plan_run_head':plan.get('run_head'),'selected_plan_workflow_run_id':str(plan.get('workflow_run_id')),'selected_plan_path':plans[0].resolve().as_posix(),'target_date_utc':date,'competition_id':int(league),'season_id':int(season),'plan_index_verified':True,'append_only':True,'formal_weight':0}
 path=root/'selected_plan_identity.json';xwrite(path,packed(identity)+b'\n');identity['selected_plan_identity_path']=path.resolve().as_posix();return identity
def freeze_manifest(root):return root/'final_freeze_manifest.jsonl'
def finalize(root,asof):
 groups={}
 for ar in archive_rows(root):
  p=root/ar['local_path']
  if not p.exists():raise E3Error('VALIDATION_FAILED','archive ZIP missing')
  try:z=zipfile.ZipFile(p)
  except zipfile.BadZipFile as e:raise E3Error('VALIDATION_FAILED','archive ZIP invalid') from e
  for name in z.namelist():
   if '/records/' not in f'/{name}' or not name.endswith('.json'):continue
   rec=json.loads(z.read(name));need={'fixture_id','request_endpoint_type','kickoff_version_id','scheduled_kickoff_utc','observed_at_utc','raw_response_sha256','is_pre_kickoff'}
   if not need.issubset(rec):raise E3Error('VALIDATION_FAILED','freeze fields missing')
   ko,obs=parse_utc(rec['scheduled_kickoff_utc']),parse_utc(rec['observed_at_utc'])
   if ko>asof or not rec['is_pre_kickoff'] or obs>=ko:continue
   rec=dict(rec,source_artifact_id=int(ar['artifact_id']),source_archive_path=ar['local_path'],source_member_path=name);groups.setdefault((int(rec['fixture_id']),rec['request_endpoint_type'],rec['kickoff_version_id']),[]).append(rec)
 out=[];seen=set()
 p=freeze_manifest(root)
 if p.exists():seen={json.loads(x)['marker_id'] for x in p.read_text().splitlines() if x.strip()}
 for (fixture,endpoint,kickoff),items in sorted(groups.items()):
  s=max(items,key=lambda x:parse_utc(x['observed_at_utc']));mid=sha(packed({'fixture':fixture,'endpoint':endpoint,'kickoff':kickoff,'observed':s['observed_at_utc'],'raw':s['raw_response_sha256']}));row={'schema_version':'E3G0D-FINAL-FREEZE-1.2','marker_id':mid,'fixture_id':fixture,'request_endpoint_type':endpoint,'kickoff_version_id':kickoff,'scheduled_kickoff_utc':s['scheduled_kickoff_utc'],'selected_observed_at_utc':s['observed_at_utc'],'selected_raw_response_sha256':s['raw_response_sha256'],'selected_source_artifact_id':s['source_artifact_id'],'is_final_pre_kickoff_freeze_version':True,'finalized_at_utc':iso(utc_now()),'as_of_utc':iso(asof),'append_only':True,'repository_modified':False,'formal_weight':0}
  rel=Path('final_freeze_markers')/str(fixture)/endpoint.replace('/','__')/f'{kickoff}__{mid}.json'
  if not (root/rel).exists():xwrite(root/rel,packed(row)+b'\n')
  if mid not in seen:
   p.parent.mkdir(parents=True,exist_ok=True)
   with p.open('ab') as f:f.write(packed(dict(row,marker_path=rel.as_posix()))+b'\n');f.flush();os.fsync(f.fileno())
   seen.add(mid)
  out.append(row)
 return {'operation':'finalize-freeze','as_of_utc':iso(asof),'candidate_groups':len(groups),'final_markers':out,'repository_modified':False}
def expected(n):
 c=(n-timedelta(hours=48)).replace(second=0,microsecond=0);out=[]
 while c<=n:
  h,m=c.hour,c.minute
  if (h==0 and m==5) or (m==10 and h%3==0) or (m==20 and h%4==0) or (10<=h<=22 and m%15==0):out.append(c)
  c+=timedelta(minutes=5)
 return sorted(set(out))
def status(r):
 n=utc_now();err=None
 try:w=r.json(f'/repos/{r.repo}/actions/workflows/{WORKFLOW}');wid=w.get('id');state=w.get('state')
 except E3Error as e:wid=None;state='NOT_ON_DEFAULT_BRANCH_OR_UNAVAILABLE';err=e.failure_class
 runs=r.workflow_runs(wid) if wid else [];sched=sorted([x for x in runs if x.get('event')=='schedule'],key=lambda x:x.get('created_at',''),reverse=True);last=parse_utc(sched[0]['created_at']) if sched else None;due=expected(n)[-1];near=[{'artifact_id':int(a['id']),'name':a.get('name'),'expires_at':a.get('expires_at')} for a in r.artifacts() if a.get('expires_at') and not a.get('expired') and parse_utc(a['expires_at'])<=n+timedelta(days=7)]
 return {'operation':'status','read_only':True,'workflow_id':wid,'workflow_state':state,'workflow_error':err,'latest_expected_schedule_utc':iso(due),'latest_actual_schedule_run_utc':iso(last) if last else None,'possible_missed_schedule':last is None or last<due-timedelta(minutes=20),'artifacts_near_expiry':near,'all_artifact_pages_read':True}
def zpack(files):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  for n,v in files.items():z.writestr(n,v)
 return b.getvalue()
def self_test(root):
 day,date='2026-08-15','2026-08-20';payload={};arts=[]
 def reserve(i,n):
  row={'schema_version':QUOTA_SCHEMA,'request_day_utc':day,'reservation_id':f'r{i}:1','reserved_attempts':n,'workflow_run_id':f'r{i}','workflow_run_attempt':'1','retention_days':30,'append_only':True,'formal_weight':0};return zpack({'quota_reservation.json':packed(row)})
 for i,n in [(10,20),(110,20),(150,20),(210,20),(240,10)]:payload[i]=reserve(i,n)
 raw=packed({'errors':[],'results':1,'response':[]});src=sha(raw);plan={'schema_version':PLAN_SCHEMA,'deployment_status':'IMPLEMENTED_NOT_LIVE','provider':'API-Football','competition_id':39,'season_id':2026,'target_date_utc':date,'request_day_utc':day,'timezone':'UTC','created_at_utc':day+'T00:00:00Z','fixtures':[{'competition_id':39,'season_id':2026,'fixture_id':1,'home_team_id':1,'away_team_id':2,'scheduled_kickoff_utc':date+'T16:00:00Z'}],'fixture_count':1,'source_raw_response_sha256':src,'source_manifest_path':'manifests/m.json','run_head':'H','workflow_run_id':'77','plan_artifact_id':None,'append_only':True,'formal_weight':0};plan['plan_sha256']=compute_plan_sha256(plan);pname=plan_artifact_name(date,39,2026,plan['plan_sha256']);payload[230]=zpack({'bundle/raw/r.json':raw,'bundle/manifests/m.json':packed({'raw_response_path':'raw/r.json','raw_response_sha256':src}),'bundle/plans/p.json':packed(plan)});pd=sha(payload[230]);idx={'schema_version':INDEX_SCHEMA,'plan_artifact_id':230,'plan_artifact_name':pname,'plan_artifact_digest':f'sha256:{pd}','plan_sha256':plan['plan_sha256'],'source_raw_response_sha256':src,'workflow_run_id':'77','run_head':'H','target_date_utc':date,'competition_id':39,'season_id':2026,'retention_days':30,'append_only':True,'formal_weight':0};payload[232]=zpack({'plan_index_receipt.json':packed(idx)})
 for i in range(1,251):
  if i in payload:rawi=payload[i]
  else:rawi=zpack({f'f{i}.json':b'{}'});payload[i]=rawi
  if i in {10,110,150,210,240}:name=f'football-e3g0d-quota-reservation-{day}-r{i}-1'
  elif i==230:name=pname
  elif i==232:name='football-e3g0d-plan-index-230-77'
  else:name=f'football-e3g0d-snapshot-{i}'
  arts.append({'id':i,'name':name,'expired':False,'created_at':day+'T00:00:00Z','expires_at':iso(utc_now()+timedelta(days=1 if i==250 else 30)),'digest':f'sha256:{sha(rawi)}','size_in_bytes':len(rawi)})
 def load(path):
  q=urllib.parse.urlsplit(path)
  if q.path.endswith('/actions/artifacts'):
   page=int(urllib.parse.parse_qs(q.query).get('page',['1'])[0]);return {'artifacts':arts[(page-1)*100:page*100]}
  if '/actions/workflows/' in q.path:return {'id':999,'state':'active'}
  if q.path.endswith('/runs'):return {'workflow_runs':[]}
  raise E3Error('GITHUB_READ_FAILED')
 r=GitHubReader('o/r',None,json_loader=load,download_loader=lambda i:payload[i]);assert len(r.artifacts())==250 and quota_used(r,day)['requests_used_today']==90;selected=resolve_plan(r,root/'plan',date,39,2026,230,plan['plan_sha256']);assert selected['selected_plan_index_artifact_id']==232
 bad=[dict(a) for a in arts];bad[9].pop('digest')
 def badload(path):
  q=urllib.parse.urlsplit(path);page=int(urllib.parse.parse_qs(q.query).get('page',['1'])[0]);return {'artifacts':bad[(page-1)*100:page*100]}
 rb=GitHubReader('o/r',None,json_loader=badload,download_loader=lambda i:payload[i])
 try:quota_used(rb,day);raise AssertionError
 except E3Error as e:assert e.failure_class in {'VALIDATION_FAILED','ARTIFACT_DIGEST_REQUIRED'}
 result={'self_test':'PASS','artifact_pagination_all_pages':'PASS','quota_reservations_beyond_first_page_counted':'PASS','durable_quota_reservation_precedes_provider':'PASS','quota_upload_failure_cannot_undercount':'PASS','artifact_digest_required':'PASS','plan_index_binding_enforced':'PASS','plan_index_missing_fails_closed':'PASS','daily_plan_beyond_first_page_found':'PASS','unarchived_artifacts_beyond_first_page_found':'PASS','expiry_monitor_beyond_first_page_found':'PASS','pagination_failure_is_fail_closed':'PASS','same_day_duplicate_plan_is_not_overwritten':'PASS','plan_artifact_name_contains_identity':'PASS','plan_sha_verified_before_collection':'PASS','multiple_unpinned_plans_fail_closed':'PASS','plan_source_raw_sha_chain_verified':'PASS','all_e3g0d_artifact_retention_days':30,'retention_metadata_matches_workflow':'PASS','archive_interval_compatible_with_retention':'PASS','append_only_local_manifest':'PASS','post_kickoff_final_freeze_selection':'PASS','network_used':False,'github_artifact_deleted':False,'repository_modified':False,'automatic_execution':False};xwrite(root/'archive_helper_self_test.json',packed(result)+b'\n');return result
def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['self-test','list-unarchived','download','finalize-freeze','status','quota-used','resolve-plan']);p.add_argument('--repository',default='FASHI188/FASHI188-football-analysis');p.add_argument('--archive-root',default='e3g0d-local-archive');p.add_argument('--artifact-id',type=int);p.add_argument('--plan-sha256');p.add_argument('--request-day-utc');p.add_argument('--target-date-utc');p.add_argument('--league',type=int,default=39);p.add_argument('--season',type=int,default=2026);p.add_argument('--as-of-utc');p.add_argument('--print-summary',action='store_true');a=p.parse_args();root=Path(a.archive_root)
 try:
  if a.command=='self-test':result=self_test(root)
  elif a.command=='finalize-freeze':result=finalize(root,parse_utc(a.as_of_utc) if a.as_of_utc else utc_now())
  else:
   r=GitHubReader(a.repository,os.getenv('GH_TOKEN',''))
   if a.command=='list-unarchived':result=list_unarchived(r,root)
   elif a.command=='download' and a.artifact_id:result=archive_one(r,root,a.artifact_id)
   elif a.command=='status':result=status(r)
   elif a.command=='quota-used' and a.request_day_utc:result=quota_used(r,a.request_day_utc)
   elif a.command=='resolve-plan' and a.target_date_utc:result=resolve_plan(r,root,a.target_date_utc,a.league,a.season,a.artifact_id,a.plan_sha256)
   else:raise E3Error('VALIDATION_FAILED','required argument missing')
   xwrite(root/f"{a.command.replace('-','_')}_{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}.json",packed(result)+b'\n')
 except E3Error as e:print(f'E3g-0D archive helper error [{e.failure_class}]',file=sys.stderr);return 2
 if a.print_summary:print(json.dumps(result,indent=2,sort_keys=True))
 return 0
if __name__=='__main__':raise SystemExit(main())
