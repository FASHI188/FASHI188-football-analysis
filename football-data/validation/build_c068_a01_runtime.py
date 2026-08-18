#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, zipfile
from pathlib import Path
SEED='A_SERIES_WYSCOUT_20260818_R1'
EXPECTED_IDS_SHA='12ccdea126c4b92c2ea82ce4fbcbea54c8525885423371883f71339c1204adcf'
MATCHES_SHA='c8f92bb7533e5c127e043cee764c991b5c25b4f5e70a65be931baae0b1765ce9'
EVENTS_SHA='877e015b716ffdeea18f04418e3f24fed307ed03c37ff305cabe1f47c4822a45'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main(matches_zip,events_zip,out):
    if sha(matches_zip)!=MATCHES_SHA: raise RuntimeError('matches source hash mismatch')
    if sha(events_zip)!=EVENTS_SHA: raise RuntimeError('events source hash mismatch')
    mz=zipfile.ZipFile(matches_zip); ez=zipfile.ZipFile(events_zip)
    allm=[]
    for n in mz.namelist():
        if not n.startswith('matches_') or not n.endswith('.json'): continue
        comp=Path(n).stem.removeprefix('matches_')
        for m in json.loads(mz.read(n)):
            mid=int(m['wyId']); key=hashlib.sha256(f'{SEED}|{mid}'.encode()).hexdigest()
            allm.append((key,mid,comp,m))
    allm.sort(key=lambda x:x[0]); sel=allm[:400]; ids=[str(x[1]) for x in sel]
    ids_sha=hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()
    if ids_sha!=EXPECTED_IDS_SHA: raise RuntimeError(f'identity mismatch {ids_sha}')
    wanted={x[1] for x in sel}; ev_by={}
    for n in ez.namelist():
        if not n.startswith('events_') or not n.endswith('.json'): continue
        for e in json.loads(ez.read(n)):
            mid=int(e['matchId'])
            if mid in wanted: ev_by.setdefault(mid,[]).append(e)
    if set(ev_by)!=wanted: raise RuntimeError(f'event coverage {len(ev_by)} !=400')
    manifest=[]
    for rank,(key,mid,comp,m) in enumerate(sel,1):
        manifest.append({'package_id':'A01','rank':rank,'match_id':mid,'competition_file':comp,'selection_sha256':key,'event_count':len(ev_by[mid])})
    out=Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('PACKAGE.json',json.dumps({'package_id':'A01','series':'A','semantics':'RESEARCH_DEVELOPMENT_ONLY_NOT_PROTECTED','source':'Pappalardo/Wyscout public event dataset','match_count':400,'seed':SEED},indent=2))
        z.writestr('MANIFEST.jsonl',''.join(json.dumps(x,separators=(',',':'))+'\n' for x in manifest))
        z.writestr('matches.jsonl',''.join(json.dumps(x[3],separators=(',',':'))+'\n' for x in sel))
        for _,mid,_,_ in sel: z.writestr(f'events/{mid}.json',json.dumps(ev_by[mid],separators=(',',':')))
    print(json.dumps({'status':'A01_RUNTIME_REBUILT','matches':400,'ids_sha256':ids_sha,'out':str(out)},indent=2))
if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--matches-zip',required=True);ap.add_argument('--events-zip',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();main(a.matches_zip,a.events_zip,a.out)
