#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"
LABELS = ("H", "D", "A")
ENC = {"A": -1.0, "D": 0.0, "H": 1.0}


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r40f", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ridge_fit(X: list[list[float]], y: list[float], l2: float, solve_linear) -> list[float]:
    if not X or len(X) != len(y):
        raise RuntimeError("INVALID_RIDGE_INPUT")
    d = len(X[0])
    A = [[0.0] * d for _ in range(d)]
    b = [0.0] * d
    for row, yy in zip(X, y):
        for j in range(d):
            b[j] += row[j] * yy
            for k in range(j, d):
                A[j][k] += row[j] * row[k]
    for j in range(d):
        for k in range(j):
            A[j][k] = A[k][j]
        if j > 0:
            A[j][j] += l2
        A[j][j] += 1e-9
    return solve_linear(A, b)


def dot(beta: list[float], x: list[float]) -> float:
    return sum(b * v for b, v in zip(beta, x))


def rate(count: int, n: int) -> float | None:
    return count / n if n else None


def aggregate_records(records: list[dict]) -> dict:
    central = [r for r in records if r["central"]]
    outer = [r for r in records if not r["central"]]
    cd = sum(r["actual"] == "D" for r in central)
    od = sum(r["actual"] == "D" for r in outer)
    cr = rate(cd, len(central))
    orate = rate(od, len(outer))
    lift = (cr - orate) if cr is not None and orate is not None else None
    return {
        "n": len(records),
        "central_n": len(central),
        "outer_n": len(outer),
        "central_draws": cd,
        "outer_draws": od,
        "central_draw_rate": cr,
        "outer_draw_rate": orate,
        "central_draw_lift": lift,
    }


def decile_summary(records: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in range(10):
        rows = [r for r in records if r["decile"] == d]
        counts = {lab: sum(r["actual"] == lab for r in rows) for lab in LABELS}
        out[str(d + 1)] = {
            "n": len(rows),
            "counts": counts,
            "rates": {lab: rate(counts[lab], len(rows)) for lab in LABELS},
        }
    return out


def group_rate(records: list[dict], deciles: set[int], lab: str) -> float | None:
    rows = [r for r in records if r["decile"] in deciles]
    return rate(sum(r["actual"] == lab for r in rows), len(rows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R40F-ROLLING-DRAW-CENTRALITY-1.0"
    fold_cfg = pre["folding"]
    axis_cfg = pre["latent_axis"]
    cent_cfg = pre["centrality_definition"]
    hard = pre["hard_boundaries"]
    assert int(fold_cfg["minimum_train_rows"]) == 200
    assert int(fold_cfg["minimum_test_rows"]) == 80
    assert fold_cfg["same_season_training"] is False
    assert fold_cfg["same_date_leakage"] is False
    assert float(axis_cfg["l2"]) == 10.0
    assert cent_cfg["no_test_threshold_tuning"] is True
    assert hard["external_network_requests"] == 0 and hard["football_api_requests"] == 0
    assert hard["new_data_collection"] is False
    assert hard["random_fixed100_consumed"] == 0
    assert hard["candidate_probability_model_fit"] == 0

    w = load_r39w_module()
    u = w.load_r39u_module()
    rows = [r for r in u.load_rows(args.root) if u.finite_vec(r.x)]

    by_comp: dict[str, list] = defaultdict(list)
    for r in rows:
        by_comp[r.competition].append(r)
    for comp in by_comp:
        by_comp[comp].sort(key=lambda r: (r.dt, r.key))

    min_train = int(fold_cfg["minimum_train_rows"])
    min_test = int(fold_cfg["minimum_test_rows"])
    l2 = float(axis_cfg["l2"])
    fold_metrics = []
    scored_records: list[dict] = []
    skipped_folds = []

    for comp, comp_rows in sorted(by_comp.items()):
        seasons: dict[str, list] = defaultdict(list)
        for r in comp_rows:
            seasons[r.season].append(r)
        season_groups = sorted(
            seasons.items(),
            key=lambda kv: (min(r.dt for r in kv[1]), kv[0]),
        )
        for season, test_rows in season_groups:
            test_rows = sorted(test_rows, key=lambda r: (r.dt, r.key))
            test_start = min(r.dt for r in test_rows)
            train_rows = [r for r in comp_rows if r.dt < test_start]
            if len(train_rows) < min_train or len(test_rows) < min_test:
                skipped_folds.append({
                    "competition": comp,
                    "season": season,
                    "train_n": len(train_rows),
                    "test_n": len(test_rows),
                    "test_start": test_start.isoformat(),
                    "reason": "MINIMUM_ROWS",
                })
                continue

            train_raw = [w.side_features(r.x) for r in train_rows]
            # w.standardize returns rows with intercept and a target row. Reuse one dummy target,
            # then independently transform all test rows using the same train-only medians/IQRs below.
            d = len(train_raw[0])
            med = []
            scale = []
            for j in range(d):
                vals = [x[j] for x in train_raw]
                m = u.quantile(vals, 0.5)
                s = u.quantile(vals, 0.75) - u.quantile(vals, 0.25)
                if not math.isfinite(s) or s < 1e-9:
                    s = 1.0
                med.append(m)
                scale.append(s)
            Xtrain = [[1.0] + [(x[j] - med[j]) / scale[j] for j in range(d)] for x in train_raw]
            ytrain = [ENC[r.label] for r in train_rows]
            beta = ridge_fit(Xtrain, ytrain, l2, w.solve_linear)
            train_scores = [dot(beta, x) for x in Xtrain]

            train_draw_rate = sum(r.label == "D" for r in train_rows) / len(train_rows)
            q_low = (1.0 - train_draw_rate) / 2.0
            q_high = (1.0 + train_draw_rate) / 2.0
            lower = u.quantile(train_scores, q_low)
            upper = u.quantile(train_scores, q_high)
            decile_cuts = [u.quantile(train_scores, q / 10.0) for q in range(1, 10)]

            fold_records = []
            for r in test_rows:
                raw = w.side_features(r.x)
                xt = [1.0] + [(raw[j] - med[j]) / scale[j] for j in range(d)]
                score = dot(beta, xt)
                dec = bisect_right(decile_cuts, score)
                if dec < 0 or dec > 9:
                    raise RuntimeError(f"INVALID_DECILE:{comp}:{season}:{r.key}:{dec}")
                central = lower <= score <= upper
                rec = {
                    "competition": comp,
                    "season": season,
                    "key": r.key,
                    "date": r.dt.isoformat(),
                    "actual": r.label,
                    "latent_score": score,
                    "decile": dec,
                    "central": central,
                    "train_n": len(train_rows),
                    "train_draw_rate": train_draw_rate,
                    "central_q_low": q_low,
                    "central_q_high": q_high,
                    "central_lower": lower,
                    "central_upper": upper,
                }
                fold_records.append(rec)
                scored_records.append(rec)

            agg = aggregate_records(fold_records)
            fold_metrics.append({
                "competition": comp,
                "season": season,
                "test_start": test_start.isoformat(),
                "train_n": len(train_rows),
                "test_n": len(test_rows),
                "train_draw_rate": train_draw_rate,
                "central_q_low": q_low,
                "central_q_high": q_high,
                "central_lower": lower,
                "central_upper": upper,
                "latent_beta": beta,
                **agg,
                "deciles": decile_summary(fold_records),
            })

    if not fold_metrics:
        raise RuntimeError("NO_ELIGIBLE_ROLLING_FOLDS")

    pooled = aggregate_records(scored_records)
    pooled_deciles = decile_summary(scored_records)
    fold_positive = sum((f["central_draw_lift"] or 0.0) > 0.0 for f in fold_metrics)
    fold_sign_rate = fold_positive / len(fold_metrics)

    by_comp_records: dict[str, list[dict]] = defaultdict(list)
    by_comp_folds: dict[str, int] = defaultdict(int)
    for f in fold_metrics:
        by_comp_folds[f["competition"]] += 1
    for r in scored_records:
        by_comp_records[r["competition"]].append(r)
    competition_aggregates = []
    eligible_comp_for_sign = []
    for comp in sorted(by_comp_records):
        agg = aggregate_records(by_comp_records[comp])
        item = {"competition": comp, "eligible_folds": by_comp_folds[comp], **agg}
        competition_aggregates.append(item)
        if by_comp_folds[comp] >= 2:
            eligible_comp_for_sign.append(item)
    comp_positive = sum((x["central_draw_lift"] or 0.0) > 0.0 for x in eligible_comp_for_sign)
    comp_sign_rate = comp_positive / len(eligible_comp_for_sign) if eligible_comp_for_sign else 0.0

    central_decile_draw = group_rate(scored_records, {4, 5}, "D")
    outer_decile_draw = group_rate(scored_records, {0, 1, 8, 9}, "D")
    central_decile_lift = (central_decile_draw - outer_decile_draw) if central_decile_draw is not None and outer_decile_draw is not None else None

    top_h = group_rate(scored_records, {7, 8, 9}, "H")
    bottom_h = group_rate(scored_records, {0, 1, 2}, "H")
    bottom_a = group_rate(scored_records, {0, 1, 2}, "A")
    top_a = group_rate(scored_records, {7, 8, 9}, "A")
    h_direction_gap = (top_h - bottom_h) if top_h is not None and bottom_h is not None else None
    a_direction_gap = (bottom_a - top_a) if bottom_a is not None and top_a is not None else None

    gate = {
        "pooled_central_draw_lift_ge_0_03": pooled["central_draw_lift"] is not None and pooled["central_draw_lift"] >= 0.03,
        "fold_positive_lift_rate_ge_0_60": fold_sign_rate >= 0.60,
        "competition_positive_lift_rate_ge_0_60": comp_sign_rate >= 0.60,
        "central_decile_draw_lift_ge_0_02": central_decile_lift is not None and central_decile_lift >= 0.02,
        "axis_direction_sanity": h_direction_gap is not None and a_direction_gap is not None and h_direction_gap >= 0.20 and a_direction_gap >= 0.20,
    }
    passed = all(gate.values())
    terminal = "PASS_R40F_STABLE_DRAW_CENTRALITY" if passed else "FAIL_R40F_DRAW_CENTRALITY_NOT_STABLE"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(rows),
        "eligible_fold_count": len(fold_metrics),
        "skipped_fold_count": len(skipped_folds),
        "competition_count": len(set(f["competition"] for f in fold_metrics)),
        "competitions_with_at_least_two_folds": len(eligible_comp_for_sign),
        "rows_scored": len(scored_records),
        "pooled": pooled,
        "fold_positive_lift_count": fold_positive,
        "fold_positive_lift_rate": fold_sign_rate,
        "competition_positive_lift_count": comp_positive,
        "competition_positive_lift_rate": comp_sign_rate,
        "central_decile_draw_rate": central_decile_draw,
        "outer_decile_draw_rate": outer_decile_draw,
        "central_decile_draw_lift": central_decile_lift,
        "top30_H_rate": top_h,
        "bottom30_H_rate": bottom_h,
        "H_direction_gap": h_direction_gap,
        "bottom30_A_rate": bottom_a,
        "top30_A_rate": top_a,
        "A_direction_gap": a_direction_gap,
        "pooled_deciles": pooled_deciles,
        "fold_metrics": fold_metrics,
        "competition_aggregates": competition_aggregates,
        "skipped_folds": skipped_folds,
        "gate": gate,
        "hard_boundaries": hard,
        "interpretation_boundary": "Retrospective rolling structural diagnostic only. It is not a probability model or formal prediction track and consumes no random fixed100 sample. formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r40f_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r40f_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal,
        "passed": passed,
        "eligible_fold_count": len(fold_metrics),
        "competition_count": out["competition_count"],
        "competitions_with_at_least_two_folds": len(eligible_comp_for_sign),
        "rows_scored": len(scored_records),
        "pooled": pooled,
        "fold_positive_lift_rate": fold_sign_rate,
        "competition_positive_lift_rate": comp_sign_rate,
        "central_decile_draw_rate": central_decile_draw,
        "outer_decile_draw_rate": outer_decile_draw,
        "central_decile_draw_lift": central_decile_lift,
        "H_direction_gap": h_direction_gap,
        "A_direction_gap": a_direction_gap,
        "gate": gate,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
