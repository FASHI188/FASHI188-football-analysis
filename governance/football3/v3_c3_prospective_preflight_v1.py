from __future__ import annotations
import argparse,gzip,hashlib,json,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path

SCHEMA='football3-v3-c3-prospective-confirmation-contract-v1'
REQUIRED_N=8454
LEAGUES={'EPL':'EPL','La_liga':'La_Liga','Bundesliga':'Bundesliga','Serie_A':'Serie_A','Ligue_1':'Ligue_1'}
SEASON='2026/27'
SEASON_START_YEAR=2026
UA='Mozilla/5.0 (compatible; Football3Research/1.0; +noncommercial-research)'
AJAX_HEADERS={'User-Agent':UA,'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}

class PreflightError(RuntimeError): pass

def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def parse_dt(v):
    s=str(v).strip().replace('T',' ')
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try: return datetime.strptime(s,fmt).replace(tzinfo=timezone.utc)
        except ValueError: pass
    raise PreflightError(f'unsupported datetime {v!r}')
def truthy(v): return v is True or str(v).lower() in {'true','1','yes'}
def team_title(v): return str((v or {}).get('title') or '').strip()
def identity(comp,row):
    h,a=team_title(row.get('h')),team_title(row.get('a'))
    if not h or not a: raise PreflightError('team title missing')
    ko=parse_dt(row['datetime']).isoformat().replace('+00:00','Z')
    return {'competition':comp,'season':SEASON,'home_team':h,'away_team':a,'scheduled_kickoff_utc':ko}
def identity_sha(x): return sha256_bytes(canon(x))
def decode(raw,enc=''):
    if str(enc).lower().strip()=='gzip' or raw[:2]==b'\x1f\x8b': return gzip.decompress(raw)
    return raw
def fetch_json(url,tries=3):
    err=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers=AJAX_HEADERS)
            with urllib.request.urlopen(req,timeout=30) as r:
                if getattr(r,'status',200)!=200: raise PreflightError(f'HTTP {r.status}')
                wire=r.read(); raw=decode(wire,r.headers.get('Content-Encoding') or '')
                obj=json.loads(raw)
                if not isinstance(obj,dict): raise PreflightError('response not object')
                return obj,sha256_bytes(raw)
        except Exception as exc:
            err=exc
            if i+1<tries: time.sleep(2*(i+1))
    raise PreflightError(f'fetch failed {url}: {err}')

def load_contract(path):
    c=json.loads(Path(path).read_text(encoding='utf-8'))
    if c.get('schema_version')!=SCHEMA or c.get('status')!='FROZEN_BEFORE_FRESH_ENROLLMENT': raise PreflightError('contract drift')
    if c['frozen_candidate']['head']!='8a0da8528b9074e095cab323f825fd53570b5db4': raise PreflightError('candidate head drift')
    if float(c['frozen_candidate']['beta'])!=0.878653613734059 or int(c['cohort_rule']['required_n'])!=REQUIRED_N: raise PreflightError('beta/required_n drift')
    if not all(c['forbidden_changes'].values()): raise PreflightError('forbidden surface unlocked')
    return c
def load_exclusions(path):
    x=json.loads(Path(path).read_text(encoding='utf-8'))
    vals=x.get('understat_match_ids')
    if x.get('status')!='FROZEN_ZERO_LABEL_IDENTITY_ONLY' or not isinstance(vals,list) or len(vals)!=1335 or len(set(vals))!=1335: raise PreflightError('exclusion ledger drift')
    got=sha256_bytes(('\n'.join(sorted((str(v) for v in vals),key=lambda v:int(v)))+'\n').encode())
    if got!=x.get('sorted_understat_match_id_set_sha256'): raise PreflightError('exclusion set digest mismatch')
    if x.get('contains_target_results') or x.get('contains_goal_labels'): raise PreflightError('exclusion ledger contains labels')
    return set(vals),x

def build_inventory(payloads,excluded,activation):
    all_future=[]; seen=set(); source_meta={}
    for comp in LEAGUES:
        obj,raw_sha=payloads[comp]
        dates=obj.get('dates')
        if not isinstance(dates,list): raise PreflightError(f'{comp}: dates missing')
        n0=0
        for row in dates:
            if not isinstance(row,dict) or not row.get('datetime'): continue
            ko=parse_dt(row['datetime'])
            if truthy(row.get('isResult')) or ko<=activation: continue
            x=identity(comp,row); h=identity_sha(x); mid=str(row.get('id') or '')
            if not mid: raise PreflightError('future fixture understat match id missing')
            if h in seen: raise PreflightError('duplicate future identity')
            seen.add(h); n0+=1
            if mid in excluded: continue
            all_future.append({**x,'fixture_identity_sha256':h,'understat_match_id':mid})
        source_meta[comp]={'schedule_rows_n':len(dates),'future_post_activation_before_exclusion_n':n0,'decoded_payload_sha256':raw_sha}
    all_future.sort(key=lambda x:(x['scheduled_kickoff_utc'],x['competition'],x['home_team'],x['away_team']))
    return all_future,source_meta

def run(contract_path,exclusion_path,out,inventory_out,receipt_out):
    c=load_contract(contract_path); excluded,ledger=load_exclusions(exclusion_path)
    activation=datetime.now(timezone.utc); payloads={}
    for comp,slug in LEAGUES.items():
        url=f'https://understat.com/getLeagueData/{slug}/{SEASON_START_YEAR}'
        obj,dig=fetch_json(url); payloads[comp]=(obj,dig)
    inv,meta=build_inventory(payloads,excluded,activation)
    ids=[x['fixture_identity_sha256'] for x in inv]
    inv_sha=sha256_bytes(('\n'.join(ids)+'\n').encode())
    payload={'schema_version':'football3-v3-c3-rolling-inventory-snapshot-v1','activation_utc':activation.isoformat().replace('+00:00','Z'),'season':SEASON,'required_total_n':REQUIRED_N,'current_inventory_n':len(inv),'ordered_inventory_identity_sha256':inv_sha,'fixtures':inv,'target_labels_opened':False,'result_or_goal_fields_persisted':False,'stage6_1335_excluded':True,'this_snapshot_is_final_cohort':False}
    report={'schema_version':'football3-v3-c3-prospective-zero-label-preflight-v1','generated_at_utc':payload['activation_utc'],'required_total_n':REQUIRED_N,'current_inventory_n':len(inv),'remaining_after_current_inventory':max(0,REQUIRED_N-len(inv)),'season':SEASON,'source':'Understat AJAX schedule metadata','source_leagues':meta,'stage6_exclusion_count':len(excluded),'stage6_exclusion_set_sha256':ledger['sorted_understat_match_id_set_sha256'],'stage6_overlap_after_exclusion_n':sum(1 for x in inv if x['understat_match_id'] in excluded),'real_target_result_or_goal_values_read':0,'raw_payload_persisted':False,'cohort_mode':'ROLLING_FUTURE_PROSPECTIVE_ENROLLMENT','full_8454_identities_required_now':False,'initial_inventory_sha256':inv_sha,'source_preflight_status':'PASS_ROLLING_COHORT_RULE_ZERO_LABEL'}
    receipt={'schema_version':'football3-v3-c3-cohort-rule-seal-v1','candidate_head':c['frozen_candidate']['head'],'beta':c['frozen_candidate']['beta'],'required_n':REQUIRED_N,'activation_utc':payload['activation_utc'],'cohort_rule':c['cohort_rule'],'initial_inventory_n':len(inv),'initial_inventory_sha256':inv_sha,'stage6_exclusion_set_sha256':ledger['sorted_understat_match_id_set_sha256'],'target_labels_opened':False,'CONFIRMATION_PASS':False,'PROSPECTIVE_PASS':False,'promotion_allowed':False}
    for p,x in ((out,report),(inventory_out,payload),(receipt_out,receipt)):
        Path(p).parent.mkdir(parents=True,exist_ok=True); Path(p).write_text(json.dumps(x,sort_keys=True,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(report,sort_keys=True))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=Path,required=True); ap.add_argument('--exclusions',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); ap.add_argument('--inventory-out',type=Path,required=True); ap.add_argument('--receipt-out',type=Path,required=True); a=ap.parse_args(); run(a.contract,a.exclusions,a.out,a.inventory_out,a.receipt_out)
if __name__=='__main__': main()
