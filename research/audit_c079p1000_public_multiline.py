#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

INDEX='https://footiqo.com/database/leagues/'
BASE='https://footiqo.com'
MARKER=b'Historical Odds'
REQ=['id','matchDate','Country','League','Season','homeTeam','awayTeam','O25','U25','O35','U35','O45','U45']
PRICE=['O25','U25','O35','U35','O45','U45']
UA='Mozilla/5.0 C079-P1000 public-market research audit'


def sha_text(s:str)->str:
    return hashlib.sha256(s.encode()).hexdigest()

def discover_urls():
    r=requests.get(INDEX,timeout=45,headers={'User-Agent':UA}); r.raise_for_status()
    soup=BeautifulSoup(r.content,'lxml'); urls=set()
    for a in soup.find_all('a',href=True):
        u=urljoin(INDEX,a['href']).split('#')[0].split('?')[0]
        p=urlparse(u)
        if p.netloc not in {'footiqo.com','www.footiqo.com'}: continue
        if re.fullmatch(r'/database/leagues/[^/]+/',p.path): urls.add('https://footiqo.com'+p.path)
    return sorted(urls),int(r.status_code)


def stream_post_marker(url:str):
    r=requests.get(url,stream=True,timeout=60,headers={'User-Agent':UA}); r.raise_for_status()
    buf=b''; out=bytearray(); found=False
    for chunk in r.iter_content(65536):
        if not chunk: continue
        if not found:
            buf=(buf+chunk)[-524288:]
            i=buf.find(MARKER)
            if i>=0:
                found=True; out.extend(buf[i:]); buf=b''
        else: out.extend(chunk)
    if not found: return [],0,int(r.status_code),'marker_missing'
    soup=BeautifulSoup(bytes(out),'lxml'); rows=[]; nt=0
    slug=urlparse(url).path.rstrip('/').split('/')[-1]
    for t in soup.find_all('table'):
        trs=t.find_all('tr')
        if not trs: continue
        hdr=[x.get_text(' ',strip=True) for x in trs[0].find_all(['th','td'])]
        if not all(c in hdr for c in REQ): continue
        nt+=1; pos={h:i for i,h in enumerate(hdr)}
        for tr in trs[1:]:
            vals=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
            if len(vals)<len(hdr): continue
            rec={c:vals[pos[c]] for c in REQ}
            if not rec['id'] or not rec['matchDate'] or not rec['homeTeam'] or not rec['awayTeam']: continue
            rec['domain']=slug
            rec['identity_key']=f"{slug}|{rec['id']}|{rec['matchDate']}|{rec['homeTeam']}|{rec['awayTeam']}"
            rows.append(rec)
    return rows,nt,int(r.status_code),None


def devig(o,u):
    io=1.0/o; iu=1.0/u
    return io/(io+iu)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); ap.add_argument('--workers',type=int,default=8); a=ap.parse_args()
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    try: urls,index_status=discover_urls()
    except Exception as e:
        urls=[]; index_status=0; index_error=repr(e)
    else: index_error=None
    reports={}; allrows=[]
    with ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex:
        fut={ex.submit(stream_post_marker,u):u for u in urls}
        for f in as_completed(fut):
            u=fut[f]; slug=urlparse(u).path.rstrip('/').split('/')[-1]
            try:
                rows,nt,status,err=f.result(); allrows.extend(rows)
                reports[slug]={'url':u,'http_status':status,'odds_tables_found':nt,'rows_extracted':len(rows),'error':err}
            except Exception as e:
                reports[slug]={'url':u,'http_status':None,'odds_tables_found':0,'rows_extracted':0,'error':repr(e)}
    d=pd.DataFrame(allrows)
    base_summary={'schema_version':'C079P1000_PUBLIC_MULTILINE_V1','index_url':INDEX,'index_http_status':index_status,'index_error':index_error,'league_urls_discovered':len(urls),'reports':reports,'formal_gate_3000_unchanged':True,'formal_weight':0,'label_boundary':{'result_score_fields_materialized':0,'FTHG_FTAG_FTR_access':False,'goal_totals_computed':False,'tail_membership_computed':False,'model_fit':False},'hard_boundaries':{'C078D_late2119_opened':False,'C076D_opened':False,'C071_reserve52180_opened':False,'C070F1597_opened':False,'A05_or_protected_opened':False,'CURRENT_change':False,'unified_matrix_generated':False}}
    if d.empty:
        base_summary.update({'status':'STOP_PILOT1000_SOURCE','identity_count_raw':0,'complete_valid_count':0,'coherent_valid_count':0,'pilot1000_count':0,'gate':{}})
        (out/'summary.json').write_text(json.dumps(base_summary,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(base_summary,ensure_ascii=False,indent=2)); return 0
    d=d.sort_values(['identity_key']+PRICE).reset_index(drop=True)
    # Conflicts are duplicate identities with more than one distinct six-price tuple.
    conflict=0
    for _,g in d.groupby('identity_key',sort=False):
        if len(g)>1 and len(g[PRICE].astype(str).drop_duplicates())>1: conflict+=1
    duplicate_rows=int(d.duplicated('identity_key').sum())
    unique=d.drop_duplicates('identity_key',keep='first').copy()
    num=pd.DataFrame({c:pd.to_numeric(unique[c],errors='coerce') for c in PRICE},index=unique.index)
    valid=num.notna().all(axis=1)&(num>1.0).all(axis=1)
    valid_ix=valid[valid].index
    coherent=pd.Series(False,index=unique.index)
    if len(valid_ix):
        p25=devig(num.loc[valid_ix,'O25'].to_numpy(float),num.loc[valid_ix,'U25'].to_numpy(float))
        p35=devig(num.loc[valid_ix,'O35'].to_numpy(float),num.loc[valid_ix,'U35'].to_numpy(float))
        p45=devig(num.loc[valid_ix,'O45'].to_numpy(float),num.loc[valid_ix,'U45'].to_numpy(float))
        coh=(p25+1e-9>=p35)&(p35+1e-9>=p45)
        coherent.loc[valid_ix]=coh
        unique.loc[valid_ix,'pO25_devig']=p25; unique.loc[valid_ix,'pO35_devig']=p35; unique.loc[valid_ix,'pO45_devig']=p45
    complete_n=int(valid.sum()); coherence_rate=float(coherent.loc[valid_ix].mean()) if len(valid_ix) else 0.0
    eligible=unique.loc[valid].copy()
    eligible['selection_hash']=eligible.identity_key.astype(str).map(sha_text)
    eligible=eligible.sort_values(['selection_hash','identity_key'])
    pilot=eligible.head(1000).copy() if len(eligible)>=1000 else eligible.iloc[0:0].copy()
    pages_with_odds=sum(int(v.get('odds_tables_found',0)>=1) for v in reports.values())
    gate={
      'index_http_200':index_status==200,
      'league_urls_discovered_ge_20':len(urls)>=20,
      'pages_with_required_odds_ge_15':pages_with_odds>=15,
      'complete_valid_unique_ge_1000':complete_n>=1000,
      'nested_devig_coherence_ge_0_98':coherence_rate>=0.98,
      'conflicting_duplicate_identities_zero':conflict==0,
      'result_score_fields_materialized_zero':True,
      'target_model_computation_zero':True,
    }
    status='PASS_PILOT1000_SOURCE' if all(gate.values()) and len(pilot)==1000 else 'STOP_PILOT1000_SOURCE'
    keep=['identity_key','selection_hash','domain','id','matchDate','Country','League','Season','homeTeam','awayTeam']+PRICE+['pO25_devig','pO35_devig','pO45_devig']
    for c in keep:
        if c not in pilot: pilot[c]=np.nan
    if len(pilot): pilot[keep].to_csv(out/'pilot1000_market_only.csv',index=False)
    # Full market-only inventory is retained only for audit; still contains no results.
    snapkeep=['identity_key','domain','id','matchDate','Country','League','Season','homeTeam','awayTeam']+PRICE+['pO25_devig','pO35_devig','pO45_devig']
    for c in snapkeep:
        if c not in unique: unique[c]=np.nan
    unique[snapkeep].to_csv(out/'public_multiline_inventory.csv',index=False)
    summary={**base_summary,'status':status,'identity_count_raw':int(len(d)),'identity_count_unique':int(len(unique)),'duplicate_rows_same_identity':duplicate_rows,'conflicting_duplicate_identities':int(conflict),'complete_valid_count':complete_n,'complete_valid_fraction':float(complete_n/len(unique)),'nested_devig_coherence_rate':coherence_rate,'pages_with_required_odds':pages_with_odds,'pilot1000_count':int(len(pilot)),'pilot1000_identity_sha256':hashlib.sha256(('\n'.join(sorted(pilot.identity_key.astype(str).tolist()))+'\n').encode()).hexdigest() if len(pilot) else None,'domains_in_pilot1000':int(pilot.domain.nunique()) if len(pilot) else 0,'seasons_in_pilot1000':sorted(set(pilot.Season.astype(str)) - {'','nan'}) if len(pilot) else [],'gate':gate}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
