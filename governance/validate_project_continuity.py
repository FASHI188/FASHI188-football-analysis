#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re,subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
PASS='GOVERNANCE_TOPOLOGY_INTEGRITY_PASS'; FAIL='BLOCKED_GOVERNANCE_TOPOLOGY_INTEGRITY'
AIRTABLE_MARKER='CONTROL_MARKER: AIRTABLE_CURRENT_STATE_ONLY'; FORMAL_MARKER='FORMAL_MARKER: FORMAL_CURRENT_WHEN_REQUIRED'; AUTH_MARKER='AUTH_MARKER: CURRENT_USER_COMMAND_REQUIRED'; MIRROR_MARKER='MIRROR_MARKER: NO_DYNAMIC_STATE_MIRRORS'; CURRENT_STATE_TEXT='Airtable《当前状态》'
TEXT_SUFFIXES={'.md','.txt','.json'}; HISTORICAL_PREFIXES=('governance/archive/','evidence/manifests/')
FORBIDDEN_NAME_PATTERNS=(re.compile(r'(^|/)PROJECT_CURRENT\.md$',re.I),re.compile(r'(^|/)FOOTBALL3_INDEPENDENT_CURRENT\.md$',re.I),re.compile(r'(^|/)[^/]*_START_HERE\.[^/]+$',re.I),re.compile(r'(^|/)[^/]*_HANDOFF\.[^/]+$',re.I),re.compile(r'(^|/)[^/]*_CHECKPOINT\.[^/]+$',re.I))
DYNAMIC_KEYS={'current_pr','current_head','current_run','current_job','current_artifact','current_state','next_step','unique_next_step','authorization_source','authorization_authority','project_state_authority','task_selection_authority','continuation_authority','authoritative'}
MANIFEST_SCHEMA_ALLOWLIST={'football3_label_identity_manifest_v1','football3_global_fixture_registry_v1','football3_temporal_fold_manifest_v1','football3_sealed_pool_manifest_v1','football3_remediation_evidence_v1','repository_governance_audit_v1'}
@dataclass(frozen=True)
class Decision: status:str; reasons:tuple[str,...]=()
def _dynamic(x):
    if isinstance(x,dict):
        for k,v in x.items():
            if str(k).strip().casefold() in DYNAMIC_KEYS and v not in (None,'',False,0,[],{}): return True
            if _dynamic(v): return True
    if isinstance(x,list): return any(_dynamic(v) for v in x)
    return False
def _changed(root,rel,base):
    if not base:return True
    return subprocess.run(['git','diff','--quiet',base,'--',rel],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
def scan_repository(root:Path,base_ref:str|None=None):
    reasons=[]
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES: continue
        rel=p.relative_to(root).as_posix()
        if rel.startswith('.git/'):continue
        historical=rel.startswith(HISTORICAL_PREFIXES)
        if any(rx.search(rel) for rx in FORBIDDEN_NAME_PATTERNS) and not historical:
            reasons.append(f'forbidden dynamic-state mirror path: {rel}');continue
        if p.suffix.lower()=='.json' and rel.startswith('football-data/manifests/'):
            if not _changed(root,rel,base_ref):continue
            try:payload=json.loads(p.read_text(encoding='utf-8'))
            except Exception: reasons.append(f'new/modified manifest invalid JSON: {rel}');continue
            schema=payload.get('schema') or payload.get('schema_version')
            if str(schema) not in MANIFEST_SCHEMA_ALLOWLIST:reasons.append(f'new/modified manifest schema not allowlisted: {rel} schema={schema!r}')
            if _dynamic(payload):reasons.append(f'new/modified manifest contains live state/continuation/authorization semantics: {rel}')
    return sorted(set(reasons))
def validate_root(root:Path,base_ref:str|None=None):
    reasons=[]
    req={'AGENTS.md':(AIRTABLE_MARKER,FORMAL_MARKER,AUTH_MARKER,MIRROR_MARKER,CURRENT_STATE_TEXT),'EXECUTION_LITE.md':(AIRTABLE_MARKER,AUTH_MARKER,MIRROR_MARKER,CURRENT_STATE_TEXT)}
    for rel,markers in req.items():
        p=root/rel
        if not p.is_file(): reasons.append(f'missing file: {rel}');continue
        t=p.read_text(encoding='utf-8',errors='replace')
        reasons += [f'{rel}: missing {m}' for m in markers if m not in t]
    reasons+=scan_repository(root,base_ref)
    return Decision(FAIL,tuple(sorted(set(reasons)))) if reasons else Decision(PASS)
def _fixture(root):
    (root/'AGENTS.md').write_text('\n'.join((AIRTABLE_MARKER,FORMAL_MARKER,AUTH_MARKER,MIRROR_MARKER,CURRENT_STATE_TEXT)),encoding='utf-8')
    (root/'EXECUTION_LITE.md').write_text('\n'.join((AIRTABLE_MARKER,AUTH_MARKER,MIRROR_MARKER,CURRENT_STATE_TEXT)),encoding='utf-8')
def run_self_test():
    failures=[]
    with TemporaryDirectory() as tmp:
        root=Path(tmp);_fixture(root)
        if validate_root(root).status!=PASS:failures.append('positive')
        for rel in ('notes/current_season/FOOTBALL3_INDEPENDENT_CURRENT.md','notes/current_roster/PROJECT_CURRENT.md'):
            p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fake',encoding='utf-8')
            if validate_root(root).status!=FAIL:failures.append(rel)
            p.unlink()
        p=root/'football-data/manifests/fake.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps({'schema_version':'unknown','current_pr':332,'next_step':'merge'}),encoding='utf-8')
        if validate_root(root).status!=FAIL:failures.append('manifest')
    print(json.dumps({'status':FAIL if failures else PASS,'failures':failures},ensure_ascii=False));return 2 if failures else 0
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',default='.');ap.add_argument('--base-ref');ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:return run_self_test()
    d=validate_root(Path(a.root).resolve(),a.base_ref);print(json.dumps({'status':d.status,'reasons':list(d.reasons)},ensure_ascii=False,indent=2));return 0 if d.status==PASS else 2
if __name__=='__main__':raise SystemExit(main())
