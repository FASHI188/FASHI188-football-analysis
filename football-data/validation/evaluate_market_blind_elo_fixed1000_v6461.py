#!/usr/bin/env python3
"""V6.46.1 leakage-safe market-blind Elo baseline on the neutral fixed1000.

This is deliberately a simple benchmark, not a Champion candidate. It uses only prior
90-minute results. It never reads odds, injuries, lineups, future matches, or fixed1000
outcomes before each prediction. Matches on the same date are all predicted first and
ratings are updated only after every prediction for that date has been frozen.

Three-way probabilities:
- Elo supplies the decisive home-vs-away share.
- Draw probability is the empirical draw rate from strictly earlier matches in the same
  competition. A prediction is emitted only after >=100 prior competition matches, so no
  arbitrary draw prior is needed.
- Remaining probability mass is allocated by the Elo decisive share.

Hyperparameters are fixed ex ante (initial rating 1500, K=20, home advantage=100 Elo
points) and are never tuned on fixed1000.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_1x2_fixed1000_benchmark_v6130 as metrics
from platform_core import parse_match_date

BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
OUT = ROOT / "manifests" / "v6_market_blind_fixed1000_v6461_status.json"
INITIAL_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 100.0
MIN_PRIOR_COMP_MATCHES = 100
DIRECTIONS = ("home", "draw", "away")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def actual(raw: dict[str, str]) -> str | None:
    ftr = str(raw.get("FTR") or raw.get("Result") or "").strip().upper()
    if ftr in {"H", "HOME"}:
        return "home"
    if ftr in {"D", "DRAW"}:
        return "draw"
    if ftr in {"A", "AWAY"}:
        return "away"
    try:
        hg = int(float(str(raw.get("FTHG") or raw.get("HG") or "")))
        ag = int(float(str(raw.get("FTAG") or raw.get("AG") or "")))
    except (TypeError, ValueError):
        return None
    return "home" if hg > ag else "away" if ag > hg else "draw"


def team(raw: dict[str, str], side: str) -> str:
    keys = ("HomeTeam", "Home", "home_team", "home") if side == "home" else ("AwayTeam", "Away", "away_team", "away")
    for k in keys:
        value = str(raw.get(k) or "").strip()
        if value:
            return value
    return ""


def season(raw: dict[str, str], path: Path) -> str:
    return str(raw.get("season") or raw.get("Season") or path.stem).strip()


def expected_home(home_rating: float, away_rating: float) -> float:
    x = (home_rating + HOME_ADVANTAGE - away_rating) / 400.0
    return 1.0 / (1.0 + 10.0 ** (-x))


def match_score(result: str) -> float:
    return 1.0 if result == "home" else 0.5 if result == "draw" else 0.0


def load_benchmark() -> tuple[dict[str, Any], set[tuple[str, int]]]:
    payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    keys = {(str(r["source_file"]), int(r["row_index"])) for r in payload["rows"]}
    if len(keys) != 1000:
        raise RuntimeError(f"benchmark key count mismatch: {len(keys)}")
    return payload, keys


def read_domain_rows(cid: str) -> list[dict[str, Any]]:
    comp_dir = ROOT / "processed" / cid
    rows = []
    for path in sorted(comp_dir.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row_index, raw0 in enumerate(csv.DictReader(handle)):
                raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                result = actual(raw)
                h, a = team(raw, "home"), team(raw, "away")
                season_label = season(raw, path)
                date_raw = str(raw.get("Date") or raw.get("date") or "").strip()
                if result is None or not h or not a or not date_raw:
                    continue
                try:
                    dt = parse_match_date(date_raw, season_label)
                except Exception:
                    continue
                rows.append({
                    "competition_id": cid,
                    "date": dt,
                    "season": season_label,
                    "home_team": h,
                    "away_team": a,
                    "actual": result,
                    "source_file": str(path.relative_to(ROOT)),
                    "row_index": row_index,
                })
    rows.sort(key=lambda r: (r["date"], r["home_team"], r["away_team"], r["source_file"], r["row_index"]))
    return rows


def evaluate_domain(cid: str, benchmark_keys: set[tuple[str, int]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = read_domain_rows(cid)
    ratings: dict[str, float] = defaultdict(lambda: INITIAL_RATING)
    prior_matches = 0
    prior_draws = 0
    predictions: list[dict[str, Any]] = []
    benchmark_seen = 0
    warmup_misses = 0

    by_day: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)

    for dt in sorted(by_day):
        day = sorted(by_day[dt], key=lambda r: (r["home_team"], r["away_team"], r["source_file"], r["row_index"]))
        frozen_day: list[tuple[dict[str, Any], float]] = []
        for row in day:
            e_home = expected_home(ratings[row["home_team"]], ratings[row["away_team"]])
            key = (row["source_file"], int(row["row_index"]))
            if key in benchmark_keys:
                benchmark_seen += 1
                if prior_matches < MIN_PRIOR_COMP_MATCHES:
                    warmup_misses += 1
                else:
                    p_draw = prior_draws / prior_matches
                    p_home = (1.0 - p_draw) * e_home
                    p_away = (1.0 - p_draw) * (1.0 - e_home)
                    probs = {"home": p_home, "draw": p_draw, "away": p_away}
                    total = sum(probs.values())
                    probs = {d: probs[d] / total for d in DIRECTIONS}
                    pick = max(DIRECTIONS, key=lambda d: probs[d])
                    predictions.append({
                        "competition_id": cid,
                        "season": row["season"],
                        "date": row["date"].isoformat(),
                        "row_index": row["row_index"],
                        "home_team": row["home_team"],
                        "away_team": row["away_team"],
                        "actual": row["actual"],
                        "provider": "MARKET_BLIND_ELO_RESULT_ONLY",
                        "probabilities": probs,
                        "pick": pick,
                        "pmax": probs[pick],
                        "prior_competition_matches": prior_matches,
                        "prior_competition_draw_rate": p_draw,
                    })
            frozen_day.append((row, e_home))

        # Same-day anti-leakage: update only after all day predictions were frozen.
        for row, e_home in frozen_day:
            score = match_score(row["actual"])
            delta = K_FACTOR * (score - e_home)
            ratings[row["home_team"]] += delta
            ratings[row["away_team"]] -= delta
            prior_matches += 1
            prior_draws += int(row["actual"] == "draw")

    return predictions, {
        "competition_id": cid,
        "processed_rows": len(rows),
        "benchmark_rows_seen": benchmark_seen,
        "predictions": len(predictions),
        "warmup_unavailable": warmup_misses,
    }


def main() -> int:
    benchmark, keys = load_benchmark()
    comps = list(benchmark["target_competitions"])
    all_predictions = []
    domain_status = {}
    for cid in comps:
        pred, status = evaluate_domain(cid, keys)
        all_predictions.extend(pred)
        domain_status[cid] = status

    all_predictions.sort(key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    matched_keys = {(r["competition_id"], r["date"], r["home_team"], r["away_team"]) for r in all_predictions}
    benchmark_n = int(benchmark["target_n"])
    report = {
        "schema_version": "V6.46.1-market-blind-elo-fixed1000-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if len(all_predictions) == benchmark_n else "PASS_WITH_WARMUP_GAPS",
        "formal_current_version": "V5.0.1",
        "classification": "SIMPLE_MARKET_BLIND_BASELINE_RESEARCH_ONLY",
        "benchmark_path": str(BENCHMARK.relative_to(ROOT)),
        "benchmark_target_n": benchmark_n,
        "prediction_count": len(all_predictions),
        "coverage": len(all_predictions) / benchmark_n if benchmark_n else 0.0,
        "competition_count": len({r["competition_id"] for r in all_predictions}),
        "model": {
            "name": "result-only three-way Elo baseline",
            "initial_rating": INITIAL_RATING,
            "k_factor": K_FACTOR,
            "home_advantage_elo_points": HOME_ADVANTAGE,
            "draw_probability": "strictly-prior competition empirical draw rate",
            "minimum_prior_competition_matches": MIN_PRIOR_COMP_MATCHES,
            "hyperparameters_tuned_on_fixed1000": False,
            "market_inputs": False,
            "lineup_or_injury_inputs": False,
        },
        "metrics": metrics.multiclass_scores(all_predictions),
        "top1_calibration": metrics.top1_ece(all_predictions),
        "draw_audit": metrics.draw_audit(all_predictions),
        "by_competition": metrics.subgroup(all_predictions, "competition_id"),
        "pmax_bins": metrics.pmax_bins(all_predictions),
        "chronological_100_match_windows": metrics.windows100(all_predictions),
        "domain_status": domain_status,
        "audit": {
            "benchmark_membership_used_for_tuning": False,
            "same_day_updates_after_predictions": True,
            "future_rows_used_before_prediction": False,
            "odds_read": False,
            "result_only": True,
            "prediction_identity_count": len(matched_keys),
        },
        "governance": {
            "research_only": True,
            "simple_baseline_not_champion_candidate": True,
            "formal_weight": 0,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "prediction_count": report["prediction_count"],
        "coverage": report["coverage"],
        "metrics": report["metrics"],
        "draw_audit": report["draw_audit"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
