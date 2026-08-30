from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import pickle
import sys
from pathlib import Path
from typing import Any

FORBIDDEN = {"home_goals","away_goals","result","score","ft_score","label","labels"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str,Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def import_engines(v1_dir: Path, v2_dir: Path):
    sys.path.insert(0,str(v1_dir))
    v1=importlib.import_module("pure_engine")
    sys.path.insert(0,str(v2_dir))
    sys.modules.pop("engine",None)
    v2=importlib.import_module("engine")
    return v1,v2


def dt(text: str):
    from datetime import datetime, timezone
    x=datetime.fromisoformat(text.replace("Z","+00:00"))
    if x.tzinfo is None: raise RuntimeError("naive cutoff")
    return x.astimezone(timezone.utc)


def nested_from_cells(cells: list[dict], max_goals: int=14) -> list[list[float]]:
    out=[[0.0]*(max_goals+1) for _ in range(max_goals+1)]
    for c in cells:
        h,a=int(c["home_goals"]),int(c["away_goals"])
        if h<=max_goals and a<=max_goals:
            out[h][a]=float(c["probability"])
    total=sum(map(sum,out))
    if total<=0: raise RuntimeError("empty v1 score matrix")
    return [[v/total for v in row] for row in out]


def one(matrix):
    h=d=a=0.0
    for i,row in enumerate(matrix):
        for j,p in enumerate(row):
            if i>j: h+=p
            elif i==j: d+=p
            else: a+=p
    s=h+d+a
    return {"home":h/s,"draw":d/s,"away":a/s}


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--v1-state",required=True)
    ap.add_argument("--v2-state",required=True)
    ap.add_argument("--features",required=True)
    ap.add_argument("--v2-lock",required=True)
    ap.add_argument("--v1-dir",required=True)
    ap.add_argument("--v2-dir",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()
    v1_state_path=Path(args.v1_state); v2_state_path=Path(args.v2_state); features_path=Path(args.features)
    before1=sha256_file(v1_state_path); before2=sha256_file(v2_state_path)
    rows=read_jsonl(features_path)
    if not rows: raise RuntimeError("empty prediction batch")
    if len({r["cutoff"] for r in rows})!=1: raise RuntimeError("prediction batch has multiple cutoffs")
    for r in rows:
        bad=FORBIDDEN.intersection({str(k).casefold() for k in r})
        if bad: raise RuntimeError(f"label-like input reached predictor: {sorted(bad)}")
    v1,v2=import_engines(Path(args.v1_dir),Path(args.v2_dir))
    with v1_state_path.open("rb") as f: s1=pickle.load(f)
    with v2_state_path.open("rb") as f: s2=pickle.load(f)
    lock=json.loads(Path(args.v2_lock).read_text(encoding="utf-8"))
    if not lock.get("research_only") or lock.get("formal_candidate"):
        raise RuntimeError("V2 PIT lock governance mismatch")
    preds=[]
    for r in rows:
        f1=v1.Fixture(r["fixture_id"],r["competition_id"],r["season"],dt(r["cutoff"]),r["home_team_id"],r["away_team_id"])
        p1=s1.predict(f1)
        m1=nested_from_cells(p1["score_matrix"],14)

        f2=v2.Fixture(
            fixture_id=r["fixture_id"],competition_id=r["competition_id"],season=r["season"],
            kickoff=dt(r["cutoff"]),home_team_id=r["home_team_id"],away_team_id=r["away_team_id"],
            round_index=r.get("round_index"),
        )
        feat=s2.predict_features(f2)
        mj=v2.joint_matrix(
            lock["joint_family"],feat,
            dispersion_home=float(lock["dispersion_home"]),
            dispersion_away=float(lock["dispersion_away"]),
            dependence=float(lock["dependence"]),max_goals=int(lock["max_goals"]),
        )
        moff=v2.joint_matrix("INDEPENDENT_POISSON_FROZEN",feat,dependence=0.0,max_goals=int(lock["max_goals"]))
        pj=v2.matrix_1x2(mj); poff=v2.matrix_1x2(moff)
        preds.append({
            "fixture_id":r["fixture_id"],"competition_id":r["competition_id"],"season":r["season"],
            "cutoff":r["cutoff"],"home_team_id":r["home_team_id"],"away_team_id":r["away_team_id"],
            "round_index":r.get("round_index"),
            "shared_cold_start_bucket":r["shared_cold_start_bucket"],
            "shared_home_prior_appearances":r["shared_home_prior_appearances"],
            "shared_away_prior_appearances":r["shared_away_prior_appearances"],
            "shared_home_elo":r["shared_home_elo"],"shared_away_elo":r["shared_away_elo"],
            "lineup_completeness":"DATA_UNAVAILABLE",
            "v1":{
                "p_home":float(p1["p_home"]),"p_draw":float(p1["p_draw"]),"p_away":float(p1["p_away"]),
                "score_matrix":m1,"cold_start_bucket":p1["cold_start_bucket"],
            },
            "v2_joint":{
                "p_home":float(pj["home"]),"p_draw":float(pj["draw"]),"p_away":float(pj["away"]),
                "score_matrix":mj,"cold_start_bucket":feat["cold_start_bucket"],
                "joint_family":lock["joint_family"],
            },
            "v2_joint_off":{
                "p_home":float(poff["home"]),"p_draw":float(poff["draw"]),"p_away":float(poff["away"]),
                "score_matrix":moff,"cold_start_bucket":feat["cold_start_bucket"],
                "joint_family":"INDEPENDENT_POISSON_FROZEN",
            },
        })
    if sha256_file(v1_state_path)!=before1 or sha256_file(v2_state_path)!=before2:
        raise RuntimeError("predictor mutated frozen state files")
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8",newline="\n") as f:
        for p in preds:
            f.write(json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
    print(json.dumps({"cutoff":rows[0]["cutoff"],"n":len(preds),"prediction_batch_sha256":sha256_file(out)}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
