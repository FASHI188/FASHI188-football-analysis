from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT_SHA='e3e73c998020beef585cc459a69ea5b73b44ddb3'
SEED_PREFIXES=('c073','c074','c075','c076','c077')
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


def all_research_refs()->list[str]:
    out=git('for-each-ref','--format=%(refname)','refs/remotes/origin/research/*')
    return [x.strip() for x in out.splitlines() if x.strip()]


def ref_tip(ref:str)->str:
    return git('rev-parse',ref)


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


def commits_not_in_root(ref:str)->set[str]:
    """Commits reachable from ref but not from the immutable football3 root.

    This is the correct quarantine patch universe: it excludes legitimate shared
    pre-root ancestry (for example C071) while retaining old-project commits that
    are scientifically outside football3, even when the two branches forked before
    C072-C.
    """
    out=git('rev-list',ref,f'^{ROOT_SHA}')
    return {x for x in out.splitlines() if x}


def main()->int:
    blockers=[]; direct=[]; patch_matches=[]
    if not is_ancestor(ROOT_SHA,'HEAD'):
        blockers.append('HEAD is outside immutable C072-C root lineage')

    seed_refs=[]
    for prefix in SEED_PREFIXES:
        seed_refs.extend(refs_for(prefix))
    seed_refs=sorted(set(seed_refs))
    if not seed_refs:
        blockers.append('C073-C077 seed refs unavailable; lineage audit cannot certify isolation')

    seed_tips={ref:ref_tip(ref) for ref in seed_refs}

    # A later research branch is quarantined only when a C073-C077 seed *tip* is
    # genuinely its ancestor. Shared older ancestry is NOT sufficient. This keeps
    # legitimate earlier football3 history such as C071 out of quarantine while
    # still capturing true descendants such as C078/C079 and future renamed refs.
    derived_parent_seeds:dict[str,list[str]]={}
    for ref in all_research_refs():
        if ref in seed_refs:
            continue
        parents=[sref for sref,stip in seed_tips.items() if is_ancestor(stip,ref)]
        if parents:
            derived_parent_seeds[ref]=sorted(parents)

    derived_refs=sorted(derived_parent_seeds)
    quarantined_refs=sorted(set(seed_refs)|set(derived_refs))
    if not quarantined_refs:
        blockers.append('no quarantined/derived research refs resolved')

    # Quarantine only commits that are not already part of the immutable football3
    # root history. This prevents common ancestors from being mislabeled as isolated
    # old-project science.
    quarantine_commits:set[str]=set()
    commit_source_ref:dict[str,str]={}
    for ref in quarantined_refs:
        for sha in commits_not_in_root(ref):
            quarantine_commits.add(sha)
            commit_source_ref.setdefault(sha,ref)

    for sha in sorted(quarantine_commits):
        if is_ancestor(sha,'HEAD'):
            direct.append({'sha':sha,'ref':commit_source_ref.get(sha,''),'subject':git('show','-s','--format=%s',sha)})
    if direct:
        blockers.append(f'quarantined/derived commit ancestry detected in HEAD: {len(direct)} commit(s)')

    # Stable patch-id comparison catches cherry-picks/rebases whose SHA changed.
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
        blockers.append(f'quarantined/derived scientific patch-id overlap/cherry-pick detected: {len(patch_matches)} commit(s)')

    out={
        'status':'FOOTBALL3_LINEAGE_AUDIT_PASS' if not blockers else 'FOOTBALL3_LINEAGE_AUDIT_BLOCK',
        'root_sha':ROOT_SHA,
        'seed_quarantine_refs':seed_refs,
        'seed_quarantine_ref_count':len(seed_refs),
        'derived_quarantine_refs':derived_refs,
        'derived_quarantine_ref_count':len(derived_refs),
        'derived_parent_seed_refs':derived_parent_seeds,
        'quarantined_unique_commit_count':len(quarantine_commits),
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
