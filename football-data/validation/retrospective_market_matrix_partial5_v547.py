#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import sys
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

import retrospective_market_all17_ceiling_v524 as market17
import retrospective_market_matrix_projection_v530 as base
from backtest_last_complete_season_all_domains_v470 import REPORT_ROOT, _fold_for_season, _predict_from_loaded_matches, _target_season_temperature
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, read_processed_matches

DOMAINS = {
    "SUI_SuperLeague": "2025/26",
    "SWE_Allsvenskan": "2025",
    "NOR_Eliteserien": "2025",
    "JPN_J1": "2025",
    "USA_MLS": "2025",
}
OUT = ROOT / "manifests" / "retrospective_market_matrix_partial5_v547_status.json"
BLOCK_SIZE = 20
DRAWS = 1400
SEED = 5472026
TOL = 1e-10


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _blocks(rows):
    ordered = sorted(rows, key=lambda r: (r["date"], r["match_key"]))
    return [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]


def _bootstrap(rows, metric: str, seed: int):
    blocks = _blocks(rows)
    point = mean(float(r[f"candidate_{metric}"]) - float(r[f"baseline_{metric}"]) for r in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(DRAWS):
        sample = []
        for _ in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        values.append(mean(float(r[f"candidate_{metric}"]) - float(r[f"baseline_{metric}"]) for r in sample))
    values.sort()
    return {
        "candidate_minus_baseline": point,
        "ci95_lower": values[int(0.025 * (len(values)-1))],
        "ci95_upper": values[int(0.975 * (len(values)-1))],
        "blocks": len(blocks), "draws": DRAWS,
    }


def _summary(rows, prefix: str):
    metrics = [
        "one_x_two_accuracy", "one_x_two_brier", "one_x_two_rps", "joint_log",
        "score_top1", "score_top3", "total_top1", "total_top2", "total_rps",
    ]
    return {m: mean(float(r[f"{prefix}_{m}"]) for r in rows) for m in metrics}


def audit(cid: str, season: str) -> dict[str, Any]:
    formal_report = _load(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(formal_report, season)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise PlatformError(f"missing frozen parameters {cid} {season}")
    temperature, calibration_mode = _target_season_temperature(cid, season)
    all_matches = read_processed_matches(cid)
    targets = [m for m in all_matches if str(m.season) == season]
    market = market17._market_lookup(cid, season)
    rows = []
    baseline_failures = 0
    no_market = 0
    max_prob_residual = 0.0
    max_constraint_residual = 0.0
    for match in targets:
        key = (match.date.date().isoformat(), match.home_team, match.away_team)
        ref = market.get(key)
        if not ref:
            no_market += 1
            continue
        one = ref.get("closing") or ref.get("opening")
        if one is None:
            no_market += 1
            continue
        try:
            baseline = _predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
            if abs(temperature - 1.0) > 1e-15:
                baseline = temperature_scale_matrix(baseline, temperature)
        except Exception:
            baseline_failures += 1
            continue
        candidate, audit_row = base._project_1x2(baseline, one)
        bm = base._metrics(baseline, match)
        cm = base._metrics(candidate, match)
        row = {"date": match.date.date().isoformat(), "match_key": f"{cid}:{season}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}"}
        for prefix, metrics in (("baseline", bm), ("candidate", cm)):
            for k, v in metrics.items():
                if isinstance(v, (int, float)):
                    row[f"{prefix}_{k}"] = v
        rows.append(row)
        max_prob_residual = max(max_prob_residual, abs(float(cm["probability_sum_residual"])))
        max_constraint_residual = max(max_constraint_residual, float(audit_row["max_residual"]))
    if not rows:
        raise PlatformError(f"no comparable rows {cid}")
    baseline_summary = _summary(rows, "baseline")
    candidate_summary = _summary(rows, "candidate")
    delta = {k: candidate_summary[k] - baseline_summary[k] for k in baseline_summary}
    checks = {
        "one_x_two_brier_better": delta["one_x_two_brier"] < 0.0,
        "one_x_two_rps_better": delta["one_x_two_rps"] < 0.0,
        "joint_log_nonworse": delta["joint_log"] <= 0.0,
        "score_top1_nonworse": delta["score_top1"] >= 0.0,
        "score_top3_nonworse": delta["score_top3"] >= 0.0,
        "total_top1_nonworse": delta["total_top1"] >= 0.0,
        "total_top2_nonworse": delta["total_top2"] >= 0.0,
        "total_rps_nonworse": delta["total_rps"] <= 0.0,
        "probability_conservation": max_prob_residual <= TOL,
        "market_constraint_fit": max_constraint_residual <= TOL,
    }
    bootstrap = {
        metric: _bootstrap(rows, metric, SEED + idx)
        for idx, metric in enumerate(["one_x_two_brier", "one_x_two_rps", "joint_log", "score_top1", "score_top3", "total_rps"], start=1)
    }
    return {
        "competition_id": cid,
        "season": season,
        "target_match_count": len(targets),
        "comparable_prediction_count": len(rows),
        "coverage": len(rows) / len(targets),
        "baseline_failure_count": baseline_failures,
        "no_market_count": no_market,
        "baseline": baseline_summary,
        "candidate_1x2_matrix": candidate_summary,
        "candidate_minus_baseline": delta,
        "bootstrap": bootstrap,
        "frozen_point_checks": checks,
        "all_frozen_point_checks_pass": all(checks.values()),
        "max_probability_sum_residual": max_prob_residual,
        "max_market_constraint_residual": max_constraint_residual,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "formal_pit_market_eligible": False,
    }


def main() -> int:
    reports = {}; failures = {}
    for cid, season in DOMAINS.items():
        try:
            reports[cid] = audit(cid, season)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    passed = [cid for cid, r in reports.items() if r.get("all_frozen_point_checks_pass")]
    payload = {
        "schema_version": "V5.4.7-retrospective-market-matrix-partial5-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "domains": DOMAINS,
        "reports": reports,
        "failures": failures,
        "all_point_checks_pass_domains": passed,
        "status": "PASS" if len(reports) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": "Retrospective 1X2-only minimum-KL architecture ceiling with frozen all-target nonworse checks. Historical market timestamps are unavailable; no formal promotion or post-hoc retuning is allowed."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
