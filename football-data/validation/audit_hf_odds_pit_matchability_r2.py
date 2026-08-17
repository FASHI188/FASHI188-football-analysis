from __future__ import annotations

import csv, hashlib, json, math, re, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

HF=Path('/tmp/hf-football-odds-2023-24/match_odds.csv')
OUT=Path('football-data/manifests/hf_odds_pit_matchability_r2.json')
OUT90=Path('football-data/manifests/hf_odds_pit_fixed500_t90_r2.csv')
OUT5=Path('football-data/manifests/hf_odds_pit_fixed500_t5_r2.csv')
UK=ZoneInfo('Europe/London')
LEAGUES={
 'PREMIER LEAGUE':('ENG_PremierLeague',Path('football-data/processed/ENG_PremierLeague/2023-24.csv')),
 'LIGA':('ESP_LaLiga',Path('football-data/processed/ESP_LaLiga/2023-24.csv')),
 'SERIE A':('ITA_SerieA',Path('football-data/processed/ITA_SerieA/2023-24.csv')),
 'BUNDESLIGA':('GER_Bundesliga',Path('football-data/processed/GER_Bundesliga/2023-24.csv')),
 'LIGUE 1':('FRA_Ligue1',Path('football-data/processed/FRA_Ligue1/2023-24.csv')),
}
ALIASES={
 'manchester city':'man city','manchester united':'man united','nottingham forest':'nottm forest',
 'wolverhampton wanderers':'wolves','wolverhampton':'wolves','tottenham hotspur':'tottenham',
 'newcastle united':'newcastle','west ham united':'west ham','sheffield united':'sheffield utd',
 'brighton hove albion':'brighton','paris saint germain':'paris sg','psg':'paris sg',
 'tolosa':'toulouse','nizza':'nice','lione':'lyon','marsiglia':'marseille','strasburgo':'strasbourg',
 'lipsia':'rb leipzig','stoccarda':'stuttgart','friburgo':'freiburg','colonia':'fc koln',
 'eintracht francofort':'ein frankfurt','eintracht frankfurt':'ein frankfurt',
 'borussia monchengladbach':'mgladbach','borussia m gladbach':'mgladbach','monchengladbach':'mgladbach',
 'atletico madrid':'ath madrid','athletic bilbao':'ath bilbao','inter milan':'inter','internazionale':'inter',
 'hellas verona':'verona','bayern monaco':'bayern munich','cologne':'fc koln','koln':'fc koln',
 'borussia dortmund':'dortmund','real sociedad san sebastian':'sociedad','real sociedad':'sociedad',
 'real betis balompie':'betis','deportivo alaves':'alaves','celta vigo':'celta','cadiz cf':'cadiz',
 'granada cf':'granada','las palmas':'las palmas','rayo vallecano':'vallecano',
}

def norm(s):
 s=unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower()
 s=re.sub(r'[^a-z0-9]+',' ',s)
 toks=[t for t in s.split() if t not in {'fc','cf','afc','ssc','calcio','club'}]
 z=' '.join(toks)
 return ALIASES.get(z,z)

def sim(a,b):
 a,b=norm(a),norm(b)
 return 1.0 if a==b else SequenceMatcher(None,a,b).ratio()

def q(xs,p):
 if not xs:return None
 ys=sorted(xs); pos=(len(ys)-1)*p; lo=math.floor(pos); hi=math.ceil(pos)
 return ys[lo] if lo==hi else ys[lo]*(hi-pos)+ys[hi]*(pos-lo)

def repo_dt(d,t):
 dd=datetime.strptime(d.strip(),'%d/%m/%Y').date(); tt=datetime.strptime(t.strip(),'%H:%M').time()
 return datetime.combine(dd,tt,tzinfo=UK).astimezone(timezone.utc)

def hf_dt(s):
 return datetime.fromisoformat((s or '').strip()).replace(tzinfo=UK).astimezone(timezone.utc)

fixtures=defaultdict(list); exact={}
for _,(league,path) in LEAGUES.items():
 with path.open('r',encoding='utf-8-sig',newline='',errors='replace') as f:
  for r in csv.DictReader(f):
   try:
    ko=repo_dt(r['Date'],r['Time']); hg=int(float(r['FTHG'])); ag=int(float(r['FTAG']))
   except Exception: continue
   z={'league_id':league,'home':r['HomeTeam'],'away':r['AwayTeam'],'kickoff_utc':ko,'fthg':hg,'ftag':ag}
   fixtures[league].append(z); exact[(league,norm(z['home']),norm(z['away']))]=z

keys={}; comp_rows=defaultdict(int)
with HF.open('r',encoding='utf-8-sig',newline='',errors='replace') as f:
 for r in csv.DictReader(f):
  c=(r.get('competition') or '').strip().upper(); comp_rows[c]+=1
  if c in LEAGUES:
   k=(c,r.get('home_team','').strip(),r.get('away_team','').strip(),r.get('refreshed_at','').strip())
   keys[k]=None

matched={}; unmatched=[]; refresh_delta=[]; scores=[]
for k in keys:
 c,h,a,refresh=k; league,_=LEAGUES[c]
 rec=exact.get((league,norm(h),norm(a))); best=2.0 if rec else -1.0
 if rec is None:
  try: rd=hf_dt(refresh).astimezone(UK).date()
  except Exception: rd=None
  cand=fixtures[league]
  if rd: cand=[x for x in cand if abs((x['kickoff_utc'].astimezone(UK).date()-rd).days)<=1]
  arr=sorted(((sim(h,x['home'])+sim(a,x['away']),x) for x in cand),key=lambda z:z[0],reverse=True)
  if arr:
   best,rec=arr[0]; second=arr[1][0] if len(arr)>1 else -1
   if best<1.35 or best-second<0.08: rec=None
 if rec is None:
  unmatched.append({'competition':c,'home':h,'away':a,'refreshed_at':refresh,'best_score_sum':best}); continue
 matched[k]=rec; scores.append(best)
 try: refresh_delta.append((hf_dt(refresh)-rec['kickoff_utc']).total_seconds())
 except Exception: pass

CUTS=[360,90,15,5]
snaps={k:{m:None for m in CUTS} for k in matched}; valid=post=0
with HF.open('r',encoding='utf-8-sig',newline='',errors='replace') as f:
 for r in csv.DictReader(f):
  c=(r.get('competition') or '').strip().upper()
  if c not in LEAGUES: continue
  k=(c,r.get('home_team','').strip(),r.get('away_team','').strip(),r.get('refreshed_at','').strip()); rec=matched.get(k)
  if rec is None: continue
  try:
   ts=hf_dt(r.get('U/O 2.5 timestamp','')); over=float(r.get('odds_over_2.5','')); under=float(r.get('odds_under_2.5',''))
   if over<=1 or under<=1: continue
  except Exception: continue
  valid+=1
  if ts>=rec['kickoff_utc']: post+=1
  for m in CUTS:
   cutoff=rec['kickoff_utc']-timedelta(minutes=m)
   if ts<=cutoff:
    old=snaps[k][m]
    if old is None or ts>old['ts']:
     snaps[k][m]={'ts':ts,'over':over,'under':under,'age_min':(cutoff-ts).total_seconds()/60}

def coverage(m):
 ages=[s[m]['age_min'] for s in snaps.values() if s[m]]
 return {'n':len(ages),'p50_age_min':q(ages,.5),'p90_age_min':q(ages,.9),'p95_age_min':q(ages,.95),
         'age_le_15m':sum(x<=15 for x in ages),'age_le_30m':sum(x<=30 for x in ages),
         'age_le_60m':sum(x<=60 for x in ages),'age_le_360m':sum(x<=360 for x in ages),
         'age_le_1440m':sum(x<=1440 for x in ages)}

def cohort(m,max_age=1440):
 arr=[]
 for k,rec in matched.items():
  s=snaps[k][m]
  if not s or s['age_min']>max_age: continue
  ident=f"{rec['league_id']}|{rec['kickoff_utc'].isoformat()}|{rec['home']}|{rec['away']}"
  arr.append((hashlib.sha256(ident.encode()).hexdigest(),rec,s))
 arr.sort(key=lambda z:z[0]); return arr

def write500(path,arr,m):
 if len(arr)<500:return 0
 with path.open('w',encoding='utf-8',newline='') as f:
  cols=['sha256_rank','league_id','kickoff_utc','home_team','away_team','fthg','ftag','total_goals','freeze_minutes','quote_utc','quote_age_to_cutoff_min','odds_over_2.5','odds_under_2.5','fair_over_2.5','fair_under_2.5']
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
  for sha,r,s in arr[:500]:
   io=1/s['over']; iu=1/s['under']; den=io+iu
   w.writerow({'sha256_rank':sha,'league_id':r['league_id'],'kickoff_utc':r['kickoff_utc'].isoformat(),'home_team':r['home'],'away_team':r['away'],'fthg':r['fthg'],'ftag':r['ftag'],'total_goals':r['fthg']+r['ftag'],'freeze_minutes':m,'quote_utc':s['ts'].isoformat(),'quote_age_to_cutoff_min':round(s['age_min'],3),'odds_over_2.5':s['over'],'odds_under_2.5':s['under'],'fair_over_2.5':io/den,'fair_under_2.5':iu/den})
 return 500

c90=cohort(90,1440); c5=cohort(5,1440)
OUT.parent.mkdir(parents=True,exist_ok=True)
n90=write500(OUT90,c90,90); n5=write500(OUT5,c5,5)
# Four-point trajectory diagnostics only; not required for the single-OU fixed500.
all4=[]
for k,r in matched.items():
 ss=snaps[k]
 if all(ss[m] for m in CUTS):
  all4.append((len({ss[m]['ts'] for m in CUTS}),ss[5]['age_min']))
summary={
 'schema_version':'hf-odds-pit-matchability-r2',
 'status':'PASS_SINGLE_OU_FIXED500_READY' if n90==500 else 'INSUFFICIENT_SINGLE_OU_FIXED500',
 'clock_correction':'football-data.co.uk Date+Time and HF timestamps interpreted as Europe/London clock; continental fixture CSV Time is UK time',
 'source_rows_approx':1956225,'source_competition_rows':dict(sorted(comp_rows.items())),
 'hf_unique_top5_match_keys':len(keys),'repo_fixture_counts':{k:len(v) for k,v in fixtures.items()},
 'matched_match_keys':len(matched),'unmatched_match_keys':len(unmatched),'unmatched_examples':unmatched[:40],
 'match_score_p05':q(scores,.05),
 'refreshed_at_minus_repo_kickoff_seconds':{'n':len(refresh_delta),'median':q(refresh_delta,.5),'p05':q(refresh_delta,.05),'p95':q(refresh_delta,.95),'abs_p95':q([abs(x) for x in refresh_delta],.95)},
 'valid_ou_rows':valid,'ou_rows_at_or_after_kickoff_excluded':post,
 'cutoff_coverage':{str(m):coverage(m) for m in CUTS},
 'single_ou_age_le_24h_candidates':{'t90':len(c90),'t5':len(c5)},
 'selected_fixed500':{'t90':n90,'t5':n5},
 'trajectory_diagnostic':{'all4_any_age':len(all4),'distinct_ge2':sum(d>=2 for d,_ in all4),'distinct_ge3':sum(d>=3 for d,_ in all4),'t5_age_le60m':sum(a<=60 for _,a in all4),'distinct_ge3_and_t5_age_le60m':sum(d>=3 and a<=60 for d,a in all4)},
 'selection_guard':{'result_labels_used_for_selection':False,'all_quotes_at_or_before_freeze':True,'post_kickoff_quotes_excluded':True,'max_quote_age_to_cutoff_minutes':1440,'deterministic_sha256_ordering':True},
 'governance':{'formal_weight':0,'main_mutation':False,'formal_model_mutation':False}
}
OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
if n90!=500: raise SystemExit('T-90 strict PIT single-OU fixed500 not available')
