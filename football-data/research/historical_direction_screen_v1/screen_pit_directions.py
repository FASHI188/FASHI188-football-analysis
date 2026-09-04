from __future__ import annotations
import argparse, collections, datetime as dt, json, math, pathlib, sqlite3

BIG5=("Bundesliga","EPL","La liga","Ligue 1","Serie A")
EPS=1e-12

class TeamState:
    __slots__=('xgf','xga','n','last_dt','dates','last_start','rotation')
    def __init__(self):
        self.xgf=self.xga=0.0; self.n=0; self.last_dt=None; self.dates=[]; self.last_start=None; self.rotation=None
    def update_perf(self,xgf,xga,alpha):
        if self.n==0: self.xgf=float(xgf); self.xga=float(xga)
        else:
            self.xgf=(1-alpha)*self.xgf+alpha*float(xgf)
            self.xga=(1-alpha)*self.xga+alpha*float(xga)
        self.n+=1
    def update_start(self,start):
        if start:
            if self.last_start is not None:
                self.rotation=1.0-min(1.0,len(self.last_start & start)/11.0)
            self.last_start=set(start)

def parse_dt(s): return dt.datetime.fromisoformat(str(s))
def sigmoid(z):
    z=max(-50.0,min(50.0,float(z))); return 1.0/(1.0+math.exp(-z))

def solve(A,b):
    n=len(b); m=[list(map(float,A[i]))+[float(b[i])] for i in range(n)]
    for c in range(n):
        p=max(range(c,n),key=lambda r:abs(m[r][c])); m[c],m[p]=m[p],m[c]
        if abs(m[c][c])<1e-9: m[c][c]+=1e-6
        z=m[c][c]
        for j in range(c,n+1): m[c][j]/=z
        for r in range(n):
            if r==c: continue
            f=m[r][c]
            for j in range(c,n+1): m[r][j]-=f*m[c][j]
    return [m[i][n] for i in range(n)]

def fit_logistic(X,y,ridge):
    p=len(X[0]); beta=[0.0]*p; br=min(max(sum(y)/len(y),1e-6),1-1e-6); beta[0]=math.log(br/(1-br))
    for _ in range(30):
        g=[0.0]*p; H=[[0.0]*p for _ in range(p)]
        for x,yy in zip(X,y):
            pr=sigmoid(sum(beta[j]*x[j] for j in range(p))); w=max(pr*(1-pr),1e-6); d=yy-pr
            for j in range(p):
                g[j]+=x[j]*d
                for k in range(p): H[j][k]+=w*x[j]*x[k]
        for j in range(1,p): g[j]-=ridge*beta[j]; H[j][j]+=ridge
        delta=solve(H,g); beta=[beta[j]+delta[j] for j in range(p)]
        if max(abs(v) for v in delta)<1e-7: break
    return beta

def binary_logloss(y,p):
    return -sum(yy*math.log(max(EPS,pp))+(1-yy)*math.log(max(EPS,1-pp)) for yy,pp in zip(y,p))/len(y)

def auc(y,p):
    pairs=sorted(zip(p,y),key=lambda z:z[0]); n1=sum(y); n0=len(y)-n1
    if not n1 or not n0: return None
    rs=0.0; i=0
    while i<len(pairs):
        j=i+1
        while j<len(pairs) and abs(pairs[j][0]-pairs[i][0])<1e-15: j+=1
        avg=(i+1+j)/2.0; rs+=avg*sum(v for _,v in pairs[i:j]); i=j
    return (rs-n1*(n1+1)/2.0)/(n1*n0)

def qtile(vals,q):
    s=sorted(float(v) for v in vals); pos=(len(s)-1)*q; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    return s[lo] if lo==hi else s[lo]*(hi-pos)+s[hi]*(pos-lo)

def rates(rr):
    c=[0,0,0]
    for r in rr: c[0 if r['hg']>r['ag'] else 1 if r['hg']==r['ag'] else 2]+=1
    n=len(rr); return {'n':n,'home':c[0]/n,'draw':c[1]/n,'away':c[2]/n}

def table_snapshot(tab):
    xs=[(tid,s['pts'],s['gf']-s['ga'],s['gf'],s['played']) for tid,s in tab.items()]
    xs.sort(key=lambda x:(-x[1],-x[2],-x[3],x[0])); n=len(xs); out={}
    for i,(tid,pts,gd,gf,played) in enumerate(xs,1): out[tid]={'rank':i,'n':n,'played':played}
    return out

def importance(e,league):
    if not e or e['played']<=0 or e['n']<=1: return None
    rp=(e['rank']-1)/(e['n']-1); exp=34 if league=='Bundesliga' else 38; progress=min(1.0,e['played']/exp)
    return progress*(2.0*abs(rp-0.5))

def prepare(train,test,keys):
    means={}; sds={}
    for k in keys:
        v=[float(r[k]) for r in train if r.get(k) is not None and math.isfinite(float(r[k]))]
        means[k]=sum(v)/len(v); sds[k]=math.sqrt(sum((x-means[k])**2 for x in v)/len(v)) or 1.0
    def make(rr):
        X=[]
        for r in rr:
            row=[1.0]
            for k in keys:
                v=r.get(k); row.append(0.0 if v is None else (float(v)-means[k])/sds[k])
            X.append(row)
        return X
    return make(train),make(test)

def rolling(rows,specs,target,ridge,drop_draw=False):
    out=[]
    for season in (2020,2021,2022):
        tr=[r for r in rows if r['season']<season and (not drop_draw or r['hg']!=r['ag'])]
        te=[r for r in rows if r['season']==season and (not drop_draw or r['hg']!=r['ag'])]
        ytr=[target(r) for r in tr]; yte=[target(r) for r in te]; bp=sum(ytr)/len(ytr)
        rec={'season':season,'train_n':len(tr),'test_n':len(te),'constant':{'logloss':binary_logloss(yte,[bp]*len(te)),'auc':0.5}}
        for name,keys in specs.items():
            Xtr,Xte=prepare(tr,te,keys); b=fit_logistic(Xtr,ytr,ridge); pp=[sigmoid(sum(b[j]*x[j] for j in range(len(b)))) for x in Xte]
            rec[name]={'logloss':binary_logloss(yte,pp),'auc':auc(yte,pp)}
        out.append(rec)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_BEFORE_DIRECTION_SCREEN'; assert c['data_roles']['locked_historical_confirmation'].startswith('2023 labels strictly')
    half=float(c['pit_rules']['team_xg_state_half_life_matches']); alpha=1-math.exp(math.log(0.5)/half); ridge=float(c['diagnostics']['fixed_ridge'])
    con=sqlite3.connect(str(a.db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in BIG5)
    games=[dict(r) for r in con.execute(f'''select id,fid,h_id,a_id,date,league,season,h_goals,a_goals,h_xg,a_xg from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id''',BIG5)]
    # No 2023 row or label is selected. Starting XI is consumed only after each target kickoff batch.
    starters=collections.defaultdict(dict)
    for mid,tid,pid in con.execute(f'''select l.match_id,l.team_id,l.player_id from lineup_stats l join general_game_stats g on g.id=l.match_id where g.league in ({qs}) and g.season between 2014 and 2022 and l.position!='Sub' ''',BIG5):
        starters[int(mid)].setdefault(int(tid),set()).add(int(pid))
    con.close()
    assert len(games)==16332 and len(starters)==16332
    bytime=collections.OrderedDict()
    for g in games: bytime.setdefault(str(g['date']),[]).append(g)
    states=collections.defaultdict(TeamState); last_season={}; tables=collections.defaultdict(dict); rows=[]
    for datestr,batch in bytime.items():
        now=parse_dt(datestr); snaps={}
        for g in batch:
            key=(g['league'],int(g['season']))
            if key not in snaps: snaps[key]=table_snapshot(tables[key])
        for g in sorted(batch,key=lambda x:int(x['id'])):
            hk=(g['league'],int(g['h_id'])); ak=(g['league'],int(g['a_id'])); hs=states.get(hk); aa=states.get(ak)
            r={'id':int(g['id']),'season':int(g['season']),'league':g['league'],'hg':int(g['h_goals']),'ag':int(g['a_goals'])}
            if hs and aa and hs.n and aa.n:
                r['strength_gap']=(hs.xgf-hs.xga)-(aa.xgf-aa.xga); r['strength_abs']=abs(r['strength_gap']); r['prior_total_xg']=0.5*(hs.xgf+aa.xga)+0.5*(aa.xgf+hs.xga)
            else: r['strength_gap']=r['strength_abs']=r['prior_total_xg']=None
            def sched(s):
                if s is None or s.last_dt is None: return (None,None,None)
                rest=min(30.0,(now-s.last_dt).total_seconds()/86400.0); c7=sum(1 for d in s.dates if 0<(now-d).total_seconds()/86400.0<=7); c14=sum(1 for d in s.dates if 0<(now-d).total_seconds()/86400.0<=14); return rest,c7,c14
            hr,h7,h14=sched(hs); ar,a7,a14=sched(aa); r['rest_diff']=None if hr is None or ar is None else hr-ar; r['cong14_diff']=None if h14 is None or a14 is None else h14-a14
            hrot=None if hs is None or last_season.get(hk)!=g['season'] else hs.rotation; arot=None if aa is None or last_season.get(ak)!=g['season'] else aa.rotation
            r['rotation_diff']=None if hrot is None or arot is None else hrot-arot; r['rotation_mean']=None if hrot is None or arot is None else 0.5*(hrot+arot)
            ts=snaps[(g['league'],int(g['season']))]; hi=importance(ts.get(int(g['h_id'])),g['league']); ai=importance(ts.get(int(g['a_id'])),g['league']); r['importance_mean']=None if hi is None or ai is None else 0.5*(hi+ai); r['importance_diff']=None if hi is None or ai is None else hi-ai
            rows.append(r)
        # all target features are now frozen; only then consume target match state/lineup/result updates
        for g in sorted(batch,key=lambda x:int(x['id'])):
            for tid,xgf,xga in ((g['h_id'],g['h_xg'],g['a_xg']),(g['a_id'],g['a_xg'],g['h_xg'])):
                key=(g['league'],int(tid)); s=states[key]
                if last_season.get(key) is not None and last_season[key]!=g['season']: s.last_start=None; s.rotation=None
                s.update_perf(xgf,xga,alpha); s.last_dt=now; s.dates.append(now); s.dates=s.dates[-20:]; s.update_start(starters[int(g['id'])].get(int(tid))); last_season[key]=int(g['season'])
            tab=tables[(g['league'],int(g['season']))]
            for tid in (int(g['h_id']),int(g['a_id'])): tab.setdefault(tid,{'pts':0,'gf':0,'ga':0,'played':0})
            h=tab[int(g['h_id'])]; aw=tab[int(g['a_id'])]; hg=int(g['h_goals']); ag=int(g['a_goals']); h['played']+=1; aw['played']+=1; h['gf']+=hg; h['ga']+=ag; aw['gf']+=ag; aw['ga']+=hg
            if hg>ag: h['pts']+=3
            elif hg<ag: aw['pts']+=3
            else: h['pts']+=1; aw['pts']+=1
    dev=[r for r in rows if 2018<=r['season']<=2022]; assert len(dev)==9029
    for r in dev:
        r['abs_strength']=r['strength_abs']; r['abs_rest_diff']=None if r['rest_diff'] is None else abs(r['rest_diff']); r['abs_cong14_diff']=None if r['cong14_diff'] is None else abs(r['cong14_diff']); r['abs_rotation_diff']=None if r['rotation_diff'] is None else abs(r['rotation_diff']); r['abs_importance_diff']=None if r['importance_diff'] is None else abs(r['importance_diff'])
    cov={k:sum(r.get(k) is not None for r in dev)/len(dev) for k in ('strength_gap','rest_diff','rotation_diff','importance_mean')}
    vv=[r for r in dev if r['strength_abs'] is not None]; q20=qtile([r['strength_abs'] for r in vv],0.2); q80=qtile([r['strength_abs'] for r in vv],0.8); balanced=[r for r in vv if r['strength_abs']<=q20]; strong=[r for r in vv if r['strength_abs']>=q80]
    tv=[r for r in dev if r['prior_total_xg'] is not None]; tq20=qtile([r['prior_total_xg'] for r in tv],0.2); tq80=qtile([r['prior_total_xg'] for r in tv],0.8); lowt=[r for r in tv if r['prior_total_xg']<=tq20]; hight=[r for r in tv if r['prior_total_xg']>=tq80]
    def score_stats(rr):
        n=len(rr); cnt=collections.Counter((r['hg'],r['ag']) for r in rr); return {'n':n,'zero_zero':cnt[(0,0)]/n,'one_one':cnt[(1,1)]/n,'goals_le_2':sum(r['hg']+r['ag']<=2 for r in rr)/n,'top_scorelines':[[f'{h}-{a}',v] for (h,a),v in cnt.most_common(8)]}
    strong_fav=sum((r['strength_gap']>0 and r['hg']>r['ag']) or (r['strength_gap']<0 and r['ag']>r['hg']) for r in strong)/len(strong); strong_upset=sum((r['strength_gap']>0 and r['hg']<r['ag']) or (r['strength_gap']<0 and r['ag']<r['hg']) for r in strong)/len(strong)
    draw_specs={'strength':['abs_strength','prior_total_xg'],'strength_schedule':['abs_strength','prior_total_xg','abs_rest_diff','abs_cong14_diff'],'strength_rotation':['abs_strength','prior_total_xg','abs_rotation_diff','rotation_mean'],'strength_importance':['abs_strength','prior_total_xg','importance_mean','abs_importance_diff'],'full':['abs_strength','prior_total_xg','abs_rest_diff','abs_cong14_diff','abs_rotation_diff','rotation_mean','importance_mean','abs_importance_diff']}
    side_specs={'strength':['strength_gap'],'strength_schedule':['strength_gap','rest_diff','cong14_diff'],'strength_rotation':['strength_gap','rotation_diff','rotation_mean'],'strength_importance':['strength_gap','importance_diff','importance_mean'],'full':['strength_gap','rest_diff','cong14_diff','rotation_diff','rotation_mean','importance_diff','importance_mean']}
    low_specs={'state':['prior_total_xg','abs_strength'],'state_schedule':['prior_total_xg','abs_strength','abs_rest_diff','abs_cong14_diff'],'state_rotation':['prior_total_xg','abs_strength','abs_rotation_diff','rotation_mean'],'state_importance':['prior_total_xg','abs_strength','importance_mean','abs_importance_diff'],'full':['prior_total_xg','abs_strength','abs_rest_diff','abs_cong14_diff','abs_rotation_diff','rotation_mean','importance_mean','abs_importance_diff']}
    draw=rolling(dev,draw_specs,lambda r:int(r['hg']==r['ag']),ridge); side=rolling(dev,side_specs,lambda r:int(r['hg']>r['ag']),ridge,True); low=rolling(dev,low_specs,lambda r:int(r['hg']+r['ag']<=2),ridge)
    def wins(test,base,alt): return sum(1 for x in test if x[alt]['logloss']<x[base]['logloss'])
    classification={
      'strength_gap_and_balanced_draw':'PRIMARY' if all(x['strength']['logloss']<x['constant']['logloss'] for x in draw) and all(x['strength']['auc']>0.53 for x in draw) else 'WEAK',
      'joint_score_low_score_state':'PRIMARY' if all(x['state']['logloss']<x['constant']['logloss'] for x in low) else 'WEAK',
      'match_importance_proxy':'PROMISING_INTERACTION' if wins(draw,'strength','strength_importance')>=2 else 'WEAK',
      'schedule_rest_congestion':'INTERACTION_ONLY' if wins(draw,'strength','strength_schedule')<2 or wins(side,'strength','strength_schedule')<2 else 'PROMISING',
      'rotation_pressure':'INTERACTION_ONLY' if wins(draw,'strength','strength_rotation')<2 or wins(side,'strength','strength_rotation')<2 else 'PROMISING'
    }
    out={'schema_version':'football3-historical-pit-direction-screen-result-v1','status':c['terminal']['pass'],'research_only':True,'source_max_season_loaded':2022,'development_n':len(dev),'historical_confirmation_2023_labels_opened':False,'prospective_1335_data_touched':False,'coverage':cov,'regimes':{'strength_abs_q20':q20,'strength_abs_q80':q80,'overall':rates(dev),'balanced':rates(balanced),'strong_gap':rates(strong),'strong_gap_favorite_win':strong_fav,'strong_gap_upset':strong_upset,'score_overall':score_stats(dev),'score_balanced':score_stats(balanced),'score_strong_gap':score_stats(strong),'prior_total_xg_q20':tq20,'prior_total_xg_q80':tq80,'low_prior_total':{'outcomes':rates(lowt),'scores':score_stats(lowt)},'high_prior_total':{'outcomes':rates(hight),'scores':score_stats(hight)}},'rolling':{'draw':draw,'non_draw_side':side,'low_score':low},'classification':classification,'next_step':'BUILD_FROZEN_BASELINE_RESIDUAL_DIAGNOSTICS_BEFORE_ANY_CANDIDATE_FREEZE'}
    assert cov['strength_gap']>0.99 and cov['rest_diff']>0.99 and cov['rotation_diff']>0.94 and cov['importance_mean']>0.97; assert out['historical_confirmation_2023_labels_opened'] is False; assert out['prospective_1335_data_touched'] is False
    (a.out/'pit_direction_screen.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n'); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
