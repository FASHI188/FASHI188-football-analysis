from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, pathlib, sqlite3, sys, heapq
from collections import defaultdict
from datetime import timedelta

TOL=1e-12
MAXG=14
LEAGUES=('Bundesliga','EPL','La liga','Ligue 1','Serie A')
DEV_N=16332
DEV_SHA='71771c1366adb544fec18536a2f55cf632212e05e6a139ab91fd472784d30de0'
V311_HEAD='a90762a97515f3edd564e8ad204db0d0d4231494'
FORMAL_HEAD='e12f5d1193be5d81f60301cf34ab2140e11712a9'

class ResearchError(RuntimeError): pass

def loadmod(name,p):
    spec=importlib.util.spec_from_file_location(name,str(p)); m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

def sha_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def canon(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def write_json(p,o): pathlib.Path(p).write_text(json.dumps(o,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

def safe_universe(xg,db,max_season,identity=None):
    if max_season not in (2022,2023): raise ResearchError('max_season outside governed loader')
    if identity is not None:
        ident=json.loads(pathlib.Path(identity).read_text())
        if sha_file(db)!=ident['source']['database_sha256']: raise ResearchError('source DB SHA mismatch')
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row; qs=','.join('?'*len(LEAGUES))
    sql=f"select fid,h_id,a_id,date,league_id,season,h_goals,a_goals,team_h,team_a,h_xg,a_xg,h_shot,a_shot,h_shotOnTarget,a_shotOnTarget,league from general_game_stats where league in ({qs}) and season between 2014 and ? order by date,fid"
    raw=[dict(r) for r in con.execute(sql,(*LEAGUES,max_season))]; con.close()
    h=hashlib.sha256()
    for r in raw: h.update(canon(r)+b'\n')
    got=h.hexdigest()
    if max_season==2022:
        if len(raw)!=DEV_N or got!=DEV_SHA: raise ResearchError(f'development source drift n={len(raw)} sha={got}')
    else:
        if len(raw)!=18084 or got!=xg.UNIVERSE_SHA256: raise ResearchError('2023 universe identity drift')
    fixtures=[]; labels={}; meta={}
    for r in raw:
        fid=f"understat:{int(r['fid'])}"; ko=xg.dt_source(r['date']); season=str(int(r['season'])); comp=f"understat-league:{int(r['league_id'])}"
        f=xg.FixtureRow(fid,comp,season,ko,f"understat-team:{int(r['h_id'])}",f"understat-team:{int(r['a_id'])}",str(r['team_h']),str(r['team_a']))
        lab=xg.ReleasedLabel(int(r['h_goals']),int(r['a_goals']),float(r['h_xg']),float(r['a_xg']),ko+timedelta(hours=3))
        if fid in labels: raise ResearchError('duplicate fixture id')
        fixtures.append(f); labels[fid]=lab; meta[fid]={'league':str(r['league']),'season':int(r['season']),'source_date':str(r['date']),'source_fid':int(r['fid'])}
    return fixtures,labels,meta,{'row_n':len(raw),'canonical_jsonl_sha256':got,'max_season':max_season}

def build_rows(xg,v2,v1_engine,v1_result,db,identity,max_season):
    fixtures,labels,meta,source=safe_universe(xg,db,max_season,identity)
    v1=xg.import_v1(v1_engine); v1params=xg.load_v1_params(v1_result)
    fold_map,fold_rows=xg.outer_fold_map(fixtures)
    cache=xg.precompute_v1_cache(v1,v1params,fixtures,labels)
    p=v2.fixed_xg_params(xg)
    score_seasons=set(range(2018,max_season+1))
    rep=xg.replay(v1,v1params,fixtures,labels,meta,p,score_seasons,fold_map,write_predictions=True,base_cache=cache)
    raw=[]; parents={}
    for pr in rep['predictions']:
        fid=pr['fixture_id']; lab=labels[fid]
        raw.append({'fixture_id':fid,'league':pr['league'],'season':int(pr['season']),'kickoff':pr['kickoff'],'home_team_id':pr['home_team_id'],'away_team_id':pr['away_team_id'],'home_goals':lab.home_goals,'away_goals':lab.away_goals,'v1_parent':pr['v1'],'xg_parent':pr['challenger']})
        parents[fid]={'v1_mu_home':float(pr['v1']['mu_home']),'v1_mu_away':float(pr['v1']['mu_away']),'xg_mu_home':float(pr['challenger']['mu_home']),'xg_mu_away':float(pr['challenger']['mu_away']),'xg_fallback':bool(pr['challenger']['dynamic']['fallback_exact_v1'])}
    weighted=v2.with_weight(xg,raw,.75)
    for r in weighted: r.update(parents[r['fixture_id']])
    counts={s:sum(1 for r in weighted if r['season']==s) for s in range(2018,max_season+1)}
    exp={2018:1826,2019:1725,2020:1826,2021:1826,2022:1826}
    if max_season==2023: exp[2023]=1752
    if counts!=exp: raise ResearchError(f'weighted row count drift {counts}')
    return weighted,fold_map,fold_rows,source

def integrate(m):
    p=[0.0,0.0,0.0]
    for h in range(MAXG+1):
        for a in range(MAXG+1): p[0 if h>a else 1 if h==a else 2]+=float(m[h][a])
    return p

def goal_bucket(g): return 0 if g==0 else 1 if g==1 else 2 if g==2 else 3
def total_bucket(t): return 0 if t<=1 else 1 if t==2 else 2 if t==3 else 3 if t==4 else 4
def margin_bucket(d): return 0 if d<=-2 else 1 if d==-1 else 2 if d==0 else 3 if d==1 else 4

def baseline_distributions(m,home_side=True):
    score=[0.0]*4; concede=[0.0]*4; total=[0.0]*5; margin=[0.0]*5
    fts=cs=0.0
    for h in range(MAXG+1):
        for a in range(MAXG+1):
            q=float(m[h][a]); gf=h if home_side else a; ga=a if home_side else h
            score[goal_bucket(gf)]+=q; concede[goal_bucket(ga)]+=q; total[total_bucket(h+a)]+=q; margin[margin_bucket(gf-ga)]+=q
            if gf==0: fts+=q
            if ga==0: cs+=q
    return {'scored':score,'conceded':concede,'total':total,'fts':fts,'cs':cs,'margin':margin}

def residual_record(m,hg,ag,home_side):
    b=baseline_distributions(m,home_side); gf=hg if home_side else ag; ga=ag if home_side else hg
    def resid(k,n,idx):
        out=[-float(x) for x in b[k]]; out[idx]+=1.0; return out
    return {'scored':resid('scored',4,goal_bucket(gf)),'conceded':resid('conceded',4,goal_bucket(ga)),'total':resid('total',5,total_bucket(hg+ag)),'fts':(1.0 if gf==0 else 0.0)-b['fts'],'cs':(1.0 if ga==0 else 0.0)-b['cs'],'margin':resid('margin',5,margin_bucket(gf-ga))}

def avg_records(records):
    if not records: return None
    n=len(records); out={'scored':[0.0]*4,'conceded':[0.0]*4,'total':[0.0]*5,'fts':0.0,'cs':0.0,'margin':[0.0]*5}
    for r in records:
        for k in ('scored','conceded','total','margin'):
            for i,x in enumerate(r[k]): out[k][i]+=x
        out['fts']+=r['fts']; out['cs']+=r['cs']
    for k in ('scored','conceded','total','margin'): out[k]=[x/n for x in out[k]]
    out['fts']/=n; out['cs']/=n
    return out

def cell_raw_score(h,a,hp,ap):
    comps=[hp['scored'][goal_bucket(h)],ap['conceded'][goal_bucket(h)],ap['scored'][goal_bucket(a)],hp['conceded'][goal_bucket(a)],0.5*(hp['total'][total_bucket(h+a)]+ap['total'][total_bucket(h+a)]),0.5*(hp['margin'][margin_bucket(h-a)]+ap['margin'][margin_bucket(a-h)])]
    if h==0: comps.append(0.5*(hp['fts']+ap['cs']))
    if a==0: comps.append(0.5*(ap['fts']+hp['cs']))
    return sum(comps)/len(comps)

def tilt_matrix(base,hp,ap,shrinkage):
    target=integrate(base); out=[[0.0]*(MAXG+1) for _ in range(MAXG+1)]
    regions=[[],[],[]]
    for h in range(MAXG+1):
        for a in range(MAXG+1):
            k=0 if h>a else 1 if h==a else 2; q=float(base[h][a])*math.exp(float(shrinkage)*cell_raw_score(h,a,hp,ap)); out[h][a]=q; regions[k].append((h,a))
    for k,inds in enumerate(regions):
        mass=math.fsum(out[h][a] for h,a in inds)
        if mass<=0: raise ResearchError('tilt region lost all mass')
        fac=target[k]/mass
        for h,a in inds: out[h][a]*=fac
        got=math.fsum(out[h][a] for h,a in inds); resid=target[k]-got; h0,a0=max(inds,key=lambda z:out[z[0]][z[1]]); out[h0][a0]+=resid
        if out[h0][a0]<0: raise ResearchError('negative residual cell')
    vals=[q for row in out for q in row]
    if any((not math.isfinite(q)) or q<0 for q in vals) or abs(math.fsum(vals)-1.0)>TOL: raise ResearchError('invalid tilted matrix')
    return out

def matrix_errors(base,cand):
    bp,cp=integrate(base),integrate(cand)
    return max(abs(a-b) for a,b in zip(bp,cp)),abs(math.fsum(q for row in cand for q in row)-1.0)

def exact_ll(rows,mmap):
    if not rows: return None
    s=0.0
    for r in rows:
        h,a=int(r['home_goals']),int(r['away_goals']); p=(mmap[r['fixture_id']][h][a] if 0<=h<=MAXG and 0<=a<=MAXG else 0.0); s+=-math.log(max(p,1e-15))
    return s/len(rows)

def total_probs(m):
    p=[0.0]*7
    for h in range(MAXG+1):
        for a in range(MAXG+1): p[min(h+a,6)]+=float(m[h][a])
    return p

def total_rps(rows,mmap):
    if not rows:return None
    z=0.0
    for r in rows:
        p=total_probs(mmap[r['fixture_id']]); y=min(int(r['home_goals'])+int(r['away_goals']),6); c=0.0
        for j in range(6):
            c += p[j]-(1.0 if y<=j else 0.0); z += c*c/6.0
    return z/len(rows)

def score_top1(rows,mmap):
    ok=0
    for r in rows:
        m=mmap[r['fixture_id']]; best=max(((m[h][a],h,a) for h in range(MAXG+1) for a in range(MAXG+1)))[1:]
        ok+=int(best==(int(r['home_goals']),int(r['away_goals'])))
    return ok/len(rows) if rows else None

def one_x_two_metrics(usr,rows,mmap):
    pred=lambda r:integrate(mmap[r['fixture_id']]); return usr.metrics(rows,pred)

def representative_calibration(rows,mmap):
    pairs=[(0,0),(1,1),(2,1),(1,0),(2,0),(0,1)]; out={}
    for h,a in pairs:
        mp=sum(mmap[r['fixture_id']][h][a] for r in rows)/len(rows); obs=sum(1 for r in rows if r['home_goals']==h and r['away_goals']==a)/len(rows)
        out[f'{h}-{a}']={'mean_probability':mp,'observed_rate':obs,'residual_probability_minus_observed':mp-obs}
    return out

def make_baselines(usr,v31,js,rows,proc,seasons):
    mmap={}; fit_meta={}
    for s in seasons:
        train=[r for r in rows if 2018<=r['season']<s]; test=[r for r in rows if r['season']==s]
        model,meta=usr.fit(train,proc); fit_meta[str(s)]=meta
        for r in test:
            target=v31.predict_variant(usr,model,r,proc,'V3.1-A',{'residual_scale':0.25}); m=js.candidate_matrix('V3.1.1-A',{},r,target)
            if not js.matrix_valid(m): raise ResearchError('invalid V3.1.1 baseline matrix')
            if max(abs(a-b) for a,b in zip(js.integrate(m),target))>TOL: raise ResearchError('V3.1.1 baseline region identity failure')
            mmap[r['fixture_id']]=m
    return mmap,fit_meta

def build_profile_snapshots(rows,baseline_mmap,lookbacks=(1,2,4)):
    rows=sorted([r for r in rows if 2019<=r['season']<=2022],key=lambda r:(r['kickoff'],r['fixture_id']))
    histories=defaultdict(list); pending=[]; seq=0; snapshots={}
    i=0
    while i<len(rows):
        ko=rows[i]['kickoff']; ko_dt=x_parse_kickoff(ko) if isinstance(ko,str) else ko; batch=[]
        while i<len(rows) and rows[i]['kickoff']==ko: batch.append(rows[i]); i+=1
        while pending and pending[0][0]<=ko_dt:
            _,_,team,season,rec=heapq.heappop(pending); histories[team].append((season,rec))
        for r in batch:
            s=r['season']; fid=r['fixture_id']; snapshots[fid]={}
            for L in lookbacks:
                hr=[rec for ss,rec in histories[r['home_team_id']] if ss>=s-L]; ar=[rec for ss,rec in histories[r['away_team_id']] if ss>=s-L]
                snapshots[fid][L]={'home_n':len(hr),'away_n':len(ar),'home':avg_records(hr),'away':avg_records(ar)}
        for r in batch:
            m=baseline_mmap[r['fixture_id']]; rel=x_parse_kickoff(r['kickoff'])+timedelta(hours=3) if isinstance(r['kickoff'],str) else r['kickoff']+timedelta(hours=3)
            seq+=1; heapq.heappush(pending,(rel,seq,r['home_team_id'],r['season'],residual_record(m,int(r['home_goals']),int(r['away_goals']),True)))
            seq+=1; heapq.heappush(pending,(rel,seq,r['away_team_id'],r['season'],residual_record(m,int(r['home_goals']),int(r['away_goals']),False)))
    return snapshots

def x_parse_kickoff(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def candidate_map(rows,base_map,snapshots,params):
    out={}; active=0; fallback=0; maxp=maxsum=0.0; L=int(params['lookback_seasons']); mn=int(params['min_effective_matches']); sh=float(params['shrinkage'])
    for r in rows:
        b=base_map[r['fixture_id']]; sp=snapshots[r['fixture_id']][L]
        if sp['home_n']>=mn and sp['away_n']>=mn and sp['home'] is not None and sp['away'] is not None:
            c=tilt_matrix(b,sp['home'],sp['away'],sh); active+=1
        else: c=b; fallback+=1
        pe,se=matrix_errors(b,c); maxp=max(maxp,pe); maxsum=max(maxsum,se); out[r['fixture_id']]=c
    return out,{'active_n':active,'fallback_n':fallback,'active_rate':active/max(1,active+fallback),'max_1x2_abs_delta':maxp,'max_matrix_sum_error':maxsum}

def eval_set(usr,rows,base_map,cand_map,fold_map=None,require_folds=False):
    be=exact_ll(rows,base_map); ce=exact_ll(rows,cand_map); bt=total_rps(rows,base_map); ct=total_rps(rows,cand_map)
    bm=one_x_two_metrics(usr,rows,base_map); cm=one_x_two_metrics(usr,rows,cand_map)
    maxp=max(max(abs(a-b) for a,b in zip(integrate(base_map[r['fixture_id']]),integrate(cand_map[r['fixture_id']]))) for r in rows)
    topid=all(max(range(3),key=lambda i:integrate(base_map[r['fixture_id']])[i])==max(range(3),key=lambda i:integrate(cand_map[r['fixture_id']])[i]) for r in rows)
    maxsum=max(abs(math.fsum(q for row in cand_map[r['fixture_id']] for q in row)-1.0) for r in rows)
    folds=[]; fex=ftot=0
    if fold_map:
        for k in range(8):
            rs=[r for r in rows if fold_map.get(r['fixture_id'])==k]
            if not rs: continue
            eb,ec=exact_ll(rs,base_map),exact_ll(rs,cand_map); rb,rc=total_rps(rs,base_map),total_rps(rs,cand_map); oe=ec-eb; ot=rc-rb
            ok1=oe<=TOL; ok2=ot<=TOL; fex+=int(ok1); ftot+=int(ok2); folds.append({'fold':k,'n':len(rs),'exact_score_logloss_delta':oe,'total_goals_rps_delta':ot,'exact_nondegrade':ok1,'total_nondegrade':ok2})
    groups=[]; worst=-1e9
    by=defaultdict(list)
    for r in rows: by[(r['league'],r['season'])].append(r)
    for (league,season),rs in sorted(by.items()):
        if len(rs)<100: continue
        d=exact_ll(rs,cand_map)-exact_ll(rs,base_map); worst=max(worst,d); groups.append({'league':league,'season':season,'n':len(rs),'exact_score_logloss_degradation':d})
    return {'n':len(rows),'baseline':{'exact_score_logloss':be,'total_goals_rps':bt,'one_x_two':bm,'exact_score_top1':score_top1(rows,base_map)},'candidate':{'exact_score_logloss':ce,'total_goals_rps':ct,'one_x_two':cm,'exact_score_top1':score_top1(rows,cand_map)},'delta':{'exact_score_logloss':ce-be,'total_goals_rps':ct-bt,'one_x_two_logloss':cm['logloss']-bm['logloss'],'one_x_two_brier':cm['brier']-bm['brier'],'one_x_two_rps':cm['rps']-bm['rps'],'one_x_two_top1':cm['top1']-bm['top1']},'identity':{'max_1x2_probability_abs_delta':maxp,'top1_exact_identity':topid,'max_matrix_sum_error':maxsum},'folds':folds,'fold_exact_score_logloss_nondegrade_n':fex,'fold_total_goals_rps_nondegrade_n':ftot,'league_season_groups':groups,'worst_eligible_league_season_exact_score_logloss_degradation':worst if groups else None,'representative_score_calibration':representative_calibration(rows,cand_map)}

def grid_params(freeze):
    g=freeze['grid_and_selection']['grid_cartesian']; return [{'shrinkage':s,'lookback_seasons':L,'min_effective_matches':m} for s in g['shrinkage'] for L in g['lookback_seasons'] for m in g['min_effective_matches']]

def choose_2020(usr,rows,base_map,snapshots,freeze):
    board=[]
    for p in grid_params(freeze):
        cm,cov=candidate_map(rows,base_map,snapshots,p); e=eval_set(usr,rows,base_map,cm); feasible=e['identity']['max_1x2_probability_abs_delta']<=TOL and e['identity']['top1_exact_identity'] and e['delta']['exact_score_logloss']<=TOL and e['delta']['total_goals_rps']<=TOL
        board.append({'params':p,'feasible':feasible,'coverage':cov,'evaluation':e})
    fs=[x for x in board if x['feasible']]
    if not fs:return None,board
    fs.sort(key=lambda x:(x['evaluation']['candidate']['exact_score_logloss'],x['evaluation']['candidate']['total_goals_rps'],x['params']['shrinkage'],-x['params']['min_effective_matches'],-x['params']['lookback_seasons']))
    return fs[0],board

def gate_2021_2022(e,c):
    g=c['hard_gates_2021_2022']; checks={
      'coverage':True,
      'one_x_two_probability':e['identity']['max_1x2_probability_abs_delta']<=g['one_x_two_probability_max_abs_delta']+TOL,
      'one_x_two_top1':e['identity']['top1_exact_identity'],
      'one_x_two_logloss':e['delta']['one_x_two_logloss']<=g['global_1x2_logloss_delta_max']+TOL,
      'one_x_two_brier':e['delta']['one_x_two_brier']<=g['global_1x2_brier_delta_max']+TOL,
      'one_x_two_rps':e['delta']['one_x_two_rps']<=g['global_1x2_rps_delta_max']+TOL,
      'exact_score_logloss':e['delta']['exact_score_logloss']<=g['exact_score_logloss_delta_max']+TOL,
      'total_goals_rps':e['delta']['total_goals_rps']<=g['total_goals_rps_delta_max']+TOL,
      'matrix_sum':e['identity']['max_matrix_sum_error']<=g['score_matrix_sum_max_abs_error']+TOL,
      'outcome_region_mass':e['identity']['max_1x2_probability_abs_delta']<=g['outcome_region_mass_max_abs_error']+TOL,
      'fold_exact':e['fold_exact_score_logloss_nondegrade_n']>=g['fold_exact_score_logloss_nondegrade_min'],
      'fold_total':e['fold_total_goals_rps_nondegrade_n']>=g['fold_total_goals_rps_nondegrade_min'],
      'league_season':e['worst_eligible_league_season_exact_score_logloss_degradation'] is not None and e['worst_eligible_league_season_exact_score_logloss_degradation']<=g['worst_eligible_league_season_exact_score_logloss_degradation_max']+TOL,
    }
    return {'checks':checks,'all_pass':all(checks.values())}

def main():
    ap=argparse.ArgumentParser()
    for n in ['contract','implementation-freeze','v2-engine','xg-engine','v1-engine','v1-result','db','xg-identity','usr1-engine','v31-engine','v311-engine','out']:
        ap.add_argument('--'+n,type=pathlib.Path,required=True)
    a=ap.parse_args(); out=a.out; out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text()); fr=json.loads(a.implementation_freeze.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_SCORING' or fr['status']!='FROZEN_BEFORE_ANY_SCORE_SHAPE_TARGET_SCORING': raise ResearchError('freeze status mismatch')
    xg=loadmod('ss_xg',a.xg_engine); v2=loadmod('ss_v2',a.v2_engine); usr=loadmod('ss_usr',a.usr1_engine); v31=loadmod('ss_v31',a.v31_engine); js=loadmod('ss_v311',a.v311_engine)
    rows,fold_map,fold_rows,source=build_rows(xg,v2,a.v1_engine,a.v1_result,a.db,a.xg_identity,2022)
    if source['row_n']!=fr['development_source_freeze']['row_n'] or source['canonical_jsonl_sha256']!=fr['development_source_freeze']['canonical_jsonl_sha256']: raise ResearchError('implementation source freeze mismatch')
    proc,proc_receipt=v31.process_features_ext(usr,a.db,rows,2022)
    bmap,fitmeta=make_baselines(usr,v31,js,rows,proc,[2019,2020,2021,2022]); snaps=build_profile_snapshots(rows,bmap)
    write_json(out/'source_receipt.json',{'source':source,'counts':{str(s):sum(1 for r in rows if r['season']==s) for s in range(2018,2023)},'2023_opened':False,'3504_opened':False,'same_kickoff_isolation':True})
    write_json(out/'baseline_receipt.json',{'candidate_id':'V3.1.1-A','fit_meta':fitmeta,'process_feature_receipt':proc_receipt,'baseline_matrix_n':len(bmap),'one_x_two_frozen_exact':True})
    dev=[r for r in rows if r['season']==2020]; chosen,board=choose_2020(usr,dev,bmap,snaps,fr); write_json(out/'development_grid_2020.json',{'grid_n':len(board),'selected':chosen,'board':board,'selection_frozen_before_2021_2022':True})
    if chosen is None:
        final={'schema_version':'football3-v3-score-shape-final-v1','status':c['terminal']['development_failure'],'reason':'no_feasible_2020_grid_candidate','research_only':True,'promotion_allowed':False,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(out/'final_status.json',final); (out/'artifact_slug.txt').write_text(final['status']+'\n'); print(json.dumps(final,sort_keys=True)); return 0
    params=chosen['params']; gate_rows=[r for r in rows if r['season'] in (2021,2022)]; cmap,cov=candidate_map(gate_rows,bmap,snaps,params); e=eval_set(usr,gate_rows,bmap,cmap,fold_map=fold_map,require_folds=True); gate=gate_2021_2022(e,c); gate_payload={'selected_params':params,'coverage':cov,'evaluation':e,'gates':gate,'hyperparameters_fixed_from_2020':True}; write_json(out/'fixed_gate_2021_2022.json',gate_payload)
    if not gate['all_pass']:
        final={'schema_version':'football3-v3-score-shape-final-v1','status':c['terminal']['development_failure'],'reason':'fixed_2021_2022_hard_gate_failed','research_only':True,'promotion_allowed':False,'selected_params':params,'fixed_2021_2022':gate_payload,'2023_opened':False,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(out/'final_status.json',final); (out/'artifact_slug.txt').write_text(final['status']+'\n'); print(json.dumps(final,sort_keys=True)); return 0
    rows23,_,_,source23=build_rows(xg,v2,a.v1_engine,a.v1_result,a.db,a.xg_identity,2023); proc23,proc23r=v31.process_features_ext(usr,a.db,rows23,2023); b23,fit23=make_baselines(usr,v31,js,rows23,proc23,[2019,2020,2021,2022,2023]); snaps23=build_profile_snapshots(rows23,b23); hold=[r for r in rows23 if r['season']==2023]; c23,cov23=candidate_map(hold,b23,snaps23,params); e23=eval_set(usr,hold,b23,c23); gg=fr['candidate_fixed_2023_gate']['gates']; checks23={'coverage':True,'one_x_two_probability':e23['identity']['max_1x2_probability_abs_delta']<=gg['one_x_two_probability_max_abs_delta']+TOL,'one_x_two_top1':e23['identity']['top1_exact_identity'],'one_x_two_logloss':e23['delta']['one_x_two_logloss']<=gg['global_1x2_logloss_delta_max']+TOL,'one_x_two_brier':e23['delta']['one_x_two_brier']<=gg['global_1x2_brier_delta_max']+TOL,'one_x_two_rps':e23['delta']['one_x_two_rps']<=gg['global_1x2_rps_delta_max']+TOL,'exact_score_logloss':e23['delta']['exact_score_logloss']<=gg['exact_score_logloss_delta_max']+TOL,'total_goals_rps':e23['delta']['total_goals_rps']<=gg['total_goals_rps_delta_max']+TOL,'matrix_sum':e23['identity']['max_matrix_sum_error']<=gg['score_matrix_sum_max_abs_error']+TOL,'outcome_region_mass':e23['identity']['max_1x2_probability_abs_delta']<=gg['outcome_region_mass_max_abs_error']+TOL,'league_season':e23['worst_eligible_league_season_exact_score_logloss_degradation'] is not None and e23['worst_eligible_league_season_exact_score_logloss_degradation']<=gg['worst_eligible_league_season_exact_score_logloss_degradation_max']+TOL}; pass23=all(checks23.values()); hpay={'selected_params':params,'coverage':cov23,'source':source23,'evaluation':e23,'gates':{'checks':checks23,'all_pass':pass23},'fit_meta':fit23,'process_feature_receipt':proc23r}; write_json(out/'candidate_fixed_2023.json',hpay)
    status=c['terminal']['candidate_fixed_2023_success'] if pass23 else c['terminal']['candidate_fixed_2023_failure']; final={'schema_version':'football3-v3-score-shape-final-v1','status':status,'research_only':True,'promotion_allowed':False,'selected_params':params,'fixed_2021_2022':gate_payload,'candidate_fixed_2023':hpay,'2023_opened':True,'3504_opened':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}; write_json(out/'final_status.json',final); (out/'artifact_slug.txt').write_text(status+'\n'); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
