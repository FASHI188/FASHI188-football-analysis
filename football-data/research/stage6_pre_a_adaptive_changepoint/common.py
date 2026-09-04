from __future__ import annotations
import hashlib, importlib.util, json, math, pathlib, sqlite3, sys
from collections import defaultdict

LEAGUES=('Bundesliga','EPL','La liga','Ligue 1','Serie A')
EPS=1e-15
TOL=1e-12

class Stage84Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    if spec is None or spec.loader is None: raise Stage84Error(f'cannot import {path}')
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2,allow_nan=False)+'\n')

def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def group(rows,keyfn):
    d=defaultdict(list)
    for r in rows:d[keyfn(r)].append(r)
    return d

def build_rows_upto(xg,v2,v1_engine,v1_result,db,xg_identity,max_season=2022):
    ident=json.loads(pathlib.Path(xg_identity).read_text())
    full_sha=sha_file(db)
    if full_sha!=ident['source']['database_sha256']: raise Stage84Error('source DB SHA mismatch')
    v1=xg.import_v1(v1_engine); v1params=xg.load_v1_params(v1_result)
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    qs=','.join('?' for _ in LEAGUES)
    sql=f"select fid,h_id,a_id,date,league_id,season,h_goals,a_goals,team_h,team_a,h_xg,a_xg,h_shot,a_shot,h_shotOnTarget,a_shotOnTarget,h_deep,a_deep,h_ppda,a_ppda,league from general_game_stats where league in ({qs}) and season between 2014 and ? order by date,fid"
    raw=[dict(r) for r in con.execute(sql,(*LEAGUES,int(max_season)))]; con.close()
    expected_total={2022:16332}.get(int(max_season))
    if expected_total is not None and len(raw)!=expected_total: raise Stage84Error(f'filtered universe count drift {len(raw)}')
    fixtures=[]; labels={}; meta={}; source={}
    for r in raw:
        fid=f"understat:{int(r['fid'])}"; ko=xg.dt_source(r['date']); season=str(int(r['season'])); comp=f"understat-league:{int(r['league_id'])}"
        f=xg.FixtureRow(fid,comp,season,ko,f"understat-team:{int(r['h_id'])}",f"understat-team:{int(r['a_id'])}",str(r['team_h']),str(r['team_a']))
        labels[fid]=xg.ReleasedLabel(int(r['h_goals']),int(r['a_goals']),float(r['h_xg']),float(r['a_xg']),ko+xg.timedelta(hours=3))
        fixtures.append(f); meta[fid]={'league':str(r['league']),'season':int(r['season']),'source_date':str(r['date']),'source_fid':int(r['fid'])}; source[fid]=r
    fold_map,fold_rows=xg.outer_fold_map(fixtures)
    cache=xg.precompute_v1_cache(v1,v1params,fixtures,labels)
    p=v2.fixed_xg_params(xg)
    score_seasons={s for s in range(2018,int(max_season)+1)}
    rep=xg.replay(v1,v1params,fixtures,labels,meta,p,score_seasons,fold_map,write_predictions=True,base_cache=cache)
    rows=[]; parents={}
    for pr in rep['predictions']:
        fid=pr['fixture_id']; lab=labels[fid]; src=source[fid]
        rows.append({'fixture_id':fid,'league':pr['league'],'season':int(pr['season']),'kickoff':pr['kickoff'],
                     'home_team_id':pr['home_team_id'],'away_team_id':pr['away_team_id'],
                     'home_team':str(src['team_h']),'away_team':str(src['team_a']),
                     'home_goals':lab.home_goals,'away_goals':lab.away_goals,
                     'h_deep':src['h_deep'],'a_deep':src['a_deep'],'h_ppda':src['h_ppda'],'a_ppda':src['a_ppda'],
                     'v1_parent':pr['v1'],'xg_parent':pr['challenger']})
        parents[fid]={'v1_mu_home':float(pr['v1']['mu_home']),'v1_mu_away':float(pr['v1']['mu_away']),
                      'xg_mu_home':float(pr['challenger']['mu_home']),'xg_mu_away':float(pr['challenger']['mu_away']),
                      'xg_fallback':bool(pr['challenger']['dynamic']['fallback_exact_v1'])}
    weighted=v2.with_weight(xg,rows,.75)
    for r in weighted:r.update(parents[r['fixture_id']])
    counts={s:sum(1 for r in weighted if r['season']==s) for s in range(2018,int(max_season)+1)}
    exp={2018:1826,2019:1725,2020:1826,2021:1826,2022:1826}
    if any(counts.get(s)!=exp[s] for s in counts): raise Stage84Error(f'count drift {counts}')
    return weighted,fold_map,fold_rows,{'counts':counts,'filtered_universe_n':len(fixtures),'max_source_season_loaded':int(max_season),'v1_cache_n':len(cache),'xg_coverage':rep['coverage']}

def frozen_baselines_upto(v311,v31,usr,rows,proc,max_season=2022):
    bys=group(rows,lambda r:r['season']); pmap={}; mats={}; receipts=[]
    r18=sorted(bys[2018],key=lambda r:(r['kickoff'],r['fixture_id'])); burn=1000; pos=burn
    while pos<len(r18):
        end=min(len(r18),pos+250)
        while end<len(r18) and r18[end]['kickoff']==r18[end-1]['kickoff']: end+=1
        train=r18[:pos]; test=r18[pos:end]; model,fitrec=usr.fit(train,proc)
        for r in test:pmap[r['fixture_id']]=v311.target_v31(v31,usr,model,r,proc)
        receipts.append({'season':2018,'train_n':len(train),'test_n':len(test),'fit_n':fitrec['fit_n']})
        pos=end
    for s in range(2019,int(max_season)+1):
        train=[r for r in rows if 2018<=r['season']<s]; test=bys[s]; model,fitrec=usr.fit(train,proc)
        for r in test:pmap[r['fixture_id']]=v311.target_v31(v31,usr,model,r,proc)
        receipts.append({'season':s,'train_n':len(train),'test_n':len(test),'fit_n':fitrec['fit_n']})
    for r in rows:
        if r['fixture_id'] in pmap:
            m=v311.project_regions(v311.base_matrix(r),pmap[r['fixture_id']])
            if m is None: raise Stage84Error('baseline matrix projection failed')
            mats[r['fixture_id']]=m
    return pmap,mats,receipts

def build_frozen_baseline(args, prefix='s84'):
    v311=loadmod(prefix+'_v311',args.v311); v31=loadmod(prefix+'_v31',args.v31); usr=loadmod(prefix+'_usr',args.usr1); v2=loadmod(prefix+'_v2',args.v2); xg=loadmod(prefix+'_xg',args.xg)
    rows,fold_map,fold_rows,rowrec=build_rows_upto(xg,v2,args.v1,args.v1_result,args.db,args.xg_identity,2022)
    proc,procrec=v31.process_features_ext(usr,args.db,rows,2022)
    bmap,bmats,baserec=frozen_baselines_upto(v311,v31,usr,rows,proc,2022)
    return {'v311':v311,'v31':v31,'usr':usr,'v2':v2,'xg':xg,'rows':rows,'fold_map':fold_map,'fold_rows':fold_rows,'row_receipt':rowrec,'process_receipt':procrec,'bmap':bmap,'bmats':bmats,'baseline_receipt':baserec}

def result_idx(r):
    hg=int(r['home_goals']); ag=int(r['away_goals']); return 0 if hg>ag else 1 if hg==ag else 2

def top1_idx(p):
    return max(range(3), key=lambda i:(float(p[i]),-i))

def metrics(rows,pmap):
    n=len(rows)
    if not n: raise Stage84Error('empty metric rows')
    ll=br=rps=0.0; hit=0
    for r in rows:
        p=[float(x) for x in pmap[r['fixture_id']]]; y=result_idx(r)
        if abs(sum(p)-1.0)>1e-9 or min(p)<-1e-12: raise Stage84Error('invalid probability')
        ll-=math.log(max(EPS,p[y])); br+=sum((p[i]-(1.0 if i==y else 0.0))**2 for i in range(3))
        c1=p[0]; c2=p[0]+p[1]; o1=1.0 if y<=0 else 0.0; o2=1.0 if y<=1 else 0.0
        rps+=0.5*((c1-o1)**2+(c2-o2)**2); hit+=int(top1_idx(p)==y)
    return {'n':n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1':hit/n,'hits':hit}

def region_rescale(base_matrix,base_p,cand_p):
    out=[]
    for h,row in enumerate(base_matrix):
        rr=[]
        for a,val in enumerate(row):
            k=0 if h>a else 1 if h==a else 2
            bp=max(EPS,float(base_p[k])); rr.append(float(val)*float(cand_p[k])/bp)
        out.append(rr)
    z=sum(sum(r) for r in out)
    if not math.isfinite(z) or z<=0: raise Stage84Error('matrix rescale invalid')
    out=[[x/z for x in r] for r in out]
    return out

def integrate_matrix(m):
    p=[0.0,0.0,0.0]
    for h,row in enumerate(m):
        for a,x in enumerate(row): p[0 if h>a else 1 if h==a else 2]+=float(x)
    return p

def max_abs_prob_delta(rows,base,cand):
    return max(abs(float(cand[r['fixture_id']][i])-float(base[r['fixture_id']][i])) for r in rows for i in range(3))

def score_axis(rows,bmap,bmats,cand,contract,active_mask=None):
    dev=[r for r in rows if r['season'] in (2020,2021,2022) and r['fixture_id'] in bmap]
    if len(dev)!=5478: raise Stage84Error(f'development count {len(dev)} != 5478')
    missing=[r['fixture_id'] for r in dev if r['fixture_id'] not in cand]
    if missing: raise Stage84Error(f'candidate missing {len(missing)}')
    base={r['fixture_id']:bmap[r['fixture_id']] for r in dev}
    bm=metrics(dev,base); cm=metrics(dev,cand)
    deltas={'top1_pp':(cm['top1']-bm['top1'])*100.0,'logloss':cm['logloss']-bm['logloss'],'brier':cm['brier']-bm['brier'],'rps':cm['rps']-bm['rps'],'hits':cm['hits']-bm['hits']}
    seasons=[]; ft=fl=0
    for s in (2020,2021,2022):
        rr=[r for r in dev if r['season']==s]; bb=metrics(rr,base); cc=metrics(rr,cand); dd={'top1_pp':(cc['top1']-bb['top1'])*100.0,'logloss':cc['logloss']-bb['logloss'],'brier':cc['brier']-bb['brier'],'rps':cc['rps']-bb['rps'],'hits':cc['hits']-bb['hits']}
        ft+=int(dd['top1_pp']>=-1e-12); fl+=int(dd['logloss']<=1e-12); seasons.append({'season':s,'baseline':bb,'candidate':cc,'deltas':dd})
    mats={}; maxerr=0.0
    for r in dev:
        fid=r['fixture_id']; m=region_rescale(bmats[fid],bmap[fid],cand[fid]); mats[fid]=m; got=integrate_matrix(m); maxerr=max(maxerr,max(abs(got[i]-cand[fid][i]) for i in range(3)))
    g=contract.get('one_x_two_gates') or contract.get('gates')
    checks={
      'top1':deltas['top1_pp']>=float(g['top1_delta_pp_min'])-1e-12,
      'logloss':deltas['logloss']<=float(g['logloss_delta_max'])+1e-15,
      'brier':deltas['brier']<=float(g['brier_delta_max'])+1e-15,
      'rps':deltas['rps']<=float(g['rps_delta_max'])+1e-15,
      'fold_top1':ft>=int(g['fold_top1_nonnegative_min']),
      'fold_logloss':fl>=int(g['fold_logloss_nonpositive_min']),
      'matrix':maxerr<=float(g['matrix_1x2_max_abs_error'])+1e-15,
      'max_delta':max_abs_prob_delta(dev,base,cand)<=float(g['max_outcome_probability_abs_delta'])+1e-15,
    }
    active_n=sum(1 for r in dev if active_mask is None or active_mask.get(r['fixture_id'],False)) if active_mask is not None else len(dev)
    return {'baseline':bm,'candidate':cm,'deltas':deltas,'seasons':seasons,'fold_top1_nonnegative_n':ft,'fold_logloss_nonpositive_n':fl,'matrix_to_1x2_max_abs_error':maxerr,'max_outcome_probability_abs_delta':max_abs_prob_delta(dev,base,cand),'active_n':active_n,'checks':checks,'all_pass':all(checks.values())},mats
