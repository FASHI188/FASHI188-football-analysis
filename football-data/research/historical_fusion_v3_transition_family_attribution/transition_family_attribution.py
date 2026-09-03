#!/usr/bin/env python3
import argparse,csv,datetime,importlib.util,json,math,pathlib,sys
EPS=1e-15; OUTCOME={'H':0,'D':1,'A':2}; FAMILIES=('DRAW_TO_SIDE_ONLY','SIDE_TO_SIDE_ONLY')
def loadmod(name,path):
 s=importlib.util.spec_from_file_location(name,str(path)); m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m
def loadj(p): return json.loads(pathlib.Path(p).read_text())
def dump(p,x):
 p=pathlib.Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,indent=2,sort_keys=True,allow_nan=False)+'\n')
def norm(v):
 x=[1/float(z) for z in v]; s=sum(x); return [z/s for z in x]
def top1(p): return max(range(3),key=lambda i:(float(p[i]),-i))
def parse_date(s):
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
def proposal_family(op,cp):
 old=top1(op); target=top1(cp)
 if old==target or target==1:return None,target
 if old==1 and target in (0,2):return 'DRAW_TO_SIDE_ONLY',target
 if old in (0,2) and target in (0,2) and old!=target:return 'SIDE_TO_SIDE_ONLY',target
 return None,target
def project(v324,op,target,eps):
 weak=0 if op[0]<op[2] else 2; return v324.minimum_boundary_projection(op,target,weak,eps)
def load_rows(c,data_dir,league_key,v324):
 req=c['data']['required_columns']; eps=float(c['proxy_mapping']['epsilon']); rows=[]; inv=[]; invalid=0
 for season in c['data']['seasons']:
  sp=c['data']['season_paths'][season]
  for lg in c['data'][league_key]:
   code=lg['code']; p=pathlib.Path(data_dir)/f'{sp}_{code}.csv'
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
    op=norm([r['AvgH'],r['AvgD'],r['AvgA']]); cp=norm([r['AvgCH'],r['AvgCD'],r['AvgCA']]); fam,target=proposal_family(op,cp); allp=list(op); dp=list(op); spv=list(op); ar={'executed':False}; dr={'executed':False}; sr={'executed':False}
    if fam in FAMILIES: allp,ar=project(v324,op,target,eps)
    if fam=='DRAW_TO_SIDE_ONLY': dp,dr=project(v324,op,target,eps)
    if fam=='SIDE_TO_SIDE_ONLY': spv,sr=project(v324,op,target,eps)
    rows.append({'season':season,'league':code,'date':parse_date(r['Date']),'home':r['HomeTeam'],'away':r['AwayTeam'],'y':OUTCOME[r['FTR'].strip()],'open':op,'close':cp,'ALL_SIDE':allp,'DRAW_TO_SIDE_ONLY':dp,'SIDE_TO_SIDE_ONLY':spv,'proposal_family':fam,'all_executed':bool(ar.get('executed')),'draw_executed':bool(dr.get('executed')),'side_executed':bool(sr.get('executed'))}); use+=1
   inv.append({'season':season,'league':code,'completed_match_count':done,'usable_pre_match_odds_count':use,'file':p.name})
 rows.sort(key=lambda r:(r['date'],r['league'],r['home'],r['away'],r['season']))
 if not rows:raise RuntimeError('no usable rows')
 return rows,inv,invalid
def summarize(rows,key,nfold,league_key,c):
 b=metric(rows,'open'); q=metric(rows,key); d=delta(b,q); folds=[]; fn=0
 for k in range(nfold):
  lo=len(rows)*k//nfold; hi=len(rows)*(k+1)//nfold; rr=rows[lo:hi]; bb=metric(rr,'open'); qq=metric(rr,key); dd=delta(bb,qq); ok=dd['logloss']<=0; fn+=ok; folds.append({'fold':k+1,'n':len(rr),'min_date':str(rr[0]['date']),'max_date':str(rr[-1]['date']),'deltas':dd,'ll_nondegrade':ok})
 groups=[]; gn=0
 for season in c['data']['seasons']:
  for lg in c['data'][league_key]:
   rr=[r for r in rows if r['season']==season and r['league']==lg['code']]; bb=metric(rr,'open'); qq=metric(rr,key); dd=delta(bb,qq); ok=dd['logloss']<=0; gn+=ok; groups.append({'season':season,'league':lg['code'],'n':len(rr),'deltas':dd,'ll_nondegrade':ok})
 return {'baseline':b,'candidate':q,'deltas':d,'fold_ll_nondegrade_n':fn,'group_ll_nondegrade_n':gn,'folds':folds,'groups':groups}
def gates(summary,allside,g):
 d=summary['deltas']; return {'global_ll':d['logloss']<=g['global_logloss_delta_vs_opening_max'],'global_brier':d['brier']<=g['global_brier_delta_vs_opening_max'],'global_rps':d['rps']<=g['global_rps_delta_vs_opening_max'],'global_top1':d['top1_accuracy']>g['global_top1_delta_vs_opening_min_exclusive'],'fold_ll':summary['fold_ll_nondegrade_n']>=g['chronological_fold_ll_nondegrade_min'],'group_ll':summary['group_ll_nondegrade_n']>=g['league_season_ll_nondegrade_min'],'ll_le_ALL_SIDE':summary['candidate']['logloss']<=allside['candidate']['logloss'],'top1_ge_ALL_SIDE':summary['candidate']['top1_accuracy']>=allside['candidate']['top1_accuracy']}
def run_development(c,data_dir,v324,out):
 rows,inv,invalid=load_rows(c,data_dir,'development_leagues',v324); allside=summarize(rows,'ALL_SIDE',12,'development_leagues',c); board=[]
 for fam in FAMILIES:
  s=summarize(rows,fam,12,'development_leagues',c); ck=gates(s,allside,c['development_protocol']['hard_gates']); board.append({'family':fam,'summary':s,'checks':ck,'all_pass':all(ck.values()),'executed_n':sum(r[fam=='DRAW_TO_SIDE_ONLY' and 'draw_executed' or 'side_executed'] for r in rows)})
 passed=[x for x in board if x['all_pass']]; passed.sort(key=lambda x:(-x['summary']['candidate']['top1_accuracy'],x['summary']['candidate']['logloss'],0 if x['family']=='DRAW_TO_SIDE_ONLY' else 1)); selected=passed[0]['family'] if passed else None
 counts={fam:sum(r['proposal_family']==fam for r in rows) for fam in FAMILIES}; dev={'schema_version':'football3-v3-transition-family-development-v1','row_count':len(rows),'invalid_odds_row_count':invalid,'inventory':inv,'proposal_counts':counts,'ALL_SIDE':allside,'board':board,'selected_family':selected,'confirmation_opened':False}
 dump(out/'development.json',dev)
 if selected:
  freeze={'schema_version':'football3-v3-transition-family-freeze-v1','selected_family':selected,'selection_rule':c['development_protocol']['selection_rule'],'development_rows':len(rows),'development_proposal_counts':counts,'frozen_before_confirmation_download':True}; dump(out/'candidate_freeze.json',freeze)
 else:
  final={'schema_version':'football3-v3-transition-family-final-v1','status':c['confirmation_protocol']['terminal_no_candidate'],'selected_family':None,'development':dev,'confirmation_opened':False,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; dump(out/'final_status.json',final)
 print(json.dumps({'phase':'development','rows':len(rows),'proposal_counts':counts,'selected_family':selected,'board':[{'family':x['family'],'all_pass':x['all_pass'],'top1':x['summary']['candidate']['top1_accuracy'],'ll':x['summary']['candidate']['logloss'],'fold':x['summary']['fold_ll_nondegrade_n'],'group':x['summary']['group_ll_nondegrade_n']} for x in board]},sort_keys=True))
def run_confirmation(c,data_dir,v324,out,freeze_path):
 fr=loadj(freeze_path); fam=fr['selected_family']; assert fam in FAMILIES and fr['frozen_before_confirmation_download'] is True
 rows,inv,invalid=load_rows(c,data_dir,'confirmation_leagues',v324); allside=summarize(rows,'ALL_SIDE',12,'confirmation_leagues',c); s=summarize(rows,fam,12,'confirmation_leagues',c); ck=gates(s,allside,c['confirmation_protocol']['hard_gates']); ok=all(ck.values()); status=c['confirmation_protocol']['terminal_pass'] if ok else c['confirmation_protocol']['terminal_fail']; counts={x:sum(r['proposal_family']==x for r in rows) for x in FAMILIES}
 conf={'schema_version':'football3-v3-transition-family-confirmation-v1','selected_family':fam,'row_count':len(rows),'invalid_odds_row_count':invalid,'inventory':inv,'proposal_counts':counts,'ALL_SIDE':allside,'candidate':s,'checks':ck,'all_pass':ok}; dump(out/'confirmation.json',conf)
 dev=loadj(out/'development.json'); dev['confirmation_opened']=True; dump(out/'development.json',dev)
 final={'schema_version':'football3-v3-transition-family-final-v1','status':status,'selected_family':fam,'development':dev,'candidate_freeze':fr,'confirmation':conf,'confirmation_opened':True,'formal_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; dump(out/'final_status.json',final); print(json.dumps({'phase':'confirmation','status':status,'selected_family':fam,'rows':len(rows),'proposal_counts':counts,'candidate_top1':s['candidate']['top1_accuracy'],'candidate_ll':s['candidate']['logloss'],'fold':s['fold_ll_nondegrade_n'],'group':s['group_ll_nondegrade_n'],'checks':ck},sort_keys=True))
def main():
 p=argparse.ArgumentParser(); p.add_argument('--phase',choices=['development','confirmation'],required=True); p.add_argument('--contract',required=True,type=pathlib.Path); p.add_argument('--data-dir',required=True,type=pathlib.Path); p.add_argument('--v324-runner',required=True,type=pathlib.Path); p.add_argument('--out',required=True,type=pathlib.Path); p.add_argument('--freeze',type=pathlib.Path); a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=loadj(a.contract); assert c['status']=='FROZEN_BEFORE_DEVELOPMENT_OR_CONFIRMATION_DOWNLOAD'; v324=loadmod('tfa_v324',a.v324_runner)
 if a.phase=='development': run_development(c,a.data_dir,v324,a.out)
 else:
  if not a.freeze: raise RuntimeError('--freeze required'); run_confirmation(c,a.data_dir,v324,a.out,a.freeze)
if __name__=='__main__':main()
