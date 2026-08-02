#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, pathlib
from typing import Any
HERE=pathlib.Path(__file__).resolve().parent

def read_json(path:pathlib.Path)->dict[str,Any]:
    v=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(v,dict): raise ValueError(f'object required: {path}')
    return v

def canonical_sha(v:Any)->str:
    return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def git_blob_sha(path:pathlib.Path)->str:
    raw=path.read_bytes();return hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()
def validate_payload(auth:dict[str,Any],spec:dict[str,Any],identity:dict[str,Any],identity_blob_sha:str)->dict[str,Any]:
    if auth.get('schema_version')!='DRAW-AUTO-RESEARCH-AUTHORIZATION-R1.4': raise ValueError('authorization schema mismatch')
    if auth.get('status')!='AUTHORIZED_VIEWED_DEVELOPMENT_AUTO_RESEARCH': raise ValueError('authorization status mismatch')
    if auth.get('user_authorization_record')!='rec0WJJzXiuDvAqSb': raise ValueError('authorization record mismatch')
    if auth.get('data_status')!='VIEWED_DEVELOPMENT_DATA' or auth.get('formal_weight')!=0: raise ValueError('authorization boundary mismatch')
    if auth.get('spec_canonical_sha256')!=canonical_sha(spec): raise ValueError('spec digest mismatch')
    if auth.get('identity_canonical_sha256')!=canonical_sha(identity): raise ValueError('identity digest mismatch')
    if auth.get('identity_git_blob_sha')!=identity_blob_sha: raise ValueError('identity blob mismatch')
    required=identity.get('authorization_required_bindings') or []; bindings=auth.get('bindings') or {}; files=identity.get('files') or {}
    if set(bindings)!=set(required): raise ValueError('binding set mismatch')
    for key in required:
        if bindings.get(key)!=files.get(key): raise ValueError(f'binding mismatch: {key}')
    return {'status':'PASS_AUTHORIZATION_BINDINGS_ZERO_LABEL','binding_count':len(required),'rows_parsed':0,'labels_parsed':0,'training_runs':0,'scoring_runs':0,'formal_weight':0}
def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
    try:
        identity_path=HERE/'draw_auto_research_identity_r1.json';r=validate_payload(read_json(HERE/'draw_composite_run_authorization_r1.json'),read_json(HERE/'draw_auto_research_spec_r1.json'),read_json(identity_path),git_blob_sha(identity_path));a.output.write_text(json.dumps(r,indent=2)+'\n');return 0
    except Exception as e:
        a.output.write_text(json.dumps({'status':'FAIL_CLOSED_AUTHORIZATION_BINDINGS','error':str(e),'rows_parsed':0,'labels_parsed':0,'training_runs':0,'scoring_runs':0},indent=2)+'\n');return 1
if __name__=='__main__': raise SystemExit(main())
