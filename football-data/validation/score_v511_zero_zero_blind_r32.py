#!/usr/bin/env python3
"""Freeze R32 0-0 pair predictions from public inputs only.

Uses closing market fields and each team's last eight matches strictly before the candidate
match date. Historical half-time scores are allowed; current and same-day rows are not.
The scorer has no hidden-label path.
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
DEFAULT_CONFIG = ROOT / "config" / "v511_zero_zero_screen10_r32.json"
DEFAULT_PUBLIC = ROOT / "manifests" / "v511_zero_zero_screen10_r32_public.json"
DEFAULT_OUT = ROOT / "manifests" / "v511_zero_zero_screen10_r32_blind_predictions.json"


class R32ScoreError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R32ScoreError(f"JSON root must be object: {path}")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mean(values: list[float]) -> float:
    return float(sum(values) / len(values))


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
            raise R32ScoreError(f"missing source file: {source_file}")
        parsed: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            date_field = r22.field_name(headers, ["Date", "date", "kickoff_at", "kickoff_utc"])
            home_field = r22.field_name(headers, ["HomeTeam", "home_team", "home"])
            away_field = r22.field_name(headers, ["AwayTeam", "away_team", "away"])
            fthg_field = r22.field_name(headers, ["FTHG", "home_goals_90", "home_score"])
            ftag_field = r22.field_name(headers, ["FTAG", "away_goals_90", "away_score"])
            hthg_field = r22.field_name(headers, ["HTHG", "home_goals_half"])
            htag_field = r22.field_name(headers, ["HTAG", "away_goals_half"])
            for row_number, row in enumerate(reader, start=2):
                date = r22.parse_date(r22.text_value(row, date_field))
                home = r22.text_value(row, home_field)
                away = r22.text_value(row, away_field)
                fthg = r22.int_value(row, fthg_field)
                ftag = r22.int_value(row, ftag_field)
                hthg = r22.int_value(row, hthg_field)
                htag = r22.int_value(row, htag_field)
                if date is None or not home or not away or fthg is None or ftag is None:
                    continue
                parsed.append({
                    "row_number": row_number,
                    "date": date.date(),
                    "home_norm": r22.normalize_team(home),
                    "away_norm": r22.normalize_team(away),
                    "fthg": fthg,
                    "ftag": ftag,
                    "hthg": hthg,
                    "htag": htag,
                })
        return parsed


def team_metrics(
    cache: SourceCache, source_file: str, match_date: str, team_name: str,
    history_window: int, minimum_history: int,
) -> dict[str, Any] | None:
    cutoff = datetime.fromisoformat(match_date).date()
    team_norm = r22.normalize_team(team_name)
    matches: list[dict[str, Any]] = []
    for row in cache.rows(source_file):
        if row["date"] >= cutoff:
            continue
        if row["home_norm"] == team_norm:
            gf, ga = row["fthg"], row["ftag"]
        elif row["away_norm"] == team_norm:
            gf, ga = row["ftag"], row["fthg"]
        else:
            continue
        matches.append({
            "date": row["date"],
            "gf": int(gf),
            "ga": int(ga),
            "hthg": row["hthg"],
            "htag": row["htag"],
        })
    matches.sort(key=lambda item: item["date"], reverse=True)
    selected = matches[:history_window]
    if len(selected) < minimum_history:
        return None
    halftime_known = [m for m in selected if m["hthg"] is not None and m["htag"] is not None]
    return {
        "matches": len(selected),
        "halftime_known_matches": len(halftime_known),
        "zero_zero_rate": mean([1.0 if m["gf"] == 0 and m["ga"] == 0 else 0.0 for m in selected]),
        "halftime_zero_zero_rate": (
            mean([1.0 if m["hthg"] == 0 and m["htag"] == 0 else 0.0 for m in halftime_known])
            if halftime_known else None
        ),
        "failure_to_score_rate": mean([1.0 if m["gf"] == 0 else 0.0 for m in selected]),
        "clean_sheet_rate": mean([1.0 if m["ga"] == 0 else 0.0 for m in selected]),
        "one_or_fewer_total_rate": mean([1.0 if m["gf"] + m["ga"] <= 1 else 0.0 for m in selected]),
    }


def candidate_metrics(
    candidate: dict[str, Any], cache: SourceCache, config: dict[str, Any]
) -> tuple[dict[str, float | None], dict[str, Any]]:
    history_window = int(config["history_window_matches_per_team"])
    minimum_history = int(config["minimum_history_matches_per_team"])
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
    balance = -(abs(home_share - 0.5) / 0.5) if home_share is not None else None
    raw = {
        "close_draw_probability": close_draw,
        "close_under_probability": close_under,
        "market_home_share_non_draw": home_share,
        "home_history": home,
        "away_history": away,
    }
    if home is None or away is None:
        return {
            "market_low_total": close_under,
            "draw_price_support": close_draw,
            "market_strength_balance": balance,
            "historical_zero_zero_rate": None,
            "historical_halftime_zero_zero_rate": None,
            "attack_dryness_and_clean_sheet_support": None,
        }, raw
    ht_values = [
        value for value in [home["halftime_zero_zero_rate"], away["halftime_zero_zero_rate"]]
        if value is not None
    ]
    dryness = mean([
        home["failure_to_score_rate"], away["failure_to_score_rate"],
        home["clean_sheet_rate"], away["clean_sheet_rate"],
        home["one_or_fewer_total_rate"], away["one_or_fewer_total_rate"],
    ])
    return {
        "market_low_total": close_under,
        "draw_price_support": close_draw,
        "market_strength_balance": balance,
        "historical_zero_zero_rate": mean([home["zero_zero_rate"], away["zero_zero_rate"]]),
        "historical_halftime_zero_zero_rate": mean(ht_values) if ht_values else None,
        "attack_dryness_and_clean_sheet_support": dryness,
    }, raw


def threshold(component: str, config: dict[str, Any]) -> float:
    values = config["pairwise_thresholds"]
    if component in {"market_low_total", "draw_price_support"}:
        return float(values["market_probability"])
    if component == "market_strength_balance":
        return float(values["strength_balance"])
    return float(values["historical_rate"])


def vote(left: float | None, right: float | None, margin: float) -> str:
    if left is None or right is None:
        return "TIE"
    difference = left - right
    if difference >= margin:
        return "A"
    if difference <= -margin:
        return "B"
    return "TIE"


def run(config: dict[str, Any], public: dict[str, Any], out_path: Path) -> dict[str, Any]:
    cache = SourceCache()
    predictions: list[dict[str, Any]] = []
    for pair in public["pairs"]:
        candidates = {candidate["side"]: candidate for candidate in pair["candidates"]}
        if set(candidates) != {"A", "B"}:
            raise R32ScoreError(f"{pair['pair_id']}: expected A and B")
        metrics_a, raw_a = candidate_metrics(candidates["A"], cache, config)
        metrics_b, raw_b = candidate_metrics(candidates["B"], cache, config)
        votes: dict[str, str] = {}
        count_a = 0
        count_b = 0
        for component in config["rubric"]:
            component_vote = vote(
                finite(metrics_a.get(component)), finite(metrics_b.get(component)),
                threshold(component, config),
            )
            votes[component] = component_vote
            count_a += int(component_vote == "A")
            count_b += int(component_vote == "B")
        prediction = "A" if count_a > count_b else "B" if count_b > count_a else "TIE"
        predictions.append({
            "pair_id": pair["pair_id"],
            "prediction": prediction,
            "votes_A": count_a,
            "votes_B": count_b,
            "component_votes": votes,
            "side_A_metrics": metrics_a,
            "side_B_metrics": metrics_b,
            "side_A_raw": raw_a,
            "side_B_raw": raw_b,
        })
    payload: dict[str, Any] = {
        "schema_version": "v511_zero_zero_screen10_r32_blind_predictions.1",
        "status": "PASS_R32_BLIND_PREDICTIONS_FROZEN_BEFORE_LABEL_ACCESS",
        "classification": config["classification"],
        "formal_weight": 0,
        "pairs": len(predictions),
        "directional_predictions": sum(row["prediction"] in {"A", "B"} for row in predictions),
        "ties": sum(row["prediction"] == "TIE" for row in predictions),
        "label_seal_sha256": public["label_seal_sha256"],
        "predictions": predictions,
        "ruling": {
            "hidden_labels_read": False,
            "web_context_used": False,
            "current_match_rows_used": False,
            "same_day_rows_used": False,
            "minute_events_inferred": False,
            "resampling_allowed": False,
            "training_performed": False,
            "formal_promotion_allowed": False
        },
        "hard_limits": config["hard_limits"],
    }
    payload["prediction_payload_sha256"] = sha256_text(canonical(payload))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return payload


def self_test() -> None:
    assert vote(0.60, 0.55, 0.02) == "A"
    assert vote(0.50, 0.55, 0.02) == "B"
    assert vote(0.50, 0.51, 0.02) == "TIE"
    assert vote(None, 0.51, 0.02) == "TIE"
    print(json.dumps({"self_test": "PASS"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(run(load_json(args.config), load_json(args.public), args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
