#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,re,urllib.request
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path

SOURCES={
 'ENG_PremierLeague':'E0','ESP_LaLiga':'SP1','FRA_Ligue1':'F1','GER_Bundesliga':'D1',
 'ITA_SerieA':'I1','NED_Eredivisie':'N1','POR_PrimeiraLiga':'P1','SCO_Premiership':'SC0'}
SEASONS={'2023/24':'2324','2024/25':'2425','2025/26':'2526'}
GROUPS={
 'identity':['Date','Time','HomeTeam','AwayTeam'],
 'result_ht':['FTHG','FTAG','FTR','HTHG','HTAG','HTR'],
 'match_stats':['Referee','HS','AS','HST','AST','HF','AF','HC','AC','HY','AY','HR','AR'],
 'opening_1x2':['B365H','B365D','B365A','BWH','BWD','BWA','IWH','IWD','IWA','PSH','PSD','PSA','WHH','WHD','WHA','VCH','VCD','VCA','MaxH','MaxD','MaxA','AvgH','AvgD','AvgA'],
 'closing_1x2':['B365CH','B365CD','B365CA','BWCH','BWCD','BWCA','IWCH','IWCD','IWCA','PSCH','PSCD','PSCA','WHCH','WHCD','WHCA','VCCH','VCCD','VCCA','MaxCH','MaxCD','MaxCA','AvgCH','AvgCD','AvgCA'],
 'opening_ou25':['B365>2.5','B365<2.5','P>2.5','P<2.5','Max>2.5','Max<2.5','Avg>2.5','Avg<2.5'],
 'closing_ou25':['B365C>2.5','B365C<2.5','PC>2.5','PC<2.5','MaxC>2.5','MaxC<2.5','AvgC>2.5','AvgC<2.5'],
 'opening_ah':['AHh','B365AHH','B365AHA','PAHH','PAHA','MaxAHH','MaxAHA','AvgAHH','AvgAHA'],
 'closing_ah':['AHCh','B365CAHH','B365CAHA','PCAHH','PCAHA','MaxCAHH','MaxCAHA','AvgCAHH','AvgCAHA']}
ALL=[]
for v in GROUPS.values():
 for x in v:
  if x not in ALL:ALL.append(x)
def hf(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def norm(s):return re.sub(r'[^a-z0-9]','',s.lower().replace('&','and').replace("'",''))
def day(s):
 s=s.strip()
 for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
  try:return datetime.strptime(s,f).strftime('%Y-%m-%d')
  except ValueError:pass
 raise ValueError(f'bad date {s!r}')
def get(url,path):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 GOLD1000 research audit contact repository-owner'})
 with urllib.request.urlopen(req,timeout=60) as r:path.write_bytes(r.read())
def read_csv(path):
 b=path.read_bytes()
 for enc in ('utf-8-sig','cp1252','latin-1'):
  try:text=b.decode(enc);break
  except UnicodeDecodeError:continue
 return list(csv.DictReader(text.splitlines()))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sample',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);rawdir=a.out/'raw';rawdir.mkdir(exist_ok=True)
 with a.sample.open(encoding='utf-8-sig',newline='') as f:sample=list(csv.DictReader(f))
 target=[r for r in sample if r['competition_id'] in SOURCES];index={};sources=[]
 for comp,code in SOURCES.items():
  for season,sc in SEASONS.items():
   url=f'https://www.football-data.co.uk/mmz4281/{sc}/{code}.csv';p=rawdir/f'{comp}_{sc}_{code}.csv';get(url,p);rr=read_csv(p);sources.append({'competition':comp,'season':season,'url':url,'file':str(p.relative_to(a.out)),'sha256':hf(p),'rows':len(rr),'columns':list(rr[0]) if rr else []})
   for x in rr:
    try:k=(comp,season,day(x['Date']),norm(x['HomeTeam']),norm(x['AwayTeam']))
    except Exception:continue
    if k in index:raise ValueError(f'duplicate source key {k}')
    index[k]=(x,url,hf(p))
 matched=[];unmatched=[]
 for r in target:
  k=(r['competition_id'],r['season'],r['date'],norm(r['home_team']),norm(r['away_team']))
  hit=index.get(k)
  if not hit:unmatched.append(r);continue
  x,url,sha=hit;o={z:r[z] for z in ('gold_sample_id','match_identity','competition_id','season','date','home_team','away_team','label_result')};o.update({'source_url':url,'source_file_sha256':sha})
  for c in ALL:o['fd_'+c]=x.get(c,'')
  matched.append(o)
 fields=['gold_sample_id','match_identity','competition_id','season','date','home_team','away_team','label_result','source_url','source_file_sha256']+['fd_'+x for x in ALL]
 mp=a.out/'GOLD1000_R1_football_data_main_enriched.csv'
 with mp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(matched)
 up=a.out/'GOLD1000_R1_football_data_main_unmatched.csv'
 with up.open('w',encoding='utf-8',newline='') as f:
  fs=['gold_sample_id','match_identity','competition_id','season','date','home_team','away_team'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows({k:r[k] for k in fs} for r in unmatched)
 coverage=[]
 for g,cols in GROUPS.items():
  for c in cols:
   n=sum(r.get('fd_'+c,'') not in ('',None) for r in matched);coverage.append({'group':g,'field':c,'matched_rows':len(matched),'present':n,'missing':len(matched)-n,'coverage_on_matched':f'{n/len(matched):.6f}' if matched else '0'})
 cp=a.out/'GOLD1000_R1_football_data_main_field_coverage.csv'
 with cp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(coverage[0]));w.writeheader();w.writerows(coverage)
 rec={'schema_version':'GOLD1000-FOOTBALL-DATA-MAIN-R1','static_download_only':True,'api_calls':0,'target_rows':len(target),'matched_rows':len(matched),'unmatched_rows':len(unmatched),'match_rate':len(matched)/len(target) if target else 0,'target_by_competition':dict(sorted(Counter(r['competition_id'] for r in target).items())),'matched_by_competition':dict(sorted(Counter(r['competition_id'] for r in matched).items())),'sources':sources,'outputs':{mp.name:hf(mp),up.name:hf(up),cp.name:hf(cp)}}
 rp=a.out/'GOLD1000_R1_football_data_main_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 manifest={'schema_version':'GOLD1000-FOOTBALL-DATA-ARTIFACT-R1','files':{}}
 for p in sorted(a.out.rglob('*')):
  if p.is_file() and p.name!='manifest.json':manifest['files'][str(p.relative_to(a.out))]={'sha256':hf(p),'bytes':p.stat().st_size}
 (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','target':len(target),'matched':len(matched),'unmatched':len(unmatched)},ensure_ascii=False))
if __name__=='__main__':main()
