#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json,re
from collections import defaultdict,deque
from datetime import datetime
from pathlib import Path

SEASONS={'2324':'2023/24','2425':'2024/25','2526':'2025/26'}
STAT_PAIRS={'shots':('HS','AS'),'sot':('HST','AST'),'corners':('HC','AC'),'fouls':('HF','AF'),'yellow':('HY','AY'),'red':('HR','AR'),'ht_goals':('HTHG','HTAG')}
def hf(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def norm(s):return re.sub(r'[^a-z0-9]','',str(s).lower().replace('&','and').replace("'",''))
def day(s):
 for f in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
  try:return datetime.strptime(str(s).strip(),f).strftime('%Y-%m-%d')
  except ValueError:pass
 raise ValueError(s)
def number(v):
 try:return float(v)
 except (TypeError,ValueError):return None
def avg(q,n):
 a=[v for v in list(q)[-n:] if v is not None];return '' if not a else sum(a)/len(a)
def rate(q,n):return avg(q,n)
def read_csv(path):
 b=path.read_bytes()
 for enc in ('utf-8-sig','cp1252','latin-1'):
  try:text=b.decode(enc);break
  except UnicodeDecodeError:continue
 return list(csv.DictReader(text.splitlines()))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sample',type=Path,required=True);ap.add_argument('--raw-dir',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 with a.sample.open(encoding='utf-8-sig',newline='') as f:sample=list(csv.DictReader(f))
 target={r['match_identity']:r for r in sample};key_to_id={(r['competition_id'],r['season'],r['date'],norm(r['home_team']),norm(r['away_team'])):r['match_identity'] for r in sample}
 rows=[];source_hashes={}
 for p in sorted(a.raw_dir.glob('*.csv')):
  parts=p.stem.split('_');sc=next((x for x in parts if x in SEASONS),None)
  if sc is None:continue
  comp=p.stem[:p.stem.index('_'+sc)]
  source_hashes[p.name]=hf(p)
  for r in read_csv(p):
   try:dt=day(r['Date'])
   except Exception:continue
   x=dict(r);x['_comp']=comp;x['_season']=SEASONS[sc];x['_date']=dt;rows.append(x)
 by=defaultdict(list)
 for r in rows:by[(r['_comp'],r['_season'])].append(r)
 feats={};feature_names=[]
 for stat in STAT_PAIRS:
  for side in ('for','against'):
   for n in (5,10):feature_names += [f'home_{stat}_{side}_avg_{n}',f'away_{stat}_{side}_avg_{n}']
 for n in (5,10):feature_names += [f'home_ht_draw_rate_{n}',f'away_ht_draw_rate_{n}',f'home_low_event_rate_{n}',f'away_low_event_rate_{n}']
 for (comp,season),rs in sorted(by.items()):
  rs.sort(key=lambda r:(r['_date'],str(r.get('HomeTeam','')),str(r.get('AwayTeam',''))));state=defaultdict(lambda:defaultdict(lambda:deque(maxlen=20)));days=defaultdict(list)
  for r in rs:days[r['_date']].append(r)
  for dt in sorted(days):
   for r in days[dt]:
    h,a_team=r['HomeTeam'],r['AwayTeam'];k=(comp,season,dt,norm(h),norm(a_team));mid=key_to_id.get(k)
    if mid:
     o={}
     for stat in STAT_PAIRS:
      for side in ('for','against'):
       for n in (5,10):o[f'home_{stat}_{side}_avg_{n}']=avg(state[h][f'{stat}_{side}'],n);o[f'away_{stat}_{side}_avg_{n}']=avg(state[a_team][f'{stat}_{side}'],n)
     for n in (5,10):o[f'home_ht_draw_rate_{n}']=rate(state[h]['ht_draw'],n);o[f'away_ht_draw_rate_{n}']=rate(state[a_team]['ht_draw'],n);o[f'home_low_event_rate_{n}']=rate(state[h]['low_event'],n);o[f'away_low_event_rate_{n}']=rate(state[a_team]['low_event'],n)
     feats[mid]=o
   for r in days[dt]:
    h,a_team=r['HomeTeam'],r['AwayTeam']
    for stat,(hc,ac) in STAT_PAIRS.items():
     hv,av=number(r.get(hc)),number(r.get(ac));state[h][f'{stat}_for'].append(hv);state[h][f'{stat}_against'].append(av);state[a_team][f'{stat}_for'].append(av);state[a_team][f'{stat}_against'].append(hv)
    hth,hta=number(r.get('HTHG')),number(r.get('HTAG'));htd=None if hth is None or hta is None else int(hth==hta);state[h]['ht_draw'].append(htd);state[a_team]['ht_draw'].append(htd)
    hs,as_=number(r.get('HS')),number(r.get('AS'));hst,ast=number(r.get('HST')),number(r.get('AST'))
    low=None if None in (hs,as_,hst,ast) else int((hs+as_)<=22 and (hst+ast)<=8);state[h]['low_event'].append(low);state[a_team]['low_event'].append(low)
 missing=[r['match_identity'] for r in sample if r['competition_id'] in {'ENG_PremierLeague','ESP_LaLiga','FRA_Ligue1','GER_Bundesliga','ITA_SerieA','NED_Eredivisie','POR_PrimeiraLiga','SCO_Premiership'} and r['match_identity'] not in feats]
 if missing:raise ValueError(f'unmatched selected main rows: {len(missing)}')
 selected=[r for r in sample if r['match_identity'] in feats];op=a.out/'GOLD1000_R1_main_rolling_stats.csv';fields=['gold_sample_id','match_identity','competition_id','season','date','home_team','away_team','label_result']+feature_names
 with op.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for r in selected:o={k:r[k] for k in fields[:8]};o.update(feats[r['match_identity']]);w.writerow(o)
 cp=a.out/'GOLD1000_R1_main_rolling_stats_coverage.csv';coverage=[]
 for k in feature_names:
  n=sum(feats[r['match_identity']][k] not in ('',None) for r in selected);coverage.append({'field':k,'rows':len(selected),'present':n,'missing':len(selected)-n,'coverage':f'{n/len(selected):.6f}'})
 with cp.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(coverage[0]));w.writeheader();w.writerows(coverage)
 rec={'schema_version':'GOLD1000-MAIN-ROLLING-STATS-R1','rows':len(selected),'feature_count':len(feature_names),'same_date_updates_deferred':True,'current_match_stats_used':False,'source_file_sha256':source_hashes,'outputs':{op.name:hf(op),cp.name:hf(cp)}};rp=a.out/'GOLD1000_R1_main_rolling_stats_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 man={'schema_version':'GOLD1000-MAIN-ROLLING-STATS-ARTIFACT-R1','files':{}}
 for p in sorted(a.out.iterdir()):
  if p.is_file() and p.name!='manifest.json':man['files'][p.name]={'sha256':hf(p),'bytes':p.stat().st_size}
 (a.out/'manifest.json').write_text(json.dumps(man,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','rows':len(selected),'features':len(feature_names)},ensure_ascii=False))
if __name__=='__main__':main()
