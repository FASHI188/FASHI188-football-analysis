from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dt(text: str) -> datetime:
    x = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise RuntimeError("naive datetime")
    return x.astimezone(timezone.utc)


def grouped(rows: list[dict]) -> list[list[dict]]:
    out, cur, key = [], [], None
    for r in sorted(rows, key=lambda x:(x["cutoff"], x["competition_id"], x["fixture_id"])):
        k = r["cutoff"]
        if key is None or k == key:
            cur.append(r); key = k
        else:
            out.append(cur); cur=[r]; key=k
    if cur:
        out.append(cur)
    return out


def outcome(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


def logloss_1x2(probs: dict[str,float], hg: int, ag: int) -> float:
    return -math.log(max(1e-15, float(probs[outcome(hg, ag)])))


def score_p_from_cells(cells: list[dict], hg: int, ag: int) -> float:
    for c in cells:
        if int(c["home_goals"]) == hg and int(c["away_goals"]) == ag:
            return max(1e-15, float(c["probability"]))
    return 1e-15


def import_engines(v1_dir: Path, v2_dir: Path):
    sys.path.insert(0, str(v1_dir))
    v1 = importlib.import_module("pure_engine")
    sys.path.insert(0, str(v2_dir))
    # Ensure repository V2 engine wins the generic module name.
    sys.modules.pop("engine", None)
    v2 = importlib.import_module("engine")
    return v1, v2


def v1_fixture(v1, r):
    return v1.Fixture(
        fixture_id=r["fixture_id"], competition_id=r["competition_id"], season=r["season"],
        kickoff=dt(r["cutoff"]), home_team_id=r["home_team_id"], away_team_id=r["away_team_id"]
    )


def v2_fixture(v2, r):
    return v2.Fixture(
        fixture_id=r["fixture_id"], competition_id=r["competition_id"], season=r["season"],
        kickoff=dt(r["cutoff"]), home_team_id=r["home_team_id"], away_team_id=r["away_team_id"],
        round_index=r.get("round_index")
    )


def cutoff_index(rows: list[dict], frac: float=0.60) -> datetime:
    bs=grouped(rows)
    if len(bs) < 10:
        raise RuntimeError("development universe too small")
    idx=min(len(bs)-1, max(1, int(len(bs)*frac)))
    return dt(bs[idx][0]["cutoff"])


def v1_grid(v1):
    return [
        v1.Parameters(half_life_days=170.0, prior_matches=6.0, cross_season_shrink=0.48,
                      competition_prior_matches=20.0, global_team_prior_matches=10.0),
        v1.Parameters(half_life_days=210.0, prior_matches=8.0, cross_season_shrink=0.58,
                      competition_prior_matches=24.0, global_team_prior_matches=12.0),
        v1.Parameters(half_life_days=260.0, prior_matches=10.0, cross_season_shrink=0.66,
                      competition_prior_matches=28.0, global_team_prior_matches=14.0),
        v1.Parameters(half_life_days=320.0, prior_matches=12.0, cross_season_shrink=0.74,
                      competition_prior_matches=32.0, global_team_prior_matches=16.0),
    ]


def release_v1(v1, state, pending, now):
    while pending and pending[0][0] <= now:
        _, batch = pending.pop(0)
        fixtures=[v1_fixture(v1,r) for r in batch]
        labels={r["fixture_id"]:(int(r["home_goals"]),int(r["away_goals"])) for r in batch}
        state.apply_batch(fixtures, labels)


def release_v2(v2, state, pending, now):
    while pending and pending[0][0] <= now:
        _, batch = pending.pop(0)
        fixtures=[v2_fixture(v2,r) for r in batch]
        labels={r["fixture_id"]:(int(r["home_goals"]),int(r["away_goals"])) for r in batch}
        state.apply_batch(fixtures, labels)


def tune_v1(v1, rows, tune_start):
    board=[]
    for idx, params in enumerate(v1_grid(v1)):
        state=v1.EngineState(params=params)
        pending=[]
        losses=[]
        n=0
        for batch in grouped(rows):
            now=dt(batch[0]["cutoff"])
            release_v1(v1,state,pending,now)
            if now >= tune_start:
                for r in batch:
                    pred=state.predict(v1_fixture(v1,r))
                    probs={"home":pred["p_home"],"draw":pred["p_draw"],"away":pred["p_away"]}
                    losses.append(logloss_1x2(probs,int(r["home_goals"]),int(r["away_goals"])))
                    n += 1
            pending.append((dt(batch[0]["result_available_at"]),batch))
        board.append({
            "candidate":idx,
            "objective":"1x2_logloss",
            "value":sum(losses)/max(1,len(losses)),
            "n":n,
            "parameters":dataclasses.asdict(params),
        })
    board.sort(key=lambda x:(x["value"], x["candidate"]))
    return board[0], board


def v2_candidates():
    out=[{"joint_family":"INDEPENDENT_POISSON_FROZEN","dependence":0.0,"dispersion":50.0}]
    for dep in (-0.12,-0.06,0.0,0.06,0.12):
        out.append({"joint_family":"DIXON_COLES_LOW_SCORE","dependence":dep,"dispersion":50.0})
    for dep in (-0.6,-0.3,0.0,0.3,0.6):
        out.append({"joint_family":"DIAGONAL_INFLATION_BIVARIATE","dependency":dep,"dispersion":50.0})
    for disp in (8.0,14.0,24.0,50.0):
        for dep in (-0.6,-0.3,0.0,0.3,0.6):
            out.append({"joint_family":"DYNAMIC_NB_DIAGONAL","dependency":dep,"dispersion":disp})
    for disp in (8.0,14.0,24.0,50.0):
        for dep in (-0.3,0.0,0.3):
            out.append({"joint_family":"DYNAMIC_NB_MARCO","dependency":dep,"dispersion":disp})
    return out


def tune_v2(v2, rows, tune_start):
    board=[]
    base_params=v2.Parameters()
    for idx,cfg in enumerate(v2_candidates()):
        state=v2.EngineState(base_params)
        pending=[]
        score_losses=[]
        one_losses=[]
        n=0
        for batch in grouped(rows):
            now=dt(batch[0]["cutoff"])
            release_v2(v2,state,pending,now)
            if now >= tune_start:
                for r in batch:
                    fixture=v2_fixture(v2,r)
                    feat=state.predict_features(fixture)
                    matrix=v2.joint_matrix(
                        cfg["joint_family"],feat,
                        dispersion_home=cfg["dispersion"],dispersion_away=cfg["dispersion"],
                        dependency=cfg["dependence"],max_goals=base_params.max_goals,
                    )
                    hg,ag=int(r["home_goals"]),int(r["away_goals"])
                    p=v2.exact_score_probability(matrix,hg,ag)
                    score_losses.append(-math.log(max(1e-15,p)))
                    one_losses.append(logloss_1x2(v2.matrix_1x2(matrix),hg,ag))
                    n += 1
            pending.append((dt(batch[0]["result_available_at"]),batch))
        board.append({
            "candidate":idx,
            "objective":"exact_score_logloss",
            "value":sum(score_losses)/max(1,len(score_losses)),
            "secondary_1x2_logloss":sum(one_losses)/max(1,len(one_losses)),
            "n":n, **cfg,
        })
    board.sort(key=lambda x:(x["value"],x["secondary_1x2_logloss"],x["candidate"]))
    return board[0], board, dataclasses.asdict(base_params)


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--development",required=True)
    ap.add_argument("--dataset-manifest",required=True)
    ap.add_argument("--v1-dir",required=True)
    ap.add_argument("--v2-dir",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()
    dev_path=Path(args.development)
    manifest_path=Path(args.dataset_manifest)
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    rows=read_jsonl(dev_path)
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256_file(dev_path) != manifest["development_sha256"]:
        raise RuntimeError("development SHA mismatch")
    if any(r["season"] not in manifest["development_seasons"] for r in rows):
        raise RuntimeError("non-development season reached lock fit")
    eval_set=set(manifest["evaluation_seasons"])
    if any(r["season"] in eval_set for r in rows):
        raise RuntimeError("evaluation season reached lock fit")
    v1,v2=import_engines(Path(args.v1_dir),Path(args.v2_dir))
    tune_start=cutoff_index(rows,0.60)
    v1_best,v1_board=tune_v1(v1,rows,tune_start)
    v2_best,v2_board,v2_params=tune_v2(v2,rows,tune_start)

    v1_lock={
        "schema_version":"football3-v1-pit-research-lock-v1",
        "research_only":True,
        "development_sha256":sha256_file(dev_path),
        "development_seasons":manifest["development_seasons"],
        "tune_start":tune_start.isoformat(),
        "objective":"1x2_logloss",
        "parameters":v1_best["parameters"],
        "selected_candidate":v1_best["candidate"],
    }
    v2_lock={
        "schema_version":"football3-v2-pit-research-lock-v1",
        "research_only":True,
        "formal_candidate":False,
        "development_sha256":sha256_file(dev_path),
        "development_seasons":manifest["development_seasons"],
        "tune_start":tune_start.isoformat(),
        "objective":"exact_score_logloss",
        "parameters":v2_params,
        "joint_family":v2_best["joint_family"],
        "dependence":v2_best["dependence"],
        "dispersion_home":v2_best["dispersion"],
        "dispersion_away":v2_best["dispersion"],
        "fitness_retained":False,
        "dual_head_retained":False,
        "max_goals":int(v2_params["max_goals"]),
        "selected_candidate":v2_best["candidate"],
        "ablation_control":"INDEPENDENT_POISSON_FROZEN",
    }
    v1p=out/"v1_lock.json"; v2p=out/"v2_lock.json"
    v1p.write_text(json.dumps(v1_lock,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    v2p.write_text(json.dumps(v2_lock,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    report={
        "schema_version":"football3-v2-pit-lock-fit-report-v1",
        "research_only":True,
        "evaluation_labels_read":False,
        "development_n":len(rows),
        "tune_start":tune_start.isoformat(),
        "v1_selected":v1_best,
        "v2_selected":v2_best,
        "v1_leaderboard":v1_board,
        "v2_leaderboard":v2_board,
        "v1_lock_sha256":sha256_file(v1p),
        "v2_lock_sha256":sha256_file(v2p),
    }
    (out/"lock_fit_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "status":"RESEARCH_LOCKS_FROZEN_BEFORE_EVALUATION_SCORING",
        "tune_start":tune_start.isoformat(),
        "v1":v1_best,
        "v2":v2_best,
        "v1_lock_sha256":report["v1_lock_sha256"],
        "v2_lock_sha256":report["v2_lock_sha256"],
    },indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
