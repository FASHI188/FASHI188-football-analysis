#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, os, urllib.request
from collections import Counter, defaultdict
from pathlib import Path

COMMIT='b0bc9f22dd77c206ddedc1d742893b3bbe64baec'
RAW=f'https://raw.githubusercontent.com/hudl/open-data/{COMMIT}/data'
OUT=Path(os.environ.get('R44L6_OUT','r44l6_census_output')); OUT.mkdir(parents=True,exist_ok=True)
ALLOWED={'match_id','match_date','kick_off','competition','season','home_team','away_team'}

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'r44l6-zero-label-census/1.0'})
    with urllib.request.urlopen(req,timeout=180) as r:return r.read()

def sha(b):return hashlib.sha256(b).hexdigest()

def one(entry):
    cid=int(entry['competition_id']); sid=int(entry['season_id']); url=f'{RAW}/matches/{cid}/{sid}.json'
    try:b=fetch(url); src=json.loads(b.decode('utf-8'))
    except Exception as e:return {'competition_id':cid,'season_id':sid,'ok':False,'error':type(e).__name__}
    ids=[]; dates=[]; errs=[]
    for obj in src:
        r={k:obj.get(k) for k in ALLOWED}
        got_cid=int((r.get('competition') or {}).get('competition_id',-1)); got_sid=int((r.get('season') or {}).get('season_id',-1))
        if got_cid!=cid or got_sid!=sid: errs.append([r.get('match_id'),got_cid,got_sid])
        ids.append(int(r['match_id'])); dates.append(str(r.get('match_date') or ''))
    return {'competition_id':cid,'season_id':sid,'country_name':str(entry.get('country_name') or ''),'competition_name':str(entry.get('competition_name') or ''),'competition_gender':str(entry.get('competition_gender') or ''),'competition_international':bool(entry.get('competition_international')),'season_name':str(entry.get('season_name') or ''),'ok':True,'identity_count':len(ids),'unique_match_ids':len(set(ids)),'identity_error_count':len(errs),'first_match_date':min(dates) if dates else None,'last_match_date':max(dates) if dates else None,'sha256':sha(b),'bytes':len(b)}

def main():
    comp_bytes=fetch(f'{RAW}/competitions.json'); entries=json.loads(comp_bytes.decode('utf-8'))
    seen=set(); uniq=[]
    for e in entries:
        key=(int(e['competition_id']),int(e['season_id']))
        if key in seen: continue
        seen.add(key); uniq.append(e)
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for i,r in enumerate(ex.map(one,uniq),1):
            rows.append(r)
            if i%25==0: print(f'CENSUS {i}/{len(uniq)}',flush=True)
    rows.sort(key=lambda r:(int(r['competition_id']),str(r.get('season_name','')),int(r['season_id'])))
    bad=[r for r in rows if not r.get('ok') or r.get('identity_error_count',0)!=0 or r.get('identity_count')!=r.get('unique_match_ids')]
    by=defaultdict(list)
    for r in rows:
        if r.get('ok'): by[int(r['competition_id'])].append(r)
    summaries=[]
    for cid,rr in by.items():
        first=rr[0]; counts=[int(x['identity_count']) for x in rr]
        summaries.append({'competition_id':cid,'country_name':first['country_name'],'competition_name':first['competition_name'],'competition_gender':first['competition_gender'],'competition_international':first['competition_international'],'season_count':len(rr),'total_identity_count':sum(counts),'max_season_count':max(counts),'seasons_ge_50':sum(c>=50 for c in counts),'seasons_ge_100':sum(c>=100 for c in counts),'seasons_ge_200':sum(c>=200 for c in counts),'seasons_ge_300':sum(c>=300 for c in counts),'season_counts':[{'season_name':x['season_name'],'season_id':x['season_id'],'count':x['identity_count']} for x in sorted(rr,key=lambda z:(z.get('first_match_date') or '',z['season_id']))]})
    summaries.sort(key=lambda x:(x['total_identity_count'],x['max_season_count']),reverse=True)
    candidates=[x for x in summaries if x['total_identity_count']>=600 or x['max_season_count']>=300]
    result={'study_id':'r44l6_statsbomb_open_domain_census','source_commit':COMMIT,'competition_file_sha256':sha(comp_bytes),'competition_season_entries':len(uniq),'containers_ok':sum(bool(r.get('ok')) for r in rows),'integrity_failures':len(bad),'label_fields_accessed':0,'model_fits':0,'thresholds_selected':0,'formal_weight':0,'high_scale_candidates':candidates,'status':'PASS_R44L6_ZERO_LABEL_CENSUS' if not bad else 'STOP_R44L6_CENSUS_INTEGRITY'}
    (OUT/'census_result_r44l6.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'competition_season_counts_r44l6.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'competition_summaries_r44l6.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'entries':len(uniq),'integrity_failures':len(bad),'top_candidates':candidates[:12]},ensure_ascii=False))
    return 0 if not bad else 3
if __name__=='__main__': raise SystemExit(main())
