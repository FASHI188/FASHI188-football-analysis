#!/usr/bin/env python3
from __future__ import annotations
import csv,hashlib,io,json,math,urllib.request,zipfile
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date,datetime
from pathlib import Path
import numpy as np

ARCHIVE='https://codeload.github.com/footballcsv/england/zip/refs/heads/master'
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';OUT=ROOT/'results'
N=15000;BURN=3000;TRAIN=6000;VAL=3000;TEST=3000
GH=1.45;GA=1.20;CP=30.;TP=6.;HALF=240.;LR=.06;BLEND=.50;MAXG=12
ELO_K=20.;ELO_HOME=60.;ELO_SCALE=400.
FIELDS=['date','game_id','competition_id','season','home_team','away_team','home_goals','away_goals']

def clamp(x,a,b):return min(b,max(a,x))
def fsha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def parse_date(s):return datetime.strptime(str(s).strip(),'%a %b %d %Y').date()
def parse_ft(s):
    z=str(s).strip().split('-')
    if len(z)!=2:return None
    try:return int(z[0]),int(z[1])
    except:return None

def freeze():
    DATA.mkdir(parents=True,exist_ok=True);req=urllib.request.Request(ARCHIVE,headers={'User-Agent':'football3-research/7.0'})
    with urllib.request.urlopen(req,timeout=300) as r:raw=r.read()
    rows=[];files=[];bad=Counter()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in sorted(z.namelist()):
            if '/1990s/' not in name or not name.endswith('.csv') or '/eng.' not in name:continue
            parts=name.split('/');season=parts[-2];fn=parts[-1]
            if season<'1992-93' or season>'1999-00' or fn not in {'eng.1.csv','eng.2.csv','eng.3.csv','eng.4.csv'}:continue
            files.append(name);rd=csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig',errors='replace')))
            for i,r in enumerate(rd,1):
                try:d=parse_date(r.get('Date',''))
                except:bad['date']+=1;continue
                sc=parse_ft(r.get('FT',''));h=str(r.get('Team 1') or '').strip();a=str(r.get('Team 2') or '').strip()
                if sc is None:bad['score']+=1;continue
                if not h or not a or h==a:bad['identity']+=1;continue
                hg,ag=sc;comp=fn[:-4];gid=f'{season}:{comp}:{i}:{h}:{a}'
                rows.append({'date':d.isoformat(),'game_id':gid,'competition_id':comp,'season':season,'home_team':h,'away_team':a,'home_goals':hg,'away_goals':ag})
    rows.sort(key=lambda r:(r['date'],r['game_id']))
    if len(rows)<N:raise RuntimeError(f'only {len(rows)} eligible 1990s rows; need {N}')
    chosen=rows[-N:];p=DATA/'matches_r7_15000.csv'
    with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(chosen)
    m={'schema_version':'football3-top1-r7-england-1990s','status':'FROZEN_15000_EXTERNAL_PURE_RESULTS','source_repository':'footballcsv/england',
       'source_archive_sha256':hashlib.sha256(raw).hexdigest(),'source_files_used':len(files),'eligible_rows':len(rows),'snapshot_rows':N,
       'selection':'latest 15000 rows from 1992-93..1999-00 eng.1..eng.4','first_date':chosen[0]['date'],'last_date':chosen[-1]['date'],
       'snapshot_sha256':fsha(p),'overlap_with_r1_r6':False,'source_has_odds_columns':False,'odds_used':False,'market_prices_used':False,
       'same_date_update_allowed':False,'excluded_counts':dict(bad)}
    (DATA/'source_manifest_r7.json').write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(m,indent=2));return m

@dataclass
class V:n:int=0;gf:float=0.;ga:float=0.
@dataclass
class L:attack:float=0.;defence:float=0.;matches:int=0;last:date|None=None
class S:
    def __init__(self):
        self.cn=Counter();self.ch=defaultdict(float);self.ca=defaultdict(float);self.v=defaultdict(V);self.l=defaultdict(L)
        self.elo=defaultdict(lambda:1500.0);self.elo_n=Counter()
    def means(self,c):
        n=self.cn[c];return ((self.ch[c]+CP*GH)/(n+CP),(self.ca[c]+CP*GA)/(n+CP))
    def rate(self,s,x,p):return ((s.gf if x=='gf' else s.ga)+TP*p)/(s.n+TP)
    def dec(self,t,d):
        s=self.l[t]
        if s.last is None:return s.attack,s.defence,s.matches
        z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);return s.attack*z,s.defence*z,s.matches
    def elo_features(self,h,a):
        diff=self.elo[h]+ELO_HOME-self.elo[a];eh=1/(1+10**(-diff/ELO_SCALE))
        return diff/ELO_SCALE,eh,self.elo_n[h],self.elo_n[a]
    def pred(self,r):
        c=r['competition_id'];h=r['home_team'];a=r['away_team'];d=date.fromisoformat(r['date']);lh,la=self.means(c);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        ah=clamp(lh*(self.rate(hs,'gf',lh)/lh)*(self.rate(av,'ga',lh)/lh),.15,4.5);aa=clamp(la*(self.rate(av,'gf',la)/la)*(self.rate(hs,'ga',la)/la),.15,4.5)
        ha,hd,hn=self.dec(h,d);aa2,ad,an=self.dec(a,d);xh=clamp(lh*math.exp(ha+ad),.15,4.5);xa=clamp(la*math.exp(aa2+hd),.15,4.5)
        mh=math.exp((1-BLEND)*math.log(ah)+BLEND*math.log(xh));ma=math.exp((1-BLEND)*math.log(aa)+BLEND*math.log(xa));p=poisson_1x2(mh,ma)
        ed,ee,en_h,en_a=self.elo_features(h,a)
        return {'p_home':p[0],'p_draw':p[1],'p_away':p[2],'mu_home':mh,'mu_away':ma,'mu_total':mh+ma,'latent_h':xh,'latent_a':xa,
                'home_history':hn,'away_history':an,'comp_history':self.cn[c],'elo_diff_scaled':ed,'elo_home_expect':ee,'elo_home_n':en_h,'elo_away_n':en_a}
    def touch(self,t,d):
        s=self.l[t]
        if s.last is not None:
            z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);s.attack*=z;s.defence*=z
        s.last=d;return s
    def update(self,r,p):
        d=date.fromisoformat(r['date']);c=r['competition_id'];h=r['home_team'];a=r['away_team'];hg=int(r['home_goals']);ag=int(r['away_goals']);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        hs.n+=1;hs.gf+=hg;hs.ga+=ag;av.n+=1;av.gf+=ag;av.ga+=hg;self.cn[c]+=1;self.ch[c]+=hg;self.ca[c]+=ag
        H=self.touch(h,d);A=self.touch(a,d);eh=clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2);ea=clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2)
        H.attack=clamp(H.attack+LR*eh,-1.2,1.2);A.defence=clamp(A.defence+LR*eh,-1.2,1.2);A.attack=clamp(A.attack+LR*ea,-1.2,1.2);H.defence=clamp(H.defence+LR*ea,-1.2,1.2);H.matches+=1;A.matches+=1
        exp=p['elo_home_expect'];score=1.0 if hg>ag else .5 if hg==ag else 0.0;delta=ELO_K*(score-exp);self.elo[h]+=delta;self.elo[a]-=delta;self.elo_n[h]+=1;self.elo_n[a]+=1

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
    with (DATA/'matches_r7_15000.csv').open(encoding='utf-8') as f:
        for r in csv.DictReader(f):r['home_goals']=int(r['home_goals']);r['away_goals']=int(r['away_goals']);z.append(r)
    z.sort(key=lambda r:(r['date'],r['game_id']));assert len(z)==N;return z
def feat_e2(p):
    v=np.clip([p['p_home'],p['p_draw'],p['p_away']],1e-8,1);gap=abs(p['mu_home']-p['mu_away']);tot=p['mu_total']
    return [math.log(v[0]/v[2]),math.log(v[1]/v[2]),tot,tot*tot,gap,gap*gap,math.log1p(p['home_history']),math.log1p(p['away_history']),
            math.log1p(p['comp_history']),p['p_draw']*tot,p['p_draw']*gap]
def feat_h1(p):
    return feat_e2(p)+[p['elo_diff_scaled'],p['elo_home_expect'],abs(p['elo_diff_scaled']),math.log1p(p['elo_home_n']),math.log1p(p['elo_away_n'])]
def decorate(v):
    v=np.asarray(v,dtype=float);v=np.clip(v,1e-12,None);v=v/v.sum();o=np.argsort(-v);return {'p_home':float(v[0]),'p_draw':float(v[1]),'p_away':float(v[2]),'top1':int(o[0])}
def metrics(rows,key):
    n=len(rows);hit=ll=br=rps=0;picks=[0,0,0];hits=[0,0,0];acts=[0,0,0]
    for r in rows:
        p=r[key];y=r['y'];v=[p['p_home'],p['p_draw'],p['p_away']];t=p['top1'];hit+=t==y;picks[t]+=1;hits[t]+=t==y;acts[y]+=1
        ll-=math.log(max(v[y],1e-15));br+=sum((v[i]-(i==y))**2 for i in range(3));rps+=((v[0]-(y==0))**2+((v[0]+v[1])-(y<=1))**2)/2
    return {'count':n,'coverage':1.0,'hits':hit,'top1_accuracy':hit/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,
            'top1_picks':{'home':picks[0],'draw':picks[1],'away':picks[2]},'top1_hits':{'home':hits[0],'draw':hits[1],'away':hits[2]},'actuals':{'home':acts[0],'draw':acts[1],'away':acts[2]}}

def run():
    rows=load();st=S();pred=[];by=defaultdict(list)
    for r in rows:by[r['date']].append(r)
    for ds in sorted(by):
        pending=[]
        for r in sorted(by[ds],key=lambda x:x['game_id']):p=st.pred(r);pred.append({'y':actual(r),'raw':p});pending.append((r,p))
        for r,p in pending:st.update(r,p)
    train=pred[BURN:BURN+TRAIN];val=pred[BURN+TRAIN:BURN+TRAIN+VAL];test=pred[-TEST:]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[r['y'] for r in train]
    e2=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    h1=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    e2.fit([feat_e2(r['raw']) for r in train],y);h1.fit([feat_h1(r['raw']) for r in train],y)
    for subset in (val,test):
        P0=e2.predict_proba([feat_e2(r['raw']) for r in subset]);P1=h1.predict_proba([feat_h1(r['raw']) for r in subset])
        for r,a,b in zip(subset,P0,P1):r['E2']=decorate(a);r['H1']=decorate(b)
    vm0=metrics(val,'E2');vm1=metrics(val,'H1');tm0=metrics(test,'E2');tm1=metrics(test,'H1')
    out={'schema_version':'football3-top1-r7-elo-fullcoverage','status':'COMPLETE','classification':'RESEARCH_ONLY_NOT_VALIDATED_NOT_PROMOTED','formal_weight':0,
         'governance':{'snapshot_rows':N,'external_source':'footballcsv/england 1990s','source_pure_results_only':True,'overlap_with_r1_r6':False,
                       'same_date_results_withheld':True,'burn_in':BURN,'train':TRAIN,'validation':VAL,'untouched_test':TEST,'coverage_required':1.0,
                       'abstention_used':False,'veto_used':False,'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,
                       'test_labels_used_for_training_or_hyperparameter_selection':False},
         'fixed_elo':{'initial':1500.0,'k':ELO_K,'home_advantage':ELO_HOME,'scale':ELO_SCALE},
         'models':{'E2':'linear contextual calibration','H1':'E2 plus strictly-prior W/D/L Elo strength features'},
         'validation':{'E2':vm0,'H1':vm1,'delta_H1_minus_E2':{'top1_pp':100*(vm1['top1_accuracy']-vm0['top1_accuracy']),'logloss':vm1['logloss']-vm0['logloss'],'brier':vm1['brier']-vm0['brier'],'rps':vm1['rps']-vm0['rps']}},
         'test':{'E2':tm0,'H1':tm1,'delta_H1_minus_E2':{'top1_pp':100*(tm1['top1_accuracy']-tm0['top1_accuracy']),'logloss':tm1['logloss']-tm0['logloss'],'brier':tm1['brier']-tm0['brier'],'rps':tm1['rps']-tm0['rps']}}}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r7.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(out,indent=2));return out
def verify():
    m=json.loads((DATA/'source_manifest_r7.json').read_text());o=json.loads((OUT/'summary_r7.json').read_text())
    assert m['snapshot_rows']==N and m['overlap_with_r1_r6'] is False and m['source_has_odds_columns'] is False
    assert o['governance']['coverage_required']==1.0 and not o['governance']['abstention_used'] and not o['governance']['veto_used'] and not o['governance']['odds_used']
    print('VERIFY_OK')
if __name__=='__main__':
    import argparse
    q=argparse.ArgumentParser();q.add_argument('mode',choices=['freeze','run','verify','all']);a=q.parse_args()
    if a.mode in {'freeze','all'}:freeze()
    if a.mode in {'run','all'}:run()
    if a.mode in {'verify','all'}:verify()
