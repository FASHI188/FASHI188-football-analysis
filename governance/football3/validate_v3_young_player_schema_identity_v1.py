#!/usr/bin/env python3
import argparse, gzip, hashlib, json
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

REMOTE='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc/'
ALLOWLIST_PATH=Path(__file__).with_name('v3_young_player_object_allowlist_v1.json')
SNAPSHOT_DATE=date.fromisoformat('2026-07-11')

def object_url(md5):
    if not isinstance(md5,str) or len(md5)!=32 or any(c not in '0123456789abcdef' for c in md5): raise ValueError('invalid md5')
    return f'{REMOTE}files/md5/{md5[:2]}/{md5[2:]}'

def load_allowlist(path=ALLOWLIST_PATH):
    x=json.loads(Path(path).read_text())
    if x.get('status')!='EXACT_HASHES_FROZEN': raise RuntimeError('allowlist not frozen')
    return x

def fetch_exact(obj):
    if not obj.get('download_allowed'): raise RuntimeError('object not download allowed')
    url=object_url(obj['md5'])
    with urlopen(Request(url,headers={'User-Agent':'Football3-young-player-schema-audit/1.0'}),timeout=60) as r: raw=r.read(obj['size']+1)
    if len(raw)!=obj['size']: raise RuntimeError(f"size mismatch for {obj['relpath']}")
    if hashlib.md5(raw).hexdigest()!=obj['md5']: raise RuntimeError(f"md5 mismatch for {obj['relpath']}")
    return raw,url

def decode_records(raw, relpath):
    data=gzip.decompress(raw) if relpath.endswith('.gz') else raw
    text=data.decode('utf-8')
    try:
        obj=json.loads(text)
        if isinstance(obj,list): return obj
        if isinstance(obj,dict):
            for k in ('data','records','items'):
                if isinstance(obj.get(k),list): return obj[k]
            return [obj]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    raise RuntimeError('unsupported json structure')

def token_after(href, marker):
    if not isinstance(href,str): return None
    parts=[p for p in href.split('/') if p]
    try: i=parts.index(marker)
    except ValueError: return None
    return parts[i+1] if i+1<len(parts) and parts[i+1] else None

def parse_player_dob(v):
    if not isinstance(v,str) or not v or v in {'N/A','null'}: return None
    if len(v)==4 and v.isdigit(): return date(int(v),1,1)
    for fmt in ('%b %d, %Y','%d/%m/%Y'):
        try: return datetime.strptime(v,fmt).date()
        except ValueError: pass
    return None

def parse_iso_date(v):
    if not isinstance(v,str) or not v or v=='0000-00-00': return None
    try: return date.fromisoformat(v[:10])
    except ValueError: return None

def dedupe_identity(rows, id_key, compare_keys):
    by={}; duplicate_n=0; conflict_n=0
    for row in rows:
        key=row.get(id_key)
        if key is None: continue
        if key in by:
            duplicate_n+=1
            if any(by[key].get(k)!=row.get(k) for k in compare_keys): conflict_n+=1
            continue
        by[key]=row
    return list(by.values()),duplicate_n,conflict_n

def project_players(rows):
    out=[]
    for r in rows:
        parent=r.get('parent') if isinstance(r.get('parent'),dict) else {}
        current=r.get('current_club') if isinstance(r.get('current_club'),dict) else {}
        club_href=current.get('href') if parent.get('type')=='national_team' else parent.get('href')
        out.append({'player_id':token_after(r.get('href'),'spieler'),'current_club_id':token_after(club_href,'verein'),'date_of_birth':parse_player_dob(r.get('date_of_birth')),'position':r.get('position'),'last_season':'2025'})
    return dedupe_identity(out,'player_id',('current_club_id','date_of_birth','position'))

def project_clubs(rows):
    out=[]
    for r in rows:
        parent=r.get('parent') if isinstance(r.get('parent'),dict) else {}
        out.append({'club_id':token_after(r.get('href'),'verein'),'domestic_competition_id':token_after(parent.get('href'),'wettbewerb')})
    return dedupe_identity(out,'club_id',('domestic_competition_id',))

def decode_nested_json(value):
    if isinstance(value,(dict,list)): return value
    if isinstance(value,str):
        try: return json.loads(value)
        except json.JSONDecodeError: return None
    return None

def project_transfers(rows):
    out=[]; seen=set(); decoded_response_n=0; null_response_n=0
    for r in rows:
        pid=r.get('player_id'); response=decode_nested_json(r.get('response'))
        if response is None: null_response_n+=1; continue
        decoded_response_n+=1
        if isinstance(response,dict): ts=decode_nested_json(response.get('transfers'))
        else: ts=response
        if not isinstance(ts,list): continue
        for t in ts:
            t=decode_nested_json(t)
            if not isinstance(t,dict): continue
            td=parse_iso_date(t.get('dateUnformatted'))
            if td is None: continue
            fr=decode_nested_json(t.get('from')) or {}; to=decode_nested_json(t.get('to')) or {}
            if not isinstance(fr,dict): fr={}
            if not isinstance(to,dict): to={}
            row={'player_id':pid,'transfer_date':td,'transfer_season':t.get('season'),'from_club_id':token_after(fr.get('href'),'verein'),'to_club_id':token_after(to.get('href'),'verein')}
            key=(str(row['player_id']),row['transfer_date'].isoformat(),str(row['from_club_id']),str(row['to_club_id']))
            if key not in seen: seen.add(key); out.append(row)
    return out,decoded_response_n,null_response_n

def audit(network=True, allowlist_path=ALLOWLIST_PATH):
    receipt={'schema_version':'football3-v3-young-player-schema-identity-receipt-v1','phase':'SAFE_HASH_BOUND_RAW_TO_CURATED_IDENTITY_AUDIT','labels_opened':0,'training':False,'tuning':False,'target_result_or_goal_values_read':0,'stage6_touched':False,'data_ready':False,'formal_weight':0,'matrix_delta':0,'downloaded_objects':[],'blocked_objects':[],'upstream_adapter_blobs':{'players':'2f6225ff0def71884671ce55c28d77656b64c4c6','clubs':'d0e13b223af2cf70cbc847cb88caf521fca6ac6a','transfers':'10dcf53ce2e79d9e46ce2b615b3a2af98c114fd5'}}
    allow=load_allowlist(allowlist_path)
    for obj in allow['objects']:
        if not obj.get('download_allowed'): receipt['blocked_objects'].append({'relpath':obj['relpath'],'md5':obj['md5'],'reason':obj.get('reason')})
    if not network: receipt['decision']='OFFLINE_SYNTHETIC_ONLY'; return receipt
    projected={}; raw_meta={}
    for obj in allow['objects']:
        if not obj.get('download_allowed'): continue
        raw,url=fetch_exact(obj); raw_rows=decode_records(raw,obj['relpath']); table=Path(obj['relpath']).name.split('.')[0]
        if table=='players': rows,dups,conflicts=project_players(raw_rows); raw_meta[table]={'duplicate_identity_rows':dups,'duplicate_identity_conflicts':conflicts}
        elif table=='clubs': rows,dups,conflicts=project_clubs(raw_rows); raw_meta[table]={'duplicate_identity_rows':dups,'duplicate_identity_conflicts':conflicts}
        elif table=='transfers': rows,decoded,nulls=project_transfers(raw_rows); raw_meta[table]={'decoded_response_n':decoded,'null_response_n':nulls}
        else: raise RuntimeError('unexpected table')
        projected[table]=rows
        receipt['downloaded_objects'].append({'table':table,'relpath':obj['relpath'],'md5':obj['md5'],'size':obj['size'],'object_url':url,'raw_record_count':len(raw_rows),'projected_record_count':len(rows),**raw_meta[table]})
    if set(projected)!={'players','clubs','transfers'}: receipt['decision']='STOP_SAFE_OBJECT_SET_INCOMPLETE'; return receipt
    if raw_meta['players']['duplicate_identity_conflicts'] or raw_meta['clubs']['duplicate_identity_conflicts']: receipt['decision']='STOP_DUPLICATE_ID_CONFLICT'; return receipt
    players=projected['players']; clubs=projected['clubs']; transfers=projected['transfers']
    pids=[r['player_id'] for r in players if r['player_id'] is not None]; cids=[r['club_id'] for r in clubs if r['club_id'] is not None]
    receipt['players']={'n':len(players),'id_nonnull_n':len(pids),'id_unique_n':len(set(pids)),'id_digest_sha256':hashlib.sha256('\n'.join(sorted(pids)).encode()).hexdigest(),'dob_parseable_n':sum(r['date_of_birth'] is not None for r in players),'dob_parseable_fraction':sum(r['date_of_birth'] is not None for r in players)/len(players),'current_club_nonnull_n':sum(r['current_club_id'] is not None for r in players),'duplicate_identity_rows':raw_meta['players']['duplicate_identity_rows']}
    receipt['clubs']={'n':len(clubs),'id_nonnull_n':len(cids),'id_unique_n':len(set(cids)),'id_digest_sha256':hashlib.sha256('\n'.join(sorted(cids)).encode()).hexdigest(),'domestic_competition_nonnull_n':sum(r['domestic_competition_id'] is not None for r in clubs),'duplicate_identity_rows':raw_meta['clubs']['duplicate_identity_rows']}
    req=('player_id','transfer_date','transfer_season','from_club_id','to_club_id')
    receipt['transfers']={'n':len(transfers),**{f'{c}_nonnull_n':sum(r[c] is not None and r[c]!='' for r in transfers) for c in req},'transfer_date_min':min((r['transfer_date'] for r in transfers),default=None).isoformat() if transfers else None,'transfer_date_max':max((r['transfer_date'] for r in transfers),default=None).isoformat() if transfers else None,'future_dated_vs_snapshot_n':sum(r['transfer_date']>SNAPSHOT_DATE for r in transfers),'decoded_response_n':raw_meta['transfers']['decoded_response_n'],'null_response_n':raw_meta['transfers']['null_response_n']}
    if len(pids)!=len(players) or len(set(pids))!=len(players) or len(cids)!=len(clubs) or len(set(cids))!=len(clubs): receipt['decision']='STOP_STABLE_ID_INTEGRITY'; return receipt
    if receipt['players']['dob_parseable_fraction']<0.99: receipt['decision']='STOP_DOB_COVERAGE'; return receipt
    if not transfers: receipt['decision']='STOP_TRANSFER_PAYLOAD_UNPARSED'; return receipt
    if any(receipt['transfers'][f'{c}_nonnull_n']!=len(transfers) for c in req): receipt['decision']='STOP_TRANSFER_REQUIRED_NONNULL'; return receipt
    clubset=set(cids); playerset=set(pids); cc=[r['current_club_id'] for r in players if r['current_club_id'] is not None]
    receipt['relationships']={'player_current_club_linked_n':sum(x in clubset for x in cc),'player_current_club_nonnull_n':len(cc),'player_current_club_link_fraction':sum(x in clubset for x in cc)/len(cc) if cc else None,'transfer_player_link_fraction':sum(str(r['player_id']) in playerset for r in transfers)/len(transfers),'transfer_from_club_link_fraction':sum(r['from_club_id'] in clubset for r in transfers)/len(transfers),'transfer_to_club_link_fraction':sum(r['to_club_id'] in clubset for r in transfers)/len(transfers)}
    receipt['referenced_dvc_data_objects_downloaded']=3
    receipt['decision']='PASS_SAFE_SCHEMA_IDENTITY_TIME_SEMANTICS_NEXT_TARGET_IDENTITY_AUDIT'
    return receipt

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True); ap.add_argument('--offline',action='store_true'); a=ap.parse_args(); r=audit(not a.offline); Path(a.output).write_text(json.dumps(r,indent=2,sort_keys=True,default=lambda x:x.isoformat())+'\n'); print(json.dumps({'decision':r['decision'],'downloaded_n':len(r['downloaded_objects']),'blocked_n':len(r['blocked_objects'])},sort_keys=True))
if __name__=='__main__': main()
