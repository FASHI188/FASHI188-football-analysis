from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GAMMAS = (0.0, 0.35, 0.55, 0.75, 0.95, 1.15, 1.35, 1.55, 1.75)
DC_DEPS = (-0.12, -0.06, 0.0, 0.06, 0.12)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def dt(text: str) -> datetime:
    x = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise RuntimeError("naive datetime")
    return x.astimezone(timezone.utc)


def grouped(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    key = None
    for r in sorted(rows, key=lambda x: (x["cutoff"], x["competition_id"], x["fixture_id"])):
        k = r["cutoff"]
        if key is None or k == key:
            cur.append(r)
            key = k
        else:
            out.append(cur)
            cur = [r]
            key = k
    if cur:
        out.append(cur)
    return out


def fixture(v2, r):
    return v2.Fixture(
        fixture_id=r["fixture_id"], competition_id=r["competition_id"], season=r["season"],
        kickoff=dt(r["cutoff"]), home_team_id=r["home_team_id"], away_team_id=r["away_team_id"],
        round_index=r.get("round_index"),
    )


def result_key(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


def tune_start(rows: list[dict[str, Any]], frac: float = 0.60) -> datetime:
    batches = grouped(rows)
    if len(batches) < 10:
        raise RuntimeError("development universe too small")
    idx = min(len(batches) - 1, max(1, int(len(batches) * frac)))
    return dt(batches[idx][0]["cutoff"])


def repaired_features(state, f, gamma: float) -> dict[str, Any]:
    base = state.predict_features(f)
    kickoff = f.kickoff.astimezone(timezone.utc)
    comp_h, comp_a, _ = state._view_comp(f.competition_id, kickoff)
    pooled = max(1e-9, 0.5 * (float(comp_h) + float(comp_a)))
    out = dict(base)
    out["mu_home"] = min(state.params.max_rate, max(state.params.min_rate,
        float(base["mu_home"]) * (float(comp_h) / pooled) ** gamma))
    out["mu_away"] = min(state.params.max_rate, max(state.params.min_rate,
        float(base["mu_away"]) * (float(comp_a) / pooled) ** gamma))
    return out


def apply_batch(v2, state, batch: list[dict[str, Any]], label_map: dict[str, dict[str, Any]]) -> None:
    fs = [fixture(v2, r) for r in batch]
    labels = {r["fixture_id"]: (int(label_map[r["fixture_id"]]["home_goals"]), int(label_map[r["fixture_id"]]["away_goals"])) for r in batch}
    state.apply_batch(fs, labels)


def init_from_development(v2, params, development: list[dict[str, Any]]):
    state = v2.EngineState(params)
    label_map = {r["fixture_id"]: r for r in development}
    for batch in grouped(development):
        apply_batch(v2, state, batch, label_map)
    return state


def development_tune(v2, params, development: list[dict[str, Any]]) -> tuple[float, float, dict[str, Any]]:
    start = tune_start(development)
    state = v2.EngineState(params)
    pending: list[tuple[datetime, list[dict[str, Any]]]] = []
    gamma_losses = {g: [] for g in GAMMAS}
    for batch in grouped(development):
        now = dt(batch[0]["cutoff"])
        while pending and pending[0][0] <= now:
            _, old = pending.pop(0)
            apply_batch(v2, state, old, {r["fixture_id"]: r for r in old})
        if now >= start:
            for r in batch:
                f = fixture(v2, r)
                hg, ag = int(r["home_goals"]), int(r["away_goals"])
                for g in GAMMAS:
                    feat = repaired_features(state, f, g)
                    m = v2.joint_matrix("INDEPENDENT_POISSON_FROZEN", feat, dependence=0.0, max_goals=params.max_goals)
                    p = v2.matrix_1x2(m)[result_key(hg, ag)]
                    gamma_losses[g].append(-math.log(max(1e-15, float(p))))
        pending.append((dt(batch[0]["result_available_at"]), batch))
    gamma_board = [{"gamma": g, "logloss": sum(v) / len(v), "n": len(v)} for g, v in gamma_losses.items()]
    gamma_board.sort(key=lambda x: (x["logloss"], x["gamma"]))
    best_gamma = float(gamma_board[0]["gamma"])

    state = v2.EngineState(params)
    pending = []
    dep_losses = {d: [] for d in DC_DEPS}
    for batch in grouped(development):
        now = dt(batch[0]["cutoff"])
        while pending and pending[0][0] <= now:
            _, old = pending.pop(0)
            apply_batch(v2, state, old, {r["fixture_id"]: r for r in old})
        if now >= start:
            for r in batch:
                f = fixture(v2, r)
                feat = repaired_features(state, f, best_gamma)
                hg, ag = int(r["home_goals"]), int(r["away_goals"])
                for dep in DC_DEPS:
                    fam = "INDEPENDENT_POISSON_FROZEN" if dep == 0.0 else "DIXON_COLES_LOW_SCORE"
                    m = v2.joint_matrix(fam, feat, dependence=dep, max_goals=params.max_goals)
                    q = v2.exact_score_probability(m, hg, ag)
                    dep_losses[dep].append(-math.log(max(1e-15, float(q))))
        pending.append((dt(batch[0]["result_available_at"]), batch))
    dep_board = [{"dependence": d, "exact_score_logloss": sum(v) / len(v), "n": len(v)} for d, v in dep_losses.items()]
    dep_board.sort(key=lambda x: (x["exact_score_logloss"], abs(x["dependence"]), x["dependence"]))
    best_dep = float(dep_board[0]["dependence"])
    return best_gamma, best_dep, {"tune_start": start.isoformat(), "gamma_board": gamma_board, "dependence_board": dep_board}


def metric_accumulator() -> dict[str, Any]:
    return {"n": 0, "ll": 0.0, "score_ll": 0.0, "correct": 0, "pred_h": 0.0, "pred_a": 0.0,
            "actual_h": 0, "actual_a": 0, "top_home": 0, "top_draw": 0, "top_away": 0}


def add_metric(acc: dict[str, Any], v2, matrix, hg: int, ag: int) -> None:
    p = v2.matrix_1x2(matrix)
    y = result_key(hg, ag)
    acc["n"] += 1
    acc["ll"] += -math.log(max(1e-15, float(p[y])))
    q = v2.exact_score_probability(matrix, hg, ag)
    acc["score_ll"] += -math.log(max(1e-15, float(q)))
    top = max(("home", "draw", "away"), key=lambda k: p[k])
    acc["correct"] += int(top == y)
    acc["top_" + top] += 1
    acc["pred_h"] += sum(i * q for i, row in enumerate(matrix) for q in row)
    acc["pred_a"] += sum(j * q for i, row in enumerate(matrix) for j, q in enumerate(row))
    acc["actual_h"] += hg
    acc["actual_a"] += ag


def finish(acc: dict[str, Any]) -> dict[str, Any]:
    n = acc["n"]
    return {
        "n": n,
        "logloss": acc["ll"] / n,
        "exact_score_logloss": acc["score_ll"] / n,
        "top1": acc["correct"] / n,
        "top1_calls": {"home": acc["top_home"], "draw": acc["top_draw"], "away": acc["top_away"]},
        "predicted_mean_goals": {"home": acc["pred_h"] / n, "away": acc["pred_a"] / n},
        "actual_mean_goals": {"home": acc["actual_h"] / n, "away": acc["actual_a"] / n},
    }


def evaluation_replay(v2, params, development, features, labels, gamma: float, dep: float) -> dict[str, Any]:
    state = init_from_development(v2, params, development)
    lm = {r["fixture_id"]: r for r in labels}
    if set(lm) != {r["fixture_id"] for r in features}:
        raise RuntimeError("evaluation identity mismatch")
    pending: list[tuple[datetime, list[dict[str, Any]]]] = []
    frozen: list[tuple[str, list[list[float]], list[list[float]]]] = []
    for batch in grouped(features):
        now = dt(batch[0]["cutoff"])
        while pending and pending[0][0] <= now:
            _, old = pending.pop(0)
            if max(dt(x["cutoff"]) for x in old) >= now:
                raise RuntimeError("same/future label attempted before prediction")
            apply_batch(v2, state, old, lm)
        for r in batch:
            f = fixture(v2, r)
            feat = repaired_features(state, f, gamma)
            off = v2.joint_matrix("INDEPENDENT_POISSON_FROZEN", feat, dependence=0.0, max_goals=params.max_goals)
            fam = "INDEPENDENT_POISSON_FROZEN" if dep == 0.0 else "DIXON_COLES_LOW_SCORE"
            joint = v2.joint_matrix(fam, feat, dependence=dep, max_goals=params.max_goals)
            frozen.append((r["fixture_id"], off, joint))
        available = max(dt(lm[r["fixture_id"]]["result_available_at"]) for r in batch)
        pending.append((available, batch))
        pending.sort(key=lambda x: x[0])

    off_acc = metric_accumulator()
    joint_acc = metric_accumulator()
    for fid, off, joint in frozen:
        lab = lm[fid]
        hg, ag = int(lab["home_goals"]), int(lab["away_goals"])
        add_metric(off_acc, v2, off, hg, ag)
        add_metric(joint_acc, v2, joint, hg, ag)
    return {"independent": finish(off_acc), "joint": finish(joint_acc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--development", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--label-vault", required=True)
    ap.add_argument("--v2-dir", required=True)
    ap.add_argument("--baseline-metrics", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sys.path.insert(0, args.v2_dir)
    sys.modules.pop("engine", None)
    v2 = importlib.import_module("engine")
    params = v2.Parameters()
    dev = read_jsonl(Path(args.development))
    features = read_jsonl(Path(args.features))
    labels = read_jsonl(Path(args.label_vault))
    baseline = json.loads(Path(args.baseline_metrics).read_text(encoding="utf-8"))
    if baseline.get("n") != len(features) or baseline.get("formal_promotion_eligible") is not False:
        raise RuntimeError("baseline metric contract mismatch")

    gamma, dep, tune = development_tune(v2, params, dev)
    replay = evaluation_replay(v2, params, dev, features, labels, gamma, dep)
    v1 = baseline["metrics"]["v1"]
    result = {
        "schema_version": "football3-v2-venue-baseline-postview-diagnostic-v1",
        "status": "POSTVIEW_DIAGNOSTIC_COMPLETE",
        "research_only": True,
        "strict_prospective": False,
        "formal_promotion_eligible": False,
        "evaluation_reused_after_root_cause_discovery": True,
        "development_only_selection": True,
        "development_n": len(dev),
        "evaluation_n": len(features),
        "selected_gamma": gamma,
        "selected_dependence": dep,
        "tuning": tune,
        "metrics": replay,
        "baseline_v1": {k: v1[k] for k in ("logloss", "top1", "exact_score_logloss")},
        "comparison_to_v1": {
            "independent_delta_logloss": replay["independent"]["logloss"] - v1["logloss"],
            "independent_delta_top1": replay["independent"]["top1"] - v1["top1"],
            "independent_delta_exact_score_logloss": replay["independent"]["exact_score_logloss"] - v1["exact_score_logloss"],
            "joint_delta_logloss": replay["joint"]["logloss"] - v1["logloss"],
            "joint_delta_top1": replay["joint"]["top1"] - v1["top1"],
            "joint_delta_exact_score_logloss": replay["joint"]["exact_score_logloss"] - v1["exact_score_logloss"],
        },
        "input_sha256": {
            "development": sha256_file(Path(args.development)),
            "features": sha256_file(Path(args.features)),
            "label_vault": sha256_file(Path(args.label_vault)),
            "baseline_metrics": sha256_file(Path(args.baseline_metrics)),
            "script": sha256_file(Path(__file__)),
        },
        "interpretation_guard": "Post-view root-cause diagnostic only. The 2023-26 evaluation block was already inspected before this repair was designed; no promotion or prospective claim is allowed.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"], "selected_gamma": gamma, "selected_dependence": dep,
        "development_n": len(dev), "evaluation_n": len(features),
        "independent": replay["independent"], "joint": replay["joint"],
        "comparison_to_v1": result["comparison_to_v1"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
