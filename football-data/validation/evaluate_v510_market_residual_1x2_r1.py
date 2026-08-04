#!/usr/bin/env python3
"""Frozen market-centered residual 1X2 diagnostic for CURRENT V5.1.0 research.

The model is deliberately simple and auditable. It starts from de-vigged market probabilities
and applies hierarchical Dirichlet residual learning in strict date order. All matches on the
same calendar day are predicted before any result from that day updates the model or team state.

This is a retrospective prequential diagnostic on the frozen neutral 1000 benchmark. Closing
odds have no original tradable timestamp, so the output can reject an architecture but cannot
promote it or support EV.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_context_market_prototype_r1.json"
BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_market_residual_1x2_r1_status.json"
DIRECTIONS = ("home", "draw", "away")
EPS = 1e-15


class ModelError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ModelError(f"missing input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ModelError(f"JSON root is not an object: {path.relative_to(ROOT)}")
    return value


def normalized_probs(value: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for direction in DIRECTIONS:
        try:
            probability = float(value[direction])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError(f"invalid probability for {direction}: {value}") from exc
        if not math.isfinite(probability) or probability <= 0:
            raise ModelError(f"nonpositive probability for {direction}: {probability}")
        out[direction] = probability
    total = sum(out.values())
    if not math.isfinite(total) or total <= 0:
        raise ModelError("invalid probability total")
    return {direction: out[direction] / total for direction in DIRECTIONS}


def bucket(value: float, edges: list[float]) -> int:
    for index in range(len(edges) - 1):
        if edges[index] <= value < edges[index + 1]:
            return index
    return len(edges) - 2


def actual_direction(row: dict[str, Any]) -> str:
    value = str(row.get("actual") or "").strip().casefold()
    aliases = {"h": "home", "home": "home", "d": "draw", "draw": "draw", "a": "away", "away": "away"}
    if value not in aliases:
        raise ModelError(f"invalid actual direction: {value}")
    return aliases[value]


def identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    key = (
        str(row.get("competition_id") or "").strip(),
        str(row.get("date") or "").strip(),
        str(row.get("home_team") or "").strip(),
        str(row.get("away_team") or "").strip(),
    )
    if not all(key):
        raise ModelError(f"incomplete benchmark identity: {row}")
    return key


def posterior(counts: Counter[str], prior: dict[str, float], strength: float) -> dict[str, float]:
    if strength <= 0:
        raise ModelError("prior strength must be positive")
    n = sum(int(counts[d]) for d in DIRECTIONS)
    denominator = n + strength
    result = {d: (float(counts[d]) + strength * prior[d]) / denominator for d in DIRECTIONS}
    total = sum(result.values())
    return {d: result[d] / total for d in DIRECTIONS}


def metric_summary(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    hits = 0
    log_loss = 0.0
    brier = 0.0
    rps = 0.0
    for row in rows:
        probs = row[field]
        actual = row["actual"]
        pick = max(DIRECTIONS, key=lambda d: probs[d])
        hits += int(pick == actual)
        log_loss -= math.log(max(EPS, min(1.0, probs[actual])))
        target = {d: 1.0 if actual == d else 0.0 for d in DIRECTIONS}
        brier += sum((probs[d] - target[d]) ** 2 for d in DIRECTIONS)
        rps += ((probs["home"] - target["home"]) ** 2 +
                (probs["home"] + probs["draw"] - target["home"] - target["draw"]) ** 2) / 2.0
    n = len(rows)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n,
        "log_loss": log_loss / n,
        "brier": brier / n,
        "rps": rps / n,
    }


def draw_summary(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    actual_draws = sum(row["actual"] == "draw" for row in rows)
    top1_rows = [row for row in rows if max(DIRECTIONS, key=lambda d: row[field][d]) == "draw"]
    correct = sum(row["actual"] == "draw" for row in top1_rows)
    mean_draw = sum(float(row[field]["draw"]) for row in rows) / len(rows)
    draw_brier = sum(
        (float(row[field]["draw"]) - (1.0 if row["actual"] == "draw" else 0.0)) ** 2
        for row in rows
    ) / len(rows)
    return {
        "count": len(rows),
        "actual_draw_count": actual_draws,
        "actual_draw_rate": actual_draws / len(rows),
        "mean_draw_probability": mean_draw,
        "draw_probability_bias": mean_draw - actual_draws / len(rows),
        "draw_brier": draw_brier,
        "draw_top1_count": len(top1_rows),
        "draw_top1_rate": len(top1_rows) / len(rows),
        "draw_top1_precision": correct / len(top1_rows) if top1_rows else None,
        "draw_top1_recall": correct / actual_draws if actual_draws else None,
    }


class MarketResidualModel:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.global_counts: defaultdict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
        self.competition_counts: defaultdict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
        self.full_counts: defaultdict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
        self.ratings: defaultdict[tuple[str, str], float] = defaultdict(lambda: float(cfg["elo_initial"]))
        window = int(cfg["recent_window"])
        self.recent_draws: defaultdict[tuple[str, str], deque[int]] = defaultdict(lambda: deque(maxlen=window))

    def recent_draw_bin(self, competition: str, team: str) -> str:
        values = self.recent_draws[(competition, team)]
        if len(values) < int(self.cfg["recent_minimum"]):
            return "NA"
        return str(bucket(sum(values) / len(values), [float(x) for x in self.cfg["recent_draw_edges"]]))

    def frozen_features(self, row: dict[str, Any]) -> dict[str, Any]:
        competition = str(row["competition_id"])
        home = str(row["home_team"])
        away = str(row["away_team"])
        market = normalized_probs((row.get("market") or {}).get("probabilities") or {})
        pick = max(DIRECTIONS, key=lambda d: market[d])
        pmax = market[pick]
        home_rating = self.ratings[(competition, home)]
        away_rating = self.ratings[(competition, away)]
        elo_diff = home_rating + float(self.cfg["elo_home_advantage"]) - away_rating
        pmax_bin = bucket(pmax, [float(x) for x in self.cfg["pmax_edges"]])
        pdraw_bin = bucket(market["draw"], [float(x) for x in self.cfg["pdraw_edges"]])
        elo_bin = bucket(elo_diff, [float(x) for x in self.cfg["elo_diff_edges"]])
        home_draw_bin = self.recent_draw_bin(competition, home)
        away_draw_bin = self.recent_draw_bin(competition, away)
        global_key = (pick, pmax_bin, pdraw_bin)
        competition_key = (competition, pick, pmax_bin, pdraw_bin)
        full_key = (competition, pick, pmax_bin, pdraw_bin, elo_bin, home_draw_bin, away_draw_bin)
        return {
            "market": market,
            "market_pick": pick,
            "pmax": pmax,
            "pmax_bin": pmax_bin,
            "pdraw_bin": pdraw_bin,
            "elo_diff": elo_diff,
            "elo_bin": elo_bin,
            "home_recent_draw_bin": home_draw_bin,
            "away_recent_draw_bin": away_draw_bin,
            "global_key": global_key,
            "competition_key": competition_key,
            "full_key": full_key,
        }

    def predict(self, features: dict[str, Any]) -> dict[str, float]:
        market = features["market"]
        global_p = posterior(
            self.global_counts[features["global_key"]],
            market,
            float(self.cfg["global_prior_strength"]),
        )
        competition_p = posterior(
            self.competition_counts[features["competition_key"]],
            global_p,
            float(self.cfg["competition_prior_strength"]),
        )
        return posterior(
            self.full_counts[features["full_key"]],
            competition_p,
            float(self.cfg["full_cell_prior_strength"]),
        )

    def update(self, row: dict[str, Any], features: dict[str, Any]) -> None:
        actual = actual_direction(row)
        self.global_counts[features["global_key"]][actual] += 1
        self.competition_counts[features["competition_key"]][actual] += 1
        self.full_counts[features["full_key"]][actual] += 1

        competition = str(row["competition_id"])
        home = str(row["home_team"])
        away = str(row["away_team"])
        home_rating = self.ratings[(competition, home)]
        away_rating = self.ratings[(competition, away)]
        advantage = float(self.cfg["elo_home_advantage"])
        expected_home = 1.0 / (1.0 + 10.0 ** (-(home_rating + advantage - away_rating) / 400.0))
        actual_home = 1.0 if actual == "home" else 0.5 if actual == "draw" else 0.0
        delta = float(self.cfg["elo_k"]) * (actual_home - expected_home)
        self.ratings[(competition, home)] = home_rating + delta
        self.ratings[(competition, away)] = away_rating - delta
        draw_indicator = int(actual == "draw")
        self.recent_draws[(competition, home)].append(draw_indicator)
        self.recent_draws[(competition, away)].append(draw_indicator)


def evaluate(config: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    rows = benchmark.get("rows")
    if not isinstance(rows, list):
        raise ModelError("benchmark missing rows list")
    clean: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            errors.append({"index": index, "reason": "row_not_object"})
            continue
        try:
            key = identity(raw)
            if key in seen:
                raise ModelError("duplicate benchmark identity")
            seen.add(key)
            actual = actual_direction(raw)
            market = normalized_probs((raw.get("market") or {}).get("probabilities") or {})
            date = datetime.fromisoformat(str(raw["date"]).replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ModelError("naive benchmark date")
            clean.append({**raw, "actual": actual, "market": {**(raw.get("market") or {}), "probabilities": market}})
        except Exception as exc:
            errors.append({"index": index, "reason": f"{type(exc).__name__}: {exc}"})
    clean.sort(key=lambda row: (str(row["date"]), str(row["competition_id"]), str(row["home_team"]), str(row["away_team"])))

    model_cfg = config.get("market_residual") or {}
    model = MarketResidualModel(model_cfg)
    predictions: list[dict[str, Any]] = []
    by_day: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clean:
        by_day[str(row["date"])[:10]].append(row)

    for day in sorted(by_day):
        frozen_batch: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in by_day[day]:
            features = model.frozen_features(row)
            residual = model.predict(features)
            predictions.append({
                "competition_id": row["competition_id"],
                "season": row.get("season"),
                "date": row["date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "actual": row["actual"],
                "market_probabilities": features["market"],
                "model_probabilities": residual,
                "market_pick": max(DIRECTIONS, key=lambda d: features["market"][d]),
                "model_pick": max(DIRECTIONS, key=lambda d: residual[d]),
                "features": {
                    "pmax_bin": features["pmax_bin"],
                    "pdraw_bin": features["pdraw_bin"],
                    "elo_bin": features["elo_bin"],
                    "home_recent_draw_bin": features["home_recent_draw_bin"],
                    "away_recent_draw_bin": features["away_recent_draw_bin"],
                },
            })
            frozen_batch.append((row, features))
        for row, features in frozen_batch:
            model.update(row, features)

    market_metrics = metric_summary(predictions, "market_probabilities")
    model_metrics = metric_summary(predictions, "model_probabilities")
    market_draw = draw_summary(predictions, "market_probabilities")
    model_draw = draw_summary(predictions, "model_probabilities")

    by_competition: dict[str, Any] = {}
    competitions = sorted({str(row["competition_id"]) for row in predictions})
    for competition in competitions:
        subset = [row for row in predictions if row["competition_id"] == competition]
        by_competition[competition] = {
            "market": metric_summary(subset, "market_probabilities"),
            "model": metric_summary(subset, "model_probabilities"),
            "market_draw": draw_summary(subset, "market_probabilities"),
            "model_draw": draw_summary(subset, "model_probabilities"),
        }

    gates = {
        "coverage_1000": len(predictions) == 1000,
        "no_input_errors": len(errors) == 0,
        "log_loss_nonworse_than_market": model_metrics.get("log_loss", math.inf) <= market_metrics.get("log_loss", -math.inf) + 1e-12,
        "brier_nonworse_than_market": model_metrics.get("brier", math.inf) <= market_metrics.get("brier", -math.inf) + 1e-12,
        "rps_nonworse_than_market": model_metrics.get("rps", math.inf) <= market_metrics.get("rps", -math.inf) + 1e-12,
        "accuracy_nonworse_than_market": model_metrics.get("accuracy", -1.0) >= market_metrics.get("accuracy", 2.0) - 1e-12,
        "draw_brier_nonworse_than_market": model_draw.get("draw_brier", math.inf) <= market_draw.get("draw_brier", -math.inf) + 1e-12,
        "draw_top1_exists": int(model_draw.get("draw_top1_count") or 0) > 0,
    }
    challenge_pass = all(gates.values())
    return {
        "schema_version": "V5.1.0-market-residual-1x2-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_RESEARCH_CHALLENGE" if challenge_pass else "FAIL_RESEARCH_CHALLENGE_NO_PROMOTION",
        "classification": "RETROSPECTIVE_PREQUENTIAL_FIXED1000_DIAGNOSTIC_FORMAL_WEIGHT_0",
        "benchmark_schema": benchmark.get("schema_version"),
        "sample_count": len(predictions),
        "market_baseline": market_metrics,
        "market_residual_model": model_metrics,
        "delta_model_minus_market": {
            "accuracy": model_metrics.get("accuracy", 0.0) - market_metrics.get("accuracy", 0.0),
            "log_loss": model_metrics.get("log_loss", 0.0) - market_metrics.get("log_loss", 0.0),
            "brier": model_metrics.get("brier", 0.0) - market_metrics.get("brier", 0.0),
            "rps": model_metrics.get("rps", 0.0) - market_metrics.get("rps", 0.0),
        },
        "draw_audit": {"market": market_draw, "model": model_draw},
        "challenge_gate": {"passed": challenge_pass, "checks": gates},
        "by_competition": by_competition,
        "input_errors": errors,
        "model_contract": {
            "market_is_baseline_prior": True,
            "hierarchical_levels": ["global_market_cell", "competition_market_cell", "competition_market_team_state_cell"],
            "direct_draw_probability": True,
            "same_day_batch_update": True,
            "manual_draw_adjustment": False,
            "features": ["market_pick", "market_pmax_bin", "market_pdraw_bin", "pre_match_elo_bin", "home_prior_draw_bin", "away_prior_draw_bin"],
            "frozen_parameters": model_cfg,
        },
        "governance": {
            "formal_weight": 0,
            "retrospective_closing_odds_without_original_timestamp": True,
            "benchmark_labels_used_only_after_each_prediction": True,
            "benchmark_is_not_independent_promotion_evidence": True,
            "context_features_used": False,
            "formal_probability_output": False,
            "ev_available": False,
            "current_rule_change": False,
        },
        "next_action": (
            "attach frozen prospective context features and rerun on a sufficiently settled context sample"
            if challenge_pass
            else "retain market baseline; inspect failing gates before any context residual fit"
        ),
        "predictions": predictions,
    }


def self_test() -> None:
    cfg = {
        "pmax_edges": [0.0, 0.5, 1.01],
        "pdraw_edges": [0.0, 0.3, 1.01],
        "elo_diff_edges": [-1000000, 0, 1000000],
        "recent_draw_edges": [-0.01, 0.5, 1.01],
        "recent_window": 3,
        "recent_minimum": 1,
        "elo_initial": 1500.0,
        "elo_home_advantage": 0.0,
        "elo_k": 20.0,
        "global_prior_strength": 2.0,
        "competition_prior_strength": 2.0,
        "full_cell_prior_strength": 2.0,
    }
    row = {"competition_id": "X", "home_team": "A", "away_team": "B", "market": {"probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3}}, "actual": "draw"}
    model = MarketResidualModel(cfg)
    features = model.frozen_features(row)
    before = model.predict(features)
    assert max(abs(before[d] - row["market"]["probabilities"][d]) for d in DIRECTIONS) < 1e-12
    for _ in range(5):
        features = model.frozen_features(row)
        model.update(row, features)
    after = model.predict(model.frozen_features(row))
    assert after["draw"] > before["draw"]
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
    payload = evaluate(load_json(args.config), load_json(args.benchmark))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "sample_count": payload["sample_count"],
        "market_baseline": payload["market_baseline"],
        "market_residual_model": payload["market_residual_model"],
        "delta": payload["delta_model_minus_market"],
        "draw_audit": payload["draw_audit"],
        "challenge_gate": payload["challenge_gate"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
