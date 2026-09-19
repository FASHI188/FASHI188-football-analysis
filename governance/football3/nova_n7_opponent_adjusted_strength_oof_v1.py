#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,math,random
from collections import defaultdict,deque
from datetime import datetime
from pathlib import Path
class E(RuntimeError):pass
def req(c,m):
    if not c:raise E(m)
def readl(p:Path):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def dt(v:str)->datetime:return datetime.fromisoformat(v.replace('Z','+00:00'))
def groups(rows):
    out=[];s=0
    while s<len(rows):
        e=s+1
        while e<len(rows) and rows[e]['kickoff']==rows[s]['kickoff']:e+=1
        out.append((s,e));s=e
    return out
def align(events,baseline,labels):
    eids=[r['fixture_id'] for r in events];lids=[r['fixture_id'] for r in labels];req(eids==lids and len(set(eids))==len(eids),'EVENT_LABEL_ALIGN')
    b={str(r['n2_fixture_id']):r for r in baseline};req(len(b)==len(baseline),'BASE_DUP');req(set(eids)==set(b),'BASE_SET')
    out=[b[x] for x in eids]
    for e,r in zip(events,out):
        req(dt(e['kickoff'])==dt(r['kickoff']),'KO');req(e['home_team_id']==r['home_team_id'] and e['away_team_id']==r['away_team_id'],'TEAM');req(e['league']==r['league'],'LEAGUE');req(r['target_label_read'] is False,'BASE_LABEL')
    return out
def score(o):return 1.0 if o=='home' else 0.5 if o=='draw' else 0.0
def expectation(rh,ra,home_adv,scale):return 1.0/(1.0+10.0**(-((rh+home_adv)-ra)/scale))
def raw_states(rows,p):
    init=float(p['model']['elo_initial']);k=float(p['model']['elo_k']);scale=float(p['model']['elo_scale']);ha=float(p['model']['elo_home_advantage']);sw=int(p['model']['shock_window'])
    overall=defaultdict(lambda:init);home_r=defaultdict(lambda:init);away_r=defaultdict(lambda:init);shock=defaultdict(list);count=defaultdict(int);pending=deque();out=[None]*len(rows)
    def apply(r):
        h,a=r['home_team_id'],r['away_team_id'];s=score(r['outcome']);eh=expectation(overall[h],overall[a],ha,scale);d=k*(s-eh);overall[h]+=d;overall[a]-=d;shock[h].append(s-eh);shock[a].append(-(s-eh));count[h]+=1;count[a]+=1
        ev=expectation(home_r[h],away_r[a],ha,scale);dv=k*(s-ev);home_r[h]+=dv;away_r[a]-=dv
    for s,e in groups(rows):
        ko=dt(rows[s]['kickoff'])
        while pending and dt(pending[0]['release_at'])<=ko:apply(pending.popleft())
        for i in range(s,e):
            r=rows[i];h,a=r['home_team_id'],r['away_team_id'];sh=shock[h][-sw:];sa=shock[a][-sw:]
            od=(overall[h]+ha)-overall[a];vd=(home_r[h]+ha)-away_r[a]
            out[i]={'overall_diff':od,'venue_diff':vd,'shock_diff':(sum(sh)/len(sh) if sh else 0.0)-(sum(sa)/len(sa) if sa else 0.0),'home_n':math.log1p(count[h]),'away_n':math.log1p(count[a])}
        for i in range(s,e):pending.append(rows[i])
    return out
def basis(r,route,scale):
    d=r['overall_diff']/scale;v=r['venue_diff']/scale;s=r['shock_diff'];hn=r['home_n'];an=r['away_n']
    if route=='R1_ELO_DIFF':return [d,hn,an]
    if route=='R2_ELO_PLUS_SHOCK5':return [d,s,hn,an]
    if route=='R3_ELO_PLUS_VENUE':return [d,v,hn,an]
    if route=='R4_ELO_NONLINEAR':return [d,s,v,abs(d),d*abs(d),hn,an]
    raise E('ROUTE')
def scaler(X,idx):
    m=[];s=[]
    for j in range(len(X[0])):
        v=[float(X[i][j]) for i in idx];z=sum(v)/len(v);q=(sum((x-z)**2 for x in v)/len(v))**.5;m.append(z);s.append(max(q,1e-9))
    return m,s
def zr(x,m,s):return [(float(v)-m[j])/s[j] for j,v in enumerate(x)]
def softmax(base,x,beta):
    eps=1e-15;oh=math.log(max(base[0],eps)/max(base[2],eps));od=math.log(max(base[1],eps)/max(base[2],eps));xx=[1.0]+x;lh=oh+sum(beta[0][j]*xx[j] for j in range(len(xx)));ld=od+sum(beta[1][j]*xx[j] for j in range(len(xx)));mm=max(lh,ld,0.0);eh,ed,ea=math.exp(lh-mm),math.exp(ld-mm),math.exp(-mm);z=eh+ed+ea;return [eh/z,ed/z,ea/z]
def lossgrad(beta,X,B,y,c):
    n=len(X);d=len(beta[0]);g=[[0.0]*d,[0.0]*d];loss=0.0;eps=1e-15
    for x,b,o in zip(X,B,y):
        p=softmax(b,x,beta);loss-=math.log(max(p[o],eps));xx=[1.0]+x;er=[p[0]-(1 if o==0 else 0),p[1]-(1 if o==1 else 0)]
        for a in range(2):
            for j,v in enumerate(xx):g[a][j]+=er[a]*v
    loss/=n;g=[[v/n for v in row] for row in g];reg=1.0/(c*n)
    for a in range(2):
        for j in range(1,d):loss+=.5*reg*beta[a][j]**2;g[a][j]+=reg*beta[a][j]
    return loss,g
def fit(X,B,y,c=.25):
    d=len(X[0])+1;beta=[[0.0]*d,[0.0]*d];loss,g=lossgrad(beta,X,B,y,c)
    for it in range(300):
        n2=sum(v*v for row in g for v in row)
        if n2<1e-12:return beta
        step=1.0;ok=False
        while step>1e-8:
            z=[[beta[a][j]-step*g[a][j] for j in range(d)] for a in range(2)];nl,ng=lossgrad(z,X,B,y,c)
            if nl<=loss-1e-4*step*n2:beta,loss,g=z,nl,ng;ok=True;break
            step*=.5
        if not ok or max(abs(v) for row in g for v in row)<1e-6:return beta
    return beta
def blocks(rows,warm=.2,nblocks=5):
    gs=groups(rows);target=math.ceil(len(rows)*warm);cum=0;gi=0
    while gi<len(gs) and cum<target:cum+=gs[gi][1]-gs[gi][0];gi+=1
    we=gs[gi-1][1];remain=gs[gi:];each=(len(rows)-we)/nblocks;out=[];bs=we;acc=0
    for k,g in enumerate(remain):
        acc+=g[1]-g[0];left=len(remain)-k-1
        if len(out)<nblocks-1 and acc>=each and left>=nblocks-len(out)-1:out.append((bs,g[1]));bs=g[1];acc=0
    out.append((bs,len(rows)));req(len(out)==nblocks,'BLOCKS');return we,out
def oi(v):return {'home':0,'draw':1,'away':2}[v]
def metrics(P,y):
    n=len(y);eps=1e-15;ll=sum(-math.log(max(p[o],eps)) for p,o in zip(P,y))/n;br=sum(sum((p[k]-(1 if o==k else 0))**2 for k in range(3)) for p,o in zip(P,y))/n;rps=sum(((p[0]-(1 if o==0 else 0))**2+(p[0]+p[1]-(1 if o in (0,1) else 0))**2)/2 for p,o in zip(P,y))/n;top=sum(max(range(3),key=lambda k:p[k])==o for p,o in zip(P,y))/n;bins=[[] for _ in range(10)]
    for p,o in zip(P,y):k=max(range(3),key=lambda z:p[z]);c=p[k];bins[min(9,int(c*10))].append((c,1.0 if k==o else 0.0))
    ece=sum(len(b)/n*abs(sum(x for x,_ in b)/len(b)-sum(z for _,z in b)/len(b)) for b in bins if b);return {'n':n,'logloss':ll,'brier':br,'rps':rps,'top1':top,'ece':ece}
def boot(vals,reps,seed):
    r=random.Random(seed);n=len(vals);x=sorted(sum(vals[r.randrange(n)] for _ in range(n))/n for _ in range(reps));return [x[int(.025*(reps-1))],x[int(.975*(reps-1))]]
def qual(p,b,c):
    q=p['development_protocol']['qualification'];return b['logloss']-c['logloss']>q['logloss_gain_gt'] and c['brier']-b['brier']<=q['candidate_minus_formal_brier_lte'] and c['rps']-b['rps']<=q['candidate_minus_formal_rps_lte'] and c['ece']-b['ece']<=q['candidate_minus_formal_ece_lte']
def pos(p,b,c):
    q=p['development_protocol']['positive_signal_floor'];return b['logloss']-c['logloss']>q['logloss_gain_gt'] and c['brier']-b['brier']<=q['candidate_minus_formal_brier_lte'] and c['rps']-b['rps']<=q['candidate_minus_formal_rps_lte'] and c['top1']-b['top1']>=q['candidate_minus_formal_top1_gte']
def run(prereg:Path,events:Path,baseline:Path,labels:Path,out:Path,research_head:str):
    p=json.loads(prereg.read_text());req(p['status']=='DESIGN_LOCKED_PRELABEL','PREREG');ev=readl(events);ba=readl(baseline);la=readl(labels);n=int(p['data']['development_n']);req(len(ev)==len(ba)==len(la)==n,'N');ba=align(ev,ba,la);B=[[float(x) for x in r['formal_v2_1x2']] for r in ba];y=[oi(r['outcome']) for r in la];raw=raw_states(ev,p);we,bs=blocks(ev,float(p['development_protocol']['warmup_fraction']),int(p['development_protocol']['oof_blocks']));routes=[];pred={};oref=None
    for spec in p['subroutes']:
        rid=spec['id'];X=[basis(r,rid,float(p['model']['elo_scale'])) for r in raw];idx=[];cp=[]
        for s,e in bs:
            tr=list(range(s));va=list(range(s,e));m,z=scaler(X,tr);xt=[zr(X[i],m,z) for i in tr];xv=[zr(X[i],m,z) for i in va];be=fit(xt,[B[i] for i in tr],[y[i] for i in tr],float(p['model']['ridge_c']));cp.extend(softmax(B[i],x,be) for i,x in zip(va,xv));idx.extend(va)
        if oref is None:oref=idx
        req(idx==oref,'OOF');fb=[B[i] for i in idx];yy=[y[i] for i in idx];bm,cm=metrics(fb,yy),metrics(cp,yy);eff=[math.log(max(cp[j][yy[j]],1e-15))-math.log(max(fb[j][yy[j]],1e-15)) for j in range(len(yy))];routes.append({'route':rid,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss':bm['logloss']-cm['logloss'],'candidate_minus_formal_brier':cm['brier']-bm['brier'],'candidate_minus_formal_rps':cm['rps']-bm['rps'],'candidate_minus_formal_top1':cm['top1']-bm['top1'],'candidate_minus_formal_ece':cm['ece']-bm['ece'],'qualified':qual(p,bm,cm),'positive_floor':pos(p,bm,cm),'paired_bootstrap_95ci':boot(eff,int(p['metrics']['uncertainty']['repetitions']),int(p['metrics']['uncertainty']['seed']))});pred[rid]=cp
    best=max(routes,key=lambda x:x['formal_minus_candidate_logloss']);classification='POSITIVE_SIGNAL' if any(x['qualified'] or x['positive_floor'] for x in routes) else 'FAIL_RESEARCH_DIRECTION';idx=oref or [];yy=[y[i] for i in idx];fb=[B[i] for i in idx];cp=pred[best['route']];lr={}
    for league in p['metrics']['group_report']:
        if league in {'J1','K1'}:lr[league]={'status':'NOT_AVAILABLE','n':0,'coverage':0.0,'weight':0,'matrix_delta':0};continue
        q=[j for j,i in enumerate(idx) if ev[i]['league']==league];bm=metrics([fb[j] for j in q],[yy[j] for j in q]);cm=metrics([cp[j] for j in q],[yy[j] for j in q]);lr[league]={'status':'DEVELOPMENT_OOF','n':len(q),'coverage':1.0 if q else 0.0,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss':bm['logloss']-cm['logloss'],'matrix_delta':0}
    rec={'schema_version':'football3-nova-n7-opponent-adjusted-strength-terminal-v1','status':'N7_TERMINAL','research_head':research_head,'classification':classification,'development_n':n,'oof_evaluation_n':len(idx),'warmup_end':we,'completed_matches_only':True,'feature_time_rule':'prior completed result events only when release_at<=target kickoff; same-kickoff atomic','routes':routes,'best_signal_route':best['route'],'best_formal_minus_candidate_logloss':best['formal_minus_candidate_logloss'],'selected_route':best['route'] if classification=='POSITIVE_SIGNAL' else None,'league_report':lr,'isolated_2021_labels_read':0,'isolated_2023_labels_read':0,'isolated_2025_labels_read':0,'research_candidate_allowed':False,'candidate_activation_allowed':False,'promotion_allowed':False,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'candidate_weight':0,'matrix_delta':0,'score_matrix':{'formal_v2_unchanged':True,'candidate_matrix_delta':0,'exact_score_metrics_changed':False}}
    out.mkdir(parents=True,exist_ok=True);(out/'final_receipt.json').write_text(json.dumps(rec,indent=2,sort_keys=True)+'\n');return rec
def main():
    a=argparse.ArgumentParser();a.add_argument('--prereg',type=Path,required=True);a.add_argument('--events',type=Path,required=True);a.add_argument('--baseline',type=Path,required=True);a.add_argument('--labels',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--research-head',required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.events,x.baseline,x.labels,x.out,x.research_head),sort_keys=True))
if __name__=='__main__':main()
