#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json
from collections import Counter,defaultdict,deque
from dataclasses import dataclass,field
from datetime import datetime
from pathlib import Path

def hfile(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def d(s):return datetime.strptime(s.strip(),'%Y-%m-%d').date()
def ident(r):return '|'.join(r[k].strip() for k in ('competition_id','season','date','home_team','away_team'))
def rate(q,n):
 a=list(q)[-n:];return '' if not a else sum(a)/len(a)
def avg(q,n):
 a=list(q)[-n:];return '' if not a else sum(a)/len(a)
def recent(q,now,n):return sum(1 for x in q if 0<(now-x).days<=n)
@dataclass
class S:
 m:int=0;p:int=0;gf:int=0;ga:int=0;last:object=None
 dates:deque=field(default_factory=lambda:deque(maxlen=64));draws:deque=field(default_factory=lambda:deque(maxlen=64));totals:deque=field(default_factory=lambda:deque(maxlen=64));cs:deque=field(default_factory=lambda:deque(maxlen=64));fts:deque=field(default_factory=lambda:deque(maxlen=64));one:deque=field(default_factory=lambda:deque(maxlen=64));home:deque=field(default_factory=lambda:deque(maxlen=64));away:deque=field(default_factory=lambda:deque(maxlen=64))
F=['home_rest_days','away_rest_days','home_matches_7d','away_matches_7d','home_matches_14d','away_matches_14d','home_matches_28d','away_matches_28d','home_pre_points','away_pre_points','home_pre_rank','away_pre_rank','points_gap_h_minus_a','rank_gap_h_minus_a','home_pre_gd','away_pre_gd','home_pre_ppg','away_pre_ppg','home_draw_rate_5','away_draw_rate_5','home_draw_rate_10','away_draw_rate_10','home_draw_rate_20','away_draw_rate_20','home_home_draw_rate_10','away_away_draw_rate_10','home_avg_total_goals_10','away_avg_total_goals_10','home_clean_sheet_rate_10','away_clean_sheet_rate_10','home_failed_to_score_rate_10','away_failed_to_score_rate_10','home_one_goal_margin_rate_10','away_one_goal_margin_rate_10','h2h_draw_rate_5','season_progress_home','season_progress_away']
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--repo-root',type=Path,default=Path('.'));ap.add_argument('--sample',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 with a.sample.open('r',encoding='utf-8-sig',newline='') as f: sample=list(csv.DictReader(f))
 comps=sorted({r['competition_id'] for r in sample});allr=[]
 for comp in comps:
  p=a.repo_root/'football-data'/'training_datasets'/comp/'point_in_time.csv'
  with p.open('r',encoding='utf-8-sig',newline='') as f:
   for r in csv.DictReader(f):r=dict(r);r['_id']=ident(r);r['_d']=d(r['date']);allr.append(r)
 by=defaultdict(list)
 for r in allr:by[(r['competition_id'],r['season'])].append(r)
 feats={};h2h=defaultdict(lambda:deque(maxlen=20))
 for (comp,season),rs in sorted(by.items()):
  rs.sort(key=lambda r:(r['_d'],r['home_team'],r['away_team']));teams=sorted({r['home_team'] for r in rs}|{r['away_team'] for r in rs});total=Counter()
  for r in rs:total[r['home_team']]+=1;total[r['away_team']]+=1
  st={t:S() for t in teams};days=defaultdict(list)
  for r in rs:days[r['_d']].append(r)
  for now in sorted(days):
   rank={t:i+1 for i,t in enumerate(sorted(teams,key=lambda t:(-st[t].p,-(st[t].gf-st[t].ga),-st[t].gf,t)))}
   for r in days[now]:
    h,w=r['home_team'],r['away_team'];x,y=st[h],st[w];key=(comp,season,*sorted((h,w)))
    feats[r['_id']]={'home_rest_days':'' if x.last is None else (now-x.last).days,'away_rest_days':'' if y.last is None else (now-y.last).days,'home_matches_7d':recent(x.dates,now,7),'away_matches_7d':recent(y.dates,now,7),'home_matches_14d':recent(x.dates,now,14),'away_matches_14d':recent(y.dates,now,14),'home_matches_28d':recent(x.dates,now,28),'away_matches_28d':recent(y.dates,now,28),'home_pre_points':x.p,'away_pre_points':y.p,'home_pre_rank':rank[h],'away_pre_rank':rank[w],'points_gap_h_minus_a':x.p-y.p,'rank_gap_h_minus_a':rank[h]-rank[w],'home_pre_gd':x.gf-x.ga,'away_pre_gd':y.gf-y.ga,'home_pre_ppg':'' if not x.m else x.p/x.m,'away_pre_ppg':'' if not y.m else y.p/y.m,'home_draw_rate_5':rate(x.draws,5),'away_draw_rate_5':rate(y.draws,5),'home_draw_rate_10':rate(x.draws,10),'away_draw_rate_10':rate(y.draws,10),'home_draw_rate_20':rate(x.draws,20),'away_draw_rate_20':rate(y.draws,20),'home_home_draw_rate_10':rate(x.home,10),'away_away_draw_rate_10':rate(y.away,10),'home_avg_total_goals_10':avg(x.totals,10),'away_avg_total_goals_10':avg(y.totals,10),'home_clean_sheet_rate_10':rate(x.cs,10),'away_clean_sheet_rate_10':rate(y.cs,10),'home_failed_to_score_rate_10':rate(x.fts,10),'away_failed_to_score_rate_10':rate(y.fts,10),'home_one_goal_margin_rate_10':rate(x.one,10),'away_one_goal_margin_rate_10':rate(y.one,10),'h2h_draw_rate_5':rate(h2h[key],5),'season_progress_home':x.m/total[h],'season_progress_away':y.m/total[w]}
   for r in days[now]:
    h,w=r['home_team'],r['away_team'];x,y=st[h],st[w];hg,ag=int(float(r['label_home_goals'])),int(float(r['label_away_goals']));z=r['label_result'];dr=int(z=='D');key=(comp,season,*sorted((h,w)));h2h[key].append(dr)
    for s,gf,ga,home in ((x,hg,ag,1),(y,ag,hg,0)):
     s.m+=1;s.p+=1 if z=='D' else (3 if (home and z=='H') or ((not home) and z=='A') else 0);s.gf+=gf;s.ga+=ga;s.last=now;s.dates.append(now);s.draws.append(dr);s.totals.append(hg+ag);s.cs.append(int(ga==0));s.fts.append(int(gf==0));s.one.append(int(abs(hg-ag)==1));(s.home if home else s.away).append(dr)
 a.out.mkdir(parents=True,exist_ok=True);op=a.out/'GOLD1000_R1_random_manifest_with_p0.csv';fields=list(sample[0])+F
 with op.open('w',encoding='utf-8',newline='') as f:
  wr=csv.DictWriter(f,fieldnames=fields);wr.writeheader()
  for r in sample:
   x=dict(r);x.update(feats[r['match_identity']]);wr.writerow(x)
 cp=a.out/'GOLD1000_R1_p0_coverage.csv'
 with cp.open('w',encoding='utf-8',newline='') as f:
  wr=csv.DictWriter(f,fieldnames=['field','present','missing','coverage']);wr.writeheader()
  for k in F:
   n=sum(feats[r['match_identity']][k] not in ('',None) for r in sample);wr.writerow({'field':k,'present':n,'missing':len(sample)-n,'coverage':f'{n/len(sample):.6f}'})
 rec={'schema_version':'GOLD1000-P0-R1','same_date_updates_deferred':True,'current_match_labels_used':False,'rows':len(sample),'feature_count':len(F),'outputs':{op.name:hfile(op),cp.name:hfile(cp)}};rp=a.out/'GOLD1000_R1_p0_receipt.json';rp.write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','rows':len(sample),'features':len(F),'receipt_sha256':hfile(rp)}))
if __name__=='__main__':main()
