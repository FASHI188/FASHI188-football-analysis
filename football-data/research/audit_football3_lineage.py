from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT_SHA='e3e73c998020beef585cc459a69ea5b73b44ddb3'
QUARANTINE_PREFIXES=('c073','c074','c075','c076','c077')
SCIENCE_PATHS=('football-data/research','.github/workflows')


class LineageError(RuntimeError):
    pass


def run(*args:str,check:bool=True)->subprocess.CompletedProcess[str]:
    return subprocess.run(['git',*args],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=check)


def git(*args:str)->str:
    return run(*args).stdout.strip()


def is_ancestor(older:str,newer:str)->bool:
    return run('merge-base','--is-ancestor',older,newer,check=False).returncode==0


def refs_for(prefix:str)->list[str]:
    out=git('for-each-ref','--format=%(refname)',f'refs/remotes/origin/research/{prefix}*')
    return [x.strip() for x in out.splitlines() if x.strip()]


def changed_science_paths(sha:str)->list[str]:
    out=git('diff-tree','--no-commit-id','--name-only','-r',sha,'--',*SCIENCE_PATHS)
    return [x for x in out.splitlines() if x.strip()]


def patch_id(sha:str)->str|None:
    if not changed_science_paths(sha):
        return None
    show=subprocess.Popen(['git','show','--pretty=format:','--binary',sha,'--',*SCIENCE_PATHS],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert show.stdout is not None
    p=subprocess.run(['git','patch-id','--stable'],stdin=show.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    show.stdout.close(); show.wait()
    if show.returncode!=0 or p.returncode!=0:
        raise LineageError(f'patch-id failed for {sha}')
    line=p.stdout.strip()
    return line.split()[0] if line else None


def commits_since(base:str,tip:str)->list[str]:
    out=git('rev-list',tip,f'^{base}')
    return [x for x in out.splitlines() if x]


def main()->int:
    blockers=[]; refs=[]; direct=[]; patch_matches=[]
    if not is_ancestor(ROOT_SHA,'HEAD'):
        blockers.append('HEAD is outside immutable C072-C root lineage')

    quarantine_commits:set[str]=set()
    for prefix in QUARANTINE_PREFIXES:
        found=refs_for(prefix); refs.extend(found)
        for ref in found:
            base=git('merge-base',ROOT_SHA,ref)
            for sha in commits_since(base,ref):
                quarantine_commits.add(sha)
                if is_ancestor(sha,'HEAD'):
                    direct.append({'sha':sha,'ref':ref,'subject':git('show','-s','--format=%s',sha)})
    if not refs:
        blockers.append('quarantine refs C073-C077 unavailable; lineage audit cannot certify isolation')
    if direct:
        blockers.append(f'quarantined commit ancestry detected in HEAD: {len(direct)} commit(s)')

    # Stable patch-id comparison catches cherry-picks whose commit SHA changed.
    qpatch:dict[str,list[str]]={}
    for sha in sorted(quarantine_commits):
        pid=patch_id(sha)
        if pid:
            qpatch.setdefault(pid,[]).append(sha)
    if is_ancestor(ROOT_SHA,'HEAD'):
        for hsha in commits_since(ROOT_SHA,'HEAD'):
            if hsha in quarantine_commits:
                continue
            pid=patch_id(hsha)
            if pid and pid in qpatch:
                patch_matches.append({
                    'head_sha':hsha,
                    'head_subject':git('show','-s','--format=%s',hsha),
                    'quarantine_shas':qpatch[pid],
                    'patch_id':pid,
                })
    if patch_matches:
        blockers.append(f'quarantined scientific patch-id overlap/cherry-pick detected: {len(patch_matches)} commit(s)')

    out={
        'status':'FOOTBALL3_LINEAGE_AUDIT_PASS' if not blockers else 'FOOTBALL3_LINEAGE_AUDIT_BLOCK',
        'root_sha':ROOT_SHA,
        'quarantine_refs_checked':refs,
        'direct_quarantine_ancestry':direct,
        'quarantine_patch_matches':patch_matches,
        'blockers':blockers,
        'real_target_labels_opened':0,
        'sealed_pools_opened':0,
    }
    Path('football-data/research/football3_lineage_audit_summary.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,indent=2))
    return 0 if not blockers else 2


if __name__=='__main__':
    raise SystemExit(main())
