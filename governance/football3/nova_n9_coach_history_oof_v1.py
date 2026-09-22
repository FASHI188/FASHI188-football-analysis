#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math
import numpy as np
from pathlib import Path
from datetime import datetime

class N9CoachError(RuntimeError): pass
def require(c,m):
    if not c: raise N9CoachError(m)
def readl(p): return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
def dt(v): return datetime.fromisoformat(str(v).replace('Z','+00:00'))
def groups(rows):
    out=[]; s=0
    while s<len(rows):
        e=s+1
        while e<len(rows) and rows[e]['kickoff']==rows[s]['kickoff']: e+=1
        out.append((s,e)); s=e
    return out

def validate_source(rows,p):
    require(len(rows)==p['data']['development_n'],'SOURCE_N')
    for r in rows:
        require(int(r['season_start'])==2022,'SOURCE_SEASON')
        for side in ('home','away'):
            s=r[side+'_manager_state']
            require(set(s)>={'available','history_n','tenure_observation_n','source_game_id'},'STATE_SCHEMA')
            require(s.get('source_game_id')!=r.get('tm_game_id'),'TARGET_MANAGER_DIRECT_USE')

def align_baseline(rows,base):
    sids=[r['fixture_id'] for r in rows]; bids=[r['n2_fixture_id'] for r in base]
    require(len(bids)==len(set(bids)),'BASE_DUP'); require(set(sids)==set(bids),'BASE_ID_SET')
    m={r['n2_fixture_id']:r for r in base}; out=[m[x] for x in sids]
    for s,b in zip(rows,out):
        require(dt(s['kickoff'])==dt(b['kickoff']),f"KO:{s['fixture_id']}")
        require(s['home_team_id']==b['home_team_id'] and s['away_team_id']==b['away_team_id'] and s['league']==b['league'],f"IDENTITY:{s['fixture_id']}")
        require(b.get('target_label_read') is False,'BASE_LABEL_READ')
    return out

def state_num(s,key):
    return float(s[key]) if s.get('available') is True and s.get(key) is not None else None

def raw_features(r,route,p):
    h=r['home_manager_state']; a=r['away_manager_state']
    ht=state_num(h,'tenure_observation_n'); at=state_num(a,'tenure_observation_n')
    hh=state_num(h,'history_n'); ah=state_num(a,'history_n')
    hav=float(h.get('available') is True); aav=float(a.get('available') is True)
    lht=None if ht is None else math.log1p(ht); lat=None if at is None else math.log1p(at)
    tdiff=None if lht is None or lat is None else lht-lat
    one=float(p['development_protocol']['new_manager_1_lte']); three=float(p['development_protocol']['new_manager_3_lte']); established=float(p['development_protocol']['established_manager_gte'])
    hn1=None if ht is None else float(ht<=one); an1=None if at is None else float(at<=one)
    hn3=None if ht is None else float(ht<=three); an3=None if at is None else float(at<=three)
    r1=[lht,lat,tdiff,hav,aav]
    r2=[hn1,an1,None if hn1 is None or an1 is None else hn1-an1,hn3,an3,None if hn3 is None or an3 is None else hn3-an3,hav,aav]
    lhh=None if hh is None else math.log1p(hh); lah=None if ah is None else math.log1p(ah)
    hdiff=None if lhh is None or lah is None else lhh-lah
    if route=='R1_TENURE': return r1
    if route=='R2_CHANGE_BANDS': return r2
    r3=r1+[lhh,lah,hdiff]+r2[:6]
    if route=='R3_TENURE_DEPTH': return r3
    if route=='R4_FIXED_NONLINEAR':
        he=None if ht is None else float(ht>=established); ae=None if at is None else float(at>=established)
        extra=[
            None if hn1 is None or ae is None else hn1*ae,
            None if an1 is None or he is None else an1*he,
            None if hn1 is None or an1 is None else hn1*an1,
            None if hn3 is None or an3 is None else hn3*an3,
            None if tdiff is None or hdiff is None else tdiff*hdiff
        ]
        return r3+extra
    raise N9CoachError('ROUTE')

def scaler(X,idx):
    means=[]; stds=[]
    for j in range(len(X[0])):
        vals=[float(X[i][j]) for i in idx if X[i][j] is not None and math.isfinite(float(X[i][j]))]
        m=sum(vals)/len(vals) if vals else 0.; v=sum((x-m)**2 for x in vals)/len(vals) if vals else 0.
        means.append(m); stds.append(max(math.sqrt(v),1e-9))
    return means,stds
def zrow(x,m,s): return [((m[j] if v is None else float(v))-m[j])/s[j] for j,v in enumerate(x)]
def softmax(base,x,beta):
    eps=1e-15; oh=math.log(max(base[0],eps)/max(base[2],eps)); od=math.log(max(base[1],eps)/max(base[2],eps)); xx=[1.]+x
    lh=oh+sum(beta[0][j]*xx[j] for j in range(len(xx))); ld=od+sum(beta[1][j]*xx[j] for j in range(len(xx))); mm=max(lh,ld,0.)
    a=math.exp(lh-mm); d=math.exp(ld-mm); z=math.exp(-mm); t=a+d+z; return [a/t,d/t,z/t]
def fit(X,bases,y,c=.25,iters=300):
    Xn=np.asarray([[1.0]+list(map(float,x)) for x in X],dtype=float); bn=np.asarray(bases,dtype=float); yn=np.asarray(y,dtype=int); eps=1e-15
    off=np.column_stack((np.log(np.maximum(bn[:,0],eps)/np.maximum(bn[:,2],eps)),np.log(np.maximum(bn[:,1],eps)/np.maximum(bn[:,2],eps))))
    Y=np.column_stack((yn==0,yn==1)).astype(float); reg=1.0/(float(c)*len(Xn)); beta=np.zeros((2,Xn.shape[1]),dtype=float)
    def objgrad(B):
        z2=off+Xn@B.T; z=np.column_stack((z2,np.zeros(len(Xn)))); z-=z.max(axis=1,keepdims=True); ez=np.exp(z); pr=ez/ez.sum(axis=1,keepdims=True)
        loss=float(-np.log(np.maximum(pr[np.arange(len(yn)),yn],eps)).mean())+0.5*reg*float((B[:,1:]**2).sum())
        g=((pr[:,:2]-Y).T@Xn)/len(Xn); g[:,1:]+=reg*B[:,1:]; return loss,g
    loss,g=objgrad(beta)
    for _ in range(iters):
        n2=float((g*g).sum())
        if n2<1e-12: break
        step=1.; ok=False
        while step>1e-8:
            cand=beta-step*g; nl,ng=objgrad(cand)
            if nl<=loss-1e-4*step*n2: beta,loss,g=cand,nl,ng; ok=True; break
            step*=.5
        if not ok or float(np.max(np.abs(g)))<1e-6: break
    return beta.tolist()

def blocks(rows,warm=.2,nblocks=5):
    gs=groups(rows); target=math.ceil(len(rows)*warm); n=0; gi=0
    while gi<len(gs) and n<target: n+=gs[gi][1]-gs[gi][0]; gi+=1
    we=gs[gi-1][1]; rem=gs[gi:]; each=(len(rows)-we)/nblocks; out=[]; s=we; a=0
    for k,g in enumerate(rem):
        a+=g[1]-g[0]; left=len(rem)-k-1
        if len(out)<nblocks-1 and a>=each and left>=nblocks-len(out)-1: out.append((s,g[1])); s=g[1]; a=0
    out.append((s,len(rows))); require(len(out)==nblocks,'BLOCKS'); return we,out
def oi(v): return {'home':0,'draw':1,'away':2}[v]
def metrics(ps,y):
    n=len(y); eps=1e-15
    ll=sum(-math.log(max(p[o],eps)) for p,o in zip(ps,y))/n
    br=sum(sum((p[k]-(1 if o==k else 0))**2 for k in range(3)) for p,o in zip(ps,y))/n
    rps=sum(((p[0]-(1 if o==0 else 0))**2+(p[0]+p[1]-(1 if o in (0,1) else 0))**2)/2 for p,o in zip(ps,y))/n
    top=sum(max(range(3),key=lambda k:p[k])==o for p,o in zip(ps,y))/n; bins=[[] for _ in range(10)]
    for p,o in zip(ps,y):
        k=max(range(3),key=lambda q:p[q]); c=p[k]; bins[min(9,int(c*10))].append((c,1. if k==o else 0.))
    ece=sum(len(z)/n*abs(sum(a for a,b in z)/len(z)-sum(b for a,b in z)/len(z)) for z in bins if z)
    return {'n':n,'logloss':ll,'brier':br,'rps':rps,'top1':top,'ece':ece}
def boot(vals,reps,seed):
    a=np.asarray(vals,dtype=float); n=len(a); rng=np.random.default_rng(seed); means=[]; left=int(reps)
    while left:
        b=min(500,left); idx=rng.integers(0,n,size=(b,n),endpoint=False); means.extend(a[idx].mean(axis=1).tolist()); left-=b
    means.sort(); return [means[int(.025*(reps-1))],means[int(.975*(reps-1))]]

def run(prereg,source,baseline,labels,out):
    p=json.loads(Path(prereg).read_text()); rows=readl(source); validate_source(rows,p); base=readl(baseline); lab=readl(labels); n=p['data']['development_n']
    require(len(base)==len(lab)==n,'N'); require([r['fixture_id'] for r in rows]==[r['fixture_id'] for r in lab],'LABEL_ALIGN')
    base=align_baseline(rows,base); probs=[[float(x) for x in r['formal_v2_1x2']] for r in base]; y=[oi(r['outcome']) for r in lab]
    warm,bs=blocks(rows,p['development_protocol']['warmup_fraction'],p['development_protocol']['oof_blocks']); routes=[]; pred={}; ref=None
    for sp in p['subroutes']:
        rid=sp['id']; raw=[raw_features(r,rid,p) for r in rows]; idx=[]; cp=[]
        for s,e in bs:
            tr=list(range(s)); va=list(range(s,e)); m,sd=scaler(raw,tr); X=[zrow(raw[i],m,sd) for i in tr]; XV=[zrow(raw[i],m,sd) for i in va]
            beta=fit(X,[probs[i] for i in tr],[y[i] for i in tr],p['model']['ridge_c']); cp.extend(softmax(probs[i],x,beta) for i,x in zip(va,XV)); idx.extend(va)
        if ref is None: ref=idx
        require(idx==ref,'OOF_DRIFT'); bp=[probs[i] for i in idx]; yy=[y[i] for i in idx]; bm,cm=metrics(bp,yy),metrics(cp,yy)
        eff=[math.log(max(cp[j][yy[j]],1e-15))-math.log(max(bp[j][yy[j]],1e-15)) for j in range(len(yy))]
        q=p['development_protocol']['qualification']; pf=p['development_protocol']['positive_signal_floor']
        qual=bm['logloss']-cm['logloss']>q['logloss_gain_gt'] and cm['brier']-bm['brier']<=q['candidate_minus_formal_brier_lte'] and cm['rps']-bm['rps']<=q['candidate_minus_formal_rps_lte'] and cm['ece']-bm['ece']<=q['candidate_minus_formal_ece_lte']
        pos=bm['logloss']-cm['logloss']>pf['logloss_gain_gt'] and cm['brier']-bm['brier']<=pf['candidate_minus_formal_brier_lte'] and cm['rps']-bm['rps']<=pf['candidate_minus_formal_rps_lte'] and cm['top1']-bm['top1']>=pf['candidate_minus_formal_top1_gte']
        routes.append({'route':rid,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss':bm['logloss']-cm['logloss'],'candidate_minus_formal_brier':cm['brier']-bm['brier'],'candidate_minus_formal_rps':cm['rps']-bm['rps'],'candidate_minus_formal_top1':cm['top1']-bm['top1'],'candidate_minus_formal_ece':cm['ece']-bm['ece'],'qualified':qual,'positive_floor':pos,'paired_bootstrap_95ci':boot(eff,p['metrics']['uncertainty']['repetitions'],p['metrics']['uncertainty']['seed'])}); pred[rid]=cp
    best=max(routes,key=lambda r:r['formal_minus_candidate_logloss']); cls='POSITIVE_SIGNAL' if any(r['qualified'] or r['positive_floor'] for r in routes) else 'FAIL_CURRENT_IMPLEMENTATION'
    idx=ref; yy=[y[i] for i in idx]; bp=[probs[i] for i in idx]; cp=pred[best['route']]; lr={}
    for lg in p['metrics']['group_report']:
        if lg in {'J1','K1'}: lr[lg]={'status':'NOT_AVAILABLE','n':0,'coverage':0.,'weight':0,'matrix_delta':0}; continue
        pos=[j for j,i in enumerate(idx) if rows[i]['league']==lg]; b=[bp[j] for j in pos]; c=[cp[j] for j in pos]; z=[yy[j] for j in pos]
        bm,cm=metrics(b,z),metrics(c,z); lr[lg]={'status':'DEVELOPMENT_OOF','n':len(pos),'coverage':1. if pos else 0.,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss':bm['logloss']-cm['logloss'],'matrix_delta':0}
    res={'schema_version':'football3-nova-n9-coach-history-development-oof-v1','status':'N9_COACH_HISTORY_DEVELOPMENT_OOF_COMPLETE','classification':cls,'development_n':n,'oof_evaluation_n':len(idx),'warmup_end':warm,'completed_matches_only':True,'data_ready_exact_head':p['data_ready']['exact_head'],'data_ready_state_sha256':p['data_ready']['state_projection_sha256'],'manager_name_direct_encoding_used':False,'target_match_manager_direct_feature_used':0,'routes':routes,'best_signal_route':best['route'],'best_formal_minus_candidate_logloss':best['formal_minus_candidate_logloss'],'selected_route':best['route'] if cls=='POSITIVE_SIGNAL' else None,'league_report':lr,'n1_2025_isolated_labels_read':0,'n2_2023_isolated_labels_read':0,'n3_2021_isolated_labels_read':0,'candidate_activation_allowed':False,'promotion_allowed':False,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'candidate_weight':0,'matrix_delta':0,'score_matrix':{'formal_v2_unchanged':True,'candidate_matrix_delta':0,'exact_score_metrics_changed':False}}
    Path(out).mkdir(parents=True,exist_ok=True); Path(out,'development_oof_result.json').write_text(json.dumps(res,indent=2,sort_keys=True)+'\n'); return res

def main():
    a=argparse.ArgumentParser(); a.add_argument('--prereg',required=True); a.add_argument('--source',required=True); a.add_argument('--baseline',required=True); a.add_argument('--labels',required=True); a.add_argument('--out',required=True); x=a.parse_args(); print(json.dumps(run(x.prereg,x.source,x.baseline,x.labels,x.out),sort_keys=True))
if __name__=='__main__': main()
