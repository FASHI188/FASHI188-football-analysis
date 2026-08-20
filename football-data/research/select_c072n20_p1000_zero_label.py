#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, re
from pathlib import Path
import pandas as pd

EXPECTED_FULL_N = 14250
EXPECTED_OLD_N = 2000
EXPECTED_OLD_ORDERED_SHA = '65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559'
EXPECTED_C079_N = 1000
EXPECTED_C079_SHA = 'ce2af86f206077255ea489242a3e8473e34b89f140cc9528f2ad9594593c3413'
PRICE_COLS = ['O05','U05','O15','U15','O25','U25','O35','U35','O45','U45']
SLUG = {'BR':'brazil-serie-a','GR':'greece-super-league','MLS':'usa-mls','TR':'turkey-super-lig'}


def hlines(xs:list[str], sort:bool=False)->str:
    vals=sorted(xs) if sort else xs
    return hashlib.sha256(('\n'.join(vals)+'\n').encode()).hexdigest()


def find_one(root:Path,name:str)->Path:
    hits=list(root.rglob(name))
    if len(hits)!=1: raise RuntimeError(f'{name}: expected 1 hit, got {len(hits)}')
    return hits[0]


def valid_price(x)->bool:
    try: v=float(str(x).replace(',','.'))
    except Exception: return False
    return math.isfinite(v) and v>1.0


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--n16-dir',required=True); ap.add_argument('--c079-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    n16=Path(a.n16_dir); c079=Path(a.c079_dir); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    full=pd.read_csv(find_one(n16,'c072n16r1_footiqo_full_zero_label_inventory.csv'),dtype=str,keep_default_na=False)
    old=pd.read_csv(find_one(n16,'c072n16r1_footiqo_new2000_zero_label.csv'),dtype=str,keep_default_na=False)
    other=pd.read_csv(find_one(c079,'pilot1000_market_only.csv'),dtype=str,keep_default_na=False)

    old_order_sha=hlines(old['identity_sha256'].astype(str).tolist(),sort=False) if 'identity_sha256' in old else ''
    other_keys=other['identity_key'].astype(str).tolist()
    other_sha=hlines(other_keys,sort=True)

    summary={
      'schema':'C072N20_P1000_ZERO_LABEL_V1','classification':'POST_VIEW_DEVELOPMENT_REPLICATION_ZERO_LABEL',
      'full_rows':int(len(full)),'old_rows':int(len(old)),'old_ordered_identity_sha256':old_order_sha,
      'c079_rows':int(len(other)),'c079_identity_sha256':other_sha,
      'target_result_values_materialized':0,'model_fit':0,'model_score':0,
      'C070F1597_opened':False,'N17_reserve266_opened':False,'N18_confirmation150_opened':False,
      'C073_C077_scientific_results_used':False,'cross_project_use':'GLOBAL_CONSUMPTION_EXCLUSION_ONLY','formal_weight':0,
    }

    need=['identity_sha256','sourceCode','id','matchDate','Country','League','Season','homeTeam','awayTeam']+PRICE_COLS
    missing=[c for c in need if c not in full.columns]
    if missing: raise RuntimeError(f'missing full columns {missing}')
    old_ids=set(old['identity_sha256'].astype(str))
    other_ids=set(other_keys)

    d=full.loc[full['sourceCode'].isin(SLUG)].copy()
    core=['id','matchDate','Country','League','Season','homeTeam','awayTeam']
    d=d.loc[d[core].apply(lambda r:all(str(x).strip() for x in r),axis=1)].copy()
    complete=d[PRICE_COLS].applymap(valid_price).all(axis=1)
    d=d.loc[complete].copy()
    d['c079_identity_key']=d.apply(lambda r:f"{SLUG[r['sourceCode']]}|{r['id']}|{r['matchDate']}|{r['homeTeam']}|{r['awayTeam']}",axis=1)
    d['overlap_old']=d['identity_sha256'].isin(old_ids)
    d['overlap_c079']=d['c079_identity_key'].isin(other_ids)
    eligible=d.loc[~d['overlap_old'] & ~d['overlap_c079']].copy()
    eligible=eligible.sort_values(['identity_sha256','sourceCode','id']).reset_index(drop=True)
    sel=eligible.head(1000).copy()
    selected_sha=hlines(sel['identity_sha256'].astype(str).tolist(),sort=False) if len(sel) else None
    overlap_old=int(sel['identity_sha256'].isin(old_ids).sum()) if len(sel) else 0
    overlap_c079=int(sel['c079_identity_key'].isin(other_ids).sum()) if len(sel) else 0
    dup=int(sel['identity_sha256'].duplicated().sum()) if len(sel) else 0
    allfive=float(sel[PRICE_COLS].applymap(valid_price).all(axis=1).mean()) if len(sel) else 0.0
    summary.update({
      'complete_after_basic_gates':int(len(d)),'eligible_after_global_exclusions':int(len(eligible)),
      'selected_rows':int(len(sel)),'selected_ordered_identity_sha256':selected_sha,
      'selected_source_counts':sel['sourceCode'].value_counts().sort_index().to_dict() if len(sel) else {},
      'selected_seasons':sorted(sel['Season'].astype(str).unique().tolist()) if len(sel) else [],
      'selected_overlap_old2000':overlap_old,'selected_overlap_c079_consumed1000':overlap_c079,
      'selected_duplicate_identity_rows':dup,'selected_allfive_ou_coverage':allfive,
    })
    gates={
      'full_inventory_exact_14250':len(full)==EXPECTED_FULL_N,
      'old_exact_2000':len(old)==EXPECTED_OLD_N,
      'old_ordered_sha_reproduced':old_order_sha==EXPECTED_OLD_ORDERED_SHA,
      'c079_exact_1000':len(other)==EXPECTED_C079_N,
      'c079_sha_reproduced':other_sha==EXPECTED_C079_SHA,
      'eligible_ge_1000':len(eligible)>=1000,
      'selected_exact_1000':len(sel)==1000,
      'old_overlap_zero':overlap_old==0,
      'c079_overlap_zero':overlap_c079==0,
      'selected_duplicates_zero':dup==0,
      'allfive_ou_coverage_100pct':allfive==1.0,
      'zero_target_model_access':True,
    }
    summary['gates']=gates
    summary['terminal']='C072N20_P1000_ZERO_LABEL_PASS' if all(gates.values()) else 'C072N20_P1000_ZERO_LABEL_STOP'
    keep=['identity_sha256','sourceCode','id','matchDate','Country','League','Season','homeTeam','awayTeam']+PRICE_COLS+['H','D','A','BTTSY','BTTSN','c079_identity_key']
    keep=[c for c in keep if c in sel.columns]
    sel[keep].to_csv(out/'c072n20_p1000_zero_label.csv',index=False)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
