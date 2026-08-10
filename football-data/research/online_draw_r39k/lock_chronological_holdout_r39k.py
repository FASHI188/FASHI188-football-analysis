#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math
from datetime import datetime,timezone
from pathlib import Path

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
REQ=['AvgCH','AvgCD','AvgCA','AvgC>2.5','AvgC<2.5','AHCh','AvgCAHH','AvgCAHA']

def htxt(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids:list[str])->str:return htxt('\n'.join(sorted(ids))+'\n')
def ident(r):return '|'.join(str(r.get(c,'')).strip() for c in IDCOLS)
def valid_odd(v):
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x) and x>1.0
def valid_line(v):
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x)
def parse_date(s):
    for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(str(s).strip(),f).date()
        except:pass
    raise ValueError(s)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--registration',type=Path,required=True);ap.add_argument('--market-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    reg=json.loads(a.registration.read_text());rows=[]
    forbidden={'FTR','FTHG','FTAG','HTR','HTHG','HTAG','Referee'}
    for p in sorted(a.market_dir.glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            if hdr & forbidden:raise RuntimeError(f'label/performance column leaked into {p.name}: {sorted(hdr&forbidden)}')
            if not set(IDCOLS+REQ)<=hdr:continue
            for r in rd:
                if r.get('Season')!='2526':continue
                if not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in ['AvgCH','AvgCD','AvgCA','AvgC>2.5','AvgC<2.5','AvgCAHH','AvgCAHA']):continue
                if not valid_line(r.get('AHCh','')):continue
                rows.append({'identity':ident(r),'date':parse_date(r['Date']),'div':r['Div'],'home':r['HomeTeam'],'away':r['AwayTeam']})
    if len(rows)<300:raise RuntimeError(f'insufficient complete holdout rows {len(rows)}')
    seed=reg['prior_r39j']['fixed100_seed'];old=sorted(rows,key=lambda x:htxt(f"{seed}|{x['identity']}"))[:reg['prior_r39j']['fixed100_rows']]
    old_sha=set_sha([x['identity'] for x in old])
    if old_sha!=reg['prior_r39j']['fixed100_identity_sha256']:raise RuntimeError(f'R39J rederived SHA drift {old_sha}')
    old_ids={x['identity'] for x in old}
    eligible=[x for x in rows if x['identity'] not in old_ids]
    chosen=sorted(eligible,key=lambda x:(x['date'],x['div'],x['home'],x['away'],x['identity']))[:reg['r39k_holdout']['rows']]
    if len(chosen)!=reg['r39k_holdout']['rows']:raise RuntimeError('not enough rows for R39K fixed100')
    sha=set_sha([x['identity'] for x in chosen]);overlap=len({x['identity'] for x in chosen}&old_ids)
    out={
      'schema_version':reg['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),
      'status':'PASS_R39K_ZERO_LABEL_CHRONOLOGICAL_HOLDOUT_LOCK','complete_2526_rows':len(rows),
      'r39j_rederived_rows':len(old),'r39j_rederived_identity_sha256':old_sha,
      'r39k_fixed100_rows':len(chosen),'r39k_fixed100_identity_sha256':sha,
      'r39k_fixed100_min_date':str(min(x['date'] for x in chosen)),'r39k_fixed100_max_date':str(max(x['date'] for x in chosen)),
      'r39j_overlap':overlap,'r39k_identities':[x['identity'] for x in chosen],
      'audit_access':{'score_values':0,'result_labels':0,'model_fit':0,'hyperparameter_selection':0},
      'hard_limits':reg['hard_limits']
    }
    a.out_dir.mkdir(parents=True,exist_ok=True);(a.out_dir/'holdout_lock_r39k.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps({k:out[k] for k in ['status','complete_2526_rows','r39j_rederived_identity_sha256','r39k_fixed100_rows','r39k_fixed100_identity_sha256','r39k_fixed100_min_date','r39k_fixed100_max_date','r39j_overlap','audit_access']},indent=2))
if __name__=='__main__':main()
