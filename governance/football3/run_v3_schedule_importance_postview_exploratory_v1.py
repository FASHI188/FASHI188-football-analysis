#!/usr/bin/env python3
import argparse, importlib.util, json, hashlib
from collections import defaultdict
from pathlib import Path
BASE_COMMIT='be83b54e82f8e2e70b8666cceabbccb55d5ace77'
BASE_BLOB='62d10c76bc683269f01e0b5ba66dcf6a86d33223'
FEATURE_SHA='c9b14435062f59f1caaf4ffdbf83373d7aa50b858a48057f66b89cc0605ecea4'
FEATURE_N=30531; COMPLETED_N=30335; MISSING_N=196
class Stop(RuntimeError): pass
def sha(p):
 b=Path(p).read_bytes(); h=hashlib.sha1(); h.update(f'blob {len(b)}\0'.encode()); h.update(b); return h.hexdigest()
def loadmod(p):
 s=importlib.util.spec_from_file_location('base_eval',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def completed(rows,root):
 g=defaultdict(list)
 for r in rows:g[r['source_path']].append(r)
 keep=[]; y={}; miss=[]; cc=defaultdict(int)
 for pth in sorted(g):
  ms=json.loads((Path(root)/pth).read_text())['matches']
  for r in g[pth]:
   i=int(r['row_index']); m=ms[i]
   if any(m.get(k)!=r.get(k) for k in ('date','round','team1','team2')): raise Stop('STOP_IDENTITY')
   ft=(m.get('score') or {}).get('ft')
   if not(isinstance(ft,list) and len(ft)==2 and all(type(x) is int for x in ft)):
    miss.append((pth,i)); continue
   t='H' if ft[0]>ft[1] else 'A' if ft[1]>ft[0] else 'D'; keep.append(r); y[(pth,i)]=t; cc[t]+=1
 if (len(keep),len(miss))!=(COMPLETED_N,MISSING_N): raise Stop('STOP_COHORT_COUNT')
 return keep,y,miss,dict(cc)
def main():
 a=argparse.ArgumentParser();
 for n in ('features','source_root','contract','base_runner','output_dir'): a.add_argument('--'+n.replace('_','-'),dest=n,required=True)
 x=a.parse_args(); c=json.loads(Path(x.contract).read_text())
 if c!={'schema':'f3-v3-postview-v1','status':'POST_VIEW_EXPLORATORY_ONLY','independent':False,'promotion':False,'feature_rows':30531,'completed_rows':30335,'excluded_missing_ft':196,'feature_sha':FEATURE_SHA,'source_commit':'41a6eb96ba816757ff892387da5cb111390a7f9c','formal_weight':0,'matrix_delta':0}: raise Stop('STOP_CONTRACT')
 if sha(x.base_runner)!=BASE_BLOB: raise Stop('STOP_BASE_RUNNER_BLOB')
 b=loadmod(x.base_runner); rows=b.load_features(x.features); keep,y,miss,cc=completed(rows,x.source_root); res=b.evaluate(keep,y); res.pop('records',None)
 passed=all(res.get('gates',{}).values())
 receipt={'schema':'f3-v3-postview-receipt-v1','decision':'POST_VIEW_EXPLORATORY_SIGNAL_PRESENT' if passed else 'POST_VIEW_EXPLORATORY_NO_ROBUST_SIGNAL','status':'POST_VIEW_EXPLORATORY_ONLY','independent':False,'promotion':False,'base_evaluator_commit':BASE_COMMIT,'base_evaluator_blob':BASE_BLOB,'feature_rows':FEATURE_N,'completed_rows':len(keep),'excluded_missing_ft':len(miss),'class_counts':cc,'future_matches_used':False,'tuning':False,'formal_weight':0,'matrix_delta':0,**res}
 out=Path(x.output_dir); out.mkdir(parents=True,exist_ok=True); (out/'postview-exploratory-receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n'); print(json.dumps(receipt,sort_keys=True))
if __name__=='__main__': main()
