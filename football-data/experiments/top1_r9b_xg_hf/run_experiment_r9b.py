#!/usr/bin/env python3
from __future__ import annotations
import csv,hashlib,json,math,urllib.request
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';OUT=ROOT/'results'
HF='https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main'
FIX_URL=f'{HF}/fixtures.parquet?download=true'; STAT_URL=f'{HF}/match_stats.parquet?download=true'
N=20000;TARGET_BURN=4000;TARGET_TRAIN=8000;TARGET_VAL=4000
GH=1.45;GA=1.20;CP=30.;TP=6.;HALF=240.;LR=.06;BLEND=.50;MAXG=12;XG_HALF=180.;XG_PRIOR=6.
FIELDS=['date','game_id','competition_id','home_team','away_team','home_goals','away_goals','home_xg','away_xg','xg_known_at']

def clamp(x,a,b):return min(b,max(a,x))
def fsha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def download(url,path):
    req=urllib.request.Request(url,headers={'User-Agent':'football3-research/9b'})
    with urllib.request.urlopen(req,timeout=300) as r, path.open('wb') as f:
        while True:
            b=r.read(1<<20)
            if not b:break
            f.write(b)

def freeze():
    DATA.mkdir(parents=True,exist_ok=True);fp=DATA/'fixtures.parquet';sp=DATA/'match_stats.parquet'
    download(FIX_URL,fp);download(STAT_URL,sp)
    fx=pd.read_parquet(fp,columns=['id','date_utc','league_id','home_team_id','away_team_id','goals_home','goals_away','status_norm','is_played'])
    st=pd.read_parquet(sp,columns=['fixture_id','home_xg','away_xg','xg_covered','xg_nulled','known_at'])
    st=st[(st['xg_covered']==True)&(st['xg_nulled']==False)&st['home_xg'].notna()&st['away_xg'].notna()]
    fx=fx[(fx['is_played']==True)&(fx['status_norm']=='FT')&fx['goals_home'].notna()&fx['goals_away'].notna()]
    df=fx.merge(st,left_on='id',right_on='fixture_id',how='inner',validate='one_to_one')
    df['date']=pd.to_datetime(df['date_utc'],utc=True).dt.date.astype(str);df['known']=pd.to_datetime(df['known_at'],utc=True)
    df=df[(df['known']>pd.to_datetime(df['date_utc'],utc=True))&(df['home_xg'].between(0,6))&(df['away_xg'].between(0,6))]
    df=df.sort_values(['date','id']).drop_duplicates('id')
    if len(df)<N:raise RuntimeError(f'only {len(df)} valid played FT xG rows; need {N}')
    df=df.tail(N)
    out=[]
    for r in df.itertuples(index=False):
        out.append({'date':r.date,'game_id':str(int(r.id)),'competition_id':str(int(r.league_id)),'home_team':str(int(r.home_team_id)),'away_team':str(int(r.away_team_id)),
                    'home_goals':int(r.goals_home),'away_goals':int(r.goals_away),'home_xg':float(r.home_xg),'away_xg':float(r.away_xg),'xg_known_at':r.known.isoformat()})
    p=DATA/'matches_r9b_xg_20000.csv'
    with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(out)
    m={'schema_version':'football3-top1-r9b-hf-coarse-xg','status':'FROZEN_20000_XG_DEVELOPMENT','classification':'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION',
       'source_dataset':'eatpizzanot/soccer-dataset','license':'CC-BY-4.0','source_tables_downloaded':['fixtures','match_stats'],'odds_table_downloaded':False,
       'fixtures_sha256':fsha(fp),'match_stats_sha256':fsha(sp),'joined_valid_xg_rows':int(len(fx.merge(st,left_on='id',right_on='fixture_id',how='inner'))),'snapshot_rows':len(out),
       'selection':'latest 20000 FT played rows with xg_covered=true, xg_nulled=false and known_at>kickoff','first_date':out[0]['date'],'last_date':out[-1]['date'],'snapshot_sha256':fsha(p),
       'xg_quality':'coarse provider shots-by-zone estimate; development signal only','strict_prior_xg_contract':True,'current_match_xg_available_before_prediction':False,'same_date_update_allowed':False,'odds_used':False,'market_prices_used':False}
    (DATA/'source_manifest_r9b.json').write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    fp.unlink();sp.unlink();print(json.dumps(m,indent=2));return m

@dataclass
class V:n:int=0;gf:float=0.;ga:float=0.
@dataclass
class L:attack:float=0.;defence:float=0.;matches:int=0;last:date|None=None
class S:
    def __init__(self):
        self.cn=Counter();self.ch=defaultdict(float);self.ca=defaultdict(float);self.v=defaultdict(V);self.l=defaultdict(L);self.xgn=Counter();self.xgh=defaultdict(float);self.xga=defaultdict(float);self.xhist=defaultdict(list)
    def means(self,c):
        n=self.cn[c];return ((self.ch[c]+CP*GH)/(n+CP),(self.ca[c]+CP*GA)/(n+CP))
    def xgmeans(self,c):
        n=self.xgn[c];return ((self.xgh[c]+CP*GH)/(n+CP),(self.xga[c]+CP*GA)/(n+CP))
    def rate(self,s,x,p):return ((s.gf if x=='gf' else s.ga)+TP*p)/(s.n+TP)
    def dec(self,t,d):
        s=self.l[t]
        if s.last is None:return s.attack,s.defence,s.matches
        z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);return s.attack*z,s.defence*z,s.matches
    def xgform(self,t,d,prior):
        sw=sf=sa=0.
        for dd,xf,xa in reversed(self.xhist[t]):
            days=(d-dd).days
            if days<=0:continue
            if days>XG_HALF*8:break
            w=math.exp(-math.log(2)*days/XG_HALF);sw+=w;sf+=w*xf;sa+=w*xa
        den=sw+XG_PRIOR;return ((sf+XG_PRIOR*prior)/den,(sa+XG_PRIOR*prior)/den,sw)
    def pred(self,r):
        c=r['competition_id'];h=r['home_team'];a=r['away_team'];d=date.fromisoformat(r['date']);lh,la=self.means(c);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        ah=clamp(lh*(self.rate(hs,'gf',lh)/lh)*(self.rate(av,'ga',lh)/lh),.15,4.5);aa=clamp(la*(self.rate(av,'gf',la)/la)*(self.rate(hs,'ga',la)/la),.15,4.5)
        ha,hd,hn=self.dec(h,d);aa2,ad,an=self.dec(a,d);xh=clamp(lh*math.exp(ha+ad),.15,4.5);xa=clamp(la*math.exp(aa2+hd),.15,4.5);mh=math.exp((1-BLEND)*math.log(ah)+BLEND*math.log(xh));ma=math.exp((1-BLEND)*math.log(aa)+BLEND*math.log(xa));p=poisson_1x2(mh,ma)
        cxh,cxa=self.xgmeans(c);avg=(cxh+cxa)/2;hf=self.xgform(h,d,avg);af=self.xgform(a,d,avg);xmh=clamp(cxh*(hf[0]/avg)*(af[1]/avg),.15,4.5);xma=clamp(cxa*(af[0]/avg)*(hf[1]/avg),.15,4.5)
        return {'p_home':p[0],'p_draw':p[1],'p_away':p[2],'mu_home':mh,'mu_away':ma,'mu_total':mh+ma,'latent_h':xh,'latent_a':xa,'home_history':hn,'away_history':an,'comp_history':self.cn[c],
                'xg_mu_home':xmh,'xg_mu_away':xma,'xg_mu_total':xmh+xma,'xg_home_for':hf[0],'xg_home_against':hf[1],'xg_away_for':af[0],'xg_away_against':af[1],'xg_weight_min':min(hf[2],af[2])}
    def touch(self,t,d):
        s=self.l[t]
        if s.last is not None:
            z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);s.attack*=z;s.defence*=z
        s.last=d;return s
    def update(self,r,p):
        d=date.fromisoformat(r['date']);c=r['competition_id'];h=r['home_team'];a=r['away_team'];hg=int(r['home_goals']);ag=int(r['away_goals']);hx=float(r['home_xg']);ax=float(r['away_xg']);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        hs.n+=1;hs.gf+=hg;hs.ga+=ag;av.n+=1;av.gf+=ag;av.ga+=hg;self.cn[c]+=1;self.ch[c]+=hg;self.ca[c]+=ag;H=self.touch(h,d);A=self.touch(a,d);eh=clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2);ea=clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2)
        H.attack=clamp(H.attack+LR*eh,-1.2,1.2);A.defence=clamp(A.defence+LR*eh,-1.2,1.2);A.attack=clamp(A.attack+LR*ea,-1.2,1.2);H.defence=clamp(H.defence+LR*ea,-1.2,1.2);H.matches+=1;A.matches+=1
        self.xgn[c]+=1;self.xgh[c]+=hx;self.xga[c]+=ax;self.xhist[h].append((d,hx,ax));self.xhist[a].append((d,ax,hx))

def poisson_1x2(mh,ma):
    hp=[math.exp(-mh)];ap=[math.exp(-ma)]
    for k in range(1,MAXG+1):hp.append(hp[-1]*mh/k);ap.append(ap[-1]*ma/k)
    h=d=a=t=0.
    for i,x in enumerate(hp):
        for j,y in enumerate(ap):
            z=x*y;t+=z
            if i>j:h+=z
            elif i==j:d+=z
            else:a+=z
    return [h/t,d/t,a/t]
def actual(r):return 0 if r['home_goals']>r['away_goals'] else 1 if r['home_goals']==r['away_goals'] else 2
def load():
    z=[]
    with (DATA/'matches_r9b_xg_20000.csv').open(encoding='utf-8') as f:
        for r in csv.DictReader(f):r['home_goals']=int(r['home_goals']);r['away_goals']=int(r['away_goals']);r['home_xg']=float(r['home_xg']);r['away_xg']=float(r['away_xg']);z.append(r)
    z.sort(key=lambda r:(r['date'],r['game_id']));assert len(z)==N;return z
def feat_e2(p):
    v=np.clip([p['p_home'],p['p_draw'],p['p_away']],1e-8,1);gap=abs(p['mu_home']-p['mu_away']);tot=p['mu_total'];return [math.log(v[0]/v[2]),math.log(v[1]/v[2]),tot,tot*tot,gap,gap*gap,math.log1p(p['home_history']),math.log1p(p['away_history']),math.log1p(p['comp_history']),p['p_draw']*tot,p['p_draw']*gap]
def feat_k1(p):return feat_e2(p)+[p['xg_mu_home'],p['xg_mu_away'],p['xg_mu_total'],p['xg_mu_home']-p['xg_mu_away'],p['xg_mu_total']-p['mu_total'],(p['xg_mu_home']-p['xg_mu_away'])-(p['mu_home']-p['mu_away']),p['xg_home_for']-p['xg_away_for'],p['xg_home_against']-p['xg_away_against'],math.log1p(p['xg_weight_min'])]
def decorate(v):
    v=np.asarray(v,dtype=float);v=np.clip(v,1e-12,None);v=v/v.sum();o=np.argsort(-v);return {'p_home':float(v[0]),'p_draw':float(v[1]),'p_away':float(v[2]),'top1':int(o[0])}
def metrics(rows,key):
    n=len(rows);hit=ll=br=rps=0;picks=[0,0,0];hits=[0,0,0];acts=[0,0,0]
    for r in rows:
        p=r[key];y=r['y'];v=[p['p_home'],p['p_draw'],p['p_away']];t=p['top1'];hit+=t==y;picks[t]+=1;hits[t]+=t==y;acts[y]+=1;ll-=math.log(max(v[y],1e-15));br+=sum((v[i]-(i==y))**2 for i in range(3));rps+=((v[0]-(y==0))**2+((v[0]+v[1])-(y<=1))**2)/2
    return {'count':n,'coverage':1.0,'hits':hit,'top1_accuracy':hit/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':{'home':picks[0],'draw':picks[1],'away':picks[2]},'top1_hits':{'home':hits[0],'draw':hits[1],'away':hits[2]},'actuals':{'home':acts[0],'draw':acts[1],'away':acts[2]}}
def boundary(pred,target):
    i=min(max(1,target),len(pred)-1)
    while i<len(pred) and pred[i]['date']==pred[i-1]['date']:i+=1
    return i

def run():
    rows=load();st=S();pred=[];by=defaultdict(list)
    for r in rows:by[r['date']].append(r)
    for ds in sorted(by):
        pending=[]
        for r in sorted(by[ds],key=lambda x:x['game_id']):p=st.pred(r);pred.append({'date':ds,'y':actual(r),'raw':p});pending.append((r,p))
        for r,p in pending:st.update(r,p)
    b1=boundary(pred,TARGET_BURN);b2=boundary(pred,b1+TARGET_TRAIN);b3=boundary(pred,b2+TARGET_VAL);train=pred[b1:b2];val=pred[b2:b3];test=pred[b3:]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[r['y'] for r in train];e2=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0));k1=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0));e2.fit([feat_e2(r['raw']) for r in train],y);k1.fit([feat_k1(r['raw']) for r in train],y)
    for subset in (val,test):
        p0=e2.predict_proba([feat_e2(r['raw']) for r in subset]);p1=k1.predict_proba([feat_k1(r['raw']) for r in subset])
        for r,a,b in zip(subset,p0,p1):r['E2']=decorate(a);r['K1']=decorate(b)
    vm0=metrics(val,'E2');vm1=metrics(val,'K1');tm0=metrics(test,'E2');tm1=metrics(test,'K1')
    out={'schema_version':'football3-top1-r9b-xg-fullcoverage','status':'COMPLETE','classification':'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION','formal_weight':0,'governance':{'snapshot_rows':N,'strict_prior_xg':True,'current_match_xg_used_before_prediction':False,'same_date_results_and_xg_withheld':True,'burn_in':b1,'train':len(train),'validation':len(val),'untouched_within_r9b_test':len(test),'date_safe_split_boundaries':True,'coverage_required':1.0,'abstention_used':False,'veto_used':False,'odds_table_downloaded':False,'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'formal_promotion_allowed_from_this_run':False},'fixed_xg':{'half_life_days':XG_HALF,'prior_weight':XG_PRIOR,'quality':'coarse shots-by-zone provider estimate'},'models':{'E2':'goal-results contextual calibration','K1':'E2 plus strictly-prior rolling xG attack/defence state'},'validation':{'E2':vm0,'K1':vm1,'delta_K1_minus_E2':{'top1_pp':100*(vm1['top1_accuracy']-vm0['top1_accuracy']),'logloss':vm1['logloss']-vm0['logloss'],'brier':vm1['brier']-vm0['brier'],'rps':vm1['rps']-vm0['rps']}},'test':{'E2':tm0,'K1':tm1,'delta_K1_minus_E2':{'top1_pp':100*(tm1['top1_accuracy']-tm0['top1_accuracy']),'logloss':tm1['logloss']-tm0['logloss'],'brier':tm1['brier']-tm0['brier'],'rps':tm1['rps']-tm0['rps']}}}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r9b.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(out,indent=2));return out
def verify():
    m=json.loads((DATA/'source_manifest_r9b.json').read_text());o=json.loads((OUT/'summary_r9b.json').read_text());assert m['snapshot_rows']==N and m['odds_table_downloaded'] is False and m['strict_prior_xg_contract'];assert o['governance']['coverage_required']==1.0 and not o['governance']['abstention_used'] and not o['governance']['veto_used'] and not o['governance']['odds_used'] and not o['governance']['formal_promotion_allowed_from_this_run'];print('VERIFY_OK')
if __name__=='__main__':
    import argparse;q=argparse.ArgumentParser();q.add_argument('mode',choices=['freeze','run','verify','all']);a=q.parse_args()
    if a.mode in {'freeze','all'}:freeze()
    if a.mode in {'run','all'}:run()
    if a.mode in {'verify','all'}:verify()
