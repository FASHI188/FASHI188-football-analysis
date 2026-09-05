from __future__ import annotations
import argparse, collections, datetime as dt, json, math, pathlib, sqlite3, sys

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'research'/'stage6_pre_b_deep_ppda'))
sys.path.insert(0,str(ROOT/'research'/'historical_direction_screen_v1'))
import common
import run_stage6_pre_b as bmod
import screen_pit_directions as ds

EPS=1e-12

class SchedState:
    __slots__=('last_dt','dates','last_start','rotation')
    def __init__(self): self.last_dt=None; self.dates=[]; self.last_start=None; self.rotation=None
    def update_start(self,start):
        if start:
            if self.last_start is not None: self.rotation=1.0-min(1.0,len(self.last_start & start)/11.0)
            self.last_start=set(start)

def logit(p):
    p=min(max(float(p),1e-8),1-1e-8); return math.log(p/(1-p))

def matrix_low(m):
    return sum(float(v) for h,row in enumerate(m) for a,v in enumerate(row) if h+a<=2)

def matrix_cell(m,h,a):
    return float(m[h][a]) if h<len(m) and a<len(m[h]) else 0.0

def team_match_profile(shots):
    xgs=[float(s['xG']) for s in shots]
    total=sum(xgs); denom=max(total,EPS)
    return {
      'high':sum(x for x in xgs if x>=0.20)/denom,
      'central':sum(float(s['xG']) for s in shots if float(s['X'])>=0.83 and 0.33<=float(s['Y'])<=0.67)/denom,
      'setp':sum(float(s['xG']) for s in shots if str(s['situation']) in ('SetPiece','FromCorner','DirectFreekick'))/denom,
      'hhi':sum((x/denom)**2 for x in xgs) if total>EPS else 0.0,
    }

def feature_map(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in common.LEAGUES)
    games=[dict(r) for r in con.execute(f'''select id,fid,h_id,a_id,date,league,season from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',common.LEAGUES)]
    shots=collections.defaultdict(lambda:{'h':[],'a':[]})
    for r in con.execute(f'''select e.match_id,e.h_a,e.xG,e.X,e.Y,e.situation from game_events e join general_game_stats g on g.id=e.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and e.situation!='Penalty' order by e.match_id,e.id''',common.LEAGUES):
        shots[int(r['match_id'])][str(r['h_a'])].append(dict(r))
    con.close(); assert len(games)==16332
    states=collections.defaultdict(lambda:collections.deque(maxlen=8)); out={}; bytime=collections.OrderedDict()
    for g in games: bytime.setdefault(str(g['date']),[]).append(g)
    def prior_profile(key,season):
        q=states.get(key)
        if not q or len(q)<3: return None
        return {k:sum(float(r[k]) for r in q)/len(q) for k in ('atk_high','atk_central','atk_setp','atk_hhi','def_high','def_central','def_setp','def_hhi')}
    for datestr,batch in bytime.items():
        for g in sorted(batch,key=lambda x:int(x['id'])):
            if int(g['season'])<2020: continue
            hk=(g['league'],int(g['h_id'])); ak=(g['league'],int(g['a_id'])); hp=prior_profile(hk,int(g['season'])); ap=prior_profile(ak,int(g['season']))
            rec={}
            if hp and ap:
                for z in ('high','central','setp','hhi'):
                    signed=hp['atk_'+z]*ap['def_'+z]-ap['atk_'+z]*hp['def_'+z]
                    rec[z+'_fit_diff']=signed; rec[z+'_fit_abs']=abs(signed)
            out[f"understat:{int(g['fid'])}"]=rec
        for g in sorted(batch,key=lambda x:int(x['id'])):
            season=int(g['season']); hk=(g['league'],int(g['h_id'])); ak=(g['league'],int(g['a_id']))
            hm=team_match_profile(shots[int(g['id'])]['h']); am=team_match_profile(shots[int(g['id'])]['a'])
            states[hk].append({**{'atk_'+k:v for k,v in hm.items()},**{'def_'+k:v for k,v in am.items()}})
            states[ak].append({**{'atk_'+k:v for k,v in am.items()},**{'def_'+k:v for k,v in hm.items()}})
    return out

def standardize(train,test,keys):
    means={}; sds={}
    for k in keys:
        vals=[float(r[k]) for r in train if r.get(k) is not None and math.isfinite(float(r[k]))]
        means[k]=sum(vals)/len(vals); sds[k]=math.sqrt(sum((x-means[k])**2 for x in vals)/len(vals)) or 1.0
    def make(rr):
        X=[]
        for r in rr:
            x=[1.0]
            for k in keys:
                v=r.get(k); x.append(0.0 if v is None else (float(v)-means[k])/sds[k])
            X.append(x)
        return X
    return make(train),make(test)

def fit_offset(X,y,offset,ridge):
    p=len(X[0]); beta=[0.0]*p
    for _ in range(30):
        g=[0.0]*p; H=[[0.0]*p for _ in range(p)]
        for x,yy,off in zip(X,y,offset):
            pr=ds.sigmoid(float(off)+sum(beta[j]*x[j] for j in range(p))); w=max(pr*(1-pr),1e-6); d=yy-pr
            for j in range(p):
                g[j]+=x[j]*d
                for k in range(p): H[j][k]+=w*x[j]*x[k]
        for j in range(1,p): g[j]-=ridge*beta[j]; H[j][j]+=ridge
        delta=ds.solve(H,g); beta=[beta[j]+delta[j] for j in range(p)]
        if max(abs(v) for v in delta)<1e-7: break
    return beta

def rolling_axis(rows,prob_key,target,groups,ridge,drop_draw=False):
    out=[]
    for season in (2021,2022):
        tr=[r for r in rows if r['season']<season and (not drop_draw or r['y']!=1)]
        te=[r for r in rows if r['season']==season and (not drop_draw or r['y']!=1)]
        ytr=[target(r) for r in tr]; yte=[target(r) for r in te]
        ptr=[r[prob_key] for r in tr]; pte=[r[prob_key] for r in te]; base_ll=ds.binary_logloss(yte,pte)
        rec={'season':season,'train_n':len(tr),'test_n':len(te),'base_logloss':base_ll}
        for name,keys in groups.items():
            Xtr,Xte=standardize(tr,te,keys); beta=fit_offset(Xtr,ytr,[logit(p) for p in ptr],ridge); pp=[ds.sigmoid(logit(p)+sum(beta[j]*x[j] for j in range(len(beta)))) for p,x in zip(pte,Xte)]
            ll=ds.binary_logloss(yte,pp); rec[name]={'logloss':ll,'delta':ll-base_ll,'auc':ds.auc(yte,pp)}
        out.append(rec)
    return out

def score_shape(rows,prefix):
    vals=sorted(rows,key=lambda r:r['balance_abs']); n=len(vals); k=max(1,n//5); groups={'balanced':vals[:k],'overall':vals,'strong_gap':vals[-k:]}; out={}
    for name,rr in groups.items():
        out[name]={
          'n':len(rr),
          'actual_00':sum(r['hg']==0 and r['ag']==0 for r in rr)/len(rr),
          'pred_00':sum(r[prefix+'_p00'] for r in rr)/len(rr),
          'actual_11':sum(r['hg']==1 and r['ag']==1 for r in rr)/len(rr),
          'pred_11':sum(r[prefix+'_p11'] for r in rr)/len(rr),
          'actual_low2':sum(r['hg']+r['ag']<=2 for r in rr)/len(rr),
          'pred_low2':sum(r[prefix+'_low'] for r in rr)/len(rr),
        }
        for z in ('00','11','low2'): out[name]['residual_'+z]=out[name]['actual_'+z]-out[name]['pred_'+z]
    return out

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'): ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_BEFORE_RESIDUAL_SCREEN'
    b=common.build_frozen_baseline(a,'resid'); dev=[r for r in b['rows'] if r['season'] in (2020,2021,2022) and r['fixture_id'] in b['bmap']]; assert len(dev)==5478
    f=feature_map(a.db); assert all(r['fixture_id'] in f for r in dev)
    wanted={r['fixture_id'] for r in dev}; snaps,srec=bmod.make_snapshots(a.db,wanted,float(c['frozen_bases']['stage6_b_half_life'])); bprob={}; bmats={}
    for r in dev:
        fid=r['fixture_id']; p,on=bmod.predict(b['bmap'][fid],snaps.get(fid),float(c['frozen_bases']['stage6_b_coefficient'])); bprob[fid]=p; bmats[fid]=common.region_rescale(b['bmats'][fid],b['bmap'][fid],p)
    rows=[]
    for r in dev:
        fid=r['fixture_id']; bp=list(map(float,b['bmap'][fid])); cp=list(map(float,bprob[fid])); feat=dict(f[fid]); hg=int(r['home_goals']); ag=int(r['away_goals']); y=0 if hg>ag else 1 if hg==ag else 2
        side=bp[0]/max(EPS,bp[0]+bp[2]); feat.update({'fixture_id':fid,'season':int(r['season']),'hg':hg,'ag':ag,'y':y,'balance_abs':abs(logit(side)),'balance_sq':logit(side)*abs(logit(side)),'base_draw':bp[1],'b_draw':cp[1],'base_side':side,'b_side':cp[0]/max(EPS,cp[0]+cp[2]),'base_low':matrix_low(b['bmats'][fid]),'b_low':matrix_low(bmats[fid]),'base_p00':matrix_cell(b['bmats'][fid],0,0),'base_p11':matrix_cell(b['bmats'][fid],1,1),'b_p00':matrix_cell(bmats[fid],0,0),'b_p11':matrix_cell(bmats[fid],1,1)})
        rows.append(feat)
    coverage=sum(all(r.get(k) is not None for k in ('high_fit_diff','central_fit_diff','setp_fit_diff','hhi_fit_diff')) for r in rows)/len(rows)
    draw_groups={'quality_tail':['high_fit_abs'],'central_access':['central_fit_abs'],'setpiece_route':['setp_fit_abs'],'shot_concentration':['hhi_fit_abs'],'compact_all':['high_fit_abs','central_fit_abs','setp_fit_abs','hhi_fit_abs']}
    side_groups={'quality_tail':['high_fit_diff'],'central_access':['central_fit_diff'],'setpiece_route':['setp_fit_diff'],'shot_concentration':['hhi_fit_diff'],'compact_all':['high_fit_diff','central_fit_diff','setp_fit_diff','hhi_fit_diff']}
    ridge=float(c['method']['fixed_ridge']); result={'draw':rolling_axis(rows,'b_draw',lambda r:int(r['y']==1),draw_groups,ridge),'side':rolling_axis(rows,'b_side',lambda r:int(r['y']==0),side_groups,ridge,True)}
    cls={}
    for g in draw_groups:
        dw=sum(1 for z in result['draw'] if z[g]['delta']<0); sw=sum(1 for z in result['side'] if z[g]['delta']<0)
        if dw==2: cls[g]='ROBUST_DRAW_POST_B_RESIDUAL'
        elif sw==2: cls[g]='ROBUST_SIDE_POST_B_RESIDUAL'
        elif max(dw,sw)==1: cls[g]='MIXED_POST_B_RESIDUAL'
        else: cls[g]='NO_POST_B_RESIDUAL'
    status=c['terminal']['pass'] if coverage>=float(c['gates']['minimum_coverage']) else c['terminal']['stop_coverage']
    out={'schema_version':'football3-shot-profile-matchup-residual-result-v1','status':status,'research_only':True,'development_n':len(rows),'source_max_season_loaded':2022,'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,'stage6_b_active_n':srec['active'],'coverage':coverage,'residual_tests':result,'classification':cls,'next_step':'FREEZE_ONE_SMALL_CANDIDATE_ONLY_IF_A_FAMILY_IS_ROBUST_IN_BOTH_ROLLING_FOLDS_ON_ONE_AXIS'}
    assert out['stage6_b_active_n']==5463; assert out['historical_confirmation_2023_labels_opened'] is False and out['prospective_1335_data_touched'] is False
    common.write_json(a.out/'shot_profile_matchup_residual.json',out); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
