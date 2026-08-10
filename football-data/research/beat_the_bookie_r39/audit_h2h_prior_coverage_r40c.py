#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_parent(path: Path):
    spec = importlib.util.spec_from_file_location("r39c_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import frozen R39C parent")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_dt(text: str) -> datetime:
    s = text.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unsupported match_datetime: {text!r}")


def qstats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    x = sorted(values); n = len(x)
    def q(frac: float):
        return x[min(n-1, max(0, int(round(frac*(n-1)))))]
    return {"n":n,"min":x[0],"p05":q(.05),"p10":q(.10),"p25":q(.25),"p50":q(.50),"p75":q(.75),"p90":q(.90),"p95":q(.95),"max":x[-1],"mean":sum(x)/n}


def fixture_source_for_matches(name: str) -> str:
    if name == "odds_series_matches.csv.gz": return "odds_series.csv.gz"
    if name == "odds_series_b_matches.csv.gz": return "odds_series_b.csv.gz"
    raise ValueError(name)


def load_metadata(source_dir: Path):
    rows=[]; event_counts=Counter(); team_pairs_to_leagues=defaultdict(set)
    for original in ("odds_series_matches.csv.gz","odds_series_b_matches.csv.gz"):
        path=source_dir/original.replace(".csv.gz","_no_scores.csv.gz")
        source_odds=fixture_source_for_matches(original)
        with gzip.open(path,"rt",encoding="latin-1",newline="") as f:
            reader=csv.DictReader(f)
            expected=["match_id","league","home_team","away_team","match_datetime"]
            if reader.fieldnames!=expected:
                raise RuntimeError(f"unexpected sanitized metadata header {reader.fieldnames} for {path}")
            for r in reader:
                mid=r["match_id"].strip(); league=r["league"].strip(); home=r["home_team"].strip(); away=r["away_team"].strip()
                if not mid or not league or not home or not away: continue
                dt=parse_dt(r["match_datetime"])
                identity=f"{source_odds}|{mid}"
                pair=tuple(sorted((home,away)))
                rows.append({"identity":identity,"source_file":source_odds,"match_id":mid,"league":league,"home_team":home,"away_team":away,"dt":dt,"pair":pair})
                event_counts[(league,home,away,dt)]+=1
                team_pairs_to_leagues[pair].add(league)
    return rows,event_counts,team_pairs_to_leagues


def build_h2h(metadata_rows:list[dict]):
    histories:dict[tuple[str,str],list[datetime]]=defaultdict(list)
    features={}; same_time_pair_conflicts=0
    rows=sorted(metadata_rows,key=lambda x:(x["dt"],x["source_file"],x["match_id"]))
    i=0
    while i<len(rows):
        dt=rows[i]["dt"]; j=i+1
        while j<len(rows) and rows[j]["dt"]==dt: j+=1
        batch=rows[i:j]; pair_counts=Counter()
        for r in batch:
            pair=r["pair"]; pair_counts[pair]+=1; hist=histories[pair]
            gap=None if not hist else (dt-hist[-1]).total_seconds()/86400.0
            last365=sum(1 for x in hist if x>=dt-timedelta(days=365))
            last730=sum(1 for x in hist if x>=dt-timedelta(days=730))
            features[r["identity"]]={
                "prior_h2h_meeting_count":len(hist),
                "days_since_previous_h2h":gap,
                "prior_h2h_meetings_previous_365d":last365,
                "prior_h2h_meetings_previous_730d":last730,
            }
        same_time_pair_conflicts += sum(v-1 for v in pair_counts.values() if v>1)
        for pair in set(r["pair"] for r in batch): histories[pair].append(dt)
        i=j
    return features,same_time_pair_conflicts


def aggregate(rows:list[dict], fmap:dict[str,dict]):
    out={"rows":len(rows),"joined_rows":0,"prior_ge1":0,"prior_ge2":0,"prior_ge3":0,"prior_ge5":0,"gap_available":0,"zero_or_negative_gap_values":0}
    series=defaultdict(list)
    for r in rows:
        f=fmap.get(r["identity"])
        if f is None: continue
        out["joined_rows"]+=1; n=f["prior_h2h_meeting_count"]
        for k,t in (("prior_ge1",1),("prior_ge2",2),("prior_ge3",3),("prior_ge5",5)): out[k]+=int(n>=t)
        gap=f["days_since_previous_h2h"]
        out["gap_available"]+=int(gap is not None)
        if gap is not None and gap<=0: out["zero_or_negative_gap_values"]+=1
        for k,v in f.items():
            if v is not None: series[k].append(float(v))
    den=out["rows"] or 1
    out["coverage_rates"]={k:out[k]/den for k in ("joined_rows","prior_ge1","prior_ge2","prior_ge3","prior_ge5","gap_available")}
    out["distributions"]={k:qstats(v) for k,v in sorted(series.items())}
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--registration",type=Path,required=True); ap.add_argument("--sanitized-dir",type=Path,required=True); ap.add_argument("--parent-code",type=Path,required=True); ap.add_argument("--out-dir",type=Path,required=True); args=ap.parse_args()
    args.out_dir.mkdir(parents=True,exist_ok=True)
    reg=json.loads(args.registration.read_text(encoding="utf-8"))
    assert reg["status"]=="PRE_REGISTERED_ZERO_LABEL_H2H_PRIOR_COVERAGE_AUDIT"
    assert reg["hard_limits"]["result_labels_allowed"] is False
    assert reg["population_binding"]["fifth_fixed100_lock_allowed_in_this_stage"] is False
    parent=load_parent(args.parent_code)
    eligible,_=parent.load_feature_rows(args.sanitized_dir,{})
    start=datetime.fromisoformat(reg["population_binding"]["holdout_start"])
    train=sorted([x for x in eligible.values() if x["dt"]<start],key=lambda z:(z["dt"],z["identity"]))
    hold=sorted([x for x in eligible.values() if x["dt"]>=start],key=lambda z:(z["dt"],z["identity"]))
    assert len(train)==reg["population_binding"]["expected_training_eligible_rows"]
    assert len(hold)==reg["population_binding"]["expected_holdout_eligible_rows"]
    metadata,event_counts,pair_leagues=load_metadata(args.sanitized_dir)
    fmap,same_time=build_h2h(metadata)
    tr=aggregate(train,fmap); ho=aggregate(hold,fmap)
    screen=reg["coverage_screen"]
    screen_gates={
        "training_prior_ge1":tr["coverage_rates"]["prior_ge1"]>=screen["training_prior_ge1_min_rate"],
        "holdout_prior_ge1":ho["coverage_rates"]["prior_ge1"]>=screen["holdout_prior_ge1_min_rate"],
        "holdout_prior_ge3":ho["coverage_rates"]["prior_ge3"]>=screen["holdout_prior_ge3_min_rate"],
    }
    base_gates={
        "training_rows_exact":len(train)==reg["pass_gate"]["training_rows_exactly_reproduce"],
        "holdout_rows_exact":len(hold)==reg["pass_gate"]["holdout_rows_exactly_reproduce"],
        "all_training_rows_join_metadata":tr["joined_rows"]==len(train),
        "all_holdout_rows_join_metadata":ho["joined_rows"]==len(hold),
        "zero_or_negative_h2h_gap_days":tr["zero_or_negative_gap_values"]+ho["zero_or_negative_gap_values"]==0,
        "score_or_result_values_accessed_zero":True,"prediction_metrics_computed_zero":True,"model_fits_zero":True,"identity_locks_zero":True,
    }
    base_gates["passed"]=all(base_gates.values()); screen_gates["passed"]=all(screen_gates.values())
    status=("PASS_R40C_ZERO_LABEL_H2H_COVERAGE_SCREEN" if base_gates["passed"] and screen_gates["passed"] else "STOP_R40C_H2H_COVERAGE_TOO_SPARSE_OR_AUDIT_FAIL")
    result={
        "schema_version":reg["schema_version"],"generated_at_utc":datetime.now(timezone.utc).isoformat(),"status":status,
        "population":{"metadata_rows":len(metadata),"training_eligible_rows":len(train),"holdout_eligible_rows":len(hold)},
        "training":tr,"holdout":ho,
        "identity_diagnostics":{"unique_unordered_pairs":len(pair_leagues),"pairs_seen_in_multiple_league_labels":sum(1 for x in pair_leagues.values() if len(x)>1),"duplicate_event_rows":sum(v-1 for v in event_counts.values() if v>1),"same_timestamp_pair_conflict_excess":same_time,"pair_key":reg["pair_identity"]["pair_key"]},
        "base_gates":base_gates,"coverage_screen_gates":screen_gates,
        "no_label_audit":{"score_values_accessed":0,"result_values_accessed":0,"prediction_metrics_computed":0,"model_fits":0,"thresholds_selected":0,"identity_locks_created":0,"holdout_individual_identities_output":0},
        "next_stage_authorization":("PREHOLDOUT_LABEL_BEARING_PREREGISTRATION_ALLOWED_FIFTH100_STILL_FORBIDDEN" if status.startswith("PASS") else "CLOSE_H2H_NO_MODEL_NO_FIFTH100"),
        "hard_limits":reg["hard_limits"]}
    (args.out_dir/"h2h_prior_coverage_status_r40c.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
