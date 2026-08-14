#!/usr/bin/env python3
from __future__ import annotations
import base64, csv, hashlib, html, io, json, re, urllib.request
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'football-data'/'research'
PARTS=[R/f'r55_candidate_identity_part{i}.csv' for i in range(1,5)]
OUT=R/'r55_independent_ou_matched300_zero_label.json'
SOURCES={
 'England: Premier League':('Premier.csv','6d94d4ae4e3a995261b5c3aa92e48ddf05d46e74'),
 'England: Championship':('Championship.csv','57cf8071f86b45870018bf90554f612c66b281d1'),
 'England: League One':('League 1.csv','c7f3399aa0f6b7ca8c8b10844fc7bbba78de9ab2'),
 'England: League Two':('League 2.csv','3aa62ec193d7e590004c34ff08e91c2b57122c76'),
}
CONTAMINATED={'2142033','2157450','2157451','2148155','2157452','879658','879659','879663','879664','879665'}
ALIASES={
 'newcastleutd':'newcastle','sheffieldutd':'sheffieldunited','cambridgeutd':'cambridge','oxfordutd':'oxford',
 'gillinghamfc':'gillingham','nottingham':'nottmforest','nottinghamforest':'nottmforest',
 'miltonkeynesdons':'mkdons','dagenhamred':'dagenhamredbridge','dagenhamredbridge':'dagenhamredbridge',
 'hullcity':'hull','stoke city':'stoke','stokecity':'stoke','swansea':'swansea','crawleytown':'crawleytown',
}
def norm(s:str)->str:
 s=html.unescape(s or '').lower().replace('&',' and ')
 s=re.sub(r'\bfc\b','',s)
 s=re.sub(r'[^a-z0-9]+','',s)
 return ALIASES.get(s,s)
def fodd(v):
 try:
  x=float(v); return x if x>1 else None
 except: return None
def fetch_blob(sha):
 url=f'https://api.github.com/repos/jokecamp/FootballData/git/blobs/{sha}'
 req=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'r55-zero-label/1.0'})
 with urllib.request.urlopen(req,timeout=60) as resp: obj=json.load(resp)
 raw=base64.b64decode(obj['content'])
 assert hashlib.sha1((f'blob {len(raw)}\0').encode()+raw).hexdigest()==sha
 return raw.decode('latin1')

def parse_date(s):
 for fmt in ('%d/%m/%y','%d/%m/%Y'):
  try:return datetime.strptime(s.strip(),fmt).date().isoformat()
  except:pass
 raise ValueError(s)

# Candidate rows contain identity only. No archive score/result columns exist in these files.
cands=[]
for p in PARTS:
 with p.open(encoding='utf-8',newline='') as f:
  for row in csv.DictReader(f):
   assert row['match_id'] not in CONTAMINATED
   cands.append(row)
assert len(cands)==400

# Project public source to identity + independent OU only. FTHG/FTAG/FTR are never accessed.
ou={}
source_meta={}
for league,(name,sha) in SOURCES.items():
 text=fetch_blob(sha)
 reader=csv.DictReader(io.StringIO(text))
 required={'Date','HomeTeam','AwayTeam','BbAv>2.5','BbAv<2.5'}
 if not required.issubset(set(reader.fieldnames or [])):
  raise SystemExit(f'missing required OU fields for {league}: {required-set(reader.fieldnames or [])}')
 kept=0
 for row in reader:
  over=fodd(row.get('BbAv>2.5')); under=fodd(row.get('BbAv<2.5'))
  if over is None or under is None: continue
  d=parse_date(row['Date']); key=(league,d,norm(row['HomeTeam']),norm(row['AwayTeam']))
  if key in ou: raise SystemExit(f'duplicate source identity {key}')
  inv_o,inv_u=1/over,1/under; s=inv_o+inv_u
  ou[key]={'ou_over_2_5':over,'ou_under_2_5':under,'q_over_2_5':inv_o/s,'q_under_2_5':inv_u/s,
           'source_home':row['HomeTeam'],'source_away':row['AwayTeam'],'source_blob_sha':sha}
  kept+=1
 source_meta[league]={'filename':name,'blob_sha':sha,'usable_ou_rows':kept}

matched=[]; unmatched=[]
for row in cands:
 d=row['match_datetime'][:10]
 key=(row['league'],d,norm(row['home_team']),norm(row['away_team']))
 hit=ou.get(key)
 if hit:
  matched.append({**row,**hit})
 else:
  unmatched.append({'match_id':row['match_id'],'league':row['league'],'date':d,'home_team':row['home_team'],'away_team':row['away_team']})
matched.sort(key=lambda r:(r['match_datetime'],int(r['match_id'])))
if len(matched)<300:
 raise SystemExit(f'MATCH_GATE_FAIL: only {len(matched)} of 400 candidates have strict identity + complete OU; need 300')
frozen=matched[:300]
ids='\n'.join(r['match_id'] for r in frozen).encode()
sample_sha=hashlib.sha256(ids).hexdigest()
payload={
 'experiment':'R55_ARCHIVE_1X2_PLUS_INDEPENDENT_FOOTBALLDATA_OU_ZERO_LABEL',
 'status':'ZERO_LABEL_MATCH_FREEZE_PASS',
 'candidate_count':len(cands),'strict_matched_count':len(matched),'frozen_count':len(frozen),
 'selection':'sort by archive match_datetime then numeric match_id; first 300 strict date+normalized-home+normalized-away matches with complete BbAv OU2.5 pair',
 'label_access':{'archive_score_columns':0,'football_data_FTHG':0,'football_data_FTAG':0,'football_data_FTR':0},
 'ou_definition':'de-vigged BbAv>2.5/BbAv<2.5; retrospective historical market reference; no original quote timestamp',
 'source_meta':source_meta,
 'contaminated_ids_excluded':sorted(CONTAMINATED),
 'sample_id_sha256':sample_sha,
 'time_window':[frozen[0]['match_datetime'],frozen[-1]['match_datetime']],
 'unmatched_count':len(unmatched),'unmatched_preview':unmatched[:20],
 'frozen':frozen,
}
OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps({k:payload[k] for k in ['status','candidate_count','strict_matched_count','frozen_count','sample_id_sha256','time_window','unmatched_count']},indent=2))
