#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
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

from backtest_last_complete_season_all_domains_v470 import (
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import (
    canonical_team_name,
    load_aliases,
    read_processed_matches,
    score_matrix_rows,
    settle_home_handicap,
)

DOMAINS = [
    "ENG_PremierLeague",
    "ESP_LaLiga",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
]
SEASON = "2025/26"
OUT = ROOT / "manifests" / "retrospective_ah_ou_ceiling_v528_status.json"
EPS = 1e-15
BLOCK_SIZE = 20
DRAWS = 1600
SEED = 5282026


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _devig_two_way(first_odds: float, second_odds: float) -> tuple[float, float]:
    a = 1.0 / float(first_odds)
    b = 1.0 / float(second_odds)
    total = a + b
    if total <= 0.0:
        raise ValueError("invalid two-way odds")
    return a / total, b / total


def _market_lookup(cid: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    path = ROOT / "processed" / cid / "2025-26.csv"
    aliases = load_aliases()
    output: dict[tuple[str, str, str], dict[str, Any]] = {}
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

            ah_line = _finite(row.get("AHCh"))
            ah_home = _positive_decimal(row.get("AvgCAHH"))
            ah_away = _positive_decimal(row.get("AvgCAHA"))
            over = _positive_decimal(row.get("AvgC>2.5"))
            under = _positive_decimal(row.get("AvgC<2.5"))

            output[(date, home, away)] = {
                "ah_line": ah_line,
                "ah_home_odds": ah_home,
                "ah_away_odds": ah_away,
                "over_2_5_odds": over,
                "under_2_5_odds": under,
                "source_path": str(path.relative_to(ROOT)),
            }
    return output


def _formal_ou_over_probability(matrix: list[dict[str, Any]], line: float = 2.5) -> float:
    # 2.5 has no push/half settlement; direct total marginal is exact.
    return sum(p for h, a, p in score_matrix_rows(matrix) if h + a > line)


def _formal_home_ah_expectation(matrix: list[dict[str, Any]], line: float) -> float:
    value = 0.0
    for h, a, p in score_matrix_rows(matrix):
        settlement = settle_home_handicap(h, a, line)
        value += p * (float(settlement["win"]) - float(settlement["loss"]))
    return value


def _actual_home_ah_net(home_goals: int, away_goals: int, line: float) -> float:
    settlement = settle_home_handicap(home_goals, away_goals, line)
    return float(settlement["win"]) - float(settlement["loss"])


def _actual_selected_profit(home_goals: int, away_goals: int, line: float, side: str, decimal_odds: float) -> float:
    home = settle_home_handicap(home_goals, away_goals, line)
    if side == "home":
        win = float(home["win"])
        loss = float(home["loss"])
    else:
        # The away side is exactly the complement of the home Asian settlement.
        win = float(home["loss"])
        loss = float(home["win"])
    return win * (float(decimal_odds) - 1.0) - loss


def _binary_brier(prob: float, actual: int) -> float:
    return (float(prob) - float(actual)) ** 2


def _binary_log(prob: float, actual: int) -> float:
    p = min(1.0 - EPS, max(EPS, float(prob)))
    return -(math.log(p) if actual else math.log(1.0 - p))


def _blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (row["date"], row["match_key"]))
    return [ordered[i:i + BLOCK_SIZE] for i in range(0, len(ordered), BLOCK_SIZE)]


def _bootstrap_difference(rows: list[dict[str, Any]], candidate_key: str, baseline_key: str, seed: int) -> dict[str, Any]:
    blocks = _blocks(rows)
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    values = []
    for _ in range(DRAWS):
        sampled = []
        for _ in range(len(blocks)):
            sampled.extend(rng.choice(blocks))
        values.append(mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sampled))
    values.sort()
    return {
        "candidate_minus_baseline": point,
        "ci95_lower": values[int(0.025 * (len(values) - 1))],
        "ci95_upper": values[int(0.975 * (len(values) - 1))],
        "blocks": len(blocks),
        "draws": DRAWS,
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return mean(float(row[key]) for row in rows)


def audit_domain(cid: str) -> dict[str, Any]:
    formal_report = _load(REPORT_ROOT / f"{cid}.json")
    fold = _fold_for_season(formal_report, SEASON)
    params = fold.get("selected_parameters")
    if not isinstance(params, dict):
        raise RuntimeError(f"missing frozen formal parameters for {cid} {SEASON}")
    temperature, calibration_mode = _target_season_temperature(cid, SEASON)
    all_matches = read_processed_matches(cid)
    targets = [m for m in all_matches if str(m.season) == SEASON]
    market = _market_lookup(cid)

    ou_rows = []
    ah_rows = []
    baseline_failures = 0
    missing_ou = 0
    missing_ah = 0
    invalid_ah_line = 0

    for match in targets:
        date = match.date.date().isoformat()
        key = (date, match.home_team, match.away_team)
        ref = market.get(key)
        if not ref:
            missing_ou += 1
            missing_ah += 1
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

        match_key = f"{cid}:{date}:{match.home_team}:{match.away_team}"

        over_odds = ref.get("over_2_5_odds")
        under_odds = ref.get("under_2_5_odds")
        if over_odds is None or under_odds is None:
            missing_ou += 1
        else:
            market_over, _market_under = _devig_two_way(over_odds, under_odds)
            formal_over = _formal_ou_over_probability(matrix, 2.5)
            actual_over = 1 if match.home_goals + match.away_goals >= 3 else 0
            formal_side = "over" if formal_over >= 0.5 else "under"
            market_side = "over" if market_over >= 0.5 else "under"
            ou_rows.append({
                "date": date,
                "match_key": match_key,
                "formal_over_probability": formal_over,
                "market_over_probability": market_over,
                "actual_over": actual_over,
                "formal_accuracy": 1.0 if (formal_side == "over") == bool(actual_over) else 0.0,
                "market_accuracy": 1.0 if (market_side == "over") == bool(actual_over) else 0.0,
                "formal_brier": _binary_brier(formal_over, actual_over),
                "market_brier": _binary_brier(market_over, actual_over),
                "formal_log": _binary_log(formal_over, actual_over),
                "market_log": _binary_log(market_over, actual_over),
                "formal_side": formal_side,
                "market_side": market_side,
                "market_over_odds": over_odds,
                "market_under_odds": under_odds,
            })

        line = ref.get("ah_line")
        ah_home_odds = ref.get("ah_home_odds")
        ah_away_odds = ref.get("ah_away_odds")
        if line is None or ah_home_odds is None or ah_away_odds is None:
            missing_ah += 1
        else:
            try:
                formal_home_expectation = _formal_home_ah_expectation(matrix, float(line))
                actual_home_net = _actual_home_ah_net(match.home_goals, match.away_goals, float(line))
            except Exception:
                invalid_ah_line += 1
                continue
            market_home_share, _market_away_share = _devig_two_way(ah_home_odds, ah_away_odds)
            market_signed_lean = 2.0 * market_home_share - 1.0
            formal_side = "home" if formal_home_expectation >= 0.0 else "away"
            market_side = "home" if market_home_share >= 0.5 else "away"
            formal_selected_net = actual_home_net if formal_side == "home" else -actual_home_net
            market_selected_net = actual_home_net if market_side == "home" else -actual_home_net
            formal_price = ah_home_odds if formal_side == "home" else ah_away_odds
            market_price = ah_home_odds if market_side == "home" else ah_away_odds
            ah_rows.append({
                "date": date,
                "match_key": match_key,
                "ah_line": float(line),
                "formal_home_expected_settlement_net": formal_home_expectation,
                "market_home_signed_lean": market_signed_lean,
                "actual_home_settlement_net": actual_home_net,
                "formal_squared_error": (formal_home_expectation - actual_home_net) ** 2,
                "market_squared_error": (market_signed_lean - actual_home_net) ** 2,
                "formal_side": formal_side,
                "market_side": market_side,
                "formal_selected_settlement_net": formal_selected_net,
                "market_selected_settlement_net": market_selected_net,
                "formal_selected_settlement_score": (formal_selected_net + 1.0) / 2.0,
                "market_selected_settlement_score": (market_selected_net + 1.0) / 2.0,
                "formal_positive_settlement": 1.0 if formal_selected_net > 1e-12 else 0.0,
                "market_positive_settlement": 1.0 if market_selected_net > 1e-12 else 0.0,
                "formal_nonnegative_settlement": 1.0 if formal_selected_net >= -1e-12 else 0.0,
                "market_nonnegative_settlement": 1.0 if market_selected_net >= -1e-12 else 0.0,
                "formal_selected_closing_profit": _actual_selected_profit(
                    match.home_goals, match.away_goals, float(line), formal_side, formal_price
                ),
                "market_selected_closing_profit": _actual_selected_profit(
                    match.home_goals, match.away_goals, float(line), market_side, market_price
                ),
            })

    if not ou_rows and not ah_rows:
        raise RuntimeError(f"no AH/OU comparable rows for {cid}")

    ou_summary = None
    if ou_rows:
        ou_summary = {
            "n": len(ou_rows),
            "coverage": len(ou_rows) / len(targets),
            "formal_accuracy": _mean(ou_rows, "formal_accuracy"),
            "market_accuracy": _mean(ou_rows, "market_accuracy"),
            "accuracy_gain_pp": 100.0 * (_mean(ou_rows, "market_accuracy") - _mean(ou_rows, "formal_accuracy")),
            "formal_brier": _mean(ou_rows, "formal_brier"),
            "market_brier": _mean(ou_rows, "market_brier"),
            "formal_log": _mean(ou_rows, "formal_log"),
            "market_log": _mean(ou_rows, "market_log"),
            "bootstrap_market_minus_formal": {
                "brier": _bootstrap_difference(ou_rows, "market_brier", "formal_brier", SEED + 1),
                "log": _bootstrap_difference(ou_rows, "market_log", "formal_log", SEED + 2),
                "accuracy": _bootstrap_difference(ou_rows, "market_accuracy", "formal_accuracy", SEED + 3),
            },
        }

    ah_summary = None
    if ah_rows:
        ah_summary = {
            "n": len(ah_rows),
            "coverage": len(ah_rows) / len(targets),
            "formal_settlement_mse": _mean(ah_rows, "formal_squared_error"),
            "market_settlement_proxy_mse": _mean(ah_rows, "market_squared_error"),
            "formal_selected_mean_settlement_net": _mean(ah_rows, "formal_selected_settlement_net"),
            "market_selected_mean_settlement_net": _mean(ah_rows, "market_selected_settlement_net"),
            "formal_selected_mean_settlement_score": _mean(ah_rows, "formal_selected_settlement_score"),
            "market_selected_mean_settlement_score": _mean(ah_rows, "market_selected_settlement_score"),
            "formal_positive_settlement_rate": _mean(ah_rows, "formal_positive_settlement"),
            "market_positive_settlement_rate": _mean(ah_rows, "market_positive_settlement"),
            "formal_nonnegative_settlement_rate": _mean(ah_rows, "formal_nonnegative_settlement"),
            "market_nonnegative_settlement_rate": _mean(ah_rows, "market_nonnegative_settlement"),
            "formal_selected_closing_profit_mean": _mean(ah_rows, "formal_selected_closing_profit"),
            "market_selected_closing_profit_mean": _mean(ah_rows, "market_selected_closing_profit"),
            "bootstrap_market_minus_formal": {
                "settlement_proxy_mse": _bootstrap_difference(ah_rows, "market_squared_error", "formal_squared_error", SEED + 4),
                "settlement_score": _bootstrap_difference(ah_rows, "market_selected_settlement_score", "formal_selected_settlement_score", SEED + 5),
                "positive_settlement_rate": _bootstrap_difference(ah_rows, "market_positive_settlement", "formal_positive_settlement", SEED + 6),
                "closing_profit": _bootstrap_difference(ah_rows, "market_selected_closing_profit", "formal_selected_closing_profit", SEED + 7),
            },
            "metric_note": (
                "Asian quarter-lines are not forced into a binary Brier score. Actual settlement net is win_fraction-loss_fraction in [-1,1]. "
                "Formal expectation is the same quantity integrated over the score matrix. Market signed lean is 2*de-vigged-home-share-1 and is a proxy, not an exact quarter-line fair-settlement expectation."
            ),
        }

    return {
        "competition_id": cid,
        "season": SEASON,
        "target_match_count": len(targets),
        "baseline_failure_count": baseline_failures,
        "missing_ou_count": missing_ou,
        "missing_ah_count": missing_ah,
        "invalid_ah_line_count": invalid_ah_line,
        "over_under_2_5": ou_summary,
        "asian_handicap_closing_line": ah_summary,
        "oof_temperature": temperature,
        "oof_calibration_mode": calibration_mode,
        "formal_pit_market_eligible": False,
        "usage": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
    }


def main() -> int:
    reports = {}
    failures = {}
    for index, cid in enumerate(DOMAINS):
        try:
            reports[cid] = audit_domain(cid)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    ou_market_strict_brier_wins = []
    ah_market_settlement_score_wins = []
    for cid, report in reports.items():
        ou = report.get("over_under_2_5") or {}
        ou_boot = ((ou.get("bootstrap_market_minus_formal") or {}).get("brier") or {})
        if ou_boot and float(ou_boot.get("ci95_upper") or 1.0) < 0.0:
            ou_market_strict_brier_wins.append(cid)
        ah = report.get("asian_handicap_closing_line") or {}
        ah_boot = ((ah.get("bootstrap_market_minus_formal") or {}).get("settlement_score") or {})
        if ah_boot and float(ah_boot.get("ci95_lower") or -1.0) > 0.0:
            ah_market_settlement_score_wins.append(cid)

    payload = {
        "schema_version": "V5.2.8-retrospective-ah-ou-ceiling-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": SEASON,
        "reports": reports,
        "failures": failures,
        "ou_market_strict_brier_win_domains": ou_market_strict_brier_wins,
        "ah_market_strict_settlement_score_win_domains": ah_market_settlement_score_wins,
        "status": "PASS" if len(reports) == len(DOMAINS) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_pit_market_eligible": False,
        "governance": (
            "Football-Data closing average AH/OU surfaces do not carry original quote timestamps and remain retrospective reference only. "
            "This diagnostic may justify prospective synchronized 1X2+AH+OU acquisition, but cannot authorize formal historical market coordination, EV or probability mutation."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
