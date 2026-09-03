#!/usr/bin/env python3
import argparse,csv,datetime,importlib.util,json,math,pathlib,sys
EPS=1e-15; OUTCOME={'H':0,'D':1,'A':2}
def loadmod(name,path):
 s=importlib.util.spec_from_file_location(name,str(path)); m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
def loadj(p): return json.loads(pathlib.Path(p).read_text())
def dump(p,x): pathlib.Path(p).write_text(json.dumps(x,indent=2,sort_keys=True,allow_nan=False)+'\n')
def norm(v):
 x=[1/float(z) for z in v]; s=sum(x); return [z/s for z in x]
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))
def date(s):
 for f in ('%d/%m/%Y','%d/%m/%y'):
  try:return datetime.datetime.strptime(s.strip(),f).date()
  except ValueError:pass
 raise ValueError(s)
def usable(r,cols):
 try:
  x=[float(r[c]) for c in cols]; return all(math.isfinite(z) and z>1 for z in x)
 except Exception:return False
def metric(rows,key):
 n=len(rows)
 if not n:return {'n':0,'logloss':None,'brier':None,'rps':None,'top1_accuracy':None}
 ll=br=rp=ac=0.0
 for r in rows:
  p=r[key]; y=r['y']; ll-=math.log(max(EPS,p[y])); br+=sum((p[i]-(1 if i==y else 0))**2 for i in range(3)); yc=[1.0 if y==0 else 0.0,1.0 if y<=1 else 0.0]; pc=[p[0],p[0]+p[1]]; rp+=.5*sum((pc[i]-yc[i])**2 for i in range(2)); ac+=top1(p)==y
 return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rp/n,'top1_accuracy':ac/n}
def delta(a,b): return {k:b[k]-a[k] for k in ('logloss','brier','rps','top1_accuracy')}
def classify(op,cp):
 old=top1(op); target=top1(cp); all_side=(old!=target and target in (0,2)); side=(all_side and old in (0,2)); runner=bool(side and op[target]>op[1]); return old,target,all_side,side,runner
def project(v324,op,target,eps):
 weak=0 if op[0]<op[2] else 2; return v324.minimum_boundary_projection(op,target,weak,eps)
def summary(rows,key,c):
 b=metric(rows,'open'); q=metric(rows,key); d=delta(b,q); folds=[]; fn=0; nfold=int(c['evaluation']['chronological_folds'])
 for k in range(nfold):
  lo=len(rows)*k//nfold; hi=len(rows)*(k+1)//nfold; rr=rows[lo:hi]; bb=metric(rr,'open'); qq=metric(rr,key); dd=delta(bb,qq); ok=dd['logloss']<=0; fn+=ok; folds.append({'fold':k+1,'n':len(rr),'min_date':str(rr[0]['date']),'max_date':str(rr[-1]['date']),'deltas':dd,'ll_nondegrade':ok})
 groups=[]; gn=0
 for season in c['data']['seasons']:
  for lg in c['data']['leagues']:
   rr=[r for r in rows if r['season']==season and r['league']==lg['code']]; bb=metric(rr,'open'); qq=metric(rr,key); dd=delta(bb,qq); ok=dd['logloss']<=0; gn+=ok; groups.append({'season':season,'league':lg['code'],'n':len(rr),'deltas':dd,'ll_nondegrade':ok})
 return {'baseline':b,'candidate':q,'deltas':d,'fold_ll_nondegrade_n':fn,'group_ll_nondegrade_n':gn,'folds':folds,'groups':groups}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--contract',required=True,type=pathlib.Path); p.add_argument('--data-dir',required=True,type=pathlib.Path); p.add_argument('--v324-runner',required=True,type=pathlib.Path); p.add_argument('--out',required=True,type=pathlib.Path); a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=loadj(a.contract); assert c['status']=='FROZEN_BEFORE_PREVIOUSLY_UNOPENED_CONFIRMATION_DOWNLOAD'; v324=loadmod('ru_v324',a.v324_runner); eps=float(c['mechanism']['epsilon']); req=c['data']['required_columns']; rows=[]; inv=[]; invalid=0
 for season in c['data']['seasons']:
  sp=c['data']['season_paths'][season]
  for lg in c['data']['leagues']:
   code=lg['code']; f=a.data_dir/f'{sp}_{code}.csv'
   with f.open(encoding='utf-8-sig',newline='') as h:
    rd=csv.DictReader(h); miss=[x for x in req if x not in (rd.fieldnames or [])]
    if miss: raise RuntimeError(f'{f}: missing {miss}')
    raw=list(rd)
   done=use=0
   for r in raw:
    if r.get('Div','').strip()!=code: raise RuntimeError(f'{f}: division mismatch')
    if r.get('FTR','').strip() not in OUTCOME: continue
    done+=1
    if not usable(r,['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA']): invalid+=1; continue
    op=norm([r['AvgH'],r['AvgD'],r['AvgA']]); cp=norm([r['AvgCH'],r['AvgCD'],r['AvgCA']]); old,target,all_side,side,runner=classify(op,cp); ap=list(op); spv=list(op); rp=list(op); ar={'executed':False}; sr={'executed':False}; rr={'executed':False}
    if all_side: ap,ar=project(v324,op,target,eps)
    if side: spv,sr=project(v324,op,target,eps)
    if runner: rp,rr=project(v324,op,target,eps)
    rows.append({'season':season,'league':code,'date':date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'y':OUTCOME[r['FTR'].strip()],'open':op,'close':cp,'ALL_SIDE':ap,'SIDE_TO_SIDE':spv,'RUNNERUP':rp,'all_side':all_side,'side_to_side':side,'runnerup':runner,'all_executed':bool(ar.get('executed')),'side_executed':bool(sr.get('executed')),'runner_executed':bool(rr.get('executed'))}); use+=1
   inv.append({'season':season,'league':code,'completed_match_count':done,'usable_pre_match_odds_count':use,'file':f.name})
 rows.sort(key=lambda r:(r['date'],r['league'],r['home'],r['away'],r['season']));
 if not rows: raise RuntimeError('no rows')
 allsum=summary(rows,'ALL_SIDE',c); sidesum=summary(rows,'SIDE_TO_SIDE',c); runsum=summary(rows,'RUNNERUP',c); g=c['evaluation']['hard_gates']; d=runsum['deltas']; checks={'global_ll':d['logloss']<=g['global_logloss_delta_vs_opening_max'],'global_brier':d['brier']<=g['global_brier_delta_vs_opening_max'],'global_rps':d['rps']<=g['global_rps_delta_vs_opening_max'],'global_top1':d['top1_accuracy']>g['global_top1_delta_vs_opening_min_exclusive'],'fold_ll':runsum['fold_ll_nondegrade_n']>=g['chronological_fold_ll_nondegrade_min'],'group_ll':runsum['group_ll_nondegrade_n']>=g['league_season_ll_nondegrade_min'],'ll_le_SIDE_TO_SIDE':runsum['candidate']['logloss']<=sidesum['candidate']['logloss'],'top1_ge_SIDE_TO_SIDE':runsum['candidate']['top1_accuracy']>=sidesum['candidate']['top1_accuracy']}; ok=all(checks.values()); status=c['evaluation']['terminal_pass'] if ok else c['evaluation']['terminal_fail']
 counts={'all_side_proposal_n':sum(r['all_side'] for r in rows),'side_to_side_proposal_n':sum(r['side_to_side'] for r in rows),'runnerup_candidate_n':sum(r['runnerup'] for r in rows),'target_rank3_blocked_n':sum(r['side_to_side'] and not r['runnerup'] for r in rows),'all_executed_n':sum(r['all_executed'] for r in rows),'side_executed_n':sum(r['side_executed'] for r in rows),'runner_executed_n':sum(r['runner_executed'] for r in rows)}
 out={'schema_version':'football3-v3-runnerup-single-boundary-confirm-result-v1','status':status,'all_pass':ok,'row_count':len(rows),'invalid_odds_row_count':invalid,'inventory':inv,'counts':counts,'ALL_SIDE':allsum,'SIDE_TO_SIDE':sidesum,'RUNNERUP':runsum,'checks':checks,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; dump(a.out/'runnerup_single_boundary_confirm_result.json',out); print(json.dumps({'status':status,'row_count':len(rows),'counts':counts,'runnerup':{'top1':runsum['candidate']['top1_accuracy'],'ll':runsum['candidate']['logloss'],'deltas':runsum['deltas'],'fold':runsum['fold_ll_nondegrade_n'],'group':runsum['group_ll_nondegrade_n']},'side':{'top1':sidesum['candidate']['top1_accuracy'],'ll':sidesum['candidate']['logloss'],'fold':sidesum['fold_ll_nondegrade_n'],'group':sidesum['group_ll_nondegrade_n']},'checks':checks},sort_keys=True))
if __name__=='__main__':main()
