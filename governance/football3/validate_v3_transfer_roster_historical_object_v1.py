#!/usr/bin/env python3
import argparse, hashlib, json, re
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen
C=Path(__file__).with_name('v3_transfer_roster_historical_object_contract_v1.json')
MD5=re.compile(r'^[0-9a-f]{32}$'); SHA=re.compile(r'^[0-9a-f]{40}$')
class Stop(RuntimeError): pass

def ts(v):
    try: return datetime.fromisoformat(v.replace('Z','+00:00'))
    except Exception: raise Stop('STOP_TIME_SEMANTICS')
def dt(v):
    try: return date.fromisoformat(str(v)[:10])
    except Exception: return None
def nested(v):
    if isinstance(v,(dict,list)): return v
    if isinstance(v,str):
        try: return json.loads(v)
        except Exception: return None
    return None
def club(h):
    if not isinstance(h,str): return None
    p=[x for x in h.split('/') if x]
    try: x=p[p.index('verein')+1]
    except Exception: return None
    return x if x.isdigit() and int(x)>0 else None
def url(prefix,h,dir_=False):
    if not MD5.fullmatch(str(h)): raise Stop('STOP_HASH_FORMAT')
    return f"{prefix}/{h[:2]}/{h[2:]}{'.dir' if dir_ else ''}"
def get(u,limit):
    with urlopen(Request(u,headers={'User-Agent':'Football3-transfer-object-audit/1.0'}),timeout=90) as r: b=r.read(limit+1)
    if len(b)>limit: raise Stop('STOP_OBJECT_TOO_LARGE')
    return b
def resolve(manifest,dir_md5,target):
    if hashlib.md5(manifest).hexdigest()!=dir_md5: raise Stop('STOP_DIR_MANIFEST_HASH_MISMATCH')
    try: xs=json.loads(manifest)
    except Exception: raise Stop('STOP_DIR_MANIFEST_PARSE')
    hits=[x for x in xs if isinstance(x,dict) and x.get('relpath')==target]
    if len(hits)!=1: raise Stop('STOP_TARGET_TRANSFER_OBJECT_MISSING')
    x=hits[0]
    if not MD5.fullmatch(str(x.get('md5',''))): raise Stop('STOP_CHILD_HASH_INVALID')
    if not isinstance(x.get('size'),int) or x['size']<=0: raise Stop('STOP_CHILD_SIZE_INVALID')
    return x['md5'],x['size']
def rows(raw):
    try: lines=raw.decode().splitlines()
    except Exception: raise Stop('STOP_TRANSFER_OBJECT_ENCODING')
    out=[]
    for line in lines:
        if not line.strip(): continue
        try: x=json.loads(line)
        except Exception: raise Stop('STOP_TRANSFER_JSONL_PARSE')
        if not isinstance(x,dict): raise Stop('STOP_TOP_LEVEL_SCHEMA')
        out.append(x)
    if not out: raise Stop('STOP_EMPTY_TRANSFER_OBJECT')
    return out
def project(xs,observed):
    pids=[]; decoded=items=nulls=future=bad_date=bad_end=bad_season=0; safe=[]
    for x in xs:
        if 'player_id' not in x or 'response' not in x: raise Stop('STOP_TOP_LEVEL_SCHEMA')
        pid=x['player_id']; pid=int(pid) if isinstance(pid,str) and pid.isdigit() else pid
        if not isinstance(pid,int) or pid<=0: raise Stop('STOP_PLAYER_ID_INTEGRITY')
        pids.append(pid); r=nested(x['response'])
        if r is None: nulls+=1; continue
        decoded+=1; tr=nested(r.get('transfers')) if isinstance(r,dict) else r
        if not isinstance(tr,list): continue
        for t in tr:
            t=nested(t)
            if not isinstance(t,dict): continue
            items+=1; d=dt(t.get('dateUnformatted'))
            if d is None: bad_date+=1; continue
            season=t.get('season')
            if season is None or not str(season).strip(): bad_season+=1; continue
            fr=nested(t.get('from')) or {}; to=nested(t.get('to')) or {}
            a=club(fr.get('href') if isinstance(fr,dict) else None); b=club(to.get('href') if isinstance(to,dict) else None)
            if a is None or b is None: bad_end+=1; continue
            q=(pid,d.isoformat(),str(season),a,b)
            if d>observed.date(): future+=1
            else: safe.append(q)
    if decoded==0 or items==0: raise Stop('STOP_TRANSFER_PAYLOAD_UNPARSED')
    safe=sorted(set(safe))
    if not safe: raise Stop('STOP_NO_PIT_SAFE_TRANSFER_ROWS')
    digest=hashlib.sha256('\n'.join('|'.join(map(str,q)) for q in safe).encode()).hexdigest()
    return {'raw_top_level_rows':len(xs),'player_id_unique_n':len(set(pids)),'decoded_response_n':decoded,'null_response_n':nulls,'transfer_item_n':items,'pit_safe_usable_transfer_rows_n':len(safe),'pit_safe_transfer_digest_sha256':digest,'future_effective_transfer_rows_excluded_n':future,'unparseable_transfer_date_n':bad_date,'unresolved_club_endpoint_n':bad_end,'missing_transfer_season_n':bad_season}
def base(c):
    return {'schema_version':'football3-v3-transfer-roster-historical-object-receipt-v1','phase':'EXACT_HASH_HISTORICAL_TRANSFER_OBJECT_SCHEMA_IDENTITY_AUDIT','target_population':'COMPLETED_MATCHES_ONLY','future_matches_allowed':False,'existing_frozen_future_receipts_used':False,'stage6_1335_queue_used':False,'labels_opened':0,'target_match_rows_read':0,'target_result_or_goal_values_read':0,'games_payload_downloaded':False,'appearances_payload_downloaded':False,'lineups_payload_downloaded':False,'market_values_payload_downloaded':False,'training':False,'tuning':False,'stage6_touched':False,'formal_weight':0,'matrix_delta':0,'data_ready':False,'available_at_semantics':'snapshot_observed_at_lte_target_cutoff','roster_transition_semantics':'transfer_date_lte_target_cutoff','acquisition_adapter_blob_sha':c['source']['acquisition_adapter_blob_sha'],'dvc_dir_manifests_downloaded':0,'transfer_objects_downloaded':0,'snapshots':[]}
def audit(c):
    r=base(c); prev=None; prefix=c['source']['dvc_remote_prefix']; target=c['source']['target_relpath']
    if len(c.get('snapshots',[]))!=10: r['decision']='STOP_SNAPSHOT_CONTRACT'; return r
    try:
      for s in c['snapshots']:
        o=ts(s['observed_at'])
        if prev and o<=prev: raise Stop('STOP_TIME_SEMANTICS')
        prev=o
        if not SHA.fullmatch(s['source_commit_sha']): raise Stop('STOP_SOURCE_COMMIT_FORMAT')
        man=get(url(prefix,s['dir_md5'],True),2_000_000); r['dvc_dir_manifests_downloaded']+=1
        h,n=resolve(man,s['dir_md5'],target)
        if n>20_000_000: raise Stop('STOP_TARGET_OBJECT_TOO_LARGE')
        raw=get(url(prefix,h),n); r['transfer_objects_downloaded']+=1
        if len(raw)!=n or hashlib.md5(raw).hexdigest()!=h: raise Stop('STOP_TARGET_OBJECT_HASH_MISMATCH')
        m=project(rows(raw),o); r['snapshots'].append({'month':s['month'],'observed_at':s['observed_at'],'source_commit_sha':s['source_commit_sha'],'dir_md5':s['dir_md5'],'transfer_object_md5':h,'transfer_object_size':n,**m})
    except Stop as e: r['decision']=str(e); return r
    r['distinct_transfer_object_hashes']=len({x['transfer_object_md5'] for x in r['snapshots']})
    r['distinct_pit_safe_transfer_digests']=len({x['pit_safe_transfer_digest_sha256'] for x in r['snapshots']})
    r['total_pit_safe_usable_rows_across_snapshots']=sum(x['pit_safe_usable_transfer_rows_n'] for x in r['snapshots'])
    r['total_future_effective_rows_excluded']=sum(x['future_effective_transfer_rows_excluded_n'] for x in r['snapshots'])
    r['decision']='PASS_EXACT_HASH_TRANSFER_OBJECT_SCHEMA_IDENTITY_NEXT_COMPLETED_MATCH_TARGET_BINDING_AUDIT' if r['distinct_transfer_object_hashes']>=2 else 'STOP_NO_OBJECT_VERSION_VARIATION'
    return r
def main():
    a=argparse.ArgumentParser(); a.add_argument('--output',required=True); z=a.parse_args(); c=json.loads(C.read_text())
    if c.get('status')!='DESIGN_LOCKED': raise Stop('STOP_CONTRACT_NOT_LOCKED')
    r=audit(c); Path(z.output).write_text(json.dumps(r,indent=2,sort_keys=True)+'\n'); print(json.dumps({'decision':r['decision'],'snapshots':len(r['snapshots'])},sort_keys=True))
if __name__=='__main__': main()
