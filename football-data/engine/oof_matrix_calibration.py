#!/usr/bin/env python3
"""Leakage-safe OOF calibration helpers for the V4.6.x unified score matrix.

The calibrator applies one scalar temperature to the entire joint score
matrix. This keeps one coherent matrix as the source of 1X2, total goals,
BTTS, Asian-line settlements and score rankings. Calibration artifacts are
trained only from out-of-fold predictions and are independently rolling-
validated before they are allowed into the single-match runtime.
"""
from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from platform_core import (
    ROOT,
    PlatformError,
    derive_score_marginals,
    load_json,
    parse_iso_datetime,
    score_matrix_rows,
    settle_home_handicap,
    settle_over_total,
    sha256_file,
    sha256_json,
    top_scores,
)

EPSILON = 1e-15
CALIBRATOR_FILENAME = "oof_matrix_calibrator.json"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
CALIBRATION_MODULE_PATH = Path(__file__).resolve()
SOURCE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
CALIBRATION_REPORT_ROOT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461"


def calibrator_path(competition_id: str) -> Path:
    return MODEL_ROOT / competition_id / CALIBRATOR_FILENAME


def temperature_scale_matrix(matrix: list[dict[str, Any]], temperature: float) -> list[dict[str, float | int]]:
    """Scale every score-cell probability with q_i proportional to p_i ** (1 / T)."""
    temperature = float(temperature)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise PlatformError(f"invalid calibration temperature: {temperature!r}")
    rows = list(score_matrix_rows(matrix))
    if not rows:
        raise PlatformError("cannot calibrate an empty score matrix")
    scaled_logs = [math.log(max(EPSILON, probability)) / temperature for _, _, probability in rows]
    maximum = max(scaled_logs)
    weights = [math.exp(value - maximum) for value in scaled_logs]
    denominator = sum(weights)
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise PlatformError("matrix calibration normalization failed")
    return [
        {"home_goals": home, "away_goals": away, "probability": weight / denominator}
        for (home, away, _), weight in zip(rows, weights)
    ]


def load_oof_matrix_calibrator(competition_id: str) -> tuple[Path, dict[str, Any]] | None:
    path = calibrator_path(competition_id)
    if not path.exists():
        return None
    return path, load_json(path)


def _derive_line_market(matrix: list[dict[str, Any]], line: float, settlement_fn) -> dict[str, float]:
    result = {"win": 0.0, "push": 0.0, "loss": 0.0}
    for home, away, probability in score_matrix_rows(matrix):
        settlement = settlement_fn(home, away, line)
        for key in result:
            result[key] += probability * settlement[key]
    return result


def _conditional_goal_difference_by_total(matrix: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for home, away, probability in score_matrix_rows(matrix):
        grouped[home + away].append((home - away, probability))
    output: dict[str, dict[str, float]] = {}
    for total, items in sorted(grouped.items()):
        total_probability = sum(probability for _, probability in items)
        if total_probability <= 0.0:
            continue
        distribution: Counter[str] = Counter()
        for difference, probability in items:
            distribution[str(difference)] += probability / total_probability
        output[str(total)] = {
            key: float(value)
            for key, value in sorted(distribution.items(), key=lambda item: int(item[0]))
        }
    return output


def _minimum_score_set(matrix: list[dict[str, Any]], target: float) -> dict[str, Any]:
    ranking = top_scores(matrix, len(matrix))
    cumulative = 0.0
    selected = []
    for item in ranking:
        selected.append(item)
        cumulative += float(item["probability"])
        if cumulative + 1e-12 >= target:
            break
    return {
        "target": target,
        "size": len(selected),
        "cumulative_probability": cumulative,
        "scores": selected,
    }


def _set_unavailable(calculation: dict[str, Any], reason: str) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    output.setdefault("module_states", {})["oof_matrix_calibration"] = "不可用"
    output["calibration_audit"] = {
        "method": "full_matrix_temperature_scaling",
        "status": "不可用",
        "reason": reason,
    }
    return output


def apply_oof_matrix_calibration(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    """Apply a replay-safe validated OOF full-matrix calibrator and rebuild all marginals."""
    competition_id = context.get("match_identity", {}).get("competition_id")
    if not competition_id:
        raise PlatformError("match identity is missing competition_id for OOF calibration")
    loaded = load_oof_matrix_calibrator(str(competition_id))
    if loaded is None:
        return _set_unavailable(calculation, "calibrator artifact missing")
    path, artifact = loaded
    if artifact.get("operational_status") != "OOF_MATRIX_CALIBRATOR_AVAILABLE" or artifact.get("enabled") is not True:
        return _set_unavailable(calculation, f"calibrator not operational: {artifact.get('operational_status')}")

    current_calibration_sha = sha256_file(CALIBRATION_MODULE_PATH)
    if artifact.get("calibration_code_sha256") != current_calibration_sha:
        return _set_unavailable(calculation, "calibration code hash mismatch")
    model_engine_sha = (
        calculation.get("model_audit", {}).get("audit", {}).get("engine_sha256")
        or calculation.get("model_audit", {}).get("engine_sha256")
    )
    if not model_engine_sha or artifact.get("engine_sha256") != model_engine_sha:
        return _set_unavailable(calculation, "calibrator engine hash mismatch")

    source_report_path = SOURCE_REPORT_ROOT / f"{competition_id}.json"
    calibration_report_path = CALIBRATION_REPORT_ROOT / f"{competition_id}.json"
    if not source_report_path.exists() or not calibration_report_path.exists():
        return _set_unavailable(calculation, "calibration source/report artifact missing")
    source_report_sha = sha256_file(source_report_path)
    if artifact.get("source_nested_backtest_report_sha256") != source_report_sha:
        return _set_unavailable(calculation, "calibrator stale versus current nested-backtest report")
    calibration_report = load_json(calibration_report_path)
    if artifact.get("calibration_report_sha256") != sha256_json(calibration_report):
        return _set_unavailable(calculation, "calibration report hash mismatch")

    target_season = str(calculation.get("model_audit", {}).get("season") or "")
    season_map = artifact.get("season_calibrators")
    season_calibrator = season_map.get(target_season) if isinstance(season_map, dict) else None
    if not isinstance(season_calibrator, dict):
        return _set_unavailable(calculation, f"no point-in-time OOF calibrator for target season {target_season}")
    training_max_date = season_calibrator.get("training_max_date")
    cutoff = parse_iso_datetime(context.get("match_identity", {}).get("freeze_time_utc"), "freeze_time_utc")
    if training_max_date:
        try:
            training_date = datetime.fromisoformat(str(training_max_date)).date()
        except ValueError:
            return _set_unavailable(calculation, "invalid calibrator training_max_date")
        if training_date >= cutoff.date():
            return _set_unavailable(calculation, "calibrator training horizon is not strictly before prediction cutoff")

    raw_matrix = calculation.get("probabilities", {}).get("score_matrix")
    if not isinstance(raw_matrix, list) or not raw_matrix:
        return _set_unavailable(calculation, "base unified score matrix missing")

    temperature = float(season_calibrator.get("temperature", 1.0))
    calibrated_matrix = temperature_scale_matrix(raw_matrix, temperature)
    raw_marginals = derive_score_marginals(raw_matrix)
    marginals = derive_score_marginals(calibrated_matrix)
    if abs(marginals["probability_sum"] - 1.0) > 1e-10:
        raise PlatformError("OOF calibrated matrix failed probability conservation")

    output = copy.deepcopy(calculation)
    output.setdefault("module_states", {})["oof_matrix_calibration"] = "通过"
    output["probabilities"] = {
        "one_x_two": marginals["1x2"],
        "total_goals": marginals["total_goals"],
        "btts_yes": marginals["btts_yes"],
        "score_matrix": calibrated_matrix,
    }

    derived = output.get("derived_markets") or {}
    if isinstance(derived.get("home_handicap"), dict) and isinstance(derived["home_handicap"].get("line"), (int, float)):
        line = float(derived["home_handicap"]["line"])
        derived["home_handicap"] = {"line": line, **_derive_line_market(calibrated_matrix, line, settle_home_handicap)}
    if isinstance(derived.get("over_total"), dict) and isinstance(derived["over_total"].get("line"), (int, float)):
        line = float(derived["over_total"]["line"])
        derived["over_total"] = {"line": line, **_derive_line_market(calibrated_matrix, line, settle_over_total)}
    output["derived_markets"] = derived

    ranking = top_scores(calibrated_matrix, 10)
    total_rank = sorted(marginals["total_goals"].items(), key=lambda item: (-item[1], item[0]))
    score_sets = {
        "80": _minimum_score_set(calibrated_matrix, 0.80),
        "90": _minimum_score_set(calibrated_matrix, 0.90),
    }
    output["conditional_goal_difference_audit"] = _conditional_goal_difference_by_total(calibrated_matrix)
    output["score_set_audit"] = score_sets

    conclusions = output.setdefault("conclusions", {})
    direction = max(marginals["1x2"], key=marginals["1x2"].get)
    matrix_publishable = output.get("module_states", {}).get("unified_score_matrix") == "通过"
    conclusions.update({
        "result_direction": direction,
        "result_text": (
            f"90分钟OOF校准后模型概率：主胜{marginals['1x2']['home']:.1%}、"
            f"平局{marginals['1x2']['draw']:.1%}、客胜{marginals['1x2']['away']:.1%}。"
        ),
        "total_goals_text": f"OOF完整矩阵校准后总进球中心：{total_rank[0][0]}球；0—7+边际由同一最终矩阵汇总。",
        "total_goals_primary": total_rank[0][0],
        "total_goals_secondary": total_rank[1][0],
        "top_score": ranking[0]["score"] if matrix_publishable else None,
        "second_score": ranking[1]["score"] if matrix_publishable and len(ranking) > 1 else None,
        "top3_cumulative": sum(item["probability"] for item in ranking[:3]) if matrix_publishable else None,
        "top1_top2_gap": (ranking[0]["probability"] - ranking[1]["probability"]) if matrix_publishable and len(ranking) > 1 else None,
        "score_set_80": score_sets["80"],
        "score_set_90": score_sets["90"],
        "score_text": f"模型中心比分 {ranking[0]['score']}；EXACT独立门控未通过。" if matrix_publishable else "精确比分不可用。",
        "score_label": "模型中心比分" if matrix_publishable else "精确比分不可用",
    })
    confidence = conclusions.get("confidence_grade", "D")
    price_status = conclusions.get("price_status", "No Bet")
    conclusions["final_line"] = (
        f"{direction}；可信等级{confidence}；{price_status}；"
        + ("比分标签为模型中心比分。" if matrix_publishable else "精确比分不可用。")
    )

    output["calibration_audit"] = {
        "method": artifact.get("method"),
        "status": "通过",
        "artifact_path": str(path.relative_to(ROOT)),
        "artifact_sha256": sha256_file(path),
        "calibration_code_sha256": current_calibration_sha,
        "engine_sha256": artifact.get("engine_sha256"),
        "source_report_sha256": source_report_sha,
        "source_report_hash_verified": True,
        "calibration_report_hash_verified": True,
        "target_season": target_season,
        "training_max_date": training_max_date,
        "mode": season_calibrator.get("mode"),
        "temperature": temperature,
        "fit_predictions": season_calibrator.get("training_predictions"),
        "rolling_validation_predictions": season_calibrator.get("rolling_validation_predictions"),
        "rolling_validation": season_calibrator.get("rolling_validation"),
        "guardrail_passed": season_calibrator.get("guardrail_passed"),
        "raw_probability_sum": raw_marginals["probability_sum"],
        "calibrated_probability_sum": marginals["probability_sum"],
        "probability_sum_residual": marginals["probability_sum"] - 1.0,
    }
    return output
