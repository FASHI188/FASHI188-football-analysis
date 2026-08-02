#!/usr/bin/env python3
from __future__ import annotations
import hashlib,math,time
from typing import Any,Sequence
import numpy as np
from draw_auto_research_baseline_r1 import baseline_identity,baseline_predictions
from draw_auto_research_engine_data_r1 import OuterFold
from draw_auto_research_engine_fit_r1 import fit_for_l2,fit_predict_split,labels
from draw_auto_research_gate_r1 import DEFAULT_GATE,evaluate_challenger_gate
from draw_auto_research_math_r1 import metric_delta,metrics
def fingerprint(p):return hashlib.sha256(np.round(np.asarray(p,float),12).tobytes(order='C')).hexdigest()
def evaluate_candidate(candidate:dict[str,Any],folds:Sequence[OuterFold],seen_prediction_fingerprints:set[str]|None=None,challenger_gate:dict[str,Any]|None=None)->dict[str,Any]:
 started=time.monotonic();frs=[];prs=[];fold_results=[];cps=[];bps=[];all_labels=[];bff={}
 for fold in folds:
  trials=[fit_for_l2(fold.inner_train_rows,fold.inner_validation_rows,candidate,float(l2)) for l2 in candidate['l2_grid']];valid=[t for t in trials if math.isfinite(float(t.get('objective',math.inf)))]
  if not valid:raise ValueError(f'all inner trials failed: {fold.fold_id}')
  sel=min(valid,key=lambda t:(float(t['objective']),float(t['l2'])));cp,fr,pr=fit_predict_split(fold.train_rows,fold.evaluation_rows,candidate,float(sel['l2']));bp,_,br=baseline_predictions(fold.train_rows,fold.evaluation_rows);ls,_=labels(fold.evaluation_rows);cm=metrics(cp,ls);bm=metrics(bp,ls);fr|={'fold_id':fold.fold_id};pr|={'fold_id':fold.fold_id};frs.append(fr);prs.append(pr);bff[fold.fold_id]=fingerprint(bp);fold_results.append({'fold_id':fold.fold_id,'competition':fold.competition,'target_season':fold.target_season,'prior_seasons':list(fold.prior_seasons),'inner_train_seasons':list(fold.inner_train_seasons),'inner_validation_season':fold.inner_validation_season,'selected_l2':sel['l2'],'inner_trials':trials,'candidate_metrics':cm,'baseline_metrics':bm,'delta':metric_delta(cm,bm),'fit_receipt':fr,'preprocessing_receipt':pr,'baseline_receipt':br,'baseline_prediction_fingerprint':bff[fold.fold_id],'row_count':len(ls)});cps.append(cp);bps.append(bp);all_labels+=ls
 ca=np.vstack(cps);ba=np.vstack(bps);pcm=metrics(ca,all_labels);pbm=metrics(ba,all_labels);leagues={}
 for comp in sorted({f.competition for f in folds}):
  ix=[i for i,f in enumerate(folds) if f.competition==comp];cp=np.vstack([cps[i] for i in ix]);bp=np.vstack([bps[i] for i in ix]);ls=[]
  for i in ix:ls += [r.label for r in folds[i].evaluation_rows]
  cm=metrics(cp,ls);bm=metrics(bp,ls);leagues[comp]={'candidate_metrics':cm,'baseline_metrics':bm,'delta':metric_delta(cm,bm)}
 d=metric_delta(pcm,pbm);score=float(d['Draw F1']+.2*d['Macro-F1']+.1*d['Accuracy']-.2*max(0.,d['Log Loss'])-.15*max(0.,d['RPS'])-.1*max(0.,d['Draw ECE']));fp=fingerprint(ca);unique=seen_prediction_fingerprints is None or fp not in seen_prediction_fingerprints
 result={'schema_version':'DRAW-AUTO-CANDIDATE-RESULT-R1.4','data_status':'VIEWED_DEVELOPMENT_DATA','baseline':baseline_identity(),'candidate':candidate,'status':'COMPLETED','fold_count':len(fold_results),'pooled_candidate_metrics':pcm,'pooled_baseline_metrics':pbm,'pooled_delta':d,'ranking_score':score,'fold_results':fold_results,'league_results':leagues,'fit_receipts':frs,'preprocessing_receipts':prs,'candidate_prediction_fingerprint':fp,'prediction_fingerprint_unique':unique,'baseline_fold_fingerprints':bff,'safety_gates':{'all_51_folds_present':len(fold_results)==51,'all_fits_converged':all(x['converged'] for x in frs),'probability_gates_pass':all(x['probability_gate_pass'] for x in frs),'evaluation_rows_used_for_preprocessing_decisions':sum(x['evaluation_rows_used_for_decisions'] for x in prs),'random_split_used':False,'fixed_baseline_candidate_parameter_count':0},'runtime_seconds':time.monotonic()-started}
 result['challenger_gate']=evaluate_challenger_gate(result,challenger_gate or DEFAULT_GATE);return result
def validate_candidate_result(r):
 if r.get('status')!='COMPLETED':raise ValueError('candidate result incomplete')
 if r.get('fold_count')!=51 or len(r.get('fold_results') or [])!=51:raise ValueError('fold completeness failure')
 if len(r.get('league_results') or {})!=17:raise ValueError('league results incomplete')
 g=r.get('safety_gates') or {}
 if not g.get('all_51_folds_present') or not g.get('all_fits_converged') or not g.get('probability_gates_pass'):raise ValueError('numerical safety failure')
 if g.get('evaluation_rows_used_for_preprocessing_decisions')!=0:raise ValueError('evaluation leakage')
 if g.get('fixed_baseline_candidate_parameter_count')!=0:raise ValueError('baseline contamination')
 if not r.get('prediction_fingerprint_unique'):raise ValueError('equivalent prediction')
 req={'Accuracy','Macro-F1','Draw Precision','Draw Recall','Draw F1','Log Loss','Brier','RPS','Draw ECE','Top-label ECE'}
 if not req.issubset(r.get('pooled_candidate_metrics') or {}):raise ValueError('metrics incomplete')
 if r.get('challenger_gate',{}).get('status') not in {'PASS','FAIL'}:raise ValueError('challenger gate missing')
