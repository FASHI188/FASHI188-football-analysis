from __future__ import annotations
import argparse, json, math, pathlib, sys

HERE=pathlib.Path(__file__).resolve().parent
FD=HERE.parents[1]
sys.path.insert(0,str(FD/'research'/'stage6_pre_b_deep_ppda'))
sys.path.insert(0,str(FD/'research'/'historical_direction_screen_v1'))
sys.path.insert(0,str(FD/'research'/'historical_baseline_residual_v1'))
import common
import run_stage6_pre_b as bmod
import screen_pit_directions as ds
import run_baseline_residual_diagnostics as resid

EPS=1e-15

def ridx(h,a): return 0 if h>a else 1 if h==a else 2

def integrate(m):
    p=[0.0,0.0,0.0]
    for h,row in enumerate(m):
        for a,v in enumerate(row): p[ridx(h,a)]+=float(v)
    return p

def clone(m): return [list(map(float,row)) for row in m]

def apply_region_tilt(m,predicate,eta):
    out=clone(m); fac=math.exp(float(eta)); before=integrate(out)
    for h,row in enumerate(out):
        for a in range(len(row)):
            if predicate(h,a): row[a]*=fac
    after=integrate(out)
    for k in range(3):
        if before[k]<=0 or after[k]<=0: continue
        scale=before[k]/after[k]
        for h,row in enumerate(out):
            for a in range(len(row)):
                if ridx(h,a)==k: row[a]*=scale
    z=sum(sum(row) for row in out)
    if not math.isfinite(z) or z<=0: raise RuntimeError('invalid tilted matrix')
    out=[[v/z for v in row] for row in out]
    # final region repair after global normalization, to machine precision
    after=integrate(out)
    for k in range(3):
        if after[k]<=0: continue
        scale=before[k]/after[k]
        for h,row in enumerate(out):
            for a in range(len(row)):
                if ridx(h,a)==k: row[a]*=scale
    return out

def exact_score_prob(m,h,a):
    return float(m[h][a]) if 0<=h<len(m) and 0<=a<len(m[h]) else EPS

def total_prob(m,t):
    return sum(float(v) for h,row in enumerate(m) for a,v in enumerate(row) if h+a==t)

def metric(rows,mats):
    ex=tg=0.0
    for r in rows:
        m=mats[r['fixture_id']]; hg=int(r['hg']); ag=int(r['ag'])
        ex-=math.log(max(EPS,exact_score_prob(m,hg,ag)))
        tg-=math.log(max(EPS,total_prob(m,hg+ag)))
    n=len(rows)
    return {'n':n,'exact_score_logloss':ex/n,'total_goals_logloss':tg/n}

def qtile(vals,q): return ds.qtile(vals,q)

def fit_scaler(rows,keys):
    means={}; sds={}
    for k in keys:
        vals=[float(r[k]) for r in rows if r.get(k) is not None and math.isfinite(float(r[k]))]
        means[k]=sum(vals)/len(vals); sds[k]=math.sqrt(sum((x-means[k])**2 for x in vals)/len(vals)) or 1.0
    return means,sds

def design(rows,keys,means,sds):
    X=[]
    for r in rows:
        x=[1.0]
        for k in keys:
            v=r.get(k); x.append(0.0 if v is None else (float(v)-means[k])/sds[k])
        X.append(x)
    return X

def fit_params(train,contract):
    fit=contract['fit_rules']; ridge=float(fit['fixed_ridge']); keys=list(fit['importance_features'])
    bal=[float(r['balance_abs']) for r in train]
    q20=qtile(bal,float(fit['balance_lower_quantile'])); q80=qtile(bal,float(fit['balance_upper_quantile']))
    means,sds=fit_scaler(train,keys); X=design(train,keys,means,sds)
    y=[int(r['hg']+r['ag']<=2) for r in train]; off=[resid.logit(r['b_low']) for r in train]
    beta= resid.fit_offset(X,y,off,ridge)
    br=[r for r in train if r['balance_abs']<=q20]
    sr=[r for r in train if r['balance_abs']>=q80]
    alpha11=resid.fit_offset([[1.0] for _ in br],[int(r['hg']==1 and r['ag']==1) for r in br],[resid.logit(r['b_p11']) for r in br],ridge)[0]
    alphalow=resid.fit_offset([[1.0] for _ in sr],[int(r['hg']+r['ag']<=2) for r in sr],[resid.logit(r['b_low']) for r in sr],ridge)[0]
    return {'balance_q20':q20,'balance_q80':q80,'importance_keys':keys,'importance_means':means,'importance_sds':sds,'importance_beta':beta,'balanced_11_log_tilt':alpha11,'strong_gap_low2_log_tilt':alphalow,'train_n':len(train),'balanced_train_n':len(br),'strong_gap_train_n':len(sr)}

def clip(x,c): return max(-c,min(c,float(x)))

def importance_eta(r,p,cap):
    z=float(p['importance_beta'][0])
    for j,k in enumerate(p['importance_keys'],1):
        v=r.get(k); zz=0.0 if v is None else (float(v)-float(p['importance_means'][k]))/float(p['importance_sds'][k])
        z+=float(p['importance_beta'][j])*zz
    return clip(z,cap)

def apply_candidate(r,m,name,p,cap):
    out=clone(m)
    if name in ('importance_low2','combined'):
        out=apply_region_tilt(out,lambda h,a:h+a<=2,importance_eta(r,p,cap))
    if name in ('regime_shape','combined'):
        if float(r['balance_abs'])<=float(p['balance_q20']):
            out=apply_region_tilt(out,lambda h,a:h==1 and a==1,clip(p['balanced_11_log_tilt'],cap))
        elif float(r['balance_abs'])>=float(p['balance_q80']):
            out=apply_region_tilt(out,lambda h,a:h+a<=2,clip(p['strong_gap_low2_log_tilt'],cap))
    return out

def max_cell_delta(base,cand):
    ans=0.0
    for fid,m in base.items():
        c=cand[fid]
        for h,row in enumerate(m):
            for a,v in enumerate(row): ans=max(ans,abs(float(c[h][a])-float(v)))
    return ans

def max_1x2_error(base,cand):
    ans=0.0
    for fid,m in base.items():
        a=integrate(m); b=integrate(cand[fid]); ans=max(ans,max(abs(a[i]-b[i]) for i in range(3)))
    return ans

def shape_summary(rows,mats,q20,q80):
    groups={
      'balanced':[r for r in rows if r['balance_abs']<=q20],
      'strong_gap':[r for r in rows if r['balance_abs']>=q80],
      'overall':list(rows),
    }
    out={}
    for name,rr in groups.items():
        n=len(rr); out[name]={'n':n}
        if not n: continue
        out[name].update({
          'actual_11':sum(r['hg']==1 and r['ag']==1 for r in rr)/n,
          'pred_11':sum(exact_score_prob(mats[r['fixture_id']],1,1) for r in rr)/n,
          'actual_low2':sum(r['hg']+r['ag']<=2 for r in rr)/n,
          'pred_low2':sum(resid.matrix_low(mats[r['fixture_id']]) for r in rr)/n,
        })
    return out

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    assert c['status']=='FROZEN_BEFORE_CONSUMED_HISTORY_CANDIDATE_SCORING'
    b=common.build_frozen_baseline(a,'shape'); dev0=[r for r in b['rows'] if r['season'] in (2020,2021,2022) and r['fixture_id'] in b['bmap']]; assert len(dev0)==5478
    f=resid.feature_map(a.db); wanted={r['fixture_id'] for r in dev0}; snaps,srec=bmod.make_snapshots(a.db,wanted,float(c['base']['stage6_b_half_life']))
    bmats={}; rows=[]
    for src in dev0:
        fid=src['fixture_id']; p,on=bmod.predict(b['bmap'][fid],snaps.get(fid),float(c['base']['stage6_b_coefficient'])); m=common.region_rescale(b['bmats'][fid],b['bmap'][fid],p); bmats[fid]=m
        bp=list(map(float,b['bmap'][fid])); side=bp[0]/max(EPS,bp[0]+bp[2]); feat=dict(f[fid]); feat.update({'fixture_id':fid,'season':int(src['season']),'hg':int(src['home_goals']),'ag':int(src['away_goals']),'balance_abs':abs(resid.logit(side)),'b_low':resid.matrix_low(m),'b_p11':exact_score_prob(m,1,1)}); rows.append(feat)
    names=list(c['selection_rule']['candidate_names']); cap=float(c['fit_rules']['max_abs_log_tilt']); gates=c['gates']; folds=[]
    aggregate={n:{'exact':[],'total':[],'max_1x2':0.0,'max_cell':0.0} for n in names}
    for season in c['data_roles']['rolling_tests']:
        train=[r for r in rows if r['season']<int(season)]; test=[r for r in rows if r['season']==int(season)]; params=fit_params(train,c)
        base={r['fixture_id']:bmats[r['fixture_id']] for r in test}; base_m=metric(test,base); frec={'season':int(season),'train_n':len(train),'test_n':len(test),'params':params,'baseline_b':base_m,'candidates':{}}
        for name in names:
            cand={r['fixture_id']:apply_candidate(r,bmats[r['fixture_id']],name,params,cap) for r in test}; mm=metric(test,cand); dx=mm['exact_score_logloss']-base_m['exact_score_logloss']; dtg=mm['total_goals_logloss']-base_m['total_goals_logloss']; e=max_1x2_error(base,cand); cd=max_cell_delta(base,cand)
            frec['candidates'][name]={'metrics':mm,'deltas':{'exact_score_logloss':dx,'total_goals_logloss':dtg},'max_1x2_abs_error':e,'max_score_cell_abs_delta':cd,'shape':shape_summary(test,cand,params['balance_q20'],params['balance_q80'])}
            aggregate[name]['exact'].append(dx); aggregate[name]['total'].append(dtg); aggregate[name]['max_1x2']=max(aggregate[name]['max_1x2'],e); aggregate[name]['max_cell']=max(aggregate[name]['max_cell'],cd)
        folds.append(frec)
    summary={}; eligible=[]
    for pos,name in enumerate(names):
        ag=aggregate[name]; mean_ex=sum(ag['exact'])/len(ag['exact']); mean_t=sum(ag['total'])/len(ag['total'])
        checks={'exact_each_fold':all(v<=float(gates['exact_score_fold_delta_max'])+1e-15 for v in ag['exact']),'mean_total_goals':mean_t<=float(gates['mean_total_goals_delta_max'])+1e-15,'one_x_two':ag['max_1x2']<=float(gates['max_1x2_abs_error'])+1e-15,'cell_delta':ag['max_cell']<=float(gates['max_abs_probability_change_per_score_cell'])+1e-15}
        summary[name]={'mean_exact_score_logloss_delta':mean_ex,'mean_total_goals_logloss_delta':mean_t,'fold_exact_score_deltas':ag['exact'],'fold_total_goals_deltas':ag['total'],'max_1x2_abs_error':ag['max_1x2'],'max_score_cell_abs_delta':ag['max_cell'],'checks':checks,'eligible':all(checks.values())}
        if all(checks.values()): eligible.append((mean_ex,mean_t,pos,name))
    eligible.sort(); selected=eligible[0][3] if eligible else None
    final_params=fit_params(rows,c) if selected else None
    frozen=None
    if selected:
        frozen={'schema_version':'football3-historical-score-shape-frozen-params-v1','candidate':selected,'fitted_on_consumed_history_seasons':[2020,2021,2022],'fit_n':len(rows),'max_abs_log_tilt':cap,'params':final_params,'one_x_two_policy':'EXACT_STAGE6_B_REGION_TOTALS_PRESERVED','historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False}
        common.write_json(a.out/'FROZEN_SCORE_SHAPE_PARAMS.json',frozen)
    status=c['terminal']['pass'] if selected else c['terminal']['none']
    out={'schema_version':'football3-historical-score-shape-candidate-result-v1','status':status,'research_only':True,'development_n':len(rows),'source_max_season_loaded':2022,'stage6_b_active_n':srec['active'],'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,'folds':folds,'summary':summary,'selected_candidate':selected,'frozen_params_written':bool(frozen),'next_step':'ONE_SHOT_2023_HISTORICAL_CONFIRMATION' if selected else 'RETURN_TO_RESEARCH_NO_2023_LABEL_REVEAL'}
    assert out['stage6_b_active_n']==5463; assert out['historical_confirmation_2023_labels_opened'] is False; assert out['prospective_1335_data_touched'] is False
    common.write_json(a.out/'score_shape_candidate_result.json',out); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
