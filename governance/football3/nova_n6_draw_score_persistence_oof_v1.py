from __future__ import annotations
import argparse, json, math, random
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

class N6Error(RuntimeError): pass
def req(c: bool, m: str) -> None:
    if not c: raise N6Error(m)
def readl(p: Path): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def dt(v: str) -> datetime: return datetime.fromisoformat(v.replace('Z','+00:00'))

def groups(rows):
    out=[];s=0
    while s<len(rows):
        e=s+1
        while e<len(rows) and rows[e]['kickoff']==rows[s]['kickoff']:e+=1
        out.append((s,e));s=e
    return out

def align(events, baseline, labels):
    eids=[r['fixture_id'] for r in events]; bids=[r['n6_fixture_id'] for r in baseline]; lids=[r['fixture_id'] for r in labels]
    req(len(eids)==len(set(eids)),'EVENT_DUP'); req(len(bids)==len(set(bids)),'BASE_DUP'); req(len(lids)==len(set(lids)),'LABEL_DUP')
    req(set(eids)==set(bids)==set(lids),'ID_SET_MISMATCH')
    bmap={r['n6_fixture_id']:r for r in baseline}; lmap={r['fixture_id']:r for r in labels}
    bo=[];lo=[]
    for e in events:
        b=bmap[e['fixture_id']]; l=lmap[e['fixture_id']]
        req(str(e['league'])==str(b['league']),'LEAGUE_MISMATCH'); req(dt(e['kickoff'])==dt(b['kickoff']),'KICKOFF_MISMATCH')
        req(str(e['home_team_id'])==str(b['home_team_id']) and str(e['away_team_id'])==str(b['away_team_id']),'TEAM_MISMATCH')
        req(b.get('target_label_read') is False,'BASE_LABEL_READ')
        bo.append(b);lo.append(l)
    return bo,lo

def build_raw(events):
    hist=defaultdict(list); pending=deque(); out=[None]*len(events)
    for s,e in groups(events):
        ko=dt(events[s]['kickoff'])
        while pending and dt(pending[0]['release_at'])<=ko:
            r=pending.popleft(); ev=(float(r['is_draw']),float(r['low_total_le2']),float(r['tight_margin_le1']))
            hist[r['home_team_id']].append(ev); hist[r['away_team_id']].append(ev)
        for i in range(s,e):
            r=events[i]; pair=[]
            for team in (r['home_team_id'],r['away_team_id']):
                hs=hist[team]
                def avg(j,w):
                    xs=hs[-w:]; return None if not xs else sum(x[j] for x in xs)/len(xs)
                pair.append({'d5':avg(0,5),'d10':avg(0,10),'l5':avg(1,5),'l10':avg(1,10),'t5':avg(2,5),'t10':avg(2,10),'n':math.log1p(len(hs))})
            out[i]=tuple(pair)
        for i in range(s,e): pending.append(events[i])
    return out

def primitive(pair,route):
    H,A=pair
    if route=='R1_DRAW_W10': return [H['d10'],A['d10'],None if H['d10'] is None or A['d10'] is None else H['d10']-A['d10'],H['n'],A['n']]
    if route=='R2_LOW_TOTAL_W10': return [H['l10'],A['l10'],None if H['l10'] is None or A['l10'] is None else H['l10']-A['l10'],H['n'],A['n']]
    if route=='R3_TIGHT_MARGIN_W10': return [H['t10'],A['t10'],None if H['t10'] is None or A['t10'] is None else H['t10']-A['t10'],H['n'],A['n']]
    if route=='R4_W5_W10_COMBINED':
        v=[]
        for k in ('d5','d10','l5','l10','t5','t10'):v += [H[k],A[k],None if H[k] is None or A[k] is None else H[k]-A[k]]
        return v+[H['n'],A['n']]
    raise N6Error('UNKNOWN_ROUTE')

def scaler(X,idx):
    m=[];s=[]
    for j in range(len(X[0])):
        vals=[float(X[i][j]) for i in idx if X[i][j] is not None and math.isfinite(float(X[i][j]))]
        mm=sum(vals)/len(vals) if vals else 0.0; var=sum((v-mm)**2 for v in vals)/len(vals) if vals else 0.0;m.append(mm);s.append(max(math.sqrt(var),1e-9))
    return m,s
def zrow(row,m,s): return [((m[j] if v is None else float(v))-m[j])/s[j] for j,v in enumerate(row)]
def softmax(base,x,beta):
    eps=1e-15;oh=math.log(max(base[0],eps)/max(base[2],eps));od=math.log(max(base[1],eps)/max(base[2],eps));xx=[1.0]+x
    lh=oh+sum(beta[0][j]*xx[j] for j in range(len(xx)));ld=od+sum(beta[1][j]*xx[j] for j in range(len(xx)));mm=max(lh,ld,0.0);ee=[math.exp(lh-mm),math.exp(ld-mm),math.exp(-mm)];z=sum(ee);return [v/z for v in ee]
def loss_grad(beta,X,bases,y,c):
    n=len(X);d=len(beta[0]);loss=0.0;g=[[0.0]*d,[0.0]*d];eps=1e-15
    for x,b,o in zip(X,bases,y):
        p=softmax(b,x,beta);loss-=math.log(max(p[o],eps));xx=[1.0]+x
        for k in range(2):
            er=p[k]-(1 if o==k else 0)
            for j,v in enumerate(xx):g[k][j]+=er*v
    loss/=n;g=[[v/n for v in row] for row in g];reg=1.0/(c*n)
    for k in range(2):
        for j in range(1,d):loss+=.5*reg*beta[k][j]**2;g[k][j]+=reg*beta[k][j]
    return loss,g
def fit(X,bases,y,c=.25,max_iter=300):
    d=len(X[0])+1;beta=[[0.0]*d,[0.0]*d];loss,g=loss_grad(beta,X,bases,y,c)
    for it in range(max_iter):
        n2=sum(v*v for row in g for v in row)
        if n2<1e-12:return beta
        step=1.0;ok=False
        while step>1e-8:
            cand=[[beta[k][j]-step*g[k][j] for j in range(d)] for k in range(2)];nl,ng=loss_grad(cand,X,bases,y,c)
            if nl<=loss-1e-4*step*n2:beta,loss,g=cand,nl,ng;ok=True;break
            step*=.5
        if not ok or max(abs(v) for row in g for v in row)<1e-6:return beta
    return beta

def blocks(rows,warm=.2,nblocks=5):
    gs=groups(rows);target=math.ceil(len(rows)*warm);cum=0;gi=0
    while gi<len(gs) and cum<target:cum+=gs[gi][1]-gs[gi][0];gi+=1
    req(gi>0,'EMPTY_WARMUP');warm_end=gs[gi-1][1];remain=gs[gi:];each=(len(rows)-warm_end)/nblocks;out=[];bs=warm_end;acc=0
    for k,g in enumerate(remain):
        acc+=g[1]-g[0];left=len(remain)-k-1
        if len(out)<nblocks-1 and acc>=each and left>=nblocks-len(out)-1:out.append((bs,g[1]));bs=g[1];acc=0
    out.append((bs,len(rows)));req(len(out)==nblocks,'BLOCK_COUNT');return warm_end,out

def oi(v): return {'home':0,'draw':1,'away':2}[v]
def metrics(ps,y):
    n=len(y);eps=1e-15;ll=sum(-math.log(max(p[o],eps)) for p,o in zip(ps,y))/n
    br=sum(sum((p[k]-(1 if o==k else 0))**2 for k in range(3)) for p,o in zip(ps,y))/n
    rps=sum(((p[0]-(1 if o==0 else 0))**2+(p[0]+p[1]-(1 if o in (0,1) else 0))**2)/2 for p,o in zip(ps,y))/n
    top=sum(max(range(3),key=lambda k:p[k])==o for p,o in zip(ps,y))/n;bins=[[] for _ in range(10)]
    for p,o in zip(ps,y):
        k=max(range(3),key=lambda q:p[q]);c=p[k];bins[min(9,int(c*10))].append((c,1.0 if k==o else 0.0))
    ece=sum(len(b)/n*abs(sum(x[0] for x in b)/len(b)-sum(x[1] for x in b)/len(b)) for b in bins if b)
    return {'n':n,'logloss':ll,'brier':br,'rps':rps,'top1':top,'ece':ece}
def effects(b,c,y):
    eps=1e-15;return [math.log(max(c[i][y[i]],eps))-math.log(max(b[i][y[i]],eps)) for i in range(len(y))]
def boot(vals,reps,seed):
    r=random.Random(seed);n=len(vals);x=sorted(sum(vals[r.randrange(n)] for _ in range(n))/n for _ in range(reps));return [x[int(.025*(reps-1))],x[int(.975*(reps-1))]]
def qualify(p,b,c):
    q=p['development_protocol']['qualification'];return b['logloss']-c['logloss']>q['logloss_gain_gt'] and c['brier']-b['brier']<=q['candidate_minus_formal_brier_lte'] and c['rps']-b['rps']<=q['candidate_minus_formal_rps_lte'] and c['ece']-b['ece']<=q['candidate_minus_formal_ece_lte']
def positive(p,b,c):
    q=p['development_protocol']['positive_signal_floor'];return b['logloss']-c['logloss']>q['logloss_gain_gt'] and c['brier']-b['brier']<=q['candidate_minus_formal_brier_lte'] and c['rps']-b['rps']<=q['candidate_minus_formal_rps_lte'] and c['top1']-b['top1']>=q['candidate_minus_formal_top1_gte']

def run(prereg:Path,events_path:Path,baseline_path:Path,labels_path:Path,out:Path,research_head:str):
    p=json.loads(prereg.read_text());req(p['status']=='DESIGN_LOCKED_PRELABEL','PREREG');events=readl(events_path);base=readl(baseline_path);labels=readl(labels_path);n=int(p['data']['development_n']);req(len(events)==len(base)==len(labels)==n,'ROW_N')
    base,labels=align(events,base,labels);probs=[[float(x) for x in r['formal_v2_1x2']] for r in base];y=[oi(r['outcome']) for r in labels];raw=build_raw(events);warm,bs=blocks(events,float(p['development_protocol']['warmup_fraction']),int(p['development_protocol']['oof_blocks']))
    routes=[];pred_by={};idx_ref=None
    for spec in p['subroutes']:
        rid=spec['id'];X=[primitive(v,rid) for v in raw];idx=[];cand=[]
        for fs,fe in bs:
            tr=list(range(fs));va=list(range(fs,fe));m,s=scaler(X,tr);Xtr=[zrow(X[i],m,s) for i in tr];Xva=[zrow(X[i],m,s) for i in va];beta=fit(Xtr,[probs[i] for i in tr],[y[i] for i in tr],float(p['model']['ridge_c']));cand += [softmax(probs[i],x,beta) for i,x in zip(va,Xva)];idx += va
        if idx_ref is None:idx_ref=idx
        req(idx==idx_ref,'OOF_DRIFT');bb=[probs[i] for i in idx];yy=[y[i] for i in idx];bm,cm=metrics(bb,yy),metrics(cand,yy);ef=effects(bb,cand,yy)
        routes.append({'route':rid,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss':bm['logloss']-cm['logloss'],'candidate_minus_formal_brier':cm['brier']-bm['brier'],'candidate_minus_formal_rps':cm['rps']-bm['rps'],'candidate_minus_formal_top1':cm['top1']-bm['top1'],'candidate_minus_formal_ece':cm['ece']-bm['ece'],'qualified':qualify(p,bm,cm),'positive_floor':positive(p,bm,cm),'paired_bootstrap_95ci':boot(ef,int(p['metrics']['uncertainty']['repetitions']),int(p['metrics']['uncertainty']['seed']))});pred_by[rid]=cand
    best=max(routes,key=lambda r:r['formal_minus_candidate_logloss']);classification='POSITIVE_SIGNAL' if any(r['qualified'] or r['positive_floor'] for r in routes) else 'FAIL_RESEARCH_DIRECTION';selected=best['route'] if classification=='POSITIVE_SIGNAL' else None
    idx=idx_ref or [];yy=[y[i] for i in idx];bb=[probs[i] for i in idx];cc=pred_by[best['route']];lr={}
    for league in p['metrics']['group_report']:
        if league in {'J1','K1'}:lr[league]={'status':'NOT_AVAILABLE','n':0,'coverage':0.0,'weight':0,'matrix_delta':0};continue
        pos=[j for j,i in enumerate(idx) if events[i]['league']==league];lb=[bb[j] for j in pos];lc=[cc[j] for j in pos];ly=[yy[j] for j in pos];bm,cm=metrics(lb,ly),metrics(lc,ly);lr[league]={'status':'DEVELOPMENT_OOF','n':len(pos),'coverage':1.0 if pos else 0.0,'formal':bm,'candidate':cm,'formal_minus_candidate_logloss':bm['logloss']-cm['logloss'],'matrix_delta':0}
    out.mkdir(parents=True,exist_ok=True);res={'schema_version':'football3-nova-n6-draw-score-development-oof-v1','status':'N6_TERMINAL','classification':classification,'research_head':research_head,'exact_base':p['exact_base'],'development_n':n,'oof_evaluation_n':len(idx),'warmup_end':warm,'completed_matches_only':True,'feature_time_rule':'same-season prior event only; release_at<=target kickoff; same-kickoff atomic','routes':routes,'best_signal_route':best['route'],'best_formal_minus_candidate_logloss':best['formal_minus_candidate_logloss'],'selected_route':selected,'league_report':lr,'isolated_2021_labels_read':0,'isolated_2023_labels_read':0,'isolated_2025_labels_read':0,'candidate_activation_allowed':False,'research_candidate_allowed':False,'promotion_allowed':False,'formal_v2_changed':False,'current_changed':False,'production_changed':False,'candidate_weight':0,'matrix_delta':0,'score_matrix':{'formal_v2_unchanged':True,'candidate_matrix_delta':0,'exact_score_metrics_changed':False}}
    (out/'final_receipt.json').write_text(json.dumps(res,indent=2,sort_keys=True)+'\n');return res
def main():
    a=argparse.ArgumentParser();a.add_argument('--prereg',type=Path,required=True);a.add_argument('--events',type=Path,required=True);a.add_argument('--baseline',type=Path,required=True);a.add_argument('--labels',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--research-head',required=True);x=a.parse_args();print(json.dumps(run(x.prereg,x.events,x.baseline,x.labels,x.out,x.research_head),sort_keys=True))
if __name__=='__main__':main()
