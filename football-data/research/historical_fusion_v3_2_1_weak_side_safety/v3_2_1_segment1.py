from __future__ import annotations
import argparse, json, math, pathlib, importlib.util, sys
from dataclasses import dataclass
from collections import defaultdict

EPS=1e-15
TOL=1e-12

class V321Error(RuntimeError): pass

def loadmod(name,path):
    spec=importlib.util.spec_from_file_location(name,str(path))
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; spec.loader.exec_module(m); return m

def write_json(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n')

def logit(p):
    p=min(max(float(p),1e-9),1-1e-9); return math.log(p/(1-p))
def sigmoid(z):
    z=max(-40.0,min(40.0,float(z))); return 1.0/(1.0+math.exp(-z))

def fav_weak(base):
    return (0,2) if float(base[0])>=float(base[2]) else (2,0)

def entropy(p): return -sum(float(x)*math.log(max(float(x),EPS)) for x in p)
def margin(p):
    s=sorted((float(x) for x in p),reverse=True); return s[0]-s[1]

def oriented_features(base,r,snap,core):
    if not snap or not snap.get('valid'): return None
    fav,weak=fav_weak(base); sgn=1.0 if fav==0 else -1.0
    sd=max(float(snap['gap_sd']),1e-9)
    z=float(snap['gap'])/sd
    total=float(r['fusion']['mean_home'])+float(r['fusion']['mean_away'])
    atk=float(snap['home_attack'])-float(snap['away_attack'])
    de=float(snap['away_defence'])-float(snap['home_defence'])
    pr=float(snap['home_process'])-float(snap['away_process'])
    favz=sgn*z; favatk=sgn*atk; favde=sgn*de; favpr=sgn*pr; favha=sgn*float(snap['home_advantage'])
    mm=float(core.fragility_weight(float(snap['gap']),float(snap['gap_sd'])))
    common=[float(base[fav]),float(base[1]),float(base[weak]),float(base[fav])-float(base[weak]),entropy(base),margin(base),total]
    favx=common+[favz,favatk,favde,favpr,favha,float(snap['uncertainty']),mm]
    weakx=[float(base[weak]),float(base[fav]),float(base[1]),float(base[fav])-float(base[weak]),-favz,-favatk,-favde,-favpr,float(snap['uncertainty']),total,mm]
    return {'fav':fav,'weak':weak,'favx':favx,'weakx':weakx,'mismatch':mm}

def standardizer(raw,key):
    vals=[r[key] for r in raw]; d=len(vals[0]); means=[]; sds=[]; cols=[]
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
        if abs(M[piv][i])<1e-12: raise V321Error('singular')
        M[i],M[piv]=M[piv],M[i]; z=M[i][i]
        for j in range(i,n+1):M[i][j]/=z
        for r in range(n):
            if r==i:continue
            f=M[r][i]
            for j in range(i,n+1):M[r][j]-=f*M[i][j]
    return [M[i][n] for i in range(n)]

@dataclass
class OffsetBinary:
    means:list; sds:list; cols:list; beta:list; lam:float
    def residual(self,v):
        x=transform(v,self.means,self.sds,self.cols)
        return sum(a*z for a,z in zip(self.beta,x))

def fit_offset_binary(raw,key,label,offset,multiplier,weight=None):
    if len(raw)<250: raise V321Error('insufficient binary rows')
    means,sds,cols=standardizer(raw,key); d=len(cols)
    if d<1: raise V321Error('zero active columns')
    lam=float(multiplier)*d; beta=[0.0]*d
    for _ in range(9):
        g=[lam*x for x in beta]; H=[[0.0]*d for _ in range(d)]
        for j in range(d):H[j][j]+=lam
        for rec in raw:
            x=transform(rec[key],means,sds,cols); y=float(rec[label]); w=1.0 if weight is None else float(rec[weight])
            z=float(rec[offset])+sum(a*b for a,b in zip(beta,x)); q=sigmoid(z); er=w*(q-y); vv=w*q*(1-q)
            for j in range(d):
                g[j]+=er*x[j]
                for k in range(d):H[j][k]+=vv*x[j]*x[k]
        step=solve(H,g); mx=max(abs(x) for x in step); sc=1.0 if mx<=2 else 2.0/mx
        beta=[b-sc*s for b,s in zip(beta,step)]
        if max(abs(sc*s) for s in step)<1e-7:break
    return OffsetBinary(means,sds,cols,beta,lam)

def make_training(rows,base_map,snaps,core):
    favdraw=[]; upset=[]
    for r in rows:
        fid=r['fixture_id']; b=base_map.get(fid)
        if b is None:continue
        f=oriented_features(b,r,snaps.get(fid),core)
        if f is None:continue
        y=0 if int(r['home_goals'])>int(r['away_goals']) else 1 if int(r['home_goals'])==int(r['away_goals']) else 2
        fav,weak=f['fav'],f['weak']
        denom=float(b[fav])+float(b[1]); base_draw=float(b[1])/max(denom,EPS)
        if y in (fav,1):
            favdraw.append({'x':f['favx'],'y':1.0 if y==1 else 0.0,'offset':logit(base_draw),'w':f['mismatch']})
        upset.append({'x':f['weakx'],'y':1.0 if y==weak else 0.0,'offset':logit(float(b[weak])),'w':f['mismatch']})
    return favdraw,upset

def predict_A(base,r,snap,core,model,scale,cap):
    f=oriented_features(base,r,snap,core)
    if f is None:return list(base),{'fallback':True,'draw_shift':0.0,'weak_delta':0.0,'mismatch':0.0}
    fav,weak=f['fav'],f['weak']; rem=float(base[fav])+float(base[1]); bd=float(base[1])/max(rem,EPS)
    qd=sigmoid(logit(bd)+float(scale)*float(f['mismatch'])*model.residual(f['favx']))
    desired=rem*qd; shift=max(0.0,min(float(cap),desired-float(base[1]),float(base[fav])-EPS))
    out=list(map(float,base)); out[1]+=shift; out[fav]-=shift; out[weak]=float(base[weak])
    s=sum(out); out=[x/s for x in out]
    out[weak]=float(base[weak]); other=1.0-out[weak]; cur=out[fav]+out[1]; out[fav]*=other/cur; out[1]*=other/cur
    return out,{'fallback':False,'draw_shift':shift,'weak_delta':out[weak]-float(base[weak]),'mismatch':f['mismatch'],'fav':fav,'weak':weak}

def predict_B(base,r,snap,core,modelA,scaleA,capA,modelB,scaleB,capB):
    a,diag=predict_A(base,r,snap,core,modelA,scaleA,capA)
    if diag['fallback']:return a,diag
    f=oriented_features(base,r,snap,core); fav,weak=f['fav'],f['weak']
    resid=modelB.residual(f['weakx']); signal=max(0.0,math.tanh(float(scaleB)*resid))
    delta=min(float(capB)*float(f['mismatch'])*signal,max(0.0,float(a[fav])-EPS))
    out=list(a); out[weak]+=delta; out[fav]-=delta
    diag=dict(diag); diag['weak_delta']=out[weak]-float(base[weak]); diag['upset_signal']=signal
    return out,diag

def result_idx(r):return 0 if int(r['home_goals'])>int(r['away_goals']) else 1 if int(r['home_goals'])==int(r['away_goals']) else 2

def one(p,y):
    ll=-math.log(max(float(p[y]),EPS)); br=sum((float(p[i])-(1 if i==y else 0))**2 for i in range(3))
    c1=float(p[0]); c2=float(p[0])+float(p[1]); t1=1.0 if y==0 else 0.0; t2=1.0 if y<=1 else 0.0
    rps=((c1-t1)**2+(c2-t2)**2)/2; top=float(max(range(3),key=lambda i:(float(p[i]),-i))==y)
    return ll,br,rps,top

def metric(rows,pmap):
    v=[one(pmap[r['fixture_id']],result_idx(r)) for r in rows]; n=len(v)
    return {'n':n,'logloss':sum(x[0] for x in v)/n,'brier':sum(x[1] for x in v)/n,'rps':sum(x[2] for x in v)/n,'top1':sum(x[3] for x in v)/n}

def eval_candidate(rows,pmap,bmap,diags,contract,fold_map=None,require_folds=False,family=None):
    g=contract['hard_gates']; cm=metric(rows,pmap); bm=metric(rows,bmap)
    weak=[]; draw=[]; mism=[]; flips={'wrong_to_correct':0,'correct_to_wrong':0}
    minwd=1e9; maxwd=-1e9; maxweakerr=0.0
    groups=defaultdict(list)
    for r in rows:
        fid=r['fixture_id']; b=bmap[fid]; p=pmap[fid]; y=result_idx(r); fav,weakside=fav_weak(b)
        if y==weakside: weak.append((-math.log(max(p[y],EPS)),-math.log(max(b[y],EPS))))
        if y==1:draw.append((-math.log(max(p[1],EPS)),-math.log(max(b[1],EPS))))
        if diags[fid]['mismatch']>=0.5:mism.append(r)
        bt=max(range(3),key=lambda i:(b[i],-i)); pt=max(range(3),key=lambda i:(p[i],-i))
        flips['wrong_to_correct']+=int(bt!=y and pt==y); flips['correct_to_wrong']+=int(bt==y and pt!=y)
        wd=p[weakside]-b[weakside]; minwd=min(minwd,wd); maxwd=max(maxwd,wd); maxweakerr=max(maxweakerr,abs(wd))
        groups[(r['league'],r['season'])].append(r)
    def avgdiff(v):return sum(a-b for a,b in v)/len(v) if v else None
    mm=metric(mism,pmap) if mism else None; mb=metric(mism,bmap) if mism else None
    worst=-1e9
    for rs in groups.values():
        if len(rs)>=100:worst=max(worst,metric(rs,pmap)['logloss']-metric(rs,bmap)['logloss'])
    fold_non=0; folds=[]
    if fold_map:
        for k in range(8):
            rs=[r for r in rows if fold_map.get(r['fixture_id'])==k]
            if not rs:continue
            d=metric(rs,pmap)['logloss']-metric(rs,bmap)['logloss']; ok=d<=TOL; fold_non+=int(ok); folds.append({'fold':k,'n':len(rs),'logloss_delta':d,'nondegrade':ok})
    d={'global_logloss_delta':cm['logloss']-bm['logloss'],'global_brier_delta':cm['brier']-bm['brier'],'global_rps_delta':cm['rps']-bm['rps'],'global_top1_delta':cm['top1']-bm['top1'],
       'weak_conditional_logloss_degradation':avgdiff(weak),'draw_conditional_logloss_degradation':avgdiff(draw),
       'mismatch_logloss_delta':None if mm is None else mm['logloss']-mb['logloss'],'mismatch_top1_delta':None if mm is None else mm['top1']-mb['top1'],
       'worst_group_logloss_degradation':worst,'min_weak_probability_delta':minwd,'max_weak_probability_delta':maxwd,
       'max_abs_weak_probability_error':maxweakerr if family=='A' else None,'fold_nondegrade_n':fold_non,'net_top1_repairs':flips['wrong_to_correct']-flips['correct_to_wrong'],**flips}
    cks={'coverage':len(pmap)==len(rows),'ll':d['global_logloss_delta']<=g['global_logloss_delta_max']+TOL,'brier':d['global_brier_delta']<=g['global_brier_delta_max']+TOL,
         'rps':d['global_rps_delta']<=g['global_rps_delta_max']+TOL,'top1':d['global_top1_delta']>=g['global_top1_delta_min']-TOL,
         'weak':d['weak_conditional_logloss_degradation'] is not None and d['weak_conditional_logloss_degradation']<=g['actual_weak_side_win_conditional_logloss_degradation_max']+TOL,
         'draw':d['draw_conditional_logloss_degradation'] is not None and d['draw_conditional_logloss_degradation']<=g['actual_draw_conditional_logloss_degradation_max']+TOL,
         'mismatch_ll':d['mismatch_logloss_delta'] is not None and d['mismatch_logloss_delta']<=g['mismatch_regime_logloss_degradation_max']+TOL,
         'mismatch_top1':d['mismatch_top1_delta'] is not None and d['mismatch_top1_delta']>=g['mismatch_regime_top1_delta_min']-TOL,
         'group':worst<=g['worst_eligible_league_season_logloss_degradation_max']+TOL,
         'folds':(not require_folds) or fold_non>=g['fold_nondegrade_min']}
    if family=='A':cks['structural_weak_exact']=maxweakerr<=g['candidate_A_max_abs_weak_probability_error']+TOL
    if family=='B':cks['structural_weak_nondecrease']=minwd>=g['candidate_B_min_weak_probability_delta']-TOL
    cks['all_pass']=all(cks.values())
    return {'baseline':bm,'candidate':cm,'mismatch_baseline':mb,'mismatch_candidate':mm,'deltas':d,'checks':cks,'folds':folds}

def models_for(train_rows,bmap,snaps,core,lamA,lamB=None):
    fd,up=make_training(train_rows,bmap,snaps,core)
    ma=fit_offset_binary(fd,'x','y','offset',lamA,'w')
    mb=fit_offset_binary(up,'x','y','offset',lamB,'w') if lamB is not None else None
    return ma,mb,{'favdraw_n':len(fd),'upset_n':len(up)}

def predict_rows(rows,bmap,snaps,core,family,params,train_rows):
    ma,mb,fit=models_for(train_rows,bmap,snaps,core,params['lambdaA'],params.get('lambdaB'))
    pmap={}; diags={}
    for r in rows:
        b=bmap[r['fixture_id']]
        if family=='A':p,d=predict_A(b,r,snaps.get(r['fixture_id']),core,ma,params['scaleA'],params['capA'])
        else:p,d=predict_B(b,r,snaps.get(r['fixture_id']),core,ma,params['scaleA'],params['capA'],mb,params['scaleB'],params['capB'])
        pmap[r['fixture_id']]=p; diags[r['fixture_id']]=d
    return pmap,diags,fit

def inner_select(family,train18,rows19,bmap,snaps,core,c):
    A=c['candidate_A_draw_only_fragility']['inner_grid_2019_only']; B=c['candidate_B_independent_upset_gate']['inner_grid_2019_only']
    board=[]
    for la in A['ridge_lambda_multiplier']:
      for sa in A['residual_scale']:
       for ca in A['max_favourite_draw_mass_shift']:
        if family=='A': combos=[None]
        else: combos=[(lb,sb,cb) for lb in B['ridge_lambda_multiplier'] for sb in B['positive_residual_scale'] for cb in B['max_weak_probability_uplift']]
        for extra in combos:
            p={'lambdaA':float(la),'scaleA':float(sa),'capA':float(ca)}
            if extra:p.update(lambdaB=float(extra[0]),scaleB=float(extra[1]),capB=float(extra[2]))
            pm,dg,fit=predict_rows(rows19,bmap,snaps,core,family,p,train18)
            ev=eval_candidate(rows19,pm,{r['fixture_id']:bmap[r['fixture_id']] for r in rows19},dg,c,family=family)
            board.append({'params':p,'fit':fit,'eval':ev,'all_pass':ev['checks']['all_pass']})
    good=[x for x in board if x['all_pass']]
    good.sort(key=lambda x:(-x['eval']['candidate']['top1'],x['eval']['candidate']['logloss'],x['eval']['candidate']['brier'],json.dumps(x['params'],sort_keys=True)))
    return {'family':family,'board_size':len(board),'passing_n':len(good),'selected':good[0] if good else None,'top10':sorted(board,key=lambda x:(not x['all_pass'],-x['eval']['candidate']['top1'],x['eval']['candidate']['logloss']))[:10]}

def rolling(family,params,rows,bmap,snaps,core,c,fold_map):
    allr=[]; pm={}; dg={}; seasons=[]
    for s in (2020,2021,2022):
        tr=[r for r in rows if 2018<=r['season']<s and r['fixture_id'] in bmap]; te=[r for r in rows if r['season']==s]
        p,d,fit=predict_rows(te,bmap,snaps,core,family,params,tr); pm.update(p); dg.update(d); allr.extend(te)
        seasons.append({'season':s,'fit':fit,'eval':eval_candidate(te,p,{r['fixture_id']:bmap[r['fixture_id']] for r in te},d,c,family=family)})
    pooled=eval_candidate(allr,pm,{r['fixture_id']:bmap[r['fixture_id']] for r in allr},dg,c,fold_map=fold_map,require_folds=True,family=family)
    return {'family':family,'params':params,'seasons':seasons,'pooled':pooled,'all_pass':pooled['checks']['all_pass']}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v32dev','core','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'):
        ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    if c['status']!='FROZEN_BEFORE_V3_2_1_TARGET_SCORING':raise V321Error('contract drift')
    d=loadmod('v321_v32dev',a.v32dev); core=loadmod('v321_core',a.core); v311=loadmod('v321_v311',a.v311); v31=loadmod('v321_v31',a.v31); usr=loadmod('v321_usr',a.usr1); v2=loadmod('v321_v2',a.v2); xg=loadmod('v321_xg',a.xg)
    rows,fold_map,_,rowrec=v311.build_rows_joint(xg,v2,a.v1,a.v1_result,a.db,a.xg_identity)
    proc,procrec=v31.process_features_ext(usr,a.db,rows,2023)
    bmap,_,baserec=d.frozen_baselines(v311,v31,usr,rows,proc)
    games,pri=d.load_process_games(a.db); sp=c['frozen_dynamic_state']; state={'process_variance_scale':sp['process_variance_scale'],'observation_variance_scale':sp['observation_variance_scale'],'lag_half_life_matches':sp['lag_half_life_matches']}
    snaps,staterec=d.simulate_state(core,games,pri,state,{r['fixture_id'] for r in rows})
    write_json(a.out/'row_receipt.json',rowrec); write_json(a.out/'process_receipt.json',procrec); write_json(a.out/'baseline_receipt.json',{'prediction_n':len(bmap),'segments':baserec}); write_json(a.out/'fixed_state_receipt.json',{'params':state,**staterec})
    bys=d._group(rows,lambda r:r['season']); train18=[r for r in bys[2018] if r['fixture_id'] in bmap]; rows19=bys[2019]
    ia=inner_select('A',train18,rows19,bmap,snaps,core,c); ib=inner_select('B',train18,rows19,bmap,snaps,core,c)
    write_json(a.out/'inner_2019_A.json',ia); write_json(a.out/'inner_2019_B.json',ib)
    rolls=[]
    for ii in (ia,ib):
        if ii['selected'] is not None:rolls.append(rolling(ii['family'],ii['selected']['params'],rows,bmap,snaps,core,c,fold_map))
    write_json(a.out/'rolling_2020_2022.json',{'families':rolls})
    good=[x for x in rolls if x['all_pass']]
    good.sort(key=lambda x:(-x['pooled']['candidate']['top1'],-x['pooled']['mismatch_candidate']['top1'],x['pooled']['candidate']['logloss'],x['family']))
    chosen=good[0] if good else None
    if chosen is None:
        final={'schema_version':'football3-v3-2-1-segment1-final-v1','status':c['terminal']['failure'],'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'reason':'no_nontrivial_family_passed_2020_2022','formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,'2023_opened':False,'3504_opened':False}
        write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    write_json(a.out/'candidate_freeze_before_2023.json',{'family':chosen['family'],'params':chosen['params'],'selection':chosen['pooled'],'frozen_before_2023':True})
    tr=[r for r in rows if 2018<=r['season']<=2022 and r['fixture_id'] in bmap]; te=bys[2023]
    p,dg,fit=predict_rows(te,bmap,snaps,core,chosen['family'],chosen['params'],tr)
    h=eval_candidate(te,p,{r['fixture_id']:bmap[r['fixture_id']] for r in te},dg,c,family=chosen['family'])
    write_json(a.out/'candidate_fixed_2023.json',{'family':chosen['family'],'params':chosen['params'],'fit':fit,'eval':h})
    st=c['terminal']['success'] if h['checks']['all_pass'] else c['terminal']['failure']
    final={'schema_version':'football3-v3-2-1-segment1-final-v1','status':st,'research_only':True,'post_view':True,'fresh_confirmation':False,'promotion_allowed':False,'family':chosen['family'],'params':chosen['params'],'rolling_2020_2022':chosen['pooled'],'candidate_fixed_2023':h,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_enablement_changed':False,'formal_weights_changed':False,'2023_opened':True,'3504_opened':False}
    write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0

if __name__=='__main__':raise SystemExit(main())
