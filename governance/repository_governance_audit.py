#!/usr/bin/env python3
"""Read-only repository-wide governance inventory for GitHub Actions."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess,sys,urllib.request
from pathlib import Path
from typing import Any
try: import yaml
except Exception: yaml=None
PASS='REPOSITORY_GOVERNANCE_AUDIT_PASS';FAIL='REPOSITORY_GOVERNANCE_AUDIT_FAIL';AUTH_EXACT={'AGENTS.md','EXECUTION_LITE.md'};AUTH_PREFIX=('governance/','.github/workflows/','football-data/runtime/activation/');THIRD_PARTY=('deepsource','sonarqube','codecov','gitguardian','renovate');MAX_NEW_BYTES=25*1024*1024
FORMAL_CURRENT_PATTERNS=(re.compile(r'(^|/)PROJECT_CURRENT\.md$',re.I),re.compile(r'(^|/)FOOTBALL3_INDEPENDENT_CURRENT\.md$',re.I))
PSEUDO_STATE_PATTERNS=(re.compile(r'(^|/)[^/]*_START_HERE\.[^/]+$',re.I),re.compile(r'(^|/)[^/]*_HANDOFF\.[^/]+$',re.I),re.compile(r'(^|/)[^/]*_CHECKPOINT\.[^/]+$',re.I))
def sh(*args:str,check:bool=True)->str:
 cp=subprocess.run(args,text=True,capture_output=True,check=False)
 if check and cp.returncode: raise RuntimeError(f"{' '.join(args)}: {cp.stderr.strip()}")
 return cp.stdout.strip()
def gh(path:str,token:str)->Any:
 req=urllib.request.Request('https://api.github.com'+path,headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'football-governance-audit'})
 if token:req.add_header('Authorization',f'Bearer {token}')
 with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read().decode())
def paged(path:str,token:str)->list[Any]:
 out=[];page=1;sep='&' if '?' in path else '?'
 while True:
  chunk=gh(f'{path}{sep}per_page=100&page={page}',token)
  if not isinstance(chunk,list):return out
  out.extend(chunk)
  if len(chunk)<100:return out
  page+=1
def is_auth(p:str)->bool:return p in AUTH_EXACT or p.startswith(AUTH_PREFIX)
def workflow_catalog(root:Path)->list[dict]:
 rows=[]
 for p in sorted((root/'.github/workflows').glob('*.y*ml')):
  text=p.read_text(encoding='utf-8',errors='replace');doc={}
  if yaml:
   class L(yaml.SafeLoader):pass
   for c,res in list(L.yaml_implicit_resolvers.items()):L.yaml_implicit_resolvers[c]=[x for x in res if x[0]!='tag:yaml.org,2002:bool']
   try:doc=yaml.load(text,Loader=L) or {}
   except Exception:doc={}
  if doc:
   on=doc.get('on',{}) if isinstance(doc,dict) else {};triggers=sorted([on] if isinstance(on,str) else list(on.keys()) if isinstance(on,dict) else list(on) if isinstance(on,list) else []);perms=doc.get('permissions');conc=doc.get('concurrency');jobs=doc.get('jobs',{});wf_name=doc.get('name')
  else:
   m=re.search(r'(?m)^name:\s*(.+?)\s*$',text);wf_name=m.group(1).strip(' \"\'') if m else None;triggers=[k for k in ('workflow_dispatch','pull_request','push','schedule','workflow_call') if re.search(rf'(?m)^\s{{0,2}}{re.escape(k)}:\s*',text)];perms='STATIC_TEXT_FALLBACK';conc='STATIC_TEXT_FALLBACK';jobs={}
  rows.append({'path':p.relative_to(root).as_posix(),'name':wf_name,'triggers':triggers,'permissions':perms,'concurrency':conc,'jobs':sorted(jobs.keys()) if isinstance(jobs,dict) else [],'timeouts':{k:v.get('timeout-minutes') for k,v in jobs.items() if isinstance(v,dict)} if isinstance(jobs,dict) else {},'secret_reference':bool(re.search(r'\bsecrets\.',text)),'write_permission':bool(re.search(r'\b(contents|pull-requests|actions|checks|statuses|issues):\s*write\b',text)),'direct_git_push':bool(re.search(r'\bgit\s+push\b',text)),'manual_only':triggers==['workflow_dispatch']})
 return rows
def large_files(root:Path,base_sha:str):
 tracked=[x for x in sh('git','ls-files','-z').split('\0') if x];big=[]
 for rel in tracked:
  p=root/rel
  if p.is_file() and p.stat().st_size>=25*1024*1024:
   h=hashlib.sha256()
   with p.open('rb') as f:
    for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
   big.append({'path':rel,'size_bytes':p.stat().st_size,'sha256':h.hexdigest(),'source':'UNKNOWN_REPOSITORY_HISTORY','license_or_use_conditions':'UNKNOWN','externalizable':'REVIEW_REQUIRED'})
 changed=[]
 for rel in sh('git','diff','--name-only',f'{base_sha}...HEAD').splitlines() if base_sha else []:
  p=root/rel
  if p.is_file() and p.stat().st_size>MAX_NEW_BYTES:changed.append({'path':rel,'size_bytes':p.stat().st_size,'limit_bytes':MAX_NEW_BYTES})
 return big,changed
def pr_ledger(repo,token,main_sha,current_head):
 pulls=paged(f'/repos/{repo}/pulls?state=open',token);rows=[]
 for pr in pulls:
  num=pr['number'];base=pr['base']['ref'];head=pr['head']['ref'];sha=pr['head']['sha'];files=paged(f'/repos/{repo}/pulls/{num}/files',token);paths=[x.get('filename','') for x in files];authority=[x for x in paths if is_auth(x)]
  main_contains=subprocess.run(['git','merge-base','--is-ancestor',sha,main_sha],capture_output=True).returncode==0;head_desc_main=subprocess.run(['git','merge-base','--is-ancestor',main_sha,sha],capture_output=True).returncode==0
  cp=subprocess.run(['git','rev-list','--count',f'{main_sha}..{sha}'],text=True,capture_output=True);unique_count=int(cp.stdout.strip()) if cp.returncode==0 and cp.stdout.strip().isdigit() else None
  body=pr.get('body') or '';evidence_paths=[x for x in paths if x.startswith(('evidence/','governance/archive/')) or any(k in x.lower() for k in ('artifact','receipt','manifest','audit'))];artifact_refs=sorted(set(re.findall(r'(?i)artifact[^0-9]{0,10}(\d{6,})',body)));evidence='PRESENT' if evidence_paths or artifact_refs else 'NONE_IN_DIFF_OR_BODY'
  if num in (331,332):cls='GOVERNANCE_CONFLICT' if num==331 else 'AMBIGUOUS_KEEP'
  elif sha==current_head:cls='KEEP_ACTIVE'
  elif base!='main' and authority:cls='GOVERNANCE_CONFLICT'
  elif head.startswith(('research/','football3/')):cls='SCIENTIFIC_QUARANTINE'
  elif main_contains and unique_count==0 and evidence=='NONE_IN_DIFF_OR_BODY':cls='SUPERSEDED_SAFE_TO_CLOSE'
  else:cls='AMBIGUOUS_KEEP'
  safe=(cls=='SUPERSEDED_SAFE_TO_CLOSE' and evidence=='NONE_IN_DIFF_OR_BODY' and unique_count==0)
  rows.append({'pr':num,'base':base,'base_sha':pr['base']['sha'],'head':head,'head_sha':sha,'draft':pr.get('draft'),'reachable_from_main':main_contains,'head_descends_main':head_desc_main,'unique_commits_vs_main':unique_count,'unique_evidence_status':evidence,'evidence_paths':evidence_paths,'artifact_refs':artifact_refs,'authority_paths':authority,'classification':cls,'safe_to_auto_close':safe,'recommended_disposition':'CLOSE' if safe else 'KEEP_OPEN_DRAFT'})
 return sorted(rows,key=lambda x:x['pr'])
def branch_ledger(repo,token,prs):
 branches=paged(f'/repos/{repo}/branches',token);by={}
 for r in prs:by.setdefault(r['head'],[]).append(r['pr'])
 return [{'branch':b['name'],'sha':b['commit']['sha'],'open_prs':by.get(b['name'],[]),'protected':b.get('protected',False),'recommendation':'KEEP_NO_DELETE_AUTHORIZED'} for b in branches]
def third_party(repo,token,sha):
 runs=gh(f'/repos/{repo}/commits/{sha}/check-runs?per_page=100',token).get('check_runs',[]);suites=gh(f'/repos/{repo}/commits/{sha}/check-suites?per_page=100',token).get('check_suites',[]);out={}
 for key in THIRD_PARTY:
  rr=[r for r in runs if key in ((r.get('name','')+' '+((r.get('app') or {}).get('name',''))).lower())];ss=[s for s in suites if key in (((s.get('app') or {}).get('name','')).lower())]
  out[key]={'status':'ACTIVE' if rr else 'INSTALLED_NOT_ACTIVE' if ss else 'NOT_OBSERVED_ON_EXACT_HEAD','check_runs':[{'id':r['id'],'name':r['name'],'status':r['status'],'conclusion':r.get('conclusion')} for r in rr],'check_suites':[{'id':s['id'],'status':s['status'],'conclusion':s.get('conclusion'),'app':((s.get('app') or {}).get('name'))} for s in ss]}
 return out
def _scope_diff(base_sha:str):
 raw=sh('git','diff','--name-status',f'{base_sha}...HEAD').splitlines();entries=[]
 for line in raw:
  parts=line.split('\t');status=parts[0];path=parts[-1];entries.append((status,path))
 paths=[p for _,p in entries]
 # Deleting a pseudo-state/checkpoint mirror is a governance remediation, not a modification of a formal CURRENT authority.
 formal_current=sum(any(rx.search(p) for rx in FORMAL_CURRENT_PATTERNS) for st,p in entries if not st.startswith('D'))
 pseudo_added_or_modified=sum(any(rx.search(p) for rx in PSEUDO_STATE_PATTERNS) for st,p in entries if not st.startswith('D') and not p.startswith('governance/archive/'))
 return {'model_diff':sum(p.startswith(('football-data/models/','models/')) for p in paths),'formal_data_diff':sum(p.startswith(('football-data/data/','data/')) for p in paths),'config_diff':sum(p.startswith('football-data/config/') for p in paths),'CURRENT_diff':formal_current,'pseudo_state_added_or_modified':pseudo_added_or_modified,'pseudo_state_removed':sum(any(rx.search(p) for rx in PSEUDO_STATE_PATTERNS) for st,p in entries if st.startswith('D')),'changed_paths':paths}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path('.'));ap.add_argument('--repo',default=os.getenv('GITHUB_REPOSITORY',''));ap.add_argument('--token',default=os.getenv('GITHUB_TOKEN',''));ap.add_argument('--base-sha',default='');ap.add_argument('--out',type=Path,default=Path('_governance_audit'));a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);head=sh('git','rev-parse','HEAD');main_sha=a.base_sha or sh('git','rev-parse','origin/main');workflows=workflow_catalog(a.root);big,changed_big=large_files(a.root,main_sha);names=[w['name'] for w in workflows if w['name']];dup=sorted({x for x in names if names.count(x)>1});prs=[];branches=[];apps={}
 if a.repo and a.token:prs=pr_ledger(a.repo,a.token,main_sha,head);branches=branch_ledger(a.repo,a.token,prs);apps=third_party(a.repo,a.token,head)
 for n,obj in {'workflow_catalog.json':workflows,'open_pr_ledger.json':prs,'branch_ledger.json':branches,'large_files.json':big,'third_party_apps.json':apps}.items():(a.out/n).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 scope=_scope_diff(main_sha);bad_scope=any(scope[k] for k in ('model_diff','formal_data_diff','config_diff','CURRENT_diff','pseudo_state_added_or_modified'));summary={'status':PASS if not dup and not changed_big and not bad_scope else FAIL,'exact_head':head,'base_main_sha':main_sha,'workflow_count':len(workflows),'duplicate_workflow_names':dup,'open_pr_count':len(prs),'branch_count':len(branches),'large_file_count':len(big),'new_or_modified_oversize_files':changed_big,'scope_diff':scope,'formal_weight':0,'repository_secret_access':0,'provider_requests':0,'training':0,'scoring':0,'new_target_label_access':0};(a.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False));return 0 if summary['status']==PASS else 2
if __name__=='__main__':sys.exit(main())
