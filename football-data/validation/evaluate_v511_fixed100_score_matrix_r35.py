#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,math,random
from collections import Counter,defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import evaluate_v510_market_residual_1x2_r1 as base
import evaluate_v511_fixed100_market_residual_r34 as r34
ROOT=Path(__file__).resolve().parents[1]
TOTALS=("0","1","2","3","4","5","6","7+")
OUTCOMES=("home","draw","away")
EPS=1e-15
class E(RuntimeError): pass

def load(p):
 v=json.loads(Path(p).read_text(encoding="utf-8"));
 if not isinstance(v,dict): raise E("json root")
 return v

def sf(v):
 try:x=float(str(v).strip())
 except (TypeError,ValueError):return None
 return x if math.isfinite(x) else None

def si(v):
 x=sf(v)
 return int(round(x)) if x is not None and abs(x-round(x))<1e-9 else None

def nt(v):return " ".join(str(v or "").strip().casefold().split())
def hbytes(b):return hashlib.sha256(b).hexdigest()
def hfile(p):return hbytes(Path(p).read_bytes())
def norm(p):return base.normalized_probs(p)
def bucket(x,e):return base.bucket(x,e)
def dv(a,b):
 ia,ib=1/a,1/b;s=ia+ib;return ia/s,ib/s

def ou25(row):
 pairs=[("PC>2.5","PC<2.5","Pinnacle_closing"),("P>2.5","P<2.5","Pinnacle"),("AvgC>2.5","AvgC<2.5","AvgC"),("B365C>2.5","B365C<2.5","B365C"),("Avg>2.5","Avg<2.5","Avg"),("B365>2.5","B365<2.5","B365")]
 for o,u,n in pairs:
  a,b=sf(row.get(o)),sf(row.get(u))
  if a and b and a>1 and b>1:
   po,pu=dv(a,b);return {"provider":n,"over":po,"under":pu,"over_odds":a,"under_odds":b}
 return None

def has_ah(row):
 lines=("AHCh","AHh");pairs=(("PCAHH","PCAHA"),("PAHH","PAHA"),("B365CAHH","B365CAHA"),("B365AHH","B365AHA"))
 return any(sf(row.get(k)) is not None for k in lines) and any((sf(row.get(a)) or 0)>1 and (sf(row.get(b)) or 0)>1 for a,b in pairs)

class Sources:
 def __init__(self):self.c={}
 def rows(self,rel):
  if rel not in self.c:
   p=ROOT/rel
   if not p.is_file():raise E(f"missing {rel}")
   with p.open(encoding="utf-8-sig",newline="") as f:self.c[rel]=list(csv.DictReader(f))
  return self.c[rel]
 def resolve(self,b):
  rel=str(b.get("source_file") or "");rows=self.rows(rel);idx=si(b.get("row_index"));
  def ok(r):return nt(r.get("HomeTeam"))==nt(b.get("home_team")) and nt(r.get("AwayTeam"))==nt(b.get("away_team"))
  for j in ([idx,idx-1,idx+1] if idx is not None else []):
   if 0<=j<len(rows) and ok(rows[j]):return rows[j],"index"
  cand=[r for r in rows if ok(r)]
  season=nt(b.get("season"));cand2=[r for r in cand if season in {nt(r.get("season")),nt(r.get("Season")),nt(r.get("source_season"))}]
  if len(cand2)==1:return cand2[0],"identity_season"
  if len(cand)==1:return cand[0],"identity"
  raise E(f"source resolve {r34.identity(b)} candidates={len(cand)}")

def pre_source(rows):
 s=Sources();out=[];audit=Counter();byc=defaultdict(Counter);methods=Counter()
 for r in rows:
  x,m=s.resolve(r);methods[m]+=1;q=ou25(x);ah=has_ah(x)
  audit["score"]+=int("FTHG" in x and "FTAG" in x);audit["ou25"]+=int(q is not None);audit["asian"]+=int(ah)
  c=str(r["competition_id"]);byc[c]["rows"]+=1;byc[c]["ou25"]+=int(q is not None);byc[c]["asian"]+=int(ah)
  out.append({**r,"_src":x,"ou25":q,"asian":ah})
 return out,{"availability":dict(audit),"methods":dict(methods),"by_competition":{k:dict(v) for k,v in sorted(byc.items())}}

def labels(rows):
 out=[]
 for r in rows:
  h,a=si(r["_src"].get("FTHG")),si(r["_src"].get("FTAG"))
  if h is None or a is None or min(h,a)<0:raise E("bad score "+r34.identity(r))
  act="home" if h>a else "away" if h<a else "draw"
  if act!=base.actual_direction(r):raise E("result mismatch "+r34.identity(r))
  out.append({**r,"hg":h,"ag":a,"actual":act})
 return out

def tbin(h,a):return str(h+a) if h+a<=6 else "7+"
def ds(h,a,t):return ("H" if h>a else "A" if h<a else "D") if t=="7+" else h-a
def support(t):return ("H","D","A") if t=="7+" else tuple(range(-int(t),int(t)+1,2))
def uni(xs):
 xs=list(xs);return {x:1/len(xs) for x in xs}
def post(cnt,prior,k):
 n=sum(cnt[x] for x in prior);d=n+k;return {x:(cnt[x]+k*prior[x])/d for x in prior}
def state_out(s):
 if s.startswith("7+_"):return {"H":"home","D":"draw","A":"away"}[s[-1]]
 h,a=map(int,s.split("-"));return "home" if h>a else "away" if h<a else "draw"
def over(s):
 if s.startswith("7+_"):return True
 h,a=map(int,s.split("-"));return h+a>=3
def normm(m):
 z=sum(max(EPS,v) for v in m.values());return {k:max(EPS,v)/z for k,v in m.items()}
def agg1(m):
 x={o:0.0 for o in OUTCOMES}
 for s,p in m.items():x[state_out(s)]+=p
 return x
def aggo(m):
 o=sum(p for s,p in m.items() if over(s));return {"over":o,"under":1-o}
def gtarget(a,b,w):
 x={k:max(EPS,a[k])**(1-w)*max(EPS,b[k])**w for k in a};z=sum(x.values());return {k:v/z for k,v in x.items()}
def proj(m,groups,target):
 cur=defaultdict(float)
 for s,p in m.items():cur[groups(s)]+=p
 x={s:p*target[groups(s)]/max(EPS,cur[groups(s)]) for s,p in m.items()};return normm(x)
def kl(a,b):return sum(p*math.log(max(EPS,p)/max(EPS,b[s])) for s,p in a.items())

class Model:
 def __init__(self,c):
  self.c=c;self.tg=Counter();self.tc=defaultdict(Counter);self.tx=defaultdict(Counter);self.dg=defaultdict(Counter);self.dc=defaultdict(Counter);self.dx=defaultdict(Counter)
  self.elo=defaultdict(lambda:float(c["elo_initial"]));self.rt=defaultdict(lambda:deque(maxlen=int(c["recent_total_window"])))
 def f(self,r):
  c,h,a=str(r["competition_id"]),str(r["home_team"]),str(r["away_team"]);m=norm(r["market"]["probabilities"]);pk=max(OUTCOMES,key=lambda z:m[z])
  pb=bucket(max(m.values()),list(map(float,self.c["pmax_edges"])));db=bucket(m["draw"],list(map(float,self.c["pdraw_edges"])))
  ed=self.elo[(c,h)]+float(self.c["elo_home_advantage"])-self.elo[(c,a)];eb=bucket(ed,list(map(float,self.c["elo_diff_edges"])))
  if len(self.rt[(c,h)])<int(self.c["recent_total_minimum"]) or len(self.rt[(c,a)])<int(self.c["recent_total_minimum"]):rb="NA"
  else:rb=str(bucket((sum(self.rt[(c,h)])/len(self.rt[(c,h)])+sum(self.rt[(c,a)])/len(self.rt[(c,a)]))/2,list(map(float,self.c["recent_total_edges"]))))
  return {"c":c,"h":h,"a":a,"m":m,"pk":pk,"pb":pb,"db":db,"eb":eb,"tk":(c,pk,pb,db,rb)}
 def pred(self,f):
  g=post(self.tg,uni(TOTALS),float(self.c["total_global_prior_strength"]));cp=post(self.tc[(f["c"],)],g,float(self.c["total_comp_prior_strength"]));tp=post(self.tx[f["tk"]],cp,float(self.c["total_cell_prior_strength"]));m={}
  for t in TOTALS:
   gd=post(self.dg[t],uni(support(t)),float(self.c["diff_global_prior_strength"]));ck=(f["c"],t,f["pk"]);cd=post(self.dc[ck],gd,float(self.c["diff_comp_prior_strength"]));xk=(f["c"],t,f["pk"],f["pb"],f["db"],f["eb"]);dp=post(self.dx[xk],cd,float(self.c["diff_cell_prior_strength"]));
   if t=="7+":
    for d,p in dp.items():m[f"7+_{d}"]=tp[t]*p
   else:
    n=int(t)
    for d,p in dp.items():m[f"{(n+int(d))//2}-{(n-int(d))//2}"]=tp[t]*p
  return normm(m),tp
 def update(self,r,f):
  h,a=r["hg"],r["ag"];t=tbin(h,a);d=ds(h,a,t);self.tg[t]+=1;self.tc[(f["c"],)][t]+=1;self.tx[f["tk"]][t]+=1;self.dg[t][d]+=1;ck=(f["c"],t,f["pk"]);self.dc[ck][d]+=1;xk=(f["c"],t,f["pk"],f["pb"],f["db"],f["eb"]);self.dx[xk][d]+=1
  hr,ar=self.elo[(f["c"],f["h"])],self.elo[(f["c"],f["a"])];ex=1/(1+10**(-((hr+float(self.c["elo_home_advantage"])-ar)/400)));ac=1 if h>a else .5 if h==a else 0;dd=float(self.c["elo_k"])*(ac-ex);self.elo[(f["c"],f["h"])]=hr+dd;self.elo[(f["c"],f["a"])]=ar-dd;tot=h+a;self.rt[(f["c"],f["h"])].append(tot);self.rt[(f["c"],f["a"])].append(tot)

def candidate(prior,market,q,spec):
 m=prior;steps=[];ow=float(spec.get("ou_weight",0));xw=float(spec.get("x1_weight",0))
 if ow:
  if not q:raise E("missing ou")
  tar=gtarget(aggo(m),{"over":q["over"],"under":q["under"]},ow);before=m;m=proj(m,lambda s:"over" if over(s) else "under",tar);steps.append({"type":"ou","kl":kl(m,before),"res":abs(aggo(m)["over"]-tar["over"])})
 if xw:
  tar=gtarget(agg1(m),market,xw);before=m;m=proj(m,state_out,tar);fit=agg1(m);steps.append({"type":"1x2","kl":kl(m,before),"res":max(abs(fit[o]-tar[o]) for o in OUTCOMES)})
 return m,{"sum":sum(m.values()),"max_res":max([x["res"] for x in steps] or [0]),"steps":steps}

def metrics(rows,key):
 hit=ll=br=rp=0;pred=Counter();dc=ad=nh=nn=0
 for r in rows:
  p=r[key];a=r["actual"];pk=max(OUTCOMES,key=lambda x:p[x]);pred[pk]+=1;hit+=pk==a;ll-=math.log(max(EPS,p[a]));y={x:float(x==a) for x in OUTCOMES};br+=sum((p[x]-y[x])**2 for x in OUTCOMES);rp+=((p["home"]-y["home"])**2+(p["home"]+p["draw"]-y["home"]-y["draw"])**2)/2
  if a=="draw":ad+=1;dc+=pk=="draw"
  else:nn+=1;nh+=pk==a
 n=len(rows);pd=pred["draw"]
 return {"rows":n,"hits":hit,"accuracy":hit/n,"log_loss":ll/n,"brier":br/n,"rps":rp/n,"predicted_home":pred["home"],"predicted_draw":pd,"predicted_away":pred["away"],"actual_draws":ad,"draw_precision":dc/pd if pd else None,"draw_recall":dc/ad if ad else None,"draw_f1":2*dc/(pd+ad) if pd+ad else None,"non_draw_accuracy":nh/nn if nn else None}
def joint(rows,key):
 sh=th=jll=tll=0
 for r in rows:
  m=r[key];act=f"7+_{'H' if r['hg']>r['ag'] else 'A' if r['hg']<r['ag'] else 'D'}" if r["hg"]+r["ag"]>=7 else f"{r['hg']}-{r['ag']}";sh+=max(m,key=m.get)==act;jll-=math.log(max(EPS,m.get(act,EPS)));td={t:0.0 for t in TOTALS}
  for s,p in m.items():td["7+" if s.startswith("7+_") else str(sum(map(int,s.split("-"))))]+=p
  at=tbin(r["hg"],r["ag"]);th+=max(TOTALS,key=lambda x:td[x])==at;tll-=math.log(max(EPS,td[at]))
 n=len(rows);return {"rows":n,"joint_top1_accuracy":sh/n,"joint_log_loss":jll/n,"total_top1_accuracy":th/n,"total_log_loss":tll/n}
def boot(rows,key,n,seed):
 rng=random.Random(seed);vals=[];ll=[];br=[];N=len(rows)
 for _ in range(n):
  da=dl=db=0
  for _ in range(N):
   r=rows[rng.randrange(N)];a=r["actual"];m=r["market"];p=r[key];da+=int(max(OUTCOMES,key=lambda x:p[x])==a)-int(max(OUTCOMES,key=lambda x:m[x])==a);dl+=-math.log(max(EPS,p[a]))+math.log(max(EPS,m[a]));y={x:float(x==a) for x in OUTCOMES};db+=sum((p[x]-y[x])**2 for x in OUTCOMES)-sum((m[x]-y[x])**2 for x in OUTCOMES)
  vals.append(da/N);ll.append(dl/N);br.append(db/N)
 def q(x,z):
  x=sorted(x);i=z*(len(x)-1);a=int(i);b=min(a+1,len(x)-1);return x[a]+(x[b]-x[a])*(i-a)
 return {"accuracy":{"p05":q(vals,.05),"median":q(vals,.5),"p95":q(vals,.95)},"log_loss":{"p05":q(ll,.05),"median":q(ll,.5),"p95":q(ll,.95)},"brier":{"p05":q(br,.05),"median":q(br,.5),"p95":q(br,.95)}}

def manifest(out):
 fs=[]
 for p in sorted(out.iterdir()):
  if p.is_file() and p.name!="manifest.json":fs.append({"name":p.name,"bytes":p.stat().st_size,"sha256":hfile(p)})
 (out/"manifest.json").write_text(json.dumps({"schema":"r35-manifest","files":fs},indent=2),encoding="utf-8")
def run(cfg,out):
 bench=load(ROOT/cfg["source_benchmark"]);pool,ex,total=r34.prepare_market_pool(bench);pool,audit=pre_source(pool);old,_=r34.fixed_sample(pool,100,int(cfg["sample_contract"]["excluded_r34_seed"]));avail=[r for r in pool if r["_identity"] not in old and r["ou25"]]
 out.mkdir(parents=True,exist_ok=True)
 if len(avail)<100:
  res={"schema_version":cfg["schema_version"],"status":"STOP_INSUFFICIENT_EXISTING_OU25_SAMPLE_BEFORE_LABELS","source":{"source_rows":total,"market_complete_rows":len(pool),"external_collection":0},"field_audit":audit,"sample":{"target":100,"ou25_remaining":len(avail),"score_labels_parsed":0},"hard_limits":cfg["hard_limits"]};(out/"status.json").write_text(json.dumps(res,indent=2),encoding="utf-8");manifest(out);return res
 sel,quota=r34.fixed_sample(avail,100,int(cfg["sample_contract"]["seed"]));sha=hbytes(("\n".join(sorted(sel))+"\n").encode());lab=labels(pool);lab.sort(key=lambda r:(str(r["date"]),str(r["competition_id"]),str(r["home_team"]),str(r["away_team"])));by=defaultdict(list)
 for r in lab:by[str(r["date"])[:10]].append(r)
 model=Model(cfg["model"]);pred=[];specs=cfg["candidate_contract"]["fixed_candidates"]
 for day in sorted(by):
  frozen=[]
  for r in by[day]:
   f=model.f(r);prior,tp=model.pred(f);z={"id":r["_identity"],"competition_id":r["competition_id"],"season":r.get("season"),"date":r["date"],"home_team":r["home_team"],"away_team":r["away_team"],"hg":r["hg"],"ag":r["ag"],"actual":r["actual"],"selected":r["_identity"] in sel,"market":f["m"],"ou25":r["ou25"],"prior_total":tp}
   for s in specs:
    m,a=candidate(prior,f["m"],r["ou25"],s);z["m_"+s["name"]]=m;z["p_"+s["name"]]=agg1(m);z["a_"+s["name"]]=a
   pred.append(z);frozen.append((r,f))
  for r,f in frozen:model.update(r,f)
 rows=[r for r in pred if r["selected"]];mm={"market":metrics(rows,"market")};jm={};bs={};gates={};good=[]
 for i,s in enumerate(specs):
  n=s["name"];mm[n]=metrics(rows,"p_"+n);jm[n]=joint(rows,"m_"+n);bs[n]=boot(rows,"p_"+n,int(cfg["evaluation"]["paired_bootstrap"]["samples"]),int(cfg["evaluation"]["paired_bootstrap"]["seed"])+i);g={"accuracy_better":mm[n]["accuracy"]>mm["market"]["accuracy"],"log_loss_nonworse":mm[n]["log_loss"]<=mm["market"]["log_loss"]+1e-12,"brier_nonworse":mm[n]["brier"]<=mm["market"]["brier"]+1e-12,"accuracy_p05_positive":bs[n]["accuracy"]["p05"]>0,"draw_exists":mm[n]["predicted_draw"]>0};g["passed"]=all(g.values());gates[n]=g
  if g["passed"] and float(s.get("x1_weight",0))<1:good.append(n)
 coord={s["name"]:{"all_sum_one":all(abs(r["a_"+s["name"]]["sum"]-1)<=1e-12 for r in rows),"max_res":max(r["a_"+s["name"]]["max_res"] for r in rows)} for s in specs}
 res={"schema_version":cfg["schema_version"],"generated_at_utc":datetime.now(timezone.utc).isoformat(),"status":"PROMISING_SCORE_MATRIX_SIGNAL_EXPLORATION_ONLY" if good else "NO_SCORE_MATRIX_INCREMENT_FIXED100_EXPLORATION_ONLY","classification":cfg["classification"],"source":{"source_rows":total,"market_complete_rows":len(pool),"pre_result_exclusions":ex,"external_collection":0,"provider_requests":0,"closing_prices_without_timestamp":True},"field_audit":audit,"sample":{"rows":100,"pool_rows":len(avail),"r34_excluded_rows":len(old),"r34_overlap_rows":len(sel&old),"seed":cfg["sample_contract"]["seed"],"quota":quota,"identity_sha256":sha,"selection_uses_score_labels":False,"selection_frozen_before_score_label_parsing":True,"no_resampling_after_result":True,"actual_distribution":dict(Counter(r["actual"] for r in rows))},"architecture":{"direct_total_goals_track":True,"conditional_goal_difference_track":True,"unified_score_lattice":True,"tail":["7+_H","7+_D","7+_A"],"same_day_freeze":True,"poisson_used":False,"manual_expected_goals_used":False,"manual_draw_offset_used":False},"market_coordination":{"objective":"KL/I-projection partition scaling","candidate_specs":specs,"audit":coord},"metrics":mm,"joint_metrics":jm,"paired_bootstrap":bs,"gates":gates,"promising_candidates":good,"hard_limits":cfg["hard_limits"]}
 (out/"status.json").write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding="utf-8")
 with (out/"candidate_summary.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["candidate","hits","accuracy","log_loss","brier","predicted_draw","draw_precision","draw_recall","joint_top1_accuracy","total_top1_accuracy","bootstrap_accuracy_p05","gate_passed"])
  for n,m in mm.items():w.writerow([n,m.get("hits"),m.get("accuracy"),m.get("log_loss"),m.get("brier"),m.get("predicted_draw"),m.get("draw_precision"),m.get("draw_recall"),jm.get(n,{}).get("joint_top1_accuracy"),jm.get(n,{}).get("total_top1_accuracy"),bs.get(n,{}).get("accuracy",{}).get("p05"),gates.get(n,{}).get("passed")])
 with (out/"fixed100_predictions.csv").open("w",newline="",encoding="utf-8") as f:
  fields=["id","competition_id","season","date","home_team","away_team","hg","ag","actual","ou_provider","ou_over","market_pick"]+[x for s in specs for x in (s["name"]+"_pick",s["name"]+"_draw",s["name"]+"_top_score")];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for r in rows:
   z={k:r[k] for k in ("id","competition_id","season","date","home_team","away_team","hg","ag","actual")};z.update({"ou_provider":r["ou25"]["provider"],"ou_over":r["ou25"]["over"],"market_pick":max(OUTCOMES,key=lambda x:r["market"][x])})
   for s in specs:n=s["name"];z[n+"_pick"]=max(OUTCOMES,key=lambda x:r["p_"+n][x]);z[n+"_draw"]=r["p_"+n]["draw"];z[n+"_top_score"]=max(r["m_"+n],key=r["m_"+n].get)
   w.writerow(z)
 with (out/"field_audit.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["competition","rows","ou25","asian"])
  for c,v in audit["by_competition"].items():w.writerow([c,v.get("rows",0),v.get("ou25",0),v.get("asian",0)])
 manifest(out);return res

def selftest():
 p=normm({"0-0":.15,"1-0":.2,"0-1":.15,"1-1":.15,"2-1":.2,"1-2":.15});m,a=candidate(p,{"home":.45,"draw":.3,"away":.25},{"over":.4,"under":.6},{"name":"t","ou_weight":1,"x1_weight":1});assert abs(sum(m.values())-1)<1e-12 and a["max_res"]<1e-12 and max(abs(agg1(m)[x]-{"home":.45,"draw":.3,"away":.25}[x]) for x in OUTCOMES)<1e-12;print("PASS")
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--config",type=Path,default=ROOT/"config/v511_fixed100_score_matrix_r35.json");ap.add_argument("--out-dir",type=Path,default=ROOT/"manifests/v511_fixed100_score_matrix_r35");ap.add_argument("--self-test",action="store_true");a=ap.parse_args();
 if a.self_test:selftest();return
 r=run(load(a.config),a.out_dir);print(json.dumps({"status":r["status"],"sample":r.get("sample"),"field":r.get("field_audit",{}).get("availability"),"metrics":r.get("metrics"),"promising":r.get("promising_candidates")},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
