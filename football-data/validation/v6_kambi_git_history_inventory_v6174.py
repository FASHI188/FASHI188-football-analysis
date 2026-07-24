#!/usr/bin/env python3
"""V6.17.4 audit historical Kambi raw snapshots retained in git history.

Research-only. It does not resurrect or mutate formal evidence. It inventories immutable historical
blobs and reports whether >=100 distinct events have a genuine pre-kickoff snapshot and are old
enough to have a result by audit time.
"""
from __future__ import annotations
import json, subprocess, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine'; VALID=ROOT/'validation'
for p in (ENGINE,VALID):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from platform_core import parse_iso_datetime
import v6_multiline_research_forward_v6853 as v6853
OUT=ROOT/'manifests'/'v6_kambi_git_history_inventory_v6174_status.json'
PREFIX='football-data/evidence/direct_provider_probes/kambi/'

def run(*args):
    return subprocess.run(args,cwd=ROOT,text=True,capture_output=True,check=True).stdout

def main():
    now=datetime.now(timezone.utc).replace(microsecond=0); counts=Counter(); latest_path_commit={}
    text=run('git','log','--all','--format=COMMIT %H','--name-only','--',PREFIX)
    current=None
    for line in text.splitlines():
        if line.startswith('COMMIT '): current=line.split()[1]; continue
        p=line.strip()
        if current and p.startswith(PREFIX) and p.endswith('.json') and p not in latest_path_commit:
            latest_path_commit[p]=current
    counts['distinct_paths_in_history']=len(latest_path_commit)
    by_event={}; read_fail=0
    for i,(path,sha) in enumerate(latest_path_commit.items(),1):
        try:
            raw=run('git','show',f'{sha}:{path}'); env=json.loads(raw); ident=env.get('list_event_identity') or {}
            eid=str(env.get('event_id') or ident.get('id') or '').strip(); obs=parse_iso_datetime(str(env.get('observed_at_utc') or ''),'observed'); ko=parse_iso_datetime(str(ident.get('start') or ''),'kickoff')
            if not eid: counts['missing_event_id']+=1; continue
            if obs>=ko: counts['not_prematch']+=1; continue
            comp=v6853.COMP_MAP.get(str(ident.get('group') or '').strip())
            if not comp: counts['competition_unmapped']+=1; continue
            row={'event_id':eid,'path':path,'commit':sha,'observed_at_utc':obs.isoformat(),'kickoff_utc':ko.isoformat(),'competition_id':comp,'home':ident.get('homeName'),'away':ident.get('awayName')}
            prev=by_event.get(eid)
            if prev is None or obs < parse_iso_datetime(prev['observed_at_utc'],'prev_obs'): by_event[eid]=row
        except Exception:
            read_fail+=1
    counts['blob_read_failures']=read_fail; counts['distinct_valid_prematch_events']=len(by_event)
    old=[r for r in by_event.values() if parse_iso_datetime(r['kickoff_utc'],'ko')+timedelta(hours=2)<=now]
    future=[r for r in by_event.values() if parse_iso_datetime(r['kickoff_utc'],'ko')+timedelta(hours=2)>now]
    counts['old_enough_for_result']=len(old); counts['not_old_enough']=len(future)
    by_comp=Counter(r['competition_id'] for r in old)
    report={'schema_version':'V6.17.4-kambi-git-history-inventory-r1','generated_at_utc':now.isoformat(),'status':'PASS','counts':dict(counts),'old_enough_by_competition':dict(sorted(by_comp.items())),'old_enough_examples':sorted(old,key=lambda r:r['kickoff_utc'])[:20],'governance':{'research_only':True,'git_history_read_only':True,'formal_evidence_restored':False,'current_rule_change':False,'formal_weight_change':False}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
