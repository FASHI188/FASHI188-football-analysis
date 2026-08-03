#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,re,urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

SOURCES={
 'ARG_Primera':'ARG','BRA_SerieA':'BRA','JPN_J1':'JPN','NOR_Eliteserien':'NOR',
 'SWE_Allsvenskan':'SWE','SUI_SuperLeague':'SWZ','USA_MLS':'USA'}

def hf(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def norm(s):return re.sub(r'[^a-z0-9]','',str(s).lower().replace('&','and').replace("'",''))
def norm_season(s):
 s=str(s).strip().replace('-','/').replace(' ','')
 m=re.fullmatch(r'(\d{4})/(\d{2}|\d{4})',s)
 if m:return f"{m.group(1)}/{m.group(2)[-2:]}"
 m=re.search(r'(20\d{2})',s)
 return m.group(1) if m else s
def day(s):
 s=str(s).strip()
 for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d','%d.%m.%Y'):
  try:return datetime.strptime(s,f).strftime('%Y-%m-%d')
  except ValueError:pass
 raise ValueError(f'bad date {s!r}')
def get(url,path):
 req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 GOLD1000 static research audit'})
 with urllib.request.urlopen(req,timeout=90) as r:path.write_bytes(r.read())
def read_csv(path):
 b=path.read_bytes()
 for enc in ('utf-8-sig','cp1252','latin-1'):
  try:text=b.decode(enc);break
  except UnicodeDecodeError:continue
 return list(csv.DictReader(text.splitlines()))
def first(row,names):
 for n in names:
  if n in row and row[n] not in ('',None):return row[n]
 return ''
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sample',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);rawdir=a.out/'raw';rawdir.mkdir(exist_ok=True)
 with a.sample.open(encoding='utf-8-sig',newline='') as f:sample=list(csv.DictReader(f))
 target=[r for r in sample if r['competition_id'] in SOURCES];index={};sources=[];all_fields=[]
 for comp,code in SOURCES.items():
  url=f'https://www.football-data.co.uk/new/{code}.csv';p=rawdir/f'{comp}_{code}.csv';get(url,p);rr=read_csv(p);headers=list(rr[0]) if rr else []
  for c in headers:
   if c not in all_fields:all_fields.append(c)
  sources.append({'competition':comp,'url':url,'file':str(p.relative_to(a.out)),'sha256':hf(p),'rows':len(rr),'columns':headers})
  for x in rr:
   home=first(x,('Home','HomeTeam'));away=first(x,('Away','AwayTeam'));date=first(x,('Date',));season=first(x,('Season',))
   if not home or not away or not date or not season:continue
   try:k=(comp,norm_season(season),day(date),norm(home),norm(away))
   except Exception:continue
   if k in index:raise ValueError(f'duplicate source key {k}')
   index[k]=(x,url,hf(p))
 matched=[];unmatched=[]
 for r in target:
  k=(r['competition_id'],norm_season(r['season']),r['date'],norm(r['home_team']),norm(r['away_team']))
  hit=index.get(k)
  if not hit:unmatched.append(r);continue
  x,url,sha=hit;o={z:r[z] for z in ('gold_sample_id','match_identity','competition_id','season','date','home_team','away_team','label_result')};o.update({'source_url':url,'source_file_sha256':sha})
  for c in all_fields:o['fdx_'+c]=x.get(c,'')
  matched.append(o)
 base=['gold_sample_id','match_identity','competition_id','season','date','home_team','away_team','label_result','source_url','source_file_sha256'];fields=base+['fdx_'+x for x in all_fields]
 mp=a.out/'GOLD1000_R1_football_data_extra_enriched.csv'
 with mp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(matched)
 up=a.out/'GOLD1000_R1_football_data_extra_unmatched.csv'
 with up.open('w',encoding='utf-8',newline='') as f:
  fs=['gold_sample_id','match_identity','competition_id','season','date','home_team','away_team'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows({k:r[k] for k in fs} for r in unmatched)
 coverage=[]
 for c in all_fields:
  n=sum(r.get('fdx_'+c,'') not in ('',None) for r in matched);coverage.append({'field':c,'matched_rows':len(matched),'present':n,'missing':len(matched)-n,'coverage_on_matched':f'{n/len(matched):.6f}' if matched else '0'})
 cp=a.out/'GOLD1000_R1_football_data_extra_field_coverage.csv'
 with cp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(coverage[0]));w.writeheader();w.writerows(coverage)
 rec={'schema_version':'GOLD1000-FOOTBALL-DATA-EXTRA-R1','static_download_only':True,'api_calls':0,'target_rows':len(target),'matched_rows':len(matched),'unmatched_rows':len(unmatched),'match_rate':len(matched)/len(target) if target else 0,'target_by_competition':dict(sorted(Counter(r['competition_id'] for r in target).items())),'matched_by_competition':dict(sorted(Counter(r['competition_id'] for r in matched).items())),'sources':sources,'outputs':{mp.name:hf(mp),up.name:hf(up),cp.name:hf(cp)}}
 rp=a.out/'GOLD1000_R1_football_data_extra_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 manifest={'schema_version':'GOLD1000-FOOTBALL-DATA-EXTRA-ARTIFACT-R1','files':{}}
 for p in sorted(a.out.rglob('*')):
  if p.is_file() and p.name!='manifest.json':manifest['files'][str(p.relative_to(a.out))]={'sha256':hf(p),'bytes':p.stat().st_size}
 (a.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','target':len(target),'matched':len(matched),'unmatched':len(unmatched)},ensure_ascii=False))
if __name__=='__main__':main()
