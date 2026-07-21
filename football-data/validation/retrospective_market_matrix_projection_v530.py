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

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from bayesian_dynamic_state_oof_v500 import _metric_row
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    PlatformError,
    canonical_team_name,
    derive_score_marginals,
    load_aliases,
    read_processed_matches,
    score_matrix_rows,
)

DOMAINS = [
    "ENG_PremierLeague",
    "ESP_LaLiga",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
]
OU_COORDINATION_DOMAINS = {"ENG_PremierLeague", "GER_Bundesliga", "FRA_Ligue1"}
SEASON = "2025/26"
OUT = ROOT / "manifests" / "retrospective_market_matrix_projection_v530_status.json"
EPS = 1e-15
TOL = 1e-12
MAX_ITER = 5000
BOOTSTRAP_DRAWS = 1400
BLOCK_SIZE = 20
SEED = 5302026


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _odds(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 1.0:
        return None
    return number


def _devig(values: dict[str, float]) -> dict[str, float]:
    inverse = {key: 1.0 / float(value) for key, value in values.items()}
    total = sum(inverse.values())
    if total <= 0.0:
        raise PlatformError("invalid market inverse-odds sum")
    return {key: value / total for key, value in inverse.items()}


def _market_lookup(cid: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    path = ROOT / "processed" / cid / "2025-26.csv"
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
            home = canonical_team_name(cid, str(row.get("HomeTeam") or ""), aliases)
            away = canonical_team_name(cid, str(row.get("AwayTeam") or ""), aliases)
            x12_raw = {key: _odds(row.get(field)) for key, field in (
                ("home", "AvgCH"), ("draw", "AvgCD"), ("away", "AvgCA")
            )}
            ou_raw = {key: _odds(row.get(field)) for key, field in (
                ("over", "AvgC>2.5"), ("under", "AvgC<2.5")
            )}
            output[(date, home, away)] = {
                "one_x_two": _devig(x12_raw) if all(value is not None for value in x12_raw.values()) else None,
                "ou25": _devig(ou_raw) if all(value is not None for value in ou_raw.values()) else None,
                "source_path": str(path.relative_to(ROOT)),
            }
    return output


def _outcome_group(h: int, a: int) -> str:
    return "home" if h > a else "draw" if h == a else "away"


def _ou_group(h: int, a: int) -> str:
    return "over" if h + a >= 3 else "under"


def _renormalize(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(cell["probability"]) for cell in matrix)
    if total <= 0.0 or not math.isfinite(total):
        raise PlatformError("market projection produced invalid probability sum")
    return [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "probability": float(cell["probability"]) / total,
        }
        for cell in matrix
    ]


def _scale_partition(matrix: list[dict[str, Any]], grouper, target: dict[str, float], label: str) -> list[dict[str, Any]]:
    current = defaultdict(float)
    for h, a, p in score_matrix_rows(matrix):
        current[grouper(h, a)] += p
    factors = {}
    for key, target_value in target.items():
        mass = float(current.get(key, 0.0))
        if float(target_value) > 0.0 and mass <= 0.0:
            raise PlatformError(f"{label} target {key} positive but prior support mass is zero")
        factors[key] = float(target_value) / mass if mass > 0.0 else 0.0
    out = []
    for h, a, p in score_matrix_rows(matrix):
        out.append({"home_goals": h, "away_goals": a, "probability": p * factors[grouper(h, a)]})
    return _renormalize(out)


def _constraint_residual(matrix: list[dict[str, Any]], one: dict[str, float], ou: dict[str, float] | None) -> dict[str, float]:
    one_current = defaultdict(float)
    ou_current = defaultdict(float)
    for h, a, p in score_matrix_rows(matrix):
        one_current[_outcome_group(h, a)] += p
        ou_current[_ou_group(h, a)] += p
    one_resid = max(abs(float(one_current[key]) - float(one[key])) for key in one)
    ou_resid = 0.0 if ou is None else max(abs(float(ou_current[key]) - float(ou[key])) for key in ou)
    return {
        "one_x_two_max_residual": one_resid,
        "ou25_max_residual": ou_resid,
        "max_residual": max(one_resid, ou_resid),
    }


def _kl(candidate: list[dict[str, Any]], prior: list[dict[str, Any]]) -> float:
    prior_map = {(h, a): p for h, a, p in score_matrix_rows(prior)}
    value = 0.0
    for h, a, q in score_matrix_rows(candidate):
        if q <= 0.0:
            continue
        p = float(prior_map.get((h, a), 0.0))
        if p <= 0.0:
            raise PlatformError(f"candidate creates mass outside prior support at {h}-{a}")
        value += q * math.log(q / p)
    return value


def _project_1x2(prior: list[dict[str, Any]], one: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate = _scale_partition(prior, _outcome_group, one, "1x2")
    residual = _constraint_residual(candidate, one, None)
    return candidate, {
        "method": "minimum_KL_partition_projection_1x2",
        "iterations": 1,
        "converged": residual["max_residual"] <= TOL,
        "kl_from_prior": _kl(candidate, prior),
        **residual,
    }


def _project_1x2_ou(prior: list[dict[str, Any]], one: dict[str, float], ou: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate = _renormalize(prior)
    converged = False
    residual = {"max_residual": math.inf, "one_x_two_max_residual": math.inf, "ou25_max_residual": math.inf}
    iterations = 0
    for iteration in range(1, MAX_ITER + 1):
        candidate = _scale_partition(candidate, _outcome_group, one, "1x2")
        candidate = _scale_partition(candidate, _ou_group, ou, "ou25")
        residual = _constraint_residual(candidate, one, ou)
        iterations = iteration
        if residual["max_residual"] <= TOL:
            converged = True
            break
    if not converged:
        raise PlatformError(f"1x2+OU IPF did not converge after {MAX_ITER}; residual={residual}")
    return candidate, {
        "method": "minimum_KL_IPF_1x2_plus_ou25",
        "iterations": iterations,
        "converged": True,
        "kl_from_prior": _kl(candidate, prior),
        **residual,
    }


def _ou_metrics(matrix: list[dict[str, Any]], match) -> dict[str, float]:
    p_over = sum(p for h, a, p in score_matrix_rows(matrix) if h + a >= 3)
    actual = 1.0 if int(match.home_goals) + int(match.away_goals) >= 3 else 0.0
    p = min(1.0 - EPS, max(EPS, p_over))
    return {
        "ou_accuracy": 1.0 if (p_over >= 0.5) == bool(actual) else 0.0,
        "ou_brier": (p_over - actual) ** 2,
        "ou_log": -(math.log(p) if actual else math.log(1.0 - p)),
    }


def _metrics(matrix: list[dict[str, Any]], match) -> dict[str, Any]:
    return {**_metric_row(matrix, match), **_ou_metrics(matrix, match)}


def _paired_summary(rows: list[dict[str, Any]], candidate: str) -> dict[str, Any]:
    metrics = [
        "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps",
        "joint_log", "score_top1", "score_top3",
        "total_top1", "total_top2", "total_rps",
        "ou_accuracy", "ou_brier", "ou_log",
    ]
    out = {}
    for metric in metrics:
        base = mean(float(row[f"baseline_{metric}"]) for row in rows)
        cand = mean(float(row[f"{candidate}_{metric}"]) for row in rows)
        out[metric] = {
            "baseline": base,
            "candidate": cand,
            "candidate_minus_baseline": cand - base,
        }
    return out


def _blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (row["date"], row["match_key"]))
    return [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]


def _bootstrap(rows: list[dict[str, Any]], candidate: str, metric: str, seed: int) -> dict[str, Any]:
    blocks = _blocks(rows)
    point = mean(float(row[f"{candidate}_{metric}"]) - float(row[f"baseline_{metric}"]) for row in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = []
        for _ in range(len(blocks)):
            sampled.extend(rng.choice(blocks))
        values.append(mean(float(row[f"{candidate}_{metric}"]) - float(row[f"baseline_{metric}"]) for row in sampled))
    values.sort()
    return {
        "candidate_minus_baseline": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "blocks": len(blocks),
        "draws": BOOTSTRAP_DRAWS,
    }


def audit_domain(cid: str) -> dict[str, Any]:
    report = _load(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(report, SEASON)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing frozen formal parameters {cid} {SEASON}")
    temperature, calibration_mode = _target_season_temperature(cid, SEASON)
    all_matches = read_processed_matches(cid)
    targets = [m for m in all_matches if str(m.season) == SEASON]
    market = _market_lookup(cid)

    rows_1x2 = []
    rows_joint = []
    skipped_baseline = 0
    skipped_market = 0
    max_probability_residual = 0.0
    max_constraint_residual = 0.0
    max_iterations = 0
    kl_1x2 = []
    kl_joint = []

    for match in targets:
        date = match.date.date().isoformat()
        ref = market.get((date, match.home_team, match.away_team))
        if not ref or ref.get("one_x_two") is None:
            skipped_market += 1
            continue
        try:
            baseline = _predict_from_loaded_matches(
                all_matches, match.home_team, match.away_team, match.date, SEASON, params
            )
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
        except Exception:
            skipped_baseline += 1
            continue

        one_candidate, one_audit = _project_1x2(baseline, ref["one_x_two"])
        base_metrics = _metrics(baseline, match)
        one_metrics = _metrics(one_candidate, match)
        key = f"{cid}:{date}:{match.home_team}:{match.away_team}"
        row = {"date": date, "match_key": key}
        for prefix, metrics in (("baseline", base_metrics), ("market_1x2", one_metrics)):
            for metric, value in metrics.items():
                if isinstance(value, (int, float)):
                    row[f"{prefix}_{metric}"] = value
        rows_1x2.append(row)
        max_probability_residual = max(
            max_probability_residual,
            abs(float(one_metrics["probability_sum_residual"])),
        )
        max_constraint_residual = max(max_constraint_residual, float(one_audit["max_residual"]))
        kl_1x2.append(float(one_audit["kl_from_prior"]))

        if cid in OU_COORDINATION_DOMAINS and ref.get("ou25") is not None:
            joint_candidate, joint_audit = _project_1x2_ou(baseline, ref["one_x_two"], ref["ou25"])
            joint_metrics = _metrics(joint_candidate, match)
            joint_row = {"date": date, "match_key": key}
            for prefix, metrics in (("baseline", base_metrics), ("market_1x2_ou25", joint_metrics)):
                for metric, value in metrics.items():
                    if isinstance(value, (int, float)):
                        joint_row[f"{prefix}_{metric}"] = value
            rows_joint.append(joint_row)
            max_probability_residual = max(
                max_probability_residual,
                abs(float(joint_metrics["probability_sum_residual"])),
            )
            max_constraint_residual = max(max_constraint_residual, float(joint_audit["max_residual"]))
            max_iterations = max(max_iterations, int(joint_audit["iterations"]))
            kl_joint.append(float(joint_audit["kl_from_prior"]))

    result = {
        "competition_id": cid,
        "season": SEASON,
        "target_match_count": len(targets),
        "baseline_skipped_count": skipped_baseline,
        "market_skipped_count": skipped_market,
        "market_1x2_prediction_count": len(rows_1x2),
        "market_1x2": None,
        "market_1x2_plus_ou25": None,
        "max_probability_sum_residual": max_probability_residual,
        "max_market_constraint_residual": max_constraint_residual,
        "max_ipf_iterations": max_iterations,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "formal_pit_market_eligible": False,
        "usage": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
    }

    if rows_1x2:
        profile = "market_1x2"
        result["market_1x2"] = {
            "n": len(rows_1x2),
            "mean_KL_from_formal_matrix": mean(kl_1x2) if kl_1x2 else None,
            "metrics": _paired_summary(rows_1x2, profile),
            "bootstrap": {
                metric: _bootstrap(rows_1x2, profile, metric, SEED + index)
                for index, metric in enumerate(
                    ["one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_rps", "ou_brier"],
                    start=1,
                )
            },
        }

    if rows_joint:
        profile = "market_1x2_ou25"
        metrics = _paired_summary(rows_joint, profile)
        checks = {
            "one_x_two_brier_better": metrics["one_x_two_brier"]["candidate_minus_baseline"] < 0.0,
            "one_x_two_rps_better": metrics["one_x_two_rps"]["candidate_minus_baseline"] < 0.0,
            "ou_brier_better": metrics["ou_brier"]["candidate_minus_baseline"] < 0.0,
            "joint_log_nonworse": metrics["joint_log"]["candidate_minus_baseline"] <= 0.0,
            "score_top1_nonworse": metrics["score_top1"]["candidate_minus_baseline"] >= 0.0,
            "score_top3_nonworse": metrics["score_top3"]["candidate_minus_baseline"] >= 0.0,
            "total_top1_nonworse": metrics["total_top1"]["candidate_minus_baseline"] >= 0.0,
            "total_top2_nonworse": metrics["total_top2"]["candidate_minus_baseline"] >= 0.0,
            "total_rps_nonworse": metrics["total_rps"]["candidate_minus_baseline"] <= 0.0,
            "probability_conservation": max_probability_residual <= 1e-10,
            "market_constraint_fit": max_constraint_residual <= 1e-10,
        }
        result["market_1x2_plus_ou25"] = {
            "n": len(rows_joint),
            "mean_KL_from_formal_matrix": mean(kl_joint) if kl_joint else None,
            "max_ipf_iterations": max_iterations,
            "metrics": metrics,
            "checks": checks,
            "all_point_checks_pass": all(checks.values()),
            "bootstrap": {
                metric: _bootstrap(rows_joint, profile, metric, SEED + 100 + index)
                for index, metric in enumerate(
                    ["one_x_two_brier", "one_x_two_rps", "ou_brier", "ou_log", "joint_log", "score_top1", "score_top3", "total_rps"],
                    start=1,
                )
            },
        }
    return result


def main() -> int:
    reports = {}
    failures = {}
    for cid in DOMAINS:
        try:
            reports[cid] = audit_domain(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    joint_point_pass = [
        cid for cid, report in reports.items()
        if (report.get("market_1x2_plus_ou25") or {}).get("all_point_checks_pass")
    ]
    payload = {
        "schema_version": "V5.3.0-retrospective-market-matrix-projection-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": SEASON,
        "reports": reports,
        "failures": failures,
        "one_x_two_projection_domains": DOMAINS,
        "one_x_two_plus_ou25_projection_domains": sorted(OU_COORDINATION_DOMAINS),
        "joint_projection_all_point_checks_pass_domains": joint_point_pass,
        "status": "PASS" if len(reports) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": (
            "This is a retrospective minimum-KL/IPF architecture ceiling only. Football-Data closing-average surfaces lack original quote timestamps. "
            "Exact market marginal fitting cannot authorize formal promotion; score/total degradation is treated as a structural failure signal, not something to repair by post-hoc holdout tuning."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
