#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / '.github' / 'workflows'
PLAN = ROOT / 'governance' / 'legacy_workflow_migration_plan.json'
ADJ = ROOT / 'governance' / 'post_freeze_workflow_adjudication.json'
LEDGER = ROOT / 'governance' / 'final_workflow_ledger.json'
ARCH = ROOT / 'governance' / 'archive' / 'workflows'
EXPECTED_ACTIVE = {
    '.github/workflows/ci.yml',
    '.github/workflows/forward.yml',
    '.github/workflows/maintenance.yml',
    '.github/workflows/research.yml',
    '.github/workflows/scheduled-data.yml',
    '.github/workflows/football-formal-core-v460.yml',
    '.github/workflows/football-platform-integrity.yml',
    '.github/workflows/football-repository-integrity-v471.yml',
    '.github/workflows/football-v6494-active-research-scope-guard.yml',
    '.github/workflows/football-v6494-current-state-audit.yml',
    '.github/workflows/football-v6482-unified-forward-pipeline.yml',
    '.github/workflows/football-v6492-fresh-challengers.yml',
    '.github/workflows/football-v6495-context-conditioned-selector.yml',
    '.github/workflows/football-v6495-context-materialize.yml',
}
EXPECTED = {
    'active_workflow_count': 14,
    'contents_write_count': 0,
    'git_commit_push_count': 0,
    'direct_main_push_count': 0,
    'persistence_count': 0,
    'push_trigger_count': 6,
    'schedule_trigger_count': 3,
    'missing_concurrency_count': 0,
    'missing_timeout_count': 0,
    'missing_python_reference_count': 0,
    'unknown_count': 0,
}
PY_TOKEN = re.compile(r'(?<![A-Za-z0-9_.-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.py)\b')

def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()

def load_json(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--strict', action='store_true')
    args = ap.parse_args()
    errors: list[str] = []
    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None
        errors.append('PyYAML is required for YAML syntax validation')

    workflows = sorted([*WF.glob('*.yml'), *WF.glob('*.yaml')])
    active = {rel(p) for p in workflows}
    unknown = sorted(active - EXPECTED_ACTIVE)
    missing_expected = sorted(EXPECTED_ACTIVE - active)
    if missing_expected:
        errors.append(f'missing expected active workflows: {missing_expected}')

    metrics = collections.Counter()
    missing_py: set[str] = set()
    per_workflow = []
    for p in workflows:
        text = p.read_text(encoding='utf-8')
        if yaml is not None:
            try:
                doc = yaml.safe_load(text)
                if not isinstance(doc, dict):
                    errors.append(f'YAML root is not mapping: {rel(p)}')
            except Exception as exc:
                errors.append(f'YAML parse failed {rel(p)}: {exc}')
        contents_write = bool(re.search(r'(?m)^\s*contents:\s*write\s*(?:#.*)?$', text))
        persistence = bool(re.search(r'persist_generated_worktree|\bgit\s+(?:commit|push)\b|api\.github\.com/repos/.*/contents', text, re.I))
        git_cp = bool(re.search(r'\bgit\s+(?:commit|push)\b|persist_generated_worktree', text, re.I))
        direct_main = bool(re.search(r'(?i)\bgit\s+push[^\n]*\bmain\b|persist_generated_worktree[^\n]*--branch\s+main|[\"\']branch[\"\']\s*:\s*[\"\']main[\"\']', text))
        push_trigger = bool(re.search(r'(?m)^  push:\s*$', text))
        schedule_trigger = bool(re.search(r'(?m)^  schedule:\s*$', text))
        has_concurrency = bool(re.search(r'(?m)^concurrency:\s*$', text))
        has_timeout = 'timeout-minutes:' in text
        refs = sorted(set(PY_TOKEN.findall(text)))
        missing_here = []
        for token in refs:
            if '*' in token or '${{' in token:
                continue
            q = ROOT / token
            if not q.exists():
                missing_py.add(token); missing_here.append(token)
        metrics['contents_write_count'] += int(contents_write)
        metrics['persistence_count'] += int(persistence)
        metrics['git_commit_push_count'] += int(git_cp)
        metrics['direct_main_push_count'] += int(direct_main)
        metrics['push_trigger_count'] += int(push_trigger)
        metrics['schedule_trigger_count'] += int(schedule_trigger)
        metrics['missing_concurrency_count'] += int(not has_concurrency)
        metrics['missing_timeout_count'] += int(not has_timeout)
        per_workflow.append({'path':rel(p),'contents_write':contents_write,'persistence':persistence,'git_commit_push':git_cp,'direct_main_push':direct_main,'push_trigger':push_trigger,'schedule_trigger':schedule_trigger,'concurrency':has_concurrency,'timeout':has_timeout,'missing_python':missing_here})

    metrics['active_workflow_count'] = len(workflows)
    metrics['missing_python_reference_count'] = len(missing_py)
    metrics['unknown_count'] = len(unknown)

    plan = load_json(PLAN)
    migrations = plan['migrations']
    if len(migrations) != 411:
        errors.append(f'frozen migration count {len(migrations)} != 411')
    disp = collections.Counter(x['disposition'] for x in migrations)
    if dict(disp) != {'CONSOLIDATE':54,'ARCHIVE':348,'KEEP':5,'MANUAL_ONLY':4}:
        errors.append(f'disposition mismatch: {dict(disp)}')
    frozen = {x['source_path']: x for x in migrations}
    archived_by_name: dict[str,list[str]] = collections.defaultdict(list)
    for p in ARCH.rglob('*.yml'):
        archived_by_name[p.name].append(rel(p))
    for p in ARCH.rglob('*.yaml'):
        archived_by_name[p.name].append(rel(p))
    for source, row in frozen.items():
        name = Path(source).name
        if row['disposition'] in {'ARCHIVE','CONSOLIDATE'}:
            if source in active:
                errors.append(f'removed disposition still active: {source}')
            copies = archived_by_name.get(name, [])
            if len(copies) != 1:
                errors.append(f'archive copy count {len(copies)} for {source}: {copies}')
        else:
            if source not in active:
                errors.append(f'retained disposition not active: {source}')
    additions = active - set(frozen)
    adjudicated = {x['path'] for x in load_json(ADJ)['post_freeze_additions']}
    if additions != adjudicated:
        errors.append(f'post-freeze addition mismatch active={sorted(additions)} adjudicated={sorted(adjudicated)}')
    if len(additions) != 5:
        errors.append(f'post-freeze additions {len(additions)} != 5')

    for p in ROOT.joinpath('governance').rglob('*.json'):
        try: load_json(p)
        except Exception as exc: errors.append(f'JSON parse failed {rel(p)}: {exc}')

    ledger = load_json(LEDGER)
    for k,v in EXPECTED.items():
        got = int(metrics[k])
        if got != v: errors.append(f'metric {k}: got {got}, expected {v}')
        if int(ledger['final_static_metrics'][k]) != v: errors.append(f'ledger metric {k} drift')

    result = {
        'status': 'PASS' if not errors else 'FAIL',
        'metrics': {k:int(metrics[k]) for k in EXPECTED},
        'unknown_paths': unknown,
        'missing_python_references': sorted(missing_py),
        'frozen_dispositions': dict(disp),
        'frozen_original_removed_count': sum(1 for x in migrations if x['disposition'] in {'ARCHIVE','CONSOLIDATE'}),
        'post_freeze_addition_count': len(additions),
        'per_workflow': per_workflow,
        'errors': errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and errors: return 2
    return 0 if not errors else 1

if __name__ == '__main__':
    raise SystemExit(main())
