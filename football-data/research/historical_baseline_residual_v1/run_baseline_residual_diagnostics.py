from __future__ import annotations
import argparse, collections, datetime as dt, json, math, pathlib, sqlite3, sys

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[2]
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

def feature_map(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in common.LEAGUES)
    games=[dict(r) for r in con.execute(f'''select id,fid,h_id,a_id,date,league,season,h_goals,a_goals from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',common.LEAGUES)]
    starts=collections.defaultdict(dict)
    for mid,tid,pid in con.execute(f'''select l.match_id,l.team_id,l.player_id from lineup_stats l join general_game_stats g on g.id=l.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and l.position!='Sub' ''',common.LEAGUES):
        starts[int(mid)].setdefault(int(tid),set()).add(int(pid))
    con.close()
    assert len(games)==16332 and len(starts)==16332
    bytime=collections.OrderedDict()
    for g in games: bytime.setdefault(str(g['date']),[]).append(g)
    states=collections.defaultdict(SchedState); last_season={}; tables=collections.defaultdict(dict); out={}
    for datestr,batch in bytime.items():
        now=dt.datetime.fromisoformat(datestr); snaps={}
        for g in batch:
            k=(g['league'],int(g['season']))
            if k not in snaps: snaps[k]=ds.table_snapshot(tables[k])
        for g in sorted(batch,key=lambda x:int(x['id'])):
            if int(g['season'])<2020: continue
            hk=(g['league'],int(g['h_id'])); ak=(g['league'],int(g['a_id'])); hs=states.get(hk); aa=states.get(ak)
            def sched(s):
                if s is None or s.last_dt is None: return (None,None)
                rest=min(30.0,(now-s.last_dt).total_seconds()/86400.0); c14=sum(1 for d in s.dates if 0<(now-d).total_seconds()/86400.0<=14); return rest,c14
            hr,h14=sched(hs); ar,a14=sched(aa)
            hrot=None if hs is None or last_season.get(hk)!=g['season'] else hs.rotation; arot=None if aa is None or last_season.get(ak)!=g['season'] else aa.rotation
            ts=snaps[(g['league'],int(g['season']))]; hi=ds.importance(ts.get(int(g['h_id'])),g['league']); ai=ds.importance(ts.get(int(g['a_id'])),g['league'])
            out[f"understat:{int(g['fid'])}"]={
              'rest_diff':None if hr is None or ar is None else hr-ar,
              'cong14_diff':None if h14 is None or a14 is None else h14-a14,
              'rotation_diff':None if hrot is None or arot is None else hrot-arot,
              'rotation_mean':None if hrot is None or arot is None else 0.5*(hrot+arot),
              'importance_diff':None if hi is None or ai is None else hi-ai,
              'importance_mean':None if hi is None or ai is None else 0.5*(hi+ai),
            }
        # consume the target batch only after all features at this kickoff are snapshotted
        for g in sorted(batch,key=lambda x:int(x['id'])):
            for tid in (int(g['h_id']),int(g['a_id'])):
                key=(g['league'],tid); s=states[key]
                if last_season.get(key) is not None and last_season[key]!=g['season']: s.last_start=None; s.rotation=None
                s.last_dt=now; s.dates.append(now); s.dates=s.dates[-20:]; s.update_start(starts[int(g['id'])].get(tid)); last_season[key]=int(g['season'])
            tab=tables[(g['league'],int(g['season']))]
            for tid in (int(g['h_id']),int(g['a_id'])): tab.setdefault(tid,{'pts':0,'gf':0,'ga':0,'played':0})
            h=tab[int(g['h_id'])]; a=tab[int(g['a_id'])]; hg=int(g['h_goals']); ag=int(g['a_goals']); h['played']+=1; a['played']+=1; h['gf']+=hg; h['ga']+=ag; a['gf']+=ag; a['ga']+=hg
            if hg>ag: h['pts']+=3
            elif hg<ag: a['pts']+=3
            else: h['pts']+=1; a['pts']+=1
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
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_BEFORE_RESIDUAL_DIAGNOSTICS'
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
    cov={k:sum(r.get(k) is not None for r in rows)/len(rows) for k in ('rest_diff','rotation_diff','importance_mean')}
    groups={'balance_nonlinearity':['balance_abs','balance_sq'],'schedule':['rest_diff','cong14_diff'],'rotation':['rotation_diff','rotation_mean'],'importance':['importance_diff','importance_mean'],'full':['balance_abs','balance_sq','rest_diff','cong14_diff','rotation_diff','rotation_mean','importance_diff','importance_mean']}
    ridge=float(c['method']['fixed_ridge']); result={}
    for base in ('base','b'):
        result[base]={
          'draw':rolling_axis(rows,base+'_draw',lambda r:int(r['y']==1),groups,ridge),
          'side':rolling_axis(rows,base+'_side',lambda r:int(r['y']==0),groups,ridge,True),
          'low_score':rolling_axis(rows,base+'_low',lambda r:int(r['hg']+r['ag']<=2),groups,ridge),
          'score_shape':score_shape(rows,base),
        }
    def wins(axis,group): return sum(1 for z in result['b'][axis] if z[group]['delta']<0)
    cls={}
    for g in groups:
        w=max(wins('draw',g),wins('side',g),wins('low_score',g)); cls[g]='ROBUST_POST_B_RESIDUAL' if w==2 else ('MIXED_POST_B_RESIDUAL' if w==1 else 'NO_POST_B_RESIDUAL')
    out={'schema_version':'football3-historical-baseline-residual-result-v1','status':c['terminal']['pass'],'research_only':True,'development_n':len(rows),'source_max_season_loaded':2022,'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,'stage6_b_active_n':srec['active'],'coverage':cov,'residual_tests':result,'classification':cls,'next_step':'USE_RESIDUAL_EVIDENCE_TO_FREEZE_A_SMALL_CANDIDATE_FAMILY_ON_CONSUMED_HISTORY_ONLY'}
    assert out['stage6_b_active_n']==5463; assert min(cov.values())>0.93; assert out['historical_confirmation_2023_labels_opened'] is False and out['prospective_1335_data_touched'] is False
    common.write_json(a.out/'baseline_residual_diagnostics.json',out); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
