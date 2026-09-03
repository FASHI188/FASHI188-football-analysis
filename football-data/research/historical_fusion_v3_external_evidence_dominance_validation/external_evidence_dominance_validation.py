#!/usr/bin/env python3
import argparse,csv,datetime,importlib.util,json,math,pathlib,sys
EPS=1e-15; OUT={'H':0,'D':1,'A':2}
def loadmod(name,path):
 s=importlib.util.spec_from_file_location(name,str(path)); m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
def loadj(p): return json.loads(pathlib.Path(p).read_text())
def writej(p,o): pathlib.Path(p).write_text(json.dumps(o,indent=2,sort_keys=True,allow_nan=False)+'\n')
def norm(vals):
 x=[1/float(v) for v in vals]; z=sum(x); return [v/z for v in x]
def top1(p): return max(range(3),key=lambda i:(p[i],-i))
def date(s):
 for f in ('%d/%m/%Y','%d/%m/%y'):
  try:return datetime.datetime.strptime(s.strip(),f).date()
  except:pass
 raise ValueError(s)
def metric(rs,key):
 n=len(rs); ll=br=rp=ac=0.0
 for r in rs:
  p=r[key]; y=r['y']; ll-=math.log(max(EPS,p[y])); br+=sum((p[i]-(i==y))**2 for i in range(3)); pc=[p[0],p[0]+p[1]]; yc=[1.0 if y==0 else 0.0,1.0 if y<=1 else 0.0]; rp+=0.5*sum((pc[i]-yc[i])**2 for i in range(2)); ac+=top1(p)==y
 return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rp/n,'top1_accuracy':ac/n}
def delta(a,b): return {k:b[k]-a[k] for k in ('logloss','brier','rps','top1_accuracy')}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--data-dir',type=pathlib.Path,required=True); ap.add_argument('--v324',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
 c=loadj(a.contract); assert c['status']=='FROZEN_BEFORE_SECOND_EXTERNAL_COHORT_DOWNLOAD_OR_SCORING'; v=loadmod('ev_v324',a.v324)
 rs=[]; inv=[]; miss=0
 for season in c['untouched_validation_data']['seasons']:
  sp=c['untouched_validation_data']['season_paths'][season]
  for lg in c['untouched_validation_data']['leagues']:
   code=lg['code']; p=a.data_dir/f'{sp}_{code}.csv'; f=p.open(encoding='utf-8-sig',newline=''); rd=csv.DictReader(f); hdr=rd.fieldnames or []; req=c['untouched_validation_data']['required_columns']; m=[x for x in req if x not in hdr];
   if m: raise RuntimeError(f'{p} missing {m}')
   raw=list(rd); f.close(); done=use=0
   for r in raw:
    if r.get('Div','').strip()!=code: raise RuntimeError('division mismatch')
    if r.get('FTR','').strip() not in OUT: continue
    done+=1
    try: vals=[float(r[x]) for x in ['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA']]
    except: miss+=1; continue
    if not all(math.isfinite(x) and x>1 for x in vals): miss+=1; continue
    b=norm(vals[:3]); q=norm(vals[3:]); bt=top1(b); t=top1(q); weak=0 if b[0]<b[2] else 2
    rawp=list(b); gated=list(b); rawexec=gateexec=False
    if t!=bt:
     rawp,rr=v.minimum_boundary_projection(b,t,weak,float(c['projection']['epsilon'])); rawexec=bool(rr['executed'])
     resistance=b[bt]-b[t]; advantage=q[t]-q[bt]
     if advantage>=resistance:
      gated,gr=v.minimum_boundary_projection(b,t,weak,float(c['projection']['epsilon'])); gateexec=bool(gr['executed'])
    rs.append({'season':season,'league':code,'date':date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'y':OUT[r['FTR'].strip()],'base':b,'raw':rawp,'gated':gated,'rawexec':rawexec,'gateexec':gateexec}) ; use+=1
   inv.append({'season':season,'league':code,'completed':done,'usable':use,'file':p.name})
 rs.sort(key=lambda r:(r['date'],r['league'],r['home'],r['away']))
 base=metric(rs,'base'); raw=metric(rs,'raw'); gated=metric(rs,'gated'); db=delta(base,raw); dg=delta(base,gated); gr=delta(raw,gated)
 def stability(key):
  folds=[]; non=0
  for k in range(12):
   lo=len(rs)*k//12; hi=len(rs)*(k+1)//12; rr=rs[lo:hi]; d=delta(metric(rr,'base'),metric(rr,key)); ok=d['logloss']<=0; non+=ok; folds.append({'fold':k+1,'n':len(rr),'min_date':str(rr[0]['date']),'max_date':str(rr[-1]['date']),'deltas':d,'ll_nondegrade':bool(ok)})
  return non,folds
 rawfn,rawfold=stability('raw'); gatefn,gatefold=stability('gated')
 def groups(key):
  out=[]; non=0
  for season in c['untouched_validation_data']['seasons']:
   for lg in c['untouched_validation_data']['leagues']:
    rr=[r for r in rs if r['season']==season and r['league']==lg['code']]; d=delta(metric(rr,'base'),metric(rr,key)); ok=d['logloss']<=0; non+=ok; out.append({'season':season,'league':lg['code'],'n':len(rr),'deltas':d,'ll_nondegrade':bool(ok)})
  return non,out
 rawgn,rawgrp=groups('raw'); gategn,gategrp=groups('gated')
 rawex=sum(r['rawexec'] for r in rs); gateex=sum(r['gateexec'] for r in rs)
 support=(gr['logloss']<=0 and gatefn>=rawfn and gategn>=rawgn and dg['top1_accuracy']>=0 and gateex<rawex)
 refute=(gr['logloss']>0 and (gatefn<rawfn or gategn<rawgn))
 cls='SUPPORTS_MICRO_FLIP_NOISE' if support else 'REFUTES_MICRO_FLIP_NOISE' if refute else 'MIXED'
 out={'schema_version':'football3-v3-external-evidence-dominance-validation-result-v1','classification':cls,'row_count':len(rs),'missing_or_invalid_odds':miss,'inventory':inv,'execution':{'raw_executed':rawex,'gated_executed':gateex,'filtered':rawex-gateex},'global':{'baseline':base,'raw':raw,'gated':gated,'raw_vs_base':db,'gated_vs_base':dg,'gated_vs_raw':gr},'raw_fold_ll_nondegrade_n':rawfn,'gated_fold_ll_nondegrade_n':gatefn,'raw_group_ll_nondegrade_n':rawgn,'gated_group_ll_nondegrade_n':gategn,'raw_folds':rawfold,'gated_folds':gatefold,'raw_groups':rawgrp,'gated_groups':gategrp,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'interpretation':('Untouched external validation supports a zero-parameter evidence-dominance arbiter as a plausible way to suppress noisy micro-flips without learning a TV threshold.' if cls=='SUPPORTS_MICRO_FLIP_NOISE' else 'Untouched validation refutes the proposed zero-parameter arbiter.' if cls=='REFUTES_MICRO_FLIP_NOISE' else 'Untouched validation is mixed; do not carry the arbiter forward as established.')}
 writej(a.out/'external_evidence_dominance_validation_result.json',out); print(json.dumps({'classification':cls,'row_count':len(rs),'raw_executed':rawex,'gated_executed':gateex,'raw_fold_ll':rawfn,'gated_fold_ll':gatefn,'raw_group_ll':rawgn,'gated_group_ll':gategn,'gated_vs_raw':gr},sort_keys=True))
if __name__=='__main__': main()
