#!/usr/bin/env python3
"""Thin production orchestrator; all untrusted controls and digests use e3g0d_runtime."""
from __future__ import annotations
import argparse,json,os,subprocess,sys
from pathlib import Path
from e3g0d_common import E3Error,packed
from e3g0d_runtime import (build_plan_index,create_reservation,final_gate,prepare_final_evidence,
 provider_allowed,resolve_controls,write_github_outputs)

def run(cmd:list[str],capture=False)->subprocess.CompletedProcess[str]:
 return subprocess.run(cmd,text=True,capture_output=capture,check=True)

def exact_head(head:str)->None:
 actual=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
 if actual!=head: raise E3Error('EVIDENCE_HEAD_MISMATCH')

def prepare(env,root:Path)->dict[str,str]:
 c=resolve_controls(env); exact_head(c['evidence_head']); out=dict(c,needs_requests='false',used='0',reservation_id='',reservation_sha256='')
 archive=env['ARCHIVE']; repo=env['GITHUB_REPOSITORY']
 if c['mode']=='status-check':
  run([sys.executable,archive,'status','--repository',repo,'--archive-root',str(root/'status')]);return out
 if c['ok']!='true':
  dest=root/'no-network';dest.mkdir(parents=True,exist_ok=True)
  row={'schema_version':'E3G0D-NO-NETWORK-1.0','deployment_status':'IMPLEMENTED_NOT_LIVE','mode':c['mode'],'request_day_utc':c['request_day'],'target_date_utc':c['target'],'request_attempts':0,'run_head':c['evidence_head'],'evidence_head':c['evidence_head'],'workflow_run_id':c['run_id'],'workflow_run_attempt':c['run_attempt'],'append_only':True,'formal_weight':0}
  (dest/'no_network_receipt.json').write_bytes(packed(row)+b'\n');return out
 if c['mode']!='build-plan':
  args=[sys.executable,archive,'resolve-plan','--repository',repo,'--archive-root',str(root/'plan'),'--target-date-utc',c['target'],'--league',c['league'],'--season',c['season']]
  if c['plan_id']:args+=['--artifact-id',c['plan_id']]
  if c['plan_sha']:args+=['--plan-sha256',c['plan_sha']]
  run(args)
 q=run([sys.executable,archive,'quota-used','--repository',repo,'--archive-root',str(root/'quota'),'--request-day-utc',c['request_day'],'--print-summary'],True)
 used=int(json.loads(q.stdout)['requests_used_today']); assert 0<=used<=90
 r=create_reservation(dict(env,MAXIMUM=c['max'],USED=str(used),HEAD_VALUE=c['evidence_head'],RUN_VALUE=c['run_id'],ATTEMPT_VALUE=c['run_attempt'],REQUEST_DAY=c['request_day'],MODE_VALUE=c['mode'],TARGET=c['target'],EXPIRES=c['expires']),root/'reservation')
 out.update(needs_requests='true',used=str(used),reservation_id=r['row']['reservation_id'],reservation_sha256=r['reservation_sha256']);return out

def collect(env,root:Path)->int:
 if not provider_allowed(env.get('CONTROLS_OK',''),env.get('NEEDS_REQUESTS',''),env.get('RESERVATION_UPLOAD','')): raise E3Error('RESERVATION_REQUIRED')
 exact_head(env['HEAD_VALUE']); args=[sys.executable,env['COLLECTOR'],'--mode',env['MODE'],'--output-dir',str(root/'out'),'--date',env['TARGET'],'--league',env['LEAGUE'],'--season',env['SEASON'],'--fixture-limit',env['LIMIT'],'--max-requests',env['MAXIMUM'],'--requests-used-today',env['USED'],'--dry-run','false','--no-network','false','--upload-artifact','true','--allow-schedule','true','--retention','30','--expires',env['EXPIRES'],'--expected-request-day-utc',env['DAY'],'--run-head',env['HEAD_VALUE'],'--run-id',f"{env['RUN_VALUE']}-attempt-{env['ATTEMPT_VALUE']}" ]
 if env['MODE']!='build-plan':args+=['--selected-plan-identity',str(root/'plan/selected_plan_identity.json')]
 p=subprocess.run(args,text=True); receipts=sorted((root/'out/run_receipts').glob('*.json')) if (root/'out/run_receipts').exists() else []
 if len(receipts)>1:raise E3Error('EVIDENCE_AMBIGUOUS')
 receipt=receipts[0].as_posix() if receipts else ''
 e=dict(env,RECEIPT=receipt,COLLECTOR_OUTCOME='success' if p.returncode==0 else 'failure')
 row=prepare_final_evidence(e,root)
 plan=''
 if receipt:
  plan=str(json.loads(Path(receipt).read_text()).get('plan_sha256') or '')
 write_github_outputs({'receipt':receipt,'plan_sha256':plan,'final_status':row['final_status']},env['GITHUB_OUTPUT'])
 return p.returncode

def main()->int:
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','collect','plan-index','gate']);a=p.parse_args();root=Path(os.environ['RUNNER_TEMP'])
 try:
  if a.command=='prepare':write_github_outputs(prepare(os.environ,root),os.environ['GITHUB_OUTPUT'])
  elif a.command=='collect':return collect(os.environ,root)
  elif a.command=='plan-index':build_plan_index(os.environ,root/'index')
  else:
   passed,reason=final_gate(os.environ)
   if not passed:print(reason,file=sys.stderr);return 2
  return 0
 except (E3Error,OSError,ValueError,KeyError,json.JSONDecodeError,subprocess.CalledProcessError) as exc:
  print(f"E3g-0D workflow job failed [{getattr(exc,'failure_class','VALIDATION_FAILED')}]",file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
