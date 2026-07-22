#!/usr/bin/env python3
"""V6.4.5 leakage-safe pre-lineup stability pilot.

Uses StatsBomb Open Data for Bundesliga 2023/24 lineups, but NEVER uses the current
match's actual XI as a predictor. Features are computed only from earlier starting XIs:
- previous-XI continuity (last vs second-last),
- recent rotation volatility,
- size of the recurrent core XI,
- manager change flag and manager tenure.
The current match's manager identity is treated as pre-match public information.

Closing 1X2 from Football-Data is the baseline. Chronological 60/20/20 train/selection/
holdout within the season makes this an information-value pilot, not promotion evidence.
"""
from __future__ import annotations
import csv, io, json, math, urllib.request
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_prelineup_stability_v645_status.json'
SB_MATCHES='https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/9/281.json'
SB_LINEUP='https://raw.githubusercontent.com/statsbomb/open-data/master/data/lineups/{match_id}.json'
FD='https://www.football-data.co.uk/mmz4281/2324/D1.csv'
L2_GRID=(0.1,1.0,10.0,100.0,1000.0)
EPS=1e-12
CLASSES=('home','draw','away')

def get_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'football-v6.4-research/1.0'})
    with urllib.request.urlopen(req,timeout=45) as r:return json.loads(r.read().decode('utf-8'))
def get_csv(url):
    req=urllib.request.Request(url,headers={'User-Agent':'football-v6.4-research/1.0'})
    with urllib.request.urlopen(req,timeout=45) as r:txt=r.read().decode('utf-8-sig',errors='replace')
    return list(csv.DictReader(io.StringIO(txt)))
def norm(s):return ''.join(ch.lower() for ch in str(s) if ch.isalnum())
def f(row,k):
    try:x=float(str(row.get(k) or '').strip())
    except:return None
    return x if math.isfinite(x) and x>1 else None
def market(row):
    for ks in (('AvgCH','AvgCD','AvgCA'),('B365CH','B365CD','B365CA'),('AvgH','AvgD','AvgA')):
        o=[f(row,k) for k in ks]
        if all(x is not None for x in o):
            inv=[1/x for x in o];s=sum(inv);return {'home':inv[0]/s,'draw':inv[1]/s,'away':inv[2]/s}
    return None
def parse_date(x):return datetime.strptime(x,'%d/%m/%Y').date().isoformat() if len(x.split('/')[-1])==4 else datetime.strptime(x,'%d/%m/%y').date().isoformat()
def starter_ids(lineup_payload,team_id):
    team=next((x for x in lineup_payload if int(x.get('team_id',-1))==int(team_id)),None)
    if not team:return set()
    out=set()
    for p in team.get('lineup',[]):
        positions=p.get('positions') or []
        starter=False
        for pos in positions:
            reason=str(pos.get('start_reason') or '').lower()
            frm=str(pos.get('from') or '')
            if 'starting' in reason or frm in {'00:00','0:00','00:00:00.000'}:
                starter=True;break
        if starter: out.add(int(p['player_id']))
    return out
def jac(a,b):
    if not a or not b:return 0.0
    return len(a&b)/len(a|b)
def core_size(hist):
    if not hist:return 0.0
    c=Counter(p for xi in hist for p in xi);need=max(1,math.ceil(len(hist)*.6));return sum(v>=need for v in c.values())/11.0
def rotation(hist):
    if len(hist)<2:return 0.5
    vals=[1-jac(hist[i-1],hist[i]) for i in range(1,len(hist))];return sum(vals)/len(vals)
def logit(p):p=min(1-1e-8,max(1e-8,p));return math.log(p/(1-p))
def sig(x):
    if x>=0:z=math.exp(-min(700,x));return 1/(1+z)
    z=math.exp(max(-700,x));return z/(1+z)
def solve(A,b):
    n=len(b);M=[list(A[i])+[float(b[i])] for i in range(n)]
    for c in range(n):
        p=max(range(c,n),key=lambda r:abs(M[r][c]));M[c],M[p]=M[p],M[c];d=M[c][c]
        if abs(d)<1e-12:raise RuntimeError('singular')
        for j in range(c,n+1):M[c][j]/=d
        for r in range(n):
            if r==c:continue
            z=M[r][c]
            for j in range(c,n+1):M[r][j]-=z*M[c][j]
    return [M[i][n] for i in range(n)]
def fit(xs,ys,l2):
    d=len(xs[0]);means=[0]*d;scales=[1]*d
    for j in range(1,d):
        means[j]=sum(x[j] for x in xs)/len(xs);v=sum((x[j]-means[j])**2 for x in xs)/len(xs);scales[j]=max(1e-6,math.sqrt(v))
    X=[[1.0]+[(x[j]-means[j])/scales[j] for j in range(1,d)] for x in xs];theta=[0]*d;theta[0]=logit(sum(ys)/len(ys))
    for it in range(60):
        g=[0]*d;H=[[0]*d for _ in range(d)]
        for x,y in zip(X,ys):
            p=sig(sum(theta[j]*x[j] for j in range(d)));w=p*(1-p)
            for j in range(d):
                g[j]+=(p-y)*x[j]
                for k in range(d):H[j][k]+=w*x[j]*x[k]
        for j in range(1,d):g[j]+=l2*theta[j];H[j][j]+=l2
        H[0][0]+=1e-8
        if max(abs(z) for z in g)<1e-7:break
        st=solve(H,g);theta=[theta[j]-st[j] for j in range(d)]
        if max(abs(z) for z in st)<1e-8:break
    return {'theta':theta,'means':means,'scales':scales,'l2':l2}
def pred(m,x):
    xx=[1.0]+[(x[j]-m['means'][j])/m['scales'][j] for j in range(1,len(x))];return sig(sum(a*b for a,b in zip(m['theta'],xx)))
def row_features(mkt,home_hist,away_hist,home_mgr_changed,away_mgr_changed,home_tenure,away_tenure):
    remh=mkt['home']/(mkt['home']+mkt['away'])
    hc=jac(home_hist[-1],home_hist[-2]) if len(home_hist)>=2 else .5;ac=jac(away_hist[-1],away_hist[-2]) if len(away_hist)>=2 else .5
    return [1.0,logit(remh),mkt['draw'],hc-ac,rotation(home_hist)-rotation(away_hist),core_size(home_hist)-core_size(away_hist),float(home_mgr_changed)-float(away_mgr_changed),min(home_tenure,10)/10-min(away_tenure,10)/10]
def score(rows,model=None):
    n=h=0;b=ll=0.;predc=Counter()
    for r in rows:
        q=r['market'];p0=q['home']/(q['home']+q['away']);ph=p0 if model is None else pred(model,r['x']);pd=q['draw'];qq={'home':(1-pd)*ph,'draw':pd,'away':(1-pd)*(1-ph)};p=max(CLASSES,key=lambda k:qq[k]);t=r['truth'];hit=int(p==t);n+=1;h+=hit;predc[p]+=1;b+=sum((qq[k]-(1 if t==k else 0))**2 for k in CLASSES);ll-=math.log(max(EPS,qq[t]))
    return {'count':n,'hits':h,'accuracy':h/n if n else None,'mean_brier':b/n if n else None,'mean_log_loss':ll/n if n else None,'predicted':dict(predc)}
def main():
    matches=sorted(get_json(SB_MATCHES),key=lambda m:(m['match_date'],m['match_id']));fd=get_csv(FD);byfd=defaultdict(list)
    for r in fd:
        try:byfd[parse_date(r['Date'])].append(r)
        except:pass
    xi_hist=defaultdict(lambda:deque(maxlen=5));last_mgr={};tenure=defaultdict(int);rows=[];downloaded=0;starter_fail=0
    for m in matches:
        date=m['match_date'];hn=m['home_team']['home_team_name'];an=m['away_team']['away_team_name'];candidates=byfd.get(date,[]);raw=next((r for r in candidates if norm(r.get('HomeTeam'))==norm(hn) and norm(r.get('AwayTeam'))==norm(an)),None)
        if raw is None:
            # tolerant name match by first alnum prefix
            raw=next((r for r in candidates if norm(hn)[:6] in norm(r.get('HomeTeam')) or norm(r.get('HomeTeam'))[:6] in norm(hn)),None)
        mk=market(raw) if raw else None
        hid=int(m['home_team']['home_team_id']);aid=int(m['away_team']['away_team_id']);hmgr=((m['home_team'].get('managers') or [{}])[0].get('id'));amgr=((m['away_team'].get('managers') or [{}])[0].get('id'))
        hchg=hid in last_mgr and hmgr is not None and last_mgr[hid]!=hmgr;achg=aid in last_mgr and amgr is not None and last_mgr[aid]!=amgr
        if mk and len(xi_hist[hid])>=2 and len(xi_hist[aid])>=2:
            x=row_features(mk,list(xi_hist[hid]),list(xi_hist[aid]),hchg,achg,tenure[hid],tenure[aid]);truth='home' if m['home_score']>m['away_score'] else 'away' if m['home_score']<m['away_score'] else 'draw';rows.append({'date':date,'match_id':m['match_id'],'x':x,'market':mk,'truth':truth})
        try:
            lp=get_json(SB_LINEUP.format(match_id=m['match_id']));downloaded+=1;hxi=starter_ids(lp,hid);axi=starter_ids(lp,aid)
            if len(hxi)>=9:xi_hist[hid].append(hxi)
            else:starter_fail+=1
            if len(axi)>=9:xi_hist[aid].append(axi)
            else:starter_fail+=1
        except Exception:starter_fail+=2
        if hmgr is not None:
            tenure[hid]=1 if hchg or hid not in last_mgr else tenure[hid]+1;last_mgr[hid]=hmgr
        if amgr is not None:
            tenure[aid]=1 if achg or aid not in last_mgr else tenure[aid]+1;last_mgr[aid]=amgr
    rows=sorted(rows,key=lambda r:(r['date'],r['match_id']));n=len(rows);a=int(.6*n);b=int(.8*n);tr,va,ho=rows[:a],rows[a:b],rows[b:];bv=score(va);bh=score(ho);cand=[]
    for l2 in L2_GRID:
        model=fit([r['x'] for r in tr],[1 if r['truth']=='home' else 0 for r in tr if r['truth']!='draw'],l2) if False else None
        decisive=[r for r in tr if r['truth']!='draw'];model=fit([r['x'] for r in decisive],[1 if r['truth']=='home' else 0 for r in decisive],l2);mv=score(va,model);proper=mv['mean_brier']<=bv['mean_brier']+1e-12 and mv['mean_log_loss']<=bv['mean_log_loss']+1e-12;cand.append({'l2':l2,'proper_nonworse':proper,'validation':mv})
    elig=[c for c in cand if c['proper_nonworse']] or cand;elig.sort(key=lambda c:(-c['validation']['accuracy'],c['validation']['mean_log_loss']));sel=elig[0];dec=[r for r in tr+va if r['truth']!='draw'];refit=fit([r['x'] for r in dec],[1 if r['truth']=='home' else 0 for r in dec],sel['l2']);mh=score(ho,refit);guard={'brier_nonworse':mh['mean_brier']<=bh['mean_brier']+1e-12,'log_loss_nonworse':mh['mean_log_loss']<=bh['mean_log_loss']+1e-12}
    out={'schema_version':'V6.4.5-prelineup-stability-pilot-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','scope':{'competition':'Bundesliga','season':'2023/24','strict_current_xi_leakage':False,'features_use_only_prior_lineups':True},'data_audit':{'statsbomb_matches':len(matches),'lineups_downloaded':downloaded,'starter_parse_failures':starter_fail,'usable_rows':n,'train':len(tr),'validation':len(va),'holdout':len(ho)},'baseline_validation':bv,'selected_candidate':sel,'baseline_holdout':bh,'challenger_holdout':mh,'accuracy_gain_pp':100*(mh['accuracy']-bh['accuracy']) if bh['accuracy'] is not None else None,'proper_score_guard':guard,'pilot_gate_passed':mh['accuracy']>bh['accuracy'] and all(guard.values()),'governance':{'pilot_only':True,'single_season_not_promotion_evidence':True,'current_match_actual_lineup_not_used_as_feature':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
