import pandas as pd, numpy as np, hashlib, json
from lightgbm import LGBMClassifier
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.metrics import log_loss, roc_auc_score, accuracy_score
SEED=20260813
p='/mnt/data/closing_odds.csv.gz'; df=pd.read_csv(p)
df['dt']=pd.to_datetime(df.match_date); df=df.sort_values(['dt','match_id']).reset_index(drop=True)
df['hs']=df.home_score.astype(int); df['aws']=df.away_score.astype(int); df['draw_y']=(df.hs==df.aws).astype(np.int8); df['total']=df.hs+df.aws
df['low2']=(df.total<=2).astype(np.int8); df['btts']=((df.hs>0)&(df.aws>0)).astype(np.int8); df['absdiff']=(df.hs-df.aws).abs()
raw=np.c_[1/df.avg_odds_home_win,1/df.avg_odds_draw,1/df.avg_odds_away_win]; s=raw.sum(1); ok=np.isfinite(s)&(s>0)
probs=np.full_like(raw,np.nan); probs[ok]=raw[ok]/s[ok,None]
df['pH'],df['pD'],df['pA']=probs[:,0],probs[:,1],probs[:,2]; df['overround']=s-1; df['balance']=(df.pH-df.pA).abs(); df['entropy']=-(probs*np.log(np.clip(probs,1e-12,1))).sum(1)
df['maxgapD']=np.log(df.max_odds_draw/df.avg_odds_draw); df['maxgapH']=np.log(df.max_odds_home_win/df.avg_odds_home_win); df['maxgapA']=np.log(df.max_odds_away_win/df.avg_odds_away_win)
# strict-prior cumulative team state, vectorized
h=pd.DataFrame({'idx':df.index,'team':df.home_team,'dt':df.dt,'gf':df.hs,'ga':df.aws,'draw':df.draw_y,'total':df.total,'low2':df.low2,'btts':df.btts,'absdiff':df.absdiff,'venue':0})
a=pd.DataFrame({'idx':df.index,'team':df.away_team,'dt':df.dt,'gf':df.aws,'ga':df.hs,'draw':df.draw_y,'total':df.total,'low2':df.low2,'btts':df.btts,'absdiff':df.absdiff,'venue':1})
L=pd.concat([h,a],ignore_index=True).sort_values(['team','dt','idx','venue']).reset_index(drop=True); g=L.groupby('team',sort=False); cnt=g.cumcount(); L['cnt']=cnt
for m in ['gf','ga','draw','total','low2','btts','absdiff']:
    prior=g[m].cumsum()-L[m]
    L[m+'_hist']=prior/cnt.replace(0,np.nan)
H=L[L.venue==0].set_index('idx'); A=L[L.venue==1].set_index('idx')
features=['pH','pD','pA','overround','balance','entropy','maxgapD','maxgapH','maxgapA']
for side,Q in [('h',H),('a',A)]:
    df[side+'_cnt']=Q['cnt'].reindex(df.index).values; features.append(side+'_cnt')
    for m in ['gf','ga','draw','total','low2','btts','absdiff']:
        c=side+'_'+m; df[c]=Q[m+'_hist'].reindex(df.index).values; features.append(c)
for m in ['draw_y','total','low2']:
    gg=df.groupby('league',sort=False); cntl=gg.cumcount(); prior=gg[m].cumsum()-df[m]; df['lg_'+m]=prior/cntl.replace(0,np.nan); features.append('lg_'+m)
for m in ['gf','ga','draw','total','low2','btts','absdiff']:
    df['sum_'+m]=df['h_'+m]+df['a_'+m]; df['diff_'+m]=df['h_'+m]-df['a_'+m]; df['abs_'+m]=(df['h_'+m]-df['a_'+m]).abs(); features += ['sum_'+m,'diff_'+m,'abs_'+m]
elig=(df.h_cnt>=20)&(df.a_cnt>=20)&df[['pH','pD','pA']].notna().all(1)

# Fresh untouched 300 subtype-mechanism test
w=(df.dt>=pd.Timestamp('2015-06-13'))&(df.dt<pd.Timestamp('2015-06-20'))&elig
cand=df.loc[w,['match_id','dt']].copy(); cand['h']=cand.match_id.astype(str).map(lambda x: hashlib.sha256(f'{SEED}:SUBTYPE:{x}'.encode()).hexdigest())
ids=set(cand.sort_values(['h','match_id']).head(300).match_id)
test=df[df.match_id.isin(ids)].copy().sort_values(['dt','match_id']); assert len(test)==300
hist=df[(df.dt<pd.Timestamp('2015-06-13'))&elig].copy().tail(80000); core=hist.iloc[:60000].copy(); cal=hist.iloc[60000:].copy()
params=dict(n_estimators=100,learning_rate=.045,num_leaves=15,max_depth=5,min_child_samples=300,reg_lambda=12,reg_alpha=2,colsample_bytree=.8,random_state=SEED,verbosity=-1,n_jobs=-1)
def X(d): return d[features].astype('float32')
def subtype(d):
    hs=d.hs.values; aw=d.aws.values; y=np.zeros(len(d),dtype=int)
    dr=hs==aw; y[dr & (hs==0)]=1; y[dr & (hs==1)]=2; y[dr & (hs==2)]=3; y[dr & (hs>=3)]=4; return y
def fit(d):
    m=LGBMClassifier(objective='multiclass',num_class=5,**params); m.fit(X(d),subtype(d)); return m
def pdraw(m,d):
    P=m.predict_proba(X(d)); mp={int(c):P[:,i] for i,c in enumerate(m.classes_)}; return np.clip(sum(mp.get(k,np.zeros(len(d))) for k in [1,2,3,4]),1e-6,1-1e-6),P
m0=fit(core); sp,_=pdraw(m0,cal); pb=np.clip(cal.pD.values,1e-6,1-1e-6); y=cal.draw_y.values; z=np.log(sp/(1-sp))-np.log(pb/(1-pb)); off=np.log(pb/(1-pb))
def obj(q):
 b0,b1=q; pr=expit(off+b0+b1*z); return -(y*np.log(np.clip(pr,1e-12,1))+(1-y)*np.log(np.clip(1-pr,1e-12,1))).mean()+1e-4*(b0*b0+b1*b1)
o=minimize(obj,[0,.2],method='L-BFGS-B',bounds=[(-1,1),(-1.5,1.5)]); b0,b1=o.x
m=fit(hist); st,subP=pdraw(m,test); pb=np.clip(test.pD.values,1e-6,1-1e-6); pd_=expit(np.log(pb/(1-pb))+b0+b1*(np.log(st/(1-st))-np.log(pb/(1-pb))) ); ratio=test.pH.values/(test.pH.values+test.pA.values); P1=np.c_[(1-pd_)*ratio,pd_,(1-pd_)*(1-ratio)]; P0=test[['pH','pD','pA']].values; y3=np.where(test.hs>test.aws,0,np.where(test.hs==test.aws,1,2)); yd=test.draw_y.values
def met(P):
 pr=P.argmax(1); d=pr==1; hit=((pr==1)&(y3==1)).sum(); return {'ll':float(log_loss(y3,P,labels=[0,1,2])),'draw_ll':float(log_loss(yd,P[:,1],labels=[0,1])),'auc':float(roc_auc_score(yd,P[:,1])),'acc':float(accuracy_score(y3,pr)),'top1_draw':int(d.sum()),'hits':int(hit),'precision':float(hit/d.sum()) if d.sum() else None,'recall':float(hit/(y3==1).sum()),'actual_draws':int((y3==1).sum())}
bm,cm=met(P0),met(P1); e=-np.log(np.clip(P1[np.arange(300),y3],1e-12,1))+np.log(np.clip(P0[np.arange(300),y3],1e-12,1)); dates=test.dt.dt.date.astype(str).values; U=np.unique(dates); G=[np.where(dates==d)[0] for d in U]; rng=np.random.default_rng(SEED); vals=[]
for _ in range(3000):
 ids2=np.concatenate([G[j] for j in rng.integers(0,len(G),len(G))]); vals.append(e[ids2].mean())
ci=[float(np.quantile(vals,.05)),float(np.quantile(vals,.95))]
res={'schema':'DRAW-SUBTYPE-FRESH300-R1','source_rows':len(df),'train_n':len(hist),'test_n':300,'features_n':len(features),'test_dates':[str(test.dt.min()),str(test.dt.max())],'b0':float(b0),'b1':float(b1),'baseline':bm,'candidate':cm,'struct_raw_draw_ll':float(log_loss(yd,st,labels=[0,1])),'delta':{'ll':cm['ll']-bm['ll'],'draw_ll':cm['draw_ll']-bm['draw_ll'],'auc':cm['auc']-bm['auc'],'acc':cm['acc']-bm['acc']},'ci90':ci}; res['pass_core']=bool(res['delta']['ll']<0 and ci[1]<0 and res['delta']['auc']>=0)
json.dump(res,open('/mnt/data/subtype300_result.json','w'),indent=2)
out=test[['match_id','dt','league','home_team','away_team','hs','aws']].copy(); out[['baseline_pH','baseline_pD','baseline_pA']]=P0; out['struct_pD']=st; out[['candidate_pH','candidate_pD','candidate_pA']]=P1
for i,name in enumerate(['p_non_draw','p_00','p_11','p_22','p_33plus']): out[name]=subP[:,i]
out.to_csv('/mnt/data/subtype300_predictions.csv',index=False); print(json.dumps(res,indent=2))
