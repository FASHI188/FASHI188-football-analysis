#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

SOURCES={
 'argentina-liga-profesional':'https://footiqo.com/database/leagues/argentina-liga-profesional/',
 'brazil-serie-a':'https://footiqo.com/database/leagues/brazil-serie-a/',
 'australia-a-league':'https://footiqo.com/database/leagues/australia-a-league/',
 'saudi-professional-league':'https://footiqo.com/database/leagues/saudi-professional-league/',
 'usa-mls':'https://footiqo.com/database/leagues/usa-mls/',
}
REQ=['id','matchDate','Country','League','Season','homeTeam','awayTeam','O25','U25','O35','U35','O45','U45']
MARKER=b'Historical Odds'


def stream_post_marker(url:str)->tuple[bytes,int]:
    r=requests.get(url,stream=True,timeout=45,headers={'User-Agent':'Mozilla/5.0 C079A research audit'})
    r.raise_for_status(); buf=b''; out=bytearray(); found=False
    for chunk in r.iter_content(65536):
        if not chunk: continue
        if not found:
            buf=(buf+chunk)[-262144:]
            i=buf.find(MARKER)
            if i>=0:
                found=True; out.extend(buf[i:]); buf=b''
        else:
            out.extend(chunk)
    if not found: raise RuntimeError(f'Historical Odds marker not found: {url}')
    return bytes(out), int(r.status_code)


def extract_tables(html:bytes, domain:str):
    soup=BeautifulSoup(html,'lxml'); rows=[]; table_count=0
    for t in soup.find_all('table'):
        trs=t.find_all('tr')
        if not trs: continue
        headers=[x.get_text(' ',strip=True) for x in trs[0].find_all(['th','td'])]
        if not all(c in headers for c in REQ): continue
        table_count+=1; pos={h:i for i,h in enumerate(headers)}
        for tr in trs[1:]:
            vals=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
            if len(vals)<len(headers): continue
            rec={c:vals[pos[c]] for c in REQ}
            if not rec['id'] or not rec['matchDate'] or not rec['homeTeam'] or not rec['awayTeam']: continue
            rec['domain']=domain
            rec['identity_key']=f"{domain}|{rec['id']}|{rec['matchDate']}|{rec['homeTeam']}|{rec['awayTeam']}"
            rows.append(rec)
    return rows,table_count


def devig(o,u):
    io=1.0/o; iu=1.0/u; return io/(io+iu)

def ids_sha(keys):
    return hashlib.sha256(('\n'.join(sorted(keys))+'\n').encode()).hexdigest()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    allrows=[]; reports={}; http_ok=True
    for dom,url in SOURCES.items():
        try:
            frag,status=stream_post_marker(url); rows,nt=extract_tables(frag,dom)
            reports[dom]={'url':url,'http_status':status,'odds_tables_found':nt,'rows_extracted':len(rows)}
            http_ok &= status==200 and nt>=1
            allrows.extend(rows)
        except Exception as e:
            reports[dom]={'url':url,'error':repr(e),'rows_extracted':0,'odds_tables_found':0}; http_ok=False
    d=pd.DataFrame(allrows)
    if d.empty:
        summary={'schema_version':'C079A_MULTILINE_ZERO_LABEL_SOURCE_V1','status':'STOP_MULTILINE_SOURCE','reports':reports,'gate':{'all_five_http_and_odds_table':False},'label_boundary':{'result_score_fields_materialized':0,'goal_totals_computed':False,'tail_membership_computed':False,'model_fit':False},'formal_weight':0}
        (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
    dup=int(d.identity_key.duplicated().sum()); unique=d.drop_duplicates('identity_key',keep='first').copy()
    num=pd.DataFrame({c:pd.to_numeric(unique[c],errors='coerce') for c in ['O25','U25','O35','U35','O45','U45']},index=unique.index)
    valid=num.notna().all(axis=1)&(num>1.0).all(axis=1)
    joint_n=int(valid.sum()); joint_frac=joint_n/len(unique)
    if joint_n:
        ix=valid[valid].index; p25=devig(num.loc[ix,'O25'].to_numpy(float),num.loc[ix,'U25'].to_numpy(float)); p35=devig(num.loc[ix,'O35'].to_numpy(float),num.loc[ix,'U35'].to_numpy(float)); p45=devig(num.loc[ix,'O45'].to_numpy(float),num.loc[ix,'U45'].to_numpy(float))
        coherent=(p25+1e-9>=p35)&(p35+1e-9>=p45); coherence=float(coherent.mean())
        unique.loc[ix,'pO25_devig']=p25; unique.loc[ix,'pO35_devig']=p35; unique.loc[ix,'pO45_devig']=p45
    else: coherence=0.0
    domain_counts=unique.groupby('domain').size().to_dict(); domains_ge300=sum(int(v>=300) for v in domain_counts.values())
    seasons=sorted(set(unique.Season.astype(str).str.strip())-{'','nan'})
    gate={
      'all_five_http_and_odds_table':bool(http_ok and len(reports)==5 and all(v.get('odds_tables_found',0)>=1 for v in reports.values())),
      'unique_odds_identities_ge_3000':len(unique)>=3000,
      'domains_ge300_ge_4':domains_ge300>=4,
      'duplicate_identity_count_zero':dup==0,
      'joint_six_market_coverage_ge_0_85':joint_frac>=0.85,
      'nested_devig_coherence_ge_0_98':coherence>=0.98,
      'distinct_seasons_ge_5':len(seasons)>=5,
      'result_score_fields_materialized_zero':True,
      'goal_totals_tail_model_zero':True,
    }
    status='PASS_MULTILINE_ZERO_LABEL_SOURCE' if all(gate.values()) else 'STOP_MULTILINE_SOURCE'
    keep=['identity_key','domain','id','matchDate','Country','League','Season','homeTeam','awayTeam','O25','U25','O35','U35','O45','U45','pO25_devig','pO35_devig','pO45_devig']
    for c in keep:
        if c not in unique: unique[c]=np.nan
    unique[keep].to_csv(out/'multiline_market_snapshot.csv',index=False)
    summary={
      'schema_version':'C079A_MULTILINE_ZERO_LABEL_SOURCE_V1','status':status,'source':'Footiqo closing odds sourced from 1xBet','domains':list(SOURCES),'identity_count':int(len(unique)),'identity_sha256':ids_sha(unique.identity_key.astype(str).tolist()),'duplicate_identity_count':dup,'domain_counts':{k:int(v) for k,v in domain_counts.items()},'joint_market_valid_count':joint_n,'joint_market_valid_fraction':float(joint_frac),'nested_devig_coherence_rate':float(coherence),'distinct_seasons':seasons,'reports':reports,'gate':gate,
      'label_boundary':{'stream_discarded_pre_odds_section':True,'parsed_tables_required_odds_columns_only':True,'result_score_fields_materialized':0,'FTHG_FTAG_numeric_access':False,'goal_totals_computed':False,'tail_membership_computed':False,'model_fit':False},
      'hard_boundaries':{'C078D_late2119_opened':False,'C077B_labels_read':False,'C071_reserve52180_opened':False,'C070F1597_opened':False,'A05_or_protected_opened':False,'formal_weight':0,'CURRENT_change':False,'unified_matrix_generated':False},
    }
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
