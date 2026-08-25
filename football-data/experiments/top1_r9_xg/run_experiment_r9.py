#!/usr/bin/env python3
from __future__ import annotations
import csv,hashlib,json,math,re,urllib.request
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';OUT=ROOT/'results'
LEAGUES=('EPL','La_liga','Bundesliga','Serie_A','Ligue_1','RFPL');SEASONS=range(2014,2026)
N=20000;BURN=4000;TRAIN=8000;VAL=4000;TEST=4000
GH=1.45;GA=1.20;CP=30.;TP=6.;HALF=240.;LR=.06;BLEND=.50;MAXG=12
XG_HALF=180.;XG_PRIOR=6.
FIELDS=['date','game_id','competition_id','season','home_team','away_team','home_goals','away_goals','home_xg','away_xg']

def clamp(x,a,b):return min(b,max(a,x))
def sha_bytes(b):return hashlib.sha256(b).hexdigest()
def fsha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 football3-research/9.0','Accept-Language':'en-US,en;q=0.9'})
    with urllib.request.urlopen(req,timeout=90) as r:return r.read()
def extract_dates(html):
    text=html.decode('utf-8',errors='replace')
    m=re.search(r"datesData\s*=\s*JSON\.parse\('([^']+)'\)",text)
    if not m:raise RuntimeError('datesData not found')
    raw=m.group(1)
    decoded=bytes(raw,'utf-8').decode('unicode_escape')
    return json.loads(decoded)

def freeze():
    DATA.mkdir(parents=True,exist_ok=True);rows=[];pages=[];seen=set();bad=Counter()
    for league in LEAGUES:
        for season in SEASONS:
            url=f'https://understat.com/league/{league}/{season}'
            try:blob=get(url);items=extract_dates(blob);pages.append({'league':league,'season':season,'sha256':sha_bytes(blob),'rows':len(items)})
            except Exception as e:
                bad[f'page_{type(e).__name__}']+=1;continue
            for r in items:
                if not r.get('isResult'):continue
                gid=str(r.get('id') or '').strip();dt=str(r.get('datetime') or '')[:10]
                h=(r.get('h') or {}).get('title');a=(r.get('a') or {}).get('title');goals=r.get('goals') or {};xg=r.get('xG') or {}
                try:date.fromisoformat(dt);hg=int(goals.get('h'));ag=int(goals.get('a'));hx=float(xg.get('h'));ax=float(xg.get('a'))
                except Exception:bad['row_parse']+=1;continue
                if not gid or gid in seen or not h or not a or h==a or min(hx,ax)<0:bad['identity_or_xg']+=1;continue
                seen.add(gid);rows.append({'date':dt,'game_id':gid,'competition_id':league,'season':str(season),'home_team':str(h),'away_team':str(a),'home_goals':hg,'away_goals':ag,'home_xg':hx,'away_xg':ax})
    rows.sort(key=lambda r:(r['date'],r['game_id']))
    if len(rows)<N:raise RuntimeError(f'only {len(rows)} valid Understat result rows; need {N}')
    chosen=rows[-N:];p=DATA/'matches_r9_xg_20000.csv'
    with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(chosen)
    m={'schema_version':'football3-top1-r9-understat-xg','status':'FROZEN_20000_XG_DEVELOPMENT','classification':'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION',
       'source':'Understat league season pages','leagues':list(LEAGUES),'seasons':[min(SEASONS),max(SEASONS)],'pages_ok':len(pages),'pages':pages,'valid_rows':len(rows),
       'snapshot_rows':N,'selection':'latest 20000 completed matches with xG','first_date':chosen[0]['date'],'last_date':chosen[-1]['date'],'snapshot_sha256':fsha(p),
       'strict_prior_xg_contract':True,'current_match_xg_available_before_prediction':False,'odds_used':False,'market_prices_used':False,'same_date_update_allowed':False,'excluded_counts':dict(bad)}
    (DATA/'source_manifest_r9.json').write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps({k:m[k] for k in ['status','pages_ok','valid_rows','snapshot_rows','first_date','last_date','excluded_counts']},indent=2));return m

@dataclass
class V:n:int=0;gf:float=0.;ga:float=0.
@dataclass
class L:attack:float=0.;defence:float=0.;matches:int=0;last:date|None=None
class S:
    def __init__(self):
        self.cn=Counter();self.ch=defaultdict(float);self.ca=defaultdict(float);self.v=defaultdict(V);self.l=defaultdict(L)
        self.xgn=Counter();self.xgh=defaultdict(float);self.xga=defaultdict(float);self.xhist=defaultdict(list)
    def means(self,c):
        n=self.cn[c];return ((self.ch[c]+CP*GH)/(n+CP),(self.ca[c]+CP*GA)/(n+CP))
    def xgmeans(self,c):
        n=self.xgn[c];return ((self.xgh[c]+CP*GH)/(n+CP),(self.xga[c]+CP*GA)/(n+CP))
    def rate(self,s,x,p):return ((s.gf if x=='gf' else s.ga)+TP*p)/(s.n+TP)
    def dec(self,t,d):
        s=self.l[t]
        if s.last is None:return s.attack,s.defence,s.matches
        z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);return s.attack*z,s.defence*z,s.matches
    def xgform(self,t,d,prior_mean):
        sw=sfor=sagainst=0.
        for dd,xf,xa in reversed(self.xhist[t]):
            days=(d-dd).days
            if days<=0:continue
            if days>XG_HALF*8:break
            w=math.exp(-math.log(2)*days/XG_HALF);sw+=w;sfor+=w*xf;sagainst+=w*xa
        den=sw+XG_PRIOR
        return ((sfor+XG_PRIOR*prior_mean)/den,(sagainst+XG_PRIOR*prior_mean)/den,sw)
    def pred(self,r):
        c=r['competition_id'];h=r['home_team'];a=r['away_team'];d=date.fromisoformat(r['date']);lh,la=self.means(c);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        ah=clamp(lh*(self.rate(hs,'gf',lh)/lh)*(self.rate(av,'ga',lh)/lh),.15,4.5);aa=clamp(la*(self.rate(av,'gf',la)/la)*(self.rate(hs,'ga',la)/la),.15,4.5)
        ha,hd,hn=self.dec(h,d);aa2,ad,an=self.dec(a,d);xh=clamp(lh*math.exp(ha+ad),.15,4.5);xa=clamp(la*math.exp(aa2+hd),.15,4.5)
        mh=math.exp((1-BLEND)*math.log(ah)+BLEND*math.log(xh));ma=math.exp((1-BLEND)*math.log(aa)+BLEND*math.log(xa));p=poisson_1x2(mh,ma)
        cxh,cxa=self.xgmeans(c);avg=(cxh+cxa)/2.;hf=self.xgform(h,d,avg);af=self.xgform(a,d,avg)
        xmu_h=clamp(cxh*(hf[0]/avg)*(af[1]/avg),.15,4.5);xmu_a=clamp(cxa*(af[0]/avg)*(hf[1]/avg),.15,4.5)
        return {'p_home':p[0],'p_draw':p[1],'p_away':p[2],'mu_home':mh,'mu_away':ma,'mu_total':mh+ma,'latent_h':xh,'latent_a':xa,
                'home_history':hn,'away_history':an,'comp_history':self.cn[c],'xg_mu_home':xmu_h,'xg_mu_away':xmu_a,'xg_mu_total':xmu_h+xmu_a,
                'xg_home_for':hf[0],'xg_home_against':hf[1],'xg_away_for':af[0],'xg_away_against':af[1],'xg_weight_min':min(hf[2],af[2])}
    def touch(self,t,d):
        s=self.l[t]
        if s.last is not None:
            z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);s.attack*=z;s.defence*=z
        s.last=d;return s
    def update(self,r,p):
        d=date.fromisoformat(r['date']);c=r['competition_id'];h=r['home_team'];a=r['away_team'];hg=int(r['home_goals']);ag=int(r['away_goals']);hx=float(r['home_xg']);ax=float(r['away_xg']);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        hs.n+=1;hs.gf+=hg;hs.ga+=ag;av.n+=1;av.gf+=ag;av.ga+=hg;self.cn[c]+=1;self.ch[c]+=hg;self.ca[c]+=ag
        H=self.touch(h,d);A=self.touch(a,d);eh=clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2);ea=clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2)
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
    with (DATA/'matches_r9_xg_20000.csv').open(encoding='utf-8') as f:
        for r in csv.DictReader(f):r['home_goals']=int(r['home_goals']);r['away_goals']=int(r['away_goals']);r['home_xg']=float(r['home_xg']);r['away_xg']=float(r['away_xg']);z.append(r)
    z.sort(key=lambda r:(r['date'],r['game_id']));assert len(z)==N;return z
def feat_e2(p):
    v=np.clip([p['p_home'],p['p_draw'],p['p_away']],1e-8,1);gap=abs(p['mu_home']-p['mu_away']);tot=p['mu_total']
    return [math.log(v[0]/v[2]),math.log(v[1]/v[2]),tot,tot*tot,gap,gap*gap,math.log1p(p['home_history']),math.log1p(p['away_history']),math.log1p(p['comp_history']),p['p_draw']*tot,p['p_draw']*gap]
def feat_k1(p):
    return feat_e2(p)+[p['xg_mu_home'],p['xg_mu_away'],p['xg_mu_total'],p['xg_mu_home']-p['xg_mu_away'],p['xg_mu_total']-p['mu_total'],
                       (p['xg_mu_home']-p['xg_mu_away'])-(p['mu_home']-p['mu_away']),p['xg_home_for']-p['xg_away_for'],p['xg_home_against']-p['xg_away_against'],math.log1p(p['xg_weight_min'])]
def decorate(v):
    v=np.asarray(v,dtype=float);v=np.clip(v,1e-12,None);v=v/v.sum();o=np.argsort(-v);return {'p_home':float(v[0]),'p_draw':float(v[1]),'p_away':float(v[2]),'top1':int(o[0])}
def metrics(rows,key):
    n=len(rows);hit=ll=br=rps=0;picks=[0,0,0];hits=[0,0,0];acts=[0,0,0]
    for r in rows:
        p=r[key];y=r['y'];v=[p['p_home'],p['p_draw'],p['p_away']];t=p['top1'];hit+=t==y;picks[t]+=1;hits[t]+=t==y;acts[y]+=1
        ll-=math.log(max(v[y],1e-15));br+=sum((v[i]-(i==y))**2 for i in range(3));rps+=((v[0]-(y==0))**2+((v[0]+v[1])-(y<=1))**2)/2
    return {'count':n,'coverage':1.0,'hits':hit,'top1_accuracy':hit/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'top1_picks':{'home':picks[0],'draw':picks[1],'away':picks[2]},'top1_hits':{'home':hits[0],'draw':hits[1],'away':hits[2]},'actuals':{'home':acts[0],'draw':acts[1],'away':acts[2]}}

def run():
    rows=load();st=S();pred=[];by=defaultdict(list)
    for r in rows:by[r['date']].append(r)
    for ds in sorted(by):
        pending=[]
        for r in sorted(by[ds],key=lambda x:x['game_id']):p=st.pred(r);pred.append({'date':ds,'y':actual(r),'raw':p});pending.append((r,p))
        for r,p in pending:st.update(r,p)
    train=pred[BURN:BURN+TRAIN];val=pred[BURN+TRAIN:BURN+TRAIN+VAL];test=pred[-TEST:]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[r['y'] for r in train];e2=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0));k1=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    e2.fit([feat_e2(r['raw']) for r in train],y);k1.fit([feat_k1(r['raw']) for r in train],y)
    for subset in (val,test):
        P0=e2.predict_proba([feat_e2(r['raw']) for r in subset]);P1=k1.predict_proba([feat_k1(r['raw']) for r in subset])
        for r,a,b in zip(subset,P0,P1):r['E2']=decorate(a);r['K1']=decorate(b)
    vm0=metrics(val,'E2');vm1=metrics(val,'K1');tm0=metrics(test,'E2');tm1=metrics(test,'K1')
    out={'schema_version':'football3-top1-r9-xg-fullcoverage','status':'COMPLETE','classification':'DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION','formal_weight':0,
         'governance':{'snapshot_rows':N,'strict_prior_xg':True,'current_match_xg_used_before_prediction':False,'same_date_results_and_xg_withheld':True,'burn_in':BURN,'train':TRAIN,'validation':VAL,'untouched_within_r9_test':TEST,
                       'coverage_required':1.0,'abstention_used':False,'veto_used':False,'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'formal_promotion_allowed_from_this_run':False},
         'fixed_xg':{'half_life_days':XG_HALF,'prior_weight':XG_PRIOR},'models':{'E2':'goal-results contextual calibration','K1':'E2 plus strictly-prior rolling xG attack/defence state'},
         'validation':{'E2':vm0,'K1':vm1,'delta_K1_minus_E2':{'top1_pp':100*(vm1['top1_accuracy']-vm0['top1_accuracy']),'logloss':vm1['logloss']-vm0['logloss'],'brier':vm1['brier']-vm0['brier'],'rps':vm1['rps']-vm0['rps']}},
         'test':{'E2':tm0,'K1':tm1,'delta_K1_minus_E2':{'top1_pp':100*(tm1['top1_accuracy']-tm0['top1_accuracy']),'logloss':tm1['logloss']-tm0['logloss'],'brier':tm1['brier']-tm0['brier'],'rps':tm1['rps']-tm0['rps']}}}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r9.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(out,indent=2));return out
def verify():
    m=json.loads((DATA/'source_manifest_r9.json').read_text());o=json.loads((OUT/'summary_r9.json').read_text());assert m['snapshot_rows']==N and m['strict_prior_xg_contract'] and not m['current_match_xg_available_before_prediction']
    assert o['governance']['coverage_required']==1.0 and not o['governance']['abstention_used'] and not o['governance']['veto_used'] and not o['governance']['odds_used'] and not o['governance']['formal_promotion_allowed_from_this_run'];print('VERIFY_OK')
if __name__=='__main__':
    import argparse;q=argparse.ArgumentParser();q.add_argument('mode',choices=['freeze','run','verify','all']);a=q.parse_args()
    if a.mode in {'freeze','all'}:freeze()
    if a.mode in {'run','all'}:run()
    if a.mode in {'verify','all'}:verify()
