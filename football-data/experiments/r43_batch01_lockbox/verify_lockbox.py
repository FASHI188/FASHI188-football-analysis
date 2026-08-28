#!/usr/bin/env python3
from __future__ import annotations

import hashlib, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
LOCKBOX=ROOT/'forward'/'r43_batch01_pristine_lockbox.json'
OUT=ROOT/'experiments'/'r43_batch01_lockbox'/'results'/'summary_r43_batch01_lockbox_verify.json'

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def canon(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def sha(x):return hashlib.sha256(canon(x)).hexdigest()

def event_hash_valid(e):
    body={k:v for k,v in e.items() if k!='event_hash'}
    return sha(body)==e.get('event_hash')

def digest_prefix(events,n):
    rows=[{'match_id':str(e['match_id']),'event_hash':str(e['event_hash'])} for e in events[:n]]
    return sha(rows)

def audit_prefix(path,count,expected_head,expected_digest):
    doc=load(path);events=doc.get('events') or []
    errors=[]
    if len(events)<count:errors.append(f'event_count_under_seal:{len(events)}<{count}')
    prefix=events[:count]
    prev=None
    for i,e in enumerate(prefix):
        if not event_hash_valid(e):errors.append(f'event_hash:{i+1}:{e.get("match_id")}')
        if e.get('prev_event_hash')!=prev:errors.append(f'prev_hash:{i+1}:{e.get("match_id")}')
        prev=e.get('event_hash')
    head=prefix[-1]['event_hash'] if prefix else None
    digest=digest_prefix(events,count) if len(events)>=count else None
    if head!=expected_head:errors.append('sealed_head_mismatch')
    if digest!=expected_digest:errors.append('sealed_digest_mismatch')
    return {'status':'PASS' if not errors else 'FAIL','current_event_count':len(events),'sealed_event_count':count,'sealed_prefix_head':head,'sealed_prefix_digest':digest,'errors':errors}

def run():
    box=load(LOCKBOX)
    u=box['r43u1'];y=box['r43y0']
    ua=audit_prefix(ROOT/u['ledger_path'],int(u['sealed_event_count']),u['sealed_head_event_hash'],u['ordered_match_event_hash_digest_sha256'])
    ya=audit_prefix(ROOT/y['ledger_path'],int(y['sealed_event_count']),y['sealed_head_event_hash'],y['ordered_match_event_hash_digest_sha256'])
    passed=ua['status']=='PASS' and ya['status']=='PASS'
    out={'schema_version':'football3-r43-batch01-lockbox-verification-v1','status':'PASS' if passed else 'FAIL','batch_id':box['batch_id'],'lockbox_status':box['status'],'governance':box['governance'],'r43u1_prefix_audit':ua,'r43y0_prefix_audit':ya,'action':'BATCH01_PREFIX_IMMUTABLE_CONTINUE_FORWARD_SETTLEMENT' if passed else 'STOP_BATCH01_SCORING_LOCKBOX_MISMATCH'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
    if not passed:raise SystemExit(2)
    return out

def verify():
    x=load(OUT);assert x['status']=='PASS' and x['r43u1_prefix_audit']['status']=='PASS' and x['r43y0_prefix_audit']['status']=='PASS';print('R43 Batch01 lockbox verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
