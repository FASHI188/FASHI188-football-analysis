#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,hashlib,io,json,math,platform,zipfile
from collections import Counter
from pathlib import Path
import numpy as np, scipy, sklearn
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[2] if 'football-data' in str(Path(__file__)) else Path.cwd()
R=ROOT/'football-data'/'research'
OUT=R/'r55b_fixed_target300_result_20260816.json'
LEAGUES=['England: Premier League','England: Championship','England: League One','England: League Two']
EXPECTED_TRAIN_N=19422
EXPECTED_CLASS_COUNTS={0:1522,1:3745,2:4914,3:4231,4:2714,5:1421,6:569,7:306}
EXPECTED_TARGET_N=300
EXPECTED_TARGET_SHA='7f874277290f3c9664425f80e5281c18feddea85ff7306ed8ae64e6f6d949ffb'
FROZEN_ALPHA=0.27198933241466033
MAX_ITER=5000; EPS=1e-12; BOOT_N=2000; BOOT_SEED=20260814
R55={'dt_base_ll':1.7969672498659899,'dt_hard_ll':1.7978930920719418,'dt_base_rps':0.1196302491961927,'dt_hard_rps':0.11957135005634344,'hda_base_ll':1.047891107276976,'hda_hard_ll':1.0485497642705877,'hda_base_brier':0.6313617681731262,'hda_hard_brier':0.6318316924246979,'draw_base_ll':0.6049476110376673,'draw_hard_ll':0.6056062680312786,'draw_base_brier':0.20737665810016812,'draw_hard_brier':0.2076447360152388,'draw_base_auc':0.5073434819897085,'draw_hard_auc':0.48986921097770153}

def fodd(v):
    try:
        x=float(v); return x if math.isfinite(x) and x>1 else None
    except: return None

def market_probs(h,d,a):
    vals=[fodd(h),fodd(d),fodd(a)]
    if any(x is None for x in vals): return None
    x=np.array([1/vals[0],1/vals[1],1/vals[2]],float); return x/x.sum()

def features(q,league):
    qh,qd,qa=map(float,q); strength=math.log(qh/qa); entropy=-sum(x*math.log(x) for x in (qh,qd,qa)); pmax=max(qh,qd,qa); openness=math.log(math.sqrt(qh*qa)/qd)
    return [strength,qd,entropy,pmax,openness,*[1.0 if league==x else 0.0 for x in LEAGUES]]

def member(zf,basename):
    hits=[n for n in zf.namelist() if Path(n).name==basename]
    if len(hits)!=1: raise SystemExit(f'ARCHIVE_MEMBER_GATE_FAIL {basename}: {hits}')
    return hits[0]

def load_training(archive):
    X=[]; y=[]; draw=[]; dates=[]
    with zipfile.ZipFile(archive) as zf:
        name=member(zf,'closing_odds.csv.gz')
        with zf.open(name) as raw,gzip.GzipFile(fileobj=raw) as gz,io.TextIOWrapper(gz,encoding='utf-8',newline='') as text:
            rd=csv.DictReader(text)
            for r in rd:
                lg=r.get('league',''); d=r.get('match_date','')[:10]
                if lg not in LEAGUES or not d or d>'2015-06-30': continue
                q=market_probs(r.get('avg_odds_home_win'),r.get('avg_odds_draw'),r.get('avg_odds_away_win'))
                if q is None: continue
                try: hs=int(float(r['home_score'])); aas=int(float(r['away_score']))
                except: continue
                X.append(features(q,lg)); y.append(min(hs+aas,7)); draw.append(int(hs==aas)); dates.append(d)
    X=np.asarray(X,float); y=np.asarray(y,int); draw=np.asarray(draw,int); counts=dict(sorted(Counter(map(int,y)).items()))
    if len(y)!=EXPECTED_TRAIN_N or counts!=EXPECTED_CLASS_COUNTS or min(dates)!='2005-01-01' or max(dates)!='2015-05-25': raise SystemExit(f'BASELINE_TRAIN_FINGERPRINT_FAIL n={len(y)} counts={counts} dates={min(dates)}..{max(dates)}')
    return X,y,draw

def fit_lr(X,y):
    sc=StandardScaler(); Xs=sc.fit_transform(X); clf=LogisticRegression(penalty='l2',C=1.0,solver='lbfgs',max_iter=MAX_ITER,tol=1e-8,class_weight=None); clf.fit(Xs,y)
    if int(np.max(clf.n_iter_))>=MAX_ITER: raise SystemExit('MODEL_CONVERGENCE_FAIL')
    return sc,clf

def fit_models(X,y,draw):
    scT,clfT=fit_lr(X,y)
    if list(map(int,clfT.classes_))!=list(range(8)): raise SystemExit('DIRECT_T_CLASS_ORDER_FAIL')
    cond={}
    for t in (2,4,6,7):
        idx=np.where(y==t)[0]; sc,clf=fit_lr(X[idx],draw[idx]); cond[t]=(sc,clf,int(len(idx)),int(draw[idx].sum()))
    return scT,clfT,cond

def load_target(recovery_json,archive):
    obj=json.loads(Path(recovery_json).read_text(encoding='utf-8')); rows=obj.get('target') or []
    if obj.get('status')!='PASS_COMPLETE_INPUT_RECOVERY' or len(rows)!=EXPECTED_TARGET_N: raise SystemExit('TARGET_RECOVERY_GATE_FAIL')
    rows=sorted(rows,key=lambda r:(r['match_datetime'],int(r['match_id'])))
    sha=hashlib.sha256('\n'.join(r['match_id'] for r in rows).encode()).hexdigest()
    if sha!=EXPECTED_TARGET_SHA: raise SystemExit(f'TARGET_HASH_FAIL {sha}')
    wanted={r['match_id'] for r in rows}; scores={}
    with zipfile.ZipFile(archive) as zf:
        name=member(zf,'odds_series_b_matches.csv.gz')
        with zf.open(name) as raw,gzip.GzipFile(fileobj=raw) as gz,io.TextIOWrapper(gz,encoding='latin1',newline='') as text:
            rd=csv.DictReader(text); rd.fieldnames=[x.strip() for x in (rd.fieldnames or [])]
            req={'match_id','score'}
            if not req.issubset(set(rd.fieldnames or [])): raise SystemExit(f'TARGET_SCORE_HEADER_FAIL {rd.fieldnames[:20]}')
            for r0 in rd:
                r={k:(v.strip() if isinstance(v,str) else v) for k,v in r0.items()}; mid=r['match_id']
                if mid not in wanted: continue
                parts=r['score'].split(':')
                if len(parts)!=2: raise SystemExit(f'TARGET_SCORE_PARSE_FAIL {mid}={r["score"]}')
                scores[mid]=(int(parts[0].strip()),int(parts[1].strip()))
    if set(scores)!=wanted: raise SystemExit(f'TARGET_LABEL_COVERAGE_FAIL got={len(scores)} missing={sorted(wanted-set(scores))[:10]}')
    for r in rows:
        hs,aas=scores[r['match_id']]; r['home_score']=hs; r['away_score']=aas; r['total_goals']=hs+aas; r['t_class']=min(hs+aas,7); r['hda']='H' if hs>aas else ('A' if hs<aas else 'D')
    return rows,sha

def blend_over(pold,qou,a):
    pold=np.clip(np.asarray(pold,float),EPS,1-EPS); qou=np.clip(np.asarray(qou,float),EPS,1-EPS); return expit(logit(pold)+float(a)*(logit(qou)-logit(pold)))

def apply_alpha(p8,qou,a):
    p=np.asarray(p8,float); over_old=p[:,3:].sum(1); over_new=blend_over(over_old,qou,a); under_new=1-over_new
    out=p.copy(); out[:,:3]*=(under_new/p[:,:3].sum(1))[:,None]; out[:,3:]*=(over_new/p[:,3:].sum(1))[:,None]
    return out

def conditional_draw_matrix(X,cond):
    n=len(X); cd=np.zeros((n,8),float); cd[:,0]=1.0
    for t,(sc,clf,_,_) in cond.items(): cd[:,t]=clf.predict_proba(sc.transform(X))[:,list(clf.classes_).index(1)]
    return cd

def hda_from_t(p8,cd,qha):
    pd=(p8*cd).sum(1); qh=qha[:,0]; qa=qha[:,2]; split=qh+qa; ph=(1-pd)*qh/split; pa=(1-pd)*qa/split; return np.column_stack([ph,pd,pa])
def mc_ll(y,p): return float(-np.mean(np.log(np.clip(p[np.arange(len(y)),y],EPS,1))))
def rps(y,p):
    cum=np.cumsum(p[:,:-1],axis=1); obs=(y[:,None] <= np.arange(7)[None,:]).astype(float)
    return float(np.mean(np.sum((cum-obs)**2,axis=1)/7.0))
def hda_metrics(y,p):
    ll=float(-np.mean(np.log(np.clip(p[np.arange(len(y)),y],EPS,1)))); oh=np.eye(3)[y]; brier=float(np.mean(np.sum((p-oh)**2,axis=1))); pred=np.argmax(p,1); acc=float(np.mean(pred==y)); dn=int(np.sum(pred==1)); hits=int(np.sum((pred==1)&(y==1))); actual=int(np.sum(y==1)); return {'ll':ll,'brier':brier,'acc':acc,'draw_n':dn,'draw_hits':hits,'draw_precision':None if dn==0 else hits/dn,'draw_recall':hits/actual if actual else None}
def draw_metrics(y,pd):
    yd=(y==1).astype(int); pd=np.clip(pd,EPS,1-EPS); ll=float(-np.mean(yd*np.log(pd)+(1-yd)*np.log(1-pd))); br=float(np.mean((pd-yd)**2)); auc=float(roc_auc_score(yd,pd)); return {'log_loss':ll,'brier':br,'auc':auc}
def boot(delta):
    rng=np.random.default_rng(BOOT_SEED); n=len(delta); vals=np.empty(BOOT_N)
    for i in range(BOOT_N): vals[i]=float(np.mean(delta[rng.integers(0,n,n)]))
    q=np.percentile(vals,[5,50,95]); return {'p05':float(q[0]),'median':float(q[1]),'p95':float(q[2]),'p_lt_0':float(np.mean(vals<0))}
def assert_close(name,a,b,tol=2e-9):
    if abs(float(a)-float(b))>tol: raise SystemExit(f'R55_ENDPOINT_REPRO_FAIL {name}: expected={b} actual={a} delta={a-b}')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--archive',required=True); ap.add_argument('--recovery-json',required=True); ap.add_argument('--alpha-result',required=True); args=ap.parse_args()
    frozen=json.loads(Path(args.alpha_result).read_text()); a=float(frozen['calibration']['full_fit']['alpha'])
    if abs(a-FROZEN_ALPHA)>1e-15 or frozen.get('target_application_performed') is not False: raise SystemExit(f'FROZEN_ALPHA_GATE_FAIL {a}')
    Xtr,ytr,dtr=load_training(args.archive); scT,clfT,cond=fit_models(Xtr,ytr,dtr); rows,sha=load_target(args.recovery_json,args.archive)
    X=np.asarray([features((r['qH'],r['qD'],r['qA']),r['league']) for r in rows],float); qou=np.asarray([r['q_over_2_5'] for r in rows],float); qha=np.asarray([[r['qH'],r['qD'],r['qA']] for r in rows],float)
    yt=np.asarray([r['t_class'] for r in rows],int); yh=np.asarray([0 if r['hda']=='H' else (1 if r['hda']=='D' else 2) for r in rows],int)
    base=clfT.predict_proba(scT.transform(X)); cd=conditional_draw_matrix(X,cond); hard=apply_alpha(base,qou,1.0); part=apply_alpha(base,qou,a)
    hb=hda_from_t(base,cd,qha); hh=hda_from_t(hard,cd,qha); hp=hda_from_t(part,cd,qha)
    assert_close('dt_base_ll',mc_ll(yt,base),R55['dt_base_ll']); assert_close('dt_hard_ll',mc_ll(yt,hard),R55['dt_hard_ll'])
    assert_close('dt_base_rps',rps(yt,base),R55['dt_base_rps']); assert_close('dt_hard_rps',rps(yt,hard),R55['dt_hard_rps'])
    mb=hda_metrics(yh,hb); mh=hda_metrics(yh,hh); mp=hda_metrics(yh,hp)
    db=draw_metrics(yh,hb[:,1]); dh=draw_metrics(yh,hh[:,1]); dp=draw_metrics(yh,hp[:,1])
    for nm,val in [('hda_base_ll',mb['ll']),('hda_hard_ll',mh['ll']),('hda_base_brier',mb['brier']),('hda_hard_brier',mh['brier']),('draw_base_ll',db['log_loss']),('draw_hard_ll',dh['log_loss']),('draw_base_brier',db['brier']),('draw_hard_brier',dh['brier']),('draw_base_auc',db['auc']),('draw_hard_auc',dh['auc'])]: assert_close(nm,val,R55[nm])
    dtb=mc_ll(yt,base); dtp=mc_ll(yt,part); rpsb=rps(yt,base); rpsp=rps(yt,part)
    per_dt=-np.log(np.clip(part[np.arange(len(yt)),yt],EPS,1))+np.log(np.clip(base[np.arange(len(yt)),yt],EPS,1))
    per_h=-np.log(np.clip(hp[np.arange(len(yh)),yh],EPS,1))+np.log(np.clip(hb[np.arange(len(yh)),yh],EPS,1))
    yd=(yh==1).astype(float); per_d=-(yd*np.log(np.clip(hp[:,1],EPS,1))+(1-yd)*np.log(np.clip(1-hp[:,1],EPS,1)))+(yd*np.log(np.clip(hb[:,1],EPS,1))+(1-yd)*np.log(np.clip(1-hb[:,1],EPS,1)))
    if dtp >= dtb:
        ruling='FAIL_R55B_PARTIAL_OU_DIRECT_T_NO_IMPROVEMENT'
    elif mp['ll']<=mb['ll'] and dp['log_loss']<=db['log_loss'] and dp['brier']<=db['brier']:
        ruling='PASS_R55B_PARTIAL_OU_CLEAN_INCREMENT_RESEARCH_ONLY'
    else:
        ruling='R55B_PARTIAL_OU_MIXED_TARGET_RESULT_REQUIRES_PREREG_INTERPRETATION'
    payload = {
        'schema':'R55B_FIXED_TARGET300_APPLY_ONCE_RESULT_R1','classification':'RETROSPECTIVE_RESEARCH_ONLY_FORMAL_WEIGHT_0','formal_weight':0,'operation_id':'R55B_FIXED_TARGET300_APPLY_ONCE_20260816',
        'target':{'n':len(rows),'sample_id_sha256':sha,'time_window':[rows[0]['match_datetime'],rows[-1]['match_datetime']],'labels_opened_for_this_authorized_application':True,'actual_hda_counts':dict(Counter(r['hda'] for r in rows)),'actual_t_counts':{str(k):int(v) for k,v in sorted(Counter(map(int,yt)).items())},'actual_draws':int(np.sum(yh==1))},
        'frozen_alpha':a,'runtime':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'sklearn':sklearn.__version__},
        'training':{'n':len(ytr),'class_counts':{str(k):int(v) for k,v in sorted(Counter(map(int,ytr)).items())},'conditional_draw_counts':{str(t):{'n':v[2],'draws':v[3]} for t,v in cond.items()},'direct_t_iterations':int(np.max(clfT.n_iter_)),'conditional_iterations':{str(t):int(np.max(v[1].n_iter_)) for t,v in cond.items()}},
        'endpoint_reproduction':{'r55_baseline_and_hard_projection_metrics_match':True,'tolerance':2e-9},
        'metrics':{
            'direct_t_baseline':{'log_loss':dtb,'rps':rpsb,'top1_accuracy':float(np.mean(np.argmax(base,1)==yt))},'direct_t_partial':{'log_loss':dtp,'rps':rpsp,'top1_accuracy':float(np.mean(np.argmax(part,1)==yt))},
            'direct_t_delta_partial_minus_baseline':{'log_loss':dtp-dtb,'rps':rpsp-rpsb,'top1_accuracy_pp':100*(float(np.mean(np.argmax(part,1)==yt))-float(np.mean(np.argmax(base,1)==yt)))},
            'hda_baseline':mb,'hda_partial':mp,'hda_delta_partial_minus_baseline':{'log_loss':mp['ll']-mb['ll'],'brier':mp['brier']-mb['brier'],'accuracy_pp':100*(mp['acc']-mb['acc'])},
            'draw_baseline':db,'draw_partial':dp,'draw_delta_partial_minus_baseline':{'log_loss':dp['log_loss']-db['log_loss'],'brier':dp['brier']-db['brier'],'auc':dp['auc']-db['auc']},
            'ou_group_diagnostic':{'actual':float(np.mean(yt>=3)),'baseline_ll':float(-np.mean((yt>=3)*np.log(np.clip(base[:,3:].sum(1),EPS,1))+(yt<3)*np.log(np.clip(base[:,:3].sum(1),EPS,1)))),'partial_ll':float(-np.mean((yt>=3)*np.log(np.clip(part[:,3:].sum(1),EPS,1))+(yt<3)*np.log(np.clip(part[:,:3].sum(1),EPS,1)))),'baseline_mean':float(np.mean(base[:,3:].sum(1))),'partial_mean':float(np.mean(part[:,3:].sum(1))),'ou_mean':float(np.mean(qou))}},
        'bootstrap90':{'direct_t_ll_partial_minus_baseline':boot(per_dt),'hda_ll_partial_minus_baseline':boot(per_h),'draw_ll_partial_minus_baseline':boot(per_d)},
        'audit':{'alpha_refit':False,'alpha_search_on_target':False,'target_resampled':False,'sample_hash_changed':False,'forced_draw':False,'threshold_search':False,'topk_override':False,'class_weight':False,'confirmation_2025_26_access':0,'formal_model_data_config_current_changes':0},
        'ruling':ruling,'interpretation':'Frozen pre-target alpha was applied exactly once to the original R55 frozen 300. Promotion remains prohibited; this result only tests whether partial independent-OU residual improves the preregistered retrospective target.'}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8'); print(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True))
if __name__=='__main__': main()
