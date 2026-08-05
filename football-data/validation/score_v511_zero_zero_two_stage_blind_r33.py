#!/usr/bin/env python3
"""Freeze R33 two-stage 0-0 predictions from public inputs only.

For each candidate, estimate empirical P(HT 0-0) and empirical
P(FT 0-0 | HT 0-0) from each team's strictly prior matches, then multiply the
two stage estimates. No smoothing, label access, web context or event inference is used.
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
DEFAULT_CONFIG = ROOT / "config" / "v511_zero_zero_two_stage_screen10_r33.json"
DEFAULT_PUBLIC = ROOT / "manifests" / "v511_zero_zero_two_stage_screen10_r33_public.json"
DEFAULT_OUT = ROOT / "manifests" / "v511_zero_zero_two_stage_screen10_r33_blind_predictions.json"


class R33ScoreError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R33ScoreError(f"JSON root must be object: {path}")
    return value


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


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
            raise R33ScoreError(f"missing source file: {source_file}")
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
                    "fthg": int(fthg),
                    "ftag": int(ftag),
                    "hthg": hthg,
                    "htag": htag,
                })
        return parsed


def team_two_stage(
    cache: SourceCache,
    source_file: str,
    match_date: str,
    team_name: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    cutoff = datetime.fromisoformat(match_date).date()
    team_norm = r22.normalize_team(team_name)
    matches: list[dict[str, Any]] = []
    for row in cache.rows(source_file):
        if row["date"] >= cutoff:
            continue
        if row["home_norm"] != team_norm and row["away_norm"] != team_norm:
            continue
        matches.append(row)
    matches.sort(key=lambda row: row["date"], reverse=True)
    selected = matches[: int(config["history_window_matches_per_team"])]
    if len(selected) < int(config["minimum_history_matches_per_team"]):
        return None
    halftime_known = [
        row for row in selected
        if row["hthg"] is not None and row["htag"] is not None
    ]
    if len(halftime_known) < int(config["minimum_halftime_known_matches_per_team"]):
        return None
    ht00 = [
        row for row in halftime_known
        if int(row["hthg"]) == 0 and int(row["htag"]) == 0
    ]
    if len(ht00) < int(config["minimum_halftime_zero_zero_observations_per_team"]):
        return None
    survived = [
        row for row in ht00
        if int(row["fthg"]) == 0 and int(row["ftag"]) == 0
    ]
    return {
        "history_matches": len(selected),
        "halftime_known_matches": len(halftime_known),
        "halftime_zero_zero_matches": len(ht00),
        "fulltime_zero_zero_after_halftime_zero_zero": len(survived),
        "p_halftime_zero_zero": len(ht00) / len(halftime_known),
        "p_fulltime_zero_zero_given_halftime_zero_zero": len(survived) / len(ht00),
    }


def candidate_two_stage(
    candidate: dict[str, Any], cache: SourceCache, config: dict[str, Any]
) -> dict[str, Any]:
    home = team_two_stage(
        cache,
        candidate["source_file"],
        candidate["match_date"],
        candidate["home_team"],
        config,
    )
    away = team_two_stage(
        cache,
        candidate["source_file"],
        candidate["match_date"],
        candidate["away_team"],
        config,
    )
    if home is None or away is None:
        return {
            "home_history": home,
            "away_history": away,
            "stage_1_p_halftime_zero_zero": None,
            "stage_2_p_survival_given_halftime_zero_zero": None,
            "joint_zero_zero_score": None,
        }
    stage_1 = mean([
        float(home["p_halftime_zero_zero"]),
        float(away["p_halftime_zero_zero"]),
    ])
    stage_2 = mean([
        float(home["p_fulltime_zero_zero_given_halftime_zero_zero"]),
        float(away["p_fulltime_zero_zero_given_halftime_zero_zero"]),
    ])
    return {
        "home_history": home,
        "away_history": away,
        "stage_1_p_halftime_zero_zero": stage_1,
        "stage_2_p_survival_given_halftime_zero_zero": stage_2,
        "joint_zero_zero_score": stage_1 * stage_2,
    }


def pair_prediction(left: float | None, right: float | None, threshold: float) -> str:
    if left is None or right is None:
        return "TIE"
    difference = left - right
    if difference >= threshold:
        return "A"
    if difference <= -threshold:
        return "B"
    return "TIE"


def run(config: dict[str, Any], public: dict[str, Any], out_path: Path) -> dict[str, Any]:
    cache = SourceCache()
    predictions: list[dict[str, Any]] = []
    threshold = float(config["joint_score_pairwise_threshold"])
    for pair in public["pairs"]:
        candidates = {candidate["side"]: candidate for candidate in pair["candidates"]}
        if set(candidates) != {"A", "B"}:
            raise R33ScoreError(f"{pair['pair_id']}: expected A and B")
        score_a = candidate_two_stage(candidates["A"], cache, config)
        score_b = candidate_two_stage(candidates["B"], cache, config)
        joint_a = finite(score_a["joint_zero_zero_score"])
        joint_b = finite(score_b["joint_zero_zero_score"])
        prediction = pair_prediction(joint_a, joint_b, threshold)
        predictions.append({
            "pair_id": pair["pair_id"],
            "prediction": prediction,
            "joint_score_difference_A_minus_B": (
                joint_a - joint_b
                if joint_a is not None and joint_b is not None
                else None
            ),
            "side_A_two_stage": score_a,
            "side_B_two_stage": score_b,
        })
    payload: dict[str, Any] = {
        "schema_version": "v511_zero_zero_two_stage_screen10_r33_blind_predictions.1",
        "status": "PASS_R33_TWO_STAGE_BLIND_PREDICTIONS_FROZEN_BEFORE_LABEL_ACCESS",
        "classification": config["classification"],
        "formal_weight": 0,
        "pairs": len(predictions),
        "directional_predictions": sum(
            row["prediction"] in {"A", "B"} for row in predictions
        ),
        "ties": sum(row["prediction"] == "TIE" for row in predictions),
        "label_seal_sha256": public["label_seal_sha256"],
        "prediction_contract": config["estimator_contract"],
        "predictions": predictions,
        "ruling": {
            "hidden_labels_read": False,
            "web_context_used": False,
            "current_match_rows_used": False,
            "same_day_rows_used": False,
            "minute_events_inferred": False,
            "smoothing_used": False,
            "resampling_allowed": False,
            "training_performed": False,
            "formal_promotion_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    payload["prediction_payload_sha256"] = sha256_text(canonical(payload))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return payload


def self_test() -> None:
    assert pair_prediction(0.20, 0.10, 0.02) == "A"
    assert pair_prediction(0.10, 0.20, 0.02) == "B"
    assert pair_prediction(0.20, 0.19, 0.02) == "TIE"
    assert pair_prediction(None, 0.20, 0.02) == "TIE"
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
    print(json.dumps(
        run(load_json(args.config), load_json(args.public), args.out),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
