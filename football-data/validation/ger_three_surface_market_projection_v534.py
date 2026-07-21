#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import retrospective_market_matrix_projection_v530 as base
from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    canonical_team_name,
    load_aliases,
    read_processed_matches,
    score_matrix_rows,
    settle_home_handicap,
)

CID = "GER_Bundesliga"
SEASON = "2025/26"
OUT = ROOT / "manifests" / "ger_three_surface_market_projection_v534_status.json"
TOL = 1e-10
MAX_ITER = 5000
AH_BISECT_ITER = 160
BOOTSTRAP_DRAWS = 1600
BLOCK_SIZE = 20
SEED = 5342026
EPS = 1e-15


def _positive_decimal(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 1.0:
        return None
    return number


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ah_market_lookup() -> dict[tuple[str, str, str], dict[str, float]]:
    path = ROOT / "processed" / CID / "2025-26.csv"
    aliases = load_aliases()
    output = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("season") or row.get("Season") or "") != SEASON:
                continue
            try:
                date = datetime.strptime(str(row["Date"]), "%d/%m/%Y").date().isoformat()
            except Exception:
                continue
            home = canonical_team_name(CID, str(row.get("HomeTeam") or ""), aliases)
            away = canonical_team_name(CID, str(row.get("AwayTeam") or ""), aliases)
            line = _finite(row.get("AHCh"))
            home_odds = _positive_decimal(row.get("AvgCAHH"))
            away_odds = _positive_decimal(row.get("AvgCAHA"))
            if line is None or home_odds is None or away_odds is None:
                continue
            target_w_over_l = math.sqrt((away_odds - 1.0) / (home_odds - 1.0))
            if not math.isfinite(target_w_over_l) or target_w_over_l <= 0.0:
                continue
            output[(date, home, away)] = {
                "line": float(line),
                "home_odds": float(home_odds),
                "away_odds": float(away_odds),
                "target_W_over_L": float(target_w_over_l),
            }
    return output


def _cell_ah_components(h: int, a: int, line: float) -> tuple[float, float]:
    settlement = settle_home_handicap(h, a, line)
    return float(settlement["win"]), float(settlement["loss"])


def _ah_quantities(matrix, line: float) -> dict[str, float]:
    win = loss = 0.0
    for h, a, p in score_matrix_rows(matrix):
        w, l = _cell_ah_components(h, a, line)
        win += p * w
        loss += p * l
    ratio = win / loss if loss > EPS else math.inf
    return {
        "W": win,
        "L": loss,
        "W_over_L": ratio,
        "signed_settlement_expectation": win - loss,
    }


def _ah_moment(matrix, line: float, target_w_over_l: float) -> float:
    q = _ah_quantities(matrix, line)
    return float(q["W"] - target_w_over_l * q["L"])


def _ah_projection(matrix, line: float, target_w_over_l: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    for h, a, p in score_matrix_rows(matrix):
        w, l = _cell_ah_components(h, a, line)
        g = w - target_w_over_l * l
        rows.append((h, a, p, g))

    current = _ah_moment(matrix, line, target_w_over_l)
    if abs(current) <= TOL:
        q = _ah_quantities(matrix, line)
        return base._renormalize(matrix), {
            "theta_ah": 0.0,
            "ah_moment_residual": abs(current),
            "target_W_over_L": target_w_over_l,
            "achieved_W_over_L": q["W_over_L"],
        }

    positive_rows = [(h, a, p, g) for h, a, p, g in rows if p > 0.0]
    if not positive_rows:
        raise PlatformError("AH projection prior has no positive support")
    g_values = [g for _, _, _, g in positive_rows]
    if min(g_values) > 0.0 or max(g_values) < 0.0:
        raise PlatformError("AH settlement-ratio constraint infeasible on prior support")

    def evaluate(theta: float):
        logs = [math.log(p) + theta * g for _, _, p, g in positive_rows]
        anchor = max(logs)
        weights = [math.exp(value - anchor) for value in logs]
        z = sum(weights)
        probs = [weight / z for weight in weights]
        moment = sum(prob * row[3] for prob, row in zip(probs, positive_rows))
        return moment, probs

    lo, hi = -80.0, 80.0
    lo_m, _ = evaluate(lo)
    hi_m, _ = evaluate(hi)
    if lo_m > 0.0 or hi_m < 0.0:
        raise PlatformError(f"AH exponential tilt failed to bracket zero: lo={lo_m} hi={hi_m}")
    for _ in range(AH_BISECT_ITER):
        mid = (lo + hi) / 2.0
        moment, _ = evaluate(mid)
        if moment < 0.0:
            lo = mid
        else:
            hi = mid
    theta = (lo + hi) / 2.0
    moment, probs = evaluate(theta)

    probability_map = {(h, a): prob for prob, (h, a, _p, _g) in zip(probs, positive_rows)}
    out = []
    for h, a, p, _g in rows:
        out.append({
            "home_goals": h,
            "away_goals": a,
            "probability": probability_map.get((h, a), 0.0) if p > 0.0 else 0.0,
        })
    out = base._renormalize(out)
    q = _ah_quantities(out, line)
    return out, {
        "theta_ah": theta,
        "ah_moment_residual": abs(q["W"] - target_w_over_l * q["L"]),
        "target_W_over_L": target_w_over_l,
        "achieved_W_over_L": q["W_over_L"],
        "W": q["W"],
        "L": q["L"],
    }


def _three_surface_project(prior, one, ou, line: float, target_w_over_l: float):
    candidate = base._renormalize(prior)
    audit = None
    for iteration in range(1, MAX_ITER + 1):
        candidate = base._scale_partition(candidate, base._outcome_group, one, "1x2")
        candidate = base._scale_partition(candidate, base._ou_group, ou, "ou25")
        candidate, ah_audit = _ah_projection(candidate, line, target_w_over_l)
        constraint = base._constraint_residual(candidate, one, ou)
        ah_residual = float(ah_audit["ah_moment_residual"])
        max_residual = max(float(constraint["max_residual"]), ah_residual)
        audit = {
            "iterations": iteration,
            "one_x_two_max_residual": constraint["one_x_two_max_residual"],
            "ou25_max_residual": constraint["ou25_max_residual"],
            **ah_audit,
            "max_constraint_residual": max_residual,
        }
        if max_residual <= TOL:
            probability_sum = sum(p for _h, _a, p in score_matrix_rows(candidate))
            audit.update({
                "converged": True,
                "probability_sum_residual": abs(probability_sum - 1.0),
                "kl_from_formal_matrix": base._kl(candidate, prior),
            })
            return candidate, audit
    raise PlatformError(f"three-surface cyclic KL did not converge; last={audit}")


def _actual_home_net(hg: int, ag: int, line: float) -> float:
    s = settle_home_handicap(hg, ag, line)
    return float(s["win"]) - float(s["loss"])


def _selected_ah_metrics(matrix, match, line: float, home_odds: float, away_odds: float) -> dict[str, float]:
    quantities = _ah_quantities(matrix, line)
    side = "home" if quantities["signed_settlement_expectation"] >= 0.0 else "away"
    home_net = _actual_home_net(int(match.home_goals), int(match.away_goals), line)
    selected_net = home_net if side == "home" else -home_net
    settlement = settle_home_handicap(int(match.home_goals), int(match.away_goals), line)
    if side == "home":
        win_fraction = float(settlement["win"])
        loss_fraction = float(settlement["loss"])
        odds = home_odds
    else:
        win_fraction = float(settlement["loss"])
        loss_fraction = float(settlement["win"])
        odds = away_odds
    realized_profit = win_fraction * (odds - 1.0) - loss_fraction
    return {
        "ah_selected_settlement_net": selected_net,
        "ah_selected_settlement_score": (selected_net + 1.0) / 2.0,
        "ah_positive_settlement": 1.0 if selected_net > 1e-12 else 0.0,
        "ah_nonnegative_settlement": 1.0 if selected_net >= -1e-12 else 0.0,
        "ah_selected_closing_profit": realized_profit,
    }


def _metrics(matrix, match, ah):
    return {
        **base._metrics(matrix, match),
        **_selected_ah_metrics(matrix, match, ah["line"], ah["home_odds"], ah["away_odds"]),
    }


def _summary(rows, prefix: str):
    metrics = [
        "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps",
        "ou_accuracy", "ou_brier", "ou_log",
        "joint_log", "score_top1", "score_top3",
        "total_top1", "total_top2", "total_rps",
        "ah_selected_settlement_net", "ah_selected_settlement_score",
        "ah_positive_settlement", "ah_nonnegative_settlement", "ah_selected_closing_profit",
    ]
    return {metric: mean(float(row[f"{prefix}_{metric}"]) for row in rows) for metric in metrics}


def _bootstrap(rows, candidate: str, baseline: str, metric: str, seed: int):
    ordered = sorted(rows, key=lambda row: (row["date"], row["match_key"]))
    blocks = [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]
    point = mean(float(row[f"{candidate}_{metric}"]) - float(row[f"{baseline}_{metric}"]) for row in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(BOOTSTRAP_DRAWS):
        sample = []
        for _ in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        values.append(mean(float(row[f"{candidate}_{metric}"]) - float(row[f"{baseline}_{metric}"]) for row in sample))
    values.sort()
    return {
        "candidate_minus_baseline": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "blocks": len(blocks),
        "draws": BOOTSTRAP_DRAWS,
    }


def main() -> int:
    formal_report = json.loads((REPORT_ROOT / f"{CID}.json").read_text(encoding="utf-8"))
    fold = _fold_for_season(formal_report, SEASON)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError("missing frozen GER formal parameters")
    temperature, calibration_mode = _target_season_temperature(CID, SEASON)
    all_matches = read_processed_matches(CID)
    targets = [m for m in all_matches if str(m.season) == SEASON]
    market = base._market_lookup(CID)
    ah_market = _ah_market_lookup()

    rows = []
    failures = []
    max_residual = 0.0
    max_probability_residual = 0.0
    max_iterations = 0
    kl_values = []

    for match in targets:
        date = match.date.date().isoformat()
        key = (date, match.home_team, match.away_team)
        ref = market.get(key)
        ah = ah_market.get(key)
        if not ref or ref.get("one_x_two") is None or ref.get("ou25") is None or ah is None:
            continue
        try:
            baseline = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, SEASON, params)
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
            two_surface, two_audit = base._project_1x2_ou(baseline, ref["one_x_two"], ref["ou25"])
            three_surface, three_audit = _three_surface_project(
                baseline, ref["one_x_two"], ref["ou25"], ah["line"], ah["target_W_over_L"]
            )
        except Exception as exc:
            failures.append({
                "date": date,
                "home_team": match.home_team,
                "away_team": match.away_team,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        base_metrics = _metrics(baseline, match, ah)
        two_metrics = _metrics(two_surface, match, ah)
        three_metrics = _metrics(three_surface, match, ah)
        row = {"date": date, "match_key": f"{CID}:{date}:{match.home_team}:{match.away_team}"}
        for prefix, metrics in (("baseline", base_metrics), ("two_surface", two_metrics), ("three_surface", three_metrics)):
            for metric, value in metrics.items():
                if isinstance(value, (int, float)):
                    row[f"{prefix}_{metric}"] = value
        rows.append(row)
        max_residual = max(max_residual, float(three_audit["max_constraint_residual"]))
        max_probability_residual = max(max_probability_residual, float(three_audit["probability_sum_residual"]))
        max_iterations = max(max_iterations, int(three_audit["iterations"]))
        kl_values.append(float(three_audit["kl_from_formal_matrix"]))

    if not rows:
        raise PlatformError("no comparable GER three-surface rows")

    baseline_summary = _summary(rows, "baseline")
    two_summary = _summary(rows, "two_surface")
    three_summary = _summary(rows, "three_surface")

    frozen_point_checks = {
        "one_x_two_brier_nonworse_vs_two_surface": three_summary["one_x_two_brier"] <= two_summary["one_x_two_brier"] + 1e-12,
        "one_x_two_rps_nonworse_vs_two_surface": three_summary["one_x_two_rps"] <= two_summary["one_x_two_rps"] + 1e-12,
        "ou_brier_nonworse_vs_two_surface": three_summary["ou_brier"] <= two_summary["ou_brier"] + 1e-12,
        "joint_log_nonworse_vs_two_surface": three_summary["joint_log"] <= two_summary["joint_log"] + 1e-12,
        "score_top1_nonworse_vs_two_surface": three_summary["score_top1"] + 1e-12 >= two_summary["score_top1"],
        "score_top3_nonworse_vs_two_surface": three_summary["score_top3"] + 1e-12 >= two_summary["score_top3"],
        "total_top1_nonworse_vs_two_surface": three_summary["total_top1"] + 1e-12 >= two_summary["total_top1"],
        "total_top2_nonworse_vs_two_surface": three_summary["total_top2"] + 1e-12 >= two_summary["total_top2"],
        "total_rps_nonworse_vs_two_surface": three_summary["total_rps"] <= two_summary["total_rps"] + 1e-12,
        "ah_settlement_score_improves_vs_two_surface": three_summary["ah_selected_settlement_score"] > two_summary["ah_selected_settlement_score"] + 1e-12,
        "ah_positive_settlement_nonworse_vs_two_surface": three_summary["ah_positive_settlement"] + 1e-12 >= two_summary["ah_positive_settlement"],
        "probability_conservation": max_probability_residual <= TOL,
        "all_three_market_constraints_fit": max_residual <= TOL,
    }

    bootstrap = {
        metric: _bootstrap(rows, "three_surface", "two_surface", metric, SEED + index)
        for index, metric in enumerate([
            "one_x_two_brier", "one_x_two_rps", "ou_brier", "joint_log",
            "score_top1", "score_top3", "total_top1", "total_top2", "total_rps",
            "ah_selected_settlement_score", "ah_positive_settlement", "ah_selected_closing_profit",
        ], start=1)
    }

    payload = {
        "schema_version": "V5.3.4-GER-three-surface-market-projection-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": CID,
        "season": SEASON,
        "target_match_count": len(targets),
        "comparable_prediction_count": len(rows),
        "execution_failure_count": len(failures),
        "execution_failure_examples": failures[:10],
        "baseline": baseline_summary,
        "two_surface_1x2_ou25": two_summary,
        "three_surface_1x2_ou25_ah": three_summary,
        "three_minus_two": {metric: three_summary[metric] - two_summary[metric] for metric in three_summary},
        "bootstrap_three_minus_two": bootstrap,
        "frozen_point_checks": frozen_point_checks,
        "all_frozen_point_checks_pass": all(frozen_point_checks.values()),
        "mean_three_surface_KL_from_formal_matrix": mean(kl_values),
        "max_outer_iterations": max_iterations,
        "max_constraint_residual": max_residual,
        "max_probability_sum_residual": max_probability_residual,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "status": "THREE_SURFACE_ARCHITECTURE_POINT_PASS_RETROSPECTIVE_ONLY" if all(frozen_point_checks.values()) else "THREE_SURFACE_REJECT_KEEP_TWO_SURFACE_PRIMARY_CANDIDATE",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": "AH mapping and all checks were frozen before this result. 2025/26 market prices lack original quote timestamps; no formal promotion or post-hoc retuning is allowed."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
