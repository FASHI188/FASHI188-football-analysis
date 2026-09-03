#!/usr/bin/env python3
import argparse,csv,datetime,importlib.util,json,math,pathlib,sys
EPS=1e-15; OUTCOME={'H':0,'D':1,'A':2}
def loadmod(name,path):
 s=importlib.util.spec_from_file_location(name,str(path)); m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
def loadj(p): return json.loads(pathlib.Path(p).read_text())
def dump(p,x): pathlib.Path(p).write_text(json.dumps(x,indent=2,sort_keys=True,allow_nan=False)+'\n')
def norm(vals):
 x=[1/float(v) for v in vals]; z=sum(x); return [v/z for v in x]
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))
def date(s):
 for f in ('%d/%m/%Y','%d/%m/%y'):
  try:return datetime.datetime.strptime(s.strip(),f).date()
  except ValueError:pass
 raise ValueError(s)
def usable(r,cols):
 try:
  x=[float(r[c]) for c in cols]; return all(math.isfinite(v) and v>1 for v in x)
 except Exception:return False
def metric(rows,key):
 if not rows:return {'n':0,'logloss':None,'brier':None,'rps':None,'top1_accuracy':None}
 ll=br=rp=ac=0.0
 for r in rows:
  p=r[key]; y=r['y']; ll-=math.log(max(EPS,p[y])); br+=sum((p[i]-(1 if i==y else 0))**2 for i in range(3))
  yc=[1.0 if y==0 else 0.0,1.0 if y<=1 else 0.0]; pc=[p[0],p[0]+p[1]]; rp+=.5*sum((pc[i]-yc[i])**2 for i in range(2)); ac+=top1(p)==y
 n=len(rows); return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rp/n,'top1_accuracy':ac/n}
def delta(a,b): return {k:b[k]-a[k] for k in ('logloss','brier','rps','top1_accuracy')}
def consistent(op,cp,target):
 o=op[0]/(op[0]+op[2]); c=cp[0]/(cp[0]+cp[2]); return (target==0 and c>o) or (target==2 and c<o)
def proj(v324,op,target,eps):
 weak=0 if op[0]<op[2] else 2; return v324.minimum_boundary_projection(op,target,weak,eps)
def evals(rows,key):
 b=metric(rows,'open'); q=metric(rows,key); return {'baseline':b,'candidate':q,'deltas':delta(b,q),'ll_nondegrade':q['logloss']<=b['logloss']}
def main():
 a=argparse.ArgumentParser(); a.add_argument('--contract',required=True,type=pathlib.Path); a.add_argument('--data-dir',required=True,type=pathlib.Path); a.add_argument('--v324-runner',required=True,type=pathlib.Path); a.add_argument('--out',required=True,type=pathlib.Path); z=a.parse_args(); z.out.mkdir(parents=True,exist_ok=True)
 c=loadj(z.contract); assert c['status']=='FROZEN_BEFORE_THIRD_EXTERNAL_COHORT_DOWNLOAD_OR_SCORING'; v324=loadmod('shc_v324',z.v324_runner); req=c['data']['required_columns']; eps=float(c['proxy_mapping']['epsilon'])
 prior={'N1','P1','B1','SC0','T1','G1','E1','D2','I2','SP2','F2'}; codes={x['code'] for x in c['data']['leagues']}; assert not codes&prior
 rows=[]; inv=[]; invalid=0
 for season in c['data']['seasons']:
  sp=c['data']['season_paths'][season]
  for lg in c['data']['leagues']:
   code=lg['code']; p=z.data_dir/f'{sp}_{code}.csv'
   with p.open(encoding='utf-8-sig',newline='') as f:
    rd=csv.DictReader(f); miss=[x for x in req if x not in (rd.fieldnames or [])]
    if miss: raise RuntimeError(f'{p}: missing columns {miss}')
    raw=list(rd)
   done=use=0
   for r in raw:
    if r.get('Div','').strip()!=code: raise RuntimeError(f'{p}: division mismatch')
    if r.get('FTR','').strip() not in OUTCOME: continue
    done+=1
    if not usable(r,['AvgH','AvgD','AvgA','AvgCH','AvgCD','AvgCA']): invalid+=1; continue
    op=norm([r['AvgH'],r['AvgD'],r['AvgA']]); cp=norm([r['AvgCH'],r['AvgCD'],r['AvgCA']]); old=top1(op); target=top1(cp); raw_side=(old!=target and target in (0,2)); con=bool(raw_side and consistent(op,cp,target))
    always=list(op); cand=list(op); ar={'executed':False}; cr={'executed':False}
    if raw_side: always,ar=proj(v324,op,target,eps)
    if con: cand,cr=proj(v324,op,target,eps)
    rows.append({'season':season,'league':code,'date':date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'y':OUTCOME[r['FTR'].strip()],'open':op,'close':cp,'always':always,'candidate':cand,'raw_side':raw_side,'consistent':con,'always_executed':bool(ar.get('executed')),'candidate_executed':bool(cr.get('executed'))}); use+=1
   inv.append({'season':season,'league':code,'completed_match_count':done,'usable_pre_match_odds_count':use,'file':p.name})
 rows.sort(key=lambda r:(r['date'],r['league'],r['home'],r['away'],r['season']));
 if not rows: raise RuntimeError('no rows')
 opening=metric(rows,'open'); always=metric(rows,'always'); cand=metric(rows,'candidate'); do=delta(opening,cand); da=delta(always,cand)
 nf=int(c['evaluation']['chronological_folds']); folds=[]; cn=an=0
 for k in range(nf):
  lo=len(rows)*k//nf; hi=len(rows)*(k+1)//nf; rr=rows[lo:hi]; ce=evals(rr,'candidate'); ae=evals(rr,'always'); cn+=ce['ll_nondegrade']; an+=ae['ll_nondegrade']; folds.append({'fold':k+1,'n':len(rr),'min_date':str(rr[0]['date']),'max_date':str(rr[-1]['date']),'candidate':ce,'always_side_project':ae})
 groups=[]; cg=ag=0
 for season in c['data']['seasons']:
  for lg in c['data']['leagues']:
   rr=[r for r in rows if r['season']==season and r['league']==lg['code']]; ce=evals(rr,'candidate'); ae=evals(rr,'always'); cg+=ce['ll_nondegrade']; ag+=ae['ll_nondegrade']; groups.append({'season':season,'league':lg['code'],'n':len(rr),'candidate':ce,'always_side_project':ae})
 ac=c['evaluation']['acceptance']; checks={'global_ll':do['logloss']<=ac['candidate_global_logloss_delta_vs_opening_max'],'global_brier':do['brier']<=ac['candidate_global_brier_delta_vs_opening_max'],'global_rps':do['rps']<=ac['candidate_global_rps_delta_vs_opening_max'],'global_top1':do['top1_accuracy']>ac['candidate_global_top1_delta_vs_opening_min_exclusive'],'fold_ll':cn>=ac['candidate_chronological_fold_ll_nondegrade_min'],'group_ll':cg>=ac['candidate_league_season_ll_nondegrade_min'],'fold_ge_always':cn>=an,'ll_le_always':cand['logloss']<=always['logloss'],'top1_ge_always':cand['top1_accuracy']>=always['top1_accuracy']}
 ok=all(checks.values()); status=c['evaluation']['terminal']['pass'] if ok else c['evaluation']['terminal']['fail']; raw=sum(r['raw_side'] for r in rows); con=sum(r['consistent'] for r in rows); ae=sum(r['always_executed'] for r in rows); ce=sum(r['candidate_executed'] for r in rows)
 out={'schema_version':'football3-v3-side-head-consistency-external-confirm-result-v1','status':status,'all_pass':ok,'row_count':len(rows),'invalid_odds_row_count':invalid,'inventory':inv,'counts':{'raw_side_proposal_n':raw,'side_consistent_n':con,'always_executed_n':ae,'candidate_executed_n':ce},'global':{'opening':opening,'always_side_project':always,'candidate':cand,'candidate_delta_vs_opening':do,'candidate_delta_vs_always':da},'chronological_fold_ll_nondegrade':{'candidate':cn,'always':an},'league_season_ll_nondegrade':{'candidate':cg,'always':ag},'checks':checks,'chronological_folds':folds,'league_season_groups':groups,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
 dump(z.out/'side_head_consistency_external_confirm_result.json',out); print(json.dumps({'status':status,'row_count':len(rows),'counts':out['counts'],'global':out['global'],'folds':out['chronological_fold_ll_nondegrade'],'groups':out['league_season_ll_nondegrade'],'checks':checks},sort_keys=True))
if __name__=='__main__': main()
