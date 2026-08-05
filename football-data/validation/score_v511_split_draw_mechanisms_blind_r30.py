#!/usr/bin/env python3
"""Freeze R30A/R30B pair predictions from public packets only.

The scorer reads no hidden-label path. It uses closing market fields plus each team's last
8 matches strictly before the candidate match date. Same-day rows and the candidate row
are excluded. Components cast equal pairwise votes under thresholds frozen in the R30
config; tied pairs remain TIE and later count as failures.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import evaluate_v511_market_draw_screen10_r22 as r22

ROOT = r22.ROOT
DEFAULT_CONFIG = ROOT / "config" / "v511_split_draw_mechanisms_screen10_r30.json"
DEFAULT_PUBLIC_DIR = ROOT / "manifests" / "r30_public"
DEFAULT_OUT = ROOT / "manifests" / "v511_split_draw_mechanisms_blind_predictions_r30.json"


class ScoreError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ScoreError(f"JSON root must be object: {path}")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mean(values: list[float]) -> float:
    return float(sum(values) / len(values))


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class SourceCache:
    def __init__(self) -> None:
        self._rows: dict[str, list[dict[str, Any]]] = {}

    def rows(self, source_file: str) -> list[dict[str, Any]]:
        if source_file not in self._rows:
            self._rows[source_file] = self._load(source_file)
        return self._rows[source_file]

    def _load(self, source_file: str) -> list[dict[str, Any]]:
        path = ROOT / source_file
        if not path.is_file():
            raise ScoreError(f"missing source file: {source_file}")
        parsed: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            date_field = r22.field_name(headers, ["Date", "date", "kickoff_at", "kickoff_utc"])
            home_field = r22.field_name(headers, ["HomeTeam", "home_team", "home"])
            away_field = r22.field_name(headers, ["AwayTeam", "away_team", "away"])
            home_goals_field = r22.field_name(headers, ["FTHG", "home_goals_90", "home_score"])
            away_goals_field = r22.field_name(headers, ["FTAG", "away_goals_90", "away_score"])
            for row_number, row in enumerate(reader, start=2):
                date = r22.parse_date(r22.text_value(row, date_field))
                home = r22.text_value(row, home_field)
                away = r22.text_value(row, away_field)
                home_goals = r22.int_value(row, home_goals_field)
                away_goals = r22.int_value(row, away_goals_field)
                if date is None or not home or not away or home_goals is None or away_goals is None:
                    continue
                parsed.append({
                    "row_number": row_number,
                    "date": date.date(),
                    "home": home,
                    "away": away,
                    "home_norm": r22.normalize_team(home),
                    "away_norm": r22.normalize_team(away),
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                })
        return parsed


def team_metrics(
    cache: SourceCache,
    source_file: str,
    match_date: str,
    team_name: str,
    history_window: int,
    minimum_history: int,
) -> dict[str, Any] | None:
    cutoff = datetime.fromisoformat(match_date).date()
    team_norm = r22.normalize_team(team_name)
    matches: list[dict[str, Any]] = []
    for row in cache.rows(source_file):
        if row["date"] >= cutoff:
            continue
        if row["home_norm"] == team_norm:
            gf, ga = row["home_goals"], row["away_goals"]
        elif row["away_norm"] == team_norm:
            gf, ga = row["away_goals"], row["home_goals"]
        else:
            continue
        matches.append({"date": row["date"], "gf": gf, "ga": ga})
    matches.sort(key=lambda row: row["date"], reverse=True)
    selected = matches[:history_window]
    if len(selected) < minimum_history:
        return None
    gf_values = [float(row["gf"]) for row in selected]
    ga_values = [float(row["ga"]) for row in selected]
    totals = [gf + ga for gf, ga in zip(gf_values, ga_values)]
    goal_differences = [gf - ga for gf, ga in zip(gf_values, ga_values)]
    points = [3.0 if gd > 0 else 1.0 if gd == 0 else 0.0 for gd in goal_differences]
    return {
        "matches": len(selected),
        "gf_avg": mean(gf_values),
        "ga_avg": mean(ga_values),
        "ppg": mean(points),
        "draw_rate": mean([1.0 if gd == 0 else 0.0 for gd in goal_differences]),
        "low_total_rate": mean([1.0 if total <= 2.0 else 0.0 for total in totals]),
        "high_total_rate": mean([1.0 if total >= 4.0 else 0.0 for total in totals]),
        "btts_rate": mean([1.0 if gf > 0 and ga > 0 else 0.0 for gf, ga in zip(gf_values, ga_values)]),
        "close_margin_rate": mean([1.0 if abs(gd) <= 1.0 else 0.0 for gd in goal_differences]),
    }


def candidate_features(
    candidate: dict[str, Any], cache: SourceCache, history_window: int, minimum_history: int
) -> dict[str, Any]:
    home = team_metrics(
        cache, candidate["source_file"], candidate["match_date"], candidate["home_team"],
        history_window, minimum_history,
    )
    away = team_metrics(
        cache, candidate["source_file"], candidate["match_date"], candidate["away_team"],
        history_window, minimum_history,
    )
    closing_1x2 = candidate["market"]["one_x_two"]["closing"]
    closing_ou = candidate["market"]["over_under"]["closing_prices"]
    close_draw = finite(closing_1x2["de_vig"]["draw"]) if closing_1x2 else None
    home_share = finite(closing_1x2["home_share_non_draw"]) if closing_1x2 else None
    close_under = finite(closing_ou["de_vig"]["second"]) if closing_ou else None
    features: dict[str, Any] = {
        "close_draw_probability": close_draw,
        "close_under_probability": close_under,
        "market_home_share_non_draw": home_share,
        "home_history": home,
        "away_history": away,
    }
    if home is None or away is None:
        features.update({
            "combined_gf_avg": None,
            "combined_ga_avg": None,
            "risk_aversion": None,
            "close_match_consistency": None,
            "ppg_gap_normalized": None,
            "two_sided_attack": None,
            "open_match_consistency": None,
        })
        return features
    features.update({
        "combined_gf_avg": mean([home["gf_avg"], away["gf_avg"]]),
        "combined_ga_avg": mean([home["ga_avg"], away["ga_avg"]]),
        "risk_aversion": mean([
            home["low_total_rate"], away["low_total_rate"], home["draw_rate"], away["draw_rate"]
        ]),
        "close_match_consistency": mean([home["close_margin_rate"], away["close_margin_rate"]]),
        "ppg_gap_normalized": abs(home["ppg"] - away["ppg"]) / 3.0,
        "two_sided_attack": min(home["gf_avg"], away["gf_avg"]),
        "open_match_consistency": mean([
            home["high_total_rate"], away["high_total_rate"], home["btts_rate"], away["btts_rate"]
        ]),
    })
    return features


def lane_metrics(lane: str, features: dict[str, Any]) -> dict[str, float | None]:
    close_under = finite(features.get("close_under_probability"))
    close_draw = finite(features.get("close_draw_probability"))
    home_share = finite(features.get("market_home_share_non_draw"))
    combined_gf = finite(features.get("combined_gf_avg"))
    combined_ga = finite(features.get("combined_ga_avg"))
    if lane == "R30A":
        return {
            "market_low_total": close_under,
            "draw_price_support": close_draw,
            "recent_attack_suppression": -combined_gf if combined_gf is not None else None,
            "recent_risk_aversion": finite(features.get("risk_aversion")),
            "recent_defensive_stability": -combined_ga if combined_ga is not None else None,
            "recent_close_match_consistency": finite(features.get("close_match_consistency")),
        }
    market_imbalance = abs(home_share - 0.5) / 0.5 if home_share is not None else None
    ppg_gap = finite(features.get("ppg_gap_normalized"))
    balance = None
    if market_imbalance is not None and ppg_gap is not None:
        balance = -(0.5 * market_imbalance + 0.5 * ppg_gap)
    return {
        "market_high_total": (1.0 - close_under) if close_under is not None else None,
        "strength_balance": balance,
        "recent_defensive_weakness": combined_ga,
        "recent_combined_attack": combined_gf,
        "recent_two_sided_attack": finite(features.get("two_sided_attack")),
        "recent_open_match_consistency": finite(features.get("open_match_consistency")),
    }


def threshold_for(component: str, thresholds: dict[str, Any]) -> float:
    if component in {"market_low_total", "market_high_total"}:
        return float(thresholds["market_total_probability"])
    if component == "draw_price_support":
        return float(thresholds["market_probability"])
    if component == "strength_balance":
        return float(thresholds["strength_balance"])
    if component in {
        "recent_attack_suppression", "recent_defensive_stability", "recent_defensive_weakness",
        "recent_combined_attack", "recent_two_sided_attack",
    }:
        return float(thresholds["recent_goal_average"])
    return float(thresholds["recent_rate"])


def component_vote(left: float | None, right: float | None, threshold: float) -> str:
    if left is None or right is None:
        return "TIE"
    difference = left - right
    if difference >= threshold:
        return "A"
    if difference <= -threshold:
        return "B"
    return "TIE"


def score_lane(
    lane: str, public_packet: dict[str, Any], config: dict[str, Any], cache: SourceCache
) -> dict[str, Any]:
    operational = config["operational_blind_scoring"]
    history_window = int(operational["history_window_matches_per_team"])
    minimum_history = int(operational["minimum_history_matches_per_team"])
    thresholds = operational["thresholds"]
    rows: list[dict[str, Any]] = []
    for pair in public_packet["pairs"]:
        candidates = {candidate["side"]: candidate for candidate in pair["candidates"]}
        if set(candidates) != {"A", "B"}:
            raise ScoreError(f"{lane} {pair['pair_id']}: expected A and B")
        feature_a = candidate_features(candidates["A"], cache, history_window, minimum_history)
        feature_b = candidate_features(candidates["B"], cache, history_window, minimum_history)
        metrics_a = lane_metrics(lane, feature_a)
        metrics_b = lane_metrics(lane, feature_b)
        votes: dict[str, str] = {}
        count_a = 0
        count_b = 0
        for component in config["lanes"][lane]["rubric"]:
            vote = component_vote(
                finite(metrics_a.get(component)), finite(metrics_b.get(component)),
                threshold_for(component, thresholds),
            )
            votes[component] = vote
            count_a += int(vote == "A")
            count_b += int(vote == "B")
        prediction = "A" if count_a > count_b else "B" if count_b > count_a else "TIE"
        rows.append({
            "pair_id": pair["pair_id"],
            "prediction": prediction,
            "votes_A": count_a,
            "votes_B": count_b,
            "component_votes": votes,
            "side_A_metrics": metrics_a,
            "side_B_metrics": metrics_b,
            "side_A_features": feature_a,
            "side_B_features": feature_b,
        })
    return {
        "lane": lane,
        "lane_name": config["lanes"][lane]["name"],
        "public_packet_sha256": sha256_text(canonical(public_packet)),
        "label_seal_sha256": public_packet["label_seal_sha256"],
        "pairs": len(rows),
        "directional_predictions": sum(row["prediction"] in {"A", "B"} for row in rows),
        "ties": sum(row["prediction"] == "TIE" for row in rows),
        "predictions": rows,
    }


def run(config: dict[str, Any], public_dir: Path, out_path: Path) -> dict[str, Any]:
    cache = SourceCache()
    lanes: list[dict[str, Any]] = []
    for lane in ("R30A", "R30B"):
        packet_path = public_dir / f"v511_{lane.casefold()}_public.json"
        lanes.append(score_lane(lane, load_json(packet_path), config, cache))
    payload: dict[str, Any] = {
        "schema_version": "v511_split_draw_mechanisms_blind_predictions_r30.1",
        "status": "PASS_R30_BLIND_PREDICTIONS_FROZEN_BEFORE_LABEL_ACCESS",
        "classification": config["classification"],
        "formal_weight": 0,
        "operational_blind_scoring": config["operational_blind_scoring"],
        "lanes": lanes,
        "ruling": {
            "hidden_labels_read": False,
            "web_context_used": False,
            "current_match_rows_used": False,
            "same_day_rows_used": False,
            "resampling_allowed": False,
            "training_performed": False,
            "formal_promotion_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    payload["prediction_payload_sha256"] = sha256_text(canonical(payload))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return payload


def self_test() -> None:
    assert component_vote(0.60, 0.55, 0.02) == "A"
    assert component_vote(0.50, 0.55, 0.02) == "B"
    assert component_vote(0.50, 0.51, 0.02) == "TIE"
    assert component_vote(None, 0.51, 0.02) == "TIE"
    print(json.dumps({"self_test": "PASS"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(run(load_json(args.config), args.public_dir, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
