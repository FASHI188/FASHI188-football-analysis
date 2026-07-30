#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASE='bc670923e5dbfb80048d8129d32d502a25b44238'
MAN=ROOT/'governance/archive/workflows/phase2b-batch05/ARCHIVE_MANIFEST.json'
PLAN=ROOT/'governance/legacy_workflow_migration_plan.json'
EXPECTED=25
ACTIVE_AFTER=295

def git(*args:str)->str:
 p=subprocess.run(['git','-C',str(ROOT),*args],text=True,capture_output=True)
 if p.returncode: raise RuntimeError(p.stderr.strip())
 return p.stdout

def blob(rev,path):
 s=git('ls-tree',rev,'--',path).strip().split(None,3)
 if len(s)!=4: raise RuntimeError(f'missing {rev}:{path}')
 return s[2]

def exists(rev,path):
 return subprocess.run(['git','-C',str(ROOT),'cat-file','-e',f'{rev}:{path}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0

def main()->int:
 try:
  m=json.loads(MAN.read_text(encoding='utf-8')); p=json.loads(PLAN.read_text(encoding='utf-8'))
  plan={x['source_path']:x for x in p['migrations']}
  entries=m['entries']
  assert m['base_commit']==BASE and len(entries)==EXPECTED and m['workflow_files_after']==ACTIVE_AFTER
  assert all(m[k]==0 for k in ('model_changes','data_changes','config_changes','current_changes'))
  assert m['main_modified'] is False and m['actions_reenabled'] is False
  for source,sha in entries:
   assert plan[source]['disposition']=='ARCHIVE'
   target='governance/archive/workflows/phase2b-batch05/'+Path(source).name
   assert blob(BASE,source)==sha
   assert not exists('HEAD',source)
   assert exists('HEAD',target) and blob('HEAD',target)==sha
  active=[x for x in git('ls-tree','-r','--name-only','HEAD','--','.github/workflows').splitlines() if x.startswith('.github/workflows/')]
  assert len(active)==ACTIVE_AFTER
  diff=git('diff','--name-status','--find-renames=100%',f'{BASE}..HEAD','--').splitlines()
  ren=[x for x in diff if x.startswith('R100\t')]
  adds=[x for x in diff if x.startswith('A\t')]
  assert len(ren)==EXPECTED and set(adds)=={'A\tgovernance/archive/workflows/phase2b-batch05/ARCHIVE_MANIFEST.json','A\tscripts/governance/validate_phase2b_batch05.py'}
 except Exception as e:
  print(f'FAIL {e}',file=sys.stderr); return 2
 print('PASS phase2b_batch05'); print('archived_workflow_count=25'); print('active_workflow_count=295'); return 0
if __name__=='__main__': raise SystemExit(main())
