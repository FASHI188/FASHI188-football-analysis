#!/usr/bin/env python3
from __future__ import annotations
import argparse,bisect,csv,difflib,hashlib,json,math,random,re,unicodedata
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np

IDCOLS=['Season','Div','Date','Time','HomeTeam','AwayTeam']
METRICS=['xG','xGA','npxG','npxGA','xpts','npxGD','ppda','ppda_allowed','deep','deep_allowed']
ALIASES={
'man united':'manchester united','man city':'manchester city','wolves':'wolverhampton wanderers','newcastle':'newcastle united',"nott'm forest":'nottingham forest','nottm forest':'nottingham forest','brighton':'brighton and hove albion','leicester':'leicester city','leeds':'leeds united','west ham':'west ham united',
'dortmund':'borussia dortmund',"m'gladbach":'borussia monchengladbach','monchengladbach':'borussia monchengladbach','leverkusen':'bayer leverkusen','frankfurt':'eintracht frankfurt','mainz':'mainz 05','bremen':'werder bremen','koln':'fc cologne','cologne':'fc cologne','dusseldorf':'fortuna dusseldorf','bielefeld':'arminia bielefeld','greuther furth':'greuther fuerth',
'milan':'ac milan','verona':'hellas verona','spal':'spal 2013','ath madrid':'atletico madrid','ath bilbao':'athletic club','athletic bilbao':'athletic club','bilbao':'athletic club','sociedad':'real sociedad','betis':'real betis','alaves':'deportivo alaves','vallecano':'rayo vallecano','paris sg':'paris saint germain','st etienne':'saint etienne','st. etienne':'saint etienne'
}

def htxt(s):return hashlib.sha256(s.encode()).hexdigest()
def hfile(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()
def canonical_sha(o):return htxt(json.dumps(o,sort_keys=True,separators=(',',':')))
def set_sha(ids):return htxt('\n'.join(sorted(ids))+'\n')
def clip(p,lo=1e-8,hi=1-1e-8):return min(hi,max(lo,float(p)))
def logit(p):p=clip(p);return math.log(p/(1-p))
def entropy(q):return -sum(float(x)*math.log(max(float(x),1e-15)) for x in q)
def valid_odd(v):
    try:x=float(str(v).strip())
    except:return False
    return math.isfinite(x) and x>1.0
def devig3(a,b,c):
    x=np.array([1/float(a),1/float(b),1/float(c)],dtype=float);x/=x.sum();return x
def fd_identity(r):return '|'.join(str(r.get(c,'')).strip() for c in IDCOLS)
def league_norm(s):return re.sub(r'[^a-z0-9]','',str(s).lower())
def clean_name(s):
    s=unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().lower().replace('&',' and ')
    s=re.sub(r"[.'’`-]",' ',s);s=re.sub(r'\b(fc|cf|afc|ssc|calcio|football club)\b',' ',s);s=re.sub(r'\s+',' ',s).strip();return ALIASES.get(s,s)
def sim(a,b):
    a,b=clean_name(a),clean_name(b)
    if a==b:return 1.0
    ta,tb=set(a.split()),set(b.split());jac=len(ta&tb)/len(ta|tb) if ta|tb else 0.;seq=difflib.SequenceMatcher(None,a,b).ratio();return max(seq,.65*seq+.35*jac)
def parse_fd_date(s):
    for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(str(s).strip(),f).date()
        except:pass
    raise ValueError(s)
def parse_dt(date_s,time_s):
    ds=str(date_s).strip();ts=str(time_s).strip()
    for f in ('%d/%m/%Y %H:%M','%d/%m/%y %H:%M'):
        try:return datetime.strptime(f'{ds} {ts}',f)
        except:pass
    raise ValueError(f'{ds} {ts}')
def parse_us_date(s):
    t=str(s).strip().replace('Z','+00:00')
    try:return datetime.fromisoformat(t).date()
    except:pass
    for f in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d'):
        try:return datetime.strptime(t[:19],f).date()
        except:pass
    raise ValueError(s)
def finite(v):
    try:x=float(str(v).strip())
    except:return None
    return x if math.isfinite(x) else None

# Exact identity-mapping semantics frozen in R39H v2.
def build_understat(path,divmap):
    wanted={league_norm(v) for v in divmap.values()};rows=[];by_side=defaultdict(list);hist=defaultdict(list)
    with Path(path).open('r',encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);required={'id','league','season','club_name','home_away','date',*METRICS};hdr=set(rd.fieldnames or [])
        if not required<=hdr:raise RuntimeError(f'Understat missing {sorted(required-hdr)}')
        for r in rd:
            lg=league_norm(r['league'])
            if lg not in wanted:continue
            side=str(r['home_away']).strip().lower()[:1]
            if side not in {'h','a'}:continue
            d=parse_us_date(r['date']);vals={m:finite(r[m]) for m in METRICS}
            if any(v is None for v in vals.values()):continue
            x={'row_key':f"{r['id']}|{r['date']}|{side}|{r['club_name']}",'club_id':r['id'],'league':lg,'date':d,'side':side,'club':r['club'],**vals} if 'club' in r else {'row_key':f"{r['id']}|{r['date']}|{side}|{r['club_name']}",'club_id':r['id'],'league':lg,'date':d,'side':side,'club':r['club_name'],**vals}
            rows.append(x);by_side[(lg,d,side)].append(x);hist[(lg,clean_name(x['club']))].append(x)
    for k in hist:hist[k].sort(key=lambda z:(z['date'],z['row_key']))
    return rows,by_side,hist

def best_team(cands,target,used,cfg):
    rr=sorted([(sim(target,x['club']),x) for x in cands if x['row_key'] not in used],key=lambda z:(-z[0],z[1]['row_key']))
    if not rr:return None
    b=rr[0];second=rr[1][0] if len(rr)>1 else -1
    if b[0]<cfg['minimum_individual_team_similarity']:return None
    if len(rr)>1 and b[0]-second<cfg['minimum_best_vs_second_pair_margin']:return None
    return b
def choose_match(r,by_side,lg,cfg,used):
    d=parse_fd_date(r['Date'])
    for off in (0,-1,1):
        day=d+timedelta(days=off);h=best_team(by_side.get((lg,day,'h'),[]),r['HomeTeam'],used,cfg);a=best_team(by_side.get((lg,day,'a'),[]),r['AwayTeam'],used,cfg)
        if h is None or a is None:continue
        pair=(h[0]+a[0])/2
        if pair<cfg['minimum_pair_similarity']:continue
        return h[1],a[1],off
    return None

def load_market_rows(market_dir,pre,by_side,hist):
    divmap={'E0':'EPL','D1':'Bundesliga','I1':'Serie_A','SP1':'La_liga','F1':'Ligue_1'};cfg={'minimum_individual_team_similarity':.62,'minimum_pair_similarity':.78,'minimum_best_vs_second_pair_margin':.05}
    used=set();out=[];unmatched=0
    for p in sorted(Path(market_dir).glob('*.csv')):
        with p.open('r',encoding='utf-8-sig',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or []);forbidden={'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}
            if forbidden&hdr:raise RuntimeError('label columns in market input')
            for r in rd:
                if r.get('Div') not in divmap or not all(str(r.get(c,'')).strip() for c in IDCOLS):continue
                if not all(valid_odd(r.get(c,'')) for c in ('AvgCH','AvgCD','AvgCA')):continue
                lg=league_norm(divmap[r['Div']]);z=choose_match(r,by_side,lg,cfg,used)
                if z is None:unmatched+=1;continue
                h,a,off=z;used.update([h['row_key'],a['row_key']]);q=devig3(r['AvgCH'],r['AvgCD'],r['AvgCA']);ident=fd_identity(r)
                homehist=hist[(lg,clean_name(h['club']))];awayhist=hist[(lg,clean_name(a['club']))]
                hp=bisect.bisect_left([x['date'] for x in homehist],h['date']);ap=bisect.bisect_left([x['date'] for x in awayhist],a['date'])
                if hp<10 or ap<10:continue
                out.append({'identity':ident,'season':r['Season'],'div':r['Div'],'dt':parse_dt(r['Date'],r['Time']),'target_date':h['date'],'home_club':h['club'],'away_club':a['club'],'league':lg,'home_hist':homehist[:hp],'away_hist':awayhist[:ap],'qclose':q.tolist(),'day_offset':off})
    return out,unmatched

def mean_metric(xs,m,n):return float(np.mean([z[m] for z in xs[-n:]]))
def std_metric(xs,m,n):return float(np.std([z[m] for z in xs[-n:]],ddof=0))
def make_features(row):
    hh,ah=row['home_hist'],row['away_hist'];td=row['target_date'];maxdate=max(hh[-1]['date'],ah[-1]['date'])
    if not maxdate<td:raise RuntimeError('strict-lag violation')
    q=np.asarray(row['qclose']);context=[abs(float(q[0]-q[2])),entropy(q)];fund=[]
    cache={}
    for m in METRICS:
        for n in (5,10):
            hv=mean_metric(hh,m,n);av=mean_metric(ah,m,n);cache[(m,n,'h')]=hv;cache[(m,n,'a')]=av;fund.append(hv-av)
    fund += [cache[('xG',5,'h')]+cache[('xG',5,'a')],cache[('xGA',5,'h')]+cache[('xGA',5,'a')],cache[('xG',10,'h')]+cache[('xG',10,'a')],cache[('xGA',10,'h')]+cache[('xGA',10,'a')]]
    hs=std_metric(hh,'npxGD',5);as_=std_metric(ah,'npxGD',5);htrend=cache[('npxGD',5,'h')]-cache[('npxGD',10,'h')];atrend=cache[('npxGD',5,'a')]-cache[('npxGD',10,'a')]
    gap=abs(cache[('npxGD',10,'h')]-cache[('npxGD',10,'a')]);totalxg=cache[('xG',10,'h')]+cache[('xG',10,'a')]
    hr=min(30,max(0,(td-hh[-1]['date']).days));ar=min(30,max(0,(td-ah[-1]['date']).days))
    fund += [hs,as_,hs-as_,htrend,atrend,htrend-atrend,gap,totalxg/(1+gap),float(hr),float(ar),float(abs(hr-ar))]
    if len(fund)!=35 or len(context+fund)!=37:raise RuntimeError(f'feature count {len(fund)}')
    row['context']=context;row['fundamental']=context+fund;row['max_source_date']=str(maxdate);return row

def standardize_fit(X):
    mean=X.mean(0);std=X.std(0);std=np.where(std<1e-12,1.,std);return (X-mean)/std,mean,std
def fit_offset(X,y,offset,l2,max_iter=70,tol=1e-8):
    Z=np.column_stack([np.ones(len(X)),X]);b=np.zeros(Z.shape[1]);pen=np.zeros_like(b);pen[1:]=float(l2);conv=False;dm=None
    for it in range(1,max_iter+1):
        eta=np.asarray(offset)+Z@b;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None);grad=Z.T@(p-y)+pen*b;H=Z.T@(Z*w[:,None])+np.diag(pen)+np.eye(Z.shape[1])*1e-10;delta=np.linalg.solve(H,grad);b-=delta;dm=float(np.max(np.abs(delta)))
        if dm<tol:conv=True;break
    return b,{'iterations':it,'converged':conv,'coefficient_delta_max':dm}
def predict_offset(X,offset,b):
    Z=np.column_stack([np.ones(len(X)),X]);return 1/(1+np.exp(-np.clip(np.asarray(offset)+Z@b,-40,40)))
def threeway(q,pd):
    pd=clip(pd);h,a=float(q[0]),float(q[2]);s=h+a;return np.array([(1-pd)*h/s,pd,(1-pd)*a/s])
def probability_metrics(probs,actual):
    ll=br=rps=0.
    for p,y in zip(probs,actual):
        one=np.zeros(3);one[y]=1;ll-=math.log(max(float(p[y]),1e-15));br+=float(((p-one)**2).sum());rps+=.5*((float(p[0]-one[0]))**2+(float(p[0]+p[1]-one[0]-one[1]))**2)
    n=len(actual);return {'rows':n,'log_loss':ll/n,'brier':br/n,'rps':rps/n}
def binary_ll(pd,actual):
    y=np.array([1. if z==1 else 0. for z in actual]);p=np.clip(np.asarray(pd),1e-15,1-1e-15);return float(np.mean(-(y*np.log(p)+(1-y)*np.log(1-p))))
def auc(pd,actual):
    pos=[float(p) for p,y in zip(pd,actual) if y==1];neg=[float(p) for p,y in zip(pd,actual) if y!=1]
    if not pos or not neg:return None
    return sum(1 if a>b else .5 if a==b else 0 for a in pos for b in neg)/(len(pos)*len(neg))
def decision_metrics(pred,actual):
    n=len(actual);hits=sum(p==y for p,y in zip(pred,actual));dp=sum(p==1 for p in pred);ad=sum(y==1 for y in actual);tp=sum(p==1 and y==1 for p,y in zip(pred,actual));pr=tp/dp if dp else 0.;rc=tp/ad if ad else 0.;f1=2*pr*rc/(pr+rc) if pr+rc else 0.;return {'rows':n,'hits':hits,'accuracy':hits/n,'predicted_draw_count':dp,'actual_draw_count':ad,'draw_true_positive':tp,'draw_precision':pr,'draw_recall':rc,'draw_f1':f1}
def dscore(p):return float(p[1]-max(p[0],p[2]))
def market_pred(p):return int(np.argmax(np.asarray(p)))
def apply_boundary(p,thr):return 1 if dscore(p)>=thr else (0 if p[0]>=p[2] else 2)
def per_div_ll(rows,probs,actual):
    g=defaultdict(list)
    for r,p,y in zip(rows,probs,actual):g[r['div']].append((p,y))
    return {d:probability_metrics([x[0] for x in v],[x[1] for x in v])['log_loss'] for d,v in sorted(g.items())}

def read_labels(raw_dir,ids,holdout=False):
    labels={};access=0
    pats='2425_*.csv' if holdout else '*.csv'
    for p in sorted(Path(raw_dir).glob(pats)):
        season=p.stem.split('_',1)[0]
        if not holdout and season=='2425':continue
        with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            rd=csv.DictReader(f)
            for r in rd:
                z=dict(r);z['Season']=season;ident=fd_identity(z)
                if ident not in ids:continue
                v=str(r.get('FTR','')).strip()
                if v not in {'H','D','A'}:raise RuntimeError(f'bad label {ident}')
                labels[ident]={'H':0,'D':1,'A':2}[v];access+=1
    return labels,access

def model_predict(rows,key,b,mean,std):
    X=np.array([r[key] for r in rows],dtype=float);X=(X-mean)/std;off=np.array([logit(r['qclose'][1]) for r in rows]);pd=predict_offset(X,off,b);probs=[threeway(r['qclose'],p) for r,p in zip(rows,pd)];return pd,probs

def fit_context(rows,labels):
    X=np.array([r['context'] for r in rows]);y=np.array([1. if labels[r['identity']]==1 else 0. for r in rows]);off=np.array([logit(r['qclose'][1]) for r in rows]);Xs,mean,std=standardize_fit(X);b,d=fit_offset(Xs,y,off,1.0);return b,d,mean,std
def choose_fundamental(fit,val,labels,cands):
    X=np.array([r['fundamental'] for r in fit]);y=np.array([1. if labels[r['identity']]==1 else 0. for r in fit]);off=np.array([logit(r['qclose'][1]) for r in fit]);Xs,mean,std=standardize_fit(X);actual=[labels[r['identity']] for r in val];out=[]
    for l2 in cands:
        b,d=fit_offset(Xs,y,off,float(l2));pd,probs=model_predict(val,'fundamental',b,mean,std);out.append({'l2':float(l2),'beta':b,'diag':d,'mean':mean,'std':std,'HDA':probability_metrics(probs,actual),'binary_draw_log_loss':binary_ll(pd,actual),'draw_auc':auc(pd,actual)})
    best=sorted(out,key=lambda x:(x['HDA']['log_loss'],x['binary_draw_log_loss'],x['l2']))[0];return best,out

def choose_policy(rows,probs,actual,cfg):
    market=[market_pred(r['qclose']) for r in rows];base=decision_metrics(market,actual);prev=sum(y==1 for y in actual)/len(actual);scores=[dscore(p) for p in probs];rank=sorted(range(len(rows)),key=lambda i:(-scores[i],rows[i]['identity']));cands=[]
    for cov in cfg['coverage_candidates']:
        k=max(1,int(math.ceil(float(cov)*len(rows))));sel=set(rank[:k]);pred=[1 if i in sel else (0 if p[0]>=p[2] else 2) for i,p in enumerate(probs)];m=decision_metrics(pred,actual);ok=m['accuracy']>base['accuracy'] and m['draw_precision']>=prev+cfg['draw_precision_over_policy_prevalence_min'] and m['draw_f1']>=cfg['draw_f1_min'];cands.append({'coverage':cov,'k':k,'threshold':min(scores[i] for i in sel),'metrics':m,'eligible':ok})
    good=[c for c in cands if c['eligible']];chosen=sorted(good,key=lambda c:(-c['metrics']['accuracy'],-c['metrics']['draw_f1'],-c['metrics']['draw_precision'],c['coverage']))[0] if good else None;return chosen,cands,base,prev
def bootstrap(base,cand,actual,n,seed):
    rnd=random.Random(seed);N=len(actual);v=[]
    for _ in range(n):
        s=0
        for j in range(N):i=rnd.randrange(N);s+=int(cand[i]==actual[i])-int(base[i]==actual[i])
        v.append(s/N)
    v.sort();q=lambda x:v[min(len(v)-1,max(0,int(x*(len(v)-1))))];return {'mean':sum(v)/n,'p05':q(.05),'p50':q(.5),'p95':q(.95),'samples':n,'seed':seed}
def self_test():
    X=np.linspace(-2,2,30)[:,None];off=np.zeros(30);y=(X[:,0]>.2).astype(float);Xs,me,st=standardize_fit(X);b,d=fit_offset(Xs,y,off,1.0);assert d['converged'] and predict_offset(Xs,off,b)[0]<predict_offset(Xs,off,b)[-1];print('PASS_R39H_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--market-dir',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--understat',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    pre=json.loads(a.prereg.read_text());a.out_dir.mkdir(parents=True,exist_ok=True);divmap={'E0':'EPL','D1':'Bundesliga','I1':'Serie_A','SP1':'La_liga','F1':'Ligue_1'};usrows,by_side,hist=build_understat(a.understat,divmap);rows,unmatched=load_market_rows(a.market_dir,pre,by_side,hist);rows=[make_features(r) for r in rows]
    train=sorted([r for r in rows if r['season']!='2425'],key=lambda r:(r['dt'],r['identity']));hold=[r for r in rows if r['season']=='2425' and r['target_date']<=datetime.strptime(pre['source_binding']['holdout_window_end'],'%Y-%m-%d').date()]
    if len(train)!=pre['source_binding']['history_eligible_preholdout'] or len(hold)!=pre['source_binding']['history_eligible_2425']:raise RuntimeError(f'identity drift train={len(train)} hold={len(hold)}')
    fixed=sorted(hold,key=lambda r:htxt(f"{pre['source_binding']['fixed100_seed']}|{r['identity']}"))[:pre['source_binding']['fixed100_rows']];sha=set_sha([r['identity'] for r in fixed])
    if sha!=pre['source_binding']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {sha}')
    nfit=pre['chronological_design']['fit_rows'];nval=pre['chronological_design']['validation_rows'];npol=pre['chronological_design']['policy_rows'];fit=train[:nfit];val=train[nfit:nfit+nval];policy=train[nfit+nval:nfit+nval+npol]
    if len(fit)+len(val)+len(policy)!=len(train):raise RuntimeError('split drift')
    labels,training_access=read_labels(a.raw_dir,{r['identity'] for r in train},False)
    if len(labels)!=len(train):raise RuntimeError(f'training label count {len(labels)}')
    actual_val=[labels[r['identity']] for r in val];market_probs=[np.asarray(r['qclose']) for r in val];market_pd=[r['qclose'][1] for r in val];market_val={'HDA':probability_metrics(market_probs,actual_val),'binary_draw_log_loss':binary_ll(market_pd,actual_val),'draw_auc':auc(market_pd,actual_val)}
    cb,cd,cm,cs=fit_context(fit,labels);cpd,cprobs=model_predict(val,'context',cb,cm,cs);context_val={'HDA':probability_metrics(cprobs,actual_val),'binary_draw_log_loss':binary_ll(cpd,actual_val),'draw_auc':auc(cpd,actual_val)}
    best,cands=choose_fundamental(fit,val,labels,pre['model']['fundamental_l2_candidates']);fpd,fprobs=model_predict(val,'fundamental',best['beta'],best['mean'],best['std']);fd=per_div_ll(val,fprobs,actual_val);cdv=per_div_ll(val,cprobs,actual_val);wins=sum(fd[d]<cdv[d] for d in sorted(set(fd)&set(cdv)))
    gates={'HDA_better_than_raw_market':best['HDA']['log_loss']<market_val['HDA']['log_loss'],'binary_draw_LL_better_than_raw_market':best['binary_draw_log_loss']<market_val['binary_draw_log_loss'],'HDA_better_than_context':best['HDA']['log_loss']<context_val['HDA']['log_loss'],'binary_draw_LL_better_than_context':best['binary_draw_log_loss']<context_val['binary_draw_log_loss'],'Draw_AUC_better_than_context':best['draw_auc']>context_val['draw_auc'],'per_division_HDA_wins_ge3':wins>=3};validation_pass=all(gates.values())
    freeze={'schema_version':pre['schema_version'],'prereg_sha256':hfile(a.prereg),'fixed100_identity_sha256':sha,'training_rows':len(train),'fit_rows':len(fit),'validation_rows':len(val),'policy_rows':len(policy),'training_labels_accessed':training_access,'holdout_labels_accessed_before_freeze':0,'selected_l2':best['l2'],'context_diag':cd,'fundamental_diag':best['diag'],'validation':{'raw_market':market_val,'context_baseline':context_val,'fundamental':{'HDA':best['HDA'],'binary_draw_log_loss':best['binary_draw_log_loss'],'draw_auc':best['draw_auc']},'per_division_fundamental':fd,'per_division_context':cdv,'per_division_wins':wins,'gates':gates,'passed':validation_pass},'strict_lag_audit':{'rows_checked':len(rows),'violations':sum(not (datetime.fromisoformat(r['max_source_date']).date()<r['target_date']) for r in rows)},'unmatched_market_rows':unmatched}
    if not validation_pass:
        result={'status':pre['model']['if_validation_gate_fails'],'freeze':freeze,'holdout_labels_accessed':0,'formal_weight':0};(a.out_dir/'freeze_receipt_r39h.json').write_text(json.dumps(freeze,indent=2));(a.out_dir/'r39h_result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return
    actual_pol=[labels[r['identity']] for r in policy];ppd,pprobs=model_predict(policy,'fundamental',best['beta'],best['mean'],best['std']);chosen,pcands,pbase,prev=choose_policy(policy,pprobs,actual_pol,pre['policy']);freeze['policy']={'prevalence':prev,'market':pbase,'candidates':pcands,'chosen':chosen}
    if chosen is None:
        result={'status':pre['policy']['if_no_lane'],'freeze':freeze,'holdout_labels_accessed':0,'formal_weight':0};(a.out_dir/'freeze_receipt_r39h.json').write_text(json.dumps(freeze,indent=2));(a.out_dir/'r39h_result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return
    freeze['parameter_sha256']=canonical_sha({'context_beta':cb.tolist(),'context_mean':cm.tolist(),'context_std':cs.tolist(),'fund_beta':best['beta'].tolist(),'fund_mean':best['mean'].tolist(),'fund_std':best['std'].tolist(),'threshold':chosen['threshold'],'l2':best['l2']});(a.out_dir/'freeze_receipt_r39h.json').write_text(json.dumps(freeze,indent=2))
    fixed_ids={r['identity'] for r in fixed};hold_labels,hold_access=read_labels(a.raw_dir,fixed_ids,True)
    if hold_access!=100 or len(hold_labels)!=100:raise RuntimeError(f'holdout access {hold_access}')
    actual=[hold_labels[r['identity']] for r in fixed];hpd,hprobs=model_predict(fixed,'fundamental',best['beta'],best['mean'],best['std']);hcontext_pd,hcontext_probs=model_predict(fixed,'context',cb,cm,cs);market=[np.asarray(r['qclose']) for r in fixed];market_dec=[market_pred(p) for p in market];cand_dec=[apply_boundary(p,chosen['threshold']) for p in hprobs];hm=probability_metrics(hprobs,actual);hcm=probability_metrics(hcontext_probs,actual);hraw=probability_metrics(market,actual);dm=decision_metrics(cand_dec,actual);md=decision_metrics(market_dec,actual);hgate={'accuracy_better_than_raw_market':dm['accuracy']>md['accuracy'],'predicted_draw_count_5_25':5<=dm['predicted_draw_count']<=25,'draw_precision_ge_035':dm['draw_precision']>=.35,'draw_recall_ge_010':dm['draw_recall']>=.10,'draw_f1_ge_015':dm['draw_f1']>=.15,'HDA_LogLoss_nonworse_than_raw_market':hm['log_loss']<=hraw['log_loss'],'HDA_LogLoss_better_than_context':hm['log_loss']<hcm['log_loss'],'binary_Draw_LogLoss_better_than_raw_market':binary_ll(hpd,actual)<binary_ll([p[1] for p in market],actual),'Draw_AUC_better_than_context':auc(hpd,actual)>auc(hcontext_pd,actual)};passed=all(hgate.values());result={'status':pre['holdout']['pass_status'] if passed else pre['holdout']['fail_status'],'holdout_labels_accessed':hold_access,'actual_HDA_counts':{'H':sum(y==0 for y in actual),'D':sum(y==1 for y in actual),'A':sum(y==2 for y in actual)},'raw_market':{'HDA':hraw,'binary_draw_log_loss':binary_ll([p[1] for p in market],actual),'draw_auc':auc([p[1] for p in market],actual),'decision':md},'context_baseline':{'HDA':hcm,'binary_draw_log_loss':binary_ll(hcontext_pd,actual),'draw_auc':auc(hcontext_pd,actual)},'fundamental':{'HDA':hm,'binary_draw_log_loss':binary_ll(hpd,actual),'draw_auc':auc(hpd,actual),'decision':dm},'paired_bootstrap_accuracy_delta':bootstrap(market_dec,cand_dec,actual,pre['holdout']['bootstrap_samples'],pre['holdout']['bootstrap_seed']),'holdout_gates':hgate,'passed':passed,'fixed100_identity_sha256':sha,'parameter_sha256':freeze['parameter_sha256'],'formal_weight':0};(a.out_dir/'r39h_result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
