#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json
from collections import Counter,defaultdict
from pathlib import Path

REQ={'competition_id','season','date','home_team','away_team','label_home_goals','label_away_goals','label_result','source_path'}
def hbytes(b):return hashlib.sha256(b).hexdigest()
def hfile(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def ident(r):return '|'.join(r[k].strip() for k in ('competition_id','season','date','home_team','away_team'))
def write_csv(path,rows,fields):
 out=['gold_sample_id','selection_hash','match_identity']+fields
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=out,extrasaction='ignore');w.writeheader()
  for i,r in enumerate(rows,1):
   x=dict(r);x['gold_sample_id']=f'G{i:04d}';x['selection_hash']=r['_hash'];x['match_identity']=r['_id'];w.writerow(x)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--repo-root',type=Path,default=Path('.'));ap.add_argument('--spec',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 spec=json.loads(a.spec.read_text(encoding='utf-8'));seed=spec['seed'];strata={(c,s):(n,q) for c,s,n,q in spec['strata']};mult=int(spec.get('reserve_multiplier',3))
 rows=[];fields=set();src={}
 for comp in sorted({c for c,_ in strata}):
  p=a.repo_root/'football-data'/'training_datasets'/comp/'point_in_time.csv'
  if not p.is_file():raise FileNotFoundError(p)
  src[str(p.relative_to(a.repo_root)).replace('\\','/')]=hfile(p)
  with p.open('r',encoding='utf-8-sig',newline='') as f:
   rd=csv.DictReader(f);head=set(rd.fieldnames or []);miss=REQ-head
   if miss:raise ValueError(f'{p}: missing {sorted(miss)}')
   fields|=head
   for raw in rd:
    r=dict(raw);r['_id']=ident(r);r['_hash']=hbytes(f"{seed}|{r['_id']}".encode());rows.append(r)
 ids=[r['_id'] for r in rows]
 if len(ids)!=len(set(ids)):raise ValueError('duplicate match identity')
 by=defaultdict(list)
 for r in rows:
  k=(r['competition_id'].strip(),r['season'].strip())
  if k in strata:by[k].append(r)
 sel=[];res=[];audit=[]
 for k in sorted(strata):
  exp,q=strata[k];cand=sorted(by[k],key=lambda r:r['_hash'])
  if len(cand)!=exp:raise ValueError(f'{k}: expected {exp}, found {len(cand)}')
  sel+=cand[:q];res+=cand[q:min(len(cand),q*mult)];audit.append({'competition':k[0],'season':k[1],'available':len(cand),'quota':q,'reserve':max(0,min(len(cand),q*mult)-q)})
 sel.sort(key=lambda r:r['_hash']);res.sort(key=lambda r:r['_hash'])
 if len(sel)!=1000:raise ValueError(f'selected={len(sel)}')
 a.out.mkdir(parents=True,exist_ok=True);fields=sorted(fields)
 rp=a.out/'GOLD1000_R1_random_manifest.csv';bp=a.out/'GOLD1000_R1_reserve_manifest.csv';write_csv(rp,sel,fields);write_csv(bp,res,fields)
 receipt={'schema_version':'GOLD1000-SAMPLING-R1','seed':seed,'selection_uses_labels':False,'selection_uses_model_outputs':False,'strata_count':len(strata),'available_rows':sum(n for n,_ in strata.values()),'selected_rows':len(sel),'reserve_rows':len(res),'selected_outcomes_audit_only':dict(sorted(Counter(r['label_result'] for r in sel).items())),'source_file_sha256':src,'strata':audit,'outputs':{rp.name:hfile(rp),bp.name:hfile(bp)}}
 sp=a.out/'GOLD1000_R1_sampling_receipt.json';sp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'PASS','selected':len(sel),'reserve':len(res),'receipt_sha256':hfile(sp)},ensure_ascii=False))
if __name__=='__main__':main()
