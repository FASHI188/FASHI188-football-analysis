from pathlib import Path
import re
REQUIRED={'.github/workflows/football-repository-integrity-v471.yml':'repository-integrity','.github/workflows/football-platform-integrity.yml':'platform-integrity','.github/workflows/football-formal-core-v460.yml':'validate-core','.github/workflows/football-state-doc-integrity.yml':'governance-topology-integrity','.github/workflows/football-engineering-quality-security.yml':'Changed-file quality and security guard'}
def check(root:Path):
    for rel,ctx in REQUIRED.items():
        t=(root/rel).read_text(encoding='utf-8')
        if 'pull_request:' not in t or 'branches: [main]' not in t: raise AssertionError(f'{rel}: not all main PRs')
        block=t.split('pull_request:',1)[1].split('push:',1)[0] if 'push:' in t.split('pull_request:',1)[1] else t.split('pull_request:',1)[1].split('permissions:',1)[0]
        if re.search(r'(?m)^\s+paths(?:-ignore)?:',block): raise AssertionError(f'{rel}: paths filter suppresses required context')
        if f'name: {ctx}' not in t and f'  {ctx}:' not in t: raise AssertionError(f'{rel}: missing context {ctx}')
if __name__=='__main__':check(Path(__file__).resolve().parents[1]);print('REQUIRED_CONTEXT_COVERAGE_PASS')
