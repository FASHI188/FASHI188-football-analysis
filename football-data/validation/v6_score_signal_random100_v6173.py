#!/usr/bin/env python3
"""V6.17.3 random-100 audit of direct pre-match score signals.

Research-only, no tuning on test outcomes. Deduplicate Kambi events, keep the earliest valid
pre-match snapshot, resolve already-finished 90m scores from ESPN, then sample exactly 100 with a
fixed RNG seed if >=100 eligible events exist.

Arms, all using the same Kambi snapshot:
A cs_prior: de-vigged mapped Correct Score prices as an explicit score prior (+epsilon support).
B match: A projected to full-time 1X2 + all ordinary half-goal match-total lines.
C teamtotals: B plus all ordinary half-goal home/away team-total lines.
D full: C plus BTTS yes/no.

This isolates whether team-specific scoring markets add exact-total / exact-score information.
"""
from __future__ import annotations

import json, math, random, re, sys, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine';VALID=ROOT/'validation'
for p in (ENGINE,VALID):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import v6_pristine_forward_result_resolver_v612 as results
from platform_core import parse_iso_datetime

RAW=ROOT/'evidence'/'direct_provider_probes'/'kambi'
OUT=ROOT/'manifests'/'v6_score_signal_random100_v6173_status.json'
SEED=6173001
SCORE_MAX=8
EPS=1e-8
TOL=1e-9
MAX_ITER=5000
MIN_RESULT_AGE=timedelta(hours=2)

COMP_MAP={
 'Premier League':'ENG_PremierLeague','Bundesliga':'GER_Bundesliga','Serie A':'ITA_SerieA','Ligue 1':'FRA_Ligue1',
 'LaLiga':'ESP_LaLiga','La Liga':'ESP_LaLiga','Liga Portugal':'POR_PrimeiraLiga','Primeira Liga':'POR_PrimeiraLiga',
 'Eredivisie':'NED_Eredivisie','Super League':'SUI_SuperLeague','Scottish Premiership':'SCO_Premiership','Premiership':'SCO_Premiership',
 'Allsvenskan':'SWE_Allsvenskan','Eliteserien':'NOR_Eliteserien','J1 League':'JPN_J1','J League':'JPN_J1','J.League':'JPN_J1',
 'K-League 1':'KOR_KLeague1','K League 1':'KOR_KLeague1','Brasileirao Serie A':'BRA_SerieA','Brasileirão Serie A':'BRA_SerieA',
 'Liga Profesional Argentina':'ARG_Primera','Major League Soccer':'USA_MLS','MLS':'USA_MLS','Champions League':'UEFA_ChampionsLeague','UEFA Champions League':'UEFA_ChampionsLeague'}

def eng(o):
    if not isinstance(o,dict): return ''
    return str(o.get('englishLabel') or o.get('englishName') or o.get('label') or o.get('name') or '').strip()

def dec(raw):
    try: x=float(raw)/1000.0
    except Exception: return None
    return x if math.isfinite(x) and x>1 else None

def half_line(x): return abs((x-0.5)-round(x-0.5))<=1e-9

def prematch(o):
    tags={str(x).upper() for x in (o.get('tags') or [])}
    return not tags or 'OFFERED_PREMATCH' in tags

def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    s=s.replace('&','and')
    s=re.sub(r'\b(fc|cf|afc|sc|ac|club|football)\b',' ',s)
    s=re.sub(r'-(mg|sp|rj|pr|ba|pa|rs|ce|pe|go|sc)$','',s)
    return re.sub(r'[^a-z0-9]+','',s)

def team_match(a,b):
    x,y=norm(a),norm(b)
    return bool(x and y and (x==y or (len(x)>=7 and x in y) or (len(y)>=7 and y in x)))

def devig(prices):
    inv={k:1.0/v for k,v in prices.items()}; s=sum(inv.values())
    return {k:v/s for k,v in inv.items()}

def rows(m):
    for c in m: yield int(c['home_goals']),int(c['away_goals']),float(c['probability'])

def renorm(m):
    s=sum(p for _,_,p in rows(m));
    if s<=0: raise ValueError('zero mass')
    return [{'home_goals':h,'away_goals':a,'probability':p/s} for h,a,p in rows(m)]

def scale(m,grouper:Callable[[int,int],str],target,label):
    cur=defaultdict(float)
    for h,a,p in rows(m): cur[grouper(h,a)]+=p
    fac={}
    for k,w in target.items():
        mass=cur.get(k,0.0)
        if w>0 and mass<=0: raise ValueError(f'{label} no support {k}')
        fac[k]=w/mass if mass>0 else 0.0
    return renorm([{'home_goals':h,'away_goals':a,'probability':p*fac[grouper(h,a)]} for h,a,p in rows(m)])

def marginal(m,g):
    d=defaultdict(float)
    for h,a,p in rows(m): d[g(h,a)]+=p
    return dict(d)

def residual(m,g,target): return max(abs(marginal(m,g).get(k,0)-v) for k,v in target.items())
def outcome_g(h,a): return 'home' if h>a else 'draw' if h==a else 'away'
def total_g(line):
    k=math.floor(line); return lambda h,a:'under' if h+a<=k else 'over'
def home_g(line):
    k=math.floor(line); return lambda h,a:'under' if h<=k else 'over'
def away_g(line):
    k=math.floor(line); return lambda h,a:'under' if a<=k else 'over'
def btts_g(h,a): return 'yes' if h>0 and a>0 else 'no'

def score_prior(offers):
    vals={}
    for o in offers:
        if not prematch(o) or eng(o.get('criterion') or {})!='Correct Score' or eng(o.get('betOfferType') or {})!='Correct Score': continue
        for x in o.get('outcomes') or []:
            odd=dec(x.get('odds'))
            if not odd: continue
            text=' '.join(str(x.get(k) or '') for k in ('englishLabel','label','participant'))
            m=re.search(r'(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)',text)
            if not m: continue
            h,a=int(m.group(1)),int(m.group(2))
            if 0<=h<=SCORE_MAX and 0<=a<=SCORE_MAX: vals[(h,a)]=1.0/odd
    if len(vals)<8: return None
    base=[]
    for h in range(SCORE_MAX+1):
        for a in range(SCORE_MAX+1): base.append({'home_goals':h,'away_goals':a,'probability':vals.get((h,a),0.0)+EPS})
    return renorm(base)

def parse_markets(offers,home,away):
    one=None; match_tot={}; ht={}; at={}; btts=None
    for o in offers:
        if not isinstance(o,dict) or not prematch(o): continue
        c=eng(o.get('criterion') or {}); t=eng(o.get('betOfferType') or {}); outs=[x for x in (o.get('outcomes') or []) if isinstance(x,dict)]
        by={str(x.get('type')):x for x in outs}
        if c=='Full Time' and {'OT_ONE','OT_CROSS','OT_TWO'}<=set(by):
            pp={k:dec(by[z].get('odds')) for k,z in {'home':'OT_ONE','draw':'OT_CROSS','away':'OT_TWO'}.items()}
            if all(pp.values()): one=devig(pp)
        if c=='Total Goals' and t=='Over/Under':
            ov=by.get('OT_OVER'); un=by.get('OT_UNDER')
            if ov and un:
                try: line=float(ov.get('line') if ov.get('line') is not None else un.get('line'))/1000.0
                except Exception: continue
                po,pu=dec(ov.get('odds')),dec(un.get('odds'))
                if half_line(line) and po and pu: match_tot[line]=devig({'over':po,'under':pu})
        if c.startswith('Total Goals by ') and '1st Half' not in c and '2nd Half' not in c and t=='Over/Under':
            who=c[len('Total Goals by '):].strip(); ov=by.get('OT_OVER'); un=by.get('OT_UNDER')
            if ov and un:
                try: line=float(ov.get('line') if ov.get('line') is not None else un.get('line'))/1000.0
                except Exception: continue
                po,pu=dec(ov.get('odds')),dec(un.get('odds'))
                if half_line(line) and po and pu:
                    target=devig({'over':po,'under':pu})
                    if team_match(who,home): ht[line]=target
                    elif team_match(who,away): at[line]=target
        if c=='Both Teams To Score' and t=='Yes/No':
            y=by.get('OT_YES'); n=by.get('OT_NO')
            if y and n:
                py,pn=dec(y.get('odds')),dec(n.get('odds'))
                if py and pn: btts=devig({'yes':py,'no':pn})
    return one,sorted(match_tot.items()),sorted(ht.items()),sorted(at.items()),btts

def project(prior,constraints):
    m=renorm(prior)
    for it in range(1,MAX_ITER+1):
        for label,g,t in constraints: m=scale(m,g,t,label)
        worst=max(residual(m,g,t) for _,g,t in constraints) if constraints else 0.0
        if worst<=TOL: return m,it,worst
    raise ValueError('nonconvergence')

def metrics(m,hg,ag):
    r=sorted([(p,h,a) for h,a,p in rows(m)],reverse=True); actual=hg+ag
    td=[0.0]*10
    for p,h,a in r: td[min(h+a,9)]+=p
    tr=sorted([(p,i) for i,p in enumerate(td)],reverse=True)
    return {'score_top1':int((r[0][1],r[0][2])==(hg,ag)),'score_top3':int((hg,ag) in {(h,a) for _,h,a in r[:3]}),'total_top1':int(tr[0][1]==min(actual,9))}

def resolve(cid,kickoff,home,away,cache):
    found=[]
    for token,payload,url in results.fetch_scoreboards(cid,kickoff,cache):
        for ev in payload.get('events') or []:
            try: ek=parse_iso_datetime(str(ev.get('date') or ''),'event date')
            except Exception: continue
            if abs(ek-kickoff)>results.KICKOFF_TOLERANCE: continue
            comps=ev.get('competitions') or []
            if not comps or not isinstance(comps[0],dict): continue
            comp=comps[0]; cs=comp.get('competitors') or []
            hh=next((x for x in cs if isinstance(x,dict) and x.get('homeAway')=='home'),None); aa=next((x for x in cs if isinstance(x,dict) and x.get('homeAway')=='away'),None)
            if not hh or not aa: continue
            hnames=results.competitor_names(hh); anames=results.competitor_names(aa)
            if any(team_match(home,x) for x in hnames) and any(team_match(away,x) for x in anames):
                score=results.regulation_score(ev,comp)
                if score is not None: found.append((str(ev.get('id') or ''),score,url))
    uniq={x[0]:x for x in found}
    if len(uniq)!=1: return None
    _,(hg,ag,method),url=next(iter(uniq.values())); return int(hg),int(ag),method,url

def main():
    now=datetime.now(timezone.utc).replace(microsecond=0); earliest={}; stats=Counter()
    for p in sorted(RAW.rglob('*.json')) if RAW.exists() else []:
        try: env=json.loads(p.read_text(encoding='utf-8')); ident=env.get('list_event_identity') or {}; eid=str(env.get('event_id') or ident.get('id') or ''); obs=parse_iso_datetime(str(env.get('observed_at_utc') or ''),'observed'); ko=parse_iso_datetime(str(ident.get('start') or ''),'kickoff')
        except Exception: continue
        cid=COMP_MAP.get(str(ident.get('group') or '').strip()); home=str(ident.get('homeName') or ''); away=str(ident.get('awayName') or '')
        if not eid or not cid or not home or not away or not (obs<ko<=now-MIN_RESULT_AGE): continue
        offers=((env.get('payload') or {}).get('betOffers') or [])
        prior=score_prior(offers)
        if prior is None: continue
        one,mt,ht,at,bt=parse_markets(offers,home,away)
        if not one or not mt or not ht or not at or not bt: continue
        old=earliest.get(eid)
        if old is None or obs<old['obs']: earliest[eid]={'path':p,'env':env,'obs':obs,'ko':ko,'cid':cid,'home':home,'away':away,'prior':prior,'one':one,'mt':mt,'ht':ht,'at':at,'bt':bt}
    stats['eligible_prematch_market_events']=len(earliest)
    cache={}; resolved=[]
    for eid,x in earliest.items():
        r=resolve(x['cid'],x['ko'],x['home'],x['away'],cache)
        if r is None: stats['result_unresolved']+=1; continue
        x['eid']=eid;x['result']=r;resolved.append(x)
    stats['resolved_events']=len(resolved)
    rnd=random.Random(SEED); rnd.shuffle(resolved); sample=resolved[:100]
    stats['sample_count']=len(sample)
    arms={k:Counter() for k in ('cs_prior','match','teamtotals','full')}; rows_out=[]; failures=0
    for x in sample:
        hg,ag,method,url=x['result']
        c_match=[('1x2',outcome_g,x['one'])]+[(f'T{line}',total_g(line),t) for line,t in x['mt']]
        c_team=c_match+[(f'H{line}',home_g(line),t) for line,t in x['ht']]+[(f'A{line}',away_g(line),t) for line,t in x['at']]
        c_full=c_team+[('BTTS',btts_g,x['bt'])]
        try:
            mats={'cs_prior':x['prior']}
            mats['match']=project(x['prior'],c_match)[0];mats['teamtotals']=project(x['prior'],c_team)[0];mats['full']=project(x['prior'],c_full)[0]
        except Exception:
            failures+=1;continue
        out={'event_id':x['eid'],'competition_id':x['cid'],'kickoff_utc':x['ko'].isoformat(),'observed_at_utc':x['obs'].isoformat(),'home':x['home'],'away':x['away'],'actual':[hg,ag],'team_total_lines':[len(x['ht']),len(x['at'])],'match_total_lines':len(x['mt'])}
        for name,m in mats.items():
            z=metrics(m,hg,ag);out[name]=z
            for k,v in z.items(): arms[name][k]+=v
            arms[name]['count']+=1
        rows_out.append(out)
    summary={}
    for name,c in arms.items():
        n=c['count'];summary[name]={'count':n,'exact_total_top1':c['total_top1']/n if n else None,'score_top1':c['score_top1']/n if n else None,'score_top3':c['score_top3']/n if n else None}
    report={'schema_version':'V6.17.3-random100-direct-score-signals-r1','generated_at_utc':now.isoformat(),'status':'PASS' if len(rows_out)==100 else 'PARTIAL_INSUFFICIENT_ELIGIBLE_OR_PROJECTION_FAILURES','classification':'RETROSPECTIVE_RANDOM_SAMPLE_OF_TIMESTAMPED_PREMATCH_MARKETS','random_seed':SEED,'sampling':'fixed-seed shuffle of all uniquely resolved eligible events; earliest pre-match snapshot per event; no outcome-based filtering','stats':dict(stats),'projection_failures':failures,'summary':summary,'sample':rows_out,'governance':{'research_only':True,'no_test_tuning':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
