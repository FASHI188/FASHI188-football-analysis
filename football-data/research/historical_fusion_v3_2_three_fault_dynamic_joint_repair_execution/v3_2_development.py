from __future__ import annotations
import argparse, json, math, pathlib, sqlite3, heapq, hashlib, importlib.util, sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import stdev
from typing import Any

EPS=1e-15
TOL=1e-12
LEAGUES=('Bundesliga','EPL','La liga','Ligue 1','Serie A')

class V32Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

def sha_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n')

def result_idx(r):
    return 0 if int(r['home_goals'])>int(r['away_goals']) else 1 if int(r['home_goals'])==int(r['away_goals']) else 2

def one_contrib(p,y):
    q=max(float(p[y]),EPS)
    ll=-math.log(q)
    br=sum((float(p[i])-(1.0 if i==y else 0.0))**2 for i in range(3))
    c1=float(p[0]); c2=float(p[0])+float(p[1])
    t1=1.0 if y==0 else 0.0
    t2=1.0 if y<=1 else 0.0
    rps=((c1-t1)**2+(c2-t2)**2)/2.0
    top=float(max(range(3),key=lambda i:(float(p[i]),-i))==y)
    return ll,br,rps,top

def metrics(rows,pmap):
    vals=[one_contrib(pmap[r['fixture_id']],result_idx(r)) for r in rows]
    n=len(vals)
    if not n: return None
    return {'n':n,'logloss':sum(x[0] for x in vals)/n,'brier':sum(x[1] for x in vals)/n,
            'rps':sum(x[2] for x in vals)/n,'top1':sum(x[3] for x in vals)/n}

def conditional_ll(rows,pmap,base_map,kind,weights=None):
    vals=[]
    for r in rows:
        fid=r['fixture_id']; y=result_idx(r); b=base_map[fid]
        if kind=='draw':
            if y!=1: continue
        elif kind=='weak':
            if abs(b[0]-b[2])<=1e-15: continue
            weak=0 if b[0]<b[2] else 2
            if y!=weak: continue
        elif kind=='parity':
            if not weights or weights[fid]['parity']<0.5: continue
        elif kind=='favourite':
            if not weights or weights[fid]['fragility']<0.5: continue
        else: raise ValueError(kind)
        vals.append(-math.log(max(float(pmap[fid][y]),EPS)))
    return (sum(vals)/len(vals),len(vals)) if vals else (None,0)

def outcome_eval(rows,pmap,base_map,contract,fold_map=None,weights=None,require_folds=False):
    cm=metrics(rows,pmap); bm=metrics(rows,base_map); g=contract['hard_gates']['one_x_two']
    dll,dn=conditional_ll(rows,pmap,base_map,'draw'); bdll,_=conditional_ll(rows,base_map,base_map,'draw')
    wll,wn=conditional_ll(rows,pmap,base_map,'weak'); bwll,_=conditional_ll(rows,base_map,base_map,'weak')
    pll,pn=conditional_ll(rows,pmap,base_map,'parity',weights); bpll,_=conditional_ll(rows,base_map,base_map,'parity',weights)
    fll,fn=conditional_ll(rows,pmap,base_map,'favourite',weights); bfll,_=conditional_ll(rows,base_map,base_map,'favourite',weights)
    groups=[]; worst=-1e9
    for (league,season),rs in sorted(_group(rows,lambda r:(r['league'],r['season'])).items()):
        if len(rs)<100: continue
        a=metrics(rs,pmap); b=metrics(rs,base_map); d=a['logloss']-b['logloss']; worst=max(worst,d)
        groups.append({'league':league,'season':season,'n':len(rs),'logloss_degradation':d})
    folds=[]; non=0
    if fold_map:
        for k in range(8):
            rs=[r for r in rows if fold_map.get(r['fixture_id'])==k]
            if not rs: continue
            a=metrics(rs,pmap); b=metrics(rs,base_map); d=a['logloss']-b['logloss']; ok=d<=TOL
            non+=int(ok); folds.append({'fold':k,'n':len(rs),'logloss_degradation':d,'nondegrade':ok})
    gates={
      'coverage':len(pmap)==len(rows),
      'logloss_delta':cm['logloss']-bm['logloss'],
      'brier_delta':cm['brier']-bm['brier'],
      'rps_delta':cm['rps']-bm['rps'],
      'top1_delta':cm['top1']-bm['top1'],
      'draw_conditional_logloss_degradation':None if dll is None else dll-bdll,
      'weak_team_win_conditional_logloss_degradation':None if wll is None else wll-bwll,
      'parity_regime_logloss_degradation':None if pll is None else pll-bpll,
      'favourite_regime_logloss_degradation':None if fll is None else fll-bfll,
      'worst_eligible_league_season_logloss_degradation':worst,
      'fold_nondegrade_n':non,
    }
    checks={
      'coverage_gate':gates['coverage'],
      'logloss_gate':gates['logloss_delta']<=g['logloss_delta_max']+TOL,
      'brier_gate':gates['brier_delta']<=g['brier_delta_max']+TOL,
      'rps_gate':gates['rps_delta']<=g['rps_delta_max']+TOL,
      'top1_gate':gates['top1_delta']>=g['top1_delta_min']-TOL,
      'draw_gate':gates['draw_conditional_logloss_degradation'] is not None and gates['draw_conditional_logloss_degradation']<=g['draw_conditional_logloss_max_degradation']+TOL,
      'weak_gate':gates['weak_team_win_conditional_logloss_degradation'] is not None and gates['weak_team_win_conditional_logloss_degradation']<=g['actual_weak_team_win_conditional_logloss_max_degradation']+TOL,
      'parity_gate':gates['parity_regime_logloss_degradation'] is not None and gates['parity_regime_logloss_degradation']<=g['parity_regime_logloss_max_degradation']+TOL,
      'favourite_gate':gates['favourite_regime_logloss_degradation'] is not None and gates['favourite_regime_logloss_degradation']<=g['favourite_regime_logloss_max_degradation']+TOL,
      'group_gate':worst<=g['worst_eligible_league_season_logloss_max_degradation']+TOL,
      'fold_gate':(not require_folds) or non>=g['fold_nondegrade_min'],
    }
    checks['all_pass']=all(checks.values())
    return {'baseline':bm,'candidate':cm,'gates':gates,'checks':checks,'folds':folds,'groups':groups,
            'conditional_n':{'draw':dn,'weak':wn,'parity':pn,'favourite':fn}}

def _group(rows,keyfn):
    d=defaultdict(list)
    for r in rows:d[keyfn(r)].append(r)
    return d

@dataclass
class ScalarState:
    mean: float
    var: float
    n: int=0

@dataclass
class TeamDynamic:
    attack: ScalarState
    defence: ScalarState
    process: ScalarState

def _shrink_update(core,state,obs,prior_mean,obs_var,proc_var,half):
    shrink=1.0-math.exp(-math.log(2.0)/float(half))
    pm=(1.0-shrink)*state.mean+shrink*prior_mean
    mean,var=core.local_level_update(pm,state.var,obs,obs_var,proc_var)
    return ScalarState(mean,var,state.n+1)

def load_process_games(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row
    qs=','.join('?' for _ in LEAGUES)
    games=[dict(r) for r in con.execute(f"select id,fid,date,league,season,h_id,a_id from general_game_stats where league in ({qs}) and season between 2014 and 2023 order by date,id",LEAGUES)]
    ev={}
    sql=f"""select e.match_id,e.h_a,
      sum(case when e.situation!='Penalty' then e.xG else 0 end) npxg,
      sum(case when e.situation!='Penalty' then 1 else 0 end) npshots,
      sum(case when e.situation='OpenPlay' then 1 else 0 end) open_n,
      sum(case when e.situation in ('SetPiece','FromCorner','DirectFreekick') then 1 else 0 end) set_n
      from game_events e join general_game_stats g on g.id=e.match_id
      where g.league in ({qs}) and g.season between 2014 and 2023
      group by e.match_id,e.h_a"""
    for r in con.execute(sql,LEAGUES): ev[(str(r['match_id']),str(r['h_a']))]=dict(r)
    con.close()
    def side(g,ha):
        x=ev.get((str(g['id']),ha))
        if not x or float(x['npshots'] or 0)<=0:return None
        shots=float(x['npshots']); npxg=float(x['npxg'] or 0.0)
        return {'npxg':npxg,'npshots':shots,'per_shot':npxg/shots,
                'open_share':float(x['open_n'] or 0)/shots,'set_share':float(x['set_n'] or 0)/shots}
    rows=[]
    pools=defaultdict(lambda:defaultdict(list))
    for g in games:
        h=side(g,'h'); a=side(g,'a')
        rec={**g,'home_obs':h,'away_obs':a}
        rows.append(rec)
        if int(g['season'])<=2017 and h and a:
            pools[g['league']]['attack'].extend([h['npxg'],a['npxg']])
            pools[g['league']]['process'].extend([h['per_shot'],a['per_shot']])
            pools[g['league']]['ha'].append(h['npxg']-a['npxg'])
    pri={}
    for l,d in pools.items():
        pri[l]={'attack':sum(d['attack'])/len(d['attack']),'defence':sum(d['attack'])/len(d['attack']),
                'process':sum(d['process'])/len(d['process']),'ha':sum(d['ha'])/len(d['ha'])}
    return rows,pri

def simulate_state(core,games,pri,params,wanted):
    q=float(params['process_variance_scale']); rv=float(params['observation_variance_scale']); half=float(params['lag_half_life_matches'])
    teams={}; ha={}; out={}; queue=[]; seq=0; nll=[]
    def mkteam(l):
        p=pri[l]
        return TeamDynamic(ScalarState(p['attack'],rv,0),ScalarState(p['defence'],rv,0),ScalarState(p['process'],rv,0))
    def getteam(t,l):
        if str(t) not in teams: teams[str(t)]=mkteam(l)
        return teams[str(t)]
    def getha(l):
        if l not in ha: ha[l]=ScalarState(pri[l]['ha'],rv,0)
        return ha[l]
    for g in games:
        ko=datetime.strptime(g['date'],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        while queue and queue[0][0]<=ko:
            _,_,u=heapq.heappop(queue)
            l=u['league']; h=getteam(u['h_id'],l); a=getteam(u['a_id'],l); p=pri[l]
            ho=u['home_obs']; ao=u['away_obs']
            if ho and ao:
                h.attack=_shrink_update(core,h.attack,ho['npxg'],p['attack'],rv,q,half)
                h.defence=_shrink_update(core,h.defence,ao['npxg'],p['defence'],rv,q,half)
                h.process=_shrink_update(core,h.process,ho['per_shot'],p['process'],rv,q,half)
                a.attack=_shrink_update(core,a.attack,ao['npxg'],p['attack'],rv,q,half)
                a.defence=_shrink_update(core,a.defence,ho['npxg'],p['defence'],rv,q,half)
                a.process=_shrink_update(core,a.process,ao['per_shot'],p['process'],rv,q,half)
                hs=getha(l); hs2=_shrink_update(core,hs,ho['npxg']-ao['npxg'],p['ha'],rv,q,half); ha[l]=hs2
        fid='understat:'+str(g['fid'])
        if fid in wanted:
            l=g['league']; h=getteam(g['h_id'],l); a=getteam(g['a_id'],l); hs=getha(l)
            valid=h.attack.n>0 and a.attack.n>0 and h.defence.n>0 and a.defence.n>0 and h.process.n>0 and a.process.n>0
            gap=(h.attack.mean-a.defence.mean)-(a.attack.mean-h.defence.mean)
            gap_var=h.attack.var+a.defence.var+a.attack.var+h.defence.var
            snap={'valid':valid,'gap':gap,'gap_sd':math.sqrt(max(gap_var,1e-12)),
                  'home_attack':h.attack.mean,'away_attack':a.attack.mean,'home_defence':h.defence.mean,'away_defence':a.defence.mean,
                  'home_process':h.process.mean,'away_process':a.process.mean,'home_advantage':hs.mean,
                  'uncertainty':math.sqrt(max(gap_var,1e-12)),
                  'counts':{'home':min(h.attack.n,h.defence.n,h.process.n),'away':min(a.attack.n,a.defence.n,a.process.n)}}
            out[fid]=snap
            if int(g['season'])==2019 and valid and g['home_obs'] and g['away_obs']:
                for obs,mean,var in [(g['home_obs']['npxg'],h.attack.mean,h.attack.var+rv),
                                     (g['away_obs']['npxg'],a.attack.mean,a.attack.var+rv),
                                     (g['home_obs']['per_shot'],h.process.mean,h.process.var+rv),
                                     (g['away_obs']['per_shot'],a.process.mean,a.process.var+rv)]:
                    vv=max(var,1e-12); nll.append(0.5*(math.log(2*math.pi*vv)+(obs-mean)**2/vv))
        if g['home_obs'] and g['away_obs']:
            seq+=1; heapq.heappush(queue,(ko+timedelta(hours=3),seq,g))
    return out,{'predictive_gaussian_logscore_2019':sum(nll)/len(nll) if nll else None,'score_n':len(nll),
                'available_n':sum(1 for v in out.values() if v['valid']),'wanted_n':len(wanted)}

def select_state(core,contract,games,pri,wanted):
    c=contract['shared_dynamic_state_backbone']['inner_grid_2019_only']; board=[]
    for q in c['process_variance_scale']:
      for rv in c['observation_variance_scale']:
       for half in c['lag_half_life_matches']:
        p={'process_variance_scale':q,'observation_variance_scale':rv,'lag_half_life_matches':half}
        snaps,rec=simulate_state(core,games,pri,p,wanted); board.append({'params':p,**rec})
    board.sort(key=lambda x:(x['predictive_gaussian_logscore_2019'],x['params']['process_variance_scale'],x['params']['observation_variance_scale'],x['params']['lag_half_life_matches']))
    win=board[0]; snaps,rec=simulate_state(core,games,pri,win['params'],wanted)
    return win['params'],snaps,{'selected':win,'board':board,'selection_rule':'minimum 2019 PIT Gaussian predictive log score; deterministic tuple tie-break'}

def entropy(p): return -sum(float(x)*math.log(max(float(x),EPS)) for x in p)
def margin(p):
    s=sorted((float(x) for x in p),reverse=True); return s[0]-s[1]

def features(base,r,snap,core):
    if not snap or not snap['valid']: return None
    z=snap['gap']/max(snap['gap_sd'],1e-9)
    total=float(r['fusion']['mean_home'])+float(r['fusion']['mean_away'])
    ent=entropy(base); pg=float(base[0])-float(base[2]); mg=margin(base)
    atk=snap['home_attack']-snap['away_attack']
    de=snap['away_defence']-snap['home_defence']
    pr=snap['home_process']-snap['away_process']
    common=[z,pg,total,ent,float(base[1]),mg,atk,de,pr,snap['home_advantage'],snap['uncertainty']]
    parity=[z,total,ent,float(base[1]),abs(pg),mg,atk,de,pr,snap['home_advantage'],snap['uncertainty'],z*total]
    frag=[z,pg,total,ent,mg,atk,de,pr,snap['home_advantage'],snap['uncertainty'],abs(z),abs(pg)]
    gate=[ent,mg,float(base[1]),abs(pg),abs(z),snap['uncertainty'],total,core.parity_weight(snap['gap'],snap['gap_sd']),core.fragility_weight(snap['gap'],snap['gap_sd'])]
    return {'common':common,'parity':parity,'fragility':frag,'gate':gate,
            'w_parity':core.parity_weight(snap['gap'],snap['gap_sd']),'w_fragility':core.fragility_weight(snap['gap'],snap['gap_sd'])}

def standardizer(raw,key):
    vals=[x[key] for x in raw]; d=len(vals[0]); means=[]; sds=[]; cols=[]
    for j in range(d):
        m=sum(v[j] for v in vals)/len(vals); sd=math.sqrt(sum((v[j]-m)**2 for v in vals)/len(vals))
        means.append(m); sds.append(sd if sd>1e-8 else 1.0)
        if sd>1e-8: cols.append(j)
    return means,sds,cols

def transform(v,means,sds,cols): return [(v[j]-means[j])/sds[j] for j in cols]

def solve(A,b):
    n=len(b); M=[list(map(float,A[i]))+[float(b[i])] for i in range(n)]
    for i in range(n):
        piv=max(range(i,n),key=lambda r:abs(M[r][i]))
        if abs(M[piv][i])<1e-12: raise V32Error('singular system')
        M[i],M[piv]=M[piv],M[i]; z=M[i][i]
        for j in range(i,n+1): M[i][j]/=z
        for r in range(n):
            if r==i: continue
            f=M[r][i]
            if f:
                for j in range(i,n+1): M[r][j]-=f*M[i][j]
    return [M[i][n] for i in range(n)]

@dataclass
class MNLModel:
    means:list; sds:list; cols:list; beta0:list; beta2:list; lam:float
    def residual(self,v):
        x=transform(v,self.means,self.sds,self.cols)
        a=sum(b*z for b,z in zip(self.beta0,x)); c=sum(b*z for b,z in zip(self.beta2,x))
        return [a,0.0,c]

def fit_mnl(raw,key,multiplier,weight_key):
    if len(raw)<300: raise V32Error('insufficient MNL rows')
    means,sds,cols=standardizer(raw,key); d=len(cols)
    if d<1: raise V32Error('zero active MNL columns')
    lam=float(multiplier)*d; b0=[0.0]*d; b2=[0.0]*d
    for _ in range(7):
        g0=[lam*x for x in b0]; g2=[lam*x for x in b2]
        H00=[[0.0]*d for _ in range(d)]; H22=[[0.0]*d for _ in range(d)]; H02=[[0.0]*d for _ in range(d)]
        for i in range(d): H00[i][i]+=lam; H22[i][i]+=lam
        for rec in raw:
            x=transform(rec[key],means,sds,cols); base=rec['base']; y=rec['y']; w=float(rec[weight_key])
            e0=math.log(max(base[0],EPS)/max(base[1],EPS))+sum(a*z for a,z in zip(b0,x))
            e2=math.log(max(base[2],EPS)/max(base[1],EPS))+sum(a*z for a,z in zip(b2,x))
            mx=max(e0,0.0,e2); a=math.exp(e0-mx); d0=math.exp(-mx); c=math.exp(e2-mx); den=a+d0+c; q0=a/den; q2=c/den
            for j in range(d):
                g0[j]+=w*(q0-(1.0 if y==0 else 0.0))*x[j]
                g2[j]+=w*(q2-(1.0 if y==2 else 0.0))*x[j]
                for k in range(d):
                    H00[j][k]+=w*q0*(1-q0)*x[j]*x[k]
                    H22[j][k]+=w*q2*(1-q2)*x[j]*x[k]
                    H02[j][k]+=-w*q0*q2*x[j]*x[k]
        H=[H00[i]+H02[i] for i in range(d)]+[H02[j][:]+H22[j] for j in range(d)]
        for i in range(d,2*d):
            row=i-d
            H[i][:d]=[H02[k][row] for k in range(d)]
        step=solve(H,g0+g2)
        mxstep=max(abs(x) for x in step)
        scale=1.0 if mxstep<=2.0 else 2.0/mxstep
        b0=[x-scale*s for x,s in zip(b0,step[:d])]; b2=[x-scale*s for x,s in zip(b2,step[d:])]
        if max(abs(scale*s) for s in step)<1e-7: break
    return MNLModel(means,sds,cols,b0,b2,lam)

@dataclass
class GateModel:
    means:list; sds:list; cols:list; beta:list; intercept:float; lam:float
    def logit(self,v):
        x=transform(v,self.means,self.sds,self.cols); return self.intercept+sum(a*z for a,z in zip(self.beta,x))

def fit_gate(raw,multiplier):
    if len(raw)<300: raise V32Error('insufficient gate rows')
    means,sds,cols=standardizer(raw,'gate'); d=len(cols); lam=float(multiplier)*d
    prev=min(max(sum(r['wrong'] for r in raw)/len(raw),1e-4),1-1e-4); ic=math.log(prev/(1-prev)); beta=[0.0]*d
    for _ in range(8):
        g=[0.0]+[lam*x for x in beta]; H=[[0.0]*(d+1) for _ in range(d+1)]
        for j in range(1,d+1): H[j][j]+=lam
        for rec in raw:
            x=[1.0]+transform(rec['gate'],means,sds,cols); z=ic+sum(a*b for a,b in zip(beta,x[1:])); q=1/(1+math.exp(-max(-40,min(40,z))))
            er=q-rec['wrong']; w=q*(1-q)
            for j in range(d+1):
                g[j]+=er*x[j]
                for k in range(d+1): H[j][k]+=w*x[j]*x[k]
        step=solve(H,g); mx=max(abs(x) for x in step); scale=1 if mx<=2 else 2/mx
        ic-=scale*step[0]; beta=[b-scale*s for b,s in zip(beta,step[1:])]
        if max(abs(scale*s) for s in step)<1e-7: break
    return GateModel(means,sds,cols,beta,ic,lam)

def make_raw(rows,base_map,snaps,core):
    out=[]
    for r in rows:
        fid=r['fixture_id']; b=base_map.get(fid); f=features(b,r,snaps.get(fid),core) if b else None
        if not f: continue
        out.append({'fid':fid,'base':b,'y':result_idx(r),'parity':f['parity'],'fragility':f['fragility'],'gate':f['gate'],
                    'w_parity':f['w_parity'],'w_fragility':f['w_fragility'],'wrong':float(max(range(3),key=lambda i:(b[i],-i))!=result_idx(r))})
    return out

def frozen_baselines(v311,v31,usr,rows,proc):
    bys=_group(rows,lambda r:r['season']); pmap={}; mmat={}; receipts=[]
    r18=sorted(bys[2018],key=lambda r:(r['kickoff'],r['fixture_id']))
    burn=1000; pos=burn
    while pos<len(r18):
        end=min(len(r18),pos+250)
        while end<len(r18) and r18[end]['kickoff']==r18[end-1]['kickoff']: end+=1
        train=r18[:pos]; test=r18[pos:end]
        model,fitrec=usr.fit(train,proc)
        for r in test:pmap[r['fixture_id']]=v311.target_v31(v31,usr,model,r,proc)
        receipts.append({'season':2018,'train_n':len(train),'test_n':len(test),'fit_n':fitrec['fit_n'],'first_test':test[0]['kickoff'],'last_test':test[-1]['kickoff']})
        pos=end
    for s in (2019,2020,2021,2022,2023):
        train=[r for r in rows if 2018<=r['season']<s]; test=bys[s]; model,fitrec=usr.fit(train,proc)
        for r in test:pmap[r['fixture_id']]=v311.target_v31(v31,usr,model,r,proc)
        receipts.append({'season':s,'train_n':len(train),'test_n':len(test),'fit_n':fitrec['fit_n']})
    for r in rows:
        if r['fixture_id'] in pmap: mmat[r['fixture_id']]=v311.project_regions(v311.base_matrix(r),pmap[r['fixture_id']])
    return pmap,mmat,receipts

def model_bundle(train_rows,base_map,snaps,core,params):
    raw=make_raw(train_rows,base_map,snaps,core)
    pm=params['parity_lambda']; fm=params['fragility_lambda']; gm=params['gate_lambda']
    return {'parity':fit_mnl(raw,'parity',pm,'w_parity'),'fragility':fit_mnl(raw,'fragility',fm,'w_fragility'),'gate':fit_gate(raw,gm),'fit_n':len(raw)}

def predict_outcome(bundle,params,base,r,snap,core):
    f=features(base,r,snap,core)
    if not f:return list(base),{'fallback':True,'parity':0.0,'fragility':0.0,'gate':0.0}
    pr=bundle['parity'].residual(f['parity']); fr=bundle['fragility'].residual(f['fragility'])
    pr=[params['parity_scale']*x for x in pr]; fr=[params['fragility_scale']*x for x in fr]
    rawg=bundle['gate'].logit(f['gate']); gate=core.sigmoid(float(params['gate_repair_scale'])*rawg)
    p=core.combine_outcome_probs(base,pr,fr,gate,f['w_parity'],f['w_fragility'],1.0,float(params['max_abs_delta']))
    return p,{'fallback':False,'parity':f['w_parity'],'fragility':f['w_fragility'],'gate':gate}

def prefit_heads(train_rows,base_map,snaps,core,contract):
    raw=make_raw(train_rows,base_map,snaps,core)
    pmods={}; fmods={}; gmods={}
    for x in contract['fault_heads']['parity_repair']['inner_grid_2019_only']['ridge_lambda_multiplier']: pmods[float(x)]=fit_mnl(raw,'parity',x,'w_parity')
    for x in contract['fault_heads']['favourite_fragility_repair']['inner_grid_2019_only']['ridge_lambda_multiplier']: fmods[float(x)]=fit_mnl(raw,'fragility',x,'w_fragility')
    for x in contract['fault_heads']['base_error_gate']['inner_grid_2019_only']['ridge_lambda_multiplier']: gmods[float(x)]=fit_gate(raw,x)
    return raw,pmods,fmods,gmods

def inner_outcome_selection(rows19,train18,base_map,snaps,core,contract):
    raw,pmods,fmods,gmods=prefit_heads(train18,base_map,snaps,core,contract)
    pc=contract['fault_heads']['parity_repair']['inner_grid_2019_only']; fc=contract['fault_heads']['favourite_fragility_repair']['inner_grid_2019_only']; gc=contract['fault_heads']['base_error_gate']['inner_grid_2019_only']
    recs=[]; valid=0
    for r in rows19:
        fid=r['fixture_id']; b=base_map[fid]; f=features(b,r,snaps.get(fid),core)
        y=result_idx(r); bc=one_contrib(b,y)
        weak=(0 if b[0]<b[2] else 2) if abs(b[0]-b[2])>1e-15 else None
        if f: valid+=1
        recs.append({'r':r,'fid':fid,'base':b,'y':y,'bc':bc,'f':f,
          'p':{} if not f else {float(lam):pmods[float(lam)].residual(f['parity']) for lam in pc['ridge_lambda_multiplier']},
          'fr':{} if not f else {float(lam):fmods[float(lam)].residual(f['fragility']) for lam in fc['ridge_lambda_multiplier']},
          'g':{} if not f else {float(lam):gmods[float(lam)].logit(f['gate']) for lam in gc['ridge_lambda_multiplier']},
          'weak':weak})
    n=len(recs)
    bll=sum(z['bc'][0] for z in recs)/n; bbr=sum(z['bc'][1] for z in recs)/n; brps=sum(z['bc'][2] for z in recs)/n; btop=sum(z['bc'][3] for z in recs)/n
    def bcond(pred):
        xs=[z['bc'][0] for z in recs if pred(z)]
        return sum(xs)/len(xs),len(xs)
    bdll,dn=bcond(lambda z:z['y']==1)
    bwll,wn=bcond(lambda z:z['weak'] is not None and z['y']==z['weak'])
    bpll,pn=bcond(lambda z:z['f'] is not None and z['f']['w_parity']>=0.5)
    bfll,fn=bcond(lambda z:z['f'] is not None and z['f']['w_fragility']>=0.5)
    bg={}
    for league,rs in _group(recs,lambda z:z['r']['league']).items():
        bg[league]=sum(z['bc'][0] for z in rs)/len(rs)
    gcfg=contract['hard_gates']['one_x_two']; board=[]; best=None
    for pl in pc['ridge_lambda_multiplier']:
      pl=float(pl)
      for ps in pc['residual_scale']:
       ps=float(ps)
       for fl in fc['ridge_lambda_multiplier']:
        fl=float(fl)
        for fs in fc['residual_scale']:
         fs=float(fs)
         for cap in fc['max_outcome_probability_abs_delta']:
          cap=float(cap)
          for gl in gc['ridge_lambda_multiplier']:
           gl=float(gl)
           for gs in gc['repair_scale']:
            gs=float(gs)
            ll=br=rps=top=0.0; dll=wll=pll=fll=0.0; gd=defaultdict(lambda:[0.0,0])
            for z in recs:
                b=z['base']; f=z['f']
                if f is None: q=b
                else:
                    pr=[ps*x for x in z['p'][pl]]; fr=[fs*x for x in z['fr'][fl]]
                    gate=core.sigmoid(gs*z['g'][gl])
                    q=core.combine_outcome_probs(b,pr,fr,gate,f['w_parity'],f['w_fragility'],1.0,cap)
                cc=one_contrib(q,z['y']); ll+=cc[0]; br+=cc[1]; rps+=cc[2]; top+=cc[3]
                if z['y']==1:dll+=cc[0]
                if z['weak'] is not None and z['y']==z['weak']:wll+=cc[0]
                if f is not None and f['w_parity']>=0.5:pll+=cc[0]
                if f is not None and f['w_fragility']>=0.5:fll+=cc[0]
                gd[z['r']['league']][0]+=cc[0]; gd[z['r']['league']][1]+=1
            cm={'n':n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1':top/n}
            deltas={'logloss_delta':cm['logloss']-bll,'brier_delta':cm['brier']-bbr,'rps_delta':cm['rps']-brps,'top1_delta':cm['top1']-btop,
                    'draw_conditional_logloss_degradation':dll/dn-bdll,'weak_team_win_conditional_logloss_degradation':wll/wn-bwll,
                    'parity_regime_logloss_degradation':pll/pn-bpll,'favourite_regime_logloss_degradation':fll/fn-bfll,
                    'worst_eligible_league_season_logloss_degradation':max(gd[k][0]/gd[k][1]-bg[k] for k in gd)}
            checks={'logloss_gate':deltas['logloss_delta']<=gcfg['logloss_delta_max']+TOL,'brier_gate':deltas['brier_delta']<=gcfg['brier_delta_max']+TOL,
                    'rps_gate':deltas['rps_delta']<=gcfg['rps_delta_max']+TOL,'top1_gate':deltas['top1_delta']>=gcfg['top1_delta_min']-TOL,
                    'draw_gate':deltas['draw_conditional_logloss_degradation']<=gcfg['draw_conditional_logloss_max_degradation']+TOL,
                    'weak_gate':deltas['weak_team_win_conditional_logloss_degradation']<=gcfg['actual_weak_team_win_conditional_logloss_max_degradation']+TOL,
                    'parity_gate':deltas['parity_regime_logloss_degradation']<=gcfg['parity_regime_logloss_max_degradation']+TOL,
                    'favourite_gate':deltas['favourite_regime_logloss_degradation']<=gcfg['favourite_regime_logloss_max_degradation']+TOL,
                    'group_gate':deltas['worst_eligible_league_season_logloss_degradation']<=gcfg['worst_eligible_league_season_logloss_max_degradation']+TOL}
            checks['all_pass']=all(checks.values())
            par={'parity_lambda':pl,'parity_scale':ps,'fragility_lambda':fl,'fragility_scale':fs,'max_abs_delta':cap,'gate_lambda':gl,'gate_repair_scale':gs}
            rec={'params':par,'all_pass':checks['all_pass'],'candidate':cm,'gates':deltas,'checks':checks}
            board.append(rec)
            if rec['all_pass'] and (best is None or (cm['logloss'],json.dumps(par,sort_keys=True))<(best['candidate']['logloss'],json.dumps(best['params'],sort_keys=True))):best=rec
    ranked=sorted(board,key=lambda x:(not x['all_pass'],x['candidate']['logloss'],json.dumps(x['params'],sort_keys=True)))[:20]
    return None if best is None else best['params'],{'fit_n_2018_oos':len(raw),'valid_2019_state_n':valid,'selected':best,'board_size':len(board),'passing_n':sum(1 for x in board if x['all_pass']),'top20':ranked}

def binary_brier(rows,mmap,pfn,yfn):
    return sum((pfn(mmap[r['fixture_id']])-yfn(r))**2 for r in rows)/len(rows)

def total_metrics(rows,mmap):
    br=0.0; rps=0.0
    for r in rows:
        m=mmap[r['fixture_id']]; probs=[0.0]*8
        for h in range(15):
            for a in range(15): probs[min(h+a,7)]+=m[h][a]
        y=min(int(r['home_goals'])+int(r['away_goals']),7)
        br+=sum((probs[i]-(1.0 if i==y else 0.0))**2 for i in range(8))
        c=0.0; rr=0.0
        for i in range(7):
            c+=probs[i]; t=1.0 if y<=i else 0.0; rr+=(c-t)**2
        rps+=rr/7.0
    return {'brier':br/len(rows),'rps':rps/len(rows)}

def exact_ll(rows,mmap):
    s=0.0
    for r in rows:
        h=int(r['home_goals']); a=int(r['away_goals']); p=mmap[r['fixture_id']]
        q=p[h][a] if h<15 and a<15 else 0.0; s+=-math.log(max(q,EPS))
    return s/len(rows)

def top4_brier(rows,mmap):
    s=0.0
    for r in rows:
        m=mmap[r['fixture_id']]; cells=sorted(((m[h][a],h,a) for h in range(15) for a in range(15)),reverse=True)[:4]
        p=sum(x[0] for x in cells); hit=float(any((int(r['home_goals']),int(r['away_goals']))==(x[1],x[2]) for x in cells)); s+=(p-hit)**2
    return s/len(rows)

def score_eval(rows,mmap,bmap,core,contract,target_pmap=None):
    g=contract['hard_gates']['score']; cand={'exact_score_logloss':exact_ll(rows,mmap),'total_goals':total_metrics(rows,mmap)}
    base={'exact_score_logloss':exact_ll(rows,bmap),'total_goals':total_metrics(rows,bmap)}
    def regionp(m,pred): return sum(m[h][a] for h in range(15) for a in range(15) if pred(h,a))
    specs={
      'score_00':(lambda m:m[0][0],lambda r:float(int(r['home_goals'])==0 and int(r['away_goals'])==0)),
      'score_11':(lambda m:m[1][1],lambda r:float(int(r['home_goals'])==1 and int(r['away_goals'])==1)),
      'score_22':(lambda m:m[2][2],lambda r:float(int(r['home_goals'])==2 and int(r['away_goals'])==2)),
      'low_total_le2':(lambda m:regionp(m,lambda h,a:h+a<=2),lambda r:float(int(r['home_goals'])+int(r['away_goals'])<=2)),
      'high_total_ge5':(lambda m:regionp(m,lambda h,a:h+a>=5),lambda r:float(int(r['home_goals'])+int(r['away_goals'])>=5)),
      'very_high_total_ge7':(lambda m:regionp(m,lambda h,a:h+a>=7),lambda r:float(int(r['home_goals'])+int(r['away_goals'])>=7)),
    }
    deltas={'exact_score_logloss_delta':cand['exact_score_logloss']-base['exact_score_logloss'],
            'total_goals_brier_delta':cand['total_goals']['brier']-base['total_goals']['brier'],
            'total_goals_rps_delta':cand['total_goals']['rps']-base['total_goals']['rps']}
    for name,(pf,yf) in specs.items():
        cb=binary_brier(rows,mmap,pf,yf); bb=binary_brier(rows,bmap,pf,yf); cand[name]={'brier':cb}; base[name]={'brier':bb}; deltas[name+'_binary_brier_delta']=cb-bb
    cand['top4_cell_mass_brier']=top4_brier(rows,mmap); base['top4_cell_mass_brier']=top4_brier(rows,bmap); deltas['top4_cell_mass_calibration_brier_delta']=cand['top4_cell_mass_brier']-base['top4_cell_mass_brier']
    maxerr=0.0
    if target_pmap is not None:
        maxerr=max(max(abs(a-b) for a,b in zip(core.integrate(mmap[r['fixture_id']]),target_pmap[r['fixture_id']])) for r in rows)
    deltas['matrix_to_1x2_max_abs_error']=maxerr
    checks={
      'exact_score_logloss_gate':deltas['exact_score_logloss_delta']<=g['exact_score_logloss_delta_max']+TOL,
      'total_goals_brier_gate':deltas['total_goals_brier_delta']<=g['total_goals_brier_delta_max']+TOL,
      'total_goals_rps_gate':deltas['total_goals_rps_delta']<=g['total_goals_rps_delta_max']+TOL,
      'score_00_gate':deltas['score_00_binary_brier_delta']<=g['score_00_binary_brier_delta_max']+TOL,
      'score_11_gate':deltas['score_11_binary_brier_delta']<=g['score_11_binary_brier_delta_max']+TOL,
      'score_22_gate':deltas['score_22_binary_brier_delta']<=g['score_22_binary_brier_delta_max']+TOL,
      'low_total_gate':deltas['low_total_le2_binary_brier_delta']<=g['low_total_le2_binary_brier_delta_max']+TOL,
      'high_total_gate':deltas['high_total_ge5_binary_brier_delta']<=g['high_total_ge5_binary_brier_delta_max']+TOL,
      'very_high_total_gate':deltas['very_high_total_ge7_binary_brier_delta']<=g['very_high_total_ge7_binary_brier_delta_max']+TOL,
      'top4_gate':deltas['top4_cell_mass_calibration_brier_delta']<=g['top4_cell_mass_calibration_brier_delta_max']+TOL,
      'matrix_to_1x2_gate':deltas['matrix_to_1x2_max_abs_error']<=g['matrix_to_1x2_max_abs_error']+TOL,
    }
    checks['all_pass']=all(checks.values())
    return {'candidate':cand,'baseline':base,'deltas':deltas,'checks':checks}

def fit_surface_c(rows,bmap,core,lam_mult):
    d=8; lam=float(lam_mult)*d; beta=[0.0]*d
    basis=[[core.cell_basis(h,a) for a in range(15)] for h in range(15)]
    for _ in range(8):
        grad=[lam*x for x in beta]; diag=[lam]*d
        for r in rows:
            m=core.structured_surface(bmap[r['fixture_id']],beta)
            E=[0.0]*d; E2=[0.0]*d
            for h in range(15):
                for a in range(15):
                    ph=m[h][a]; f=basis[h][a]
                    for j in range(d):
                        E[j]+=ph*f[j]; E2[j]+=ph*f[j]*f[j]
            yh=min(int(r['home_goals']),14); ya=min(int(r['away_goals']),14); yf=basis[yh][ya]
            for j in range(d):
                grad[j]+=E[j]-yf[j]; diag[j]+=max(E2[j]-E[j]*E[j],1e-9)
        step=[grad[j]/max(diag[j],1e-9) for j in range(d)]
        mx=max(abs(x) for x in step); scale=1 if mx<=0.5 else .5/mx
        beta=[b-scale*z for b,z in zip(beta,step)]
        if max(abs(scale*z) for z in step)<1e-7:break
    return beta,lam

def matrix_for_family(cid,param,r,base_matrix,repaired,core,beta=None):
    if cid=='V3.2-A': return core.candidate_a(base_matrix,repaired)
    if cid=='V3.2-B': return core.candidate_b(float(r['fusion']['mean_home']),float(r['fusion']['mean_away']),float(param['dispersion_k']),float(param['dependence_fraction']),repaired)
    if cid=='V3.2-C': return core.candidate_c(base_matrix,beta,repaired)
    raise ValueError(cid)

def inner_surface_selection(rows19,train18,base_mats,base_pmap,repaired19,core,contract):
    out={}
    mm={r['fixture_id']:matrix_for_family('V3.2-A',{},r,base_mats[r['fixture_id']],repaired19[r['fixture_id']],core) for r in rows19}
    ev=score_eval(rows19,mm,{r['fixture_id']:base_mats[r['fixture_id']] for r in rows19},core,contract,repaired19)
    out['V3.2-A']={'selected':{'params':{},'score':ev} if ev['checks']['all_pass'] else None,'board':[{'params':{},'score':ev,'all_pass':ev['checks']['all_pass']}]}
    grid=contract['joint_score_surface_candidates'][1]['inner_grid']; board=[]
    for k in grid['dispersion_k']:
      for dep in grid['dependence_fraction']:
        par={'dispersion_k':float(k),'dependence_fraction':float(dep)}
        mm={r['fixture_id']:matrix_for_family('V3.2-B',par,r,base_mats[r['fixture_id']],repaired19[r['fixture_id']],core) for r in rows19}
        ev=score_eval(rows19,mm,{r['fixture_id']:base_mats[r['fixture_id']] for r in rows19},core,contract,repaired19)
        board.append({'params':par,'score':ev,'all_pass':ev['checks']['all_pass']})
    good=[x for x in board if x['all_pass']]; good.sort(key=lambda x:(x['score']['candidate']['exact_score_logloss'],json.dumps(x['params'],sort_keys=True)))
    out['V3.2-B']={'selected':good[0] if good else None,'board':board}
    grid=contract['joint_score_surface_candidates'][2]['inner_grid']; board=[]
    train_m={r['fixture_id']:base_mats[r['fixture_id']] for r in train18 if r['fixture_id'] in base_mats}
    for lm in grid['ridge_lambda_multiplier']:
        beta,lam=fit_surface_c([r for r in train18 if r['fixture_id'] in train_m],train_m,core,lm)
        par={'ridge_lambda_multiplier':float(lm)}
        mm={r['fixture_id']:matrix_for_family('V3.2-C',par,r,base_mats[r['fixture_id']],repaired19[r['fixture_id']],core,beta) for r in rows19}
        ev=score_eval(rows19,mm,{r['fixture_id']:base_mats[r['fixture_id']] for r in rows19},core,contract,repaired19)
        board.append({'params':par,'beta':beta,'lambda':lam,'score':ev,'all_pass':ev['checks']['all_pass']})
    good=[x for x in board if x['all_pass']]; good.sort(key=lambda x:(x['score']['candidate']['exact_score_logloss'],json.dumps(x['params'],sort_keys=True)))
    out['V3.2-C']={'selected':good[0] if good else None,'board':board}
    return out

def fit_predict_season(s,rows,base_pmap,base_mats,snaps,core,contract,out_params,surf_select):
    train=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in base_pmap]; test=[r for r in rows if r['season']==s]
    bundle=model_bundle(train,base_pmap,snaps,core,out_params); pmap={}; wmap={}
    for r in test:
        p,a=predict_outcome(bundle,out_params,base_pmap[r['fixture_id']],r,snaps.get(r['fixture_id']),core); pmap[r['fixture_id']]=p; wmap[r['fixture_id']]={'parity':a['parity'],'fragility':a['fragility']}
    mmaps={}
    for cid in ('V3.2-A','V3.2-B','V3.2-C'):
        sel=surf_select[cid]['selected']
        if sel is None: continue
        param=sel['params']; beta=None
        if cid=='V3.2-C':
            train_m={r['fixture_id']:base_mats[r['fixture_id']] for r in train}
            beta,_=fit_surface_c(train,train_m,core,param['ridge_lambda_multiplier'])
        mmaps[cid]={r['fixture_id']:matrix_for_family(cid,param,r,base_mats[r['fixture_id']],pmap[r['fixture_id']],core,beta) for r in test}
    return test,pmap,wmap,mmaps,{'train_n':len(train),'fit_n':bundle['fit_n']}

def paired_logloss_required_n(rows,pmap,bmap):
    d=[]
    for r in rows:
        y=result_idx(r); d.append(-math.log(max(bmap[r['fixture_id']][y],EPS))-(-math.log(max(pmap[r['fixture_id']][y],EPS))))
    if len(d)<2:return None
    eff=sum(d)/len(d); sd=stdev(d)
    if eff<=0 or sd<=0:return None
    zsum=1.959963984540054+0.8416212335729143
    return math.ceil((zsum*sd/eff)**2)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--core',type=pathlib.Path,required=True)
    ap.add_argument('--v311-engine',type=pathlib.Path,required=True); ap.add_argument('--v31-engine',type=pathlib.Path,required=True); ap.add_argument('--usr1-engine',type=pathlib.Path,required=True)
    ap.add_argument('--v2-engine',type=pathlib.Path,required=True); ap.add_argument('--xg-engine',type=pathlib.Path,required=True); ap.add_argument('--v1-engine',type=pathlib.Path,required=True)
    ap.add_argument('--v1-result',type=pathlib.Path,required=True); ap.add_argument('--db',type=pathlib.Path,required=True); ap.add_argument('--xg-identity',type=pathlib.Path,required=True)
    ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    if c['status']!='FROZEN_BEFORE_TARGET_LABEL_SCORING':raise V32Error('contract status drift')
    core=loadmod('v32_core_exec',a.core); v311=loadmod('v32_v311',a.v311_engine); v31=loadmod('v32_v31',a.v31_engine); usr=loadmod('v32_usr',a.usr1_engine); xg=loadmod('v32_xg',a.xg_engine); v2=loadmod('v32_v2',a.v2_engine)
    rows,fold_map,fold_rows,rowrec=v311.build_rows_joint(xg,v2,a.v1_engine,a.v1_result,a.db,a.xg_identity)
    proc,procrec=v31.process_features_ext(usr,a.db,rows,2023)
    write_json(a.out/'row_receipt.json',rowrec); write_json(a.out/'frozen_process_receipt.json',procrec)
    base_pmap,base_mats,baserec=frozen_baselines(v311,v31,usr,rows,proc); write_json(a.out/'frozen_v311_oos_baseline_receipt.json',{'segments':baserec,'prediction_n':len(base_pmap)})
    games,pri=load_process_games(a.db); state_params,snaps,stateboard=select_state(core,c,games,pri,{r['fixture_id'] for r in rows}); write_json(a.out/'dynamic_state_selection_2019.json',stateboard)
    bys=_group(rows,lambda r:r['season']); train18=[r for r in bys[2018] if r['fixture_id'] in base_pmap]; rows19=bys[2019]
    out_params,outboard=inner_outcome_selection(rows19,train18,base_pmap,snaps,core,c); write_json(a.out/'inner_2019_outcome_selection.json',outboard)
    if out_params is None:
        final={'schema_version':'football3-v3-2-final-v1','status':c['terminal']['development_failure'],'reason':'no_hard_gate_valid_2019_outcome_setting',
               'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,
               'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    bundle=model_bundle(train18,base_pmap,snaps,core,out_params); repaired19={}
    for r in rows19: repaired19[r['fixture_id']]=predict_outcome(bundle,out_params,base_pmap[r['fixture_id']],r,snaps.get(r['fixture_id']),core)[0]
    surf=inner_surface_selection(rows19,train18,base_mats,base_pmap,repaired19,core,c); write_json(a.out/'inner_2019_score_surface_selection.json',surf)
    devrows=[]; devp={}; devw={}; devm={cid:{} for cid in ('V3.2-A','V3.2-B','V3.2-C')}; seasonrecs=[]
    for s in (2020,2021,2022):
        test,p,w,mm,rec=fit_predict_season(s,rows,base_pmap,base_mats,snaps,core,c,out_params,surf)
        devrows.extend(test); devp.update(p); devw.update(w)
        for cid,z in mm.items():devm[cid].update(z)
        seasonrecs.append({'season':s,**rec})
    oe=outcome_eval(devrows,devp,{r['fixture_id']:base_pmap[r['fixture_id']] for r in devrows},c,fold_map=fold_map,weights=devw,require_folds=True)
    families=[]
    for cid in ('V3.2-A','V3.2-B','V3.2-C'):
        if surf[cid]['selected'] is None:
            families.append({'candidate_id':cid,'all_pass':False,'reason':'no_hard_gate_valid_2019_score_params'}); continue
        se=score_eval(devrows,devm[cid],{r['fixture_id']:base_mats[r['fixture_id']] for r in devrows},core,c,devp)
        allp=oe['checks']['all_pass'] and se['checks']['all_pass']
        families.append({'candidate_id':cid,'params':surf[cid]['selected']['params'],'outcome':oe,'score':se,'all_pass':allp})
    passing=[x for x in families if x['all_pass']]
    passing.sort(key=lambda x:(-x['outcome']['candidate']['top1'],x['outcome']['candidate']['logloss'],x['score']['candidate']['exact_score_logloss'],x['score']['candidate']['total_goals']['rps'],x['candidate_id']))
    chosen=passing[0] if passing else None
    write_json(a.out/'rolling_2020_2022_family_selection.json',{'seasons':seasonrecs,'families':families,'selected':chosen,'candidate_frozen_before_2023_score':True})
    if chosen is None:
        final={'schema_version':'football3-v3-2-final-v1','status':c['terminal']['development_failure'],'reason':'no_family_passed_2020_2022_hard_gates',
               'state_params':state_params,'outcome_params':out_params,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,
               'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    test23,p23,w23,mm23,rec23=fit_predict_season(2023,rows,base_pmap,base_mats,snaps,core,c,out_params,surf)
    o23=outcome_eval(test23,p23,{r['fixture_id']:base_pmap[r['fixture_id']] for r in test23},c,weights=w23)
    s23=score_eval(test23,mm23[chosen['candidate_id']],{r['fixture_id']:base_mats[r['fixture_id']] for r in test23},core,c,p23)
    all23=o23['checks']['all_pass'] and s23['checks']['all_pass']
    hold={'candidate_id':chosen['candidate_id'],'candidate_params':chosen['params'],'outcome_params':out_params,'state_params':state_params,'fit':rec23,'outcome':o23,'score':s23,'all_pass':all23}
    write_json(a.out/'candidate_fixed_2023_holdout.json',hold)
    status=c['terminal']['development_success'] if all23 else c['terminal']['development_failure']
    final={'schema_version':'football3-v3-2-final-v1','status':status,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,
           'candidate_id':chosen['candidate_id'],'score_surface_params':chosen['params'],'outcome_params':out_params,'state_params':state_params,
           'rolling_2020_2022':chosen,'candidate_fixed_2023':hold,
           'development_required_n_paired_logloss':paired_logloss_required_n(devrows,devp,{r['fixture_id']:base_pmap[r['fixture_id']] for r in devrows}),
           'prospective_confirmation_target_top1':c['objective']['target_top1_accuracy'],'prospective_confirmation_required':True,
           'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,
           'post_view_3504_used_for_selection':False,'market_used':False,'live_or_post_target_information_used_before_prediction':False}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
