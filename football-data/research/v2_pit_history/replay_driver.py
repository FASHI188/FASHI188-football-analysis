from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import math
import os
import pickle
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_jsonl(path: Path) -> list[dict[str,Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def dt(text: str) -> datetime:
    x=datetime.fromisoformat(text.replace("Z","+00:00"))
    if x.tzinfo is None: raise RuntimeError("naive datetime")
    return x.astimezone(timezone.utc)


def grouped(rows):
    out=[]; cur=[]; key=None
    for r in sorted(rows,key=lambda x:(x["cutoff"],x["competition_id"],x["fixture_id"])):
        k=r["cutoff"]
        if key is None or k==key:
            cur.append(r); key=k
        else:
            out.append(cur); cur=[r]; key=k
    if cur: out.append(cur)
    return out


def import_engines(v1_dir: Path,v2_dir: Path):
    sys.path.insert(0,str(v1_dir)); v1=importlib.import_module("pure_engine")
    sys.path.insert(0,str(v2_dir)); sys.modules.pop("engine",None); v2=importlib.import_module("engine")
    return v1,v2


def v1_fixture(v1,r):
    return v1.Fixture(r["fixture_id"],r["competition_id"],r["season"],dt(r["cutoff"]),r["home_team_id"],r["away_team_id"])


def v2_fixture(v2,r):
    return v2.Fixture(
        fixture_id=r["fixture_id"],competition_id=r["competition_id"],season=r["season"],
        kickoff=dt(r["cutoff"]),home_team_id=r["home_team_id"],away_team_id=r["away_team_id"],
        round_index=r.get("round_index"),
    )


class SharedHistory:
    def __init__(self):
        self.elo=defaultdict(lambdm:1500.0)
        self.appear=defaultdict(int)
        self.k=20.0
        self.home_adv=60.0

    def snapshot(self,r):
        h,a=r["home_team_id"],r["away_team_id"]
        nh,na=self.appear[h],self.appear[a]
        bucket="zero" if min(nh,na)==0 else "sparse" if min(nh,na)<5 else "established"
        return {
            "shared_home_elo":self.elo[h],"shared_away_elo":self.elo[a],
            "shared_home_prior_appearances":nh,"shared_away_prior_appearances":na,
            "shared_cold_start_bucket":bucket,
        }

    def apply(self,r,hg,ag):
        h,a=r["home_team_id"],r["away_team_id"]
        rh,ra=self.elo[h],self.elo[a]
        expected=1.0/(1.0+10.0**(-((rh+self.home_adv%-ra)/400.0))
        actual=1.0 if hg>ag else 0.5 if hg==ag else 0.0
        delta=self.k*(actual-expected)
        self.elo[h]=rh+delta; self.elo[a]=ra-delta
        self.appear[h]+=1; self.appear[a]+=1


def apply_rows(v1,v2,s1,s2,history,batch,label_map):
    f1=[]; f2=[]; labels={}
    for r in batch:
        lab=label_map[r["fixture_id"]]
        hg,ag=int(lab["home_goals"]),int(lab["away_goals"])
        if dt(lab["cutoff"]) != dt(r["cutoff"]):
            raise RuntimeError("feature/label cutoff mismatch")
        f1.append(v1_fixture(v1,r)); f2.append(v2_fixture(v2,r)); labels[r["fixture_id"]]=(hg,ag)
    s1.apply_batch(f1,labels); s2.apply_batch(f2,labels)
    for r in batch:
        hg,ag=labels[r["fixture_id"]]: history.apply(r,hg,ag)


def initialize_from_development(v1,v2,s1,s2,history,development):
    label_map={r["fixture_id"]:r for r in development}
    for batch in grouped(development):
        apply_rows(v1,v2,s1,s2,history,batch,label_map)


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--development",required=True)
    ap.add_argument("--features",required=True)
    ap.add_argument("--label-vault",required=True)
    ap.add_argument("--dataset-manifest",required=True)
    ap.add_argument("--v1-lock",required=True)
    ap.add_argument("--v2-lock",required=True)
    ap.add_argument("--v1-dir",required=True)
    ap.add_argument("--v2-dir",required=True)
    ap.add_argument("--predictor",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()

    development_path=Path(args.development); features_path=Path(args.features); vault_path=Path(args.label_vault)
    manifest_path=Path(args.dataset_manifest); v1_lock_path=Path(args.v1_lock); v2_lock_path=Path(args.v2_lock)
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    for path,key in ((development_path,"development_sha256"),(features_path,"evaluation_features_sha256"),(vault_path,"evaluation_label_vault_sha256")):
        if sha256_file(path)!=manifest[key]: raise RuntimeError(f"{key} mismatch")

    development=read_jsonl(development_path)
    features=read_jsonl(features_path)
    labels=read_jsonl(vault_path)
    if len(features)!=manifest["evaluation_n"] or len(labels)!=manifest["evaluation_n"]:
        raise RuntimeError("evaluation count mismatch")
    label_map={x["fixture_id"]:x for x in labels}
    if set(label_map)!={x["fixture_id"] for x in features}:
        raise RuntimeError("evaluation identity mismatch")
    if len(label_map)!=len(labels):
        raise RuntimeError("duplicate evaluation label identity")
    for f in features:
        if {"home_goals","away_goals","result","score"}.intersection(f):
            raise RuntimeError("label field leaked into predictor-safe universe")

    v1,v2=import_engines(Path(args.v1_dir),Path(args.v2_dir))
    l1=json.loads(v1_lock_path.read_text(encoding="utf-8")); l2=json.loads(v2_lock_path.read_text(encoding="utf-8")
    if not l1.get("research_only") or not l2.get("research_only") or l2.get("formal_candidate"):
        raise RuntimeError("research lock governance mismatch")
    if l1["development_sha256"]!=manifest["development_sha256"] or l2["development_sha256"]!=manifest["development_sha256"]:
        raise RuntimeError("locks not bound to this development set")

    s1=v1.EngineState(params=v1.Parameters(**l1["parameters"]))
    s2=v2.EngineState(v2.Parameters(**l2["parameters"]))
    history=SharedHistory()
    initialize_from_development(v1,v2,s1,s2,history,development)

    batches=grouped(features)
    pending=[]  # ordered tuples: (available_at, feature_batch)
    receipts=[]
    predictions_path=out/"predictions.jsonl"
    if predictions_path.exists(): predictions_path.unlink()
    chain="0"*64
    released_n=0
    max_released_cutoff=None

    with tempfile.TemporaryDirectory(prefix="football3-pit-") as td:
        td=Path(td)
        for batch_no,batch in enumerate(batches,1):
            now=dt(batch[0]["cutoff"])
            # Only already-predicted batches can exist in pending. Result availability is monotonic in kickoff.
            while pending and pending[0][0] <= now:
                _, old_batch = pending.pop(0)
                if max(dt(x["cutoff"]) for x in old_batch) >= now:
                    raise RuntimeError("same/future cutoff label attempted before prediction")
                apply_rows(v1,v2,s1,s2,history,old_batch,label_map)
                released_n += len(old_batch)
                max_released_cutoff=max(dt(x["cutoff"]) for x in old_batch)

            safe_batch=[]
            for r in batch:
               x=dict(r); x.update(history.snapshot(r)); safe_batch.append(x)
            batch_features=td/"features.jsonl"
            batch_features.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in safe_batch),encoding="utf-8")
            state1=td/"v1_state.pkl"; state2=td/"v2_state.pkl"; batch_out=td/"batch_predictions.jsonl"
            state1.write_bytes(pickle.dumps(s1,protocol=4)); state2.write_bytes(pickle.dumps(s2,protocol=4))
            state1_sha=sha256_file(state1); state2_sha=sha256_file(state2); feat_sha=sha256_file(batch_features)
            cmd=[
                sys.executable,args.predictor,
                "--v1-state",str(state1),"--v2-state",str(state2),"--features",str(batch_features),
                "--v2-lock",str(v2_lock_path),"--v1-dir",args.v1_dir,"--v2-dir",args.v2_dir,"--out",str(batch_out)
            ]
            subprocess.run(cmd,check=True)
            if sha256_file(state1)!=state1_sha or sha256_file(state2)!=state2_sha:
                raise RuntimeError("state changed during predictor subprocess")
            batch_sha=sha256_file(batch_out)
            raw=batch_out.read_bytes()
            with predictions_path.open("ab") as pf: pf.write(raw)
            ids=[r["fixture_id"] for r in batch]
            chain=sha256_bytes((chain+"|"+batch_sha+"|"+",".join(ids)).encode("utf-8"))
            receipts.append({
                "batch_no":batch_no,"cutoff":batch[0]["cutoff"],"n":len(batch),
                "fixture_ids":ids,"features_sha256":feat_sha,"v1_state_sha256":state1_sha,
                "v2_state_sha256":state2_sha,"prediction_batch_sha256":batch_sha,
                "prediction_chain_sha256":chain,"labels_released_before_batch":released_n,
                "max_released_cutoff":None if max_released_cutoff is None else max_released_cutoff.isoformat(),
            })
            # Current labels become eligible only after this batch has been frozen.
            available_times={dt(label_map[r["fixture_id"]]["result_available_at"]) for r in batch}
            if len(available_times)!=1: raise RuntimeError("same-cutoff batch has inconsistent result availability")
            pending.append((next(iter(available_times)),batch))

    pred_sha=sha256_file(predictions_path)
    receipts_path=out/"batch_freeze_receipts.jsonl"
    receipts_path.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in receipts),encoding="utf-8")
    head=os.environ.get("GITHUB_SHA") or subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    run_manifest={
        "schema_version":"football3-v2-expanded-history-pit-run-v1",
        "research_only":True,"formal_candidate":False,"prospective_validation":False,
        "head":head,"branch":os.environ.get("GITHUB_REF_NAME"),
        "run_id":os.environ.get("GITHUB_RUN_ID"),"run_attempt":os.environ.get("GITHUB_RUN_ATTEMPT"),
        "dataset_manifest_sha256":sha256_file(manifest_path),
        "raw_source_set_sha256":manifest["raw_source_set_sha256"],
        "normalized_dataset_sha256":manifest["normalized_dataset_sha256"],
        "alias_map_sha256":manifest["alias_map_sha256"],
        "evaluation_identity_sha256":manifest["evaluation_identity_sha256"],
        "evaluation_features_sha256":manifest["evaluation_features_sha256"],
        "evaluation_label_vault_sha256":manifest["evaluation_label_vault_sha256"],
        "v1_lock_sha256":sha256_file(v1_lock_path),"v2_lock_sha256":sha256_file(v2_lock_path),
        "v1_engine_sha256":sha256_file(Path(args.v1_dir)/"pure_engine.py"),
        "v2_engine_sha256":sha256_file(Path(args.v2_dir)/"engine.py"),
        "predictor_sha256":sha256_file(Path(args.predictor)),
        "split_rule":"development=2022-23; evaluation=2023-24..2025-26; exact-cutoff batches; past result usable iff kickoff+3h<=current cutoff",
        "prediction_sha256":pred_sha,"prediction_chain_sha256":chain,
        "batch_receipts_sha256":sha256_file(receipts_path),
        "evaluation_n":len(features),"batch_n":len(batches),
        "predictor_label_access":False,
        "driver_label_access":"PAST_STATE_UPDATE_ONLY_AFTER_RESULT_AVAILABLE_AT",
        "scorer_invoked":False,
        "status":"PREDICTIONS_FROZEN_READY_FOR_INDEPENDENT_SCORER",
    }
    (out/"run_manifest_pre_score.json").write_text(json.dumps(run_manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "status":run_manifest["status"],"evaluation_n":len(features),"batch_n":len(batches),
        "prediction_sha256":pred_sha,"prediction_chain_sha256":chain,
    },indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
