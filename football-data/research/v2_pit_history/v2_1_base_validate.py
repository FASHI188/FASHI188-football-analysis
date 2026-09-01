from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import pathlib
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import v2_1_base as v21

PARAM_GRID = {
    "team_half_life_days": (120.0, 240.0, 480.0),
    "competition_half_life_days": (540.0, 900.0),
    "team_prior_matches": (6.0, 12.0, 24.0),
    "competition_prior_matches": (24.0, 48.0),
    "residual_strength": (0.35, 0.60, 0.85),
    "cross_season_shrink": (0.40, 0.65),
}
GLOBAL_HOME_RATE = 1.38
GLOBAL_AWAY_RATE = 1.12
MAX_GOALS = 14
POSTVIEW_N = 5256
DEVELOPMENT_N = 1826


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def dt(value: str | datetime) -> datetime:
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise RuntimeError("naive datetime")
    return d.astimezone(timezone.utc)


def grouped(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out, current, key = [], [], None
    ordered = sorted(rows, key=lambda x: (dt(x["cutoff"]), str(x["competition_id"]), str(x["fixture_id"])))
    for row in ordered:
        k = dt(row["cutoff"])
        if key is None or k == key:
            current.append(row); key = k
        else:
            out.append(current); current = [row]; key = k
    if current: out.append(current)
    return out


def fixture21(row: dict[str, Any]) -> v21.Fixture:
    return v21.Fixture(str(row["fixture_id"]), str(row["competition_id"]), str(row["season"]), dt(row["cutoff"]),
                       str(row["home_team_id"]), str(row["away_team_id"]), row.get("round_index"))


def result_class(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


class Metrics:
    def __init__(self) -> None:
        self.n = 0; self.ll = self.brier = self.rps = 0.0; self.correct = 0
        self.pred_h = self.pred_a = 0.0; self.actual_h = self.actual_a = 0
    def add(self, p: dict[str, float], mh: float, ma: float, hg: int, ag: int) -> None:
        y = result_class(hg, ag); self.n += 1
        self.ll += -math.log(max(1e-15, float(p[y])))
        self.brier += sum((float(p[k]) - (1.0 if y == k else 0.0)) ** 2 for k in ("home", "draw", "away"))
        self.rps += ((float(p["home"]) - (1.0 if y == "home" else 0.0)) ** 2
                     + (float(p["home"]) + float(p["draw"]) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2) / 2.0
        self.correct += int(max(("home", "draw", "away"), key=lambda k: p[k]) == y)
        self.pred_h += mh; self.pred_a += ma; self.actual_h += hg; self.actual_a += ag
    def finish(self) -> dict[str, Any]:
        if self.n == 0:
            return {"n": 0, "logloss": None, "brier": None, "rps": None, "top1": None,
                    "predicted_mean_goals": {"home": None, "away": None}, "actual_mean_goals": {"home": None, "away": None}}
        return {"n": self.n, "logloss": self.ll/self.n, "brier": self.brier/self.n, "rps": self.rps/self.n,
                "top1": self.correct/self.n, "predicted_mean_goals": {"home": self.pred_h/self.n, "away": self.pred_a/self.n},
                "actual_mean_goals": {"home": self.actual_h/self.n, "away": self.actual_a/self.n}}


def parameter_grid() -> list[v21.Parameters]:
    keys = tuple(PARAM_GRID); rows = []
    for values in itertools.product(*(PARAM_GRID[k] for k in keys)):
        rows.append(v21.Parameters(**dict(zip(keys, values)), global_home_rate=GLOBAL_HOME_RATE, global_away_rate=GLOBAL_AWAY_RATE,
                                   min_rate=0.08, max_rate=6.0, max_goals=MAX_GOALS, team_venue_bias_enabled=False))
    return rows


def fold_assignment(dev: list[dict[str, Any]]) -> tuple[dict[str, int | None], dict[str, Any]]:
    batches = grouped(dev); n = len(batches)
    if n < 80: raise RuntimeError("development has too few kickoff batches")
    warm = max(1, math.ceil(0.20*n)); remaining = n-warm; base = remaining//8; extra = remaining%8
    assignment: dict[str, int | None] = {}; boundaries = []
    for i, batch in enumerate(batches):
        if i < warm: fold = None
        else:
            j, start, fold = i-warm, 0, None
            for k in range(8):
                size = base + (1 if k < extra else 0)
                if start <= j < start+size: fold = k; break
                start += size
            if fold is None: raise RuntimeError("fold assignment failure")
        for row in batch: assignment[str(row["fixture_id"])] = fold
    for k in range(8):
        ids = [r for r in dev if assignment[str(r["fixture_id"])] == k]
        boundaries.append({"fold": k, "n": len(ids), "first_cutoff": min((r["cutoff"] for r in ids), default=None),
                           "last_cutoff": max((r["cutoff"] for r in ids), default=None)})
    return assignment, {"unique_kickoff_batches": n, "warmup_batches": warm, "warmup_fraction": warm/n,
                        "test_batches": remaining, "folds": boundaries}


def _release_ready21(state: v21.EngineState, pending: list[tuple[datetime, list[dict[str, Any]]]], now: datetime) -> int:
    count = 0
    while pending and pending[0][0] <= now:
        _, batch = pending.pop(0)
        labels = {str(r["fixture_id"]): (int(r["home_goals"]), int(r["away_goals"]), dt(r["result_available_at"])) for r in batch}
        state.apply_batch([fixture21(r) for r in batch], labels, as_of=now); count += len(batch)
    return count


def replay21(dev: list[dict[str, Any]], params: v21.Parameters, fold_map: dict[str, int | None] | None = None,
             finish_all: bool = False) -> tuple[dict[str, Any], dict[int, dict[str, Any]], v21.EngineState]:
    state = v21.EngineState(params); pending = []; overall = Metrics(); folds = {k: Metrics() for k in range(8)}
    for batch in grouped(dev):
        now = dt(batch[0]["cutoff"]); _release_ready21(state, pending, now)
        preds = state.predict_batch([fixture21(r) for r in batch], include_matrix=False)
        for r, pred in zip(batch, preds):
            hg, ag = int(r["home_goals"]), int(r["away_goals"])
            p = {"home": float(pred["p_home"]), "draw": float(pred["p_draw"]), "away": float(pred["p_away"])}
            mh, ma = float(pred["matrix_mean_home"]), float(pred["matrix_mean_away"])
            overall.add(p, mh, ma, hg, ag)
            if fold_map is not None:
                f = fold_map[str(r["fixture_id"])]
                if f is not None: folds[int(f)].add(p, mh, ma, hg, ag)
        pending.append((max(dt(r["result_available_at"]) for r in batch), batch)); pending.sort(key=lambda x: (x[0], x[1][0]["cutoff"], x[1][0]["fixture_id"]))
    if finish_all and pending:
        _release_ready21(state, pending, max(x[0] for x in pending))
        if pending: raise RuntimeError("unable to flush development results")
    return overall.finish(), {k: m.finish() for k, m in folds.items()}, state


def import_v1(v1_dir: pathlib.Path):
    spec = importlib.util.spec_from_file_location("football3_frozen_v1", v1_dir / "pure_engine.py")
    if spec is None or spec.loader is None: raise RuntimeError("cannot import frozen V1")
    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod); return mod


def fixture_v1(v1, row: dict[str, Any]):
    return v1.Fixture(str(row["fixture_id"]), str(row["competition_id"]), str(row["season"]), dt(row["cutoff"]),
                      str(row["home_team_id"]), str(row["away_team_id"]))


def replay_v1(dev: list[dict[str, Any]], v1, lock: dict[str, Any], fold_map: dict[str, int | None]) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    state = v1.EngineState(params=v1.Parameters(**lock["parameters"])); pending = []; overall = Metrics(); folds = {k: Metrics() for k in range(8)}
    for batch in grouped(dev):
        now = dt(batch[0]["cutoff"])
        while pending and pending[0][0] <= now:
            _, old = pending.pop(0); labels = {str(r["fixture_id"]): (int(r["home_goals"]), int(r["away_goals"])) for r in old}
            state.apply_batch([fixture_v1(v1, r) for r in old], labels)
        for r in batch:
            pred = state.predict(fixture_v1(v1, r)); p = {"home": float(pred["p_home"]), "draw": float(pred["p_draw"]), "away": float(pred["p_away"])}
            matrix = pred["score_matrix"]
            if matrix and isinstance(matrix[0], dict):
                mh = sum(int(c["home_goals"])*float(c["probability"]) for c in matrix); ma = sum(int(c["away_goals"])*float(c["probability"]) for c in matrix)
            else:
                mh = sum(i*q for i, row in enumerate(matrix) for q in row); ma = sum(j*q for row in matrix for j, q in enumerate(row))
            hg, ag = int(r["home_goals"]), int(r["away_goals"]); overall.add(p, mh, ma, hg, ag)
            f = fold_map[str(r["fixture_id"])]
            if f is not None: folds[int(f)].add(p, mh, ma, hg, ag)
        pending.append((max(dt(r["result_available_at"]) for r in batch), batch)); pending.sort(key=lambda x: (x[0], x[1][0]["cutoff"], x[1][0]["fixture_id"]))
    return overall.finish(), {k: m.finish() for k, m in folds.items()}


def params_tuple(p: v21.Parameters) -> tuple[float, ...]:
    return (p.team_half_life_days, p.competition_half_life_days, p.team_prior_matches, p.competition_prior_matches,
            p.residual_strength, p.cross_season_shrink)


def development_select(dev_path: pathlib.Path, v1_dir: pathlib.Path, v1_lock_path: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    dev = read_jsonl(dev_path)
    if len(dev) != DEVELOPMENT_N: raise RuntimeError(f"development n mismatch {len(dev)}")
    if any(str(r["season"]) != "2022-23" for r in dev): raise RuntimeError("development contains non-2022/23 season")
    fold_map, fold_spec = fold_assignment(dev); v1 = import_v1(v1_dir); v1_lock = json.loads(v1_lock_path.read_text())
    v1_overall, v1_folds = replay_v1(dev, v1, v1_lock, fold_map); board = []
    for idx, params in enumerate(parameter_grid()):
        overall, folds, _ = replay21(dev, params, fold_map, finish_all=False); ntest = sum(folds[k]["n"] for k in range(8)); pooled = {}
        for metric in ("logloss", "brier", "rps", "top1"):
            pooled[metric] = sum(folds[k][metric]*folds[k]["n"] for k in range(8))/ntest
        board.append({"index": idx, "parameters": params.__dict__, "parameter_tuple": list(params_tuple(params)), "outer_test_n": ntest,
                      "outer_pooled": pooled, "fold_logloss": [folds[k]["logloss"] for k in range(8)], "development_all": overall})
    board.sort(key=lambda x: (x["outer_pooled"]["logloss"], x["outer_pooled"]["brier"], x["outer_pooled"]["rps"], tuple(x["parameter_tuple"])))
    best = board[0]; selected = v21.Parameters(**best["parameters"]); selected_all, selected_folds, _ = replay21(dev, selected, fold_map, finish_all=False)
    fold_compare, gains, nondeg = [], [], 0
    for k in range(8):
        gain = float(v1_folds[k]["logloss"])-float(selected_folds[k]["logloss"]); gains.append(gain)
        ok = selected_folds[k]["logloss"] <= v1_folds[k]["logloss"]+1e-12; nondeg += int(ok)
        fold_compare.append({"fold": k, "v1": v1_folds[k], "v2_1": selected_folds[k], "logloss_gain_v1_minus_v2_1": gain, "nondegrade": ok})
    pooled_gain = float(v1_overall["logloss"])-float(selected_all["logloss"])
    dev_gates = {"fold_nondegrade_n": nondeg, "fold_nondegrade_at_least_6_of_8": nondeg >= 6,
                 "median_fold_logloss_gain": statistics.median(gains), "median_fold_gain_gt_zero": statistics.median(gains) > 0.0,
                 "overall_logloss_gain_v1_minus_v2_1": pooled_gain, "overall_logloss_gain_at_least_0_001": pooled_gain >= 0.001,
                 "brier_not_worse": selected_all["brier"] <= v1_overall["brier"]+1e-12,
                 "rps_not_worse": selected_all["rps"] <= v1_overall["rps"]+1e-12,
                 "top1_delta": selected_all["top1"]-v1_overall["top1"],
                 "top1_delta_at_least_minus_0_0015": selected_all["top1"]-v1_overall["top1"] >= -0.0015}
    result = {"schema_version": "football3-v2-1-development-selection-v1", "status": "V2_1_DEVELOPMENT_SELECTION_FROZEN",
              "development_only": True, "postview_labels_read": False, "development_n": len(dev), "development_sha256": sha_file(dev_path),
              "v1_lock_sha256": sha_file(v1_lock_path), "v1_engine_sha256": sha_file(v1_dir/"pure_engine.py"), "grid_size": len(board),
              "grid_preregistered": PARAM_GRID, "fold_spec": fold_spec, "selected_parameters": selected.__dict__, "selection_key": best["outer_pooled"],
              "v1_overall": v1_overall, "v2_1_overall": selected_all, "fold_comparison": fold_compare, "development_gates": dev_gates,
              "board_sha256": v21.canonical_sha256(board), "top10": board[:10], "formal_weight": 0, "formal_enablement": False}
    write_json(out, result); return result


def initialize21_from_dev(dev: list[dict[str, Any]], params: v21.Parameters) -> v21.EngineState:
    return replay21(dev, params, None, finish_all=True)[2]


def predict_postview(dev_path: pathlib.Path, features_path: pathlib.Path, label_path: pathlib.Path,
                     selection_path: pathlib.Path, out_dir: pathlib.Path) -> dict[str, Any]:
    dev, features = read_jsonl(dev_path), read_jsonl(features_path)
    if len(dev) != DEVELOPMENT_N or len(features) != POSTVIEW_N: raise RuntimeError("postview input n mismatch")
    selection = json.loads(selection_path.read_text())
    if selection.get("status") != "V2_1_DEVELOPMENT_SELECTION_FROZEN" or selection.get("postview_labels_read") is not False:
        raise RuntimeError("development selection not frozen before postview")
    state = initialize21_from_dev(dev, v21.Parameters(**selection["selected_parameters"])); label_rows = read_jsonl(label_path)
    if len(label_rows) != POSTVIEW_N: raise RuntimeError("postview label n mismatch")
    labels = {str(r["fixture_id"]): r for r in label_rows}
    if set(labels) != {str(r["fixture_id"]) for r in features}: raise RuntimeError("postview identity mismatch")
    pending, frozen_rows, predicted_ids, release_audit = [], [], set(), []
    for batch in grouped(features):
        now = dt(batch[0]["cutoff"])
        while pending and pending[0][0] <= now:
            _, old = pending.pop(0); labs = {}
            for r in old:
                fid = str(r["fixture_id"])
                if fid not in predicted_ids: raise RuntimeError("label attempted before own prediction freeze")
                lab = labels[fid]; av = dt(lab["result_available_at"])
                if av > now or dt(r["cutoff"]) >= now: raise RuntimeError("future/same-cutoff postview label attempted")
                labs[fid] = (int(lab["home_goals"]), int(lab["away_goals"]), av)
                release_audit.append({"fixture_id": fid, "prediction_frozen_before_read": True, "fixture_cutoff": r["cutoff"],
                                      "result_available_at": lab["result_available_at"], "read_as_of": now.isoformat()})
            state.apply_batch([fixture21(r) for r in old], labs, as_of=now)
        preds = state.predict_batch([fixture21(r) for r in batch])
        for r, p in zip(batch, preds):
            fid = str(r["fixture_id"])
            if fid in predicted_ids: raise RuntimeError("duplicate postview prediction")
            predicted_ids.add(fid); mh, ma = v21.matrix_mean_goals(p["score_matrix"])
            frozen_rows.append({"fixture_id": fid, "competition_id": r["competition_id"], "season": r["season"], "cutoff": r["cutoff"],
                                "home_team_id": r["home_team_id"], "away_team_id": r["away_team_id"], "mu_home": p["mu_home"], "mu_away": p["mu_away"],
                                "matrix_mean_home": mh, "matrix_mean_away": ma, "p_home": p["p_home"], "p_draw": p["p_draw"], "p_away": p["p_away"],
                                "joint_family": p["joint_family"], "prediction_sha256": p["prediction_sha256"],
                                "score_matrix_sha256": v21.canonical_sha256(p["score_matrix"]), "formal_weight": 0})
        pending.append((max(dt(labels[str(r["fixture_id"])]["result_available_at"]) for r in batch), batch)); pending.sort(key=lambda x: (x[0], x[1][0]["cutoff"], x[1][0]["fixture_id"]))
    if len(frozen_rows) != POSTVIEW_N or len(predicted_ids) != POSTVIEW_N: raise RuntimeError("postview prediction count mismatch")
    out_dir.mkdir(parents=True, exist_ok=True); pred_path = out_dir/"v2_1_postview_predictions.jsonl"; write_jsonl(pred_path, frozen_rows)
    audit_path = out_dir/"pit_release_audit.json"; write_json(audit_path, {"schema_version": "football3-v2-1-pit-release-audit-v1",
        "prior_label_reads_n": len(release_audit), "target_label_reads_before_own_prediction": 0, "same_or_future_cutoff_label_reads": 0,
        "all_reads_after_own_prediction_freeze": True, "rows_sha256": v21.canonical_sha256(release_audit)})
    pre = {"schema_version": "football3-v2-1-postview-pre-score-v1", "status": "V2_1_POSTVIEW_PREDICTIONS_FROZEN",
           "post_view_diagnostic": True, "strict_prospective": False, "confirmation": False, "n": POSTVIEW_N,
           "development_selection_sha256": sha_file(selection_path), "prediction_sha256": sha_file(pred_path),
           "pit_release_audit_sha256": sha_file(audit_path), "target_label_reads_before_own_prediction": 0,
           "joint_family": v21.FAMILY, "formal_weight": 0, "formal_enablement": False}
    write_json(out_dir/"pre_score_manifest.json", pre); return pre


def _metrics_from_frozen_base_predictions(base_predictions: pathlib.Path, labels: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    models = {m: Metrics() for m in ("v1", "v2_joint", "v2_joint_off")}; groups = defaultdict(lambda: {m: Metrics() for m in models}); n = 0
    with base_predictions.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            r = json.loads(line); fid = str(r["fixture_id"]); lab = labels[fid]; hg, ag = int(lab["home_goals"]), int(lab["away_goals"]); g = f'{r["competition_id"]}|{r["season"]}'
            for m in models:
                z = r[m]; p = {"home": float(z["p_home"]), "draw": float(z["p_draw"]), "away": float(z["p_away"])}; matrix = z["score_matrix"]
                mh = sum(i*q for i, row in enumerate(matrix) for q in row); ma = sum(j*q for row in matrix for j, q in enumerate(row))
                models[m].add(p, mh, ma, hg, ag); groups[g][m].add(p, mh, ma, hg, ag)
            n += 1
    if n != POSTVIEW_N: raise RuntimeError(f"base prediction n mismatch {n}")
    return {m: a.finish() for m, a in models.items()}, {g: {m: a.finish() for m, a in mm.items()} for g, mm in sorted(groups.items())}


def score_postview(pred_path: pathlib.Path, pre_path: pathlib.Path, label_path: pathlib.Path, base_metrics_path: pathlib.Path,
                   base_predictions_path: pathlib.Path, selection_path: pathlib.Path, out_dir: pathlib.Path) -> dict[str, Any]:
    pre = json.loads(pre_path.read_text())
    if pre.get("status") != "V2_1_POSTVIEW_PREDICTIONS_FROZEN" or pre["prediction_sha256"] != sha_file(pred_path):
        raise RuntimeError("pre-score freeze/SHA missing")
    rows, labels_rows = read_jsonl(pred_path), read_jsonl(label_path); labels = {str(r["fixture_id"]): r for r in labels_rows}
    if len(rows) != POSTVIEW_N or len(labels) != POSTVIEW_N or {str(r["fixture_id"]) for r in rows} != set(labels): raise RuntimeError("scorer identity/n mismatch")
    base_metrics = json.loads(base_metrics_path.read_text()); baseline_same, baseline_groups = _metrics_from_frozen_base_predictions(base_predictions_path, labels)
    if base_metrics.get("n") != POSTVIEW_N: raise RuntimeError("base metric n mismatch")
    v2m = Metrics(); v2groups = defaultdict(Metrics)
    for r in rows:
        lab = labels[str(r["fixture_id"])] ; hg, ag = int(lab["home_goals"]), int(lab["away_goals"]); p = {"home": float(r["p_home"]), "draw": float(r["p_draw"]), "away": float(r["p_away"])}
        v2m.add(p, float(r["matrix_mean_home"]), float(r["matrix_mean_away"]), hg, ag); v2groups[f'{r["competition_id"]}|{r["season"]}'].add(p, float(r["matrix_mean_home"]), float(r["matrix_mean_away"]), hg, ag)
    v21_metric = v2m.finish(); group_rows = {}; direction_pass = True
    for g, acc in sorted(v2groups.items()):
        m = acc.finish(); actual_gap = m["actual_mean_goals"]["home"]-m["actual_mean_goals"]["away"]; pred_gap = m["predicted_mean_goals"]["home"]-m["predicted_mean_goals"]["away"]
        ok = (actual_gap > 0 and pred_gap > 0) or (actual_gap < 0 and pred_gap < 0) or (abs(actual_gap) <= 1e-15 and abs(pred_gap) <= 0.03)
        direction_pass = direction_pass and ok; group_rows[g] = {"v2_1": m, "v1": baseline_groups[g]["v1"], "old_v2_joint": baseline_groups[g]["v2_joint"],
            "actual_home_minus_away": actual_gap, "v2_1_pred_home_minus_away": pred_gap, "direction_pass": ok}
    selection = json.loads(selection_path.read_text()); dev_gates = selection["development_gates"]; v1, old = baseline_same["v1"], baseline_same["v2_joint"]
    for metric in ("logloss", "brier", "rps", "top1"):
        if abs(v1[metric]-base_metrics["metrics"]["v1"][metric]) > 1e-12 or abs(old[metric]-base_metrics["metrics"]["v2_joint"][metric]) > 1e-12:
            raise RuntimeError(f"frozen baseline replay mismatch {metric}")
    v1eh = abs(v1["predicted_mean_goals"]["home"]-v1["actual_mean_goals"]["home"]); v1ea = abs(v1["predicted_mean_goals"]["away"]-v1["actual_mean_goals"]["away"])
    v21eh = abs(v21_metric["predicted_mean_goals"]["home"]-v21_metric["actual_mean_goals"]["home"]); v21ea = abs(v21_metric["predicted_mean_goals"]["away"]-v21_metric["actual_mean_goals"]["away"])
    post_gates = {"logloss_not_worse_than_v1": v21_metric["logloss"] <= v1["logloss"]+1e-12, "brier_not_worse_than_v1": v21_metric["brier"] <= v1["brier"]+1e-12,
        "rps_not_worse_than_v1": v21_metric["rps"] <= v1["rps"]+1e-12, "home_mean_abs_error_v1": v1eh, "away_mean_abs_error_v1": v1ea,
        "home_mean_abs_error_v2_1": v21eh, "away_mean_abs_error_v2_1": v21ea, "home_mean_error_not_worse": v21eh <= v1eh+1e-12,
        "away_mean_error_not_worse": v21ea <= v1ea+1e-12, "competition_season_direction_all_pass": direction_pass, "competition_season_group_n": len(group_rows)}
    invariants = {"deterministic_unit_tests_required": True, "phase1_family_independent_poisson_only": all(r["joint_family"] == v21.FAMILY for r in rows),
                  "formal_weight_zero": all(r.get("formal_weight") == 0 for r in rows), "target_label_reads_before_own_prediction_zero": pre.get("target_label_reads_before_own_prediction") == 0}
    all_gates = (invariants["phase1_family_independent_poisson_only"] and invariants["formal_weight_zero"] and invariants["target_label_reads_before_own_prediction_zero"]
                 and direction_pass and dev_gates["fold_nondegrade_at_least_6_of_8"] and dev_gates["median_fold_gain_gt_zero"]
                 and dev_gates["overall_logloss_gain_at_least_0_001"] and dev_gates["brier_not_worse"] and dev_gates["rps_not_worse"]
                 and dev_gates["top1_delta_at_least_minus_0_0015"] and post_gates["logloss_not_worse_than_v1"] and post_gates["brier_not_worse_than_v1"]
                 and post_gates["rps_not_worse_than_v1"] and post_gates["home_mean_error_not_worse"] and post_gates["away_mean_error_not_worse"])
    status = "V2_1_BASE_REPAIR_ENGINEERING_PASS_POSTVIEW_ONLY" if all_gates else "V2_1_BASE_REPAIR_REJECTED"
    result = {"schema_version": "football3-v2-1-base-repair-score-v1", "status": status, "post_view_diagnostic": True, "strict_prospective": False,
              "confirmation": False, "scientific_pass": False, "formal_promotion_eligible": False, "formal_weight": 0, "formal_enablement": False,
              "n": POSTVIEW_N, "models": {"frozen_v1": v1, "old_v2_joint": old, "old_v2_independent": baseline_same["v2_joint_off"],
              "v2_1_independent_poisson": v21_metric}, "development_selection": selection, "postview_gates": post_gates,
              "competition_season": group_rows, "invariants": invariants, "prediction_sha256": sha_file(pred_path), "label_vault_sha256": sha_file(label_path),
              "base_metrics_sha256": sha_file(base_metrics_path), "base_predictions_sha256": sha_file(base_predictions_path),
              "no_postview_parameter_change": True, "new_confirmation_cohort_opened": False}
    out_dir.mkdir(parents=True, exist_ok=True); write_json(out_dir/"v2_1_score.json", result)
    write_json(out_dir/"v2_1_gate.json", {"schema_version": "football3-v2-1-base-repair-gate-v1", "status": status,
        "terminal_for_this_postview_batch": True, "all_continue_research_gates_pass": all_gates,
        "allowed_next_scientific_action": "SEPARATE_USER_AUTHORIZATION_REQUIRED_FOR_NEW_UNCONSUMED_CONFIRMATION_COHORT", "formal_weight": 0, "formal_enablement": False})
    return result


def main() -> int:
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("development"); a.add_argument("--development", type=pathlib.Path, required=True); a.add_argument("--v1-dir", type=pathlib.Path, required=True); a.add_argument("--v1-lock", type=pathlib.Path, required=True); a.add_argument("--out", type=pathlib.Path, required=True)
    a = sp.add_parser("predict-postview"); a.add_argument("--development", type=pathlib.Path, required=True); a.add_argument("--features", type=pathlib.Path, required=True); a.add_argument("--label-vault", type=pathlib.Path, required=True); a.add_argument("--selection", type=pathlib.Path, required=True); a.add_argument("--out-dir", type=pathlib.Path, required=True)
    a = sp.add_parser("score-postview"); a.add_argument("--predictions", type=pathlib.Path, required=True); a.add_argument("--pre-score", type=pathlib.Path, required=True); a.add_argument("--label-vault", type=pathlib.Path, required=True); a.add_argument("--base-metrics", type=pathlib.Path, required=True); a.add_argument("--base-predictions", type=pathlib.Path, required=True); a.add_argument("--selection", type=pathlib.Path, required=True); a.add_argument("--out-dir", type=pathlib.Path, required=True)
    x = ap.parse_args()
    if x.cmd == "development": r = development_select(x.development, x.v1_dir, x.v1_lock, x.out)
    elif x.cmd == "predict-postview": r = predict_postview(x.development, x.features, x.label_vault, x.selection, x.out_dir)
    else: r = score_postview(x.predictions, x.pre_score, x.label_vault, x.base_metrics, x.base_predictions, x.selection, x.out_dir)
    print(json.dumps({"status": r.get("status"), "keys": sorted(r)}, ensure_ascii=False, sort_keys=True)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
