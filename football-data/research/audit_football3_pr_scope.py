from __future__ import annotations

import argparse
import json
import os
import subprocess

PROTECTED_PREFIXES=(
    'football-data/research/FOOTBALL3_',
    'football-data/research/football3_core.py',
    'football-data/research/validate_football3_',
    'football-data/research/audit_football3_',
    'football-data/research/run_football3_',
    'football-data/research/test_football3_',
    '.github/workflows/football3-',
)
# Shared cross-project registry is intentionally not football3-exclusive.
SHARED_ALLOWED={'football-data/research/FOOTBALL_GLOBAL_CONSUMPTION_REGISTRY_V1.json'}


def git(*args:str)->str:
    return subprocess.check_output(['git',*args],text=True).strip()


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--base',required=True); ap.add_argument('--head',default='HEAD'); a=ap.parse_args()
    head_ref=(os.environ.get('GITHUB_HEAD_REF') or os.environ.get('GITHUB_REF_NAME') or '').strip()
    changed=[x for x in git('diff','--name-only',f'{a.base}...{a.head}').splitlines() if x.strip()]
    protected=[f for f in changed if f not in SHARED_ALLOWED and any(f.startswith(p) for p in PROTECTED_PREFIXES)]
    blockers=[]
    if head_ref and not head_ref.startswith('football3/') and protected:
        blockers.append(f'non-football3 branch {head_ref!r} modifies football3-owned authority/execution files')
    out={'status':'PASS' if not blockers else 'BLOCK','head_ref':head_ref,'changed_files':changed,'protected_football3_files':protected,'blockers':blockers}
    print(json.dumps(out,indent=2))
    return 0 if not blockers else 2


if __name__=='__main__':
    raise SystemExit(main())
