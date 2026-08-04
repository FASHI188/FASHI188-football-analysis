#!/usr/bin/env python3
"""Direct-draw residual R2 for the V5.1 research prototype.

This challenger calibrates only P(draw). The remaining non-draw mass is split between
home and away in the original market ratio. That isolates the known draw problem and
avoids the R1 error of perturbing all three classes at once.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evaluate_v510_market_residual_1x2_r1 as common

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_direct_draw_residual_r2.json"
BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_direct_draw_residual_r2_status.json"
DIRECTIONS = common.DIRECTIONS


class DirectDrawModel:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.global_counts: defaultdict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
        self.competition_counts: defaultdict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
        self.full_counts: defaultdict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
        team = cfg["team_state"]
        self.ratings: defaultdict[tuple[str, str], float] = defaultdict(lambda: float(team["elo_initial"]))
        window = int(cfg["features"]["recent_window"])
        self.recent_draws: defaultdict[tuple[str, str], deque[int]] = defaultdict(lambda: deque(maxlen=window))

    @staticmethod
    def beta_posterior(counts: Counter[str], prior_draw: float, strength: float) -> float:
        n = int(counts["total"])
        draws = int(counts["draw"])
        return (draws + strength * prior_draw) / (n + strength)

    def recent_bin(self, competition: str, team: str) -> str:
        values = self.recent_draws[(competition, team)]
        fcfg = self.cfg["features"]
        if len(values) < int(fcfg["recent_minimum"]):
            return "NA"
        return str(common.bucket(sum(values) / len(values), [float(x) for x in fcfg["recent_draw_edges"]]))

    def frozen_features(self, row: dict[str, Any]) -> dict[str, Any]:
        competition = str(row["competition_id"])
        home = str(row["home_team"])
        away = str(row["away_team"])
        market = common.normalized_probs((row.get("market") or {}).get("probabilities") or {})
        fcfg = self.cfg["features"]
        pmax = max(market.values())
        balance = abs(market["home"] - market["away"])
        home_rating = self.ratings[(competition, home)]
        away_rating = self.ratings[(competition, away)]
        tcfg = self.cfg["team_state"]
        elo_diff = home_rating + float(tcfg["elo_home_advantage"]) - away_rating
        pmax_bin = common.bucket(pmax, [float(x) for x in fcfg["pmax_edges"]])
        pdraw_bin = common.bucket(market["draw"], [float(x) for x in fcfg["pdraw_edges"]])
        balance_bin = common.bucket(balance, [float(x) for x in fcfg["home_away_balance_edges"]])
        elo_bin = common.bucket(elo_diff, [float(x) for x in fcfg["elo_diff_edges"]])
        home_recent = self.recent_bin(competition, home)
        away_recent = self.recent_bin(competition, away)
        return {
            "market": market,
            "global_key": (pmax_bin, pdraw_bin, balance_bin),
            "competition_key": (competition, pmax_bin, pdraw_bin, balance_bin),
            "full_key": (competition, pmax_bin, pdraw_bin, balance_bin, elo_bin, home_recent, away_recent),
            "pmax_bin": pmax_bin,
            "pdraw_bin": pdraw_bin,
            "balance_bin": balance_bin,
            "elo_bin": elo_bin,
            "home_recent_draw_bin": home_recent,
            "away_recent_draw_bin": away_recent,
        }

    def predict(self, features: dict[str, Any]) -> dict[str, float]:
        market = features["market"]
        shrink = self.cfg["beta_shrinkage"]
        pdraw_global = self.beta_posterior(
            self.global_counts[features["global_key"]], market["draw"], float(shrink["global_prior_strength"])
        )
        pdraw_comp = self.beta_posterior(
            self.competition_counts[features["competition_key"]], pdraw_global, float(shrink["competition_prior_strength"])
        )
        pdraw = self.beta_posterior(
            self.full_counts[features["full_key"]], pdraw_comp, float(shrink["full_cell_prior_strength"])
        )
        pdraw = min(1.0 - 1e-12, max(1e-12, pdraw))
        non_draw_market = market["home"] + market["away"]
        if non_draw_market <= 0:
            raise common.ModelError("market non-draw mass is zero")
        remaining = 1.0 - pdraw
        result = {
            "home": remaining * market["home"] / non_draw_market,
            "draw": pdraw,
            "away": remaining * market["away"] / non_draw_market,
        }
        total = sum(result.values())
        return {direction: result[direction] / total for direction in DIRECTIONS}

    def update(self, row: dict[str, Any], features: dict[str, Any]) -> None:
        actual = common.actual_direction(row)
        for table, key in (
            (self.global_counts, features["global_key"]),
            (self.competition_counts, features["competition_key"]),
            (self.full_counts, features["full_key"]),
        ):
            table[key]["total"] += 1
            table[key]["draw"] += int(actual == "draw")

        competition = str(row["competition_id"])
        home = str(row["home_team"])
        away = str(row["away_team"])
        home_rating = self.ratings[(competition, home)]
        away_rating = self.ratings[(competition, away)]
        team = self.cfg["team_state"]
        expected_home = 1.0 / (1.0 + 10.0 ** (-(home_rating + float(team["elo_home_advantage"]) - away_rating) / 400.0))
        actual_home = 1.0 if actual == "home" else 0.5 if actual == "draw" else 0.0
        delta = float(team["elo_k"]) * (actual_home - expected_home)
        self.ratings[(competition, home)] = home_rating + delta
        self.ratings[(competition, away)] = away_rating - delta
        indicator = int(actual == "draw")
        self.recent_draws[(competition, home)].append(indicator)
        self.recent_draws[(competition, away)].append(indicator)


def market_available(raw: dict[str, Any]) -> bool:
    probs = (raw.get("market") or {}).get("probabilities")
    if not isinstance(probs, dict):
        return False
    try:
        common.normalized_probs(probs)
        return True
    except Exception:
        return False


def evaluate(config: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    raw_rows = benchmark.get("rows")
    if not isinstance(raw_rows, list):
        raise common.ModelError("benchmark missing rows list")
    unavailable = sum(not isinstance(row, dict) or not market_available(row) for row in raw_rows)
    clean: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict) or not market_available(raw):
            continue
        try:
            key = common.identity(raw)
            if key in seen:
                raise common.ModelError("duplicate identity")
            seen.add(key)
            actual = common.actual_direction(raw)
            market = common.normalized_probs((raw.get("market") or {}).get("probabilities") or {})
            dt = datetime.fromisoformat(str(raw["date"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise common.ModelError("naive date")
            clean.append({**raw, "actual": actual, "market": {**(raw.get("market") or {}), "probabilities": market}})
        except Exception as exc:
            errors.append({"index": index, "reason": f"{type(exc).__name__}: {exc}"})
    clean.sort(key=lambda row: (str(row["date"]), str(row["competition_id"]), str(row["home_team"]), str(row["away_team"])))

    model = DirectDrawModel(config)
    predictions: list[dict[str, Any]] = []
    by_day: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clean:
        by_day[str(row["date"])[:10]].append(row)
    for day in sorted(by_day):
        frozen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in by_day[day]:
            features = model.frozen_features(row)
            calibrated = model.predict(features)
            predictions.append({
                "competition_id": row["competition_id"],
                "season": row.get("season"),
                "date": row["date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "actual": row["actual"],
                "market_probabilities": features["market"],
                "model_probabilities": calibrated,
                "market_pick": max(DIRECTIONS, key=lambda d: features["market"][d]),
                "model_pick": max(DIRECTIONS, key=lambda d: calibrated[d]),
                "features": {k: features[k] for k in (
                    "pmax_bin", "pdraw_bin", "balance_bin", "elo_bin", "home_recent_draw_bin", "away_recent_draw_bin"
                )},
            })
            frozen.append((row, features))
        for row, features in frozen:
            model.update(row, features)

    market_metrics = common.metric_summary(predictions, "market_probabilities")
    model_metrics = common.metric_summary(predictions, "model_probabilities")
    market_draw = common.draw_summary(predictions, "market_probabilities")
    model_draw = common.draw_summary(predictions, "model_probabilities")
    minimum = int(config.get("minimum_market_available_rows", 900))
    gates = {
        "market_available_coverage": len(predictions) >= minimum,
        "all_market_available_rows_processed": len(predictions) + len(errors) == len(raw_rows) - unavailable,
        "no_malformed_market_available_rows": len(errors) == 0,
        "log_loss_nonworse_than_market": model_metrics["log_loss"] <= market_metrics["log_loss"] + 1e-12,
        "brier_nonworse_than_market": model_metrics["brier"] <= market_metrics["brier"] + 1e-12,
        "rps_nonworse_than_market": model_metrics["rps"] <= market_metrics["rps"] + 1e-12,
        "accuracy_nonworse_than_market": model_metrics["accuracy"] >= market_metrics["accuracy"] - 1e-12,
        "draw_brier_nonworse_than_market": model_draw["draw_brier"] <= market_draw["draw_brier"] + 1e-12,
        "draw_top1_count_nonworse": model_draw["draw_top1_count"] >= market_draw["draw_top1_count"],
    }
    passed = all(gates.values())
    return {
        "schema_version": "V5.1.0-direct-draw-residual-r2-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_RESEARCH_CHALLENGE" if passed else "FAIL_RESEARCH_CHALLENGE_NO_PROMOTION",
        "classification": "RETROSPECTIVE_PREQUENTIAL_DIRECT_DRAW_DIAGNOSTIC_FORMAL_WEIGHT_0",
        "benchmark_rows": len(raw_rows),
        "market_unavailable_rows": unavailable,
        "sample_count": len(predictions),
        "input_errors": errors,
        "market_baseline": market_metrics,
        "direct_draw_model": model_metrics,
        "delta_model_minus_market": {
            "accuracy": model_metrics["accuracy"] - market_metrics["accuracy"],
            "log_loss": model_metrics["log_loss"] - market_metrics["log_loss"],
            "brier": model_metrics["brier"] - market_metrics["brier"],
            "rps": model_metrics["rps"] - market_metrics["rps"],
        },
        "draw_audit": {"market": market_draw, "model": model_draw},
        "challenge_gate": {"passed": passed, "checks": gates},
        "contract": config,
        "governance": {
            "formal_weight": 0,
            "context_effect_weight": 0,
            "manual_draw_adjustment": False,
            "retrospective_closing_odds_without_original_timestamp": True,
            "formal_probability_output": False,
            "ev_available": False,
            "automatic_promotion": False,
        },
        "next_action": "retain only if every proper-score and draw gate passes; otherwise redesign the draw state rather than tuning this failed run",
        "predictions": predictions,
    }


def self_test() -> None:
    cfg = {
        "features": {
            "pmax_edges": [0.0, 1.01], "pdraw_edges": [0.0, 1.01],
            "home_away_balance_edges": [0.0, 1.01], "elo_diff_edges": [-1000000, 1000000],
            "recent_draw_edges": [-0.01, 1.01], "recent_window": 3, "recent_minimum": 1,
        },
        "team_state": {"elo_initial": 1500.0, "elo_home_advantage": 0.0, "elo_k": 20.0},
        "beta_shrinkage": {"global_prior_strength": 2.0, "competition_prior_strength": 2.0, "full_cell_prior_strength": 2.0},
    }
    row = {"competition_id": "X", "home_team": "A", "away_team": "B", "market": {"probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3}}, "actual": "draw"}
    model = DirectDrawModel(cfg)
    features = model.frozen_features(row)
    before = model.predict(features)
    for _ in range(5):
        frozen = model.frozen_features(row)
        model.update(row, frozen)
    after = model.predict(model.frozen_features(row))
    assert after["draw"] > before["draw"]
    before_ratio = before["home"] / before["away"]
    after_ratio = after["home"] / after["away"]
    assert abs(before_ratio - after_ratio) < 1e-12
    assert abs(sum(after.values()) - 1.0) < 1e-12


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--benchmark", type=Path, default=BENCHMARK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}, ensure_ascii=False))
        return 0
    payload = evaluate(common.load_json(args.config), common.load_json(args.benchmark))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "benchmark_rows": payload["benchmark_rows"],
        "market_unavailable_rows": payload["market_unavailable_rows"],
        "sample_count": payload["sample_count"],
        "market_baseline": payload["market_baseline"],
        "direct_draw_model": payload["direct_draw_model"],
        "delta": payload["delta_model_minus_market"],
        "draw_audit": payload["draw_audit"],
        "challenge_gate": payload["challenge_gate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
