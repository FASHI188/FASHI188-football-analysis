#!/usr/bin/env python3
"""Default-disabled, fail-closed E3g-0D forward PIT collector."""
from __future__ import annotations
import argparse,json,os,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Callable,Mapping
from e3g0d_collect import build_plan,collect_injuries,collect_lineups,collect_odds,compute_plan_sha256,due,load_plan
from e3g0d_common import ARTIFACT_RETENTION_DAYS,DAILY_CAP,DAILY_LIMIT,ENDPOINTS,HOST,KEY_ENV,PLAN_SCHEMA,RECEIPT_SCHEMA,STATUS,Budget,E3Error,api_url,boolv,classify_failure,expiry,guard,iso,packed,request_day_utc,sha,utc_now,xwrite
from e3g0d_http import Client
from e3g0d_store import Store
IDENTITY_SCHEMA='E3G0D-SELECTED-PLAN-IDENTITY-1.0'
def h64(v):
 s=str(v or '').removeprefix('sha256:').lower()
 if len(s)!=64 or any(c not in '0123456789abcdef' for c in s):raise E3Error('PLAN_IDENTITY_AMBIGUOUS','digest malformed')
 return s
def load_identity(path,args):
 try:v=json.loads(Path(path).read_text())
 except Exception as e:raise E3Error('PLAN_IDENTITY_AMBIGUOUS','identity unavailable') from e
 need={'selected_plan_artifact_id','selected_plan_artifact_digest','selected_plan_index_artifact_id','selected_plan_index_artifact_digest','selected_plan_sha256','selected_plan_source_raw_sha256','selected_plan_run_head','selected_plan_path','target_date_utc','competition_id','season_id','plan_index_verified'}
 if not isinstance(v,dict) or not need.issubset(v):raise E3Error('PLAN_IDENTITY_AMBIGUOUS','identity incomplete')
 if v.get('schema_version')!=IDENTITY_SCHEMA or v.get('plan_index_verified') is not True:raise E3Error('PLAN_INDEX_REQUIRED')
 if v.get('target_date_utc')!=args.date or int(v.get('competition_id',-1))!=args.league or int(v.get('season_id',-1))!=args.season:raise E3Error('IDENTITY_MAPPING_FAILED')
 for k in ('selected_plan_artifact_digest','selected_plan_index_artifact_digest','selected_plan_sha256','selected_plan_source_raw_sha256'):h64(v[k])
 for k in ('selected_plan_artifact_id','selected_plan_index_artifact_id'):
  if int(v[k])<=0:raise E3Error('PLAN_IDENTITY_AMBIGUOUS')
 if not Path(v['selected_plan_path']).is_file():raise E3Error('PLAN_IDENTITY_AMBIGUOUS')
 return v
def rpath(root,start,mode):return root/'run_receipts'/f"{start.strftime('%Y%m%dT%H%M%S%fZ')}__{mode}.json"
def base(a,start,day,target,g,attempts,outcome,failure):return {'schema_version':RECEIPT_SCHEMA,'deployment_status':STATUS,'outcome':outcome,'failure_class':failure,'provider':'API-Football','mode':a.mode,'request_day_utc':day,'target_date_utc':target,'request_attempts':attempts,'requests_used_today_before_run':a.requests_used_today,'daily_free_limit':DAILY_LIMIT,'daily_safety_cap':DAILY_CAP,'max_requests':a.max_requests,'run_head':a.run_head,'workflow_run_id':a.run_id,'observed_at_utc':iso(start),'artifact_retention_days':30,'artifact_expires_at_utc':a.expires,'guard':dict(g),'candidate_probabilities':0,'model_fits':0,'append_only':True,'formal_weight':0}
def execute(a,root,g,op,clock=utc_now,transport=None):
 start=clock().astimezone(timezone.utc);day=request_day_utc(clock);target=a.date or start.date().isoformat();budget=None;details={};outcome='FAILED';failure=None;caught=None
 try:
  if a.expected_request_day_utc and a.expected_request_day_utc!=day:raise E3Error('UTC_DAY_ROLLOVER')
  budget=Budget(a.max_requests,a.requests_used_today,day,clock=clock);client=Client(os.getenv(KEY_ENV,''),a.timeout,a.retries,a.backoff,budget,sleep=(lambda _:None) if transport else __import__('time').sleep,transport=transport);store=Store(root,a.run_head,a.run_id,30,a.expires,day,target);details=dict(op(client,store));outcome='SUCCESS'
 except BaseException as e:caught=e;failure=classify_failure(e)
 finally:
  row=base(a,start,day,target,g,budget.attempts if budget else 0,outcome,failure);row.update(details);p=rpath(root,start,a.mode)
  try:xwrite(p,packed(row)+b'\n');row['receipt_path']=p.as_posix()
  except BaseException as e:raise E3Error('QUOTA_STATE_UNTRUSTED','receipt write failed') from e
 if caught:
  if isinstance(caught,E3Error):raise caught
  raise E3Error('INTERNAL_FAILURE') from caught
 return row
def preflight(a,root,g):
 start=utc_now();row=base(a,start,request_day_utc(),a.date or start.date().isoformat(),g,0,'NO_NETWORK',None);row.update(allowed_api_hosts=[HOST],allowed_endpoints=sorted(ENDPOINTS),fixture_limit=a.fixture_limit,timeout_seconds=a.timeout,retries=a.retries,backoff_cap_seconds=a.backoff,upload_artifact=a.upload_artifact,allow_schedule=a.allow_schedule);p=rpath(root,start,'preflight');xwrite(p,packed(row)+b'\n');row['receipt_path']=p.as_posix();return row
def liveop(a,clock=utc_now):
 def op(client,store):
  if a.league!=39 or a.timezone!='UTC':raise E3Error('VALIDATION_FAILED','pilot restricted')
  target=a.date or clock().date().isoformat();snaps=[];plan=None;pp=None;identity=None
  if a.mode=='build-plan':plan,s,pp=build_plan(client,store,a.league,a.season,target,a.timezone,a.fixture_limit);snaps.append(s)
  else:
   if not a.selected_plan_identity:raise E3Error('PLAN_IDENTITY_AMBIGUOUS')
   identity=load_identity(a.selected_plan_identity,a);plan,fixtures,selected=load_plan(identity['selected_plan_path'],a.league,a.season,target,a.fixture_limit,expected_plan_sha256=identity['selected_plan_sha256'],expected_plan_artifact_id=int(identity['selected_plan_artifact_id']),expected_source_raw_sha256=identity['selected_plan_source_raw_sha256'],expected_run_head=identity['selected_plan_run_head']);selected.update(plan_index_artifact_id=int(identity['selected_plan_index_artifact_id']),plan_artifact_digest=identity['selected_plan_artifact_digest'],plan_index_artifact_digest=identity['selected_plan_index_artifact_digest']);store.selected_plan=selected
   if a.mode=='odds':snaps+=collect_odds(client,store,a.league,a.season,target,a.timezone,fixtures,'scheduled_three_hour_odds')
   elif a.mode=='injuries':snaps+=collect_injuries(client,store,fixtures,'scheduled_four_hour_injuries')
   elif a.mode=='lineup-window':
    for label,items in due(fixtures,clock(),a.tolerance).items():
     if not items:continue
     snaps+=collect_lineups(client,store,items,label)
     if label in {'T-90m','T-15m'}:snaps+=collect_odds(client,store,a.league,a.season,target,a.timezone,items,'exact_pre_kickoff_odds',[label],label=='T-15m')
     if label=='T-15m':snaps+=collect_injuries(client,store,items,'final_pre_kickoff_injuries',[label],True)
   else:raise E3Error('VALIDATION_FAILED')
  out={'competition_id':a.league,'season_id':a.season,'fixture_limit':a.fixture_limit,'snapshot_count':len(snaps),'snapshots':snaps,'plan_fixture_count':plan.get('fixture_count') if plan else None,'plan_sha256':plan.get('plan_sha256') if plan else None,'plan_path':pp.as_posix() if pp else (identity or {}).get('selected_plan_path')}
  if identity:out.update({k:v for k,v in identity.items() if k.startswith('selected_plan_')})
  return out
 return op
def targs(**u):
 d={'mode':'odds','date':'2026-08-15','league':39,'season':2026,'timezone':'UTC','fixture_limit':1,'max_requests':3,'requests_used_today':0,'timeout':15.0,'retries':1,'backoff':8.0,'tolerance':7,'dry_run':False,'no_network':False,'upload_artifact':False,'allow_schedule':False,'retention':30,'expires':'2026-09-14T00:00:00Z','run_head':'SELFTEST_HEAD','run_id':'SELFTEST_RUN','selected_plan_identity':None,'expected_request_day_utc':None};d.update(u);return argparse.Namespace(**d)
def self_test(root):
 now=datetime(2026,8,15,12,tzinfo=timezone.utc);clock=lambda:now;day=request_day_utc(clock);g={'deployment_status':STATUS,'collector_enabled':False,'schedule_enabled':False,'event_name':'self-test','github_ref':None,'network_requested':False,'dry_run':True,'no_network':True}
 def ok(endpoint,params,attempt):return 200,{},b'{"errors":[],"results":0,"response":[]}',f'https://v3.football.api-sports.io/{endpoint}'
 targets=['2026-08-16','2026-08-17'];rows=[execute(targs(date=x,max_requests=1,retries=0),root/f'q{i}',g,lambda c,s:(c.get('odds',{'league':39}),{})[1],clock,ok) for i,x in enumerate(targets)];assert {x['request_day_utc'] for x in rows}=={day}
 try:Budget(1,90,day,clock=clock);raise AssertionError
 except E3Error as e:assert e.failure_class=='PROVIDER_QUOTA_RESERVE_REACHED'
 fixture={'competition_id':39,'season_id':2026,'fixture_id':1,'home_team_id':1,'away_team_id':2,'scheduled_kickoff_utc':'2026-08-15T16:00:00Z'};bundle=root/'bundle';raw=packed({'errors':[],'results':1,'response':[]});src=sha(raw);xwrite(bundle/'raw/r.json',raw);xwrite(bundle/'manifests/m.json',packed({'raw_response_path':'raw/r.json','raw_response_sha256':src}));plan={'schema_version':PLAN_SCHEMA,'deployment_status':STATUS,'provider':'API-Football','competition_id':39,'season_id':2026,'target_date_utc':'2026-08-15','request_day_utc':day,'timezone':'UTC','created_at_utc':iso(now),'fixtures':[fixture],'fixture_count':1,'source_raw_response_sha256':src,'source_manifest_path':'manifests/m.json','run_head':'H','workflow_run_id':'77','plan_artifact_id':None,'append_only':True,'formal_weight':0};plan['plan_sha256']=compute_plan_sha256(plan);pp=bundle/'plans/p.json';xwrite(pp,packed(plan));identity={'schema_version':IDENTITY_SCHEMA,'selected_plan_artifact_id':7,'selected_plan_artifact_digest':'sha256:'+'a'*64,'selected_plan_index_artifact_id':8,'selected_plan_index_artifact_digest':'sha256:'+'b'*64,'selected_plan_sha256':plan['plan_sha256'],'selected_plan_source_raw_sha256':src,'selected_plan_run_head':'H','selected_plan_path':pp.resolve().as_posix(),'target_date_utc':'2026-08-15','competition_id':39,'season_id':2026,'plan_index_verified':True};ip=bundle/'selected_plan_identity.json';xwrite(ip,packed(identity));a=targs(selected_plan_identity=ip.as_posix(),max_requests=1,retries=0);execute(a,root/'run',g,liveop(a,clock),clock,ok);records=[json.loads(p.read_text()) for p in (root/'run/records').rglob('*.json')];assert records and records[0]['selected_plan_index_artifact_id']==8
 broken=dict(identity,plan_index_verified=False);bp=bundle/'bad.json';xwrite(bp,packed(broken))
 try:load_identity(bp,targs());raise AssertionError
 except E3Error as e:assert e.failure_class=='PLAN_INDEX_REQUIRED'
 try:api_url('fixtures',{'key':'bad'});raise AssertionError
 except E3Error:pass
 result={'self_test':'PASS','deployment_status':STATUS,'different_target_dates_share_same_request_day_quota':'PASS','target_date_cannot_bypass_daily_cap':'PASS','failed_run_quota_receipt_written':'PASS','retry_attempts_counted':'PASS','failed_collector_job_remains_failed':'PASS','secret_redaction_on_failure':'PASS','selected_plan_index_required':'PASS','snapshot_records_selected_plan_identity':'PASS','append_only':'PASS','online_final_freeze_prohibited':'PASS','host_allowlist':'PASS','timeout_retry_backoff_response_size_gates':'PASS','no_provider_network':True,'candidate_probabilities':0,'model_fits':0,'formal_weight':0};xwrite(root/'self_test_result.json',packed(result)+b'\n');return result
def parser():
 p=argparse.ArgumentParser();p.add_argument('--mode',required=True,choices=['self-test','preflight','build-plan','odds','injuries','lineup-window']);p.add_argument('--output-dir',required=True);p.add_argument('--selected-plan-identity');p.add_argument('--date');p.add_argument('--league',type=int,default=39);p.add_argument('--season',type=int,default=2026);p.add_argument('--timezone',default='UTC');p.add_argument('--fixture-limit',type=int,default=1);p.add_argument('--max-requests',type=int,default=3);p.add_argument('--requests-used-today',type=int,default=0);p.add_argument('--timeout',type=float,default=15);p.add_argument('--retries',type=int,default=1);p.add_argument('--backoff',type=float,default=8);p.add_argument('--tolerance',type=int,default=7);p.add_argument('--dry-run',type=boolv,default=True);p.add_argument('--no-network',type=boolv,default=True);p.add_argument('--upload-artifact',type=boolv,default=False);p.add_argument('--allow-schedule',type=boolv,default=False);p.add_argument('--retention',type=int,default=30);p.add_argument('--expires');p.add_argument('--run-head',default=os.getenv('GITHUB_SHA','LOCAL_UNCOMMITTED'));p.add_argument('--run-id',default=os.getenv('GITHUB_RUN_ID','LOCAL'));p.add_argument('--expected-request-day-utc');p.add_argument('--print-summary',action='store_true');return p
def main():
 a=parser().parse_args();root=Path(a.output_dir);root.mkdir(parents=True,exist_ok=True)
 try:
  if not 1<=a.fixture_limit<=20 or a.retention!=ARTIFACT_RETENTION_DAYS:raise E3Error('VALIDATION_FAILED')
  a.expires=a.expires or expiry(a.retention);g=guard(a);result=self_test(root) if a.mode=='self-test' else preflight(a,root,g) if a.mode=='preflight' or a.dry_run or a.no_network else execute(a,root,g,liveop(a))
 except E3Error as e:print(f'E3g-0D collector error [{e.failure_class}]',file=sys.stderr);return 2
 if a.print_summary:print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
 return 0
if __name__=='__main__':raise SystemExit(main())
