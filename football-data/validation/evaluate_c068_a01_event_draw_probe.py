#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, zipfile
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score
IDS_SHA='12ccdea126c4b92c2ea82ce4fbcbea54c8525885423371883f71339c1204adcf'
C=.1; W=.4; MINPD=.20; MAXGAP=.20; MINRES=.02; PRIOR=4.
MET=['events','pass_acc_rate','shot','shot_acc_rate','foul','corner','save_attempt','transitions']
FEAT=['ev_shot_sum','ev_shot_absdiff','ev_shotacc_sum','ev_passacc_absdiff','ev_transition_sum','ev_transition_absdiff','ev_corner_sum','ev_foul_sum','ev_event_sum','ev_save_sum']
def phda(lh,la):
 p=np.array([math.exp(-lh)*lh**k/math.factorial(k) for k in range(13)]);q=np.array([math.exp(-la)*la**k/math.factorial(k) for k in range(13)]);M=np.outer(p,q);M/=M.sum();return np.array([np.tril(M,-1).sum(),np.trace(M),np.triu(M,1).sum()])
def met(y,P):
 cls=np.array(['H','D','A']);y=np.asarray(y);pred=cls[P.argmax(1)];Y=np.column_stack([y==c for c in cls]).astype(float);idx={'H':0,'D':1,'A':2};d=pred=='D';a=y=='D';h=d&a
 return {'rows':len(y),'accuracy':float((pred==y).mean()),'macro_f1':float(f1_score(y,pred,labels=cls,average='macro',zero_division=0)),'log_loss':float(-np.mean([math.log(max(P[i,idx[v]],1e-15)) for i,v in enumerate(y)])),'brier':float(np.mean(np.sum((P-Y)**2,1))),'rps':float(np.mean(np.sum((np.cumsum(P,1)[:,:-1]-np.cumsum(Y,1)[:,:-1])**2,1)/2)),'draw_calls':int(d.sum()),'draw_hits':int(h.sum()),'draw_precision':float(h.sum()/d.sum()) if d.sum() else 0.,'draw_recall':float(h.sum()/a.sum()) if a.sum() else 0.,'draw_f1':float(f1_score(a.astype(int),d.astype(int),zero_division=0))}
def comp(P,q):
 H,D,A=P[:,0],P[:,1],P[:,2];e=(D>=MINPD)&(np.abs(H-A)<=MAXGAP)&((q-D)>=MINRES);Dn=D.copy();Dn[e]=(1-W)*D[e]+W*q[e];z=H+A;return np.column_stack([(1-Dn)*H/z,Dn,(1-Dn)*A/z]),e
def load(p):
 z=zipfile.ZipFile(p);pkg=json.loads(z.read('PACKAGE.json'));man=sorted([json.loads(x) for x in z.read('MANIFEST.jsonl').decode().splitlines() if x],key=lambda x:x['rank']);ids=[str(x['match_id']) for x in man];sha=hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()
 if pkg['package_id']!='A01' or pkg['match_count']!=400 or sha!=IDS_SHA: raise RuntimeError('A01 identity mismatch')
 cf={int(x['match_id']):x['competition_file'] for x in man};ms=[json.loads(x) for x in z.read('matches.jsonl').decode().splitlines() if x];ev={int(Path(n).stem):json.loads(z.read(n)) for n in z.namelist() if n.startswith('events/') and n.endswith('.json')};return cf,ms,ev
def esum(ev):
 out={}
 for mid,rr in ev.items():
  by=defaultdict(Counter);prev=None;tr=Counter()
  for e in rr:
   tid=e.get('teamId')
   if not tid: continue
   tid=int(tid);en=e.get('eventName','');sub=e.get('subEventName','');tags={t.get('id') for t in e.get('tags',[])};c=by[tid];c['events']+=1;c['pass']+=en=='Pass';c['pass_acc']+=en=='Pass' and 1801 in tags;c['shot']+=en=='Shot';c['shot_acc']+=en=='Shot' and 1801 in tags;c['foul']+=en=='Foul';c['corner']+=en=='Free Kick' and sub=='Corner';c['save_attempt']+=en=='Save attempt';tr[tid]+=prev is not None and prev!=tid;prev=tid
  for tid,c in by.items(): out[(mid,tid)]={'events':float(c['events']),'pass_acc_rate':float(c['pass_acc']/c['pass']) if c['pass'] else 0.,'shot':float(c['shot']),'shot_acc_rate':float(c['shot_acc']/c['shot']) if c['shot'] else 0.,'foul':float(c['foul']),'corner':float(c['corner']),'save_attempt':float(c['save_attempt']),'transitions':float(tr[tid])}
 return out
def build(cf,ms,ev):
 E=esum(ev);m=[]
 for x in ms:
  td=list(x['teamsData'].values());h=next(t for t in td if t['side']=='home');a=next(t for t in td if t['side']=='away');m.append({'match_id':int(x['wyId']),'dt':pd.to_datetime(x['dateutc'],utc=True),'date':pd.to_datetime(x['dateutc']).date(),'cid':int(x['competitionId']),'home':int(h['teamId']),'away':int(a['teamId']),'hg':int(h['score']),'ag':int(a['score']),'y':'H' if h['score']>a['score'] else 'A' if h['score']<a['score'] else 'D'})
 M=pd.DataFrame(m).sort_values(['dt','match_id']);TH=defaultdict(list);CH=defaultdict(list);R=[]
 for date,G in M.groupby('date',sort=True):
  for _,r in G.iterrows():
   h,a,c=int(r.home),int(r.away),int(r.cid);hc=CH[c];lgh=float(np.mean([x[0] for x in hc])) if hc else 1.4;lga=float(np.mean([x[1] for x in hc])) if hc else 1.1;lm=(lgh+lga)/2
   def A(t):
    x=TH[t];n=len(x);d={'n':n,'gf':float(np.mean([q['gf'] for q in x])) if n else lm,'ga':float(np.mean([q['ga'] for q in x])) if n else lm}
    for k in MET:d['f_'+k]=float(np.mean([q['f_'+k] for q in x])) if n else 0.;d['a_'+k]=float(np.mean([q['a_'+k] for q in x])) if n else 0.
    return d
   H=A(h);A_=A(a);hgf=(H['gf']*H['n']+PRIOR*lm)/(H['n']+PRIOR);hga=(H['ga']*H['n']+PRIOR*lm)/(H['n']+PRIOR);agf=(A_['gf']*A_['n']+PRIOR*lm)/(A_['n']+PRIOR);aga=(A_['ga']*A_['n']+PRIOR*lm)/(A_['n']+PRIOR);lh=float(np.clip(lgh*(hgf/lm)*(aga/lm),.2,3.5));la=float(np.clip(lga*(agf/lm)*(hga/lm),.2,3.5));P=phda(lh,la);f={**r.to_dict(),'hn':H['n'],'an':A_['n'],'pH':P[0],'pD':P[1],'pA':P[2]}
   for k in MET:home=(H['f_'+k]+A_['a_'+k])/2;away=(A_['f_'+k]+H['a_'+k])/2;f['sum_'+k]=home+away;f['diff_'+k]=home-away
   f.update(ev_shot_sum=f['sum_shot'],ev_shot_absdiff=abs(f['diff_shot']),ev_shotacc_sum=f['sum_shot_acc_rate'],ev_passacc_absdiff=abs(f['diff_pass_acc_rate']),ev_transition_sum=f['sum_transitions'],ev_transition_absdiff=abs(f['diff_transitions']),ev_corner_sum=f['sum_corner'],ev_foul_sum=f['sum_foul'],ev_event_sum=f['sum_events'],ev_save_sum=f['sum_save_attempt']);R.append(f)
  for _,r in G.iterrows():
   h,a=int(r.home),int(r.away);eh,ea=E[(int(r.match_id),h)],E[(int(r.match_id),a)];hr={'gf':float(r.hg),'ga':float(r.ag)};ar={'gf':float(r.ag),'ga':float(r.hg)}
   for k in MET:hr['f_'+k]=eh[k];hr['a_'+k]=ea[k];ar['f_'+k]=ea[k];ar['a_'+k]=eh[k]
   TH[h].append(hr);TH[a].append(ar);CH[int(r.cid)].append((float(r.hg),float(r.ag)))
 return pd.DataFrame(R).sort_values(['dt','match_id']).reset_index(drop=True)
def run(a01,out):
 cf,ms,ev=load(a01);D=build(cf,ms,ev);D=D[(D.hn>=2)&(D.an>=2)].reset_index(drop=True)
 if len(D)!=241:raise RuntimeError(f'eligible={len(D)}')
 fs={};YY=[];BB=[];CC=[]
 for name,s,e in [('fold_1',120,180),('fold_2',180,241)]:
  tr,te=D.iloc[:s],D.iloc[s:e];m=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=5000,class_weight=None));m.fit(tr[FEAT],(tr.y=='D').astype(int));q=m.predict_proba(te[FEAT])[:,1];P=te[['pH','pD','pA']].to_numpy(float);Q,elig=comp(P,q);b,c=met(te.y.values,P),met(te.y.values,Q);fs[name]={'train_rows':len(tr),'test_rows':len(te),'date_min':str(te.date.min()),'date_max':str(te.date.max()),'activation_eligible_rows':int(elig.sum()),'baseline':b,'candidate':c,'delta':{k:c[k]-b[k] for k in ['accuracy','macro_f1','log_loss','brier','rps','draw_calls','draw_hits','draw_f1']}};YY+=te.y.tolist();BB.append(P);CC.append(Q)
 b,c=met(np.array(YY),np.vstack(BB)),met(np.array(YY),np.vstack(CC));delta={k:c[k]-b[k] for k in ['accuracy','macro_f1','log_loss','brier','rps','draw_calls','draw_hits','draw_f1']};stable=delta['log_loss']<0 and delta['brier']<=0 and delta['rps']<=0 and c['draw_hits']>b['draw_hits'];x={'schema_version':'C068_A01_EVENT_DRAW_PROBE_R1','status':'POSTVIEW_A01_ONLY_DEVELOPMENT_PROBE_COMPLETE','verdict':'A01_EVENT_HISTORY_DEVELOPMENT_SIGNAL' if stable else 'A01_EVENT_HISTORY_STABLE_DRAW_INCREMENT_NOT_ESTABLISHED','source':{'package':'A01','matches':400,'ids_sha256':IDS_SHA,'semantics':'RESEARCH_DEVELOPMENT_ONLY_NOT_PROTECTED'},'contract':{'same_match_events_used_for_prediction':False,'history_update':'after UTC date batch','min_prior_A01_matches_each_team':2,'eligible_rows':241,'features':FEAT,'C':C,'class_weight':None,'blend':W,'min_pD':MINPD,'max_abs_HA_gap':MAXGAP,'min_residual':MINRES,'natural_argmax_only':True},'folds':fs,'pooled':{'rows':len(YY),'baseline':b,'candidate':c,'delta':delta},'boundary':{'A02_A05_used':False,'B07_used':False,'formal_weight':0,'scientific_pass':False,'confirmation_pass':False,'post_view_development_only':True}};Path(out).write_text(json.dumps(x,ensure_ascii=False,indent=2));print(json.dumps(x,ensure_ascii=False,indent=2))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--a01',required=True);a.add_argument('--out',required=True);q=a.parse_args();run(Path(q.a01),q.out)
