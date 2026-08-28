#!/usr/bin/env python3
from __future__ import annotations

import json, shutil, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
OUT=HERE/'results'
ROOT=HERE.parents[2]
F5_DIR=ROOT/'football-data'/'experiments'/'r43f5_probability_weighted_technical_mixture'
if str(F5_DIR) not in sys.path: sys.path.insert(0,str(F5_DIR))
import run_r43f5 as f5

SOURCE_F5_BRANCH='football3/r43f5-probability-weighted-technical-mixture'
SOURCE_F5_RUNNER_BLOB='83a2d20ec0e38b6fca61da7751b7a18846414b13'
BLOCKS=[
 {'id':'r43f3_block','skip_newest_n':40000,'selection':'valid_sorted_[-60000:-40000]','previous_use':'R43F3/R43E2-related development'},
 {'id':'r43f4_block','skip_newest_n':80000,'selection':'valid_sorted_[-100000:-80000]','previous_use':'R43F4 and unrelated older research'},
]

def load(p): return json.loads(p.read_text(encoding='utf-8'))

def run_block(spec):
    work=HERE/spec['id']; out=work/'results'
    shutil.rmtree(work,ignore_errors=True); out.mkdir(parents=True,exist_ok=True)
    old_here,old_out,old_skip,old_sel=f5.HERE,f5.OUT,f5.SKIP_NEWEST_N,f5.SELECTION
    try:
        f5.HERE=work; f5.OUT=out; f5.SKIP_NEWEST_N=int(spec['skip_newest_n']); f5.SELECTION=str(spec['selection'])
        d=f5.run()
    finally:
        f5.HERE, f5.OUT, f5.SKIP_NEWEST_N, f5.SELECTION=old_here,old_out,old_skip,old_sel
    if d['status']!='COMPLETE' or d['formal_weight']!=0: raise RuntimeError(f"bad block status {spec['id']}")
    if d['governance']['parameter_search'] or d['governance']['feature_search']: raise RuntimeError('scientific contract drift')
    if float(d['governance']['tech_alpha'])!=0.5 or float(d['governance']['mixture_temperature_or_power'])!=1.0: raise RuntimeError('frozen mixture drift')
    return d

def weighted_delta(items,path):
    n=sum(int(x['outcome_split']['test_n']) for x in items)
    out={}
    for k in ('accuracy_pp','logloss','brier','rps','draw_binary_logloss','draw_binary_brier'):
        out[k]=sum(int(x['outcome_split']['test_n'])*float(x['test'][path][k]) for x in items)/n
    out['hits']=sum(int(x['test'][path]['hits']) for x in items)
    out['test_n']=n
    return out

def run():
    OUT.mkdir(parents=True,exist_ok=True)
    results=[]
    for spec in BLOCKS:
        d=run_block(spec)
        results.append(d)
    agg_primary=weighted_delta(results,'context_weighted_minus_base_weighted')
    agg_no=weighted_delta(results,'context_weighted_minus_no_tech')
    block_gates=[bool(x['gate']['passed']) for x in results]
    aggregate_primary_pass=bool(agg_primary['hits']>=0 and agg_primary['logloss']<0 and agg_primary['brier']<0 and agg_primary['rps']<0)
    compatibility=bool(all(block_gates) and aggregate_primary_pass)
    blocks=[]
    for spec,d in zip(BLOCKS,results):
        blocks.append({
          'id':spec['id'],'selection':spec['selection'],'skip_newest_n':spec['skip_newest_n'],'previous_use':spec['previous_use'],
          'source_dates':[d['source']['first_date'],d['source']['last_date']],
          'outcome_split':d['outcome_split'],
          'technical_coverage_test':d['technical_coverage_test'],
          'validation':{k:d['validation'][k] for k in ('no','base_weighted_half','context_weighted_half','context_hard_half')},
          'test':d['test'],'original_f5_gate':d['gate']
        })
    out={
      'schema_version':'football3-r43m1-weighted-mixture-multiblock-replication-v1','status':'COMPLETE','formal_weight':0,
      'classification':'FROZEN_R43F5_WEIGHTED_EXPECTED_XI_TECHNICAL_MIXTURE_COMPATIBILITY_ON_TWO_PREVIOUSLY_CONSUMED_DISJOINT_20K_BLOCKS',
      'question':'Does the exact frozen R43F5 probability-weighted expected-XI technical mixture repeat on two disjoint historical blocks without tuning?',
      'governance':{
        'source_f5_branch':SOURCE_F5_BRANCH,'source_f5_runner_blob':SOURCE_F5_RUNNER_BLOB,'blocks_disjoint_from_r43f5_design_block':True,'blocks_previously_consumed_elsewhere':True,
        'formal_weight':0,'parameter_search':False,'feature_search':False,'threshold_search':False,'mixture_temperature_or_power':1.0,'tech_alpha':0.5,'gate_changed_within_blocks':False,
        'manual_outcome_override':False,'manual_draw_override':False,'draw_threshold':False,'draw_class_weight':False,'result_driven_retuning':False,'formal_promotion_allowed':False
      },
      'blocks':blocks,'aggregate':{'context_weighted_minus_base_weighted':agg_primary,'context_weighted_minus_no_tech':agg_no,'block_original_gates_passed':block_gates,'aggregate_primary_passed':aggregate_primary_pass},
      'compatibility_gate':{'passed':compatibility,'require_all_original_block_gates':True,'require_aggregate_primary_hits_nonnegative':True,'require_aggregate_primary_proper_scores_all_improve':True,
        'action':'WEIGHTED_MIXTURE_REPEATS_JUSTIFY_EXPANDED_TRUE_FORWARD_BATCH' if compatibility else 'WEIGHTED_MIXTURE_NOT_STABLE_ENOUGH_DO_NOT_EXPAND_FROM_HISTORICAL_EVIDENCE'},
      'limitations':['Both replication blocks were previously consumed by other research and therefore have formal_weight=0.','This test cannot promote the architecture; it only decides whether expanded true-forward acquisition is worth pursuing.','No parameter, feature, alpha, draw rule or within-block gate is changed from R43F5.']
    }
    p=OUT/'summary_r43m1_weighted_mixture_multiblock_replication.json'; p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');
    for spec in BLOCKS: shutil.rmtree(HERE/spec['id'],ignore_errors=True)
    print(json.dumps(out,ensure_ascii=False,indent=2)); return out

def verify():
    x=load(OUT/'summary_r43m1_weighted_mixture_multiblock_replication.json'); assert x['status']=='COMPLETE' and x['formal_weight']==0 and len(x['blocks'])==2
    g=x['governance']; assert g['parameter_search'] is False and g['feature_search'] is False and g['threshold_search'] is False and g['tech_alpha']==0.5 and g['mixture_temperature_or_power']==1.0 and g['result_driven_retuning'] is False and g['formal_promotion_allowed'] is False
    assert len({b['selection'] for b in x['blocks']})==2
    print('R43M1 contract verified')
if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run': run()
    elif cmd=='verify': verify()
    else: raise SystemExit(cmd)
