from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import pathlib
import statistics
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import v1_1_dynamic_base as v11

DEVELOPMENT_N = 1826
POSTVIEW_N = 5256
EXPECTED_DEVELOPMENT_SHA = 'b8713ceed6d57ead7b2aadbb24d3154bf5cb5df0d45eef0e762b5c395d6d4fab'
EXPECTED_V1_ENGINE_SHA = 'cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318'
EXPECTED_V1_ARTIFACT_HEAD = '22f639304d2e32fc952dbec2255153ee45dcd41a'
EXPECTED_V1_PARAMS = {
    'half_life_days':260.0,'competition_half_life_days':720.0,'prior_matches':10.0,
    'competition_prior_matches':28.0,'global_team_prior_matches':14.0,'cross_season_shrink':0.66,
    'global_team_weight':0.35,'strength_exponent':0.82,'min_rate':0.12,'max_rate':5.5,
}


def sha_file(path: pathlib.Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def dt(x: str | datetime) -> datetime:
    d=x if isinstance(x,datetime) else datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if d.tzinfo is None or d.utcoffset() is None: raise RuntimeError('naive datetime')
    return d.astimezone(timezone.utc)


def read_jsonl(path: pathlib.Path) -> list[dict[str,Any]]:
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def write_jsonl(path: pathlib.Path, rows: list[dict[str,Any]]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')


def grouped(rows: list[dict[str,Any]]) -> list[list[dict[str,Any]]]:
    out=[]; cur=[]; key=None
    for r in sorted(rows,key=lambda x:(dt(x['cutoff']),str(x['competition_id']),str(x['fixture_id']))):
        k=dt(r['cutoff'])
        if key is None or k==key: cur.append(r); key=k
        else: out.append(cur); cur=[r]; key=k
    if cur: out.append(cur)
    return out


def import_v1(path: pathlib.Path):
    if sha_file(path)!=EXPECTED_V1_ENGINE_SHA: raise RuntimeError('frozen V1 engine SHA mismatch')
    spec=importlib.util.spec_from_file_location('football3_frozen_v1_v11',path)
    if spec is None or spec.loader is None: raise RuntimeError('cannot import frozen V1')
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m); return m


def frozen_v1_params(v1_result: pathlib.Path) -> dict[str,Any]:
    d=json.loads(v1_result.read_text())
    got=d.get('selected_parameters')
    if got!=EXPECTED_V1_PARAMS: raise RuntimeError(f'frozen V1 parameter identity mismatch: {got}')
    return dict(got)


def fx(v1, r: dict[str,Any]):
    return v1.Fixture(str(r['fixture_id']),str(r['competition_id']),str(r['season']),dt(r['cutoff']),str(r['home_team_id']),str(r['away_team_id']))


def result_class(hg:int,ag:int)->str: return 'home' if hg>ag else 'draw' if hg==ag else 'away'


class Metrics:
    def __init__(self):
        self.n=0; self.ll=self.brier=self.rps=0.0; self.correct=0; self.pred_h=self.pred_a=0.0; self.actual_h=self.actual_a=0
    def add(self,p:dict[str,float],mh:float,ma:float,hg:int,ag:int):
        y=result_class(hg,ag); self.n+=1; self.ll+=-math.log(max(1e-15,p[y]))
        self.brier+=sum((p[k]-(1.0 if y==k else 0.0))**2 for k in ('home','draw','away'))
        self.rps+=((p['home']-(1.0 if y=='home' else 0.0))**2+(p['home']+p['draw']-(1.0 if y in {'home','draw'} else 0.0))**2)/2.0
        self.correct+=int(max(('home','draw','away'),key=lambda k:p[k])==y); self.pred_h+=mh; self.pred_a+=ma; self.actual_h+=hg; self.actual_a+=ag
    def finish(self):
        if not self.n: return {'n':0,'logloss':None,'brier':None,'rps':None,'top1':None,'predicted_mean_goals':{'home':None,'away':None},'actual_mean_goals':{'home':None,'away':None}}
        n=self.n; return {'n':n,'logloss':self.ll/n,'brier':self.brier/n,'rps':self.rps/n,'top1':self.correct/n,
                          'predicted_mean_goals':{'home':self.pred_h/n,'away':self.pred_a/n},'actual_mean_goals':{'home':self.actual_h/n,'away':self.actual_a/n}}


def matrix_means(matrix:list[dict[str,Any]])->tuple[float,float]:
    return (sum(int(c['home_goals'])*float(c['probability']) for c in matrix),sum(int(c['away_goals'])*float(c['probability']) for c in matrix))


def candidate_grid()->list[v11.DynamicParameters]:
    return [v11.DynamicParameters(float(h),float(p),float(b),float(s)) for h,p,b,s in itertools.product((90,180,360),(4,8,16),(0.05,0.10,0.15),(0.40,0.70))]


def split_spec(batches:list[list[dict[str,Any]]])->dict[str,Any]:
    n=len(batches); warm=math.ceil(0.20*n); select=math.ceil(0.30*n); outer_start=warm+select
    if outer_start>=n-8: raise RuntimeError('insufficient development batches')
    outer_n=n-outer_start; base=outer_n//8; extra=outer_n%8; ranges=[]; pos=outer_start
    for k in range(8):
        size=base+(1 if k<extra else 0); ranges.append((pos,pos+size)); pos+=size
    if pos!=n: raise RuntimeError('fold split mismatch')
    return {'batch_n':n,'warmup_batches':warm,'selection_batches':select,'outer_batches':outer_n,'outer_start':outer_start,
            'fold_ranges':[{'fold':k,'start_batch':a,'end_batch_exclusive':b,'n_batches':b-a} for k,(a,b) in enumerate(ranges)]}


def fold_for_batch(i:int,spec:dict[str,Any])->int|None:
    for x in spec['fold_ranges']:
        if x['start_batch']<=i<x['end_batch_exclusive']: return int(x['fold'])
    return None


def precompute_frozen_v1(v1,v1params,batches):
    state=v1.EngineState(params=v1.Parameters(**v1params)); pending=[]; cache={}
    for batch in batches:
        now=dt(batch[0]['cutoff'])
        while pending and pending[0][0]<=now:
            _,old=pending.pop(0); state.apply_batch([fx(v1,r) for r in old],{str(r['fixture_id']):(int(r['home_goals']),int(r['away_goals'])) for r in old})
        for r in batch:
            pred=state.predict(fx(v1,r)); mh,ma=matrix_means(pred['score_matrix']); z=dict(pred); z['matrix_mean_home']=mh; z['matrix_mean_away']=ma; cache[str(r['fixture_id'])]=z
        pending.append((max(dt(r['result_available_at']) for r in batch),batch)); pending.sort(key=lambda x:(x[0],x[1][0]['cutoff'],x[1][0]['fixture_id']))
    return cache


def _release_v11_cached(v1,state,pending,now):
    while pending and pending[0][0]<=now:
        _,batch=pending.pop(0); labels={str(r['fixture_id']):(int(r['home_goals']),int(r['away_goals']),dt(r['result_available_at'])) for r in batch}
        state.apply_batch([fx(v1,r) for r in batch],labels,as_of=now,update_frozen_v1_state=False)


def replay_candidate(v1,v1params,dev,batches,params,spec,base_cache,mode:str):
    state=v11.DynamicEngineState(v1,v1params,params); pending=[]; overall=Metrics(); folds={k:Metrics() for k in range(8)}; groups=defaultdict(Metrics); fallbacks=active=0
    select_start=spec['warmup_batches']; select_end=spec['outer_start']
    for i,batch in enumerate(batches):
        now=dt(batch[0]['cutoff']); _release_v11_cached(v1,state,pending,now)
        bases=[base_cache[str(r['fixture_id'])] for r in batch]; preds=state.predict_batch_from_frozen_v1([fx(v1,r) for r in batch],bases,include_matrix=False)
        score_this=(mode=='selection' and select_start<=i<select_end) or (mode=='outer' and i>=select_end) or mode=='all'
        if score_this:
            fold=fold_for_batch(i,spec)
            for r,p in zip(batch,preds):
                hg,ag=int(r['home_goals']),int(r['away_goals']); probs={'home':float(p['p_home']),'draw':float(p['p_draw']),'away':float(p['p_away'])}; mh=float(p['matrix_mean_home']); ma=float(p['matrix_mean_away'])
                overall.add(probs,mh,ma,hg,ag); groups[f"{r['competition_id']}|{r['season']}"] .add(probs,mh,ma,hg,ag)
                if fold is not None: folds[fold].add(probs,mh,ma,hg,ag)
                fallbacks+=int(p['dynamic']['fallback_exact_v1']); active+=int(not p['dynamic']['fallback_exact_v1'])
        pending.append((max(dt(r['result_available_at']) for r in batch),batch)); pending.sort(key=lambda x:(x[0],x[1][0]['cutoff'],x[1][0]['fixture_id']))
    return overall.finish(),{k:m.finish() for k,m in folds.items()},{g:m.finish() for g,m in sorted(groups.items())},{'active_n':active,'fallback_n':fallbacks,'active_rate':active/max(1,active+fallbacks)}


def replay_v1_from_cache(dev,batches,spec,base_cache,mode='outer'):
    overall=Metrics(); folds={k:Metrics() for k in range(8)}; groups=defaultdict(Metrics); select_start=spec['warmup_batches']; select_end=spec['outer_start']
    for i,batch in enumerate(batches):
        score_this=(mode=='selection' and select_start<=i<select_end) or (mode=='outer' and i>=select_end) or mode=='all'
        if not score_this: continue
        fold=fold_for_batch(i,spec)
        for r in batch:
            p=base_cache[str(r['fixture_id'])]; hg,ag=int(r['home_goals']),int(r['away_goals']); probs={'home':float(p['p_home']),'draw':float(p['p_draw']),'away':float(p['p_away'])}; mh=float(p['matrix_mean_home']); ma=float(p['matrix_mean_away'])
            overall.add(probs,mh,ma,hg,ag); groups[f"{r['competition_id']}|{r['season']}"] .add(probs,mh,ma,hg,ag)
            if fold is not None: folds[fold].add(probs,mh,ma,hg,ag)
    return overall.finish(),{k:m.finish() for k,m in folds.items()},{g:m.finish() for g,m in sorted(groups.items())}

def development(v1_engine,v1_result,development_path,out_dir):
    if sha_file(development_path)!=EXPECTED_DEVELOPMENT_SHA: raise RuntimeError('development SHA mismatch')
    dev=read_jsonl(development_path)
    if len(dev)!=DEVELOPMENT_N or {str(r['season']) for r in dev}!={'2022-23'}: raise RuntimeError('development universe mismatch')
    if len({str(r['fixture_id']) for r in dev})!=len(dev): raise RuntimeError('development duplicate fixture')
    for r in dev:
        if dt(r['result_available_at'])<=dt(r['cutoff']): raise RuntimeError('invalid result availability')
    v1=import_v1(v1_engine); v1params=frozen_v1_params(v1_result); batches=grouped(dev); spec=split_spec(batches); base_cache=precompute_frozen_v1(v1,v1params,batches)
    board=[]
    for p in candidate_grid():
        metric,_,_,cov=replay_candidate(v1,v1params,dev,batches,p,spec,base_cache,'selection')
        board.append({'parameters':p.__dict__,'parameter_tuple':[p.dynamic_half_life_days,p.dynamic_prior_matches,p.dynamic_beta,p.dynamic_cross_season_shrink],
                      'selection':metric,'coverage':cov})
    board.sort(key=lambda x:(x['selection']['logloss'],x['selection']['brier'],x['selection']['rps'],tuple(x['parameter_tuple'])))
    best=board[0]; selected=v11.DynamicParameters(**best['parameters'])
    v11m,v11fold,v11groups,cov=replay_candidate(v1,v1params,dev,batches,selected,spec,base_cache,'outer')
    v1m,v1fold,v1groups=replay_v1_from_cache(dev,batches,spec,base_cache,'outer')
    fold_rows=[]; nondeg=0; gains=[]
    for k in range(8):
        gain=float(v1fold[k]['logloss'])-float(v11fold[k]['logloss']); gains.append(gain); ok=v11fold[k]['logloss']<=v1fold[k]['logloss']+1e-12; nondeg+=int(ok)
        fold_rows.append({'fold':k,'v1':v1fold[k],'v1_1':v11fold[k],'logloss_gain_v1_minus_v1_1':gain,'nondegrade':ok})
    group_rows={}; group_gate=True; direction_gate=True
    for g in sorted(v1groups):
        a,b=v1groups[g],v11groups[g]; deg=float(b['logloss'])-float(a['logloss']); actual_gap=float(b['actual_mean_goals']['home'])-float(b['actual_mean_goals']['away']); pred_gap=float(b['predicted_mean_goals']['home'])-float(b['predicted_mean_goals']['away'])
        sized=b['n']>=100; log_ok=(not sized) or deg<=0.020+1e-12; dir_ok=(actual_gap<=0.0) or pred_gap>0.0; group_gate &= log_ok; direction_gate &= dir_ok
        group_rows[g]={'v1':a,'v1_1':b,'logloss_degradation_v1_1_minus_v1':deg,'sufficient_n':sized,'logloss_group_gate':log_ok,'actual_home_minus_away':actual_gap,'predicted_home_minus_away':pred_gap,'home_direction_gate':dir_ok}
    overall_actual_gap=float(v11m['actual_mean_goals']['home'])-float(v11m['actual_mean_goals']['away']); overall_pred_gap=float(v11m['predicted_mean_goals']['home'])-float(v11m['predicted_mean_goals']['away'])
    overall_direction=(overall_actual_gap<=0.0) or overall_pred_gap>0.0
    gates={
        'fold_nondegrade_n':nondeg,'fold_nondegrade_at_least_6_of_8':nondeg>=6,
        'pooled_logloss_gain_v1_minus_v1_1':float(v1m['logloss'])-float(v11m['logloss']),
        'pooled_logloss_gain_at_least_0_001':float(v1m['logloss'])-float(v11m['logloss'])>=0.001,
        'brier_not_worse':v11m['brier']<=v1m['brier']+1e-12,'rps_not_worse':v11m['rps']<=v1m['rps']+1e-12,
        'top1_delta':float(v11m['top1'])-float(v1m['top1']),'top1_delta_at_least_minus_0_0015':float(v11m['top1'])-float(v1m['top1'])>=-0.0015,
        'worst_group_logloss_gate':group_gate,'competition_season_home_direction_gate':direction_gate,'overall_home_direction_gate':overall_direction,
        'deterministic_invariants_required':True,'pit_identity_batch_future_guards_required':True,'exact_v1_fallback_required':True,
    }
    all_pass=all(v for k,v in gates.items() if isinstance(v,bool))
    status='V1_1_DYNAMIC_BASE_DEVELOPMENT_PASS_POSTVIEW_ALLOWED' if all_pass else 'V1_1_DYNAMIC_BASE_REJECTED'
    result={'schema_version':'football3-v1-1-dynamic-base-development-v1','status':status,'development_only':True,'postview_labels_read':False,
            'development_n':len(dev),'development_sha256':sha_file(development_path),'v1_engine_sha256':sha_file(v1_engine),'v1_parameters':v1params,
            'candidate_grid_size':len(board),'candidate_grid_sha256':v11.canonical_sha256([x['parameters'] for x in board]),'split_spec':spec,
            'selected_parameters':selected.__dict__,'selection_metric':best['selection'],'selection_coverage':best['coverage'],'candidate_board_sha256':v11.canonical_sha256(board),'candidate_top10':board[:10],
            'outer_v1':v1m,'outer_v1_1':v11m,'outer_coverage':cov,'folds':fold_rows,'groups':group_rows,'fold_logloss_gains':gains,'median_fold_gain':statistics.median(gains),
            'gates':gates,'formal_weight':0,'formal_enablement':False,'new_blind_pool_started':False}
    out_dir.mkdir(parents=True,exist_ok=True); write_json(out_dir/'development_result.json',result); write_json(out_dir/'development_gate.json',{'status':status,'all_development_gates_pass':all_pass,'postview_allowed':all_pass})
    return result


def initialize_after_development(v1,v1params,dp,dev):
    s=v11.DynamicEngineState(v1,v1params,dp); pending=[]
    def release(now):
        while pending and pending[0][0]<=now:
            _,batch=pending.pop(0); labs={str(r['fixture_id']):(int(r['home_goals']),int(r['away_goals']),dt(r['result_available_at'])) for r in batch}; s.apply_batch([fx(v1,r) for r in batch],labs,as_of=now)
    for batch in grouped(dev):
        now=dt(batch[0]['cutoff']); release(now); s.predict_batch([fx(v1,r) for r in batch],include_matrix=False); pending.append((max(dt(r['result_available_at']) for r in batch),batch)); pending.sort(key=lambda x:(x[0],x[1][0]['cutoff'],x[1][0]['fixture_id']))
    if pending:
        flush=max(x[0] for x in pending)+timedelta(seconds=1); release(flush)
    if pending: raise RuntimeError('development pending not flushed')
    return s


def predict_postview(v1_engine,v1_result,development_path,features_path,label_vault,development_result,out_dir):
    devres=json.loads(development_result.read_text())
    if devres.get('status')!='V1_1_DYNAMIC_BASE_DEVELOPMENT_PASS_POSTVIEW_ALLOWED' or devres.get('postview_labels_read') is not False: raise RuntimeError('development gate does not authorize postview')
    dev=read_jsonl(development_path); features=read_jsonl(features_path)
    if len(features)!=POSTVIEW_N or len({r['fixture_id'] for r in features})!=POSTVIEW_N: raise RuntimeError('postview feature identity mismatch')
    v1=import_v1(v1_engine); v1params=frozen_v1_params(v1_result); dp=v11.DynamicParameters(**devres['selected_parameters']); s=initialize_after_development(v1,v1params,dp,dev)
    batches=grouped(features); pending=deque(); frozen=[]; predicted=set(); label_reads=[]
    lf=label_vault.open(encoding='utf-8'); next_label_line=None
    def read_released_batch(old_batch,now):
        nonlocal next_label_line
        labs={}
        for r in old_batch:
            line=lf.readline()
            if not line: raise RuntimeError('label vault ended early')
            lab=json.loads(line); fid=str(r['fixture_id'])
            if str(lab['fixture_id'])!=fid: raise RuntimeError('streaming label order/identity mismatch')
            if fid not in predicted: raise RuntimeError('label read before own prediction freeze')
            av=dt(lab['result_available_at'])
            if av>now or dt(r['cutoff'])+timedelta(hours=3)!=av: raise RuntimeError('label release mismatch')
            labs[fid]=(int(lab['home_goals']),int(lab['away_goals']),av); label_reads.append({'fixture_id':fid,'read_as_of':now.isoformat(),'result_available_at':av.isoformat(),'prediction_frozen_before_read':True})
        s.apply_batch([fx(v1,r) for r in old_batch],labs,as_of=now)
    for batch in batches:
        now=dt(batch[0]['cutoff'])
        while pending and pending[0][0]<=now:
            _,old=pending.popleft(); read_released_batch(old,now)
        preds=s.predict_batch([fx(v1,r) for r in batch])
        for r,p in zip(batch,preds):
            fid=str(r['fixture_id']); predicted.add(fid); frozen.append({'fixture_id':fid,'competition_id':r['competition_id'],'season':r['season'],'cutoff':r['cutoff'],'home_team_id':r['home_team_id'],'away_team_id':r['away_team_id'],
                'mu_home':p['mu_home'],'mu_away':p['mu_away'],'matrix_mean_home':p['matrix_mean_home'],'matrix_mean_away':p['matrix_mean_away'],'p_home':p['p_home'],'p_draw':p['p_draw'],'p_away':p['p_away'],
                'score_matrix':p['score_matrix'],'dynamic':p['dynamic'],'prediction_sha256':p['prediction_sha256'],'formal_weight':0})
        pending.append((max(dt(r['cutoff'])+timedelta(hours=3) for r in batch),batch))
    if len(frozen)!=POSTVIEW_N: raise RuntimeError('postview prediction n mismatch')
    pred=out_dir/'postview_predictions.jsonl'; write_jsonl(pred,frozen)
    pre={'schema_version':'football3-v1-1-dynamic-base-postview-freeze-v1','status':'V1_1_POSTVIEW_PREDICTIONS_FROZEN','n':POSTVIEW_N,'prediction_sha256':sha_file(pred),
         'development_result_sha256':sha_file(development_result),'prior_label_reads_n':len(label_reads),'target_label_reads_before_own_prediction':0,'same_or_future_label_reads':0,
         'remaining_unread_label_lines':POSTVIEW_N-len(label_reads),'post_view_diagnostic':True,'strict_prospective':False,'formal_weight':0,'formal_enablement':False}
    write_json(out_dir/'pre_score_manifest.json',pre); write_json(out_dir/'pit_label_access_audit.json',{'rows':label_reads,'rows_sha256':v11.canonical_sha256(label_reads),'target_label_reads_before_own_prediction':0})
    lf.close(); return pre


def score_postview(predictions,label_vault,pre_score,out_dir):
    pre=json.loads(pre_score.read_text()); rows=read_jsonl(predictions); labels=read_jsonl(label_vault)
    if pre.get('status')!='V1_1_POSTVIEW_PREDICTIONS_FROZEN' or pre.get('prediction_sha256')!=sha_file(predictions): raise RuntimeError('prediction freeze SHA mismatch')
    if len(rows)!=POSTVIEW_N or len(labels)!=POSTVIEW_N: raise RuntimeError('postview n mismatch')
    lm={str(x['fixture_id']):x for x in labels}; m=Metrics(); groups=defaultdict(Metrics); active=fallback=0
    for r in rows:
        lab=lm.get(str(r['fixture_id']))
        if lab is None: raise RuntimeError('scorer identity mismatch')
        hg,ag=int(lab['home_goals']),int(lab['away_goals']); p={'home':float(r['p_home']),'draw':float(r['p_draw']),'away':float(r['p_away'])}; m.add(p,float(r['matrix_mean_home']),float(r['matrix_mean_away']),hg,ag); groups[f"{r['competition_id']}|{r['season']}"] .add(p,float(r['matrix_mean_home']),float(r['matrix_mean_away']),hg,ag)
        fallback+=int(r['dynamic']['fallback_exact_v1']); active+=int(not r['dynamic']['fallback_exact_v1'])
    result={'schema_version':'football3-v1-1-dynamic-base-postview-score-v1','status':'POST_VIEW_DIAGNOSTIC_COMPLETE','n':POSTVIEW_N,'v1_1':m.finish(),'groups':{g:x.finish() for g,x in sorted(groups.items())},
            'active_n':active,'fallback_n':fallback,'prediction_sha256':sha_file(predictions),'label_vault_sha256':sha_file(label_vault),'post_view_diagnostic':True,'not_blind':True,'not_promotion_eligible':True,'formal_weight':0,'formal_enablement':False}
    write_json(out_dir/'postview_score.json',result); return result


def main():
    ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest='cmd',required=True)
    a=sp.add_parser('development'); a.add_argument('--v1-engine',type=pathlib.Path,required=True); a.add_argument('--v1-result',type=pathlib.Path,required=True); a.add_argument('--development',type=pathlib.Path,required=True); a.add_argument('--out-dir',type=pathlib.Path,required=True)
    a=sp.add_parser('predict-postview'); a.add_argument('--v1-engine',type=pathlib.Path,required=True); a.add_argument('--v1-result',type=pathlib.Path,required=True); a.add_argument('--development',type=pathlib.Path,required=True); a.add_argument('--features',type=pathlib.Path,required=True); a.add_argument('--label-vault',type=pathlib.Path,required=True); a.add_argument('--development-result',type=pathlib.Path,required=True); a.add_argument('--out-dir',type=pathlib.Path,required=True)
    a=sp.add_parser('score-postview'); a.add_argument('--predictions',type=pathlib.Path,required=True); a.add_argument('--label-vault',type=pathlib.Path,required=True); a.add_argument('--pre-score',type=pathlib.Path,required=True); a.add_argument('--out-dir',type=pathlib.Path,required=True)
    x=ap.parse_args()
    if x.cmd=='development': r=development(x.v1_engine,x.v1_result,x.development,x.out_dir)
    elif x.cmd=='predict-postview': r=predict_postview(x.v1_engine,x.v1_result,x.development,x.features,x.label_vault,x.development_result,x.out_dir)
    else: r=score_postview(x.predictions,x.label_vault,x.pre_score,x.out_dir)
    print(json.dumps({'status':r.get('status'),'keys':sorted(r)},sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
