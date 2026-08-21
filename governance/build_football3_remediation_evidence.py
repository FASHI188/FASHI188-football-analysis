from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path
STATUS='GPT_REMEDIATED_PENDING_CODEX_RECHECK'
C072={'verdict':'PILOT_NO_SIGNAL_PARK','formal_weight':0,'baseline_LogLoss':1.8156748128,'candidate_LogLoss':1.8198346208,'delta_LogLoss':0.0041598079,'delta_Brier':0.0008986,'delta_RPS':0.0001791,'bootstrap90_ci':[-0.0003678,0.0086930],'source_wins':'1/4'}
CRITICAL=['football-data/research/football3_core.py','football-data/research/football3_label_access.py','football-data/research/validate_football3_experiment.py','football-data/research/test_football3_core.py','football-data/research/test_football3_label_access.py','football-data/research/test_validate_football3_experiment.py','football-data/research/FOOTBALL3_REMEDIATION_SAFETY_CONTRACT_V3.json','governance/validate_project_continuity.py','governance/test_required_context_coverage.py','governance/repository_governance_audit.py','.github/workflows/football-repository-integrity-v471.yml','.github/workflows/football-platform-integrity.yml','.github/workflows/football-formal-core-v460.yml','.github/workflows/football-state-doc-integrity.yml','.github/workflows/football-engineering-quality-security.yml']
FORMAL_CURRENT=(re.compile(r'(^|/)PROJECT_CURRENT\.md$',re.I),re.compile(r'(^|/)FOOTBALL3_INDEPENDENT_CURRENT\.md$',re.I))
def sha(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1048576),b''):h.update(c)
 return h.hexdigest()
def git(root,*a):return subprocess.check_output(['git',*a],cwd=root,text=True).strip()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',default='.');ap.add_argument('--out',default='_football3_remediation_evidence');ap.add_argument('--base-sha',required=True);ap.add_argument('--test-summary',action='append',default=[]);a=ap.parse_args();root=Path(a.root).resolve();out=(root/a.out).resolve();out.mkdir(parents=True,exist_ok=True);head=git(root,'rev-parse','HEAD')
 raw=git(root,'diff','--name-status',a.base_sha,head).splitlines();entries=[]
 for line in raw:
  parts=line.split('\t');entries.append((parts[0],parts[-1]))
 changed=[p for _,p in entries];fm=[]
 for status,rel in sorted(entries,key=lambda x:x[1]):
  p=root/rel;fm.append({'path':rel,'git_status':status,'size':p.stat().st_size,'sha256':sha(p)} if p.is_file() else {'path':rel,'git_status':status,'deleted':True})
 crit={rel:{'sha256':sha(root/rel),'size':(root/rel).stat().st_size} for rel in CRITICAL}
 tests=[{'path':str(Path(x)),'sha256':sha(Path(x)),'size':Path(x).stat().st_size} for x in a.test_summary]
 formal={'model':sum(p.startswith(('football-data/models/','models/')) for p in changed),'data':sum(p.startswith(('football-data/data/','data/')) for p in changed),'config':sum(p.startswith('football-data/config/') for p in changed),'CURRENT':sum(any(rx.search(p) for rx in FORMAL_CURRENT) for st,p in entries if not st.startswith('D'))}
 payload={'schema':'football3_remediation_evidence_v1','status':STATUS,'checkout_head':head,'evidence_head':head,'run_head':os.environ.get('GITHUB_SHA',head),'workflow':os.environ.get('GITHUB_WORKFLOW'),'workflow_run_id':os.environ.get('GITHUB_RUN_ID'),'workflow_run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),'base_sha':a.base_sha,'critical_files':crit,'changed_file_manifest':fm,'test_summaries':tests,'runtime_counters':{'real_target_label_reads':0,'sealed_confirmation_reads':0,'training_runs':0,'scientific_scoring_runs':0,'provider_requests':0,'repository_secret_accesses':0},'formal_asset_diff':formal,'formal_current_consistency':'UNKNOWN_NOT_AUDITED','scientific_result_unchanged':C072,'acceptance':{'CODEX_PASS':False,'SCIENTIFIC_PASS':False,'ENGINEERING_ACCEPTED':False,'REPOSITORY_GOVERNANCE_PASS':False,'READY':False,'PROMOTED':False}}
 if any(formal.values()):raise SystemExit(f'formal asset diff must remain zero: {formal}')
 (out/'evidence.json').write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':STATUS,'head':head,'formal_asset_diff':formal},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
