from __future__ import annotations
import argparse, copy, json, math, pathlib, sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import common

EPS=1e-15

def clip(x,a,b): return min(b,max(a,float(x)))
def logit(p): p=clip(p,1e-6,1-1e-6); return math.log(p/(1-p))
def logistic(x): return 1/(1+math.exp(-clip(x,-40,40)))
def pois_nll(y,lam): lam=max(1e-9,float(lam)); return lam-float(y)*math.log(lam)+math.lgamma(int(y)+1)

@dataclass
class TeamState:
    attack_mean: float=0.0
    attack_var: float=0.30
    defence_mean: float=0.0
    defence_var: float=0.30
    last_ts: datetime|None=None

PROFILE={'process_noise_per_day':0.0012,'half_life_days':160.0,'prior_variance':0.30,'total_weight':0.35,'share_weight':0.55,'league_prior_matches':40.0}

def dt(text):
    s=str(text).replace('Z','+00:00')
    return datetime.fromisoformat(s)

def evolve(t:TeamState,now:datetime):
    if t.last_ts is None: t.last_ts=now; return
    days=max(0.0,(now-t.last_ts).total_seconds()/86400.0); decay=math.exp(-math.log(2)*days/PROFILE['half_life_days']); pv=PROFILE['prior_variance']; process=PROFILE['process_noise_per_day']*min(days,90.0)
    t.attack_mean*=decay; t.defence_mean*=decay
    t.attack_var=clip(pv+(t.attack_var-pv)*decay*decay+process,0.02,2.5); t.defence_var=clip(pv+(t.defence_var-pv)*decay*decay+process,0.02,2.5); t.last_ts=now

def pred_rates(states,home,away,now,lh,la):
    hs=states.setdefault(home,TeamState()); aws=states.setdefault(away,TeamState()); evolve(hs,now); evolve(aws,now)
    ph=math.exp(clip(math.log(max(.05,lh))+hs.attack_mean+aws.defence_mean,-3,2)); pa=math.exp(clip(math.log(max(.05,la))+aws.attack_mean+hs.defence_mean,-3,2))
    return clip(ph,.08,5),clip(pa,.08,5)

def pair_update(m1,v1,m2,v2,base,y):
    lin=clip(math.log(max(.05,base))+m1+m2,-4,3); lam=math.exp(lin); info=max(1e-8,lam); denom=1+info*(v1+v2); res=float(y)-lam
    nm1=clip(m1+v1*res/denom,-2.5,2.5); nm2=clip(m2+v2*res/denom,-2.5,2.5)
    nv1=max(.015,v1-info*v1*v1/denom); nv2=max(.015,v2-info*v2*v2/denom)
    return nm1,nv1,nm2,nv2,lam

def moment(mu1,var1,mu2,var2,w2):
    w=clip(w2,0,1); m=(1-w)*mu1+w*mu2; v=(1-w)*(var1+(mu1-m)**2)+w*(var2+(mu2-m)**2); return clip(m,-2.5,2.5),clip(v,.015,2.5)

def likelihood_pair(hg,ag,lh,la,hs,aws,reset=None):
    ha=0.0 if reset=='home' else hs.attack_mean; hd=0.0 if reset=='home' else hs.defence_mean; aa=0.0 if reset=='away' else aws.attack_mean; ad=0.0 if reset=='away' else aws.defence_mean
    ph=math.exp(clip(math.log(max(.05,lh))+ha+ad,-4,3)); pa=math.exp(clip(math.log(max(.05,la))+aa+hd,-4,3))
    return math.exp(-pois_nll(hg,ph)-pois_nll(ag,pa))

def update_cont(states,home,away,now,hg,ag,lh,la):
    hs=states.setdefault(home,TeamState()); aws=states.setdefault(away,TeamState()); evolve(hs,now); evolve(aws,now)
    hs.attack_mean,hs.attack_var,aws.defence_mean,aws.defence_var,_=pair_update(hs.attack_mean,hs.attack_var,aws.defence_mean,aws.defence_var,lh,hg)
    aws.attack_mean,aws.attack_var,hs.defence_mean,hs.defence_var,_=pair_update(aws.attack_mean,aws.attack_var,hs.defence_mean,hs.defence_var,la,ag)

def update_adaptive(states,home,away,now,hg,ag,lh,la,hazard):
    hs=states.setdefault(home,TeamState()); aws=states.setdefault(away,TeamState()); evolve(hs,now); evolve(aws,now)
    oldh=copy.deepcopy(hs); olda=copy.deepcopy(aws)
    lc=likelihood_pair(hg,ag,lh,la,oldh,olda,None); lrh=likelihood_pair(hg,ag,lh,la,oldh,olda,'home'); lra=likelihood_pair(hg,ag,lh,la,oldh,olda,'away')
    wh=(hazard*lrh)/max(EPS,(1-hazard)*lc+hazard*lrh); wa=(hazard*lra)/max(EPS,(1-hazard)*lc+hazard*lra)
    ch=copy.deepcopy(oldh); ca=copy.deepcopy(olda)
    ch.attack_mean,ch.attack_var,ca.defence_mean,ca.defence_var,_=pair_update(ch.attack_mean,ch.attack_var,ca.defence_mean,ca.defence_var,lh,hg)
    ca.attack_mean,ca.attack_var,ch.defence_mean,ch.defence_var,_=pair_update(ca.attack_mean,ca.attack_var,ch.defence_mean,ch.defence_var,la,ag)
    rh=TeamState(last_ts=now)
    rh.attack_mean,rh.attack_var,_,_,_=pair_update(0.0,PROFILE['prior_variance'],olda.defence_mean,olda.defence_var,lh,hg)
    _,_,rh.defence_mean,rh.defence_var,_=pair_update(olda.attack_mean,olda.attack_var,0.0,PROFILE['prior_variance'],la,ag)
    ra=TeamState(last_ts=now)
    _,_,ra.defence_mean,ra.defence_var,_=pair_update(oldh.attack_mean,oldh.attack_var,0.0,PROFILE['prior_variance'],lh,hg)
    ra.attack_mean,ra.attack_var,_,_,_=pair_update(0.0,PROFILE['prior_variance'],oldh.defence_mean,oldh.defence_var,la,ag)
    hs.attack_mean,hs.attack_var=moment(ch.attack_mean,ch.attack_var,rh.attack_mean,rh.attack_var,wh); hs.defence_mean,hs.defence_var=moment(ch.defence_mean,ch.defence_var,rh.defence_mean,rh.defence_var,wh); hs.last_ts=now
    aws.attack_mean,aws.attack_var=moment(ca.attack_mean,ca.attack_var,ra.attack_mean,ra.attack_var,wa); aws.defence_mean,aws.defence_var=moment(ca.defence_mean,ca.defence_var,ra.defence_mean,ra.defence_var,wa); aws.last_ts=now
    return wh,wa

def recenter(states):
    if not states:return
    m=sum(s.attack_mean for s in states.values())/len(states)
    for s in states.values(): s.attack_mean-=m; s.defence_mean+=m

def db_games(db):
    con=sqlite3.connect(str(db)); con.row_factory=sqlite3.Row; qs=','.join('?' for _ in common.LEAGUES)
    rows=[dict(r) for r in con.execute(f"select fid,h_id,a_id,date,league,season,h_goals,a_goals from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,fid",common.LEAGUES)]; con.close(); return rows

def matrix_means(m):
    mh=ma=0.0
    for h,row in enumerate(m):
        for a,p in enumerate(row): mh+=h*float(p); ma+=a*float(p)
    return mh,ma,mh+ma

def renorm(m):
    z=sum(sum(r) for r in m); return [[x/z for x in r] for r in m]
def total_tilt(m,target):
    items=[(h,a,float(p)) for h,row in enumerate(m) for a,p in enumerate(row)]; support=[h+a for h,a,_ in items]; target=clip(target,min(support)+1e-8,max(support)-1e-8)
    def calc(th):
        logs=[math.log(max(EPS,p))+th*t for (_,_,p),t in zip(items,support)]; mx=max(logs); w=[math.exp(x-mx) for x in logs]; z=sum(w); pr=[x/z for x in w]; return sum(p*t for p,t in zip(pr,support)),pr
    lo,hi=-20,20
    for _ in range(80):
        mid=(lo+hi)/2; val,_=calc(mid); lo,hi=(mid,hi) if val<target else (lo,mid)
    _,pr=calc((lo+hi)/2); out=[[0.0]*len(m[0]) for _ in range(len(m))]
    for (h,a,_),p in zip(items,pr): out[h][a]=p
    return renorm(out)
def share_tilt(m,target_home):
    groups=defaultdict(list); mass=defaultdict(float)
    for h,row in enumerate(m):
        for a,p in enumerate(row): groups[h+a].append((h,a,float(p))); mass[h+a]+=float(p)
    total=sum(t*x for t,x in mass.items()); target=clip(target_home,1e-8,max(1e-8,total-1e-8))
    def calc(eta):
        out=[[0.0]*len(m[0]) for _ in range(len(m))]; ach=0.0
        for t,items in groups.items():
            logs=[math.log(max(EPS,p))+eta*h for h,a,p in items]; mx=max(logs); w=[math.exp(x-mx) for x in logs]; z=sum(w)
            for (h,a,_),ww in zip(items,w): q=mass[t]*ww/z; out[h][a]=q; ach+=h*q
        return ach,out
    lo,hi=-20,20
    for _ in range(80):
        mid=(lo+hi)/2; val,_=calc(mid); lo,hi=(mid,hi) if val<target else (lo,mid)
    _,out=calc((lo+hi)/2); return renorm(out)
def candidate_matrix(base,dyn_h,dyn_a):
    bh,ba,bt=matrix_means(base); dt=dyn_h+dyn_a; tw=PROFILE['total_weight']; sw=PROFILE['share_weight']
    target_total=math.exp((1-tw)*math.log(max(EPS,bt))+tw*math.log(max(EPS,dt))); bs=bh/max(EPS,bt); ds=dyn_h/max(EPS,dt); target_share=logistic((1-sw)*logit(bs)+sw*logit(ds))
    m=total_tilt(base,target_total); return share_tilt(m,target_total*target_share)

def simulate(db,wanted,hazard):
    games=db_games(db); pri={}
    for league in common.LEAGUES:
        old=[g for g in games if g['league']==league and int(g['season'])<=2017]
        pri[league]=(sum(int(g['h_goals']) for g in old)/len(old),sum(int(g['a_goals']) for g in old)/len(old),len(old))
    adap=defaultdict(dict); fixed=defaultdict(dict); league_state={l:{'h_sum':pri[l][0]*PROFILE['league_prior_matches'],'a_sum':pri[l][1]*PROFILE['league_prior_matches'],'n':PROFILE['league_prior_matches']} for l in common.LEAGUES}
    bytime=defaultdict(list)
    for g in games:
        if int(g['season'])>=2018: bytime[str(g['date'])].append(g)
    dyn={}; active={}; nlla=nllf=0.0; scored=0; reset_probs=[]
    for ts in sorted(bytime):
        batch=sorted(bytime[ts],key=lambda g:int(g['fid'])); now=dt(ts)
        predictions=[]
        for g in batch:
            league=str(g['league']); ls=league_state[league]; lh=ls['h_sum']/ls['n']; la=ls['a_sum']/ls['n']; h=str(int(g['h_id'])); a=str(int(g['a_id']))
            ah,aa=pred_rates(adap[league],h,a,now,lh,la); fh,fa=pred_rates(fixed[league],h,a,now,lh,la); fid=f"understat:{int(g['fid'])}"
            if fid in wanted:
                hg=int(g['h_goals']); ag=int(g['a_goals']); nlla+=pois_nll(hg,ah)+pois_nll(ag,aa); nllf+=pois_nll(hg,fh)+pois_nll(ag,fa); scored+=1
                dyn[fid]=(ah,aa); active[fid]=True
            predictions.append((g,league,h,a,lh,la))
        for g,league,h,a,lh,la in predictions:
            hg=int(g['h_goals']); ag=int(g['a_goals']); wh,wa=update_adaptive(adap[league],h,a,now,hg,ag,lh,la,hazard); update_cont(fixed[league],h,a,now,hg,ag,lh,la); reset_probs.extend([wh,wa]); ls=league_state[league]; ls['h_sum']+=hg; ls['a_sum']+=ag; ls['n']+=1
        for league in {x[1] for x in predictions}: recenter(adap[league]); recenter(fixed[league])
    return dyn,active,{'scored_n':scored,'adaptive_mean_nll':nlla/scored,'fixed_mean_nll':nllf/scored,'delta_mean_nll':(nlla-nllf)/scored,'mean_reset_posterior':sum(reset_probs)/len(reset_probs),'max_reset_posterior':max(reset_probs) if reset_probs else None}

def main():
    ap=argparse.ArgumentParser()
    for x in ('contract','v311','v31','usr1','v2','xg','v1','v1_result','db','xg_identity','out'): ap.add_argument('--'+x.replace('_','-'),type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True); c=json.loads(a.contract.read_text())
    if c['status']!='FROZEN_BEFORE_CONSUMED_2020_2022_SCORING': raise common.Stage84Error('contract drift')
    b=common.build_frozen_baseline(a,'s84a'); common.write_json(a.out/'row_receipt.json',b['row_receipt']); common.write_json(a.out/'baseline_receipt.json',{'prediction_n':len(b['bmap']),'segments':b['baseline_receipt']})
    wanted={r['fixture_id'] for r in b['rows'] if r['season'] in (2020,2021,2022)}; dyn,active,pr=simulate(a.db,wanted,float(c['mechanism']['hazard_per_completed_match'])); common.write_json(a.out/'state_predictive_gate.json',pr)
    pregate=pr['adaptive_mean_nll']<pr['fixed_mean_nll']
    if not pregate:
        final={'schema_version':'football3-stage6-pre-a-final-v1','status':c['terminal']['pre_gate_fail'],'research_only':True,'post_view_development':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'pre_1x2_gate_pass':False,'state_predictive':pr,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_weights_changed':False}; common.write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
    cand={}
    for r in b['rows']:
        fid=r['fixture_id']
        if r['season'] in (2020,2021,2022) and fid in b['bmap']:
            ah,aa=dyn[fid]; cm=candidate_matrix(b['bmats'][fid],ah,aa); cand[fid]=common.integrate_matrix(cm)
    ev,_=common.score_axis(b['rows'],b['bmap'],b['bmats'],cand,c,active); status=c['terminal']['development_pass'] if ev['all_pass'] else c['terminal']['development_fail']
    final={'schema_version':'football3-stage6-pre-a-final-v1','status':status,'research_only':True,'post_view_development':True,'fresh_confirmation':False,'promotion_allowed':False,'source_max_season_loaded':2022,'2023_opened':False,'pre_1x2_gate_pass':True,'state_predictive':pr,'evaluation':ev,'formal_v2_unchanged':True,'v3_1_1_unchanged':True,'CURRENT_changed':False,'production_pointer_changed':False,'formal_weights_changed':False}; common.write_json(a.out/'development_score.json',ev); common.write_json(a.out/'final_status.json',final); print(json.dumps(final,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
