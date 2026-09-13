#!/usr/bin/env python3
import importlib.util, json
from pathlib import Path
H=Path(__file__).resolve().parent
S=importlib.util.spec_from_file_location("v",H/"validate_v3_schedule_importance_zero_label_v1.py")
v=importlib.util.module_from_spec(S); S.loader.exec_module(v)
C=json.loads((H/"v3_schedule_importance_zero_label_contract_v1.json").read_text())

def ent(path,sha="a"*40,size=50000): return {"path":path,"type":"blob","sha":sha,"size":size}

def synthetic():
    xs=[ent("LICENSE.md",C["source"]["license_blob_sha"],6555),ent("README.md",C["source"]["readme_blob_sha"],6330)]
    for season in C["completed_only_rules"]["allowed_seasons"]:
        for name,need in C["completed_only_rules"]["league_files"].items():
            source_need=need+(1 if name=="en.1.json" else 0)
            if season in C["completed_only_rules"]["allowed_seasons"][-source_need:]:
                p=f"{season}/{name}"
                xs.append(ent(p,sha=("b"*40 if p==C["contamination_guard"]["schema_probe_excluded_path"] else "a"*40)))
    xs.append(ent("2026-27/en.1.json",sha="c"*40))
    return {"sha":C["source"]["tree_sha"],"truncated":False,"tree":xs}

def t_pass():
    r=v.audit_tree(C,synthetic()); assert r["decision"]=="PASS_ZERO_LABEL_TREE_COVERAGE_NEXT_SANITIZED_SCHEMA_AUDIT"; assert r["selected_file_count"]==94 and r["contaminated_probe_path_excluded"] is True
def t_zero_guards():
    r=v.base(C); assert r["match_payloads_downloaded"]==r["target_match_rows_read"]==r["target_result_or_goal_values_read"]==r["selected_cohort_labels_opened"]==0; assert r["future_matches_allowed"] is False and not r["training"] and not r["tuning"]
def t_future_never_selected():
    r=v.audit_tree(C,synthetic()); assert r["selected_file_count"]==94 and "2026-27" not in (r["first_selected_path"] or "")+(r["last_selected_path"] or "")
def t_contaminated_probe_required():
    x=synthetic(); x["tree"]=[e for e in x["tree"] if e["path"]!=C["contamination_guard"]["schema_probe_excluded_path"]]; r=v.audit_tree(C,x); assert r["contaminated_probe_path_excluded"] is False
def t_license_drift_stop():
    x=synthetic()
    for e in x["tree"]:
        if e["path"]=="LICENSE.md": e["sha"]="d"*40
    try: v.audit_tree(C,x); assert False
    except v.Stop as e: assert str(e)=="STOP_SOURCE_PROVENANCE_BLOB_DRIFT"
def t_tree_truncated_stop():
    x=synthetic(); x["truncated"]=True
    try: v.audit_tree(C,x); assert False
    except v.Stop as e: assert str(e)=="STOP_TREE_TRUNCATED"
def t_coverage_stop():
    x=synthetic(); x["tree"]=[e for e in x["tree"] if e["path"]!="2024-25/pt.1.json"]
    try: v.audit_tree(C,x); assert False
    except v.Stop as e: assert str(e)=="STOP_LEAGUE_COVERAGE"
def t_exact_tree_url_only():
    c=json.loads(json.dumps(C)); c["source"]["tree_api_url"]="https://raw.githubusercontent.com/openfootball/football.json/master/2023-24/en.1.json"
    try: v.fetch_tree(c); assert False
    except v.Stop as e: assert str(e)=="STOP_TREE_URL_NOT_EXACT"
def t_importance_deferred():
    assert C["feature_contract"]["standings_pressure"].startswith("DEFERRED"); assert C["feature_contract"]["travel_pressure"]=="NOT_IN_THIS_BATCH"; assert C["feature_contract"]["rotation_pressure"]=="NOT_IN_THIS_BATCH"
T=[t_pass,t_zero_guards,t_future_never_selected,t_contaminated_probe_required,t_license_drift_stop,t_tree_truncated_stop,t_coverage_stop,t_exact_tree_url_only,t_importance_deferred]
if __name__=="__main__":
    for f in T: f(); print("PASS",f.__name__)
    print(f"{len(T)}/{len(T)} PASS")
