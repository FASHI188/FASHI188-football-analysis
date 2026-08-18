from __future__ import annotations
import argparse, hashlib, json, math, zipfile
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss

MATCHES_SHA='c8f92bb7533e5c127e043cee764c991b5c25b4f5e70a65be931baae0b1765ce9'
EVENTS_SHA='877e015b716ffdeea18f04418e3f24fed307ed03c37ff305cabe1f47c4822a45'
PRIOR=4.0
RATE_PC=8.0
HAZARD_PE=180.0
C=0.1
CONTROL=['baseline_pdraw','baseline_abs_home_away_prob_gap','baseline_expected_total_goals','baseline_abs_log_lambda_ratio','competition_scoring_environment','season_stage']
STATE=[
'draw_persistence_60_mean','draw_persistence_60_absdiff',
'draw_persistence_70_mean','draw_persistence_70_absdiff',
'equalizer_hazard_mean','equalizer_hazard_absdiff',
'rebreak_hazard_mean','rebreak_hazard_absdiff',
'late_tied_goal_hazard60_mean','late_tied_goal_hazard60_absdiff',
'late_tied_shot_hazard60_mean','late_tied_shot_hazard60_absdiff',
'late_tied_shot_hazard70_mean','late_tied_shot_hazard70_absdiff',
'late_tied_exit_hazard60_mean','late_tied_exit_hazard60_absdiff']
POS={(0,0),(1,1),(2,2)}
NEG={(1,0),(0,1),(2,1),(1,2)}
CAL={'baseline_pdraw':.04,'baseline_abs_home_away_prob_gap':.08,'baseline_expected_total_goals':.40,'baseline_abs_log_lambda_ratio':.35,'season_stage':.25}

def sha(p):
 return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def minute_of(e):
 p=e.get('matchPeriod'); sec=float(e.get('eventSec') or 0.)
 if p=='1H': return sec/60.
 if p=='2H': return 45.+sec/60.
 return None

def phda(lh,la):
 p=np.array([math.exp(-lh)*lh**k/math.factorial(k) for k in range(13)],float)
 q=np.array([math.exp(-la)*la**k/math.factorial(k) for k in range(13)],float)
 M=np.outer(p,q);M/=M.sum()
 return np.array([np.tril(M,-1).sum(),np.trace(M),np.triu(M,1).sum()])

def load_matches(matches_zip):
 z=zipfile.ZipFile(matches_zip); rows=[]
 for n in z.namelist():
  if not n.startswith('matches_') or not n.endswith('.json'): continue
  comp_file=Path(n).stem.removeprefix('matches_')
  for x in json.loads(z.read(n)):
   td=list(x['teamsData'].values()); h=next(t for t in td if t['side']=='home'); a=next(t for t in td if t['side']=='away')
   rows.append({'match_id':int(x['wyId']),'dt':pd.to_datetime(x['dateutc'],utc=True),'date':pd.to_datetime(x['dateutc'],utc=True).date(),'cid':int(x['competitionId']),'comp_file':comp_file,'duration':x.get('duration'),'home':int(h['teamId']),'away':int(a['teamId']),'hg':int(h['score']),'ag':int(a['score'])})
 return pd.DataFrame(rows).sort_values(['dt','match_id']).reset_index(drop=True)

def load_reduced_events(events_zip, wanted):
 z=zipfile.ZipFile(events_zip); red=defaultdict(list); endm=defaultdict(lambda:90.0); n_ev=0
 for n in z.namelist():
  if not n.startswith('events_') or not n.endswith('.json'): continue
  rr=json.loads(z.read(n)); n_ev += len(rr)
  for i,e in enumerate(rr):
   mid=int(e.get('matchId',-1))
   if mid not in wanted: continue
   m=minute_of(e)
   if m is None: continue
   endm[mid]=max(endm[mid],min(m,105.0))
   tags={int(t['id']) for t in e.get('tags',[]) if 'id' in t}
   goal=101 in tags; own=102 in tags; shot=(e.get('eventName')=='Shot')
   if goal or own or shot:
    red[mid].append((float(m),i,int(e.get('teamId') or -1),bool(shot),bool(goal),bool(own)))
 for mid in red: red[mid].sort(key=lambda x:(x[0],x[1]))
 return red,endm,n_ev

def state_at(goals,t):
 h=a=0
 for m,sc in goals:
  if m>t: break
  if sc=='H':h+=1
  else:a+=1
 return h,a

def summarize_match(r, evs, endm):
 home,away=int(r.home),int(r.away); h=a=0; goals=[]; shots=[]
 for m,idx,tid,shot,goal,own in evs:
  if m>endm+1e-9: continue
  if shot: shots.append((m,tid,h,a))
  if goal or own:
   if own:
    scorer='A' if tid==home else 'H' if tid==away else None
   else:
    scorer='H' if tid==home else 'A' if tid==away else None
   if scorer is None: continue
   pre=(h,a)
   if scorer=='H':h+=1
   else:a+=1
   goals.append((m,scorer,pre,(h,a),tid,own))
 ok=(h==int(r.hg) and a==int(r.ag))
 if not ok:return None
 ft=max(90.0,float(endm)); ft=min(ft,105.0)
 simple_goals=[(x[0],x[1]) for x in goals]
 h60,a60=state_at(simple_goals,60.0);h70,a70=state_at(simple_goals,70.0)
 final_draw=int(r.hg)==int(r.ag)
 eq={home:[0,0],away:[0,0]}; reb={home:[0,0],away:[0,0]}
 open_ep={home:False,away:False}; eq_events=[]
 for g in goals:
  m,sc,pre,post,tid,own=g; ph,pa=pre; nh,na=post
  for team,is_home in [(home,True),(away,False)]:
   pre_d=(ph-pa) if is_home else (pa-ph); post_d=(nh-na) if is_home else (na-nh)
   if post_d==-1 and pre_d!=-1:
    eq[team][0]+=1;open_ep[team]=True
   if post_d==0 and open_ep[team]:
    eq[team][1]+=1;open_ep[team]=False;reb[team][0]+=1;eq_events.append((team,m))
   if post_d<=-2 and open_ep[team]:
    open_ep[team]=False
 goal_times=[g[0] for g in goals]
 for team,m in eq_events:
  if any(t>m+1e-9 for t in goal_times): reb[team][1]+=1
 def tied_exposure(start):
  pts=[start]+[g[0] for g in goals if start<g[0]<ft]+[ft]
  exp=0.;episodes=0;exits=0
  gh=ga=0
  for g in goals:
   if g[0]<=start:
    if g[1]=='H':gh+=1
    else:ga+=1
  tied=(gh==ga)
  if tied: episodes+=1
  for i in range(len(pts)-1):
   lo,hi=pts[i],pts[i+1]
   if tied:exp+=max(0.,hi-lo)
   if i<len(pts)-2:
    scorer=next(g[1] for g in goals if abs(g[0]-hi)<1e-9)
    was=tied
    if scorer=='H':gh+=1
    else:ga+=1
    tied=(gh==ga)
    if was and not tied: exits+=1
    if (not was) and tied: episodes+=1
  return exp,episodes,exits
 exp60,ep60,exit60=tied_exposure(60.0); exp70,ep70,exit70=tied_exposure(70.0)
 shot60=shot70=goal60=0
 for m,tid,ph,pa in shots:
  if m>=60 and ph==pa: shot60+=1
  if m>=70 and ph==pa: shot70+=1
 for g in goals:
  m,sc,pre,*_=g
  if m>=60 and pre[0]==pre[1]: goal60+=1
 shared={'dp60_o':int(h60==a60),'dp60_s':int(h60==a60 and final_draw),'dp70_o':int(h70==a70),'dp70_s':int(h70==a70 and final_draw),'tg60_n':goal60,'te60':exp60,'ts60_n':shot60,'ts70_n':shot70,'te70':exp70,'tx60_n':exit60,'tx60_o':ep60}
 out={}
 for team in [home,away]:
  q=dict(shared);q.update(eq_o=eq[team][0],eq_s=eq[team][1],reb_o=reb[team][0],reb_s=reb[team][1]);out[team]=q
 return out

def rate(st, keyo, keys, gst, default=.5):
 go=gst[keyo]; gp=(gst[keys]/go) if go>0 else default
 return (st[keys]+RATE_PC*gp)/(st[keyo]+RATE_PC)
def haz(st, keyn, keye, gst, default):
 ge=gst[keye]; gp=(gst[keyn]/ge) if ge>0 else default
 return (st[keyn]+HAZARD_PE*gp)/(st[keye]+HAZARD_PE)

def team_features(st,gst):
 return {
  'draw_persistence_60':rate(st,'dp60_o','dp60_s',gst,.5),
  'draw_persistence_70':rate(st,'dp70_o','dp70_s',gst,.5),
  'equalizer_hazard':rate(st,'eq_o','eq_s',gst,.35),
  'rebreak_hazard':rate(st,'reb_o','reb_s',gst,.5),
  'late_tied_goal_hazard60':haz(st,'tg60_n','te60',gst,.025),
  'late_tied_shot_hazard60':haz(st,'ts60_n','te60',gst,.12),
  'late_tied_shot_hazard70':haz(st,'ts70_n','te70',gst,.12),
  'late_tied_exit_hazard60':rate(st,'tx60_o','tx60_n',gst,.5)
 }

def add_state_pair(f,H,A):
 for k in H:
  f[k+'_mean']=(H[k]+A[k])/2.;f[k+'_absdiff']=abs(H[k]-A[k])

def build_panel(M, summaries):
 M=M[M.match_id.isin(summaries)].copy().sort_values(['dt','match_id']).reset_index(drop=True)
 totals=M.groupby('cid').size().to_dict(); idx=Counter(); TH=defaultdict(list); CH=defaultdict(list); ST=defaultdict(Counter); GST=Counter(); R=[]
 for date,G in M.groupby('date',sort=True):
  for _,r in G.iterrows():
   h,a,c=int(r.home),int(r.away),int(r.cid); hc=CH[c]
   lgh=float(np.mean([x[0] for x in hc])) if hc else 1.4;lga=float(np.mean([x[1] for x in hc])) if hc else 1.1;lm=(lgh+lga)/2.
   def ga(t):
    x=TH[t];n=len(x);return n,(float(np.mean([q[0] for q in x])) if n else lm),(float(np.mean([q[1] for q in x])) if n else lm)
   hn,hgf,hga=ga(h);an,agf,aga=ga(a)
   hgf=(hgf*hn+PRIOR*lm)/(hn+PRIOR);hga=(hga*hn+PRIOR*lm)/(hn+PRIOR);agf=(agf*an+PRIOR*lm)/(an+PRIOR);aga=(aga*an+PRIOR*lm)/(an+PRIOR)
   lh=float(np.clip(lgh*(hgf/lm)*(aga/lm),.2,3.5));la=float(np.clip(lga*(agf/lm)*(hga/lm),.2,3.5));P=phda(lh,la)
   fh=team_features(ST[h],GST);fa=team_features(ST[a],GST)
   f={**r.to_dict(),'hn':hn,'an':an,'baseline_pdraw':P[1],'baseline_abs_home_away_prob_gap':abs(P[0]-P[2]),'baseline_expected_total_goals':lh+la,'baseline_abs_log_lambda_ratio':abs(math.log(lh/la)),'competition_scoring_environment':float(np.mean([x[0]+x[1] for x in hc])) if hc else lgh+lga,'season_stage':idx[c]/max(1,totals[c]-1)}
   add_state_pair(f,fh,fa);R.append(f)
  for _,r in G.iterrows():
   h,a,c=int(r.home),int(r.away),int(r.cid);TH[h].append((float(r.hg),float(r.ag)));TH[a].append((float(r.ag),float(r.hg)));CH[c].append((float(r.hg),float(r.ag)));idx[c]+=1
   for team in [h,a]:
    q=summaries[int(r.match_id)][team]
    ST[team].update(q);GST.update(q)
 return pd.DataFrame(R).sort_values(['dt','match_id']).reset_index(drop=True)

def target_label(hg,ag):
 s=(int(hg),int(ag))
 if s in POS:return 1
 if s in NEG:return 0
 return None

def fit_model(tr, feats):
 m=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=5000,class_weight=None,random_state=0))
 m.fit(tr[feats],tr.target.astype(int));return m

def greedy_match(te, scales):
 pos=te[te.target==1].sort_values(['date','match_id']);neg=te[te.target==0].sort_values(['date','match_id']);used=set();pairs=[]
 for _,p in pos.iterrows():
  cand=neg[(neg.cid==p.cid)&(~neg.match_id.isin(used))]
  best=None
  for _,n in cand.iterrows():
   ok=True
   for k,v in CAL.items():
    if abs(float(p[k])-float(n[k]))>v:ok=False;break
   if not ok:continue
   d=0.
   for k in CONTROL:
    s=scales.get(k,1.) or 1.;d+=((float(p[k])-float(n[k]))/s)**2
   item=(d,int(n.match_id),n)
   if best is None or item[0:2]<best[0:2]:best=item
  if best is not None:
   n=best[2];used.add(int(n.match_id));pairs.append((p,n,float(best[0])))
 return pairs

def clipprob(p):return np.clip(np.asarray(p,float),1e-6,1-1e-6)
def pair_metrics(pairs,pb,pc):
 lb=[];lc=[];accb=[];accc=[];rows=[]
 for p,n,d in pairs:
  ip,in_=int(p.match_id),int(n.match_id);bp,bn=float(pb[ip]),float(pb[in_]);cp,cn=float(pc[ip]),float(pc[in_])
  qb=(bp*(1-bn))/(bp*(1-bn)+bn*(1-bp));qc=(cp*(1-cn))/(cp*(1-cn)+cn*(1-cp));qb=float(np.clip(qb,1e-6,1-1e-6));qc=float(np.clip(qc,1e-6,1-1e-6));lb.append(-math.log(qb));lc.append(-math.log(qc));accb.append(bp>bn);accc.append(cp>cn);rows.append({'pos_id':ip,'neg_id':in_,'cid':int(p.cid),'pos_date':str(p.date),'neg_date':str(n.date),'distance':d,'baseline_pair_p':qb,'candidate_pair_p':qc,'baseline_loss':lb[-1],'candidate_loss':lc[-1]})
 return {'pairs':len(pairs),'baseline_paired_log_loss':float(np.mean(lb)) if lb else None,'candidate_paired_log_loss':float(np.mean(lc)) if lb else None,'paired_log_loss_delta':float(np.mean(np.array(lc)-np.array(lb))) if lb else None,'baseline_paired_accuracy':float(np.mean(accb)) if lb else None,'candidate_paired_accuracy':float(np.mean(accc)) if lb else None,'paired_accuracy_gain':float(np.mean(accc)-np.mean(accb)) if lb else None},rows

def row_metrics(df,p):
 y=df.target.to_numpy(int);p=clipprob(p)
 return {'rows':len(y),'log_loss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p)),'roc_auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'pr_auc':float(average_precision_score(y,p)) if len(set(y))>1 else None}

def calibration(df,p,bins=5):
 y=df.target.to_numpy(int);p=clipprob(p);qs=np.linspace(0,1,bins+1);out=[]
 for i in range(bins):
  lo,hi=qs[i],qs[i+1];mask=(p>=lo)&((p<hi) if i<bins-1 else (p<=hi))
  if mask.any():out.append({'lo':lo,'hi':hi,'n':int(mask.sum()),'mean_p':float(p[mask].mean()),'rate':float(y[mask].mean())})
 return out

def bootstrap(pair_rows,reps=5000,seed=6901):
 d=np.array([r['candidate_loss']-r['baseline_loss'] for r in pair_rows],float);rng=np.random.default_rng(seed)
 if len(d)==0:return None
 vals=np.empty(reps)
 for i in range(reps): vals[i]=d[rng.integers(0,len(d),len(d))].mean()
 return {'reps':reps,'observed':float(d.mean()),'p05':float(np.quantile(vals,.05)),'p50':float(np.quantile(vals,.5)),'p95':float(np.quantile(vals,.95)),'p_candidate_better':float(np.mean(vals<0))}

def run(matches_zip,events_zip,contract,out):
 if sha(matches_zip)!=MATCHES_SHA:raise RuntimeError('matches sha mismatch')
 if sha(events_zip)!=EVENTS_SHA:raise RuntimeError('events sha mismatch')
 json.loads(Path(contract).read_text());contract_sha=sha(contract)
 M=load_matches(matches_zip); raw_matches=len(M);M=M[M.duration=='Regular'].copy();wanted=set(M.match_id.astype(int));red,endm,n_ev=load_reduced_events(events_zip,wanted)
 summaries={};bad=[]
 for _,r in M.iterrows():
  q=summarize_match(r,red.get(int(r.match_id),[]),endm.get(int(r.match_id),90.0))
  if q is None:bad.append(int(r.match_id))
  else:summaries[int(r.match_id)]=q
 P=build_panel(M,summaries);P=P[(P.hn>=5)&(P.an>=5)].copy();P['target']=[target_label(h,a) for h,a in zip(P.hg,P.ag)]
 all_eligible=P.copy();T=P[P.target.notna()].copy();T.target=T.target.astype(int)
 dates=sorted(all_eligible.date.unique()); cuts=[int(len(dates)*x) for x in [.4,.6,.8,1.0]];cuts=[max(1,min(len(dates),x)) for x in cuts]
 folds=[];all_pair_rows=[];matched_rows=[]
 for fi in range(3):
  test_start=dates[cuts[fi]] if cuts[fi]<len(dates) else None; test_end=dates[cuts[fi+1]-1]
  if test_start is None:continue
  tr=T[T.date<test_start].copy();te=T[(T.date>=test_start)&(T.date<=test_end)].copy()
  if tr.target.nunique()<2 or te.target.nunique()<2:raise RuntimeError(f'fold {fi+1} lacks class')
  mb=fit_model(tr,CONTROL);mc=fit_model(tr,CONTROL+STATE);pb=clipprob(mb.predict_proba(te[CONTROL])[:,1]);pc=clipprob(mc.predict_proba(te[CONTROL+STATE])[:,1]);pbm=dict(zip(te.match_id.astype(int),pb));pcm=dict(zip(te.match_id.astype(int),pc));scales={k:float(tr[k].std(ddof=0)) if float(tr[k].std(ddof=0))>1e-9 else 1. for k in CONTROL};pairs=greedy_match(te,scales);pm,prows=pair_metrics(pairs,pbm,pcm)
  ids=set([x for r in prows for x in [r['pos_id'],r['neg_id']]]);mr=te[te.match_id.isin(ids)].copy()
  if len(mr):
   mrpb=np.array([pbm[int(x)] for x in mr.match_id]);mrpc=np.array([pcm[int(x)] for x in mr.match_id]);bm=row_metrics(mr,mrpb);cm=row_metrics(mr,mrpc);delta={'log_loss':cm['log_loss']-bm['log_loss'],'brier':cm['brier']-bm['brier']}
  else:
   bm=cm={'rows':0,'log_loss':None,'brier':None,'roc_auc':None,'pr_auc':None};delta={'log_loss':None,'brier':None}
  folds.append({'fold':fi+1,'train_rows':len(tr),'test_target_rows':len(te),'test_date_min':str(test_start),'test_date_max':str(test_end),'pair_metrics':pm,'matched_row_baseline':bm,'matched_row_candidate':cm,'matched_row_delta':delta})
  for r in prows:r['fold']=fi+1
  all_pair_rows.extend(prows)
  for _,r in mr.iterrows():matched_rows.append({'fold':fi+1,'match_id':int(r.match_id),'target':int(r.target),'cid':int(r.cid),'baseline_p':pbm[int(r.match_id)],'candidate_p':pcm[int(r.match_id)]})
 pair_n=len(all_pair_rows); pooled_pair={'pairs':pair_n}
 if pair_n:
  bl=np.array([r['baseline_loss'] for r in all_pair_rows]);cl=np.array([r['candidate_loss'] for r in all_pair_rows]);ba=np.array([r['baseline_pair_p']>.5 for r in all_pair_rows]);ca=np.array([r['candidate_pair_p']>.5 for r in all_pair_rows]);pooled_pair.update(baseline_paired_log_loss=float(bl.mean()),candidate_paired_log_loss=float(cl.mean()),paired_log_loss_delta=float((cl-bl).mean()),baseline_paired_accuracy=float(ba.mean()),candidate_paired_accuracy=float(ca.mean()),paired_accuracy_gain=float(ca.mean()-ba.mean()))
 MR=pd.DataFrame(matched_rows);pooled_rows={}
 if len(MR):
  bm=row_metrics(MR,MR.baseline_p.to_numpy());cm=row_metrics(MR,MR.candidate_p.to_numpy());pooled_rows={'baseline':bm,'candidate':cm,'delta':{'log_loss':cm['log_loss']-bm['log_loss'],'brier':cm['brier']-bm['brier']},'baseline_calibration':calibration(MR,MR.baseline_p.to_numpy()),'candidate_calibration':calibration(MR,MR.candidate_p.to_numpy())}
 comp={}
 for cid,rr in pd.DataFrame(all_pair_rows).groupby('cid') if all_pair_rows else []:
  d=rr.candidate_loss-rr.baseline_loss;comp[str(int(cid))]={'pairs':len(rr),'paired_ll_delta':float(d.mean()),'candidate_paired_accuracy':float((rr.candidate_pair_p>.5).mean())}
 boot=bootstrap(all_pair_rows)
 foldwins=sum(1 for f in folds if f['pair_metrics']['paired_log_loss_delta'] is not None and f['pair_metrics']['paired_log_loss_delta']<0)
 coverage=(pair_n>=100 and all(f['pair_metrics']['pairs']>=20 for f in folds))
 gate=bool(coverage and pooled_pair.get('candidate_paired_accuracy',0)>=.60 and pooled_pair.get('paired_accuracy_gain',-9)>=.03 and pooled_pair.get('paired_log_loss_delta',9)<=-.01 and boot and boot['p95']<0 and foldwins>=2 and pooled_rows.get('delta',{}).get('log_loss',9)<=0 and pooled_rows.get('delta',{}).get('brier',9)<=0)
 verdict='STATE_DYNAMICS_DEVELOPMENT_SIGNAL_ESTABLISHED' if gate else ('STOP_DATA_COVERAGE' if not coverage else 'STATE_DYNAMICS_STABLE_INCREMENT_NOT_ESTABLISHED')
 result={'schema_version':'C069_MATCHED_PAIR_STATE_DYNAMICS_R1','status':'POSTVIEW_RETROSPECTIVE_DEVELOPMENT_COMPLETE','verdict':verdict,'contract_sha256':contract_sha,'source':{'raw_matches':raw_matches,'regular_matches':len(M),'events_seen':n_ev,'state_reconciled_matches':len(summaries),'state_mismatch_matches':len(bad),'mismatch_ids_sha256':hashlib.sha256(('\n'.join(map(str,sorted(bad)))+'\n').encode()).hexdigest(),'market_odds_available':False},'panel':{'prematch_eligible_rows':len(all_eligible),'target_rows':len(T),'positive_rows':int(T.target.sum()),'negative_rows':int((1-T.target).sum()),'date_count':len(dates)},'folds':folds,'pooled_pair':pooled_pair,'pooled_matched_rows':pooled_rows,'bootstrap':boot,'competition_stability':comp,'gate':{'coverage':coverage,'fold_primary_wins':foldwins,'development_signal':gate},'boundary':{'retrospective_open_data':True,'historical_pit_proof':False,'market_matched_test':False,'protected_samples_used':False,'formal_weight':0,'scientific_pass':False,'confirmation_pass':False,'formal_promotion':False,'no_same_match_event_leakage':True,'same_date_predict_before_update':True},'interpretation':{'allowed':'post-view development evidence about whether prior-match state dynamics carry incremental discrimination beyond frozen structural controls','forbidden':'claiming independent scientific confirmation, formal promotion, or full market/AH/OU-matched identification'}}
 Path(out).parent.mkdir(parents=True,exist_ok=True);Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2));Path(out).with_name('pairs.jsonl').write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in all_pair_rows));print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--matches',required=True);ap.add_argument('--events',required=True);ap.add_argument('--contract',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();run(Path(a.matches),Path(a.events),Path(a.contract),Path(a.out))
