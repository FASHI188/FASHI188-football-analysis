#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
PLAN=ROOT/'governance/legacy_workflow_migration_plan.json'
SUMMARY={'ci.yml','forward.yml','maintenance.yml','research.yml','scheduled-data.yml'}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--target',choices=sorted(SUMMARY)); p.add_argument('--source'); p.add_argument('--require-archived',action='store_true'); a=p.parse_args()
 plan=json.loads(PLAN.read_text(encoding='utf-8'))
 rows=[x for x in plan['migrations'] if x['disposition']=='CONSOLIDATE']
 if len(rows)!=54: raise SystemExit(f'FAIL consolidate_count={len(rows)} expected=54')
 selected=rows
 if a.target: selected=[x for x in selected if a.target in x['target_workflow'].split('/')]
 if a.source:
  selected=[x for x in selected if x['source_path']==a.source]
  if len(selected)!=1: raise SystemExit(f'FAIL source_mapping_count={len(selected)} source={a.source}')
 missing=[]; syntax=[]; no_archive=[]; active=[]
 archive_root=ROOT/'governance/archive/workflows'
 archived_names={q.name for q in archive_root.rglob('*.yml')}|{q.name for q in archive_root.rglob('*.yaml')}
 for x in selected:
  src=ROOT/x['source_path']
  if src.exists(): active.append(x['source_path'])
  if a.require_archived and src.name not in archived_names: no_archive.append(src.name)
  for dep in x.get('unique_script_dependencies',[]):
   q=ROOT/dep
   if not q.exists(): missing.append(dep); continue
   if q.suffix=='.py':
    try: compile(q.read_text(encoding='utf-8'),str(q),'exec')
    except Exception as e: syntax.append(f'{dep}: {e}')
 for name in SUMMARY:
  q=ROOT/'.github/workflows'/name
  if not q.exists(): missing.append(str(q.relative_to(ROOT)))
 if missing or syntax or no_archive:
  print(json.dumps({'status':'FAIL','missing':sorted(set(missing)),'syntax':syntax,'not_archived':sorted(set(no_archive))},ensure_ascii=False,indent=2)); return 2
 print(json.dumps({'status':'PASS','target':a.target,'source':a.source,'selected':len(selected),'active_legacy_count':len(active),'require_archived':a.require_archived,'owners':sorted({x['target_workflow'] for x in selected})},ensure_ascii=False))
 return 0
if __name__=='__main__': raise SystemExit(main())
