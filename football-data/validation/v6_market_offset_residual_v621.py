#!/usr/bin/env python3
"""V6.2.1 market-offset residual challenge.

Instead of averaging the V6 model with the market, this model treats de-vigged closing
1X2 probabilities as the baseline and fits only residual corrections from leakage-safe
football features. Selection is performed on 2024/25 and evaluated once on 2025/26.
Research only; no CURRENT/runtime mutation.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
import v6_direct_outcome_draw_boundary_v601 as v601
import v6_market_residual_fusion_v620 as v620
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

OUT = ROOT / "manifests" / "v6_market_offset_residual_v621_status.json"
SEASON_CODES = {"2022/23": "2223", "2023/24": "2324", "2024/25": "2425", "2025/26": "2526"}
L2_GRID = (0.1, 1.0, 10.0, 100.0)
DRAW_RATIO_GRID = (0.75, 0.80, 0.85, 0.90, 0.95, 1.00)
EPS = 1e-12


def _clip(p: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, float(p)))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _prepare_residual_features(row: dict[str, Any]) -> None:
    market = row["market"]
    formal = row["formal"]
    # Use only pre-match/leakage-safe information. Market is the offset, not a free feature.
    row["draw_res_x"] = list(row["draw_x"]) + [
        float(formal["draw"]) - float(market["draw"]),
        abs(float(formal["home"]) - float(market["home"])),
        abs(float(formal["away"]) - float(market["away"])),
        max(float(market["home"]), float(market["away"])) - float(market["draw"]),
    ]
    market_side = float(market["home"]) / max(EPS, float(market["home"]) + float(market["away"]))
    formal_side = float(formal["home"]) / max(EPS, float(formal["home"]) + float(formal["away"]))
    row["side_res_x"] = list(row["side_x"]) + [
        formal_side - market_side,
        abs(formal_side - market_side),
        abs(float(formal["draw"]) - float(market["draw"])),
    ]
    row["draw_offset"] = _logit(float(market["draw"]))
    row["side_offset"] = _logit(market_side)


def _fit_offset_binary(rows: list[dict[str, Any]], x_key: str, y_key: str, offset_key: str, l2: float) -> dict[str, Any]:
    if not rows:
        raise PlatformError(f"no rows for {x_key}")
    d = len(rows[0][x_key])
    means = [0.0] * d
    scales = [1.0] * d
    for j in range(1, d):
        means[j] = sum(float(r[x_key][j]) for r in rows) / len(rows)
        variance = sum((float(r[x_key][j]) - means[j]) ** 2 for r in rows) / len(rows)
        scales[j] = max(1e-6, math.sqrt(variance))

    def sx(row: dict[str, Any]) -> list[float]:
        raw = row[x_key]
        return [1.0] + [(float(raw[j]) - means[j]) / scales[j] for j in range(1, d)]

    theta = [0.0] * d
    converged = False
    objective = None
    grad_norm = None
    for iteration in range(1, 61):
        gradient = [0.0] * d
        hessian = [[0.0] * d for _ in range(d)]
        current = 0.5 * l2 * sum(v * v for v in theta)
        for row in rows:
            x = sx(row)
            y = float(row[y_key])
            eta = float(row[offset_key]) + sum(theta[j] * x[j] for j in range(d))
            p = _sigmoid(eta)
            current -= y * math.log(max(EPS, p)) + (1.0 - y) * math.log(max(EPS, 1.0 - p))
            for j in range(d):
                gradient[j] += (p - y) * x[j]
                for k in range(d):
                    hessian[j][k] += p * (1.0 - p) * x[j] * x[k]
        for j in range(d):
            gradient[j] += l2 * theta[j]
            hessian[j][j] += l2
        grad_norm = max(abs(v) for v in gradient)
        if grad_norm < 1e-7:
            converged = True
            objective = current
            break
        step = base._solve(hessian, gradient)
        scale = 1.0
        accepted = False
        for _ in range(25):
            cand = [theta[j] - scale * step[j] for j in range(d)]
            cand_obj = 0.5 * l2 * sum(v * v for v in cand)
            for row in rows:
                x = sx(row)
                y = float(row[y_key])
                eta = float(row[offset_key]) + sum(cand[j] * x[j] for j in range(d))
                p = _sigmoid(eta)
                cand_obj -= y * math.log(max(EPS, p)) + (1.0 - y) * math.log(max(EPS, 1.0 - p))
            if math.isfinite(cand_obj) and cand_obj <= current + 1e-10:
                theta = cand
                objective = cand_obj
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            raise PlatformError(f"offset logistic line search failed for {x_key}")
        if max(abs(scale * v) for v in step) < 1e-8:
            converged = True
            break
    if not converged:
        raise PlatformError(f"offset logistic failed to converge for {x_key}")
    return {
        "theta": theta,
        "means": means,
        "scales": scales,
        "l2": l2,
        "iterations": iteration,
        "objective": objective,
        "max_abs_gradient": grad_norm,
        "training_count": len(rows),
    }


def _predict_offset(model: dict[str, Any], row: dict[str, Any], x_key: str, offset_key: str) -> float:
    raw = row[x_key]
    theta = [float(v) for v in model["theta"]]
    means = [float(v) for v in model["means"]]
    scales = [float(v) for v in model["scales"]]
    x = [1.0] + [(float(raw[j]) - means[j]) / scales[j] for j in range(1, len(raw))]
    return _sigmoid(float(row[offset_key]) + sum(theta[j] * x[j] for j in range(len(theta))))


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    draw_model = _fit_offset_binary(rows, "draw_res_x", "draw_y", "draw_offset", l2)
    decisive = [r for r in rows if r["is_decisive"]]
    side_model = _fit_offset_binary(decisive, "side_res_x", "side_y", "side_offset", l2)
    return {"draw_model": draw_model, "side_model": side_model, "l2": l2}


def _prob(row: dict[str, Any], models: dict[str, Any]) -> dict[str, float]:
    pd = _clip(_predict_offset(models["draw_model"], row, "draw_res_x", "draw_offset"))
    ph_cond = _clip(_predict_offset(models["side_model"], row, "side_res_x", "side_offset"))
    rem = 1.0 - pd
    return {"home": rem * ph_cond, "draw": pd, "away": rem * (1.0 - ph_cond)}


def _score(rows: list[dict[str, Any]], prob_key: str, draw_ratio: float) -> dict[str, Any]:
    count = hits = 0
    brier = rps = logloss = 0.0
    for row in rows:
        q = row[prob_key]
        pick = v601._pick(q, draw_ratio)
        truth = row["actual_result"]
        hits += int(pick == truth)
        count += 1
        brier += sum((q[k] - (1.0 if truth == k else 0.0)) ** 2 for k in base.CLASSES)
        truth_vec = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[truth]
        c1 = q["home"] - truth_vec[0]
        c2 = q["home"] + q["draw"] - truth_vec[0] - truth_vec[1]
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(EPS, q[truth]))
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "mean_brier": brier / count if count else None,
        "mean_rps": rps / count if count else None,
        "mean_log_loss": logloss / count if count else None,
    }


def _attach_market(cid: str, code: str, rows_by_season: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    output: dict[str, list[dict[str, Any]]] = {}
    audit: dict[str, Any] = {}
    for season in ("2022/23", "2023/24", "2024/25", "2025/26"):
        csv_rows, url = v620._download_csv(code, SEASON_CODES[season])
        matched, stats = v620._match_market(cid, rows_by_season[season], csv_rows)
        for row in matched:
            _prepare_residual_features(row)
        output[season] = matched
        audit[season] = {"url": url, "csv_rows": len(csv_rows), "model_rows": len(rows_by_season[season]), "matched": len(matched), "match_stats": stats}
    return output, audit


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    formal_status = load_json(base.FORMAL_STATUS)
    domains = sorted((formal_status.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 formal domains, found {len(domains)}")

    market_rows_by_domain: dict[str, dict[str, list[dict[str, Any]]]] = {}
    source_audit: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid, code in v620.LEAGUES.items():
        try:
            report = load_json(base.REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
            if seasons != ["2022/23", "2023/24", "2024/25", "2025/26"]:
                raise PlatformError(f"unexpected season roles for {cid}: {seasons}")
            built = v620._build_domain_rows_with_identity(cid, seasons)
            attached, audit = _attach_market(cid, code, built)
            market_rows_by_domain[cid] = attached
            source_audit[cid] = audit
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        payload = {"schema_version": "V6.2.1-market-offset-residual-r1", "generated_at_utc": generated.isoformat(), "status": "FAIL_DATA", "failures": failures}
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fit_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    for cid in v620.LEAGUES:
        fit_rows.extend(market_rows_by_domain[cid]["2022/23"])
        fit_rows.extend(market_rows_by_domain[cid]["2023/24"])
        validation_rows.extend(market_rows_by_domain[cid]["2024/25"])
        holdout_rows.extend(market_rows_by_domain[cid]["2025/26"])

    for rows in (fit_rows, validation_rows, holdout_rows):
        for row in rows:
            row["market_probability"] = row["market"]

    market_validation_candidates = [_score(validation_rows, "market_probability", dr) for dr in DRAW_RATIO_GRID]
    market_validation = min(market_validation_candidates, key=lambda x: (float(x["mean_log_loss"]), -float(x["accuracy"])))
    market_holdout = _score(holdout_rows, "market_probability", 1.0)

    candidates: list[dict[str, Any]] = []
    fit_models: dict[float, dict[str, Any]] = {}
    for l2 in L2_GRID:
        try:
            model = _fit_models(fit_rows, l2)
            fit_models[l2] = model
            work = []
            for row in validation_rows:
                item = dict(row)
                item["residual_probability"] = _prob(row, model)
                work.append(item)
            for draw_ratio in DRAW_RATIO_GRID:
                score = _score(work, "residual_probability", draw_ratio)
                proper_safe = (
                    float(score["mean_brier"]) <= float(market_validation["mean_brier"]) + 1e-12
                    and float(score["mean_rps"]) <= float(market_validation["mean_rps"]) + 1e-12
                    and float(score["mean_log_loss"]) <= float(market_validation["mean_log_loss"]) + 1e-12
                )
                candidates.append({"l2": l2, "draw_ratio": draw_ratio, "validation": score, "proper_scores_nonworse_than_market": proper_safe})
        except Exception as exc:
            candidates.append({"l2": l2, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "proper_scores_nonworse_than_market": False})

    eligible = [c for c in candidates if c.get("proper_scores_nonworse_than_market") and c.get("validation")]
    if not eligible:
        selected = None
        result_status = "NO_PROPER_SCORE_SAFE_RESIDUAL"
        residual_holdout = None
        refit_audit = None
    else:
        eligible.sort(key=lambda c: (-float(c["validation"]["accuracy"]), float(c["validation"]["mean_log_loss"]), float(c["l2"]), float(c["draw_ratio"])))
        selected = eligible[0]
        refit = _fit_models(fit_rows + validation_rows, float(selected["l2"]))
        holdout_work = []
        for row in holdout_rows:
            item = dict(row)
            item["residual_probability"] = _prob(row, refit)
            holdout_work.append(item)
        residual_holdout = _score(holdout_work, "residual_probability", float(selected["draw_ratio"]))
        refit_audit = refit
        result_status = "PASS"

    result: dict[str, Any] = {
        "status": result_status,
        "market_validation": market_validation,
        "market_holdout": market_holdout,
        "selected_candidate": selected,
        "residual_holdout": residual_holdout,
        "refit_audit": refit_audit,
    }
    if residual_holdout is not None:
        result["accuracy_gain_pp_vs_market"] = 100.0 * (float(residual_holdout["accuracy"]) - float(market_holdout["accuracy"]))
        result["proper_score_delta_vs_market"] = {
            "brier": float(residual_holdout["mean_brier"]) - float(market_holdout["mean_brier"]),
            "rps": float(residual_holdout["mean_rps"]) - float(market_holdout["mean_rps"]),
            "log_loss": float(residual_holdout["mean_log_loss"]) - float(market_holdout["mean_log_loss"]),
        }

    payload = {
        "schema_version": "V6.2.1-market-offset-residual-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "scope": {
            "competitions": list(v620.LEAGUES),
            "fit_seasons": ["2022/23", "2023/24"],
            "selection_validation_season": "2024/25",
            "development_holdout_season": "2025/26",
            "model": "market-logit offset + leakage-safe football residual features",
            "l2_grid": list(L2_GRID),
            "draw_ratio_grid": list(DRAW_RATIO_GRID),
        },
        "row_counts": {"fit": len(fit_rows), "validation": len(validation_rows), "holdout": len(holdout_rows)},
        "source_audit": source_audit,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "holdout_used_for_selection": False,
            "market_is_baseline_offset": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
