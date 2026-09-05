from __future__ import annotations

import argparse, json, pathlib, sys

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BP=ROOT/'research'/'historical_prior_shot_bodypart_candidate_v1'
sys.path.insert(0,str(BP))
import run_shot_bodypart_candidate as bp

FEATURE='central_xg_fit_abs'
bp.FEATURE=FEATURE
common=bp.common
bmod=bp.bmod


def main()->int:
    ap=argparse.ArgumentParser()
    for arg in ('contract','source_receipts','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+arg.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text())
    assert c['status']=='FROZEN_BEFORE_CANDIDATE_EVALUATION'
    assert c['feature']['family']=='central_xg_share' and c['feature']['key']==FEATURE and c['feature']['source_receipt_is_authoritative'] is True
    assert c['method']['fixed_ridge']==0.01
    for k in ('no_candidate_family_search','no_hyperparameter_grid','no_threshold_search','no_posthoc_calibration','no_probability_clipping','no_retune_after_result'):
        assert c['method'][k] is True

    fmap,source_audit=bp.load_feature_receipts(a.source_receipts)
    if source_audit['receipt_n']!=5478 or source_audit['max_season']!=2022: raise RuntimeError(f'source receipt cohort drift: {source_audit}')
    if source_audit['coverage']<float(c['gates']['minimum_feature_coverage']): raise RuntimeError(f"source feature coverage low {source_audit['coverage']}")

    frozen=common.build_frozen_baseline(a,'central_xg_share_candidate')
    dev=[r for r in frozen['rows'] if r['season'] in (2020,2021,2022) and r['fixture_id'] in frozen['bmap']]
    if len(dev)!=5478: raise RuntimeError(f'development_n drift {len(dev)}')
    if set(r['fixture_id'] for r in dev)!=set(fmap): raise RuntimeError('source/baseline identity mismatch')

    wanted={r['fixture_id'] for r in dev}
    snaps,snaprec=bmod.make_snapshots(a.db,wanted,float(c['frozen_bases']['stage6_b_half_life']))
    bprob={}; bmats={}
    for r in dev:
        fid=r['fixture_id']; p,_=bmod.predict(frozen['bmap'][fid],snaps.get(fid),float(c['frozen_bases']['stage6_b_coefficient']))
        bprob[fid]=list(map(float,p)); bmats[fid]=common.region_rescale(frozen['bmats'][fid],frozen['bmap'][fid],bprob[fid])

    rows=[]
    for r in dev:
        fid=r['fixture_id']; hg,ag=int(r['home_goals']),int(r['away_goals']); y=0 if hg>ag else 1 if hg==ag else 2
        rec=dict(r); rec[FEATURE]=fmap[fid].get(FEATURE); rec['y']=y; rec['b_prob']=bprob[fid]; rows.append(rec)
    coverage=sum(r.get(FEATURE) is not None for r in rows)/len(rows)
    if abs(coverage-float(source_audit['coverage']))>1e-15: raise RuntimeError('feature coverage identity drift')

    ridge=float(c['method']['fixed_ridge']); cand={}; folds=[]; max_ratio=0.0; max_delta=0.0
    for season in (2021,2022):
        train=[r for r in rows if r['season']<season]; test=[r for r in rows if r['season']==season]
        params,draw_pred=bp.fit_fold(train,test,ridge)
        for r,pd in zip(test,draw_pred):
            fid=r['fixture_id']; cp=bp.candidate_1x2(r['b_prob'],pd); cand[fid]=cp
            max_ratio=max(max_ratio,bp.ratio_error(r['b_prob'],cp)); max_delta=max(max_delta,max(abs(cp[i]-float(r['b_prob'][i])) for i in range(3)))
        base={r['fixture_id']:r['b_prob'] for r in test}; cc={r['fixture_id']:cand[r['fixture_id']] for r in test}
        bm=common.metrics(test,base); cm=common.metrics(test,cc); yy=[int(r['y']==1) for r in test]
        bll=bp.binary_ll(yy,[float(r['b_prob'][1]) for r in test]); cll=bp.binary_ll(yy,[float(cand[r['fixture_id']][1]) for r in test])
        folds.append({'season':season,'train_n':len(train),'test_n':len(test),'fit':params,'baseline':bm,'candidate':cm,'draw_logloss':{'baseline':bll,'candidate':cll},'deltas':{'hits':cm['hits']-bm['hits'],'top1_pp':(cm['top1']-bm['top1'])*100.0,'logloss':cm['logloss']-bm['logloss'],'brier':cm['brier']-bm['brier'],'rps':cm['rps']-bm['rps'],'draw_logloss':cll-bll}})

    test_rows=[r for r in rows if r['season'] in (2021,2022)]; base_test={r['fixture_id']:r['b_prob'] for r in test_rows}
    if len(cand)!=len(test_rows): raise RuntimeError('candidate map incomplete')
    bm=common.metrics(test_rows,base_test); cm=common.metrics(test_rows,cand)
    cmats={}; matrix_err=0.0
    for r in test_rows:
        fid=r['fixture_id']; m=common.region_rescale(bmats[fid],bprob[fid],cand[fid]); cmats[fid]=m; got=common.integrate_matrix(m); matrix_err=max(matrix_err,max(abs(got[i]-cand[fid][i]) for i in range(3)))
    be=bp.exact_score_ll(test_rows,bmats); ce=bp.exact_score_ll(test_rows,cmats); bt=bp.total_goals_ll(test_rows,bmats); ct=bp.total_goals_ll(test_rows,cmats)
    d={'hits':cm['hits']-bm['hits'],'top1_pp':(cm['top1']-bm['top1'])*100.0,'logloss':cm['logloss']-bm['logloss'],'brier':cm['brier']-bm['brier'],'rps':cm['rps']-bm['rps'],'exact_score_logloss':ce-be,'total_goals_logloss':ct-bt}
    g=c['gates']; checks={
      'draw_logloss_each_fold_strict_improvement':all(f['deltas']['draw_logloss']<0 for f in folds),
      'pooled_1x2_logloss_nonworse':d['logloss']<=float(g['pooled_1x2_logloss_delta_max'])+1e-15,
      'pooled_1x2_brier_nonworse':d['brier']<=float(g['pooled_1x2_brier_delta_max'])+1e-15,
      'pooled_1x2_rps_nonworse':d['rps']<=float(g['pooled_1x2_rps_delta_max'])+1e-15,
      'pooled_top1_nondecrease':d['hits']>=int(g['pooled_top1_delta_hits_min']),
      'each_fold_top1_nondecrease':all(f['deltas']['hits']>=int(g['each_fold_top1_delta_hits_min']) for f in folds),
      'pooled_exact_score_logloss_nonworse':d['exact_score_logloss']<=float(g['pooled_exact_score_logloss_delta_max'])+1e-15,
      'pooled_total_goals_logloss_nonworse':d['total_goals_logloss']<=float(g['pooled_total_goals_logloss_delta_max'])+1e-15,
      'max_outcome_probability_delta':max_delta<=float(g['max_outcome_probability_abs_delta'])+1e-15,
      'conditional_home_away_ratio_preserved':max_ratio<=float(g['conditional_home_away_ratio_max_abs_error'])+1e-15,
      'matrix_1x2_identity':matrix_err<=float(g['matrix_1x2_max_abs_error'])+1e-15,
    }
    passed=all(checks.values()); final=None
    if passed:
        mu,sd=bp.standardization(rows); X=bp.design(rows,mu,sd); yy=[int(r['y']==1) for r in rows]; offs=[bp.strict_logit(float(r['b_prob'][1])) for r in rows]; beta=bp.et.fit_offset(X,yy,offs,ridge)
        final={'schema_version':'football3-central-xg-share-frozen-params-v1','status':'FROZEN_AFTER_DEVELOPMENT_PASS_BEFORE_ANY_CONFIRMATION','feature_family':'central_xg_share','feature_key':FEATURE,'standardization_mean':mu,'standardization_sd':sd,'beta_intercept':float(beta[0]),'beta_slope':float(beta[1]),'fixed_ridge':ridge,'training_seasons':[2020,2021,2022],'training_n':len(rows),'formal_weight':0,'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False}
        common.write_json(a.out/'FROZEN_CENTRAL_XG_SHARE_PARAMS.json',final)
    out={'schema_version':'football3-prior-central-xg-share-candidate-result-v1','status':c['terminal']['pass'] if passed else c['terminal']['fail'],'scientific_pass':passed,'research_only':True,'formal_weight':0,'development_n':len(rows),'rolling_oos_n':len(test_rows),'source_max_season_loaded':source_audit['max_season'],'coverage':coverage,'source_receipt_audit':source_audit,'stage6_b_active_n':snaprec['active'],'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,'feature_family':'central_xg_share','feature_key':FEATURE,'fold_results':folds,'pooled':{'baseline':bm,'candidate':cm,'deltas':d,'exact_score_logloss':{'baseline':be,'candidate':ce},'total_goals_logloss':{'baseline':bt,'candidate':ct}},'max_outcome_probability_abs_delta':max_delta,'conditional_home_away_ratio_max_abs_error':max_ratio,'matrix_1x2_max_abs_error':matrix_err,'checks':checks,'frozen_params_written':final is not None,'next_step':'FIND_AND_PREREGISTER_GENUINELY_UNCONSUMED_HISTORICAL_CONFIRMATION_COHORT' if passed else 'CLOSE_CENTRAL_XG_SHARE_NO_RETUNE'}
    common.write_json(a.out/'central_xg_share_candidate_result.json',out); print(json.dumps(out,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
