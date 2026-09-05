from __future__ import annotations

import argparse, hashlib, json, pathlib, urllib.request
import pyarrow.parquet as pq

FILES = ["fixtures.parquet", "match_stats.parquet", "teams.parquet"]
BASE = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/"
IDENTITY_CANDIDATES = {
    "fixtures.parquet": ["fixture_id","date","kickoff","known_at","league_id","season","home_team_id","away_team_id","home_team","away_team","home_team_name","away_team_name"],
    "match_stats.parquet": ["fixture_id","known_at","home_yellow_cards","away_yellow_cards","home_red_cards","away_red_cards","home_fouls","away_fouls","home_penalties","away_penalties"],
    "teams.parquet": ["team_id","id","team_name","name","country","league_id"]
}

def sha256(path: pathlib.Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20), b''): h.update(b)
    return h.hexdigest()

def download(url: str, path: pathlib.Path):
    req=urllib.request.Request(url,headers={"User-Agent":"football3-discipline-source-schema-audit/1.0"})
    with urllib.request.urlopen(req,timeout=300) as r, path.open('wb') as f:
        headers={k.lower():v for k,v in r.headers.items()}
        final_url=r.geturl()
        while True:
            b=r.read(1<<20)
            if not b: break
            f.write(b)
    return final_url, headers

def scalar(v):
    if hasattr(v,'as_py'): return v.as_py()
    return v

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--contract',type=pathlib.Path,required=True); ap.add_argument('--out',type=pathlib.Path,required=True)
    a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    c=json.loads(a.contract.read_text()); assert c['status']=='FROZEN_ZERO_LABEL_SOURCE_AUDIT'
    rec={"schema_version":"football3-discipline-source-schema-audit-result-v1","status":"DISCIPLINE_SOURCE_SCHEMA_AUDIT_PASS","research_only":True,"model_fits":0,"candidate_probabilities":0,"outcome_labels_read":False,"historical_confirmation_2023_labels_opened":False,"prospective_1335_data_touched":False,"files":{}}
    for name in FILES:
        p=a.out/name
        final_url,headers=download(BASE+name+"?download=true",p)
        pf=pq.ParquetFile(p)
        names=pf.schema_arrow.names
        cols=[x for x in IDENTITY_CANDIDATES[name] if x in names]
        sample=[]
        if cols:
            table=pf.read(columns=cols).slice(0,5)
            for row in table.to_pylist(): sample.append({k:scalar(v) for k,v in row.items()})
        rec['files'][name]={
            "sha256":sha256(p),"size_bytes":p.stat().st_size,"row_count":pf.metadata.num_rows,
            "schema":[{"name":f.name,"type":str(f.type)} for f in pf.schema_arrow],
            "identity_columns_present":cols,"identity_sample":sample,"final_url":final_url,
            "headers":{k:headers.get(k) for k in c['audit']['capture_response_headers']}
        }
        p.unlink()
    fx=set(rec['files']['fixtures.parquet']['identity_columns_present']); ms=set(rec['files']['match_stats.parquet']['identity_columns_present']); tm=set(rec['files']['teams.parquet']['identity_columns_present'])
    direct='fixture_id' in fx and 'fixture_id' in ms
    has_date=bool(fx & {'date','kickoff'})
    has_names=(('home_team' in fx and 'away_team' in fx) or ('home_team_name' in fx and 'away_team_name' in fx))
    has_ids=('home_team_id' in fx and 'away_team_id' in fx and bool(tm & {'team_id','id'}) and bool(tm & {'team_name','name'}))
    rec['join_feasibility']={"match_stats_to_fixtures_direct_fixture_id":direct,"fixtures_has_date_or_kickoff":has_date,"fixtures_has_direct_team_names":has_names,"fixtures_team_ids_mappable_via_teams":has_ids,"mechanical_join_candidate":bool(direct and has_date and (has_names or has_ids))}
    if not rec['join_feasibility']['mechanical_join_candidate']: rec['status']='DISCIPLINE_SOURCE_SCHEMA_AUDIT_STOP_NO_MECHANICAL_IDENTITY_JOIN'
    (a.out/'discipline_source_schema_audit.json').write_text(json.dumps(rec,sort_keys=True,indent=2,default=str)+'\n')
    print(json.dumps(rec,sort_keys=True,default=str))
if __name__=='__main__': main()
