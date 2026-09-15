#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, math, random
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

class IsolatedTestError(RuntimeError): pass

def require(c,m):
    if not c: raise IsolatedTestError(m)
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def sha256_file(p): return sha256_bytes(Path(p).read_bytes())
def read_json(p):
    x=json.loads(Path(p).read_text()); require(isinstance(x,dict),f"NOT_OBJECT:{p}"); return x
def read_all(p):
    out=[]
    with Path(p).open(encoding='utf-8') as f:
        for i,line in enumerate(f,1):
            if not line.strip(): continue
            x=json.loads(line); require(isinstance(x,dict),f"BAD_JSONL:{p}:{i}"); out.append(x)
    return out
def read_prefix(p,n):
    out=[]
    with Path(p).open(encoding='utf-8') as f:
        for i in range(n):
            line=f.readline(); require(bool(line),f"PREFIX_SHORT:{i}:{n}")
            x=json.loads(line); require(isinstance(x,dict),f"PREFIX_BAD:{i+1}"); out.append(x)
    return out
def read_suffix(p,skip,n):
    out=[]
    with Path(p).open(encoding='utf-8') as f:
        for i in range(skip): require(bool(f.readline()),f"SKIP_SHORT:{i}:{skip}")
        for i in range(n):
            line=f.readline(); require(bool(line),f"SUFFIX_SHORT:{i}:{n}")
            x=json.loads(line); require(isinstance(x,dict),f"SUFFIX_BAD:{i+1}"); out.append(x)
    return out

def dt(s): return datetime.fromisoformat(str(s).replace('Z','+00:00'))
def groups(rows):
    out=[]; s=0
    while s<len(rows):
        e=s+1
        while e<len(rows) and rows[e]['kickoff']==rows[s]['kickoff']: e+=1
        out.append((s,e)); s=e
    return out

def state(hist,size=10):
    if not hist: return None,None,0
    xs=hist[-size:]
    return sum(x[0] for x in xs)/len(xs),sum(x[1] for x in xs)/len(xs),len(hist)

def r2_features(rows):
    histories=defaultdict(list); pending=deque(); out=[None]*len(rows)
    for s,e in groups(rows):
        target=dt(rows[s]['kickoff'])
        while pending and dt(pending[0]['release_at'])<=target:
            r=pending.popleft()
            histories[r['home_team_id']].append((float(r['home_ppda']),float(r['home_deep'])))
            histories[r['away_team_id']].append((float(r['away_ppda']),float(r['away_deep'])))
        for i in range(s,e):
            r=rows[i]; hp,hd,hn=state(histories[r['home_team_id']],10); ap,ad,an=state(histories[r['away_team_id']],10)
            out[i]=[hp,ap,hd,ad,math.log1p(hn),math.log1p(an)]
        for i in range(s,e): pending.append(rows[i])
    require(all(x is not None for x in out),'FEATURE_MISSING'); return out

def scaler(raw,idxs):
    means=[]; stds=[]
    for j in range(len(raw[0])):
        vals=[float(raw[i][j]) for i in idxs if raw[i][j] is not None and math.isfinite(float(raw[i][j]))]
        m=sum(vals)/len(vals) if vals else 0.; v=sum((x-m)**2 for x in vals)/len(vals) if vals else 0.; s=math.sqrt(v)
        means.append(m); stds.append(s if s>=1e-9 else 1.)
    return means,stds
def transform(row,means,stds): return [((means[j] if v is None else float(v))-means[j])/stds[j] for j,v in enumerate(row)]
def softmax_offset(base,x,beta):
    eps=1e-15; xx=[1.]+x
    h=math.log(max(float(base[0]),eps)/max(float(base[2]),eps))+sum(beta[0][j]*xx[j] for j in range(len(xx)))
    d=math.log(max(float(base[1]),eps)/max(float(base[2]),eps))+sum(beta[1][j]*xx[j] for j in range(len(xx)))
    mx=max(h,d,0.); a,b,c=math.exp(h-mx),math.exp(d-mx),math.exp(-mx); z=a+b+c
    return [a/z,b/z,c/z]
def lossgrad(beta,X,bases,y,C):
    n=len(X); d=len(beta[0]); loss=0.; g=[[0.]*d,[0.]*d]; eps=1e-15
    for x,base,t in zip(X,bases,y):
        p=softmax_offset(base,x,beta); loss-=math.log(max(p[t],eps)); xx=[1.]+x
        for k in (0,1):
            err=p[k]-(1. if t==k else 0.)
            for j,v in enumerate(xx): g[k][j]+=err*v
    loss/=n; g=[[v/n for v in row] for row in g]; reg=1./(C*n)
    for k in (0,1):
        for j in range(1,d): loss+=.5*reg*beta[k][j]**2; g[k][j]+=reg*beta[k][j]
    return loss,g
def fit(X,bases,y,C=.25,max_iter=300):
    d=len(X[0])+1; beta=[[0.]*d,[0.]*d]; loss,g=lossgrad(beta,X,bases,y,C); it=0
    for it in range(max_iter):
        norm=sum(v*v for row in g for v in row)
        if norm<1e-12: break
        step=1.; accepted=False
        while step>1e-8:
            cand=[[beta[k][j]-step*g[k][j] for j in range(d)] for k in (0,1)]
            nl,ng=lossgrad(cand,X,bases,y,C)
            if nl<=loss-1e-4*step*norm: beta,loss,g=cand,nl,ng; accepted=True; break
            step*=.5
        if not accepted: break
        if max(abs(v) for row in g for v in row)<1e-6: break
    return beta,loss,it+1

def outcome(x):
    mp={'home':0,'draw':1,'away':2}; require(x in mp,f"BAD_OUTCOME:{x}"); return mp[x]
def metrics(ps,ys):
    n=len(ys); require(n>0,'EMPTY_METRICS'); eps=1e-15
    ll=sum(-math.log(max(p[y],eps)) for p,y in zip(ps,ys))/n
    br=sum(sum((p[k]-(1. if y==k else 0.))**2 for k in range(3)) for p,y in zip(ps,ys))/n
    rps=sum(((p[0]-(1. if y==0 else 0.))**2+(p[0]+p[1]-(1. if y in (0,1) else 0.))**2)/2 for p,y in zip(ps,ys))/n
    top=sum(max(range(3),key=lambda k:p[k])==y for p,y in zip(ps,ys))/n
    bins=[[] for _ in range(10)]
    for p,y in zip(ps,ys):
        k=max(range(3),key=lambda q:p[q]); c=p[k]; bins[min(9,int(c*10))].append((c,1. if k==y else 0.))
    ece=0.
    for b in bins:
        if b: ece+=len(b)/n*abs(sum(x[0] for x in b)/len(b)-sum(x[1] for x in b)/len(b))
    return {'n':n,'logloss':ll,'brier':br,'rps':rps,'top1':top,'ece':ece}
def effects(base,cand,y):
    eps=1e-15; return [math.log(max(cand[i][y[i]],eps))-math.log(max(base[i][y[i]],eps)) for i in range(len(y))]
def bootstrap(vals,reps,seed):
    r=random.Random(seed); n=len(vals); xs=[sum(vals[r.randrange(n)] for _ in range(n))/n for _ in range(reps)]; xs.sort()
    return [xs[int(math.floor(.025*(reps-1)))],xs[int(math.ceil(.975*(reps-1)))]]

def predict(args):
    pre=read_json(args.prereg); dev=read_json(Path(args.development_dir)/'receipt.json')
    require(pre['status']=='DESIGN_LOCKED_PRELABEL','PREREG_STATUS'); require(dev['status']=='N1_DEEP_PPDA_DEVELOPMENT_OOF_PASS','DEV_STATUS')
    require(dev['classification']=='POSITIVE_SIGNAL_DEVELOPMENT_PASS','DEV_CLASS'); require(dev['selected_route']=='R2_W10' and dev['selected_route_frozen'] is True,'ROUTE_NOT_FROZEN')
    require(dev['isolated_test_opened'] is False and dev['isolated_test_label_rows_read']==0,'ISOLATED_ALREADY_OPEN')
    require(pre['experiment_budget']['test_second_chance_route_after_open'] is False,'SECOND_CHANCE_RULE'); require(pre['isolated_test_protocol']['open_once'] is True,'OPEN_ONCE_RULE')
    require(dev['formal_v2_head']==pre['formal_v2']['head'],'FORMAL_HEAD'); require(dev['candidate_weight']==0 and dev['matrix_delta']==0,'DEV_ACTIVE')
    source=read_all(Path(args.source_dir)/'state_projection.jsonl'); formal=read_all(Path(args.prelabel_dir)/'formal_v2_baseline_projection.jsonl')
    dn=int(pre['cohort']['development']['n']); tn=int(pre['cohort']['isolated_test']['n']); require(len(source)==dn+tn==len(formal),'ROW_COUNT')
    ids=[r['fixture_id'] for r in source]; require(ids==[r['fixture_id'] for r in formal],'SOURCE_FORMAL_ORDER')
    require(all(int(r['season_start'])==2024 for r in source[:dn]) and all(int(r['season_start'])==2025 for r in source[dn:]),'SEASON_BOUNDARY')
    labels=read_prefix(Path(args.data_dir)/'data/label_vault.jsonl',dn); require([r['fixture_id'] for r in labels]==ids[:dn],'DEV_LABEL_ALIGNMENT')
    y=[outcome(str(r['outcome'])) for r in labels]; bases=[[float(v) for v in r['formal_v2_1x2']] for r in formal]
    raw=r2_features(source); means,stds=scaler(raw,list(range(dn))); X=[transform(raw[i],means,stds) for i in range(dn)]
    beta,obj,it=fit(X,bases[:dn],y,.25,300)
    test_ps=[softmax_offset(bases[i],transform(raw[i],means,stds),beta) for i in range(dn,dn+tn)]
    out=Path(args.predictions_out); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf-8') as f:
        for pos,i in enumerate(range(dn,dn+tn)):
            f.write(json.dumps({'fixture_id':source[i]['fixture_id'],'kickoff':source[i]['kickoff'],'league':source[i]['league'],'season_start':2025,'route':'R2_W10','formal_v2_1x2':bases[i],'candidate_1x2':test_ps[pos],'prediction_sealed_before_isolated_label_open':True,'label_present':False,'matrix_delta':0},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n')
    seal={'schema_version':'football3-nova-n1-deep-ppda-isolated-prediction-seal-v1','status':'N1_DEEP_PPDA_ISOLATED_PREDICTIONS_SEALED','selected_route':'R2_W10','ridge_c':.25,'development_n':dn,'development_label_rows_read':dn,'isolated_test_n':tn,'isolated_label_rows_read':0,'isolated_test_opened':False,'isolated_predictions_sealed_before_label_open':True,'prediction_n':tn,'predictions_sha256':sha256_file(out),'first_isolated_fixture_id':source[dn]['fixture_id'],'last_isolated_fixture_id':source[-1]['fixture_id'],'scaler_means':means,'scaler_stds':stds,'beta':beta,'training_objective':obj,'training_iterations':it,'formal_v2_head':pre['formal_v2']['head'],'candidate_weight':0,'matrix_delta':0,'formal_v2_changed':False,'current_changed':False,'production_changed':False}
    Path(args.seal_out).write_text(json.dumps(seal,indent=2,sort_keys=True)+'\n',encoding='utf-8'); return seal

def score(args):
    pre=read_json(args.prereg); dev=read_json(Path(args.development_dir)/'receipt.json'); seal=read_json(args.seal)
    require(dev['selected_route']=='R2_W10' and dev['selected_route_frozen'] is True,'DEV_ROUTE'); require(seal['status']=='N1_DEEP_PPDA_ISOLATED_PREDICTIONS_SEALED','SEAL_STATUS')
    require(seal['isolated_label_rows_read']==0 and seal['isolated_test_opened'] is False and seal['isolated_predictions_sealed_before_label_open'] is True,'SEAL_ORDER')
    require(sha256_file(args.predictions)==seal['predictions_sha256'],'PREDICTION_SHA')
    preds=read_all(args.predictions); dn=int(pre['cohort']['development']['n']); tn=int(pre['cohort']['isolated_test']['n']); require(len(preds)==tn,'PRED_N')
    labels=read_suffix(Path(args.data_dir)/'data/label_vault.jsonl',dn,tn); require([x['fixture_id'] for x in labels]==[x['fixture_id'] for x in preds],'ISOLATED_LABEL_ALIGNMENT')
    ys=[outcome(str(x['outcome'])) for x in labels]; base=[x['formal_v2_1x2'] for x in preds]; cand=[x['candidate_1x2'] for x in preds]
    bm,cm=metrics(base,ys),metrics(cand,ys); gain=bm['logloss']-cm['logloss']; proto=pre['isolated_test_protocol']
    per={}; pos_leagues=0; worst=0.
    for league in pre['metrics']['group_report']:
        if league in ('J1','K1'):
            per[league]={'status':'NOT_AVAILABLE','n':0,'coverage':0.0,'weight':0,'matrix_delta':0}; continue
        idx=[i for i,x in enumerate(preds) if x['league']==league]; b=metrics([base[i] for i in idx],[ys[i] for i in idx]); c=metrics([cand[i] for i in idx],[ys[i] for i in idx]); g=b['logloss']-c['logloss']; pos_leagues+=int(g>0); worst=max(worst,max(0.,-g))
        per[league]={'status':'ISOLATED_TEST','n':len(idx),'coverage':1.0,'formal':b,'candidate':c,'formal_minus_candidate_logloss_gain':g,'matrix_delta':0,'score_matrix_metrics':'UNCHANGED_FROM_FORMAL_V2_BY_CONSTRUCTION'}
    stable=(cm['brier']-bm['brier']<=float(proto['candidate_minus_formal_brier_lte']) and cm['rps']-bm['rps']<=float(proto['candidate_minus_formal_rps_lte']) and cm['ece']-bm['ece']<=float(proto['candidate_minus_formal_ece_lte']) and 1.0>=float(proto['coverage_min']) and pos_leagues>=int(proto['positive_test_leagues_min']) and worst<=float(proto['worst_league_logloss_degradation_lte']))
    if gain<=float(proto['logloss_gain_gt']): cls=proto['nonpositive_pooled_test_classification']
    elif stable: cls=proto['pass_classification']
    else: cls=proto['positive_but_stability_fail_classification']
    vals=effects(base,cand,ys); mean=sum(vals)/len(vals); sd=math.sqrt(sum((x-mean)**2 for x in vals)/(len(vals)-1)); u=pre['metrics']['uncertainty']; ci=bootstrap(vals,int(u['repetitions']),int(u['seed']))
    receipt={'schema_version':'football3-nova-n1-deep-ppda-isolated-test-receipt-v1','status':'N1_DEEP_PPDA_ISOLATED_TEST_SCORED','classification':cls,'selected_route':'R2_W10','selected_route_frozen_before_test_open':True,'development_n':dn,'development_label_rows_read_for_final_fit':dn,'isolated_test_n':tn,'isolated_label_rows_read':tn,'isolated_test_opened':True,'isolated_test_open_count':1,'isolated_predictions_sealed_before_label_open':True,'predictions_sha256':seal['predictions_sha256'],'coverage':1.0,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss_gain':gain,'delta_candidate_minus_formal':{'logloss':cm['logloss']-bm['logloss'],'brier':cm['brier']-bm['brier'],'rps':cm['rps']-bm['rps'],'top1':cm['top1']-bm['top1'],'ece':cm['ece']-bm['ece']},'positive_test_leagues':pos_leagues,'worst_league_logloss_degradation':worst,'stability_gate_pass':stable,'per_league':per,'uncertainty':{'method':u['method'],'repetitions':int(u['repetitions']),'seed':int(u['seed']),'paired_logloss_effect_mean':mean,'paired_logloss_effect_sd':sd,'paired_logloss_effect_ci95':ci},'formal_v2_head':pre['formal_v2']['head'],'formal_v2_sole_baseline':True,'candidate_weight':0,'matrix_delta':0,'score_matrix_policy':pre['model']['score_matrix_policy'],'candidate_activation_allowed':False,'promotion_allowed':False,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'old_v3_code_parameters_weights_used':False,'experiment_budget_exhausted':True,'second_chance_route_allowed':False,'terminal_scientific_result':True}
    Path(args.receipt_out).write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n',encoding='utf-8'); return receipt

def main():
    p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd',required=True)
    q=sp.add_parser('predict'); q.add_argument('--prereg',type=Path,required=True); q.add_argument('--source-dir',type=Path,required=True); q.add_argument('--prelabel-dir',type=Path,required=True); q.add_argument('--development-dir',type=Path,required=True); q.add_argument('--data-dir',type=Path,required=True); q.add_argument('--predictions-out',type=Path,required=True); q.add_argument('--seal-out',type=Path,required=True)
    s=sp.add_parser('score'); s.add_argument('--prereg',type=Path,required=True); s.add_argument('--development-dir',type=Path,required=True); s.add_argument('--data-dir',type=Path,required=True); s.add_argument('--predictions',type=Path,required=True); s.add_argument('--seal',type=Path,required=True); s.add_argument('--receipt-out',type=Path,required=True)
    a=p.parse_args(); r=predict(a) if a.cmd=='predict' else score(a); print(json.dumps(r,sort_keys=True))
if __name__=='__main__': main()
