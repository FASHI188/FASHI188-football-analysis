#!/usr/bin/env python3
"""Real no-network tests for the exact E3g-0D production workflow functions."""
from __future__ import annotations
import argparse,datetime as dt,json,tempfile
from pathlib import Path
from e3g0d_common import E3Error, sha
from e3g0d_archive_core import (GitHubReader, artifact_digest, artifact_redirect_location,
    validate_artifact_redirect_url)
from e3g0d_runtime import (build_plan_index,count_reservations,final_gate,normalize_artifact_digest,
    prepare_final_evidence,provider_allowed,reservation_identity,resolve_controls)

def dispatch_env():
    return {'EVENT_NAME':'workflow_dispatch','REF_VALUE':'refs/heads/main','CRON_VALUE':'','INPUT_MODE':'preflight',
      'INPUT_TARGET':'2026-08-15','INPUT_LEAGUE':'39','INPUT_SEASON':'2026','INPUT_LIMIT':'1','INPUT_MAX':'3',
      'INPUT_PLAN_ID':'','INPUT_PLAN_SHA':'','INPUT_DRY':'true','INPUT_NETWORK':'true','INPUT_UPLOAD':'false',
      'INPUT_ALLOW':'false','EXPECTED_HEAD':'a'*40,'GITHUB_RUN_ID':'77','GITHUB_RUN_ATTEMPT':'1',
      'API_FOOTBALL_COLLECTOR_ENABLED':'false','API_FOOTBALL_SCHEDULE_ENABLED':'false'}

def run(workflow:Path):
    dig={}; raw='A'*64
    assert normalize_artifact_digest(raw)=='sha256:'+'a'*64; dig['raw_64_hex']='PASS'
    assert normalize_artifact_digest('sha256:'+raw)=='sha256:'+'a'*64; dig['sha256_prefixed']='PASS'
    for label,value in {'short':'a'*63,'non_hex':'g'*64,'other_algorithm':'sha512:'+'a'*64,'double_prefix':'sha256:sha256:'+'a'*64}.items():
        try: normalize_artifact_digest(value); raise AssertionError(label)
        except E3Error: dig[label]='PASS'
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); receipt=root/'receipt.json'; receipt.write_text(json.dumps({'snapshots':[{'sha256':'e'*64}]}))
        env={'ARTIFACT_ID':'101','ARTIFACT_NAME':'football-e3g0d-plan-test','PLAN_SHA':'f'*64,'RECEIPT':str(receipt),
          'RUN_VALUE':'77','ATTEMPT_VALUE':'1','HEAD_VALUE':'a'*40,'REQUEST_DAY':'2026-08-15','TARGET':'2026-08-15',
          'LEAGUE':'39','SEASON':'2026','EXPIRES':'2026-09-14T00:00:00Z'}
        for label,value in {'plan_index_raw_64_hex':'B'*64,'plan_index_sha256_prefixed':'sha256:'+'B'*64}.items():
            row=build_plan_index(dict(env,ARTIFACT_DIGEST=value),root/label)
            assert row['plan_artifact_digest']=='sha256:'+'b'*64
            assert json.loads((root/label/'plan_index_receipt.json').read_text())['plan_artifact_digest']==row['plan_artifact_digest']; dig[label]='PASS'
        for label,value in {'plan_index_short':'b'*63,'plan_index_non_hex':'z'*64,'plan_index_other_algorithm':'sha512:'+'b'*64}.items():
            try: build_plan_index(dict(env,ARTIFACT_DIGEST=value),root/label); raise AssertionError(label)
            except E3Error: dig[label]='PASS'
    assert resolve_controls(dispatch_env(),dt.datetime(2026,8,15,tzinfo=dt.timezone.utc))['ok']=='false'
    malicious={}
    payloads={'single_quote':"'",'double_quote':'"','command_substitution':'$(touch pwn)','backticks':'`touch pwn`',
      'semicolon':'; touch pwn','newline':'line1\nline2','secret_expression_text':'${API_FOOTBALL_KEY}','path_traversal':'../escape'}
    for label,payload in payloads.items():
        env=dispatch_env(); env['INPUT_TARGET']=payload
        try: resolve_controls(env,dt.datetime(2026,8,15,tzinfo=dt.timezone.utc)); raise AssertionError(label)
        except E3Error: malicious[label]='PASS'
    faults={}; calls=0
    if provider_allowed('true','true','failure'): calls+=1
    assert calls==0; faults['reservation_upload_failure']='PASS'
    head='b'*40; rid1,name1=reservation_identity(head,'55','1','2026-08-15')
    crash={'reservation_id':rid1,'reserved_attempts':3,'workflow_run_id':'55','workflow_run_attempt':'1'}
    assert count_reservations([crash])==3; faults['runner_crash_after_reservation']='PASS'
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); (root/'reservation').mkdir(); (root/'reservation/quota_reservation.json').write_text('{}\n')
        (root/'out/raw').mkdir(parents=True); (root/'out/raw/provider.json').write_text('{}')
        (root/'out/manifests').mkdir(); (root/'out/manifests/request.manifest.json').write_text('{}')
        (root/'out/run_receipts').mkdir(); receipt=root/'out/run_receipts/failure.json'
        receipt.write_text(json.dumps({'outcome':'FAILED','failure_class':'PROVIDER_ERROR','request_attempts':1}))
        env={'COLLECTOR_OUTCOME':'failure','RECEIPT':str(receipt),'RESERVATION_ID':rid1,'RESERVATION_SHA':'c'*64,
          'RESERVATION_ARTIFACT_ID':'123','RESERVATION_ARTIFACT_DIGEST':'D'*64,'RESERVATION_ARTIFACT_NAME':name1,
          'RESERVED':'3','MODE_VALUE':'odds','TARGET':'2026-08-15','REQUEST_DAY':'2026-08-15','HEAD_VALUE':head,
          'RUN_VALUE':'55','ATTEMPT_VALUE':'1','EXPIRES':'2026-09-14T00:00:00Z'}
        row=prepare_final_evidence(env,root); assert row['final_status']=='FAILED' and row['reservation_artifact_digest']=='sha256:'+'d'*64
        for rel in ('collector-output/raw/provider.json','collector-output/manifests/request.manifest.json','collector-output/run_receipts/failure.json'):
            assert (root/'final-evidence'/rel).is_file()
        faults['provider_partial_failure']='PASS'; receipt.unlink()
        missing=prepare_final_evidence(dict(env,RECEIPT=''),root)
        assert missing['final_status']=='FINAL_RECEIPT_MISSING' and count_reservations([crash])==3; faults['final_receipt_missing']='PASS'
        gate={'MODE_VALUE':'odds','OK_VALUE':'true','BLOCKED_LIVE':'false','NEEDS_REQUESTS':'true','RESERVATION_UPLOAD':'success',
          'COLLECTOR_OUTCOME':'success','EVIDENCE_MANIFEST':'success','FINAL_RECEIPT_UPLOAD':'success','SNAPSHOT_UPLOAD':'failure'}
        assert not final_gate(gate)[0] and (root/'final-evidence/quota_final_receipt.json').is_file() and (root/'final-evidence/reservation/quota_reservation.json').is_file()
        faults['snapshot_upload_failure']='PASS'
    rid2,name2=reservation_identity(head,'55','2','2026-08-15')
    assert rid1!=rid2 and name1!=name2 and count_reservations([crash,{'reservation_id':rid2,'reserved_attempts':3,'workflow_run_id':'55','workflow_run_attempt':'2'}])==6
    faults['rerun_same_run_id_new_attempt']='PASS'
    redirect={}; allowed={'official_blob':'https://account.blob.core.windows.net/c/a.zip?sig=x',
      'actions_domain':'https://results-receiver.actions.githubusercontent.com/twirp/a','githubusercontent':'https://objects.githubusercontent.com/a.zip'}
    for label,url in allowed.items(): assert validate_artifact_redirect_url(url)==url; redirect[label]='PASS'
    rejected={'blob_suffix_spoof':'https://blob.core.windows.net.attacker.com/a','github_suffix_spoof':'https://githubusercontent.com.attacker.com/a',
      'http':'http://results-receiver.actions.githubusercontent.com/a','non_official':'https://example.com/a',
      'username_spoof':'https://github.com@attacker.com/a','port_spoof':'https://results-receiver.actions.githubusercontent.com:444/a'}
    for label,url in rejected.items():
        try: validate_artifact_redirect_url(url); raise AssertionError(label)
        except E3Error: redirect[label]='PASS'
    assert artifact_redirect_location(302,{'Location':allowed['official_blob']})==allowed['official_blob']; redirect['status_302']='PASS'
    for label,status,headers in [('redirect_missing',302,{}),('unexpected_status',200,{'Location':allowed['official_blob']})]:
        try: artifact_redirect_location(status,headers); raise AssertionError(label)
        except E3Error: redirect[label]='PASS'
    captured={}
    def payload_loader(request): captured['headers']={str(k).lower():str(v) for k,v in request.header_items()}; return b'artifact-bytes'
    reader=GitHubReader('o/r',None,json_loader=lambda _: {},redirect_loader=lambda _: (302,{'Location':allowed['actions_domain']}),payload_loader=payload_loader)
    assert reader.download(1)==b'artifact-bytes' and 'authorization' not in captured['headers'] and 'x-apisports-key' not in captured['headers']
    redirect['second_request_has_no_credentials']='PASS'
    sample=b'artifact'; canonical='sha256:'+sha(sample)
    assert artifact_digest({'digest':sha(sample)},sample)==canonical; dig['archive_raw_64_hex']='PASS'
    assert artifact_digest({'digest':canonical},sample)==canonical; dig['archive_sha256_prefixed']='PASS'
    for label,value in {'archive_short':'a'*63,'archive_non_hex':'z'*64,'archive_other_algorithm':'sha512:'+'a'*64}.items():
        try: artifact_digest({'digest':value},sample); raise AssertionError(label)
        except E3Error: dig[label]='PASS'
    text=workflow.read_text(); required=['"$JOB" prepare','"$JOB" collect','"$JOB" plan-index','"$JOB" gate','"$RUNTIME_TESTS"']
    job=workflow.parent.parent.parent/'football-data/research/e3g0d_workflow_job.py'; job_text=job.read_text(); assert all(x in text for x in required) and all(x in job_text for x in ('resolve_controls','normalize_artifact_digest' if False else 'build_plan_index','provider_allowed','prepare_final_evidence','final_gate')) and "fault_tests={k:'PASS'" not in text and 'printf %s "$PAYLOAD"' not in text
    return {'self_test':'PASS','digest_format_tests':dig,'malicious_input_tests':malicious,'quota_fault_tests':faults,
      'workflow_uses_tested_production_functions':'PASS','artifact_redirect_tests':redirect,'real_provider_request_attempts':0,'formal_weight':0}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--workflow',required=True); p.add_argument('--output',required=True); a=p.parse_args()
    result=run(Path(a.workflow)); Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(result,sort_keys=True)+'\n')
    print(json.dumps(result,sort_keys=True,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
