#!/usr/bin/env python3
from __future__ import annotations
import csv,gzip,hashlib,io,json,math,urllib.request
from collections import Counter,defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import numpy as np

SOURCE='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/games.csv.gz'
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';OUT=ROOT/'results';N=20000;PART=5000;MAXG=12
GH=1.45;GA=1.20;CP=30.;TP=6.;HALF=240.;LR=.06;BLEND=.50
FIELDS=['date','game_id','competition_id','season','competition_type','home_club_id','away_club_id','home_club_name','away_club_name','home_goals','away_goals']
def clamp(x,a,b):return min(b,max(a,x))
def pick(r,*xs):
    for x in xs:
        v=str(r.get(x) or '').strip()
        if v:return v
    return ''
def club(x):
    s=str(x or '').strip()
    if s.endswith('.0') and s[:-2].isdigit():s=s[:-2]
    return '' if s.lower() in {'','nan','none','null'} else s
def goal(x):
    try:v=float(str(x).strip())
    except:return None
    return int(round(v)) if math.isfinite(v) and v>=0 and abs(v-round(v))<1e-9 else None
def fsha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def freeze():
    DATA.mkdir(parents=True,exist_ok=True);req=urllib.request.Request(SOURCE,headers={'User-Agent':'football3-research/3.0'})
    with urllib.request.urlopen(req,timeout=300) as rr:raw=rr.read()
    rd=csv.DictReader(io.StringIO(gzip.decompress(raw).decode('utf-8-sig',errors='replace')));valid=[];bad=Counter();seen=set();src=0
    for r in rd:
        src+=1;ds=pick(r,'date')[:10]
        try:date.fromisoformat(ds)
        except:bad['date']+=1;continue
        gid=pick(r,'game_id');comp=pick(r,'competition_id');typ=pick(r,'competition_type');h=club(pick(r,'home_club_id'));a=club(pick(r,'away_club_id'));hg=goal(pick(r,'home_club_goals'));ag=goal(pick(r,'away_club_goals'))
        if not gid or not comp or not h or not a or h==a or gid in seen:bad['identity']+=1;continue
        if hg is None or ag is None:bad['score']+=1;continue
        if typ=='national_team_competition':bad['national_team']+=1;continue
        seen.add(gid);valid.append({'date':ds,'game_id':gid,'competition_id':comp,'season':pick(r,'season'),'competition_type':typ,'home_club_id':h,'away_club_id':a,'home_club_name':pick(r,'home_club_name'),'away_club_name':pick(r,'away_club_name'),'home_goals':hg,'away_goals':ag})
    valid.sort(key=lambda r:(r['date'],r['game_id']))
    if len(valid)<60000:raise RuntimeError('need >=60000 valid club rows')
    chosen=valid[-60000:-40000];parts=[]
    for i,s in enumerate(range(0,N,PART),1):
        p=DATA/f'matches_r3_20000_part{i:02d}.csv';q=chosen[s:s+PART]
        with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(q)
        parts.append({'path':str(p.relative_to(ROOT)),'rows':len(q),'sha256':fsha(p)})
    m={'schema_version':'football3-top1-20k-r3','status':'FROZEN_DISJOINT_20000','source_url':SOURCE,'source_compressed_sha256':hashlib.sha256(raw).hexdigest(),'source_rows':src,'valid_club_rows':len(valid),'selection':'valid[-60000:-40000], disjoint from R1 and R2','snapshot_rows':N,'first_date':chosen[0]['date'],'last_date':chosen[-1]['date'],'parts':parts,'excluded_counts':dict(bad),'overlap_with_r1_r2':False,'odds_used':False,'market_columns_persisted':False,'same_date_update_allowed':False}
    (DATA/'source_manifest_r3.json').write_text(json.dumps(m,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(m,indent=2));return m
@dataclass
class V:n:int=0;gf:float=0.;ga:float=0.
@dataclass
class L:attack:float=0.;defence:float=0.;matches:int=0;last:date|None=None
class S:
    def __init__(self):self.cn=Counter();self.ch=defaultdict(float);self.ca=defaultdict(float);self.v=defaultdict(V);self.l=defaultdict(L)
    def means(self,c):
        n=self.cn[c];return ((self.ch[c]+CP*GH)/(n+CP),(self.ca[c]+CP*GA)/(n+CP))
    def rate(self,s,x,p):return ((s.gf if x=='gf' else s.ga)+TP*p)/(s.n+TP)
    def dec(self,t,d):
        s=self.l[t]
        if s.last is None:return s.attack,s.defence,s.matches
        z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);return s.attack*z,s.defence*z,s.matches
    def pred(self,r):
        c=r['competition_id'];h=r['home_club_id'];a=r['away_club_id'];d=date.fromisoformat(r['date']);lh,la=self.means(c);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        ah=clamp(lh*(self.rate(hs,'gf',lh)/lh)*(self.rate(av,'ga',lh)/lh),.15,4.5);aa=clamp(la*(self.rate(av,'gf',la)/la)*(self.rate(hs,'ga',la)/la),.15,4.5)
        ha,hd,hn=self.dec(h,d);aa2,ad,an=self.dec(a,d);xh=clamp(lh*math.exp(ha+ad),.15,4.5);xa=clamp(la*math.exp(aa2+hd),.15,4.5);mh=math.exp((1-BLEND)*math.log(ah)+BLEND*math.log(xh));ma=math.exp((1-BLEND)*math.log(aa)+BLEND*math.log(xa));p=poisson_1x2(mh,ma)
        return {'p_home':p[0],'p_draw':p[1],'p_away':p[2],'mu_home':mh,'mu_away':ma,'mu_total':mh+ma,'latent_h':xh,'latent_a':xa,'home_history':hn,'away_history':an,'comp_history':self.cn[c]}
    def touch(self,t,d):
        s=self.l[t]
        if s.last is not None:
            z=math.exp(-math.log(2)*max(0,(d-s.last).days)/HALF);s.attack*=z;s.defence*=z
        s.last=d;return s
    def update(self,r,p):
        d=date.fromisoformat(r['date']);c=r['competition_id'];h=r['home_club_id'];a=r['away_club_id'];hg=int(r['home_goals']);ag=int(r['away_goals']);hs=self.v[(c,h,'H')];av=self.v[(c,a,'A')]
        hs.n+=1;hs.gf+=hg;hs.ga+=ag;av.n+=1;av.gf+=ag;av.ga+=hg;self.cn[c]+=1;self.ch[c]+=hg;self.ca[c]+=ag;H=self.touch(h,d);A=self.touch(a,d);eh=clamp((hg-p['latent_h'])/(1+p['latent_h']),-2,2);ea=clamp((ag-p['latent_a'])/(1+p['latent_a']),-2,2);H.attack=clamp(H.attack+LR*eh,-1.2,1.2);A.defence=clamp(A.defence+LR*eh,-1.2,1.2);A.attack=clamp(A.attack+LR*ea,-1.2,1.2);H.defence=clamp(H.defence+LR*ea,-1.2,1.2);H.matches+=1;A.matches+=1
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
    for p in sorted(DATA.glob('matches_r3_20000_part*.csv')):
        with p.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):r['home_goals']=int(r['home_goals']);r['away_goals']=int(r['away_goals']);z.append(r)
    z.sort(key=lambda r:(r['date'],r['game_id']));assert len(z)==N;return z
def feat_simple(p):
    v=np.clip([p['p_home'],p['p_draw'],p['p_away']],1e-8,1);return [math.log(v[0]/v[2]),math.log(v[1]/v[2])]
def feat_context(p):
    f=feat_simple(p);gap=abs(p['mu_home']-p['mu_away']);tot=p['mu_total']
    return f+[tot,tot*tot,gap,gap*gap,math.log1p(p['home_history']),math.log1p(p['away_history']),math.log1p(p['comp_history']),p['p_draw']*tot,p['p_draw']*gap]
def decorate(v,p):
    v=np.asarray(v,dtype=float);v=np.clip(v,1e-12,None);v=v/v.sum();o=np.argsort(-v);ent=float(-np.sum(v*np.log(v)))
    return {'p_home':float(v[0]),'p_draw':float(v[1]),'p_away':float(v[2]),'top1':int(o[0]),'margin':float(v[o[0]]-v[o[1]]),'entropy':ent,'mu_home':p['mu_home'],'mu_away':p['mu_away'],'mu_total':p['mu_total'],'home_history':p['home_history'],'away_history':p['away_history'],'comp_history':p['comp_history']}
def metrics(rows,key):
    n=len(rows);hit=ll=br=rps=dp=dh=ad=0
    for r in rows:
        p=r[key];y=r['y'];v=[p['p_home'],p['p_draw'],p['p_away']];hit+=p['top1']==y;ll-=math.log(max(v[y],1e-15));br+=sum((v[i]-(i==y))**2 for i in range(3));rps+=((v[0]-(y==0))**2+((v[0]+v[1])-(y<=1))**2)/2;dp+=p['top1']==1;dh+=p['top1']==1 and y==1;ad+=y==1
    return {'count':n,'hits':hit,'top1_accuracy':hit/n,'logloss':ll/n,'brier':br/n,'rps':rps/n,'draw_top1_picks':dp,'actual_draws':ad,'draw_top1_precision':dh/dp if dp else None,'draw_recall':dh/ad if ad else None}
def run():
    rows=load();st=S();pred=[];by=defaultdict(list)
    for r in rows:by[r['date']].append(r)
    for ds in sorted(by):
        pending=[]
        for r in sorted(by[ds],key=lambda x:x['game_id']):p=st.pred(r);pred.append({'date':ds,'game_id':r['game_id'],'y':actual(r),'raw':p});pending.append((r,p))
        for r,p in pending:st.update(r,p)
    train=pred[4000:12000];val=pred[12000:16000];test=pred[16000:]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y=[r['y'] for r in train]
    m1=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=3000,random_state=0));m2=make_pipeline(StandardScaler(),LogisticRegression(C=.5,max_iter=3000,random_state=0))
    m1.fit([feat_simple(r['raw']) for r in train],y);m2.fit([feat_context(r['raw']) for r in train],y)
    for subset in [val,test]:
        P1=m1.predict_proba([feat_simple(r['raw']) for r in subset]);P2=m2.predict_proba([feat_context(r['raw']) for r in subset])
        for r,a,b in zip(subset,P1,P2):r['E0']=decorate([r['raw']['p_home'],r['raw']['p_draw'],r['raw']['p_away']],r['raw']);r['E1']=decorate(a,r['raw']);r['E2']=decorate(b,r['raw'])
    vm={k:metrics(val,k) for k in ['E0','E1','E2']};chosen=min(vm,key=lambda k:vm[k]['logloss']);tm={k:metrics(test,k) for k in ['E0','E1','E2']}
    out={'schema_version':'football3-top1-20k-r3-draw-calibration','status':'COMPLETE','classification':'RESEARCH_ONLY_NOT_VALIDATED_NOT_PROMOTED','formal_weight':0,'governance':{'snapshot_rows':N,'disjoint_from_r1_r2':True,'same_date_results_withheld':True,'burn_in':4000,'calibration_train':8000,'family_validation':4000,'untouched_test':4000,'odds_used':False,'market_prices_used':False,'manual_match_adjustment':False,'test_labels_used_for_training_or_family_selection':False},'models':{'E0':'raw opponent-adjusted B Poisson 1X2','E1':'multinomial logistic calibration on raw log probability ratios','E2':'contextual multinomial calibration adds total goals, goal-gap and history maturity; no market inputs'},'validation':vm,'selected_family_by_validation_logloss':chosen,'test':tm,'selected_test':tm[chosen]}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'summary_r3.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');print(json.dumps(out,indent=2));return out
def verify():
    m=json.loads((DATA/'source_manifest_r3.json').read_text());o=json.loads((OUT/'summary_r3.json').read_text());assert m['snapshot_rows']==20000 and m['overlap_with_r1_r2'] is False;assert o['governance']['untouched_test']==4000 and not o['governance']['odds_used'] and o['formal_weight']==0;print('VERIFY_OK')
if __name__=='__main__':
    import argparse
    q=argparse.ArgumentParser();q.add_argument('mode',choices=['freeze','run','verify','all']);a=q.parse_args()
    if a.mode in {'freeze','all'}:freeze()
    if a.mode in {'run','all'}:run()
    if a.mode in {'verify','all'}:verify()
