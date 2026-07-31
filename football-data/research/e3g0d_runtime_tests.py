#!/usr/bin/env python3
"""No-key tests for exact E3g-0D production workflow paths."""
from __future__ import annotations
import argparse,datetime as dt,inspect,io,json,re,subprocess,tempfile,urllib.parse,zipfile
from pathlib import Path
import e3g0d_api_football_archive_helper_secure as secure_archive
import e3g0d_workflow_job as workflow_job
from e3g0d_archive_core import GitHubReader,artifact_digest,artifact_redirect_location,validate_artifact_redirect_url
from e3g0d_common import E3Error,packed,sha
from e3g0d_runtime import build_plan_index,final_gate,normalize_artifact_digest,prepare_final_evidence,provider_allowed,reservation_identity,resolve_controls

def dispatch_env():
 return {'EVENT_NAME':'workflow_dispatch','REF_VALUE':'refs/heads/main','CRON_VALUE':'','INPUT_MODE':'preflight','INPUT_TARGET':'2026-08-15','INPUT_LEAGUE':'39','INPUT_SEASON':'2026','INPUT_LIMIT':'1','INPUT_MAX':'3','INPUT_PLAN_ID':'','INPUT_PLAN_SHA':'','INPUT_DRY':'true','INPUT_NETWORK':'true','INPUT_UPLOAD':'false','INPUT_ALLOW':'false','EXPECTED_HEAD':'a'*40,'GITHUB_RUN_ID':'77','GITHUB_RUN_ATTEMPT':'1','API_FOOTBALL_COLLECTOR_ENABLED':'false','API_FOOTBALL_SCHEDULE_ENABLED':'false'}
def lineup_env():
 e=dispatch_env();e.update(EVENT_NAME='schedule',CRON_VALUE='*/15 10-22 * * *',API_FOOTBALL_COLLECTOR_ENABLED='true',API_FOOTBALL_SCHEDULE_ENABLED='true',ARCHIVE='football-data/research/e3g0d_api_football_archive_helper_secure.py',GITHUB_REPOSITORY='o/r');return e
def zpack(files):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  for n,v in files.items():z.writestr(n,v)
 return b.getvalue()

def production_quota_test():
 day='2026-08-15';head='b'*40
 rows={10:{'schema_version':secure_archive.legacy.QUOTA_SCHEMA,'request_day_utc':day,'reservation_id':f'{head}:55:1','reserved_attempts':3,'workflow_run_id':'55','workflow_run_attempt':'1','retention_days':30,'append_only':True,'formal_weight':0},150:{'schema_version':secure_archive.legacy.QUOTA_SCHEMA,'request_day_utc':day,'reservation_id':f'{head}:55:2','reserved_attempts':3,'workflow_run_id':'55','workflow_run_attempt':'2','retention_days':30,'append_only':True,'formal_weight':0}}
 payloads={};arts=[]
 for i in range(1,206):
  if i in rows: raw=zpack({'quota_reservation.json':packed(rows[i])});name=f"football-e3g0d-quota-reservation-{day}-{head}-55-attempt-{rows[i]['workflow_run_attempt']}"
  elif i==160: raw=zpack({'quota_final_receipt.json':packed({'reservation_id':rows[150]['reservation_id'],'final_status':'SUCCESS','actual_request_attempts':1})});name=f'football-e3g0d-quota-receipt-{head}-55-attempt-2'
  else: raw=zpack({f'filler-{i}.json':b'{}'});name=f'football-e3g0d-snapshot-{i}'
  payloads[i]=raw;arts.append({'id':i,'name':name,'expired':False,'digest':f'sha256:{sha(raw)}'})
 def load(path):
  q=urllib.parse.urlsplit(path)
  if not q.path.endswith('/actions/artifacts'):raise E3Error('GITHUB_READ_FAILED')
  page=int(urllib.parse.parse_qs(q.query).get('page',['1'])[0]);return {'artifacts':arts[(page-1)*100:page*100]}
 reader=GitHubReader('o/r',None,json_loader=load,download_loader=lambda i:payloads[i])
 production=secure_archive.legacy.main.__globals__['quota_used'];assert production is secure_archive.legacy.quota_used
 result=production(reader,day);ids={x['artifact_id'] for x in result['quota_artifacts']};attempts={x['workflow_run_attempt'] for x in result['quota_artifacts']}
 assert result['requests_used_today']==6 and result['quota_reservation_count']==2 and ids=={10,150} and attempts=={'1','2'} and result['all_pages_read'] is True
 tests={'runner_crash_without_final_receipt_counted':'PASS','rerun_attempts_counted_independently':'PASS','multi_page_reservation_found':'PASS','final_receipt_does_not_refund_reservation':'PASS','missing_final_receipt_does_not_refund_reservation':'PASS'}
 return tests,{'requests_used_today':6,'quota_reservation_count':2,'artifact_pages':3}

def lineup_no_due_test():
 fixed=dt.datetime(2026,8,15,12,0,tzinfo=dt.timezone.utc);due_calls=[];commands=[]
 with tempfile.TemporaryDirectory() as d:
  root=Path(d)
  def runner(cmd,capture=False):
   commands.append(' '.join(cmd))
   if 'resolve-plan' in cmd:
    plan=root/'bundle/plans/plan.json';plan.parent.mkdir(parents=True);plan.write_text(json.dumps({'fixtures':[{'fixture_id':1,'competition_id':39,'season_id':2026,'home_team_id':10,'away_team_id':20,'scheduled_kickoff_utc':'2026-08-15T18:00:00Z'}]}))
    identity=root/'plan/selected_plan_identity.json';identity.parent.mkdir(parents=True);identity.write_text(json.dumps({'selected_plan_path':plan.resolve().as_posix()}));return subprocess.CompletedProcess(cmd,0,stdout='',stderr='')
   if 'quota-used' in cmd:raise AssertionError('quota-used called with no due fixture')
   return subprocess.CompletedProcess(cmd,0,stdout='',stderr='')
  def tracked(fixtures,observed,tolerance):due_calls.append((len(fixtures),tolerance));return workflow_job.due(fixtures,observed,tolerance)
  out=workflow_job.prepare(lineup_env(),root,command_runner=runner,head_checker=lambda _:None,clock=lambda:fixed,due_checker=tracked)
  receipt=json.loads((root/'no-network/no_network_due_receipt.json').read_text());gate=final_gate({'MODE_VALUE':'lineup-window','OK_VALUE':'true','BLOCKED_LIVE':'false','NEEDS_REQUESTS':out['needs_requests'],'NO_NETWORK_UPLOAD':'success'})[0]
  assert len(due_calls)==1 and any('resolve-plan' in x for x in commands) and not any('quota-used' in x for x in commands)
  assert out['needs_requests']=='false' and out['reservation_id']==out['reservation_sha256']=='' and not (root/'reservation').exists()
  assert receipt['schema_version']=='E3G0D-NO-NETWORK-DUE-1.0' and receipt['final_status']=='NO_REQUESTS_DUE' and receipt['request_attempts']==receipt['reservation_count']==0 and all(v==0 for v in receipt['due_window_counts'].values())
  assert not provider_allowed('true',out['needs_requests'],'success') and gate
  return {'due_check_calls':1,'needs_requests':'false','reservation':0,'provider_request_attempts':0,'no_network_due_receipt':'PASS','gate_expected_no_request_success':'PASS'}

def timeout_test(text):
 out={}
 for job,want in [('validate-implementation',20),('collect-forward-snapshots',25)]:
  m=re.search(rf'(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)',text);assert m
  t=re.search(r'(?m)^    timeout-minutes:\s*([0-9]+)\s*$',m.group('body'));assert t and int(t.group(1))==want;out[job]=want
 return out

def run(workflow):
 dig={};raw='A'*64
 assert normalize_artifact_digest(raw)=='sha256:'+'a'*64;dig['raw_64_hex']='PASS'
 assert normalize_artifact_digest('sha256:'+raw)=='sha256:'+'a'*64;dig['sha256_prefixed']='PASS'
 for label,value in {'short':'a'*63,'non_hex':'g'*64,'other_algorithm':'sha512:'+'a'*64,'double_prefix':'sha256:sha256:'+'a'*64}.items():
  try:normalize_artifact_digest(value);raise AssertionError(label)
  except E3Error:dig[label]='PASS'
 with tempfile.TemporaryDirectory() as d:
  root=Path(d);receipt=root/'receipt.json';receipt.write_text(json.dumps({'snapshots':[{'sha256':'e'*64}]}));env={'ARTIFACT_ID':'101','ARTIFACT_NAME':'football-e3g0d-plan-test','PLAN_SHA':'f'*64,'RECEIPT':str(receipt),'RUN_VALUE':'77','ATTEMPT_VALUE':'1','HEAD_VALUE':'a'*40,'REQUEST_DAY':'2026-08-15','TARGET':'2026-08-15','LEAGUE':'39','SEASON':'2026','EXPIRES':'2026-09-14T00:00:00Z'}
  for label,value in {'plan_index_raw_64_hex':'B'*64,'plan_index_sha256_prefixed':'sha256:'+'B'*64}.items():
   row=build_plan_index(dict(env,ARTIFACT_DIGEST=value),root/label);assert row['plan_artifact_digest']=='sha256:'+'b'*64 and json.loads((root/label/'plan_index_receipt.json').read_text())['plan_artifact_digest']==row['plan_artifact_digest'];dig[label]='PASS'
  for label,value in {'plan_index_short':'b'*63,'plan_index_non_hex':'z'*64,'plan_index_other_algorithm':'sha512:'+'b'*64}.items():
   try:build_plan_index(dict(env,ARTIFACT_DIGEST=value),root/label);raise AssertionError(label)
   except E3Error:dig[label]='PASS'
 assert resolve_controls(dispatch_env(),dt.datetime(2026,8,15,tzinfo=dt.timezone.utc))['ok']=='false'
 malicious={}
 for label,payload in {'single_quote':"'",'double_quote':'"','command_substitution':'$(touch pwn)','backticks':'`touch pwn`','semicolon':'; touch pwn','newline':'line1\nline2','secret_expression_text':'${API_FOOTBALL_KEY}','path_traversal':'../escape'}.items():
  e=dispatch_env();e['INPUT_TARGET']=payload
  try:resolve_controls(e,dt.datetime(2026,8,15,tzinfo=dt.timezone.utc));raise AssertionError(label)
  except E3Error:malicious[label]='PASS'
 quota_tests,quota_values=production_quota_test();faults={};call=0
 if provider_allowed('true','true','failure'):call+=1
 assert call==0;faults['reservation_upload_failure']='PASS';assert quota_tests['runner_crash_without_final_receipt_counted']=='PASS';faults['runner_crash_after_reservation']='PASS'
 head='b'*40;rid,name=reservation_identity(head,'55','1','2026-08-15')
 with tempfile.TemporaryDirectory() as d:
  root=Path(d);(root/'reservation').mkdir();(root/'reservation/quota_reservation.json').write_text('{}\n');(root/'out/raw').mkdir(parents=True);(root/'out/raw/provider.json').write_text('{}');(root/'out/manifests').mkdir();(root/'out/manifests/request.manifest.json').write_text('{}');(root/'out/run_receipts').mkdir();receipt=root/'out/run_receipts/failure.json';receipt.write_text(json.dumps({'outcome':'FAILED','failure_class':'PROVIDER_ERROR','request_attempts':1}))
  env={'COLLECTOR_OUTCOME':'failure','RECEIPT':str(receipt),'RESERVATION_ID':rid,'RESERVATION_SHA':'c'*64,'RESERVATION_ARTIFACT_ID':'123','RESERVATION_ARTIFACT_DIGEST':'D'*64,'RESERVATION_ARTIFACT_NAME':name,'RESERVED':'3','MODE_VALUE':'odds','TARGET':'2026-08-15','REQUEST_DAY':'2026-08-15','HEAD_VALUE':head,'RUN_VALUE':'55','ATTEMPT_VALUE':'1','EXPIRES':'2026-09-14T00:00:00Z'}
  row=prepare_final_evidence(env,root);assert row['final_status']=='FAILED' and row['reservation_artifact_digest']=='sha256:'+'d'*64
  for rel in ('collector-output/raw/provider.json','collector-output/manifests/request.manifest.json','collector-output/run_receipts/failure.json'):assert (root/'final-evidence'/rel).is_file()
  faults['provider_partial_failure']='PASS';receipt.unlink();missing=prepare_final_evidence(dict(env,RECEIPT=''),root);assert missing['final_status']=='FINAL_RECEIPT_MISSING' and quota_tests['missing_final_receipt_does_not_refund_reservation']=='PASS';faults['final_receipt_missing']='PASS'
  gate={'MODE_VALUE':'odds','OK_VALUE':'true','BLOCKED_LIVE':'false','NEEDS_REQUESTS':'true','RESERVATION_UPLOAD':'success','COLLECTOR_OUTCOME':'success','EVIDENCE_MANIFEST':'success','FINAL_RECEIPT_UPLOAD':'success','SNAPSHOT_UPLOAD':'failure'};assert not final_gate(gate)[0] and (root/'final-evidence/quota_final_receipt.json').is_file() and (root/'final-evidence/reservation/quota_reservation.json').is_file();faults['snapshot_upload_failure']='PASS'
 assert quota_tests['rerun_attempts_counted_independently']=='PASS';faults['rerun_same_run_id_new_attempt']='PASS'
 redirect={};allowed={'official_blob':'https://account.blob.core.windows.net/c/a.zip?sig=x','actions_domain':'https://results-receiver.actions.githubusercontent.com/twirp/a','githubusercontent':'https://objects.githubusercontent.com/a.zip'}
 for label,url in allowed.items():assert validate_artifact_redirect_url(url)==url;redirect[label]='PASS'
 for label,url in {'blob_suffix_spoof':'https://blob.core.windows.net.attacker.com/a','github_suffix_spoof':'https://githubusercontent.com.attacker.com/a','http':'http://results-receiver.actions.githubusercontent.com/a','non_official':'https://example.com/a','username_spoof':'https://github.com@attacker.com/a','port_spoof':'https://results-receiver.actions.githubusercontent.com:444/a'}.items():
  try:validate_artifact_redirect_url(url);raise AssertionError(label)
  except E3Error:redirect[label]='PASS'
 assert artifact_redirect_location(302,{'Location':allowed['official_blob']})==allowed['official_blob'];redirect['status_302']='PASS'
 for label,status,headers in [('redirect_missing',302,{}),('unexpected_status',200,{'Location':allowed['official_blob']})]:
  try:artifact_redirect_location(status,headers);raise AssertionError(label)
  except E3Error:redirect[label]='PASS'
 captured={}
 def payload_loader(req):captured['headers']={str(k).lower():str(v) for k,v in req.header_items()};return b'artifact-bytes'
 reader=GitHubReader('o/r',None,json_loader=lambda _:{},redirect_loader=lambda _:(302,{'Location':allowed['actions_domain']}),payload_loader=payload_loader);assert reader.download(1)==b'artifact-bytes' and 'authorization' not in captured['headers'] and 'x-apisports-key' not in captured['headers'];redirect['second_request_has_no_credentials']='PASS'
 sample=b'artifact';canonical='sha256:'+sha(sample);assert artifact_digest({'digest':sha(sample)},sample)==canonical;dig['archive_raw_64_hex']='PASS';assert artifact_digest({'digest':canonical},sample)==canonical;dig['archive_sha256_prefixed']='PASS'
 for label,value in {'archive_short':'a'*63,'archive_non_hex':'z'*64,'archive_other_algorithm':'sha512:'+'a'*64}.items():
  try:artifact_digest({'digest':value},sample);raise AssertionError(label)
  except E3Error:dig[label]='PASS'
 text=workflow.read_text();timeouts=timeout_test(text);required=['"$JOB" prepare','"$JOB" collect','"$JOB" plan-index','"$JOB" gate','"$RUNTIME_TESTS"'];job_source=inspect.getsource(workflow_job.prepare);secure_source=inspect.getsource(secure_archive);production=secure_archive.legacy.main.__globals__['quota_used']
 assert production is secure_archive.legacy.quota_used and all(x in text for x in required) and 'e3g0d_api_football_archive_helper_secure.py' in text and '"quota-used"' in job_source and 'legacy.main()' in secure_source and "fault_tests={k:'PASS'" not in text and 'printf %s "$PAYLOAD"' not in text
 return {'self_test':'PASS','digest_format_tests':dig,'malicious_input_tests':malicious,'quota_fault_tests':faults,'production_quota_ledger_tests':quota_tests,'production_quota_ledger_values':quota_values,'production_quota_ledger_binding':'PASS','lineup_no_due_prepare_integration':lineup_no_due_test(),'workflow_timeouts':timeouts,'workflow_uses_tested_production_functions':'PASS','artifact_redirect_tests':redirect,'real_provider_request_attempts':0,'formal_weight':0}

def main():
 p=argparse.ArgumentParser();p.add_argument('--workflow',required=True);p.add_argument('--output',required=True);a=p.parse_args();result=run(Path(a.workflow));out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,sort_keys=True)+'\n');print(json.dumps(result,sort_keys=True,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
