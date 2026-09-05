#!/usr/bin/env python3
"""Fail closed when a non-main-based PR edits repository-wide authority surfaces."""
from __future__ import annotations
import argparse, json, os, subprocess, sys

PASS="PR_AUTHORITY_SCOPE_PASS"; FAIL="BLOCKED_PR_AUTHORITY_SCOPE"
EXACT_FILES={"AGENTS.md","EXECUTION_LITE.md"}
PREFIXES=("governance/",)
WORKFLOW_AUTHORITY_PREFIX=".github/workflows/"
FORMAL_ENTRY_PREFIXES=("football-data/runtime/activation/",)

def is_authority_path(path:str)->bool:
    p=path.replace('\\','/')
    if p.startswith('./'): p=p[2:]
    return p in EXACT_FILES or p.startswith(WORKFLOW_AUTHORITY_PREFIX) or p.startswith(PREFIXES) or p.startswith(FORMAL_ENTRY_PREFIXES)

def evaluate(base_ref:str, changed:list[str])->dict:
    touched=sorted(p for p in changed if is_authority_path(p))
    ok=(not touched) or base_ref=="main"
    return {"status":PASS if ok else FAIL,"base_ref":base_ref,"authority_paths":touched}

def git_changed(base_sha:str)->list[str]:
    cp=subprocess.run(["git","diff","--name-only",f"{base_sha}...HEAD"],text=True,capture_output=True,check=True)
    return [x.strip() for x in cp.stdout.splitlines() if x.strip()]

def self_test()->int:
    cases=[
      ("main",["governance/x.py"],True),
      ("research/stack",["governance/x.py"],False),
      ("research/stack",["AGENTS.md"],False),
      ("football3/x",[".github/workflows/football-state-doc-integrity.yml"],False),
      ("research/stack",["football-data/runtime/activation/x.py"],False),
      ("research/stack",["football-data/research/model.py"],True),
    ]
    bad=[]
    for i,(base,paths,expected) in enumerate(cases,1):
        got=evaluate(base,paths)["status"]==PASS
        print(("PASS" if got==expected else "FAIL"),i,base,paths)
        if got!=expected: bad.append(i)
    print(json.dumps({"terminal":"PASS" if not bad else "FAIL","failed":bad}))
    return 1 if bad else 0

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--base-ref",default=os.getenv("PR_BASE_REF","")); ap.add_argument("--base-sha",default=os.getenv("PR_BASE_SHA","")); ap.add_argument("--changed-file",action="append",default=[]); ap.add_argument("--self-test",action="store_true"); a=ap.parse_args()
    if a.self_test:return self_test()
    changed=a.changed_file or (git_changed(a.base_sha) if a.base_sha else [])
    result=evaluate(a.base_ref,changed); print(json.dumps(result,ensure_ascii=False)); return 0 if result["status"]==PASS else 2
if __name__=="__main__":sys.exit(main())
