#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,math,re
from datetime import datetime,timedelta,timezone
from pathlib import Path

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')

def valid(v):
    t=v.strip().casefold()
    if not t or t in {'nan','na','null','none'}: return False
    try: x=float(t)
    except ValueError: return False
    return math.isfinite(x) and x>1.0

def parse_dt(d,t):
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')

def suffix(h): return 71-int(h)
def htxt(s): return hashlib.sha256(s.encode()).hexdigest()
def hfile(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--source-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    r=json.loads(a.registration.read_text());a.out_dir.mkdir(parents=True,exist_ok=True)
    cuts=[int(x) for x in r['eligibility']['cutoffs_hours_before_kickoff']];min_books=int(r['eligibility']['minimum_common_bookies'])
    eligible=[];rows_seen=0
    for source in r['source_files']:
        sanitized=source.replace('.csv.gz','_no_scores.csv.gz');p=a.source_dir/sanitized
        with gzip.open(p,'rt',encoding='utf-8-sig',newline='') as f:
            reader=csv.reader(f);header=next(reader)
            forbidden={'score_home','score_away','score','detailed_score','home_score','away_score'}
            assert not(forbidden&set(header));assert header[:3]==['match_id','match_date','match_time']
            m={}
            for i,name in enumerate(header[3:],3):
                x=COL_RE.match(name)
                if x:m[(int(x.group(2)),int(x.group(3)),x.group(1))]=i
            assert len(m)==32*72*3
            for row in reader:
                rows_seen+=1
                if len(row)!=len(header) or not row[0].strip():continue
                common=None
                for h in cuts:
                    s=suffix(h);books=set()
                    for b in range(1,33):
                        if all(valid(row[m[(b,s,o)]]) for o in ('home','draw','away')): books.add(b)
                    common=books if common is None else common&books
                if len(common or ())<min_books:continue
                dt=parse_dt(row[1].strip(),row[2].strip())
                identity=f'{source}|{row[0].strip()}'
                eligible.append({'identity':identity,'source_file':source,'match_id':row[0].strip(),'match_datetime':dt,'common_bookies':len(common)})
    if not eligible: raise SystemExit('no eligible rows')
    max_dt=max(x['match_datetime'] for x in eligible);window=int(r['holdout']['window_days_from_latest_match_datetime']);start=max_dt-timedelta(days=window)
    training=[x for x in eligible if x['match_datetime']<start];holdout=[x for x in eligible if x['match_datetime']>=start]
    n=int(r['holdout']['test_rows']);seed=int(r['holdout']['selection_seed'])
    if len(holdout)<n: raise SystemExit(f'holdout pool below {n}: {len(holdout)}')
    ranked=sorted(holdout,key=lambda x:htxt(f'{seed}|{x["identity"]}'));selected=ranked[:n]
    ids=sorted(x['identity'] for x in selected);set_sha=htxt('\n'.join(ids)+'\n')
    receipt={'schema_version':r['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':'LOCKED_R39B_FIXED100_NO_LABELS','eligibility':r['eligibility'],'rows_seen':rows_seen,'eligible_rows':len(eligible),'latest_match_datetime':max_dt.isoformat(),'holdout_start':start.isoformat(),'training_eligible_rows':len(training),'holdout_eligible_rows':len(holdout),'locked_test_rows':len(selected),'selection_seed':seed,'identity_set_sha256':set_sha,'no_label_audit':{'score_columns_present_in_input':False,'score_values_accessed':0,'result_values_accessed':0,'matches_files_opened':0,'prediction_metrics_computed':0},'hard_limits':r['hard_limits']}
    (a.out_dir/'identity_lock_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    with (a.out_dir/'locked100.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['identity','source_file','match_id','match_datetime','common_bookies'])
        for x in sorted(selected,key=lambda z:(z['match_datetime'],z['identity'])):w.writerow([x['identity'],x['source_file'],x['match_id'],x['match_datetime'].isoformat(),x['common_bookies']])
    manifest=[]
    for p in sorted(a.out_dir.iterdir()):
        if p.is_file() and p.name!='manifest.json':manifest.append({'name':p.name,'bytes':p.stat().st_size,'sha256':hfile(p)})
    (a.out_dir/'manifest.json').write_text(json.dumps({'schema':'r39b-identity-manifest','files':manifest},indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
