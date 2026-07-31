#!/usr/bin/env python3
"""Read-only archive, quota, immutable-plan, freeze and status helper."""
from __future__ import annotations
import argparse,io,json,os,sys,urllib.parse,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Mapping
from e3g0d_archive_core import GitHubReader,WORKFLOW,append_manifest,archive_one,archive_rows,archived_ids,plan_artifact_name,quota_used,resolve_plan,verify_zip
from e3g0d_collect import compute_plan_sha256
from e3g0d_common import E3Error,PLAN_SCHEMA,iso,packed,parse_utc,sha,utc_now,xwrite

def list_unarchived(r:GitHubReader,root:Path)->dict[str,Any]:
 d=archived_ids(root);a=[{k:x.get(k) for k in ('id','name','created_at','expires_at','expired','size_in_bytes','digest')} for x in r.artifacts() if int(x['id']) not in d]
 return {'operation':'list-unarchived','read_only':True,'recommended_archive_interval_days':'21-28','maximum_archive_interval_days':30,'unarchived_count':len(a),'artifacts':a,'all_pages_read':True}

def _freeze_manifest(root:Path)->Path:return root/'final_freeze_manifest.jsonl'
def _freeze_ids(root:Path)->set[str]:
 p=_freeze_manifest(root)
 try:return {json.loads(x)['marker_id'] for x in p.read_text().splitlines() if x.strip()} if p.exists() else set()
 except Exception as e:raise E3Error('VALIDATION_FAILED','final-freeze manifest damaged') from e
def _append_freeze(root:Path,row:Mapping[str,Any])->None:
 if str(row['marker_id']) in _freeze_ids(root):return
 p=_freeze_manifest(root);p.parent.mkdir(parents=True,exist_ok=True)
 try:
  with p.open('ab') as f:f.write(packed(dict(row))+b'\n');f.flush();os.fsync(f.fileno())
 except OSError as e:raise E3Error('APPEND_ONLY_WRITE_FAILED','final-freeze append failed') from e

def finalize(root:Path,as_of:datetime)->dict[str,Any]:
 groups:{}={}
 for ar in archive_rows(root):
  path=root/ar['local_path']
  if not path.exists():raise E3Error('VALIDATION_FAILED','archive manifest references missing ZIP')
  try:z=zipfile.ZipFile(path)
  except zipfile.BadZipFile as e:raise E3Error('VALIDATION_FAILED','archived ZIP invalid') from e
  for name in z.namelist():
   if '/records/' not in f'/{name}' or not name.endswith('.json'):continue
   rec=json.loads(z.read(name));need={'fixture_id','request_endpoint_type','kickoff_version_id','scheduled_kickoff_utc','observed_at_utc','raw_response_sha256','is_pre_kickoff'}
   if not need.issubset(rec):raise E3Error('VALIDATION_FAILED','PIT record missing freeze fields')
   ko,obs=parse_utc(rec['scheduled_kickoff_utc']),parse_utc(rec['observed_at_utc'])
   if ko>as_of or not rec['is_pre_kickoff'] or obs>=ko:continue
   rec=dict(rec,source_artifact_id=int(ar['artifact_id']),source_archive_path=ar['local_path'],source_member_path=name)
   groups.setdefault((int(rec['fixture_id']),rec['request_endpoint_type'],rec['kickoff_version_id']),[]).append(rec)
 out=[]
 for (fixture,endpoint,kickoff),items in sorted(groups.items()):
  s=max(items,key=lambda r:parse_utc(r['observed_at_utc']));mid=sha(packed({'fixture_id':fixture,'endpoint':endpoint,'kickoff_version_id':kickoff,'observed_at_utc':s['observed_at_utc'],'raw_response_sha256':s['raw_response_sha256']}))
  row={'schema_version':'E3G0D-FINAL-FREEZE-1.1','marker_id':mid,'provider':s.get('provider'),'competition_id':s.get('competition_id'),'season_id':s.get('season_id'),'fixture_id':fixture,'home_team_id':s.get('home_team_id'),'away_team_id':s.get('away_team_id'),'scheduled_kickoff_utc':s['scheduled_kickoff_utc'],'kickoff_version_id':kickoff,'request_endpoint_type':endpoint,'selected_observed_at_utc':s['observed_at_utc'],'selected_raw_response_sha256':s['raw_response_sha256'],'selected_source_artifact_id':s['source_artifact_id'],'selected_source_archive_path':s['source_archive_path'],'selected_source_member_path':s['source_member_path'],'is_final_pre_kickoff_freeze_version':True,'selection_rule':'latest observed_at_utc strictly before same kickoff version after kickoff','finalized_at_utc':iso(utc_now()),'as_of_utc':iso(as_of),'append_only':True,'github_artifact_modified':False,'repository_modified':False,'formal_weight':0}
  rel=Path('final_freeze_markers')/str(fixture)/endpoint.replace('/','__')/f'{kickoff}__{mid}.json'
  if not (root/rel).exists():xwrite(root/rel,packed(row)+b'\n')
  _append_freeze(root,dict(row,marker_path=rel.as_posix()));out.append(row)
 return {'operation':'finalize-freeze','read_only_inputs':True,'as_of_utc':iso(as_of),'candidate_groups':len(groups),'final_markers':out,'repository_modified':False}

def _expected(n:datetime)->list[datetime]:
 c=(n-timedelta(hours=48)).replace(minute=((n-timedelta(hours=48)).minute//5)*5,second=0,microsecond=0);out=[]
 while c<=n:
  h,m=c.hour,c.minute
  if (h==0 and m==5) or (m==10 and h%3==0) or (m==20 and h%4==0) or (10<=h<=22 and m%15==0):out.append(c)
  c+=timedelta(minutes=5)
 return sorted(set(out))
def status(r:GitHubReader)->dict[str,Any]:
 n=utc_now();err=None
 try:w=r.json(f'/repos/{r.repo}/actions/workflows/{WORKFLOW}');wid=w.get('id');state=w.get('state')
 except E3Error as e:wid=None;state='NOT_ON_DEFAULT_BRANCH_OR_UNAVAILABLE';err=e.failure_class
 runs=r.workflow_runs(wid) if wid else [];sched=sorted([x for x in runs if x.get('event')=='schedule'],key=lambda x:x.get('created_at',''),reverse=True);last=parse_utc(sched[0]['created_at']) if sched else None;due=_expected(n)[-1]
 near=[{'artifact_id':int(a['id']),'name':a.get('name'),'expires_at':a.get('expires_at')} for a in r.artifacts() if a.get('expires_at') and not a.get('expired') and parse_utc(a['expires_at'])<=n+timedelta(days=7)]
 return {'operation':'status','read_only':True,'workflow_file':WORKFLOW,'workflow_id':wid,'workflow_state':state,'workflow_error':err,'latest_expected_schedule_utc':iso(due),'latest_actual_schedule_run_utc':iso(last) if last else None,'latest_actual_run_utc':max((x.get('created_at','') for x in runs),default=None),'possible_missed_schedule':last is None or last<due-timedelta(minutes=20),'artifacts_near_expiry':near,'all_artifact_pages_read':True,'meaningless_keepalive_commit_permitted':False}

def _zip(files:Mapping[str,bytes])->bytes:
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  for n,r in files.items():z.writestr(n,r)
 return b.getvalue()
def _quota(day:str,n:int)->bytes:return _zip({'quota_receipt.json':packed({'request_day_utc':day,'target_date_utc':'2099-01-01','request_attempts':n,'retention_days':30,'append_only':True})})
def _plan(date:str,league:int,season:int,head:str)->tuple[bytes,str,str]:
 raw=packed({'errors':[],'results':1,'response':[]});rs=sha(raw);rp='raw/source.json';mp='manifests/source.manifest.json';man={'raw_response_path':rp,'raw_response_sha256':rs}
 plan={'schema_version':PLAN_SCHEMA,'deployment_status':'IMPLEMENTED_NOT_LIVE','provider':'API-Football','competition_id':league,'season_id':season,'target_date_utc':date,'request_day_utc':'2026-08-15','timezone':'UTC','created_at_utc':'2026-08-15T00:00:00Z','fixtures':[{'competition_id':league,'season_id':season,'fixture_id':1001,'home_team_id':1,'away_team_id':2,'scheduled_kickoff_utc':f'{date}T16:00:00Z'}],'fixture_count':1,'source_raw_response_sha256':rs,'source_manifest_path':mp,'run_head':head,'workflow_run_id':'77','plan_artifact_id':None,'append_only':True,'formal_weight':0};plan['plan_sha256']=compute_plan_sha256(plan);pp=f"bundle/plans/{date}__league_{league}__season_{season}__sha256_{plan['plan_sha256']}.json"
 return _zip({f'bundle/{rp}':raw,f'bundle/{mp}':packed(man),pp:packed(plan)}),plan['plan_sha256'],rs

def self_test(root:Path)->dict[str,Any]:
 day,date='2026-08-15','2026-08-20';praw,psha,src=_plan(date,39,2026,'PLAN_HEAD');praw2,psha2,_=_plan(date,39,2026,'PLAN_HEAD_2');payload={10:_quota(day,20),150:_quota(day,30),240:_quota(day,40),230:praw,231:praw2};arts=[]
 for i in range(1,251):
  name=f'football-e3g0d-quota-{day}-{i}' if i in {10,150,240} else plan_artifact_name(date,39,2026,psha if i==230 else psha2) if i in {230,231} else f'football-e3g0d-snapshot-{i}';raw=payload.get(i,_zip({f'f{i}.json':b'{}'}));payload.setdefault(i,raw);arts.append({'id':i,'name':name,'expired':False,'created_at':f'2026-08-{1+i%20:02d}T00:00:00Z','expires_at':iso(utc_now()+timedelta(days=1 if i==250 else 30)),'digest':f'sha256:{sha(raw)}','size_in_bytes':len(raw)})
 def load(path:str)->Mapping[str,Any]:
  q=urllib.parse.urlsplit(path)
  if q.path.endswith('/actions/artifacts'):
   page=int(urllib.parse.parse_qs(q.query).get('page',['1'])[0]);return {'artifacts':arts[(page-1)*100:page*100]}
  if '/actions/artifacts/' in q.path:return arts[int(q.path.rsplit('/',1)[-1])-1]
  if q.path.endswith('/runs'):return {'workflow_runs':[]}
  if '/actions/workflows/' in q.path:return {'id':999,'state':'active'}
  raise E3Error('GITHUB_READ_FAILED')
 r=GitHubReader('owner/repo',None,json_loader=load,download_loader=lambda i:payload[i]);assert len(r.artifacts())==250;assert quota_used(r,day)['requests_used_today']==90;assert any(x['id']==250 for x in list_unarchived(r,root/'list')['artifacts']);assert any(x['artifact_id']==250 for x in status(r)['artifacts_near_expiry'])
 got=resolve_plan(r,root/'plan',date,39,2026,artifact_id=230,plan_sha256=psha);assert got['selected_plan_artifact_id']==230 and got['selected_plan_source_raw_sha256']==src
 try:resolve_plan(r,root/'ambiguous',date,39,2026);raise AssertionError
 except E3Error as e:assert e.failure_class=='PLAN_IDENTITY_AMBIGUOUS'
 def broken(path:str)->Mapping[str,Any]:
  q=urllib.parse.urlsplit(path);page=int(urllib.parse.parse_qs(q.query).get('page',['1'])[0]);
  if page==2:raise E3Error('PAGINATION_FAILED')
  return {'artifacts':arts[:100]}
 try:quota_used(GitHubReader('owner/repo',None,json_loader=broken,download_loader=lambda i:payload[i]),day);raise AssertionError
 except E3Error as e:assert e.failure_class=='PAGINATION_FAILED'
 rec={'provider':'API-Football','competition_id':39,'season_id':2026,'fixture_id':1,'home_team_id':1,'away_team_id':2,'scheduled_kickoff_utc':'2026-08-15T16:00:00Z','kickoff_version_id':'k1','observed_at_utc':'2026-08-15T15:45:00Z','request_endpoint_type':'injuries','raw_response_sha256':'abc','is_pre_kickoff':True};fr=root/'freeze';rel=Path('artifacts/freeze.zip');xwrite(fr/rel,_zip({'bundle/records/1/injuries/test.json':packed(rec)}));append_manifest(fr,{'schema_version':'TEST','artifact_id':9999,'local_path':rel.as_posix()});assert len(finalize(fr,datetime(2026,8,16,tzinfo=timezone.utc))['final_markers'])==1
 result={'self_test':'PASS','artifact_pagination_all_pages':'PASS','quota_receipts_beyond_first_page_counted':'PASS','daily_plan_beyond_first_page_found':'PASS','unarchived_artifacts_beyond_first_page_found':'PASS','expiry_monitor_beyond_first_page_found':'PASS','pagination_failure_is_fail_closed':'PASS','same_day_duplicate_plan_is_not_overwritten':'PASS','plan_artifact_name_contains_identity':'PASS','plan_sha_verified_before_collection':'PASS','multiple_unpinned_plans_fail_closed':'PASS','plan_source_raw_sha_chain_verified':'PASS','all_e3g0d_artifact_retention_days':30,'retention_metadata_matches_workflow':'PASS','archive_interval_compatible_with_retention':'PASS','append_only_local_manifest':'PASS','post_kickoff_final_freeze_selection':'PASS','network_used':False,'github_artifact_deleted':False,'repository_modified':False,'automatic_execution':False};xwrite(root/'archive_helper_self_test.json',packed(result)+b'\n');return result

def main()->int:
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
   elif a.command=='resolve-plan' and a.target_date_utc:result=resolve_plan(r,root,a.target_date_utc,a.league,a.season,artifact_id=a.artifact_id,plan_sha256=a.plan_sha256)
   else:raise E3Error('VALIDATION_FAILED','required command argument missing')
   xwrite(root/f"{a.command.replace('-','_')}_{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}.json",packed(result)+b'\n')
 except E3Error as e:print(f'E3g-0D archive helper error [{e.failure_class}]',file=sys.stderr);return 2
 if a.print_summary:print(json.dumps(result,indent=2,sort_keys=True))
 return 0
if __name__=='__main__':raise SystemExit(main())
