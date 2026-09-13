#!/usr/bin/env python3
import hashlib, importlib.util, json
from pathlib import Path
H=Path(__file__).resolve().parent
S=importlib.util.spec_from_file_location('v',H/'validate_v3_transfer_roster_historical_object_v1.py')
v=importlib.util.module_from_spec(S); S.loader.exec_module(v)
def raw(pid=1,date='2024-08-01',to=2):
    x={'player_id':pid,'response':{'transfers':[{'dateUnformatted':date,'season':'24/25','from':{'href':'/verein/1'},'to':{'href':f'/verein/{to}'}}]}}
    return (json.dumps(x)+'\n').encode()
def manifest(b):
    h=hashlib.md5(b).hexdigest(); m=json.dumps([{'md5':h,'relpath':'2024/transfers.json','size':len(b)}],separators=(',',':')).encode(); return h,m,hashlib.md5(m).hexdigest()
def t_resolve_ok():
    b=raw(); h,m,d=manifest(b); assert v.resolve(m,d,'2024/transfers.json')==(h,len(b))
def t_dir_hash_stop():
    b=raw(); h,m,d=manifest(b)
    try: v.resolve(m+b' ',d,'2024/transfers.json'); assert False
    except v.Stop as e: assert str(e)=='STOP_DIR_MANIFEST_HASH_MISMATCH'
def t_missing_stop():
    m=json.dumps([{'md5':'a'*32,'relpath':'2024/market_values.json','size':1}]).encode(); d=hashlib.md5(m).hexdigest()
    try: v.resolve(m,d,'2024/transfers.json'); assert False
    except v.Stop as e: assert str(e)=='STOP_TARGET_TRANSFER_OBJECT_MISSING'
def t_player_stop():
    x=json.loads(raw()); x['player_id']='bad'
    try: v.project([x],v.ts('2024-09-27T04:54:42Z')); assert False
    except v.Stop as e: assert str(e)=='STOP_PLAYER_ID_INTEGRITY'
def t_future_excluded():
    x={'player_id':1,'response':{'transfers':[{'dateUnformatted':'2024-08-01','season':'24/25','from':{'href':'/verein/1'},'to':{'href':'/verein/2'}},{'dateUnformatted':'2025-08-01','season':'25/26','from':{'href':'/verein/2'},'to':{'href':'/verein/3'}}]}}
    r=v.project([x],v.ts('2024-09-27T04:54:42Z')); assert r['pit_safe_usable_transfer_rows_n']==1 and r['future_effective_transfer_rows_excluded_n']==1
def t_zero_guards():
    c=json.loads((H/'v3_transfer_roster_historical_object_contract_v1.json').read_text()); r=v.base(c)
    assert r['labels_opened']==r['target_match_rows_read']==r['target_result_or_goal_values_read']==0
    assert not r['future_matches_allowed'] and not r['training'] and not r['tuning']
def t_historical_provenance_retained():
    c=json.loads((H/'v3_transfer_roster_historical_object_contract_v1.json').read_text())
    assert c['source']['historical_dvc_config_blob_sha']=='3c0f733fe20089d44449e22cdfc707043c9f70cb'
    assert c['source']['historical_dvc_remote_root']=='https://d1mj0i4rr3evqd.cloudfront.net/dvc/'
def t_r2_exact_hash_mirror_binding():
    c=json.loads((H/'v3_transfer_roster_historical_object_contract_v1.json').read_text()); s=c['source']
    assert s['mirror_migration_commit_sha']=='0f247e4b13d8e2a619bc62854e62bb16c9759f7e'
    assert s['mirror_migration_pr']==329
    assert s['mirror_dvc_remote_root']=='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc/'
    assert s['mirror_dvc_remote_prefix']=='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc'
    assert v.url(s['mirror_dvc_remote_prefix'],'a'*32,True).endswith('/dvc/aa/'+'a'*30+'.dir')
def t_mirror_receipt_defaults_fail_closed():
    c=json.loads((H/'v3_transfer_roster_historical_object_contract_v1.json').read_text()); r=v.base(c)
    assert r['mirror_equivalence_verified'] is False and r['mirror_dir_objects_verified']==0 and r['mirror_child_objects_verified']==0
T=[t_resolve_ok,t_dir_hash_stop,t_missing_stop,t_player_stop,t_future_excluded,t_zero_guards,t_historical_provenance_retained,t_r2_exact_hash_mirror_binding,t_mirror_receipt_defaults_fail_closed]
if __name__=='__main__':
    for f in T: f(); print('PASS',f.__name__)
    print(f'{len(T)}/{len(T)} PASS')
