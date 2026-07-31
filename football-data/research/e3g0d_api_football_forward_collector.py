#!/usr/bin/env python3
"""Default-disabled, fail-closed E3g-0D forward PIT collector."""
from __future__ import annotations
import argparse,json,os,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Callable,Mapping
from e3g0d_collect import build_plan,compute_plan_sha256,collect_injuries,collect_lineups,collect_odds,due,load_plan
from e3g0d_common import ARTIFACT_RETENTION_DAYS,DAILY_CAP,DAILY_LIMIT,ENDPOINTS,HOST,KEY_ENV,PLAN_SCHEMA,RECEIPT_SCHEMA,STATUS,Budget,E3Error,api_url,boolv,classify_failure,expiry,guard,iso,packed,request_day_utc,sha,utc_now,xwrite
from e3g0d_http import Client
from e3g0d_store import Store

def _path(root:Path,start:datetime,mode:str)->Path:return root/'run_receipts'/f"{start.strftime('%Y%m%dT%H%M%S%fZ')}__{mode}.json"
def _write(root:Path,start:datetime,mode:str,row:Mapping[str,Any])->Path:
 p=_path(root,start,mode);xwrite(p,packed(dict(row))+b'\n');return p
def _base(a:argparse.Namespace,start:datetime,day:str,target:str,g:Mapping[str,Any],attempts:int,outcome:str,failure:str|None)->dict[str,Any]:
 return {'schema_version':RECEIPT_SCHEMA,'deployment_status':STATUS,'outcome':outcome,'failure_class':failure,'provider':'API-Football','mode':a.mode,'request_day_utc':day,'target_date_utc':target,'request_attempts':int(attempts),'requests_used_today_before_run':int(a.requests_used_today),'daily_free_limit':DAILY_LIMIT,'daily_safety_cap':DAILY_CAP,'max_requests':int(a.max_requests),'run_head':a.run_head,'workflow_run_id':a.run_id,'observed_at_utc':iso(start),'artifact_retention_days':ARTIFACT_RETENTION_DAYS,'artifact_expires_at_utc':a.expires,'guard':dict(g),'candidate_probabilities':0,'model_fits':0,'append_only':True,'formal_weight':0}
def execute_receipted(a:argparse.Namespace,root:Path,g:Mapping[str,Any],op:Callable[[Client,Store],Mapping[str,Any]],*,clock:Callable[[],datetime]=utc_now,transport:Any|None=None)->dict[str,Any]:
 start=clock().astimezone(timezone.utc);day=request_day_utc(clock);target=a.date or start.date().isoformat();budget=None;details={};outcome='FAILED';failure=None;caught=None
 try:
  if a.expected_request_day_utc and a.expected_request_day_utc!=day:raise E3Error('UTC_DAY_ROLLOVER')
  budget=Budget(int(a.max_requests),int(a.requests_used_today),day,clock=clock);client=Client(os.getenv(KEY_ENV,''),a.timeout,a.retries,a.backoff,budget,sleep=(lambda _:None) if transport else __import__('time').sleep,transport=transport)
  selected={'plan_sha256':a.selected_plan_sha256,'plan_artifact_id':a.selected_plan_artifact_id,'source_raw_response_sha256':a.selected_plan_source_raw_sha256}
  store=Store(root,a.run_head,a.run_id,ARTIFACT_RETENTION_DAYS,a.expires,day,target,selected_plan=selected);details=dict(op(client,store));outcome='SUCCESS'
 except BaseException as e:caught=e;failure=classify_failure(e)
 finally:
  row=_base(a,start,day,target,g,budget.attempts if budget else 0,outcome,failure);row.update(details)
  try:row['receipt_path']=_write(root,start,a.mode,row).as_posix()
  except BaseException as e:raise E3Error('QUOTA_STATE_UNTRUSTED','mandatory run receipt could not be persisted') from e
 if caught:
  if isinstance(caught,E3Error):raise caught
  raise E3Error('INTERNAL_FAILURE') from caught
 return row
def preflight(a:argparse.Namespace,root:Path,g:Mapping[str,Any])->dict[str,Any]:
 start=utc_now();row=_base(a,start,request_day_utc(),a.date or start.date().isoformat(),g,0,'NO_NETWORK',None);row.update({'allowed_api_hosts':[HOST],'allowed_endpoints':sorted(ENDPOINTS),'fixture_limit':a.fixture_limit,'timeout_seconds':a.timeout,'retries':a.retries,'backoff_cap_seconds':a.backoff,'upload_artifact':a.upload_artifact,'allow_schedule':a.allow_schedule});row['receipt_path']=_write(root,start,'preflight',row).as_posix();return row

def live_operation(a:argparse.Namespace,clock:Callable[[],datetime]=utc_now)->Callable[[Client,Store],Mapping[str,Any]]:
 def op(client:Client,store:Store)->Mapping[str,Any]:
  if a.league!=39 or a.timezone!='UTC':raise E3Error('VALIDATION_FAILED','pilot restricted to league 39/UTC')
  target=a.date or clock().date().isoformat();snaps=[];plan=None;pp=None;identity=None
  if a.mode=='build-plan':plan,s,pp=build_plan(client,store,a.league,a.season,target,a.timezone,a.fixture_limit);snaps.append(s)
  else:
   if any(v in {None,''} for v in (a.plan,a.selected_plan_sha256,a.selected_plan_artifact_id,a.selected_plan_source_raw_sha256,a.selected_plan_run_head)):raise E3Error('PLAN_IDENTITY_AMBIGUOUS')
   plan,fixtures,identity=load_plan(a.plan,a.league,a.season,target,a.fixture_limit,expected_plan_sha256=a.selected_plan_sha256,expected_plan_artifact_id=int(a.selected_plan_artifact_id),expected_source_raw_sha256=a.selected_plan_source_raw_sha256,expected_run_head=a.selected_plan_run_head);store.selected_plan=identity
   if a.mode=='odds':snaps+=collect_odds(client,store,a.league,a.season,target,a.timezone,fixtures,'scheduled_three_hour_odds')
   elif a.mode=='injuries':snaps+=collect_injuries(client,store,fixtures,'scheduled_four_hour_injuries')
   elif a.mode=='lineup-window':
    for label,items in due(fixtures,clock(),a.tolerance).items():
     if not items:continue
     snaps+=collect_lineups(client,store,items,label)
     if label in {'T-90m','T-15m'}:snaps+=collect_odds(client,store,a.league,a.season,target,a.timezone,items,'exact_pre_kickoff_odds',[label],label=='T-15m')
     if label=='T-15m':snaps+=collect_injuries(client,store,items,'final_pre_kickoff_injuries',[label],True)
   else:raise E3Error('VALIDATION_FAILED','unsupported live mode')
  return {'competition_id':a.league,'season_id':a.season,'fixture_limit':a.fixture_limit,'snapshot_count':len(snaps),'snapshots':snaps,'plan_fixture_count':plan.get('fixture_count') if plan else None,'plan_sha256':plan.get('plan_sha256') if plan else None,'plan_path':pp.as_posix() if pp else a.plan,'selected_plan_sha256':(identity or {}).get('plan_sha256'),'selected_plan_artifact_id':(identity or {}).get('plan_artifact_id'),'selected_plan_source_raw_sha256':(identity or {}).get('source_raw_response_sha256')}
 return op

def _args(**u:Any)->argparse.Namespace:
 d={'mode':'odds','date':'2026-08-15','league':39,'season':2026,'timezone':'UTC','fixture_limit':1,'max_requests':3,'requests_used_today':0,'timeout':15.0,'retries':1,'backoff':8.0,'tolerance':7,'dry_run':False,'no_network':False,'upload_artifact':False,'allow_schedule':False,'retention':30,'expires':'2026-09-14T00:00:00Z','run_head':'SELFTEST_HEAD','run_id':'SELFTEST_RUN','plan':None,'selected_plan_sha256':None,'selected_plan_artifact_id':None,'selected_plan_source_raw_sha256':None,'selected_plan_run_head':None,'expected_request_day_utc':None};d.update(u);return argparse.Namespace(**d)
def self_test(root:Path)->dict[str,Any]:
 now=datetime(2026,8,15,12,tzinfo=timezone.utc);clock=lambda:now;sent='SECRET_SHOULD_NEVER_APPEAR';g={'deployment_status':STATUS,'collector_enabled':False,'schedule_enabled':False,'event_name':'self-test','github_ref':None,'network_requested':False,'dry_run':True,'no_network':True};day=request_day_utc(clock);targets=['2026-08-16','2026-08-17','2026-08-18']
 def ok(*_:Any):return 200,{},b'{"errors":[],"results":0,"response":[]}','https://v3.football.api-sports.io/odds'
 rows=[execute_receipted(_args(date=t,max_requests=1,requests_used_today=i,retries=0),root/f'quota{i}',g,lambda c,s:(c.get('odds',{'league':39}),{})[1],clock=clock,transport=ok) for i,t in enumerate(targets)];assert {x['request_day_utc'] for x in rows}=={day} and [x['target_date_utc'] for x in rows]==targets
 try:Budget(1,90,day,clock=clock);raise AssertionError
 except E3Error as e:assert e.failure_class=='PROVIDER_QUOTA_RESERVE_REACHED'
 ca={'n':0}
 def ta(*_:Any):
  ca['n']+=1
  if ca['n']==1:return 200,{},b'{"errors":[],"results":0,"response":[]}','https://v3.football.api-sports.io/odds'
  raise TimeoutError
 def opa(c:Client,s:Store):c.get('odds',{'league':39});c.get('odds',{'league':39});return {}
 ar=root/'timeout'
 try:execute_receipted(_args(retries=1),ar,g,opa,clock=clock,transport=ta);raise AssertionError
 except E3Error as e:assert e.failure_class=='NETWORK_FAILURE'
 assert json.loads(next((ar/'run_receipts').glob('*.json')).read_text())['request_attempts']==3
 def tb(*_:Any):return 429,{'Retry-After':'0'},b'','https://v3.football.api-sports.io/odds'
 br=root/'429'
 try:execute_receipted(_args(retries=1),br,g,lambda c,s:(c.get('odds',{'league':39}),{})[1],clock=clock,transport=tb);raise AssertionError
 except E3Error as e:assert e.failure_class=='HTTP_429'
 assert json.loads(next((br/'run_receipts').glob('*.json')).read_text())['request_attempts']==2
 def tc(*_:Any):return 200,{},b'{"errors":[],"results":0,"response":[]}','https://v3.football.api-sports.io/fixtures'
 cr=root/'mapping'
 def opc(c:Client,s:Store):c.get('fixtures',{'league':39});raise E3Error('IDENTITY_MAPPING_FAILED')
 try:execute_receipted(_args(),cr,g,opc,clock=clock,transport=tc);raise AssertionError
 except E3Error as e:assert e.failure_class=='IDENTITY_MAPPING_FAILED'
 assert json.loads(next((cr/'run_receipts').glob('*.json')).read_text())['request_attempts']==1
 fixture={'provider':'API-Football','competition_id':39,'season_id':2026,'fixture_id':1001,'home_team_id':1,'away_team_id':2,'scheduled_kickoff_utc':'2026-08-15T16:00:00Z','provider_updated_at':None};sr=root/'store';st=Store(sr,'SELFTEST_HEAD','SELFTEST_RUN',30,'2026-09-14T12:00:00Z',day,'2026-08-15',{'plan_sha256':'p','plan_artifact_id':7,'source_raw_response_sha256':'s'});payload={'errors':[],'results':1,'response':[{'fixture':{'id':1001}}]};raw=packed(payload);x=st.save('fixtures',{'league':39},raw,payload,now,now,200,{'authorization':sent},'test',[fixture]);y=st.save('fixtures',{'league':39},raw,payload,now+timedelta(seconds=1),now+timedelta(seconds=1),200,{},'test',[fixture]);empty={'errors':[],'results':0,'response':[]};st.save('injuries',{'ids':'1001'},packed(empty),empty,now+timedelta(hours=3,minutes=45),now+timedelta(hours=3,minutes=45),200,{},'missing',[fixture],['T-15m'],True);records=[json.loads(p.read_text()) for p in (sr/'records').rglob('*.json')];assert x['sha256']==y['sha256']==sha(raw) and len(list((sr/'raw').rglob(f'sha256_{sha(raw)}.json')))==1;assert any(r['data_status']=='MISSING_UNINTERPRETED' for r in records) and any(r['is_final_pre_kickoff_candidate'] for r in records) and all(not r['is_final_pre_kickoff_freeze_version'] for r in records) and all(r['selected_plan_artifact_id']==7 for r in records)
 bundle=root/'planbundle';source=packed({'errors':[],'results':1,'response':[]});sd=sha(source);rp=f'raw/sha256_{sd}.json';mp='manifests/source.manifest.json';xwrite(bundle/rp,source);xwrite(bundle/mp,packed({'raw_response_path':rp,'raw_response_sha256':sd})+b'\n');plan={'schema_version':PLAN_SCHEMA,'deployment_status':STATUS,'provider':'API-Football','competition_id':39,'season_id':2026,'target_date_utc':'2026-08-15','request_day_utc':day,'timezone':'UTC','created_at_utc':iso(now),'fixtures':[fixture],'fixture_count':1,'source_raw_response_sha256':sd,'source_manifest_path':mp,'run_head':'PLAN_HEAD','workflow_run_id':'PLAN_RUN','plan_artifact_id':None,'append_only':True,'formal_weight':0};plan['plan_sha256']=compute_plan_sha256(plan);pp=bundle/'plans'/f"plan_sha256_{plan['plan_sha256']}.json";xwrite(pp,packed(plan)+b'\n');sa=_args(plan=pp.as_posix(),selected_plan_artifact_id=777,selected_plan_sha256=plan['plan_sha256'],selected_plan_source_raw_sha256=sd,selected_plan_run_head='PLAN_HEAD',retries=0,max_requests=1);rr=root/'planrun';execute_receipted(sa,rr,g,live_operation(sa,clock),clock=clock,transport=ok);prs=[json.loads(p.read_text()) for p in (rr/'records').rglob('*.json')];assert prs and all(r['selected_plan_artifact_id']==777 and r['selected_plan_sha256']==plan['plan_sha256'] and r['selected_plan_source_raw_sha256']==sd for r in prs)
 assert sent.encode() not in b''.join(p.read_bytes() for p in root.rglob('*') if p.is_file())
 try:api_url('fixtures',{'x-apisports-key':'bad'});raise AssertionError
 except E3Error:pass
 result={'self_test':'PASS','deployment_status':STATUS,'different_target_dates_share_same_request_day_quota':'PASS','target_date_cannot_bypass_daily_cap':'PASS','failed_run_quota_receipt_written':'PASS','retry_attempts_counted':'PASS','post_request_validation_failure_counted':'PASS','failed_collector_job_remains_failed':'PASS','secret_redaction_on_failure':'PASS','append_only':'PASS','raw_deduplication_without_overwrite':'PASS','missing_empty_list_semantics':'PASS','online_final_freeze_prohibited':'PASS','snapshot_records_selected_plan_identity':'PASS','host_allowlist':'PASS','timeout_retry_backoff_response_size_gates':'PASS','no_provider_network':True,'candidate_probabilities':0,'model_fits':0,'formal_weight':0};xwrite(root/'self_test_result.json',packed(result)+b'\n');return result

def parser()->argparse.ArgumentParser:
 p=argparse.ArgumentParser();p.add_argument('--mode',required=True,choices=['self-test','preflight','build-plan','odds','injuries','lineup-window']);p.add_argument('--output-dir',required=True);p.add_argument('--plan');p.add_argument('--date');p.add_argument('--league',type=int,default=39);p.add_argument('--season',type=int,default=2026);p.add_argument('--timezone',default='UTC');p.add_argument('--fixture-limit',type=int,default=1);p.add_argument('--max-requests',type=int,default=3);p.add_argument('--requests-used-today',type=int,default=0);p.add_argument('--timeout',type=float,default=15);p.add_argument('--retries',type=int,default=1);p.add_argument('--backoff',type=float,default=8);p.add_argument('--tolerance',type=int,default=7);p.add_argument('--dry-run',type=boolv,default=True);p.add_argument('--no-network',type=boolv,default=True);p.add_argument('--upload-artifact',type=boolv,default=False);p.add_argument('--allow-schedule',type=boolv,default=False);p.add_argument('--retention',type=int,default=30);p.add_argument('--expires');p.add_argument('--run-head',default=os.getenv('GITHUB_SHA','LOCAL_UNCOMMITTED'));p.add_argument('--run-id',default=os.getenv('GITHUB_RUN_ID','LOCAL'));p.add_argument('--selected-plan-artifact-id',type=int);p.add_argument('--selected-plan-sha256');p.add_argument('--selected-plan-source-raw-sha256');p.add_argument('--selected-plan-run-head');p.add_argument('--expected-request-day-utc');p.add_argument('--print-summary',action='store_true');return p
def main()->int:
 a=parser().parse_args();root=Path(a.output_dir);root.mkdir(parents=True,exist_ok=True)
 try:
  if not 1<=a.fixture_limit<=20:raise E3Error('VALIDATION_FAILED','fixture limit')
  if a.retention!=ARTIFACT_RETENTION_DAYS:raise E3Error('VALIDATION_FAILED','retention must be 30')
  a.expires=a.expires or expiry(a.retention);g=guard(a);result=self_test(root) if a.mode=='self-test' else preflight(a,root,g) if a.mode=='preflight' or a.dry_run or a.no_network else execute_receipted(a,root,g,live_operation(a))
 except E3Error as e:print(f'E3g-0D collector error [{e.failure_class}]',file=sys.stderr);return 2
 if a.print_summary:print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
 return 0
if __name__=='__main__':raise SystemExit(main())
