#!/usr/bin/env python3
import argparse, gzip, hashlib, json
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

REMOTE='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc/'
ALLOWLIST_PATH=Path(__file__).with_name('v3_young_player_object_allowlist_v1.json')
FORBIDDEN={'home_club_goals','away_club_goals','goals','assists','yellow_cards','red_cards','is_win','own_goals','opponent_goals','aggregate','score','result'}
REQUIRED={
 'players':{'player_id','current_club_id','date_of_birth','position','last_season'},
 'clubs':{'club_id','domestic_competition_id'},
 'transfers':{'player_id','transfer_date','transfer_season','from_club_id','to_club_id'},
}
SNAPSHOT_DATE=date.fromisoformat('2026-07-11')

def object_url(md5):
    if not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdef' for c in md5):
        raise ValueError('invalid md5')
    return f'{REMOTE}files/md5/{md5[:2]}/{md5[2:]}'

def load_allowlist(path=ALLOWLIST_PATH):
    x=json.loads(Path(path).read_text())
    if x.get('status')!='EXACT_HASHES_FROZEN':
        raise RuntimeError('allowlist not frozen')
    return x

def fetch_exact(obj):
    if not obj.get('download_allowed'):
        raise RuntimeError('object not download allowed')
    url=object_url(obj['md5'])
    with urlopen(Request(url,headers={'User-Agent':'Football3-young-player-schema-audit/1.0'}),timeout=60) as r:
        raw=r.read(obj['size']+1)
    if len(raw)!=obj['size']:
        raise RuntimeError(f"size mismatch for {obj['relpath']}: {len(raw)} != {obj['size']}")
    if hashlib.md5(raw).hexdigest()!=obj['md5']:
        raise RuntimeError(f"md5 mismatch for {obj['relpath']}")
    return raw,url

def decode_records(raw, relpath):
    data=gzip.decompress(raw) if relpath.endswith('.gz') else raw
    text=data.decode('utf-8')
    try:
        obj=json.loads(text)
        if isinstance(obj,list):
            return obj
        if isinstance(obj,dict):
            for k in ('data','records','items'):
                if isinstance(obj.get(k),list):
                    return obj[k]
            return [obj]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    raise RuntimeError('unsupported json structure')

def table_name(relpath):
    return Path(relpath).name.split('.')[0]

def parse_date(v):
    if not isinstance(v,str) or not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None

def audit(network=True, allowlist_path=ALLOWLIST_PATH):
    receipt={
      'schema_version':'football3-v3-young-player-schema-identity-receipt-v1',
      'phase':'SAFE_HASH_BOUND_ROW_SCHEMA_IDENTITY_AUDIT',
      'labels_opened':0,'training':False,'tuning':False,'target_result_or_goal_values_read':0,
      'stage6_touched':False,'data_ready':False,'formal_weight':0,'matrix_delta':0,
      'downloaded_objects':[],'blocked_objects':[]
    }
    allow=load_allowlist(allowlist_path)
    for obj in allow['objects']:
        if not obj.get('download_allowed'):
            receipt['blocked_objects'].append({'relpath':obj['relpath'],'md5':obj['md5'],'reason':obj.get('reason')})
    if not network:
        receipt['decision']='OFFLINE_SYNTHETIC_ONLY'
        return receipt
    tables={}
    for obj in allow['objects']:
        if not obj.get('download_allowed'):
            continue
        raw,url=fetch_exact(obj)
        name=table_name(obj['relpath'])
        rows=decode_records(raw,obj['relpath'])
        if not rows or not all(isinstance(r,dict) for r in rows):
            raise RuntimeError(f'invalid records {name}')
        cols=sorted(set().union(*(r.keys() for r in rows)))
        forbidden=sorted(FORBIDDEN & set(cols))
        missing=sorted(REQUIRED[name]-set(cols))
        info={'table':name,'relpath':obj['relpath'],'md5':obj['md5'],'size':obj['size'],'object_url':url,'record_count':len(rows),'columns':cols,'forbidden_columns_present':forbidden,'required_columns_missing':missing}
        if name in ('players','clubs'):
            idcol='player_id' if name=='players' else 'club_id'
            vals=[r.get(idcol) for r in rows]
            nonnull=[v for v in vals if v is not None]
            info['id_nonnull_n']=len(nonnull)
            info['id_unique_n']=len(set(map(str,nonnull)))
            info['id_digest_sha256']=hashlib.sha256('\n'.join(sorted(map(str,nonnull))).encode()).hexdigest()
        if name=='players':
            dob=[parse_date(r.get('date_of_birth')) for r in rows]
            info['dob_parseable_n']=sum(x is not None for x in dob)
            info['dob_parseable_fraction']=info['dob_parseable_n']/len(rows)
            info['current_club_id_nonnull_n']=sum(r.get('current_club_id') is not None for r in rows)
        if name=='transfers':
            for c in REQUIRED['transfers']:
                info[f'{c}_nonnull_n']=sum(r.get(c) is not None and r.get(c)!='' for r in rows)
            ds=[parse_date(r.get('transfer_date')) for r in rows]
            valid=[x for x in ds if x]
            info['transfer_date_parseable_n']=len(valid)
            info['transfer_date_min']=min(valid).isoformat() if valid else None
            info['transfer_date_max']=max(valid).isoformat() if valid else None
            info['future_dated_vs_snapshot_n']=sum(x>SNAPSHOT_DATE for x in valid)
        tables[name]=(rows,info)
        receipt['downloaded_objects'].append(info)
    names=set(tables)
    if names!=set(REQUIRED):
        receipt['decision']='STOP_SAFE_OBJECT_SET_INCOMPLETE'; return receipt
    if any(i['forbidden_columns_present'] for _,i in tables.values()):
        receipt['decision']='STOP_FORBIDDEN_COLUMNS_PRESENT'; return receipt
    if any(i['required_columns_missing'] for _,i in tables.values()):
        receipt['decision']='STOP_REQUIRED_SCHEMA_MISSING'; return receipt
    for name in ('players','clubs'):
        rows,info=tables[name]
        if info['id_nonnull_n']!=len(rows) or info['id_unique_n']!=len(rows):
            receipt['decision']='STOP_STABLE_ID_INTEGRITY'; return receipt
    if tables['players'][1]['dob_parseable_fraction']<0.99:
        receipt['decision']='STOP_DOB_COVERAGE'; return receipt
    tr,ti=tables['transfers']
    if any(ti[f'{c}_nonnull_n']!=len(tr) for c in REQUIRED['transfers']):
        receipt['decision']='STOP_TRANSFER_REQUIRED_NONNULL'; return receipt
    clubs={str(r['club_id']) for r in tables['clubs'][0]}
    players={str(r['player_id']) for r in tables['players'][0]}
    cc=[str(r['current_club_id']) for r in tables['players'][0] if r.get('current_club_id') is not None]
    receipt['relationships']={
      'player_current_club_linked_n':sum(x in clubs for x in cc),
      'player_current_club_nonnull_n':len(cc),
      'player_current_club_link_fraction':(sum(x in clubs for x in cc)/len(cc) if cc else None),
      'transfer_player_link_fraction':sum(str(r['player_id']) in players for r in tr)/len(tr),
      'transfer_from_club_link_fraction':sum(str(r['from_club_id']) in clubs for r in tr)/len(tr),
      'transfer_to_club_link_fraction':sum(str(r['to_club_id']) in clubs for r in tr)/len(tr)
    }
    receipt['referenced_dvc_data_objects_downloaded']=3
    receipt['decision']='PASS_SAFE_SCHEMA_IDENTITY_TIME_SEMANTICS_NEXT_TARGET_IDENTITY_AUDIT'
    return receipt

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True); ap.add_argument('--offline',action='store_true'); a=ap.parse_args()
    r=audit(not a.offline); Path(a.output).write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'decision':r['decision'],'downloaded_n':len(r['downloaded_objects']),'blocked_n':len(r['blocked_objects'])},sort_keys=True))
if __name__=='__main__': main()
