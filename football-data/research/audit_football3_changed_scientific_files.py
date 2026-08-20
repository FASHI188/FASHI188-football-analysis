from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

SCIENCE_DIR = Path('football-data/research')
EXEMPT_INFRA = {
    'football-data/research/run_football3_synthetic_prelabel_smoke.py',
    'football-data/research/audit_football3_execution_surface.py',
    'football-data/research/audit_football3_changed_scientific_files.py',
}


class GuardError(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def changed_files(base: str, head: str) -> list[str]:
    out = git('diff', '--name-only', f'{base}...{head}')
    return [x.strip() for x in out.splitlines() if x.strip()]


def contract_constant(path: Path) -> str | None:
    tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    for n in tree.body:
        if not isinstance(n,(ast.Assign,ast.AnnAssign)):
            continue
        targets=n.targets if isinstance(n,ast.Assign) else [n.target]
        if not any(isinstance(t,ast.Name) and t.id=='FOOTBALL3_EXPERIMENT_CONTRACT' for t in targets):
            continue
        v=n.value
        if isinstance(v,ast.Constant) and isinstance(v.value,str):
            return v.value
    return None


def is_scientific_runner(path: str) -> bool:
    name=Path(path).name
    return path.startswith('football-data/research/') and name.endswith('.py') and (
        name.startswith('run_') or name.startswith('evaluate_')
    ) and path not in EXEMPT_INFRA


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--base',required=True)
    ap.add_argument('--head',default='HEAD')
    a=ap.parse_args()
    files=changed_files(a.base,a.head)
    checked=[]; blockers=[]
    for f in files:
        if not is_scientific_runner(f):
            continue
        p=Path(f)
        if not p.exists():
            continue
        txt=p.read_text(encoding='utf-8')
        # Existing historical scripts are not retroactively mutated just because a governance PR touches another file.
        # But any changed/new scientific runner must migrate to the V1 execution standard.
        if 'football3_core' not in txt:
            blockers.append(f'{f}: changed scientific runner does not use football3_core')
            continue
        cp=contract_constant(p)
        if not cp:
            blockers.append(f'{f}: missing FOOTBALL3_EXPERIMENT_CONTRACT constant')
            continue
        cpath=Path(cp)
        if not cpath.exists():
            blockers.append(f'{f}: declared contract does not exist: {cp}')
            continue
        # Run exact contract/runner validator, not a second hand-written interpretation.
        r=subprocess.run([
            'python','football-data/research/validate_football3_experiment.py',
            '--contract',str(cpath),'--runner',str(p)
        ],text=True,capture_output=True)
        if r.returncode!=0:
            blockers.append(f'{f}: preflight failed: {r.stdout[-800:]} {r.stderr[-800:]}')
        checked.append({'runner':f,'contract':cp,'preflight_returncode':r.returncode})

    out={'status':'PASS' if not blockers else 'BLOCK','changed_files':files,'scientific_runners_checked':checked,'blockers':blockers}
    print(json.dumps(out,indent=2))
    if blockers:
        raise SystemExit(2)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
