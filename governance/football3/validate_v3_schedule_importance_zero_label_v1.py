#!/usr/bin/env python3
import argparse, hashlib, json, re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

C = Path(__file__).with_name("v3_schedule_importance_zero_label_contract_v1.json")
SHA = re.compile(r"^[0-9a-f]{40}$")
SEASON = re.compile(r"^\d{4}-\d{2}$")

class Stop(RuntimeError): pass

def base(c):
    return {
      "schema_version":"football3-v3-schedule-importance-zero-label-receipt-v1",
      "phase":"ZERO_LABEL_LICENSE_PIT_IDENTITY_COVERAGE_AUDIT",
      "target_population":"COMPLETED_MATCHES_ONLY",
      "source_commit_sha":c["source"]["commit_sha"],
      "source_tree_sha":c["source"]["tree_sha"],
      "license_marker":c["source"]["license_marker"],
      "tree_metadata_downloaded":False,
      "match_payloads_downloaded":0,
      "target_match_rows_read":0,
      "target_result_or_goal_values_read":0,
      "selected_cohort_labels_opened":0,
      "future_matches_allowed":False,
      "existing_frozen_future_receipts_used":False,
      "stage6_1335_queue_used":False,
      "stage6_touched":False,
      "training":False,
      "tuning":False,
      "formal_weight":0,
      "matrix_delta":0,
      "data_ready":False,
      "contaminated_probe_path_excluded":False,
      "selected_file_count":0,
      "league_file_counts":{},
      "inventory_sha256":None,
    }

def fetch_tree(c):
    u=c["source"]["tree_api_url"]
    exact=f'https://api.github.com/repos/openfootball/football.json/git/trees/{c["source"]["tree_sha"]}?recursive=1'
    if u != exact: raise Stop("STOP_TREE_URL_NOT_EXACT")
    try:
        with urlopen(Request(u,headers={"User-Agent":"Football3-schedule-importance-zero-label/1.0","Accept":"application/vnd.github+json"}),timeout=90) as r:
            raw=r.read(5_000_001)
    except HTTPError as e: raise Stop(f"STOP_SOURCE_TREE_HTTP_{e.code}") from e
    except URLError as e: raise Stop("STOP_SOURCE_TREE_NETWORK") from e
    if len(raw)>5_000_000: raise Stop("STOP_SOURCE_TREE_TOO_LARGE")
    try: x=json.loads(raw)
    except Exception: raise Stop("STOP_SOURCE_TREE_PARSE")
    return x

def audit_tree(c,x):
    r=base(c)
    if x.get("sha") != c["source"]["tree_sha"]: raise Stop("STOP_TREE_SHA_MISMATCH")
    if x.get("truncated") is not False: raise Stop("STOP_TREE_TRUNCATED")
    tree=x.get("tree")
    if not isinstance(tree,list): raise Stop("STOP_TREE_SCHEMA")
    r["tree_metadata_downloaded"]=True
    blobs={}
    for e in tree:
        if not isinstance(e,dict) or e.get("type")!="blob": continue
        p=e.get("path"); h=e.get("sha"); n=e.get("size")
        if not isinstance(p,str) or not SHA.fullmatch(str(h)) or not isinstance(n,int) or n<0: raise Stop("STOP_TREE_BLOB_SCHEMA")
        blobs[p]=(h,n)
    for path,key in ((c["source"]["license_path"],"license_blob_sha"),(c["source"]["readme_path"],"readme_blob_sha")):
        if path not in blobs: raise Stop("STOP_SOURCE_PROVENANCE_MISSING")
        if blobs[path][0] != c["source"][key]: raise Stop("STOP_SOURCE_PROVENANCE_BLOB_DRIFT")
    rules=c["completed_only_rules"]; allowed=set(rules["allowed_seasons"])
    if any(not SEASON.fullmatch(s) for s in allowed): raise Stop("STOP_SEASON_CONTRACT")
    leagues=rules["league_files"]
    excluded=c["contamination_guard"]["schema_probe_excluded_path"]
    selected=[]; counts={k:0 for k in leagues}
    for p,(h,n) in blobs.items():
        parts=p.split("/")
        if len(parts)!=2: continue
        season,name=parts
        if season not in allowed or name not in leagues: continue
        if p==excluded: continue
        if any(p.startswith(pref) for pref in rules["future_season_prefixes"]): raise Stop("STOP_FUTURE_SEASON_SELECTED")
        if n < rules["minimum_blob_size"]: raise Stop("STOP_SELECTED_BLOB_TOO_SMALL")
        selected.append((p,h,n)); counts[name]+=1
    if excluded in {p for p,_,_ in selected}: raise Stop("STOP_CONTAMINATED_PROBE_SELECTED")
    r["contaminated_probe_path_excluded"]=excluded in blobs and excluded not in {p for p,_,_ in selected}
    if counts != leagues: raise Stop("STOP_LEAGUE_COVERAGE")
    if len(selected) != rules["expected_selected_file_count"]: raise Stop("STOP_SELECTED_FILE_COUNT")
    selected.sort()
    r["selected_file_count"]=len(selected)
    r["league_file_counts"]=counts
    r["inventory_sha256"]=hashlib.sha256("\n".join(f"{p}|{h}|{n}" for p,h,n in selected).encode()).hexdigest()
    r["first_selected_path"]=selected[0][0] if selected else None
    r["last_selected_path"]=selected[-1][0] if selected else None
    r["data_ready"]=True
    r["decision"]="PASS_ZERO_LABEL_TREE_COVERAGE_NEXT_SANITIZED_SCHEMA_AUDIT"
    return r

def audit(c):
    r=base(c)
    if c.get("status")!="DESIGN_LOCKED": r["decision"]="STOP_CONTRACT_NOT_LOCKED"; return r
    if c["completed_only_rules"].get("match_payload_downloads_allowed") is not False: r["decision"]="STOP_PAYLOAD_POLICY"; return r
    try: return audit_tree(c,fetch_tree(c))
    except Stop as e: r["decision"]=str(e); return r

def main():
    a=argparse.ArgumentParser(); a.add_argument("--output",required=True); z=a.parse_args()
    c=json.loads(C.read_text()); r=audit(c)
    Path(z.output).write_text(json.dumps(r,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"decision":r["decision"],"selected_file_count":r["selected_file_count"]},sort_keys=True))
if __name__=="__main__": main()
