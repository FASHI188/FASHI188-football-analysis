#!/usr/bin/env python3
"""V6.1.4 ledger-native probability-quality audit for the pristine forward test.

Scores only immutable pre-kickoff predictions with RESULT_SETTLED events that survive the latest
V6.1.3 invalidation audit. It evaluates the frozen formal, direct and pooled 1X2 probability vectors
with Top-1 accuracy, Log Score, multiclass Brier Score, RPS, calibration diagnostics and paired score
differences. The stored V6.0.1 decision pick is reported separately from probability-vector argmax.

This is descriptive/proper-score evidence only. It cannot promote a model, change a weight, rewrite a
prediction, or override the V6.1.0 minimum forward-sample gates.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_pristine_forward_evaluate_v611_r2 as baseeval
from platform_core import (
    PlatformError,
    atomic_write_json,
    load_json,
    log_score,
    multiclass_brier,
    ranked_probability_score,
    validate_probability_vector,
)

FREEZE = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"
LEDGER = ROOT / "forward" / "v6_pristine_forward_events_v612.json"
AUDIT = ROOT / "manifests" / "v6_pristine_forward_audit_v613_status.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_probability_audit_v614_status.json"
SCHEMA = "V6.1.4-ledger-native-probability-audit-r1"
CLASSES = ("home", "draw", "away")
MODELS = ("formal", "direct", "pooled")
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 614
Z90 = 1.6448536269514722


def wilson_lower(hits: int, count: int) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z2 = Z90 * Z90
    denominator = 1.0 + z2 / count
    center = p + z2 / (2.0 * count)
    spread = Z90 * math.sqrt((p * (1.0 - p) + z2 / (4.0 * count)) / count)
    return (center - spread) / denominator


def quantile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    index = int(round(q * (len(sorted_values) - 1)))
    return float(sorted_values[max(0, min(len(sorted_values) - 1, index))])


def paired_bootstrap(rows: list[dict[str, Any]], left: str, right: str, metric: str) -> dict[str, Any] | None:
    if not rows:
        return None
    deltas = [float(row["scores"][left][metric]) - float(row["scores"][right][metric]) for row in rows]
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    samples=[]
    for _ in range(BOOTSTRAP_REPS):
        samples.append(sum(deltas[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    return {
        "definition": f"mean_{left}_minus_{right}; negative_favors_{left}",
        "point_estimate": sum(deltas) / n,
        "bootstrap_repetitions": BOOTSTRAP_REPS,
        "seed": BOOTSTRAP_SEED,
        "ci90": [quantile(samples, 0.05), quantile(samples, 0.95)],
        "ci95": [quantile(samples, 0.025), quantile(samples, 0.975)],
        "probability_left_better": sum(1 for value in samples if value < 0.0) / len(samples),
    }


def top1_calibration(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    bins=[{"count":0,"confidence_sum":0.0,"hit_sum":0} for _ in range(10)]
    for row in rows:
        probs=row["probabilities"][model]
        pick=max(CLASSES,key=lambda key: float(probs[key]))
        conf=float(probs[pick]); hit=int(pick==row["truth"])
        index=min(9,max(0,int(conf*10.0)))
        bins[index]["count"]+=1; bins[index]["confidence_sum"]+=conf; bins[index]["hit_sum"]+=hit
    rendered=[]; ece=0.0; n=len(rows)
    for index,raw in enumerate(bins):
        count=int(raw["count"])
        if count==0:
            continue
        mean_conf=float(raw["confidence_sum"])/count; accuracy=int(raw["hit_sum"])/count
        ece += (count/n)*abs(accuracy-mean_conf) if n else 0.0
        rendered.append({"bin":[index/10.0,(index+1)/10.0],"count":count,"mean_confidence":mean_conf,"accuracy":accuracy,"gap":accuracy-mean_conf})
    return {"ece_top1_equal_width_10":ece if n else None,"bins":rendered}


def model_summary(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    n=len(rows)
    hits=0; log_total=0.0; brier_total=0.0; rps_total=0.0; actual_prob_total=0.0
    class_prob=Counter(); actual_counts=Counter(); by_comp:dict[str,list[dict[str,Any]]]=defaultdict(list)
    for row in rows:
        probs=row["probabilities"][model]; truth=row["truth"]
        pick=max(CLASSES,key=lambda key: float(probs[key])); hits+=int(pick==truth)
        scores=row["scores"][model]
        log_total+=float(scores["log_score"]); brier_total+=float(scores["brier"]); rps_total+=float(scores["rps"]); actual_prob_total+=float(probs[truth])
        for key in CLASSES: class_prob[key]+=float(probs[key])
        actual_counts[truth]+=1; by_comp[row["competition_id"]].append(row)
    by_comp_out={}
    for cid,subset in sorted(by_comp.items()):
        chits=0; clog=cbrier=crps=0.0
        for row in subset:
            probs=row["probabilities"][model]; truth=row["truth"]; chits+=int(max(CLASSES,key=lambda key:float(probs[key]))==truth)
            clog+=float(row["scores"][model]["log_score"]); cbrier+=float(row["scores"][model]["brier"]); crps+=float(row["scores"][model]["rps"])
        by_comp_out[cid]={"count":len(subset),"top1_hits":chits,"top1_accuracy":chits/len(subset),"mean_log_score":clog/len(subset),"mean_brier":cbrier/len(subset),"mean_rps":crps/len(subset)}
    return {
        "count":n,
        "top1_hits":hits,
        "top1_accuracy":hits/n if n else None,
        "top1_wilson90_lower":wilson_lower(hits,n),
        "mean_actual_class_probability":actual_prob_total/n if n else None,
        "mean_log_score":log_total/n if n else None,
        "mean_brier":brier_total/n if n else None,
        "mean_rps":rps_total/n if n else None,
        "class_calibration":{
            key:{"mean_predicted_probability":class_prob[key]/n if n else None,"empirical_rate":actual_counts[key]/n if n else None,"gap":(class_prob[key]-actual_counts[key])/n if n else None,"actual_count":actual_counts[key]}
            for key in CLASSES
        },
        "top1_calibration":top1_calibration(rows,model),
        "by_competition":by_comp_out,
    }


def materialize() -> tuple[dict[str,Any],dict[str,Any],list[dict[str,Any]],list[str],int]:
    freeze=load_json(FREEZE)
    if freeze.get("status")!="PASS": raise PlatformError("V6.1.0 freeze receipt must be PASS")
    ledger=load_json(LEDGER) if LEDGER.exists() else {"schema_version":baseeval.ledgerlib.LEDGER_SCHEMA,"events":[]}
    audit=load_json(AUDIT)
    if audit.get("schema_version")!="V6.1.3-forward-audit-status-r1" or str(audit.get("status") or "").startswith("FAIL_"):
        raise PlatformError(f"V6.1.3 audit unavailable or failed: {audit.get('status')}")
    chain=baseeval.ledgerlib._audit_chain(ledger); source_integrity=baseeval.ledgerlib._source_integrity(freeze)
    base_rows,errors,open_predictions=baseeval._materialize(freeze,ledger)
    if chain.get("status")!="PASS" or source_integrity.get("status")!="PASS" or errors:
        raise PlatformError(f"ledger/source semantic integrity failed: chain={chain.get('status')} source={source_integrity.get('status')} errors={errors[:5]}")
    invalidated={str(value) for value in audit.get("invalidated_match_ids") or []}
    valid_base=[row for row in base_rows if str(row.get("match_id")) not in invalidated]
    prediction_events={str(event.get("match_id")):event for event in ledger.get("events") or [] if isinstance(event,dict) and event.get("event_type")=="PREDICTION_FROZEN"}
    rows=[]
    for base_row in valid_base:
        match_id=str(base_row["match_id"]); event=prediction_events[match_id]; prediction=((event.get("payload") or {}).get("prediction") or {})
        vectors={
            "formal":validate_probability_vector(prediction.get("formal_probabilities") or {},CLASSES,field=f"{match_id}.formal"),
            "direct":validate_probability_vector(prediction.get("direct_probabilities") or {},CLASSES,field=f"{match_id}.direct"),
            "pooled":validate_probability_vector(prediction.get("pooled_probabilities") or {},CLASSES,field=f"{match_id}.pooled"),
        }
        truth=str(base_row["truth"]); actual_index=CLASSES.index(truth); scores={}
        for model,probs in vectors.items():
            scores[model]={"log_score":log_score(float(probs[truth])),"brier":multiclass_brier(probs,truth),"rps":ranked_probability_score([float(probs[key]) for key in CLASSES],actual_index)}
        rows.append({"match_id":match_id,"competition_id":base_row["competition_id"],"kickoff_at":base_row["kickoff_at"],"home_team":base_row["home_team"],"away_team":base_row["away_team"],"truth":truth,"stored_pick":base_row["pick"],"stored_pick_hit":int(base_row["hit"]),"probabilities":vectors,"scores":scores})
    return freeze,audit,rows,errors,open_predictions


def main() -> int:
    generated=datetime.now(timezone.utc).replace(microsecond=0)
    try:
        freeze,audit,rows,semantic_errors,open_predictions=materialize()
    except Exception as exc:
        payload={"schema_version":SCHEMA,"generated_at_utc":generated.isoformat(),"status":"FAIL_INTEGRITY_GATE","error":f"{type(exc).__name__}: {exc}","governance":{"automatic_promotion":False,"formal_weight_change":False,"current_rule_change":False}}
        OUT.parent.mkdir(parents=True,exist_ok=True); atomic_write_json(OUT,payload); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 1
    n=len(rows); stored_hits=sum(int(row["stored_pick_hit"]) for row in rows); required=int((freeze.get("forward_evaluation_gates") or {}).get("minimum_completed_forward_matches") or 0)
    models={model:model_summary(rows,model) for model in MODELS}
    paired={}
    for left,right in (("pooled","formal"),("pooled","direct"),("direct","formal")):
        key=f"{left}_vs_{right}"; paired[key]={metric:paired_bootstrap(rows,left,right,metric) for metric in ("log_score","brier","rps")}
        if n:
            left_hits=sum(int(max(CLASSES,key=lambda k:float(row["probabilities"][left][k]))==row["truth"]) for row in rows)
            right_hits=sum(int(max(CLASSES,key=lambda k:float(row["probabilities"][right][k]))==row["truth"]) for row in rows)
            paired[key]["top1_accuracy_difference"]=(left_hits-right_hits)/n
    competition_counts=Counter(str(row["competition_id"]) for row in rows)
    payload={
        "schema_version":SCHEMA,
        "generated_at_utc":generated.isoformat(),
        "status":"PASS",
        "evaluation_status":"DESCRIPTIVE_ONLY_BELOW_FROZEN_FORWARD_GATE" if n<required else "SAMPLE_COUNT_GATE_REACHED_PROPER_SCORE_REVIEW_REQUIRED",
        "settled_valid_count":n,
        "open_prediction_count":open_predictions,
        "invalidated_settled_count":len(audit.get("invalidated_match_ids") or []),
        "v613_audit_status":audit.get("status"),
        "v613_audit_generated_at_utc":audit.get("generated_at_utc"),
        "frozen_minimum_completed_forward_matches":required,
        "sample_progress":n/required if required else None,
        "competitions_represented":len(competition_counts),
        "competition_counts":dict(sorted(competition_counts.items())),
        "stored_decision_pick":{"count":n,"hits":stored_hits,"accuracy":stored_hits/n if n else None,"wilson90_lower":wilson_lower(stored_hits,n)},
        "probability_models":models,
        "paired_model_comparisons":paired,
        "score_orientation":{"log_score":"lower_is_better","brier":"lower_is_better","rps":"lower_is_better","top1_accuracy":"higher_is_better"},
        "semantic_errors":semantic_errors,
        "governance":{
            "ledger_native_pre_match_predictions_only":True,
            "v613_invalidated_samples_excluded":True,
            "postmatch_prediction_reconstruction":False,
            "all_three_frozen_probability_vectors_scored":True,
            "stored_decision_pick_reported_separately_from_argmax":True,
            "strictly_proper_scores_primary_for_probability_quality":True,
            "small_sample_does_not_authorize_promotion":True,
            "frozen_forward_gate_not_modified":True,
            "automatic_promotion":False,
            "formal_weight_change":False,
            "runtime_probability_change":False,
            "current_rule_change":False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); atomic_write_json(OUT,payload); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0


if __name__=="__main__":
    raise SystemExit(main())