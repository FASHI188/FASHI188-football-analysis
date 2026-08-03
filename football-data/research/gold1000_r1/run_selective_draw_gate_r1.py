#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math,re,warnings
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
import numpy as np
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesClassifier,HistGradientBoostingClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score,roc_auc_score,log_loss,brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

P0=['away_elo_pre_match','away_history_ga','away_history_gf','away_history_matches','away_history_ppg','away_last5_ga','away_last5_gf','away_last5_matches','away_last5_ppg','away_venue_ga','away_venue_gf','away_venue_matches','cold_start_flag','elo_difference_with_home_advantage','home_elo_pre_match','home_history_ga','home_history_gf','home_history_matches','home_history_ppg','home_last5_ga','home_last5_gf','home_last5_matches','home_last5_ppg','home_venue_ga','home_venue_gf','home_venue_matches','stage_unverified_flag','home_rest_days','away_rest_days','home_matches_7d','away_matches_7d','home_matches_14d','away_matches_14d','home_matches_28d','away_matches_28d','home_pre_points','away_pre_points','home_pre_rank','away_pre_rank','points_gap_h_minus_a','rank_gap_h_minus_a','home_pre_gd','away_pre_gd','home_pre_ppg','away_pre_ppg','home_draw_rate_5','away_draw_rate_5','home_draw_rate_10','away_draw_rate_10','home_draw_rate_20','away_draw_rate_20','home_home_draw_rate_10','away_away_draw_rate_10','home_avg_total_goals_10','away_avg_total_goals_10','home_clean_sheet_rate_10','away_clean_sheet_rate_10','home_failed_to_score_rate_10','away_failed_to_score_rate_10','home_one_goal_margin_rate_10','away_one_goal_margin_rate_10','h2h_draw_rate_5','season_progress_home','season_progress_away']
COVERAGES=[.05,.075,.10,.125,.15,.20,.25,.30,.35,.40]

def hf(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def read(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(v):
 try:
  x=float(v);return x if math.isfinite(x) else np.nan
 except (TypeError,ValueError):return np.nan
def start(s):
 m=re.match(r'(\d{4})',s);return int(m.group(1)) if m else 9999
def yarr(rows):return np.asarray([int(r['label_result']=='D') for r in rows],int)
def features(r):
 ph,pd,pa=(num(r['market_close_fair_home']),num(r['market_close_fair_draw']),num(r['market_close_fair_away']))
 d={'competition':r['competition_id'],'p_home':ph,'p_draw':pd,'p_away':pa,'overround':num(r.get('market_close_overround')),'balance':num(r.get('market_close_balance')),'favorite':num(r.get('market_close_favorite')),'entropy':num(r.get('market_close_entropy'))}
 d['ha_gap']=abs(ph-pa);d['ha_closeness']=1-abs(ph-pa);d['max_ha']=max(ph,pa);d['draw_margin']=pd-max(ph,pa);d['draw_ratio_to_fav']=pd/max(ph,pa);d['draw_x_closeness']=pd*(1-abs(ph-pa))
 for c in P0:d[c]=num(r.get(c))
 return d
def split_dev(rows):
 out=defaultdict(list)
 for comp in sorted({r['competition_id'] for r in rows}):
  rr=[r for r in rows if r['competition_id']==comp];seasons=sorted({r['season'] for r in rr},key=start)
  if len(seasons)!=3:raise ValueError(f'{comp}: expected three seasons, found {seasons}')
  s0,s1,s2=seasons;mid=sorted([r for r in rr if r['season']==s1],key=lambda r:(r['date'],r['home_team'],r['away_team']));cut=(len(mid)+1)//2;cal={r['match_identity'] for r in mid[:cut]}
  for r in rr:
   phase='train' if r['season']==s0 else ('test' if r['season']==s2 else ('calib' if r['match_identity'] in cal else 'policy'))
   out[phase].append(r)
 return out
def pipe(model,scale=False):
 steps=[('vec',DictVectorizer(sparse=False)),('imp',SimpleImputer(strategy='median',add_indicator=True))]
 if scale:steps.append(('scale',StandardScaler()))
 steps.append(('model',model));return Pipeline(steps)
def models():
 out={}
 for C in (.1,1.,10.):
  for cw in (None,'balanced'):out[f'logit_C{C}_cw{cw or "none"}']=pipe(LogisticRegression(C=C,class_weight=cw,max_iter=5000,solver='liblinear',random_state=42),True)
 for leaves in (3,7):
  for l2 in (1.,4.):out[f'hgb_leaf{leaves}_l2{l2}']=pipe(HistGradientBoostingClassifier(max_leaf_nodes=leaves,l2_regularization=l2,learning_rate=.05,max_iter=150,min_samples_leaf=15,random_state=42))
 for leaf in (8,16):out[f'extratrees_leaf{leaf}']=pipe(ExtraTreesClassifier(n_estimators=500,min_samples_leaf=leaf,max_features=.7,class_weight='balanced',random_state=42,n_jobs=-1))
 return out
def platt(raw,y):
 z=np.log(np.clip(raw,1e-6,1-1e-6)/(1-np.clip(raw,1e-6,1-1e-6))).reshape(-1,1);m=LogisticRegression(C=1e6,solver='lbfgs',max_iter=2000,random_state=42);m.fit(z,y);return m
def pp(m,raw):
 z=np.log(np.clip(raw,1e-6,1-1e-6)/(1-np.clip(raw,1e-6,1-1e-6))).reshape(-1,1);return m.predict_proba(z)[:,1]
def market(rows):return np.asarray([num(r['market_close_fair_draw']) for r in rows],float)
def base_pred(rows):return np.asarray([('H','D','A')[int(np.argmax([num(r['market_close_fair_home']),num(r['market_close_fair_draw']),num(r['market_close_fair_away'])]))] for r in rows])
def wlower(s,n):
 if not n:return 0.
 z=NormalDist().inv_cdf(.95);p=s/n;den=1+z*z/n;return (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/den
def metrics(rows,prob,thr):
 y=yarr(rows);sel=prob>=thr;tp=int(np.sum(sel&(y==1)));fp=int(np.sum(sel&(y==0)));fn=int(np.sum((~sel)&(y==1)));prec=tp/(tp+fp) if tp+fp else 0.;rec=tp/(tp+fn) if tp+fn else 0.;f1=2*prec*rec/(prec+rec) if prec+rec else 0.
 truth=np.asarray([r['label_result'] for r in rows]);base=base_pred(rows);gate=base.copy();gate[sel]='D';ba=float(np.mean(base==truth));ga=float(np.mean(gate==truth))
 return {'threshold':float(thr),'coverage':float(np.mean(sel)),'selected':int(np.sum(sel)),'tp':tp,'fp':fp,'fn':fn,'precision':prec,'recall':rec,'f1':f1,'wilson90_precision_lower':wlower(tp,tp+fp),'base_1x2_accuracy':ba,'gate_1x2_accuracy':ga,'accuracy_delta':ga-ba,'pr_auc':float(average_precision_score(y,prob)),'roc_auc':float(roc_auc_score(y,prob)),'log_loss':float(log_loss(y,np.clip(prob,1e-6,1-1e-6))),'brier':float(brier_score_loss(y,prob))}
def threshold(prob,cov):
 k=max(1,int(round(len(prob)*cov)));return float(np.sort(prob)[::-1][min(k-1,len(prob)-1)])
def choose(records,lane):
 ok=[]
 for r in records:
  if lane=='conservative':valid=r['policy_coverage']<=.15+1e-12 and r['policy_precision']>=.40 and r['policy_accuracy_delta']>=-.01;score=(r['policy_f1'],r['policy_precision'],-r['policy_coverage'])
  elif lane=='balanced':valid=r['policy_coverage']<=.25+1e-12 and r['policy_precision']>=.35 and r['policy_accuracy_delta']>=-.02;score=(r['policy_f1'],r['policy_recall'],r['policy_precision'])
  elif lane=='recall':valid=r['policy_coverage']<=.40+1e-12 and r['policy_precision']>=.30 and r['policy_accuracy_delta']>=-.04;score=(r['policy_recall'],r['policy_f1'],r['policy_precision'])
  else:valid=r['policy_coverage']<=.30+1e-12 and r['policy_selected']>=15 and r['policy_wilson90_precision_lower']>=.30 and r['policy_accuracy_delta']>=-.03;score=(r['policy_coverage'],r['policy_precision'],r['policy_f1'])
  if valid:ok.append((score,r))
 return max(ok,key=lambda x:x[0])[1] if ok else None
def cluster_boot(rows,prob,thr,n=1000,seed=20260803):
 rng=np.random.default_rng(seed);comps=sorted({r['competition_id'] for r in rows});by={c:[i for i,r in enumerate(rows) if r['competition_id']==c] for c in comps};vals=defaultdict(list)
 for _ in range(n):
  idx=[]
  for c in rng.choice(comps,size=len(comps),replace=True):idx.extend(by[c])
  rr=[rows[i] for i in idx];m=metrics(rr,prob[idx],thr)
  for k in ('coverage','precision','recall','f1','accuracy_delta'):vals[k].append(m[k])
 return {k:{'p05':float(np.quantile(v,.05)),'p50':float(np.quantile(v,.5)),'p95':float(np.quantile(v,.95))} for k,v in vals.items()}
def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--development',type=Path,required=True);ap.add_argument('--confirmation',type=Path,required=True);ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 prereg=json.loads(a.prereg.read_text(encoding='utf-8'));dev=[r for r in read(a.development) if r.get('market_close_fair_draw') not in ('',None)];conf=[r for r in read(a.confirmation) if r.get('market_close_fair_draw') not in ('',None)]
 if len(dev)!=903:raise ValueError(f'development market rows {len(dev)} != 903')
 if len(conf)!=305:raise ValueError(f'confirmation market rows {len(conf)} != 305')
 sp=split_dev(dev);X={k:[features(r) for r in v] for k,v in sp.items()};Xc=[features(r) for r in conf];Y={k:yarr(v) for k,v in sp.items()}
 outputs={};raws={}
 raw={k:market(v) for k,v in sp.items()};rawc=market(conf);cal=platt(raw['calib'],Y['calib']);outputs['market_score']={k:pp(cal,raw[k]) for k in ('policy','test')};outputs['market_score']['confirm']=pp(cal,rawc);raws['market_score']={**{k:raw[k] for k in ('calib','policy','test')},'confirm':rawc}
 for name,model in models().items():
  m=clone(model)
  with warnings.catch_warnings():warnings.simplefilter('ignore');m.fit(X['train'],Y['train'])
  rp={k:m.predict_proba(X[k])[:,1] for k in ('calib','policy','test')};rp['confirm']=m.predict_proba(Xc)[:,1];cm=platt(rp['calib'],Y['calib']);outputs[name]={k:pp(cm,rp[k]) for k in ('policy','test','confirm')};raws[name]=rp
 outputs['ensemble_tree_mean']={k:np.mean([outputs[n][k] for n in ('hgb_leaf3_l24.0','extratrees_leaf8','extratrees_leaf16')],axis=0) for k in ('policy','test','confirm')}
 outputs['ensemble_diverse_mean']={k:np.mean([outputs[n][k] for n in ('market_score','logit_C1.0_cwnone','hgb_leaf3_l24.0','extratrees_leaf16')],axis=0) for k in ('policy','test','confirm')}
 stack_names=('market_score','logit_C1.0_cwnone','hgb_leaf3_l24.0','extratrees_leaf16');meta=LogisticRegression(C=1.,solver='liblinear',random_state=42);meta.fit(np.column_stack([raws[n]['calib'] for n in stack_names]),Y['calib']);outputs['stacked_logit']={k:meta.predict_proba(np.column_stack([raws[n][k] for n in stack_names]))[:,1] for k in ('policy','test','confirm')}
 records=[]
 for name,out in outputs.items():
  for cov in COVERAGES:
   th=threshold(out['policy'],cov);pm=metrics(sp['policy'],out['policy'],th);tm=metrics(sp['test'],out['test'],th);cm=metrics(conf,out['confirm'],th);r={'model':name,'target_coverage':cov};r.update({f'policy_{k}':v for k,v in pm.items()});r.update({f'diagnostic_test_{k}':v for k,v in tm.items()});r.update({f'confirm_{k}':v for k,v in cm.items()});records.append(r)
 selected={lane:choose(records,lane) for lane in ('conservative','balanced','recall','risk_control')};boot={}
 for lane,r in selected.items():
  if r:boot[lane]=cluster_boot(conf,outputs[r['model']]['confirm'],r['policy_threshold'])
 cp=a.out/'SELECTIVE_DRAW_GATE_R1_candidate_grid.csv';fields=list(records[0])
 with cp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(records)
 summary={'schema_version':'SELECTIVE-DRAW-GATE-RESULT-R1','status':'RESEARCH_ONLY','formal_weight':0,'selection_used_confirmation_labels':False,'counts':{k:len(v) for k,v in sp.items()}|{'confirmation':len(conf)},'draws':{k:int(np.sum(Y[k])) for k in Y}|{'confirmation':int(np.sum(yarr(conf)))},'selected_lanes':selected,'confirmation_cluster_bootstrap_90pct':boot}
 spath=a.out/'SELECTIVE_DRAW_GATE_R1_selected_lanes.json';spath.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 pf=['match_identity','competition_id','season','date','home_team','away_team','label_result','baseline_1x2'];lanes=[x for x,r in selected.items() if r];pf += [z for lane in lanes for z in (f'{lane}_model',f'{lane}_score',f'{lane}_threshold',f'{lane}_selected',f'{lane}_gate_1x2')]
 ppred=a.out/'SELECTIVE_DRAW_GATE_R1_confirmation_predictions.csv';base=base_pred(conf)
 with ppred.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=pf);w.writeheader()
  for i,r in enumerate(conf):
   o={k:r[k] for k in ('match_identity','competition_id','season','date','home_team','away_team','label_result')};o['baseline_1x2']=base[i]
   for lane in lanes:
    sr=selected[lane];score=float(outputs[sr['model']]['confirm'][i]);flag=score>=sr['policy_threshold'];o[f'{lane}_model']=sr['model'];o[f'{lane}_score']=score;o[f'{lane}_threshold']=sr['policy_threshold'];o[f'{lane}_selected']=int(flag);o[f'{lane}_gate_1x2']='D' if flag else base[i]
   w.writerow(o)
 per=[]
 for lane in ('conservative','balanced','recall','risk_control'):
  sr=selected.get(lane)
  if not sr:continue
  prob=outputs[sr['model']]['confirm'];th=sr['policy_threshold']
  for comp in sorted({r['competition_id'] for r in conf}):
   idx=[i for i,r in enumerate(conf) if r['competition_id']==comp];m=metrics([conf[i] for i in idx],prob[idx],th);per.append({'lane':lane,'competition_id':comp,'rows':len(idx),**m})
 perpath=a.out/'SELECTIVE_DRAW_GATE_R1_confirmation_by_competition.csv'
 with perpath.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(per[0]));w.writeheader();w.writerows(per)
 rec={'schema_version':'SELECTIVE-DRAW-GATE-RECEIPT-R1','status':'PASS_RESEARCH_ONLY','prereg_sha256':hf(a.prereg),'development_sha256':hf(a.development),'confirmation_sha256':hf(a.confirmation),'sklearn_version':__import__('sklearn').__version__,'provider_api_calls':0,'formal_weight':0,'formal_asset_writes':0,'outputs':{p.name:hf(p) for p in (cp,spath,ppred,perpath)}}
 rp=a.out/'SELECTIVE_DRAW_GATE_R1_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'PASS_RESEARCH_ONLY','confirmation_rows':len(conf),'selected':{k:(v and {'model':v['model'],'confirm_precision':v['confirm_precision'],'confirm_recall':v['confirm_recall'],'confirm_f1':v['confirm_f1'],'confirm_accuracy_delta':v['confirm_accuracy_delta']}) for k,v in selected.items()}},ensure_ascii=False))
 return 0
if __name__=='__main__':raise SystemExit(main())
