#!/usr/bin/env python3
from __future__ import annotations
import json,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[2]; BASE='5de76e7ca49c31df723f789e0a87ab58bbd022a9'; N=25; AFTER=195; B='phase2b-batch09'
M=R/f'governance/archive/workflows/{B}/ARCHIVE_MANIFEST.json'; P=R/'governance/legacy_workflow_migration_plan.json'
def g(*a):
 p=subprocess.run(['git','-C',str(R),*a],text=True,capture_output=True)
 if p.returncode: raise RuntimeError(p.stderr.strip())
 return p.stdout
def ex(rev,path): return subprocess.run(['git','-C',str(R),'cat-file','-e',f'{rev}:{path}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
def bs(rev,path): return g('ls-tree',rev,'--',path).split()[2]
def main():
 try:
  m=json.loads(M.read_text()); plan={x['source_path']:x for x in json.loads(P.read_text())['migrations']}; assert m['base_commit']==BASE and len(m['entries'])==N and m['workflow_files_after']==AFTER
  assert all(m[k]==0 for k in ('model_changes','data_changes','config_changes','current_changes')) and not m['main_modified'] and not m['actions_reenabled']
  for s,h in m['entries']:
   assert plan[s]['disposition']=='ARCHIVE'; assert not plan[s]['unique_capability'].startswith('FORMAL/GOVERNANCE'); a=f'governance/archive/workflows/{B}/'+Path(s).name; assert bs(BASE,s)==h and not ex('HEAD',s) and ex('HEAD',a) and bs('HEAD',a)==h
  active=[x for x in g('ls-tree','-r','--name-only','HEAD','--','.github/workflows').splitlines() if x.startswith('.github/workflows/')]; assert len(active)==AFTER
 except Exception as e: print('FAIL',e,file=sys.stderr); return 2
 print('PASS phase2b_batch09'); return 0
if __name__=='__main__': raise SystemExit(main())
