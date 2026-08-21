from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path

SCIENCE_DIR = Path('football-data/research')
CONTRACT_TEMPLATE = SCIENCE_DIR / 'FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json'
# Exact allow-list only. Prefix-based exemptions are forbidden because a scientific
# executable could otherwise be renamed audit_*/test_*/validate_* to bypass V2 binding.
EXEMPT_EXACT = {
    'football-data/research/football3_core.py',
    'football-data/research/run_football3_synthetic_prelabel_smoke.py',
    'football-data/research/audit_football3_execution_surface.py',
    'football-data/research/audit_football3_changed_scientific_files.py',
    'football-data/research/audit_football3_lineage.py',
    'football-data/research/audit_football3_pr_scope.py',
    'football-data/research/validate_football3_experiment.py',
    'football-data/research/validate_football3_research_policy_v3.py',
    'football-data/research/test_football3_core.py',
    'football-data/research/test_validate_football3_experiment.py',
    # Pure engineering HDA aggregation layer: no experiment contract, target labels,
    # training, or scoring runner. These paths are intentionally exact, not prefixes.
    'football-data/research/football3_hda.py',
    'football-data/research/test_football3_hda.py',
    'football-data/research/run_football3_hda_zero_label_audit.py',
}
SCIENTIFIC_CODE_PREFIXES = ('football-data/', 'scripts/')
BLOCKED_EXECUTABLE_SUFFIXES = {'.ipynb', '.sh', '.r', '.R', '.js', '.ts', '.ps1', '.bat', '.cmd'}
ZERO_LABEL_HDA_PATH = 'football-data/research/football3_hda.py'
ZERO_LABEL_HDA_MARKER = 'HDA_AGGREGATION_ONLY_NO_TARGET_LABEL_SCORING'
FORBIDDEN_ZERO_LABEL_FUNCTIONS = {'score_hda_probabilities', 'draw_classification_metrics'}


class GuardError(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], text=True).strip()


def changed_files(base: str, head: str) -> list[str]:
    out = git('diff', '--name-only', f'{base}...{head}')
    return [x.strip() for x in out.splitlines() if x.strip()]


def _string_constant(path: Path, constant_name: str) -> str | None:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for n in tree.body:
        if not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        if not any(isinstance(t, ast.Name) and t.id == constant_name for t in targets):
            continue
        v = n.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
    return None


def contract_constant(path: Path) -> str | None:
    return _string_constant(path, 'FOOTBALL3_EXPERIMENT_CONTRACT')


def helper_contract_constant(path: Path) -> str | None:
    return _string_constant(path, 'FOOTBALL3_EXPERIMENT_HELPER_FOR')


def zero_label_hda_blockers(path: Path) -> list[str]:
    blockers: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except SyntaxError as e:
        return [f'{path}: syntax error: {e}']
    marker = _string_constant(path, 'FOOTBALL3_ZERO_LABEL_ENGINEERING_SURFACE')
    if marker != ZERO_LABEL_HDA_MARKER:
        blockers.append(f'{path}: missing zero-label surface marker {ZERO_LABEL_HDA_MARKER}')
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FORBIDDEN_ZERO_LABEL_FUNCTIONS:
            blockers.append(f'{path}: forbidden target-label scoring function defined: {node.name}')
    return blockers


def is_infra_python(path: str) -> bool:
    return path in EXEMPT_EXACT


def _safe_contract_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute() or '..' in p.parts or not str(p).startswith('football-data/research/'):
        raise GuardError(f'contract path must be repo-relative under football-data/research: {raw}')
    return p


def active_v2_contracts() -> list[Path]:
    out = []
    for p in SCIENCE_DIR.rglob('*.json'):
        if p == CONTRACT_TEMPLATE:
            continue
        try:
            obj = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get('schema_version') == 2 and obj.get('project_id') == 'football3':
            out.append(p)
    return sorted(out)


def all_contract_runners() -> dict[Path, list[Path]]:
    mapping: dict[Path, list[Path]] = {}
    # Scoring runners are authority-bearing and must live under football-data/research.
    for p in SCIENCE_DIR.rglob('*.py'):
        try:
            cp = contract_constant(p)
        except SyntaxError:
            continue
        if not cp:
            continue
        try:
            cpath = _safe_contract_path(cp)
        except GuardError:
            continue
        mapping.setdefault(cpath, []).append(p)
    return mapping


def run_preflight(runner: Path, contract: Path) -> tuple[int, str]:
    r = subprocess.run([
        'python', 'football-data/research/validate_football3_experiment.py',
        '--contract', str(contract), '--runner', str(runner)
    ], text=True, capture_output=True)
    msg = (r.stdout + '\n' + r.stderr)[-1600:]
    return r.returncode, msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--head', default='HEAD')
    a = ap.parse_args()
    files = changed_files(a.base, a.head)
    checked = []
    blockers = []
    helpers = []

    active = set(active_v2_contracts())
    runner_map = all_contract_runners()

    for f in files:
        p = Path(f)
        if not p.exists():
            continue

        # HDA is exempt from experiment binding only while it remains a strictly
        # zero-label aggregation surface. This check must run before EXEMPT_EXACT.
        if f == ZERO_LABEL_HDA_PATH:
            blockers.extend(zero_label_hda_blockers(p))

        # A football3 branch may not create an alternate workflow name to execute
        # science outside the football3 workflow audit surface.
        if f.startswith('.github/workflows/'):
            if not p.name.startswith('football3-'):
                blockers.append(f'{f}: football3 branch may modify only football3-* workflows')
            continue

        if not f.startswith(SCIENTIFIC_CODE_PREFIXES):
            continue
        if p.suffix in BLOCKED_EXECUTABLE_SUFFIXES:
            blockers.append(f'{f}: alternate executable scientific surface is not allowed under V2; migrate to reviewed Python')
            continue
        if p.suffix != '.py' or is_infra_python(f):
            continue

        try:
            cp = contract_constant(p)
            hp = helper_contract_constant(p)
        except SyntaxError as e:
            blockers.append(f'{f}: syntax error: {e}')
            continue
        if cp and hp:
            blockers.append(f'{f}: cannot be both scoring runner and experiment helper')
            continue
        if cp:
            if not f.startswith('football-data/research/'):
                blockers.append(f'{f}: scoring runner must live under football-data/research')
                continue
            try:
                cpath = _safe_contract_path(cp)
            except GuardError as e:
                blockers.append(f'{f}: {e}')
                continue
            if cpath not in active:
                blockers.append(f'{f}: scoring runner binds missing/non-V2 active contract {cpath}')
        elif hp:
            try:
                cpath = _safe_contract_path(hp)
            except GuardError as e:
                blockers.append(f'{f}: {e}')
                continue
            if cpath not in active:
                blockers.append(f'{f}: helper binds missing/non-V2 active contract {cpath}')
            else:
                helpers.append({'helper': f, 'contract': str(cpath)})
        else:
            blockers.append(f'{f}: changed executable Python under football-data/scripts must declare FOOTBALL3_EXPERIMENT_CONTRACT or FOOTBALL3_EXPERIMENT_HELPER_FOR')

    # Revalidate every active V2 contract on every football3 scientific PR. This closes
    # contract-only/audit-artifact-only edits even when the scoring runner is unchanged.
    for cpath in sorted(active):
        runners = runner_map.get(cpath, [])
        if not runners:
            blockers.append(f'{cpath}: active V2 contract has no runner declaring it')
            continue
        if len(runners) != 1:
            blockers.append(f'{cpath}: active V2 contract must have exactly one scoring runner, found {len(runners)}')
        for runner in runners:
            rc, msg = run_preflight(runner, cpath)
            checked.append({'runner': str(runner), 'contract': str(cpath), 'preflight_returncode': rc})
            if rc != 0:
                blockers.append(f'{runner}: V2 preflight failed for {cpath}: {msg}')

    out = {
        'status': 'PASS' if not blockers else 'BLOCK',
        'changed_files': files,
        'active_v2_contract_count': len(active),
        'scientific_runners_checked': checked,
        'experiment_helpers_bound': helpers,
        'blockers': blockers,
    }
    print(json.dumps(out, indent=2))
    if blockers:
        raise SystemExit(2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
