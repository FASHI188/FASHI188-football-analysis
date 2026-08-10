#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,importlib.util,json,math,subprocess,sys
from collections import defaultdict
from datetime import date
from pathlib import Path

R39W_PATH=Path('football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py')
R39W_EXPECTED_BLOB='4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae'
LABELS=('H','D','A')

def load_w():
    got=subprocess.check_output(['git','rev-parse',f'HEAD:{R39W_PATH.as_posix()}'],text=True).strip()
    if got!=R39W_EXPECTED_BLOB: raise RuntimeError(f'R39W_EVALUATOR_BLOB_DRIFT:{got}')
    spec=importlib.util.spec_from_file_location('r39w_for_r40j',R39W_PATH)
    if spec is None or spec.loader is None: raise RuntimeError('IMPORT_SPEC_FAIL')
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m

def key(raw):
    try:d=date.fromisoformat(str(raw.get('date','')).strip())
    except Exception:return None
    vals=[str(raw.get(k,'')).strip() for k in ('competition_id','season','home_team','away_team')]
    if not all(vals):return None
    return f'{vals[0]}|{vals[1]}|{d.isoformat()}|{vals[2]}|{vals[3]}'

def fnum(v):
    try:x=float(str(v).strip())
    except Exception:return None
    return x if math.isfinite(x) else None

def load_maturity(root):
    out={};seen=0;bad=0
    for p in sorted(root.glob('football-data/training_datasets/*/point_in_time.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            for raw in csv.DictReader(f):
                seen+=1;k=key(raw);h=fnum(raw.get('home_last5_matches'));a=fnum(raw.get('away_last5_matches'));c=fnum(raw.get('cold_start_flag'))
                if k is None or h is None or a is None or c is None:bad+=1;continue
                if not(0<=h<=5 and 0<=a<=5 and c in (0.0,1.0)):raise RuntimeError(f'MATURITY_VALUE_INVALID:{k}:{h}:{a}:{c}')
                if k in out:raise RuntimeError(f'DUPLICATE_MATURITY_KEY:{k}')
                out[k]=(h,a,c)
    return out,{'rows_seen':seen,'maturity_rows':len(out),'invalid_rows':bad}

def transform(train,target,u):
    z=[1.0]
    for j in range(len(target)):
        vals=[r[j] for r in train];med=u.quantile(vals,.5);s=u.quantile(vals,.75)-u.quantile(vals,.25)
        if not math.isfinite(s) or s<1e-9:s=1.0
        z.append((target[j]-med)/s)
    return z

def combine(pd,ph):return {'D':pd,'H':(1-pd)*ph,'A':(1-pd)*(1-ph)}
def dll(rs):
    s=0.0
    for r in rs:
        p=min(max(float(r['probabilities']['D']),1e-12),1-1e-12);y=1.0 if r['actual']=='D' else 0.0;s+=-(y*math.log(p)+(1-y)*math.log(1-p))
    return s/len(rs)
def auc(w,rs):return w.auc_binary([float(r['probabilities']['D']) for r in rs],[r['actual']=='D' for r in rs])
def sv(x):return float(x) if x is not None else -1.0

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--root',type=Path,default=Path('.'));ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    pre=json.loads(a.prereg.read_text());assert pre['schema_version']=='R40J-ROLLING-HISTORY-MATURITY-DRAW-1.0';f=pre['folding'];m=pre['models'];h=pre['hard_boundaries']
    assert f['minimum_train_rows']==200 and f['minimum_test_rows']==80 and f['same_season_training'] is False
    assert m['class_weighting'] is False and m['candidate_grid'] is False and m['manual_draw_boost'] is False and m['threshold_tuning'] is False and m['post_result_tuning'] is False and m['abstention'] is False
    assert h['external_network_requests']==0 and h['football_api_requests']==0 and h['new_data_collection'] is False and h['random_fixed100_consumed']==0
    w=load_w();u=w.load_r39u_module();rows=[r for r in u.load_rows(a.root) if u.finite_vec(r.x)];mmap,maudit=load_maturity(a.root);missing=[r.key for r in rows if r.key not in mmap]
    if missing:raise RuntimeError(f'MATURITY_COVERAGE_MISSING:{len(missing)}:{missing[:3]}')
    by=defaultdict(list)
    for r in rows:by[r.competition].append(r)
    for c in by:by[c].sort(key=lambda r:(r.dt,r.key))
    allb=[];allc=[];folds=[];skipped=[]
    for comp,cr in sorted(by.items()):
        ss=defaultdict(list)
        for r in cr:ss[r.season].append(r)
        for season,test in sorted(ss.items(),key=lambda kv:(min(r.dt for r in kv[1]),kv[0])):
            start=min(r.dt for r in test);tr=[r for r in cr if r.dt<start]
            if len(tr)<f['minimum_train_rows'] or len(test)<f['minimum_test_rows']:skipped.append({'competition':comp,'season':season,'train_n':len(tr),'test_n':len(test)});continue
            br=[r.x for r in tr];crw=[tuple(r.x)+tuple(mmap[r.key]) for r in tr];Xb,_=w.standardize(br,br[0],u.quantile);Xc,_=w.standardize(crw,crw[0],u.quantile);yd=[1 if r.label=='D' else 0 for r in tr];bb,itb=w.fit_logistic(Xb,yd,10.0);bc,itc=w.fit_logistic(Xc,yd,10.0)
            nd=[r for r in tr if r.label!='D'];sr=[w.side_features(r.x) for r in nd];Xs,_=w.standardize(sr,sr[0],u.quantile);ys=[1 if r.label=='H' else 0 for r in nd];bs,its=w.fit_logistic(Xs,ys,10.0)
            fb=[];fc=[]
            for r in test:
                xb=transform(br,r.x,u);xc=transform(crw,tuple(r.x)+tuple(mmap[r.key]),u);xs=transform(sr,w.side_features(r.x),u);pd0=w.predict_prob(bb,xb);pd1=w.predict_prob(bc,xc);ph=w.predict_prob(bs,xs);pb=combine(pd0,ph);pc=combine(pd1,ph)
                rb={'competition':comp,'season':season,'key':r.key,'actual':r.label,'prediction':u.choose(pb),'probabilities':pb};rc={'competition':comp,'season':season,'key':r.key,'actual':r.label,'prediction':u.choose(pc),'probabilities':pc,'maturity_features':mmap[r.key]};fb.append(rb);fc.append(rc);allb.append(rb);allc.append(rc)
            mb=u.metrics(fb);mc=u.metrics(fc);bl=dll(fb);cl=dll(fc);folds.append({'competition':comp,'season':season,'train_n':len(tr),'test_n':len(test),'baseline_metrics':mb,'challenger_metrics':mc,'baseline_draw_ll':bl,'challenger_draw_ll':cl,'draw_ll_win':cl<bl,'baseline_auc':auc(w,fb),'challenger_auc':auc(w,fc),'iters':{'baseline':itb,'challenger':itc,'side':its}})
    bm=u.metrics(allb);cm=u.metrics(allc);bl=dll(allb);cl=dll(allc);ba=auc(w,allb);ca=auc(w,allc);fwr=sum(x['draw_ll_win'] for x in folds)/len(folds)
    bcomp=defaultdict(list);ccomp=defaultdict(list);nf=defaultdict(int)
    for x in folds:nf[x['competition']]+=1
    for r in allb:bcomp[r['competition']].append(r)
    for r in allc:ccomp[r['competition']].append(r)
    comps=[]
    for comp in sorted(bcomp):
        x={'competition':comp,'folds':nf[comp],'n':len(bcomp[comp]),'baseline_draw_ll':dll(bcomp[comp]),'challenger_draw_ll':dll(ccomp[comp])};x['draw_ll_win']=x['challenger_draw_ll']<x['baseline_draw_ll'];comps.append(x)
    ec=[x for x in comps if x['folds']>=2];cwr=sum(x['draw_ll_win'] for x in ec)/len(ec)
    coverage=sum(r.key in mmap for r in rows)/len(rows)
    gate={'coverage_ge_0_995':coverage>=.995,'HDA_log_loss_better':cm['log_loss']<bm['log_loss'],'binary_draw_log_loss_better':cl<bl,'draw_auc_plus_0_003':ca>=ba+.003,'draw_f1_plus_0_02':sv(cm['draw_f1'])>=sv(bm['draw_f1'])+.02,'draw_recall_nonworse':sv(cm['draw_recall'])>=sv(bm['draw_recall']),'accuracy_noninferior':cm['accuracy']>=bm['accuracy']-.01,'fold_win_rate_ge_0_55':fwr>=.55,'competition_win_rate_ge_0_60':cwr>=.60}
    passed=all(gate.values());terminal='PASS_R40J_HISTORY_MATURITY_DRAW_INCREMENT' if passed else 'FAIL_R40J_HISTORY_MATURITY_DRAW_NO_INCREMENT'
    out={'schema_version':pre['schema_version'],'terminal':terminal,'passed':passed,'source_rows':len(rows),'maturity_audit':maudit,'coverage':coverage,'eligible_fold_count':len(folds),'rows_scored':len(allb),'competition_count':len(bcomp),'baseline_metrics':bm,'challenger_metrics':cm,'baseline_binary_draw_log_loss':bl,'challenger_binary_draw_log_loss':cl,'baseline_draw_auc':ba,'challenger_draw_auc':ca,'fold_draw_ll_win_rate':fwr,'competition_draw_ll_win_rate':cwr,'fold_metrics':folds,'competition_metrics':comps,'skipped_folds':skipped,'gate':gate,'hard_boundaries':h,'interpretation_boundary':'Retrospective rolling history-maturity challenger only; no fixed100 consumed; formal_weight remains 0.'}
    a.out.mkdir(parents=True,exist_ok=True);raw=json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+'\n';(a.out/'r40j_result.json').write_text(raw);(a.out/'r40j_result.sha256').write_text(hashlib.sha256(raw.encode()).hexdigest()+'\n');print(json.dumps({k:out[k] for k in ('terminal','passed','coverage','eligible_fold_count','rows_scored','competition_count','baseline_metrics','challenger_metrics','baseline_binary_draw_log_loss','challenger_binary_draw_log_loss','baseline_draw_auc','challenger_draw_auc','fold_draw_ll_win_rate','competition_draw_ll_win_rate','gate')},indent=2))
if __name__=='__main__':main()
