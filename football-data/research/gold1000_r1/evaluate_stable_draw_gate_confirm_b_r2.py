#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,sys,warnings
from pathlib import Path
import numpy as np
from sklearn.base import clone

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import run_selective_draw_gate_r1 as r1

def hf(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--development',type=Path,required=True);ap.add_argument('--confirmation-b',type=Path,required=True);ap.add_argument('--selection',type=Path,required=True);ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 selection=json.loads(a.selection.read_text(encoding='utf-8'));prereg=json.loads(a.prereg.read_text(encoding='utf-8'))
 if selection.get('selection_uses_confirmation_b') is not False:raise ValueError('selection boundary violation')
 winner=selection['winner'];model_name=winner['model'];threshold=float(winner['policy_threshold'])
 dev=[r for r in r1.read(a.development) if r.get('market_close_fair_draw') not in ('',None)]
 sp=r1.split_dev(dev);Xtrain=[r1.features(r) for r in sp['train']];Xcal=[r1.features(r) for r in sp['calib']];ytrain=r1.yarr(sp['train']);ycal=r1.yarr(sp['calib'])
 available=r1.models()
 if model_name not in available:raise ValueError(f'unknown frozen model {model_name}')
 model=clone(available[model_name])
 with warnings.catch_warnings():warnings.simplefilter('ignore');model.fit(Xtrain,ytrain)
 raw_cal=model.predict_proba(Xcal)[:,1];platt=r1.platt(raw_cal,ycal)
 # Confirmation B is opened only after selection and model identity are frozen.
 conf=[r for r in r1.read(a.confirmation_b) if r.get('market_close_fair_draw') not in ('',None)]
 if len(conf)!=305:raise ValueError(f'confirmation B market rows {len(conf)} != 305')
 raw_b=model.predict_proba([r1.features(r) for r in conf])[:,1];prob=r1.pp(platt,raw_b);metrics=r1.metrics(conf,prob,threshold);boot=r1.cluster_boot(conf,prob,threshold,n=1000,seed=20260804)
 gate=prereg['confirmation_b_pass_gate'];passed=(metrics['coverage']<=float(gate['coverage_max'])+1e-12 and metrics['precision']>=float(gate['precision_min']) and metrics['accuracy_delta']>=float(gate['complete_1x2_accuracy_delta_min']) and metrics['selected']>=int(gate['minimum_selected']) and metrics['recall']>=float(gate['minimum_draw_recall']) and metrics['f1']>=float(gate['minimum_draw_f1']))
 result={'schema_version':'SELECTIVE-DRAW-STABILITY-CONFIRM-B-R2','status':'PASS_RESEARCH_GATE' if passed else 'FAIL_RESEARCH_GATE','formal_weight':0,'selection_uses_confirmation_b_labels':False,'model':model_name,'target_coverage':float(winner['target_coverage']),'threshold':threshold,'confirmation_b_rows':len(conf),'confirmation_b_draws':int(np.sum(r1.yarr(conf))),'metrics':metrics,'competition_cluster_bootstrap_90pct':boot,'pass_gate':gate,'gate_passed':passed,'interpretation':prereg['confirmation_b_interpretation']['pass' if passed else 'fail']}
 rp=a.out/'SELECTIVE_DRAW_STABLE_R2_confirmation_b_result.json';rp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 base=r1.base_pred(conf);sel=prob>=threshold;truth=np.asarray([r['label_result'] for r in conf]);pp=a.out/'SELECTIVE_DRAW_STABLE_R2_confirmation_b_predictions.csv';fields=['match_identity','competition_id','season','date','home_team','away_team','label_result','score','threshold','selected','baseline_1x2','gate_1x2']
 with pp.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for i,r in enumerate(conf):w.writerow({k:r[k] for k in fields[:7]}|{'score':float(prob[i]),'threshold':threshold,'selected':int(sel[i]),'baseline_1x2':base[i],'gate_1x2':'D' if sel[i] else base[i]})
 per=[]
 for comp in sorted({r['competition_id'] for r in conf}):
  idx=[i for i,r in enumerate(conf) if r['competition_id']==comp];m=r1.metrics([conf[i] for i in idx],prob[idx],threshold);per.append({'competition_id':comp,'rows':len(idx),**m})
 cp=a.out/'SELECTIVE_DRAW_STABLE_R2_confirmation_b_by_competition.csv'
 with cp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(per[0]));w.writeheader();w.writerows(per)
 receipt={'schema_version':'SELECTIVE-DRAW-STABILITY-RECEIPT-R2','status':'PASS_EXECUTION','gate_passed':passed,'development_sha256':hf(a.development),'confirmation_b_sha256':hf(a.confirmation_b),'selection_sha256':hf(a.selection),'prereg_sha256':hf(a.prereg),'provider_api_calls':0,'formal_weight':0,'formal_asset_writes':0,'outputs':{p.name:hf(p) for p in (rp,pp,cp)}}
 ep=a.out/'SELECTIVE_DRAW_STABLE_R2_receipt.json';ep.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'PASS_EXECUTION','gate_passed':passed,'model':model_name,'rows':len(conf),'metrics':metrics},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
