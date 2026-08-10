#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

R39W_PATH=Path('football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py')
R39W_EXPECTED_BLOB='4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae'
LABELS=('H','D','A')

def load_w():
    got=subprocess.check_output(['git','rev-parse',f'HEAD:{R39W_PATH.as_posix()}'],text=True).strip()
    if got!=R39W_EXPECTED_BLOB: raise RuntimeError(f'R39W_EVALUATOR_BLOB_DRIFT:{got}')
    spec=importlib.util.spec_from_file_location('r39w_for_r40i',R39W_PATH)
    if spec is None or spec.loader is None: raise RuntimeError('IMPORT_SPEC_FAIL')
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m); return m

def key(raw):
    try: d=date.fromisoformat(str(raw.get('date','')).strip())
    except Exception: return None
    vals=[str(raw.get(k,'')).strip() for k in ('competition_id','season','home_team','away_team')]
    if not all(vals): return None
    return f'{vals[0]}|{vals[1]}|{d.isoformat()}|{vals[2]}|{vals[3]}'

def fnum(v):
    try: x=float(str(v).strip())
    except Exception: return None
    return x if math.isfinite(x) else None

def load_elo_mean(root):
    out={}; seen=0; bad=0
    for p in sorted(root.glob('football-data/training_datasets/*/point_in_time.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            for raw in csv.DictReader(f):
                seen+=1; k=key(raw); h=fnum(raw.get('home_elo_pre_match')); a=fnum(raw.get('away_elo_pre_match'))
                if k is None or h is None or a is None: bad+=1; continue
                if k in out: raise RuntimeError(f'DUPLICATE_ELO_KEY:{k}')
                out[k]=(h+a)/2.0
    return out,{'rows_seen':seen,'elo_level_rows':len(out),'invalid_rows':bad}

def transform(train_raw,target_raw,u):
    d=len(target_raw); z=[1.0]
    for j in range(d):
        vals=[r[j] for r in train_raw]; med=u.quantile(vals,.5); s=u.quantile(vals,.75)-u.quantile(vals,.25)
        if not math.isfinite(s) or s<1e-9: s=1.0
        z.append((target_raw[j]-med)/s)
    return z

def combine(pd,ph):
    return {'D':pd,'H':(1-pd)*ph,'A':(1-pd)*(1-ph)}

def bdrawll(rs):
    s=0.0
    for r in rs:
        p=min(max(float(r['probabilities']['D']),1e-12),1-1e-12); y=1.0 if r['actual']=='D' else 0.0
        s+=-(y*math.log(p)+(1-y)*math.log(1-p))
    return s/len(rs)

def auc(w,rs):
    y=[r['actual']=='D' for r in rs]
    return w.auc_binary([float(r['probabilities']['D']) for r in rs],y)

def sval(x): return float(x) if x is not None else -1.0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--prereg',type=Path,required=True); ap.add_argument('--root',type=Path,default=Path('.')); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    pre=json.loads(a.prereg.read_text()); assert pre['schema_version']=='R40I-ROLLING-ELO-LEVEL-DRAW-1.0'
    fcfg=pre['folding']; mcfg=pre['models']; hard=pre['hard_boundaries']
    assert fcfg['minimum_train_rows']==200 and fcfg['minimum_test_rows']==80 and fcfg['same_season_training'] is False
    assert mcfg['class_weighting'] is False and mcfg['candidate_grid'] is False and mcfg['manual_draw_boost'] is False and mcfg['threshold_tuning'] is False and mcfg['post_result_tuning'] is False and mcfg['abstention'] is False
    assert hard['external_network_requests']==0 and hard['football_api_requests']==0 and hard['new_data_collection'] is False and hard['random_fixed100_consumed']==0
    w=load_w(); u=w.load_r39u_module(); rows=[r for r in u.load_rows(a.root) if u.finite_vec(r.x)]; emap,eaudit=load_elo_mean(a.root)
    missing=[r.key for r in rows if r.key not in emap]
    if missing: raise RuntimeError(f'ELO_COVERAGE_MISSING:{len(missing)}:{missing[:3]}')
    by=defaultdict(list)
    for r in rows: by[r.competition].append(r)
    for c in by: by[c].sort(key=lambda r:(r.dt,r.key))
    allb=[]; allc=[]; folds=[]; skipped=[]; mintr=fcfg['minimum_train_rows']; minte=fcfg['minimum_test_rows']
    for comp,cr in sorted(by.items()):
        ss=defaultdict(list)
        for r in cr: ss[r.season].append(r)
        for season,test in sorted(ss.items(),key=lambda kv:(min(r.dt for r in kv[1]),kv[0])):
            start=min(r.dt for r in test); tr=[r for r in cr if r.dt<start]
            if len(tr)<mintr or len(test)<minte: skipped.append({'competition':comp,'season':season,'train_n':len(tr),'test_n':len(test)}); continue
            base_raw=[r.x for r in tr]; chal_raw=[tuple(r.x)+(emap[r.key],) for r in tr]
            Xb,_=w.standardize(base_raw,base_raw[0],u.quantile); Xc,_=w.standardize(chal_raw,chal_raw[0],u.quantile); yd=[1 if r.label=='D' else 0 for r in tr]
            bb,itb=w.fit_logistic(Xb,yd,10.0); bc,itc=w.fit_logistic(Xc,yd,10.0)
            nd=[r for r in tr if r.label!='D']; side_raw=[w.side_features(r.x) for r in nd]; Xs,_=w.standardize(side_raw,side_raw[0],u.quantile); ys=[1 if r.label=='H' else 0 for r in nd]; bs,its=w.fit_logistic(Xs,ys,10.0)
            fb=[]; fc=[]
            for r in test:
                xb=transform(base_raw,r.x,u); xc=transform(chal_raw,tuple(r.x)+(emap[r.key],),u); xs=transform(side_raw,w.side_features(r.x),u)
                pd0=w.predict_prob(bb,xb); pd1=w.predict_prob(bc,xc); ph=w.predict_prob(bs,xs); pb=combine(pd0,ph); pc=combine(pd1,ph)
                rb={'competition':comp,'season':season,'key':r.key,'actual':r.label,'prediction':u.choose(pb),'probabilities':pb}; rc={'competition':comp,'season':season,'key':r.key,'actual':r.label,'prediction':u.choose(pc),'probabilities':pc,'elo_mean_pre_match':emap[r.key]}
                fb.append(rb); fc.append(rc); allb.append(rb); allc.append(rc)
            mb=u.metrics(fb); mc=u.metrics(fc); bll=bdrawll(fb); cll=bdrawll(fc)
            folds.append({'competition':comp,'season':season,'train_n':len(tr),'test_n':len(test),'baseline_metrics':mb,'challenger_metrics':mc,'baseline_draw_ll':bll,'challenger_draw_ll':cll,'draw_ll_win':cll<bll,'baseline_auc':auc(w,fb),'challenger_auc':auc(w,fc),'iters':{'baseline':itb,'challenger':itc,'side':its}})
    bm=u.metrics(allb); cm=u.metrics(allc); bll=bdrawll(allb); cll=bdrawll(allc); ba=auc(w,allb); ca=auc(w,allc); fw=sum(x['draw_ll_win'] for x in folds); fwr=fw/len(folds)
    cb=defaultdict(list); cc=defaultdict(list); nf=defaultdict(int)
    for x in folds: nf[x['competition']]+=1
    for r in allb: cb[r['competition']].append(r)
    for r in allc: cc[r['competition']].append(r)
    comps=[]
    for c in sorted(cb):
        x={'competition':c,'folds':nf[c],'n':len(cb[c]),'baseline_draw_ll':bdrawll(cb[c]),'challenger_draw_ll':bdrawll(cc[c])}; x['draw_ll_win']=x['challenger_draw_ll']<x['baseline_draw_ll']; comps.append(x)
    ec=[x for x in comps if x['folds']>=2]; cwr=sum(x['draw_ll_win'] for x in ec)/len(ec)
    gate={'coverage_ge_0_995':len(emap)/len(rows)>=.995,'HDA_log_loss_better':cm['log_loss']<bm['log_loss'],'binary_draw_log_loss_better':cll<bll,'draw_auc_plus_0_003':ca>=ba+.003,'draw_f1_plus_0_02':sval(cm['draw_f1'])>=sval(bm['draw_f1'])+.02,'draw_recall_nonworse':sval(cm['draw_recall'])>=sval(bm['draw_recall']),'accuracy_noninferior':cm['accuracy']>=bm['accuracy']-.01,'fold_win_rate_ge_0_55':fwr>=.55,'competition_win_rate_ge_0_60':cwr>=.60}
    passed=all(gate.values()); terminal='PASS_R40I_ELO_LEVEL_DRAW_INCREMENT' if passed else 'FAIL_R40I_ELO_LEVEL_DRAW_NO_INCREMENT'
    out={'schema_version':pre['schema_version'],'terminal':terminal,'passed':passed,'source_rows':len(rows),'elo_audit':eaudit,'coverage':len(emap)/len(rows),'eligible_fold_count':len(folds),'rows_scored':len(allb),'competition_count':len(cb),'baseline_metrics':bm,'challenger_metrics':cm,'baseline_binary_draw_log_loss':bll,'challenger_binary_draw_log_loss':cll,'baseline_draw_auc':ba,'challenger_draw_auc':ca,'fold_draw_ll_win_rate':fwr,'competition_draw_ll_win_rate':cwr,'fold_metrics':folds,'competition_metrics':comps,'skipped_folds':skipped,'gate':gate,'hard_boundaries':hard,'interpretation_boundary':'Retrospective rolling absolute-Elo challenger only; no fixed100 consumed; formal_weight remains 0.'}
    a.out.mkdir(parents=True,exist_ok=True); raw=json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+'\n'; (a.out/'r40i_result.json').write_text(raw); (a.out/'r40i_result.sha256').write_text(hashlib.sha256(raw.encode()).hexdigest()+'\n')
    print(json.dumps({k:out[k] for k in ('terminal','passed','coverage','eligible_fold_count','rows_scored','competition_count','baseline_metrics','challenger_metrics','baseline_binary_draw_log_loss','challenger_binary_draw_log_loss','baseline_draw_auc','challenger_draw_auc','fold_draw_ll_win_rate','competition_draw_ll_win_rate','gate')},indent=2))
if __name__=='__main__': main()
