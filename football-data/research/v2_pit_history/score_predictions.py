from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str,Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def outcome(hg,ag):
    return "home" if hg>ag else "draw" if hg==ag else "away"


def probs(m):
    return {"home":float(m["p_home"]),"draw":float(m["p_draw"]),"away":float(m["p_away"])}


def binary_calibration(pairs,bins=10):
    buckets=[[] for _ in range(bins)]
    for p,y in pairs:
        i=min(bins-1,max(0,int(float(p)*bins)))
        buckets[i].append((float(p),int(y)))
    out=[]; ece=0.0; n=max(1,len(pairs))
    for i,b in enumerate(buckets):
        if not b:
            out.append({"bin":i,"n":0,"mean_p":None,"actual_rate":None}); continue
        mp=sum(p for p,_ in b)/len(b); ar=sum(y for _,y in b)/len(b)
        ece += len(b)/n*abs(mp-ar)
        out.append({"bin":i,"n":len(b),"mean_p":mp,"actual_rate":ar})
    return {"ece":ece,"bins":out}


def auc(pairs):
    pos=[p for p,y in pairs if y]; neg=[p for p,y in pairs if not y]
    if not pos or not neg: return None
    wins=ties=0
    for a in pos:
        for b in neg:
            if a>b: wins+=1
            elif a==b: ties+=1
    return (wins+0.5*ties)/(len(pos)*len(neg))


def f1_counts(tp,fp,fn):
    prec=tp/(tp+fp) if tp+fp else 0.0
    rec=tp/(tp+fn) if tp+fn else 0.0
    f1=2*prec*rec/(prec+rec) if prec+rec else 0.0
    return {"precision":prec,"recall":rec,"f1":f1,"tp":tp,"fp":fp,"fn":fn}


def standard(rows, model):
    if not rows: return {"n":0}
    ll=br=rps=score_ll=0.0; correct=0
    draw_pairs=[]; score_pairs={"0-0":[],"1-1":[],"2-2":[]}
    draw_tp=draw_fp=draw_fn=0
    for pred,lab in rows:
        p=probs(pred[model]); hg,ag=int(lab["home_goals"]),int(lab["away_goals"]); y=outcome(hg,ag)
        ll += -math.log(max(1e-15,p[y]))
        yh,yd,ya=(1 if y=="home" else 0),(1 if y=="draw" else 0),(1 if y=="away" else 0)
        br += (p["home"]-yh)**2+(p["draw"]-yd)**2+(p["away"]-ya)**2
        rps += 0.5*((p["home"]-yh)**2 + ((p["home"]+p["draw"])-(yh+yd))**2)
        top=max(("home","draw","away"),key=lambda k:p[k])
        correct += int(top==y)
        pd=int(top=="draw"); yd2=int(y=="draw")
        draw_tp += int(pd and yd2); draw_fp += int(pd and not yd2); draw_fn += int((not pd) and yd2)
        draw_pairs.append((p["draw"],yd2))
        matrix=pred[model]["score_matrix"]
        actual_p=1e-15
        if 0<=hg<len(matrix) and 0<=ag<len(matrix[hg]):
            actual_p=max(1e-15,float(matrix[hg][ag]))
        score_ll += -math.log(actual_p)
        for name,(sh,sa) in {"0-0":(0,0),"1-1":(1,1),"2-2":(2,2)}.items():
            sp=float(matrix[sh][sa]) if sh<len(matrix) and sa<len(matrix[sh]) else 0.0
            score_pairs[name].append((sp,int(hg==sh and ag==sa)))
    n=len(rows)
    out={
        "n":n,"logloss":ll/n,"brier":br/n,"rps":rps/n,"top1":correct/n,
        "exact_score_logloss":score_ll/n,
        "draw":{"brier":sum((p-y)**2 for p,y in draw_pairs)/n,**f1_counts(draw_tp,draw_fp,draw_fn),
                "calibration":binary_calibration(draw_pairs)},
        "exact_scores":{},
    }
    for name,pairs in score_pairs.items():
        out["exact_scores"][name]={
            "brier":sum((p-y)**2 for p,y in pairs)/n,
            "mean_probability":sum(p for p,_ in pairs)/n,
            "actual_rate":sum(y for _,y in pairs)/n,
            "calibration":binary_calibration(pairs),
        }
    return out


def underdog(rows,model,threshold=75.0):
    pairs=[]; tp=fp=fn=0; n=0; wins=0
    for pred,lab in rows:
        he=float(pred["shared_home_elo"]); ae=float(pred["shared_away_elo"])
        if he <= ae-threshold: side="home"
        elif ae <= he-threshold: side="away"
        else: continue
        n+=1; p=probs(pred[model]); pu=p[side]
        y=outcome(int(lab["home_goals"]),int(lab["away_goals"])); actual=int(y==side); wins+=actual
        pairs.append((pu,actual))
        top=max(("home","draw","away"),key=lambda k:p[k]); called=int(top==side)
        tp += int(called and actual); fp += int(called and not actual); fn += int((not called) and actual)
    return {
        "definition":f"shared past-only Elo gap >= {threshold:.0f}; lower-rated side is underdog",
        "n":n,"actual_wins":wins,"actual_win_rate":wins/n if n else None,
        "mean_predicted_underdog_win_probability":sum(p for p,_ in pairs)/n if n else None,
        "brier":sum((p-y)**2 for p,y in pairs)/n if n else None,
        "auroc":auc(pairs),"top1_underdog_win":f1_counts(tp,fp,fn) if n else None,
        "calibration":binary_calibration(pairs) if n else None,
    }


def grouped_metrics(rows,model,key):
    groups=defaultdict(list)
    for pred,lab in rows: groups[str(pred[key])].append((pred,lab))
    return {k:standard(v,model) for k,v in sorted(groups.items())}


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--predictions",required=True)
    ap.add_argument("--label-vault",required=True)
    ap.add_argument("--pre-score-manifest",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()
    pp=Path(args.predictions); lp=Path(args.label_vault); mp=Path(args.pre_score_manifest); out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    pre=json.loads(mp.read_text(encoding="utf-8"))
    if pre["scorer_invoked"] is not False or pre["status"]!="PREDICTIONS_FROZEN_READY_FOR_INDEPENDENT_SCORER":
        raise RuntimeError("pre-score freeze manifest invalid")
    if sha256_file(pp)!=pre["prediction_sha256"]:
        raise RuntimeError("prediction SHA mismatch before scorer")
    if sha256_file(lp)!=pre["evaluation_label_vault_sha256"]:
        raise RuntimeError("label vault SHA mismatch")
    predictions=read_jsonl(pp); labels=read_jsonl(lp)
    pm={x["fixture_id"]:x for x in predictions}; lm={x["fixture_id"]:x for x in labels}
    if len(pm)!=len(predictions) or len(lm)!=len(labels) or set(pm)!=set(lm):
        raise RuntimeError("prediction/label identity mismatch")
    ordered=[(p,lm[p["fixture_id"]]) for p in predictions]
    for p,l in ordered:
        if p["cutoff"]!=l["cutoff"]: raise RuntimeError("cutoff mismatch")
    models=("v1","v2_joint","v2_joint_off")
    metrics={}
    for model in models:
        metrics[model]=standard(ordered,model)
        metrics[model]["underdog_win"]=underdog(ordered,model)
        metrics[model]["groups"]={
            "league":grouped_metrics(ordered,model,"competition_id"),
            "season":grouped_metrics(ordered,model,"season"),
            "cold_start":grouped_metrics(ordered,model,"shared_cold_start_bucket"),
            "lineup_completeness":{
                "status":"DATA_UNAVAILABLE",
                "n":len(ordered),
                "note":"No reliable pre-kickoff timestamped lineup completeness field in phase-1 source."
            },
        }
    j=metrics["v2_joint"]; off=metrics["v2_joint_off"]; v1=metrics["v1"]
    delta=lambda a,b,k: a[k]-b[k]
    ablation={
        "joint_score_core":{
            "status":"TESTED",
            "comparison":"v2_joint - v2_joint_off using identical V2 state, fixtures and input availability",
            "delta_logloss":delta(j,off,"logloss"),"delta_brier":delta(j,off,"brier"),
            "delta_rps":delta(j,off,"rps"),"delta_top1":delta(j,off,"top1"),
            "delta_exact_score_logloss":delta(j,off,"exact_score_logloss"),
            "improves_logloss":j["logloss"]<off["logloss"],
            "improves_exact_score_logloss":j["exact_score_logloss"]<off["exact_score_logloss"],
        },
        "player":{"status":"DATA_UNAVAILABLE"},
        "starting_lineup":{"status":"DATA_UNAVAILABLE"},
        "substitutes":{"status":"DATA_UNAVAILABLE"},
        "coach":{"status":"DATA_UNAVAILABLE"},
        "match_process":{"status":"DATA_UNAVAILABLE"},
    }
    comparison={
        "v2_joint_minus_v1":{
            "delta_logloss":j["logloss"]-v1["logloss"],"delta_brier":j["brier"]-v1["brier"],
            "delta_rps":j["rps"]-v1["rps"],"delta_top1":j["top1"]-v1["top1"],
            "delta_exact_score_logloss":j["exact_score_logloss"]-v1["exact_score_logloss"],
        }
    }
    result={
        "schema_version":"football3-v2-expanded-history-pit-score-v1",
        "research_only":True,"strict_prospective":False,"formal_promotion_eligible":False,
        "n":len(ordered),"prediction_sha256":sha256_file(pp),"label_vault_sha256":sha256_file(lp),
        "scorer_sha256":sha256_file(Path(__file__)),"metrics":metrics,
        "ablation":ablation,"comparison":comparison,
        "interpretation_guard":"Historical research/development evidence only; not a virgin prospective test and not production evidence.",
    }
    metrics_path=out/"metrics.json"
    metrics_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    receipt={
        "schema_version":"football3-v2-expanded-history-pit-score-receipt-v1",
        "prediction_sha256":sha256_file(pp),"label_vault_sha256":sha256_file(lp),
        "pre_score_manifest_sha256":sha256_file(mp),"scorer_sha256":result["scorer_sha256"],
        "metrics_sha256":sha256_file(metrics_path),"scorer_invoked_after_prediction_freeze":True,
        "identity_sets_equal":True,"n":len(ordered),"status":"INDEPENDENT_SCORER_PASSED",
    }
    (out/"score_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "status":receipt["status"],"n":len(ordered),
        "v1":{k:v1[k] for k in ("logloss","brier","rps","top1","exact_score_logloss")},
        "v2_joint":{k:j[k] for k in ("logloss","brier","rps","top1","exact_score_logloss")},
        "joint_ablation":ablation["joint_score_core"],
    },indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
