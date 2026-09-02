from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, pathlib, sqlite3, sys, heapq
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TOL=1e-12
LL_GAIN_MIN=0.001
TOP1_MIN=-0.0015
GROUP_MAX=0.020
DRAW_MAX=0.010
WEAK_MAX=0.010
HALF_LIFE=90.0
PRIOR_MATCHES=12.0
MIN_PROFILE_WEIGHT=3.0
PASS='V3_UPSET_SAFE_RESEARCH_ONLY_PASSED_CONFIRMATION_REQUIRED'
REJECT='HISTORICAL_FUSION_V3_UPSET_SAFE_REJECTED'

class ResearchError(RuntimeError): pass

def sha_file(p):
    h=hashlib.sha256()
    with pathlib.Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def canon(o): return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def write_json(p,o): pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True); pathlib.Path(p).write_text(json.dumps(o,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def loadmod(name,p):
    spec=importlib.util.spec_from_file_location(name,str(p)); m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

def pvec(r): return [float(r['fusion']['p_home']),float(r['fusion']['p_draw']),float(r['fusion']['p_away'])]
def result_idx(r): return 0 if r['home_goals']>r['away_goals'] else 1 if r['home_goals']==r['away_goals'] else 2

def one_contrib(p,y):
    ll=-math.log(max(p[y],1e-15))
    br=sum((p[i]-(1 if i==y else 0.0))**2 for i in range(3))
    c1=p[0]-(1 if y==0 else 0.0); c2=p[0]+p[1]-(1 if y in (0,1) else 0.0)
    rps=(c1*c1+c2*c2)/2.0
    return ll,br,rps,int(max(range(3),key=lambda i:p[i])==y)

def metrics(rows,predfn):
    vals=[one_contrib(predfn(r),result_idx(r)) for r in rows]
    n=len(vals)
    return {'n':n,'logloss':sum(v[0] for v in vals)/n,'brier':sum(v[1] for v in vals)/n,'rps':sum(v[2] for v in vals)/n,'top1':sum(v[3] for v in vals)/n}

@dataclass
class TeamState:
    last: datetime|None=None
    weight: float=0.0
    sums: dict|None=None
    def snapshot(self,t):
        if self.sums is None: self.sums={}
        if self.last is None: return 0.0,{k:0.0 for k in self.sums}
        if t<self.last: raise ResearchError('state time reversal')
        days=(t-self.last).total_seconds()/86400.0
        fac=math.exp(-math.log(2.0)*days/HALF_LIFE)
        return self.weight*fac,{k:v*fac for k,v in self.sums.items()}
    def add(self,t,stats):
        w,s=self.snapshot(t); keys=set(s)|set(stats)
        self.sums={k:s.get(k,0.0)+float(stats.get(k,0.0)) for k in keys}
        self.weight=w+1.0; self.last=t

def process_features(db, scoring_rows):
    leagues=('Bundesliga','EPL','La liga','Ligue 1','Serie A'); qs=','.join('?' for _ in leagues)
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    games=[dict(r) for r in con.execute(f"select id,fid,date,league,season,h_id,a_id,h_xg,a_xg from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id",leagues)]
    ev={}
    sql=f"""select e.match_id,e.h_a,
      sum(case when e.situation!='Penalty' then e.xG else 0 end) npxg,
      sum(case when e.situation!='Penalty' then 1 else 0 end) npshots,
      sum(case when e.situation='OpenPlay' then 1 else 0 end) open_n,
      sum(case when e.situation in ('SetPiece','FromCorner','DirectFreekick') then 1 else 0 end) set_n
      from game_events e join general_game_stats g on g.id=e.match_id
      where g.league in ({qs}) and g.season between 2014 and 2022
      group by e.match_id,e.h_a"""
    for r in con.execute(sql,leagues): ev[(str(r['match_id']),r['h_a'])]=dict(r)
    con.close()
    def side_stats(g,side):
        me=ev.get((str(g['id']),side)); opp=ev.get((str(g['id']),'a' if side=='h' else 'h'))
        if not me or not opp or float(me['npshots'] or 0)<=0 or float(opp['npshots'] or 0)<=0: return None
        return {'npxg_for':float(me['npxg'] or 0.0),'npxg_against':float(opp['npxg'] or 0.0),'npshots_for':float(me['npshots']),'npshots_against':float(opp['npshots']),'npxg_per_shot':float(me['npxg'] or 0.0)/float(me['npshots']),'open_share':float(me['open_n'] or 0.0)/float(me['npshots']),'set_share':float(me['set_n'] or 0.0)/float(me['npshots'])}
    pools=defaultdict(lambda:defaultdict(list))
    for g in games:
        if int(g['season'])<=2017:
            for side in ('h','a'):
                s=side_stats(g,side)
                if s:
                    for k,v in s.items(): pools[g['league']][k].append(v)
    priors={l:{k:sum(v)/len(v) for k,v in d.items()} for l,d in pools.items()}
    wanted={r['fixture_id'] for r in scoring_rows}; states=defaultdict(TeamState); queue=[]; seq=0; out={}
    for g in games:
        ko=datetime.strptime(g['date'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        while queue and queue[0][0]<=ko:
            _,_,updates=heapq.heappop(queue)
            for team,stats,t in updates: states[str(team)].add(t,stats)
        fid='understat:'+str(g['fid'])
        if fid in wanted:
            lp=priors.get(g['league'])
            def profile(team):
                w,s=states[str(team)].snapshot(ko)
                if not lp or w<MIN_PROFILE_WEIGHT: return None,w
                return {k:(s.get(k,0.0)+PRIOR_MATCHES*lp[k])/(w+PRIOR_MATCHES) for k in lp},w
            h,hw=profile(g['h_id']); a,aw=profile(g['a_id'])
            out[fid]={'valid':bool(h and a),'home':h,'away':a,'home_weight':hw,'away_weight':aw}
        updates=[]; hs=side_stats(g,'h'); as_=side_stats(g,'a'); release=ko+timedelta(hours=3)
        if hs: updates.append((str(g['h_id']),hs,release))
        if as_: updates.append((str(g['a_id']),as_,release))
        if updates: seq+=1; heapq.heappush(queue,(release,seq,updates))
    return out,{'available_n':sum(1 for x in out.values() if x['valid']),'total_n':len(wanted),'half_life_days':HALF_LIFE,'prior_matches':PRIOR_MATCHES,'minimum_profile_weight':MIN_PROFILE_WEIGHT,'same_kickoff_isolation':'kickoff+3h release queue'}

def valid_base(p): return len(p)==3 and all(math.isfinite(x) and 0<x<1 for x in p) and abs(sum(p)-1.0)<=1e-8

def oriented_feature_vector(r,proc):
    p=pvec(r)
    if not valid_base(p): return None
    if abs(p[0]-p[2])<=1e-15: return None
    q=proc.get(r['fixture_id'])
    if not q or not q['valid']: return None
    fav=0 if p[0]>p[2] else 2; weak=2 if fav==0 else 0
    favprof=q['home'] if fav==0 else q['away']; weakprof=q['away'] if fav==0 else q['home']
    ent=-sum(x*math.log(max(x,1e-15)) for x in p)
    total=float(r['fusion']['mean_home'])+float(r['fusion']['mean_away'])
    if not math.isfinite(total) or total<=0: return None
    cold=r['cold_start_bucket']
    vals=[abs(p[0]-p[2]),p[1],ent,total,0.0 if r['fallback_exact_v1'] else 1.0,1.0 if cold=='sparse' else 0.0,1.0 if cold=='zero' else 0.0,favprof['npxg_for']-weakprof['npxg_for'],weakprof['npxg_against']-favprof['npxg_against'],favprof['npshots_for']-weakprof['npshots_for'],weakprof['npshots_against']-favprof['npshots_against'],favprof['npxg_per_shot']-weakprof['npxg_per_shot'],favprof['open_share']-weakprof['open_share'],favprof['set_share']-weakprof['set_share']]
    if any(not math.isfinite(v) for v in vals): return None
    return fav,weak,vals

def solve(A,b):
    n=len(b); M=[list(A[i])+[b[i]] for i in range(n)]
    for i in range(n):
        piv=max(range(i,n),key=lambda r:abs(M[r][i]))
        if abs(M[piv][i])<1e-10: raise ResearchError('singular Hessian')
        M[i],M[piv]=M[piv],M[i]; z=M[i][i]
        for j in range(i,n+1): M[i][j]/=z
        for r in range(n):
            if r==i: continue
            f=M[r][i]
            if f:
                for j in range(i,n+1): M[r][j]-=f*M[i][j]
    return [M[i][n] for i in range(n)]

class Model:
    def __init__(self,means,sds,cols,beta,lam): self.means=means;self.sds=sds;self.cols=cols;self.beta=beta;self.lam=lam
    def transform(self,v): return [(v[j]-self.means[j])/self.sds[j] for j in self.cols]

def fit(rows,proc):
    raw=[]
    for r in rows:
        if r['fallback_exact_v1']: continue
        o=oriented_feature_vector(r,proc)
        if not o: continue
        fav,weak,v=o; y=result_idx(r)
        if y==weak: continue
        raw.append((r,fav,weak,v,1 if y==fav else 0))
    if len(raw)<500: raise ResearchError('insufficient research fit rows')
    dim=len(raw[0][3]); means=[sum(x[3][j] for x in raw)/len(raw) for j in range(dim)]; sds=[]; cols=[]
    for j in range(dim):
        sd=math.sqrt(sum((x[3][j]-means[j])**2 for x in raw)/len(raw)); sds.append(sd if sd>1e-8 else 1.0)
        if sd>1e-8: cols.append(j)
    d=len(cols); lam=4.0*d
    g=[0.0]*d; H=[[0.0]*d for _ in range(d)]
    for r,fav,weak,v,y in raw:
        x=[(v[j]-means[j])/sds[j] for j in cols]; p=pvec(r); den=p[fav]+p[1]; q=p[fav]/den
        err=q-y; w=q*(1-q)
        for aa in range(d):
            g[aa]+=x[aa]*err
            for bb in range(d): H[aa][bb]+=w*x[aa]*x[bb]
    A=[row[:] for row in H]
    for i in range(d): A[i][i]+=lam
    beta=[-z for z in solve(A,g)]
    return Model(means,sds,cols,beta,lam),{'fit_n':len(raw),'feature_dim_raw':dim,'feature_dim_active':d,'lambda':lam,'lambda_rule':'4.0 * active_feature_count','optimizer':'single_exact_offset_newton_step'}

def predict(model,r,proc):
    base=pvec(r)
    if r['fallback_exact_v1']: return base
    o=oriented_feature_vector(r,proc)
    if not o: return base
    fav,weak,v=o; x=model.transform(v)
    off=math.log(max(base[fav],1e-15)/max(base[1],1e-15)); z=off+sum(aa*bb for aa,bb in zip(model.beta,x))
    sig=1.0/(1.0+math.exp(-max(-40.0,min(40.0,z))))
    rem=1.0-base[weak]; out=[0.0,0.0,0.0]; out[weak]=base[weak]; out[fav]=rem*sig; out[1]=rem-out[fav]
    if any(not math.isfinite(x) or x<=0 or x>=1 for x in out) or abs(sum(out)-1)>1e-10: return base
    return out

def cond_ll(rows,predfn,kind):
    vals=[]
    for r in rows:
        y=result_idx(r); p=pvec(r)
        if kind=='draw' and y!=1: continue
        if kind=='weak':
            if abs(p[0]-p[2])<=1e-15: continue
            weak=0 if p[0]<p[2] else 2
            if y!=weak: continue
        vals.append(-math.log(max(predfn(r)[y],1e-15)))
    return sum(vals)/len(vals),len(vals)

def entropy_bucket(r):
    e=-sum(x*math.log(max(x,1e-15)) for x in pvec(r))
    return 'low' if e<.9 else 'mid' if e<1.03 else 'high'

def group_defs(r):
    p=pvec(r); gap=abs(p[0]-p[2]); mx=max(p); total=r['fusion']['mean_home']+r['fusion']['mean_away']
    return {'competition_season':f"{r['league']}|{r['season']}",'strength_gap':'0-.10' if gap<.10 else '.10-.20' if gap<.20 else '.20-.35' if gap<.35 else '>=.35','max_probability':'<.40' if mx<.40 else '.40-.45' if mx<.45 else '.45-.55' if mx<.55 else '.55-.70' if mx<.70 else '>=.70','predicted_total_goals':'<2.0' if total<2 else '2.0-2.5' if total<2.5 else '2.5-3.0' if total<3 else '>=3.0','cold_start':r['cold_start_bucket'],'xg_coverage':'fallback' if r['fallback_exact_v1'] else 'active','uncertainty':entropy_bucket(r)}

def evaluate(model,rows,proc,fold_map):
    pred=lambda r:predict(model,r,proc); base=lambda r:pvec(r)
    cm=metrics(rows,pred); bm=metrics(rows,base)
    folds=[]; non=0
    for k in range(8):
        rs=[r for r in rows if fold_map[r['fixture_id']]==k]; aa=metrics(rs,pred); bb=metrics(rs,base); gain=bb['logloss']-aa['logloss']; ok=gain>=-TOL; non+=ok; folds.append({'fold':k,'n':len(rs),'gain':gain,'nondegrade':bool(ok)})
    allgroups=defaultdict(list)
    for r in rows:
        for dim,key in group_defs(r).items(): allgroups[(dim,key)].append(r)
    groups={}; worst=-1e9; group_ok=True; comp_ok=True
    for (dim,key),rs in sorted(allgroups.items()):
        if len(rs)<100: continue
        aa=metrics(rs,pred); bb=metrics(rs,base); deg=aa['logloss']-bb['logloss']; worst=max(worst,deg); group_ok &= deg<=GROUP_MAX+TOL
        if dim=='competition_season': comp_ok &= deg<=GROUP_MAX+TOL
        groups[f'{dim}|{key}']={'n':len(rs),'degradation':deg}
    dll,dn=cond_ll(rows,pred,'draw'); bdll,_=cond_ll(rows,base,'draw'); wll,wn=cond_ll(rows,pred,'weak'); bwll,_=cond_ll(rows,base,'weak')
    max_weak_delta=0.0; fallback_exact=True
    for r in rows:
        bb=base(r); cc=pred(r)
        if r['fallback_exact_v1'] and canon(bb)!=canon(cc): fallback_exact=False
        if abs(bb[0]-bb[2])>1e-15:
            weak=0 if bb[0]<bb[2] else 2; max_weak_delta=max(max_weak_delta,abs(cc[weak]-bb[weak]))
    gates={'pooled_logloss_gain':bm['logloss']-cm['logloss'],'pooled_logloss_gain_min':bm['logloss']-cm['logloss']>=LL_GAIN_MIN,'brier_delta':cm['brier']-bm['brier'],'brier_not_worse':cm['brier']<=bm['brier']+TOL,'rps_delta':cm['rps']-bm['rps'],'rps_not_worse':cm['rps']<=bm['rps']+TOL,'top1_delta':cm['top1']-bm['top1'],'top1_gate':cm['top1']-bm['top1']>=TOP1_MIN,'fold_nondegrade_n':non,'fold_gate':non>=6,'competition_season_gate':bool(comp_ok),'group_gate':bool(group_ok),'worst_group_degradation':worst,'draw_conditional_degradation':dll-bdll,'draw_gate':dll-bdll<=DRAW_MAX+TOL,'weak_team_win_conditional_degradation':wll-bwll,'weak_gate':wll-bwll<=WEAK_MAX+TOL,'weak_side_probability_max_abs_delta':max_weak_delta,'weak_probability_invariant':max_weak_delta==0.0,'fallback_exact_formal_v2':fallback_exact}
    gates['all_pass']=all(gates[k] for k in ['pooled_logloss_gain_min','brier_not_worse','rps_not_worse','top1_gate','fold_gate','competition_season_gate','group_gate','draw_gate','weak_gate','weak_probability_invariant','fallback_exact_formal_v2'])
    return {'baseline':bm,'candidate':cm,'gates':gates,'folds':folds,'groups':groups,'conditional_n':{'draw':dn,'weak_team_win':wn}}

def main():
    ap=argparse.ArgumentParser()
    for name in ['contract','v2-engine','xg-engine','v1-engine','v1-result','db','xg-identity','parent-predictions','out']: ap.add_argument('--'+name,type=pathlib.Path,required=True)
    a=ap.parse_args(); c=json.loads(a.contract.read_text()); out=a.out; out.mkdir(parents=True,exist_ok=True)
    if c['status']!='FROZEN_BEFORE_RESCORING' or c['candidate']['id']!='V3-USR-1': raise ResearchError('contract mismatch')
    if sha_file(a.db)!=c['frozen_data_identity']['old_understat']['database_sha256']: raise ResearchError('db SHA mismatch')
    if sha_file(a.v2_engine)!=c['frozen_data_identity']['accepted_v2_research']['engine_sha256']: raise ResearchError('V2 SHA mismatch')
    xg=loadmod('usr_xg',a.xg_engine); v2=loadmod('usr_v2',a.v2_engine)
    raw,fold_map,fold_rows,parent_check,ctx=v2.build_old_rows(xg,a.v1_engine,a.v1_result,a.db,a.xg_identity,a.parent_predictions)
    weighted=v2.with_weight(xg,raw,.75)
    fitrows=[r for r in weighted if r['season'] in (2018,2019)]; val=[r for r in weighted if r['season'] in (2020,2021,2022)]
    if len(fitrows)!=3551 or len(val)!=5478: raise ResearchError('split drift')
    proc,receipt=process_features(a.db,weighted); write_json(out/'process_feature_receipt.json',receipt)
    model,fitmeta=fit(fitrows,proc); write_json(out/'fit_receipt.json',fitmeta)
    dev={'baseline':metrics(fitrows,lambda r:pvec(r)),'candidate':metrics(fitrows,lambda r:predict(model,r,proc))}; write_json(out/'post_view_fit_metrics.json',dev)
    res=evaluate(model,val,proc,fold_map); write_json(out/'post_view_research_validation.json',res)
    status=PASS if res['gates']['all_pass'] else REJECT
    final={'schema_version':'football3-historical-fusion-v3-upset-safe-final-v1','status':status,'research_only':True,'fresh_independent_confirmation_available':False,'promotion_allowed':False,'candidate_id':'V3-USR-1','fit_seasons':[2018,2019],'research_validation_seasons':[2020,2021,2022],'fit_n':len(fitrows),'research_validation_n':len(val),'fit_meta':fitmeta,'post_view_fit_metrics':dev,'research_validation':res,'formal_v2_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'odds_used':False,'provider_or_secret_used':False,'live_scores_used':False,'prospective_queue':False}
    write_json(out/'final_status.json',final)
    slug=f"{status}__llgain_{res['gates']['pooled_logloss_gain']:.6f}__weak_{res['gates']['weak_team_win_conditional_degradation']:.6f}__draw_{res['gates']['draw_conditional_degradation']:.6f}__folds_{res['gates']['fold_nondegrade_n']}"
    (out/'artifact_slug.txt').write_text(slug+'\n'); print(json.dumps(final,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
