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
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    canonical_team_name,
    derive_score_marginals,
    load_aliases,
    multiclass_brier,
    ranked_probability_score,
    read_processed_matches,
)

DOMAINS = [
    "ENG_PremierLeague",
    "ESP_LaLiga",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
]
SEASON = "2025/26"
OUT = ROOT / "manifests" / "retrospective_market_outcome_ceiling_v522_status.json"
BLOCK_SIZE = 20
DRAWS = 1600
SEED = 5222026


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _num(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 1.0:
        return None
    return value


def _devig(h: float, d: float, a: float) -> dict[str, float]:
    raw = {"home": 1.0 / h, "draw": 1.0 / d, "away": 1.0 / a}
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def _outcome(actual_h: int, actual_a: int) -> str:
    return "home" if actual_h > actual_a else "draw" if actual_h == actual_a else "away"


def _metric(prob: dict[str, float], actual: str) -> dict[str, float]:
    top = max(("home", "draw", "away"), key=lambda key: (prob[key], key))
    order = ["away", "draw", "home"]
    idx = order.index(actual)
    return {
        "accuracy": 1.0 if top == actual else 0.0,
        "brier": multiclass_brier(prob, actual),
        "rps": ranked_probability_score([prob[key] for key in order], idx),
        "top1": top,
        "gap": sorted(prob.values(), reverse=True)[0] - sorted(prob.values(), reverse=True)[1],
    }


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
            opening_values = (_num(row, "AvgH"), _num(row, "AvgD"), _num(row, "AvgA"))
            closing_values = (_num(row, "AvgCH"), _num(row, "AvgCD"), _num(row, "AvgCA"))
            opening = _devig(*opening_values) if all(value is not None for value in opening_values) else None
            closing = _devig(*closing_values) if all(value is not None for value in closing_values) else None
            output[(date, home, away)] = {
                "opening": opening,
                "closing": closing,
                "source_path": str(path.relative_to(ROOT)),
            }
    return output


def _summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, float]:
    return {
        "n": len(rows),
        "accuracy": mean(float(row[f"{prefix}_accuracy"]) for row in rows),
        "brier": mean(float(row[f"{prefix}_brier"]) for row in rows),
        "rps": mean(float(row[f"{prefix}_rps"]) for row in rows),
    }


def _bootstrap(rows: list[dict[str, Any]], candidate: str, baseline: str, metric: str, seed: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row["date"] + row["match_key"])
    blocks = [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]
    point = mean(float(row[f"{candidate}_{metric}"]) - float(row[f"{baseline}_{metric}"]) for row in rows)
    rng = random.Random(seed)
    draws = []
    for _ in range(DRAWS):
        sampled = []
        for _ in range(len(blocks)):
            sampled.extend(rng.choice(blocks))
        draws.append(mean(float(row[f"{candidate}_{metric}"]) - float(row[f"{baseline}_{metric}"]) for row in sampled))
    draws.sort()
    return {
        "candidate_minus_baseline": point,
        "ci95_lower": draws[int(0.025 * (len(draws) - 1))],
        "ci95_upper": draws[int(0.975 * (len(draws) - 1))],
        "blocks": len(blocks),
        "draws": DRAWS,
    }


def _selective(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    output = {}
    for threshold in (0.10, 0.15, 0.20, 0.25, 0.30):
        chosen = [row for row in rows if float(row[f"{prefix}_gap"]) >= threshold]
        output[f"gap_ge_{threshold:.2f}"] = {
            "selected": len(chosen),
            "coverage": len(chosen) / len(rows) if rows else 0.0,
            "accuracy": mean(float(row[f"{prefix}_accuracy"]) for row in chosen) if chosen else None,
        }
    return output


def audit_domain(cid: str) -> dict[str, Any]:
    formal_report = _load(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(formal_report, SEASON)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise RuntimeError(f"missing frozen formal parameters for {cid} {SEASON}")
    temperature, calibration_mode = _target_season_temperature(cid, SEASON)
    all_matches = read_processed_matches(cid)
    targets = [match for match in all_matches if str(match.season) == SEASON]
    market = _market_lookup(cid)
    rows = []
    baseline_failures = 0
    market_missing = {"opening": 0, "closing": 0}

    for match in targets:
        key = (match.date.date().isoformat(), match.home_team, match.away_team)
        reference = market.get(key)
        if reference is None:
            market_missing["opening"] += 1
            market_missing["closing"] += 1
            continue
        try:
            matrix = _predict_from_loaded_matches(
                all_matches, match.home_team, match.away_team, match.date, SEASON, params
            )
            if abs(temperature - 1.0) > 1e-15:
                matrix = temperature_scale_matrix(matrix, temperature)
        except Exception:
            baseline_failures += 1
            continue
        formal_prob = derive_score_marginals(matrix)["1x2"]
        actual = _outcome(match.home_goals, match.away_goals)
        formal_metric = _metric(formal_prob, actual)
        opening = reference.get("opening")
        closing = reference.get("closing")
        if opening is None:
            market_missing["opening"] += 1
        if closing is None:
            market_missing["closing"] += 1
        if opening is None or closing is None:
            continue
        opening_metric = _metric(opening, actual)
        closing_metric = _metric(closing, actual)
        row = {
            "date": match.date.date().isoformat(),
            "match_key": f"{cid}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}",
        }
        for prefix, metric in (("formal", formal_metric), ("opening", opening_metric), ("closing", closing_metric)):
            for name in ("accuracy", "brier", "rps", "gap"):
                row[f"{prefix}_{name}"] = metric[name]
        rows.append(row)

    if not rows:
        raise RuntimeError(f"no comparable market rows for {cid}")
    return {
        "competition_id": cid,
        "season": SEASON,
        "target_match_count": len(targets),
        "comparable_row_count": len(rows),
        "comparable_coverage": len(rows) / len(targets),
        "baseline_failure_count": baseline_failures,
        "market_missing": market_missing,
        "formal": _summary(rows, "formal"),
        "market_opening": _summary(rows, "opening"),
        "market_closing": _summary(rows, "closing"),
        "paired_bootstrap_vs_formal": {
            "opening_brier": _bootstrap(rows, "opening", "formal", "brier", SEED + 1),
            "opening_rps": _bootstrap(rows, "opening", "formal", "rps", SEED + 2),
            "closing_brier": _bootstrap(rows, "closing", "formal", "brier", SEED + 3),
            "closing_rps": _bootstrap(rows, "closing", "formal", "rps", SEED + 4),
        },
        "selective_accuracy_by_raw_gap": {
            "formal": _selective(rows, "formal"),
            "market_opening": _selective(rows, "opening"),
            "market_closing": _selective(rows, "closing"),
        },
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "formal_pit_market_eligible": False,
        "usage": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
    }


def main() -> int:
    reports = {}
    failures = {}
    for cid in DOMAINS:
        try:
            reports[cid] = audit_domain(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    payload = {
        "schema_version": "V5.2.2-retrospective-market-outcome-ceiling-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": SEASON,
        "reports": reports,
        "failures": failures,
        "status": "PASS" if len(reports) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": (
            "Football-Data average/opening and average-closing odds lack original quote timestamps. "
            "This is a retrospective outcome-market ceiling diagnostic only. It cannot authorize historical PIT promotion, "
            "unified score-matrix mutation, EV, fair-price execution or A-grade synchronized-market evidence."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
