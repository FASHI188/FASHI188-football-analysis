from __future__ import annotations
import argparse, collections, datetime as dt, hashlib, json, math, pathlib, sqlite3, sys

HERE=pathlib.Path(__file__).resolve().parent
FD=HERE.parents[1]
sys.path.insert(0,str(FD/'research'/'stage6_pre_b_deep_ppda'))
sys.path.insert(0,str(FD/'research'/'historical_direction_screen_v1'))
sys.path.insert(0,str(FD/'research'/'historical_baseline_residual_v1'))
sys.path.insert(0,str(FD/'research'/'historical_score_shape_candidate_v1'))
import common
import run_stage6_pre_b as bmod
import screen_pit_directions as ds
import run_baseline_residual_diagnostics as resid
import run_score_shape_candidate as cand

EPS=1e-15

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def build_baseline_2023(a):
    v311=common.loadmod('c23_v311',a.v311); v31=common.loadmod('c23_v31',a.v31); usr=common.loadmod('c23_usr',a.usr1); v2=common.loadmod('c23_v2',a.v2); xg=common.loadmod('c23_xg',a.xg)
    fixtures,labels,meta=xg.load_universe(a.db,a.xg_identity)
    assert len(fixtures)==18084
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in common.LEAGUES)
    raw=[dict(r) for r in con.execute(f'''select fid,h_id,a_id,date,league_id,season,h_goals,a_goals,team_h,team_a,h_xg,a_xg,h_shot,a_shot,h_shotOnTarget,a_shotOnTarget,h_deep,a_deep,h_ppda,a_ppda,league from general_game_stats where league in ({qs}) and season between 2014 and 2023 order by date,fid''',common.LEAGUES)]; con.close(); assert len(raw)==18084
    source={f"understat:{int(r['fid'])}":r for r in raw}
    v1=xg.import_v1(a.v1); v1params=xg.load_v1_params(a.v1_result); cache=xg.precompute_v1_cache(v1,v1params,fixtures,labels); p=v2.fixed_xg_params(xg)
    rep=xg.replay(v1,v1params,fixtures,labels,meta,p,{2018,2019,2020,2021,2022,2023},None,write_predictions=True,base_cache=cache)
    rows=[]; parents={}
    for pr in rep['predictions']:
        fid=pr['fixture_id']; lab=labels[fid]; src=source[fid]
        rows.append({'fixture_id':fid,'league':pr['league'],'season':int(pr['season']),'kickoff':pr['kickoff'],'home_team_id':pr['home_team_id'],'away_team_id':pr['away_team_id'],'home_team':str(src['team_h']),'away_team':str(src['team_a']),'home_goals':lab.home_goals,'away_goals':lab.away_goals,'h_deep':src['h_deep'],'a_deep':src['a_deep'],'h_ppda':src['h_ppda'],'a_ppda':src['a_ppda'],'v1_parent':pr['v1'],'xg_parent':pr['challenger']})
        parents[fid]={'v1_mu_home':float(pr['v1']['mu_home']),'v1_mu_away':float(pr['v1']['mu_away']),'xg_mu_home':float(pr['challenger']['mu_home']),'xg_mu_away':float(pr['challenger']['mu_away']),'xg_fallback':bool(pr['challenger']['dynamic']['fallback_exact_v1'])}
    weighted=v2.with_weight(xg,rows,.75)
    for r in weighted:r.update(parents[r['fixture_id']])
    counts={s:sum(1 for r in weighted if r['season']==s) for s in range(2018,2024)}; assert counts=={2018:1826,2019:1725,2020:1826,2021:1826,2022:1826,2023:1752},counts
    proc,procrec=v31.process_features_ext(usr,a.db,weighted,2023)
    bmap,bmats,baserec=common.frozen_baselines_upto(v311,v31,usr,weighted,proc,2023)
    return {'v311':v311,'v31':v31,'usr':usr,'v2':v2,'xg':xg,'rows':weighted,'bmap':bmap,'bmats':bmats,'process_receipt':procrec,'baseline_receipt':baserec,'xg_coverage':rep['coverage'],'counts':counts}

def make_b_snapshots_2023(db,wanted,half):
    alpha=1-math.exp(math.log(0.5)/half); con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in common.LEAGUES)
    games=[dict(r) for r in con.execute(f'''select fid,h_id,a_id,date,league,season,h_deep,a_deep,h_ppda,a_ppda from general_game_stats where league in ({qs}) and season between 2014 and 2023 order by date,fid''',common.LEAGUES)]; con.close(); assert len(games)==18084
    bytime=collections.OrderedDict()
    for g in games: bytime.setdefault(str(g['date']),[]).append(g)
    states=collections.defaultdict(dict); snaps={}; stats={'games':len(games),'target_snapshots':0,'active':0,'fallback':0}
    for datestr,batch in bytime.items():
        for g in sorted(batch,key=lambda x:int(x['fid'])):
            fid=f"understat:{int(g['fid'])}"
            if fid not in wanted: continue
            d=states[str(g['league'])]; hs=d.get(str(int(g['h_id']))); aws=d.get(str(int(g['a_id']))); stats['target_snapshots']+=1
            if hs is None or aws is None or hs.n<1 or aws.n<1: snaps[fid]={'active':False,'reason':'missing_team_state'}; stats['fallback']+=1; continue
            vals=[s for s in d.values() if s.n>=1]; md=sum(s.deep for s in vals)/len(vals); mp=sum(s.press for s in vals)/len(vals); sd=math.sqrt(sum((s.deep-md)**2 for s in vals)/len(vals)); sp=math.sqrt(sum((s.press-mp)**2 for s in vals)/len(vals))
            if sd<=1e-9 or sp<=1e-9: snaps[fid]={'active':False,'reason':'zero_league_sd'}; stats['fallback']+=1; continue
            hp=.5*((hs.deep-md)/sd)+.5*((hs.press-mp)/sp); ap=.5*((aws.deep-md)/sd)+.5*((aws.press-mp)/sp); snaps[fid]={'active':True,'home_process':hp,'away_process':ap,'edge':hp-ap}; stats['active']+=1
        for g in sorted(batch,key=lambda x:int(x['fid'])):
            try: hd=math.log1p(max(0.0,float(g['h_deep']))); ad=math.log1p(max(0.0,float(g['a_deep']))); hp=-math.log(max(1e-12,float(g['h_ppda']))); ap=-math.log(max(1e-12,float(g['a_ppda'])))
            except Exception: continue
            d=states[str(g['league'])]; d.setdefault(str(int(g['h_id'])),bmod.State()).update(hd,hp,alpha); d.setdefault(str(int(g['a_id'])),bmod.State()).update(ad,ap,alpha)
    return snaps,stats

def importance_2023(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in common.LEAGUES)
    games=[dict(r) for r in con.execute(f'''select fid,h_id,a_id,date,league,season,h_goals,a_goals from general_game_stats where league in ({qs}) and season=2023 order by date,fid''',common.LEAGUES)]; con.close(); assert len(games)==1752
    bytime=collections.OrderedDict()
    for g in games: bytime.setdefault(str(g['date']),[]).append(g)
    tabs=collections.defaultdict(dict); out={}
    for datestr,batch in bytime.items():
        snaps={lg:ds.table_snapshot(tabs[lg]) for lg in {g['league'] for g in batch}}
        for g in sorted(batch,key=lambda x:int(x['fid'])):
            ts=snaps[g['league']]; hi=ds.importance(ts.get(int(g['h_id'])),g['league']); ai=ds.importance(ts.get(int(g['a_id'])),g['league']); out[f"understat:{int(g['fid'])}"]={'importance_diff':None if hi is None or ai is None else hi-ai,'importance_mean':None if hi is None or ai is None else .5*(hi+ai)}
        for g in sorted(batch,key=lambda x:int(x['fid'])):
            tab=tabs[g['league']]
            for tid in (int(g['h_id']),int(g['a_id'])): tab.setdefault(tid,{'pts':0,'gf':0,'ga':0,'played':0})
            h=tab[int(g['h_id'])]; aw=tab[int(g['a_id'])]; hg=int(g['h_goals']); ag=int(g['a_goals']); h['played']+=1; aw['played']+=1; h['gf']+=hg; h['ga']+=ag; aw['gf']+=ag; aw['ga']+=hg
            if hg>ag:h['pts']+=3
            elif hg<ag:aw['pts']+=3
            else:h['pts']+=1;aw['pts']+=1
    return out

def paired(rows,base,candm):
    dx=[]; dtg=[]
    for r in rows:
        fid=r['fixture_id']; hg=int(r['hg']); ag=int(r['ag']); bm=base[fid]; cm=candm[fid]
        dx.append(-math.log(max(EPS,cand.exact_score_prob(cm,hg,ag)))+math.log(max(EPS,cand.exact_score_prob(bm,hg,ag))))
        dtg.append(-math.log(max(EPS,cand.total_prob(cm,hg+ag)))+math.log(max(EPS,cand.total_prob(bm,hg+ag))))
    def s(v):
        n=len(v); m=sum(v)/n; sd=math.sqrt(sum((x-m)**2 for x in v)/(n-1)); se=sd/math.sqrt(n); return {'n':n,'mean':m,'sd':sd,'se':se,'ci95':[m-1.96*se,m+1.96*se]}
    return {'exact_score_loss_delta':s(dx),'total_goals_loss_delta':s(dtg)}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','frozen_params','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'): ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text()); fp=json.loads(a.frozen_params.read_text())
    assert c['status']=='FROZEN_BEFORE_ONE_SHOT_2023_LABEL_REVEAL'; assert sha256(a.frozen_params)==c['candidate']['frozen_params_sha256']; assert fp['candidate']=='importance_low2'; assert fp['fitted_on_consumed_history_seasons']==[2020,2021,2022]
    b=build_baseline_2023(a); dev=[r for r in b['rows'] if r['season']==2023 and r['fixture_id'] in b['bmap']]; assert len(dev)==1752
    feats=importance_2023(a.db); wanted={r['fixture_id'] for r in dev}; snaps,brec=make_b_snapshots_2023(a.db,wanted,float(c['base']['stage6_b_half_life']) if 'base' in c else 16.0)
    base_mats={}; cand_mats={}; rows=[]; bprobs={}; cprobs={}; cap=float(fp['max_abs_log_tilt']); params=fp['params']
    for src in dev:
        fid=src['fixture_id']; p,on=bmod.predict(b['bmap'][fid],snaps.get(fid),0.10); bm=common.region_rescale(b['bmats'][fid],b['bmap'][fid],p); rr={'fixture_id':fid,'league':src['league'],'season':2023,'hg':int(src['home_goals']),'ag':int(src['away_goals']),**feats[fid]}; cm=cand.apply_candidate(rr,bm,'importance_low2',params,cap); base_mats[fid]=bm; cand_mats[fid]=cm; rows.append(rr); bprobs[fid]=common.integrate_matrix(bm); cprobs[fid]=common.integrate_matrix(cm)
    base_metric=cand.metric(rows,base_mats); cm=cand.metric(rows,cand_mats); deltas={'exact_score_logloss':cm['exact_score_logloss']-base_metric['exact_score_logloss'],'total_goals_logloss':cm['total_goals_logloss']-base_metric['total_goals_logloss']}
    one_err=cand.max_1x2_error(base_mats,cand_mats); cell=cand.max_cell_delta(base_mats,cand_mats); pair=paired(rows,base_mats,cand_mats)
    leagues={}; nondeg=0; worst=-1e9
    for lg in sorted(set(r['league'] for r in rows)):
        rr=[r for r in rows if r['league']==lg]; bb={r['fixture_id']:base_mats[r['fixture_id']] for r in rr}; cc={r['fixture_id']:cand_mats[r['fixture_id']] for r in rr}; mb=cand.metric(rr,bb); mc=cand.metric(rr,cc); d=mc['exact_score_logloss']-mb['exact_score_logloss']; nondeg+=int(d<=1e-15); worst=max(worst,d); leagues[lg]={'n':len(rr),'baseline_b':mb,'candidate':mc,'deltas':{'exact_score_logloss':d,'total_goals_logloss':mc['total_goals_logloss']-mb['total_goals_logloss']}}
    g=c['gates']; checks={'n':len(rows)==int(g['confirmation_n']),'pooled_exact':deltas['exact_score_logloss']<=float(g['pooled_exact_score_logloss_delta_max'])+1e-15,'pooled_total':deltas['total_goals_logloss']<=float(g['pooled_total_goals_logloss_delta_max'])+1e-15,'league_nondegrade':nondeg>=int(g['league_exact_score_nondegrade_min']),'league_cap':worst<=float(g['league_exact_score_degradation_cap'])+1e-15,'one_x_two':one_err<=float(g['max_1x2_abs_error'])+1e-15,'cell_delta':cell<=float(g['max_score_cell_abs_delta'])+1e-15}
    status=c['terminal']['pass'] if all(checks.values()) else c['terminal']['reject']
    out={'schema_version':'football3-score-shape-2023-confirmation-result-v1','status':status,'research_only':True,'promotion_allowed':False,'confirmation_n':len(rows),'candidate':'importance_low2','candidate_params_sha256':sha256(a.frozen_params),'historical_confirmation_2023_labels_opened':True,'prospective_1335_data_touched':False,'stage6_b_active_n':brec['active'],'stage6_b_fallback_n':brec['fallback'],'baseline_b':base_metric,'candidate_metrics':cm,'deltas':deltas,'paired':pair,'leagues':leagues,'league_exact_score_nondegrade_n':nondeg,'max_league_exact_score_degradation':worst,'max_1x2_abs_error':one_err,'max_score_cell_abs_delta':cell,'checks':checks,'all_pass':all(checks.values()),'formal_v2_unchanged':True,'frozen_v3_1_1_unchanged':True,'stage6_b_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'new_future_queue_created':False,'next_step':'RESEARCH_CANDIDATE_CONFIRMED_AWAIT_EXPLICIT_NEXT_STAGE' if all(checks.values()) else 'REJECT_CANDIDATE_NO_RESCUE_ON_2023'}
    common.write_json(a.out/'score_shape_2023_confirmation_result.json',out); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
