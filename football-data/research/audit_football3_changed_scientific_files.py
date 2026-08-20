from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path

SCIENCE_DIR = Path('football-data/research')
CONTRACT_TEMPLATE = SCIENCE_DIR / 'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'
EXEMPT_EXACT = {
    'football-data/research/football3_core.py',
    'football-data/research/run_football3_synthetic_prelabel_smoke.py',
    'football-data/research/audit_football3_execution_surface.py',
    'football-data/research/audit_football3_changed_scientific_files.py',
    'football-data/research/audit_football3_lineage.py',
    'football-data/research/audit_football3_pr_scope.py',
}
EXEMPT_PREFIXES = ('test_', 'validate_', 'audit_')
BLOCKED_EXECUTABLE_SUFFIXES = {'.ipynb', '.sh', '.r', '.R'}


class GuardError(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def changed_files(base: str, head: str) -> list[str]:
    out = git('diff', '--name-only', f'{base}...{head}')
    return [x.strip() for x in out.splitlines() if x.strip()]


def _string_constant(path: Path, constant_name: str) -> str | None:
    tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    for n in tree.body:
        if not isinstance(n,(ast.Assign,ast.AnnAssign)):
            continue
        targets=n.targets if isinstance(n,ast.Assign) else [n.target]
        if not any(isinstance(t,ast.Name) and t.id==constant_name for t in targets):
            continue
        v=n.value
        if isinstance(v,ast.Constant) and isinstance(v.value,str):
            return v.value
    return None


def contract_constant(path: Path) -> str | None:
    return _string_constant(path, 'FOOTBALL3_EXPERIMENT_CONTRACT')


def helper_contract_constant(path: Path) -> str | None:
    return _string_constant(path, 'FOOTBALL3_EXPERIMENT_HELPER_FOR')


def is_infra_python(path: str) -> bool:
    if path in EXEMPT_EXACT:
        return True
    name=Path(path).name
    return path.startswith('football-data/research/') and any(name.startswith(x) for x in EXEMPT_PREFIXES)


def _safe_contract_path(raw: str) -> Path:
    p=Path(raw)
    if p.is_absolute() or '..' in p.parts or not str(p).startswith('football-data/research/'):
        raise GuardError(f'contract path must be repo-relative under football-data/research: {raw}')
    return p


def active_v2_contracts() -> list[Path]:
    out=[]
    for p in SCIENCE_DIR.rglob('*.json'):
        if p == CONTRACT_TEMPLATE:
            continue
        try:
            obj=json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(obj,dict) and obj.get('schema_version')==2 and obj.get('project_id')=='football3':
            out.append(p)
    return sorted(out)


def all_contract_runners() -> dict[Path,list[Path]]:
    mapping: dict[Path,list[Path]]={}
    for p in SCIENCE_DIR.rglob('*.py'):
        try:
            cp=contract_constant(p)
        except SyntaxError:
            continue
        if not cp:
            continue
        try:
            cpath=_safe_contract_path(cp)
        except GuardError:
            continue
        mapping.setdefault(cpath,[]).append(p)
    return mapping


def run_preflight(runner: Path, contract: Path) -> tuple[int,str]:
    r=subprocess.run([
        'python','football-data/research/validate_football3_experiment.py',
        '--contract',str(contract),'--runner',str(runner)
    ],text=True,capture_output=True)
    msg=(r.stdout+'\n'+r.stderr)[-1600:]
    return r.returncode,msg


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--base',required=True)
    ap.add_argument('--head',default='HEAD')
    a=ap.parse_args()
    files=changed_files(a.base,a.head)
    checked=[]; blockers=[]; helpers=[]

    active=set(active_v2_contracts())
    runner_map=all_contract_runners()

    # Changed football3 research executable surfaces are fail-closed. A helper may exist,
    # but it must explicitly bind itself to an active V2 contract.
    for f in files:
        if not f.startswith('football-data/research/'):
            continue
        p=Path(f)
        if not p.exists():
            continue
        if p.suffix in BLOCKED_EXECUTABLE_SUFFIXES:
            blockers.append(f'{f}: alternate executable scientific surface is not allowed under V2; migrate to reviewed Python runner')
            continue
        if p.suffix != '.py' or is_infra_python(f):
            continue
        try:
            cp=contract_constant(p)
            hp=helper_contract_constant(p)
        except SyntaxError as e:
            blockers.append(f'{f}: syntax error: {e}')
            continue
        if cp and hp:
            blockers.append(f'{f}: cannot be both scoring runner and experiment helper')
            continue
        if cp:
            try:
                cpath=_safe_contract_path(cp)
            except GuardError as e:
                blockers.append(f'{f}: {e}')
                continue
            if cpath not in active:
                blockers.append(f'{f}: scoring runner binds missing/non-V2 active contract {cpath}')
        elif hp:
            try:
                cpath=_safe_contract_path(hp)
            except GuardError as e:
                blockers.append(f'{f}: {e}')
                continue
            if cpath not in active:
                blockers.append(f'{f}: helper binds missing/non-V2 active contract {cpath}')
            else:
                helpers.append({'helper':f,'contract':str(cpath)})
        else:
            blockers.append(f'{f}: changed football3 research Python must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR')

    # Revalidate every active V2 contract on every football3 scientific PR. This closes
    # the contract-only/audit-artifact-only edit bypass: an unchanged runner is still checked.
    for cpath in sorted(active):
        runners=runner_map.get(cpath,[])
        if not runners:
            blockers.append(f'{cpath}: active V2 contract has no runner declaring it')
            continue
        for runner in runners:
            rc,msg=run_preflight(runner,cpath)
            checked.append({'runner':str(runner),'contract':str(cpath),'preflight_returncode':rc})
            if rc!=0:
                blockers.append(f'{runner}: V2 preflight failed for {cpath}: {msg}')

    out={
        'status':'PASS' if not blockers else 'BLOCK',
        'changed_files':files,
        'active_v2_contract_count':len(active),
        'scientific_runners_checked':checked,
        'experiment_helpers_bound':helpers,
        'blockers':blockers,
    }
    print(json.dumps(out,indent=2))
    if blockers:
        raise SystemExit(2)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
