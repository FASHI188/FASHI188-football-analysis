#!/usr/bin/env python3
from __future__ import annotations
import csv,gzip,hashlib,io,json,math,urllib.request
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SOURCE_URL='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz'
ROOT=Path(__file__).resolve().parent; DATA=ROOT/'data'; RESULTS=ROOT/'results'
N=20000; PART=5000; MAXG=12
GLOBAL_H=1.45; GLOBAL_A=1.20; COMP_PRIOR=30.0; TEAM_PRIOR=6.0
HALF=240.0; LR=0.06; BLEND=0.50
FIELDS=['date','game_id','competition_id','season','competition_type','home_club_id','away_club_id','home_club_name','away_club_name','home_goals','away_goals']

def clamp(x,a,b): return min(b,max(a,x))
def goal(x):
    try:v=float(str(x).strip())
    except:return None
    return int(round(v)) if math.isfinite(v) and v>=0 and abs(v-round(v))<1e-9 else None
def cid(x):
    s=str(x or '').strip()
    if s.endswith('.0') and s[:-2].isdigit():s=s[:-2]
    return '' if s.lower() in {'','nan','none','null'} else s
def pick(r,*xs):
    for x in xs:
        v=str(r.get(x) or '').strip()
        if v:return v
    return ''
def sha(path):
    h=hashlib.sha256();
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()

def freeze():
    DATA.mkdir(parents=True,exist_ok=True)
    req=urllib.request.Request(SOURCE_URL,headers={'User-Agent':'football3-research/2.0'})
    with urllib.request.urlopen(req,timeout=300) as r:raw=r.read()
    rd=csv.DictReader(io.StringIO(gzip.decompress(raw).decode('utf-8-sig',errors='replace')))
    valid=[];bad=Counter();seen=set();source_rows=0
    for r in rd:
        source_rows+=1;ds=pick(r,'date')[:10]
        try:date.fromisoformat(ds)
        except:bad['date']+=1;continue
        gid=pick(r,'game_id');comp=pick(r,'competition_id');typ=pick(r,'competition_type');h=cid(pick(r,'home_club_id'));a=cid(pick(r,'away_club_id'));hg=goal(pick(r,'home_club_goals'));ag=goal(pick(r,'away_club_goals'))
        if not gid or not comp or not h or not a or h==a or gid in seen:bad['identity']+=1;continue
        if hg is None or ag is None:bad['score']+=1;continue
        if typ=='national_team_competition':bad['national_team']+=1;continue
        seen.add(gid);valid.append({'date':ds,'game_id':gid,'competition_id':comp,'season':pick(r,'season'),'competition_type':typ,'home_club_id':h,'away_club_id':a,'home_club_name':pick(r,'home_club_name'),'away_club_name':pick(r,'away_club_name'),'home_goals':hg,'away_goals':ag})
    valid.sort(key=lambda r:(r['date'],r['game_id']))
    if len(valid)<40000:raise RuntimeError('need >=40000 valid rows')
    chosen=valid[-40000:-20000]
    metas=[]
    for i,s in enumerate(range(0,N,PART),1):
        p=DATA/f'matches_r2_20000_part{i:02d}.csv';part=chosen[s:s+PART]
        with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(part)
        metas.append({'path':str(p.relative_to(ROOT)),'rows':len(part),'sha256':sha(p)})
    m={'schema_version':'football3-top1-20k-r2','status':'FROZEN_DISJOINT_20000','source_url':SOURCE_URL,'source_compressed_sha256':hashlib.sha256(raw).hexdigest(),'source_rows':source_rows,'valid_club_rows':len(valid),'selection':'valid[-40000:-20000], immediately preceding and disjoint from R1 latest 20000','snapshot_rows':N,'first_date':chosen[0]['date'],'last_date':chosen[-1]['date'],'parts':metas,'excluded_counts':dict(bad),'odds_used':False,'market_columns_persisted':False,'overlap_with_r1_selection':False,'same_date_update_allowed':False}
    (DATA/'source_manifest_r2.json').write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(m,indent=2));return m

@dataclass
class V: n:int=0;gf:float=0.;ga:float=0.
@dataclass
class L: attack:float=0.;defence:float=0.;matches:int=0;last:date|None=None
class State:
    def __init__(self):self.cn=Counter();self.ch=defaultdict(float);self.ca=defaultdict(float);self.v=defaultdict(V);self.l=defaultdict(L)
    def means(self,c):
        n=self.cn[c];return ((self.ch[c]+COMP_PRIOR*GLOBAL_H)/(n+COMP_PRIOR),(self.ca[c]+COMP_PRIOR*GLOBAL_A)/(n+COMP_PRIOR))
    def rate(self,s,attr,pr):return ((s.gf if attr=='gf' else s.ga)+TEAM_PRIOR*pr)/(s.n+TEAM_PRIOR)
    def dec(self,t,d):
        s=self.l[t]
        if s.last is None:return s.attack,s.defence,s.matches
        z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);return s.attack*z,s.defence*z,s.matches
    def pred(self,r):
        c=r['competition_id'];h=r['home_club_id'];a=r['away_club_id'];d=date.fromisoformat(r['date']);lh,la=self.means(c);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        ah=clamp(lh*(self.rate(hs,'gf',lh)/lh)*(self.rate(av,'ga',lh)/lh),.15,4.5);aa=clamp(la*(self.rate(av,'gf',la)/la)*(self.rate(hs,'ga',la)/la),.15,4.5)
        hatt,hdef,hn=self.dec(h,d);aatt,adef,an=self.dec(a,d);xh=clamp(lh*math.exp(hatt+adef),.15,4.5);xa=clamp(la*math.exp(aatt+hdef),.15,4.5)
        mh=math.exp((1-BLEND)*math.log(ah)+BLEND*math.log(xh));ma=math.exp((1-BLEND)*math.log(aa)+BLEND*math.log(xa))
        return {'mu_h':mh,'mu_a':ma,'latent_h':xh,'latent_a':xa,'home_history':hn,'away_history':an,'comp_history':self.cn[c]}
    def touch(self,t,d):
        s=self.l[t]
        if s.last is not None:
            z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);s.attack*=z;s.defence*=z
        s.last=d;return s
    def update(self,r,p):
        d=date.fromisoformat(r['date']);c=r['competition_id'];h=r['home_club_id'];a=r['away_club_id'];hg=int(r['home_goals']);ag=int(r['away_goals']);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        hs.n+=1;hs.gf+=hg;hs.ga+=ag;av.n+=1;av.gf+=ag;av.ga+=hg;self.cn[c]+=1;self.ch[c]+=hg;self.ca[c]+=ag
        H=self.touch(h,d);A=self.touch(a,d);eh=clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2);ea=clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2)
        H.attack=clamp(H.attack+LR*eh,-1.2,1.2);A.defence=clamp(A.defence+LR*eh,-1.2,1.2);A.attack=clamp(A.attack+LR*ea,-1.2,1.2);H.defence=clamp(H.defence+LR*ea,-1.2,1.2);H.matches+=1;A.matches+=1

def pois(mu):
    x=[math.exp(-mu)]
    for k in range(1,MAXG+1):x.append(x[-1]*mu/k)
    return x
def logcomb(n,k):return math.lgamma(n+1)-math.lgamma(k+1)-math.lgamma(n-k+1)
def nb_total(n,mu,k):
    p=k/(k+mu);return math.exp(math.lgamma(n+k)-math.lgamma(k)-math.lgamma(n+1)+k*math.log(p)+n*math.log(1-p))
def betabin(h,n,q,phi):
    al=max(.05,q*phi);be=max(.05,(1-q)*phi)
    return math.exp(logcomb(n,h)+math.lgamma(h+al)+math.lgamma(n-h+be)-math.lgamma(n+al+be)+math.lgamma(al+be)-math.lgamma(al)-math.lgamma(be))
def matrix(mu_h,mu_a,kind='poisson',rho=0.,k=8.,phi=12.):
    M=[];tot=0.
    if kind in {'poisson','dc'}:
        hp,ap=pois(mu_h),pois(mu_a)
        for i in range(MAXG+1):
            row=[]
            for j in range(MAXG+1):
                z=hp[i]*ap[j]
                if kind=='dc':
                    if i==0 and j==0:z*=max(.05,1-rho*mu_h*mu_a)
                    elif i==0 and j==1:z*=max(.05,1+rho*mu_h)
                    elif i==1 and j==0:z*=max(.05,1+rho*mu_a)
                    elif i==1 and j==1:z*=max(.05,1-rho)
                row.append(z);tot+=z
            M.append(row)
    else:
        mu=mu_h+mu_a;q=mu_h/mu
        M=[[0.]*(MAXG+1) for _ in range(MAXG+1)]
        for n in range(0,2*MAXG+1):
            pn=nb_total(n,mu,k)
            lo=max(0,n-MAXG);hi=min(MAXG,n)
            for i in range(lo,hi+1):j=n-i;z=pn*betabin(i,n,q,phi);M[i][j]+=z;tot+=z
    return [[z/tot for z in r] for r in M]
def summarize(M,meta):
    h=d=a=0.;best=(-1,0,0)
    for i,row in enumerate(M):
        for j,p in enumerate(row):
            if i>j:h+=p
            elif i==j:d+=p
            else:a+=p
            if p>best[0]:best=(p,i,j)
    v=[h,d,a];o=sorted(range(3),key=lambda z:v[z],reverse=True);ent=-sum(x*math.log(max(x,1e-15)) for x in v)
    return {'p_home':h,'p_draw':d,'p_away':a,'top1':o[0],'margin':v[o[0]]-v[o[1]],'entropy':ent,'score_top1':f'{best[1]}-{best[2]}','score_top1_p':best[0],**meta}
def load():
    z=[]
    for p in sorted(DATA.glob('matches_r2_20000_part*.csv')):
        with p.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):r['home_goals']=int(r['home_goals']);r['away_goals']=int(r['away_goals']);z.append(r)
    z.sort(key=lambda r:(r['date'],r['game_id']));assert len(z)==N;return z
def actual(r):return 0 if r['home_goals']>r['away_goals'] else 1 if r['home_goals']==r['away_goals'] else 2
def score_nll(rows,kind,pars):
    s=0.
    for r in rows:
        p=r['base'];M=matrix(p['mu_h'],p['mu_a'],kind,**pars);i=min(r['hg'],MAXG);j=min(r['ag'],MAXG);s-=math.log(max(M[i][j],1e-15))
    return s/len(rows)
def metrics(rows,key):
    n=len(rows);hit=ll=br=rps=scorehit=drawp=drawhit=drawactual=0;cnt=Counter()
    for r in rows:
        p=r[key];y=r['y'];v=[p['p_home'],p['p_draw'],p['p_away']];hit+=p['top1']==y;ll-=math.log(max(v[y],1e-15));br+=sum((v[i]-(i==y))**2 for i in range(3));rps+=((v[0]-(y==0))**2+((v[0]+v[1])-(y<=1))**2)/2;cnt[p['score_top1']]+=1;scorehit+=p['score_top1']==r['score'];drawp+=p['top1']==1;drawhit+=p['top1']==1 and y==1;drawactual+=y==1
    return {'count':n,'hits':hit,'top1_accuracy':hit/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'draw_top1_picks':drawp,'actual_draws':drawactual,'draw_top1_precision':drawhit/drawp if drawp else None,'draw_recall':drawhit/drawactual if drawactual else None,'exact_score_top1_accuracy':scorehit/n,'score_top1_most_common':cnt.most_common(10),'score_top1_1_1_fraction':cnt['1-1']/n}
def run():
    rows=load();st=State();pred=[];by=defaultdict(list)
    for r in rows:by[r['date']].append(r)
    for ds in sorted(by):
        pend=[]
        for r in sorted(by[ds],key=lambda x:x['game_id']):
            b=st.pred(r);pred.append({'date':ds,'game_id':r['game_id'],'hg':r['home_goals'],'ag':r['away_goals'],'y':actual(r),'score':f"{r['home_goals']}-{r['away_goals']}",'base':b});pend.append((r,b))
        for r,b in pend:st.update(r,b)
    burn=pred[:4000];train=pred[4000:12000];val=pred[12000:16000];test=pred[16000:]
    # score-head hyperparameters chosen TRAIN ONLY; validation only selects family, test untouched.
    dc=[]
    for rho in [-.20,-.15,-.10,-.05,0,.05,.10,.15,.20]:dc.append((score_nll(train,'dc',{'rho':rho}),rho))
    best_rho=min(dc)[1]
    nb=[]
    for k in [2.,3.,5.,8.,12.,20.,50.]:
        for phi in [4.,6.,8.,12.,20.,40.,80.]:nb.append((score_nll(train,'nbbb',{'k':k,'phi':phi}),k,phi))
    _,best_k,best_phi=min(nb)
    for subset in [val,test]:
        for r in subset:
            b=r['base'];meta={'mu_home':b['mu_h'],'mu_away':b['mu_a'],'mu_total':b['mu_h']+b['mu_a'],'home_history':b['home_history'],'away_history':b['away_history'],'comp_history':b['comp_history']}
            r['B0']=summarize(matrix(b['mu_h'],b['mu_a'],'poisson'),meta);r['D1']=summarize(matrix(b['mu_h'],b['mu_a'],'dc',rho=best_rho),meta);r['D2']=summarize(matrix(b['mu_h'],b['mu_a'],'nbbb',k=best_k,phi=best_phi),meta)
    vm={x:metrics(val,x) for x in ['B0','D1','D2']};chosen=min(['B0','D1','D2'],key=lambda x:vm[x]['logloss']);tm={x:metrics(test,x) for x in ['B0','D1','D2']}
    out={'schema_version':'football3-top1-20k-r2-scorehead','status':'COMPLETE','classification':'RESEARCH_ONLY_NOT_VALIDATED_NOT_PROMOTED','formal_weight':0,'governance':{'snapshot_rows':N,'disjoint_from_r1':True,'same_date_results_withheld':True,'burn_in':4000,'score_train':8000,'family_validation':4000,'untouched_test':4000,'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'test_labels_used_for_parameter_or_family_selection':False},'fixed_strength':{'latent_half_life_days':HALF,'latent_lr':LR,'latent_blend':BLEND},'score_head_train_fit':{'dixon_coles_rho':best_rho,'negative_binomial_k':best_k,'beta_binomial_phi':best_phi},'validation':vm,'selected_family_by_validation_logloss':chosen,'test':tm,'selected_test':tm[chosen]}
    RESULTS.mkdir(parents=True,exist_ok=True);(RESULTS/'summary_r2.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(out,indent=2));return out
def verify():
    m=json.loads((DATA/'source_manifest_r2.json').read_text());o=json.loads((RESULTS/'summary_r2.json').read_text());assert m['snapshot_rows']==20000 and m['overlap_with_r1_selection'] is False;assert o['governance']['untouched_test']==4000 and o['governance']['odds_used'] is False and o['formal_weight']==0;print('VERIFY_OK')
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','run','verify','all']);a=p.parse_args()
    if a.mode in {'freeze','all'}:freeze()
    if a.mode in {'run','all'}:run()
    if a.mode in {'verify','all'}:verify()
