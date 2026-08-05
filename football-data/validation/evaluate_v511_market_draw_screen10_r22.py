#!/usr/bin/env python3
"""R22 ten-pair market draw screen.

This is a deliberately small, deterministic screening gate. It selects one known draw
from each of ten competitions, matches each draw to a similar one-goal result without
using the draw price in matching, and asks whether the de-vig closing 1X2 draw
probability ranks the draw above its paired close win in at least 6 of 10 pairs.

The source prices are retrospective closing references unless an original quote timestamp
is present. This experiment is research-only: it cannot promote a model, create current
match probabilities, unlock a score matrix, produce exact scores, or calculate EV.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "processed"
DEFAULT_CONFIG = ROOT / "config" / "v511_market_draw_screen10_r22.json"
DEFAULT_OUT = ROOT / "manifests" / "v511_market_draw_screen10_r22_status.json"
DEFAULT_PAIRS = ROOT / "manifests" / "v511_market_draw_screen10_r22_pairs.csv"


class ScreenError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ScreenError("R22 config root must be an object")
    return value


def norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9><]+", "", str(value or "").casefold())


def normalize_team(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum())


def field_name(headers: list[str], aliases: list[str]) -> str | None:
    mapping = {norm_header(header): header for header in headers}
    for alias in aliases:
        hit = mapping.get(norm_header(alias))
        if hit is not None:
            return hit
    return None


def text_value(row: dict[str, Any], field: str | None) -> str:
    return str(row.get(field) or "").strip() if field else ""


def float_value(row: dict[str, Any], field: str | None) -> float | None:
    text = text_value(row, field)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def int_value(row: dict[str, Any], field: str | None) -> int | None:
    value = float_value(row, field)
    if value is None:
        return None
    integer = int(value)
    return integer if abs(value - integer) < 1e-9 and 0 <= integer <= 30 else None


def valid_price(value: float | None) -> bool:
    return value is not None and value > 1.0


def parse_date(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    direct = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(direct).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def devig(prices: tuple[float, ...]) -> tuple[float, ...]:
    inverses = np.asarray([1.0 / value for value in prices], dtype=float)
    total = float(inverses.sum())
    if not math.isfinite(total) or total <= 0:
        raise ScreenError("invalid market overround")
    return tuple(float(value / total) for value in inverses)


def find_triplet(row: dict[str, Any], headers: list[str]) -> tuple[str, tuple[float, float, float], int] | None:
    candidates = [
        ("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"),
        ("MaxCH", "MaxCD", "MaxCA"), ("B365CH", "B365CD", "B365CA"),
        ("PSH", "PSD", "PSA"), ("AvgH", "AvgD", "AvgA"),
        ("MaxH", "MaxD", "MaxA"), ("B365H", "B365D", "B365A"),
        ("home_odds", "draw_odds", "away_odds"),
    ]
    for priority, aliases in enumerate(candidates):
        fields = [field_name(headers, [alias]) for alias in aliases]
        if not all(fields):
            continue
        values = tuple(float_value(row, field) for field in fields)
        if all(valid_price(value) for value in values):
            prices = tuple(float(value) for value in values)
            return "/".join(str(field) for field in fields), prices, priority
    return None


def optional_markets(row: dict[str, Any], headers: list[str]) -> dict[str, float | None]:
    ah_field = field_name(headers, ["AHCh", "AHh", "asian_handicap_line", "handicap_line", "spread_line"])
    asian_line = float_value(row, ah_field)

    total_line: float | None = None
    over_probability: float | None = None
    for over_alias, under_alias, implicit_line in [
        ("PC>2.5", "PC<2.5", 2.5), ("AvgC>2.5", "AvgC<2.5", 2.5),
        ("MaxC>2.5", "MaxC<2.5", 2.5), ("B365C>2.5", "B365C<2.5", 2.5),
        ("P>2.5", "P<2.5", 2.5), ("Avg>2.5", "Avg<2.5", 2.5),
        ("over_odds", "under_odds", None),
    ]:
        over_field = field_name(headers, [over_alias])
        under_field = field_name(headers, [under_alias])
        over_price = float_value(row, over_field)
        under_price = float_value(row, under_field)
        if over_field and under_field and valid_price(over_price) and valid_price(under_price):
            over_probability = devig((float(over_price), float(under_price)))[0]
            total_line = implicit_line
            if total_line is None:
                line_field = field_name(headers, ["total_line", "over_under_line", "OUCh", "closing_total_line"])
                total_line = float_value(row, line_field)
            break
    return {"asian_line": asian_line, "total_line": total_line, "over_probability": over_probability}


def identity(row: dict[str, Any]) -> str:
    return "|".join([
        str(row["competition_id"]), str(row["season"]), str(row["date_key"]),
        str(row["home_team"]), str(row["away_team"]),
    ])


def hash_rank(seed: int, value: str) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()


def extract_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not PROCESSED.is_dir():
        raise ScreenError(f"missing processed root: {PROCESSED}")
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for path in sorted(PROCESSED.rglob("*.csv")):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = list(reader.fieldnames or [])
                competition_field = field_name(headers, ["competition_id", "competition", "league_id"])
                season_field = field_name(headers, ["season", "source_season", "Season"])
                date_field = field_name(headers, ["Date", "date", "kickoff_at", "kickoff_utc"])
                home_field = field_name(headers, ["HomeTeam", "home_team", "home"])
                away_field = field_name(headers, ["AwayTeam", "away_team", "away"])
                home_score_field = field_name(headers, ["FTHG", "home_goals_90", "home_score"])
                away_score_field = field_name(headers, ["FTAG", "away_goals_90", "away_score"])
                for row_number, raw in enumerate(reader, start=2):
                    market = find_triplet(raw, headers)
                    if market is None:
                        continue
                    competition = text_value(raw, competition_field) or path.parent.name
                    season = text_value(raw, season_field)
                    date = parse_date(text_value(raw, date_field))
                    home_team = text_value(raw, home_field)
                    away_team = text_value(raw, away_field)
                    home_goals = int_value(raw, home_score_field)
                    away_goals = int_value(raw, away_score_field)
                    if not (competition and date and home_team and away_team and home_goals is not None and away_goals is not None):
                        continue
                    fields, prices, priority = market
                    p_home, p_draw, p_away = devig(prices)
                    non_draw_total = p_home + p_away
                    if non_draw_total <= 0:
                        continue
                    optional = optional_markets(raw, headers)
                    row = {
                        "source_file": relative,
                        "row_number": row_number,
                        "competition_id": competition,
                        "season": season,
                        "date_key": date.date().isoformat(),
                        "date_ordinal": date.toordinal(),
                        "home_team": home_team,
                        "away_team": away_team,
                        "normalized_home_team": normalize_team(home_team),
                        "normalized_away_team": normalize_team(away_team),
                        "home_goals_90": home_goals,
                        "away_goals_90": away_goals,
                        "goal_difference": home_goals - away_goals,
                        "market_fields": fields,
                        "home_price": prices[0],
                        "draw_price": prices[1],
                        "away_price": prices[2],
                        "p_home": p_home,
                        "p_draw": p_draw,
                        "p_away": p_away,
                        "home_share_non_draw": p_home / non_draw_total,
                        "triplet_priority": priority,
                        **optional,
                    }
                    row["identity"] = identity(row)
                    rows.append(row)
        except Exception as exc:
            errors.append({"source_file": relative, "reason": f"{type(exc).__name__}: {exc}"})
    best: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda value: (
        value["identity"], value["triplet_priority"], value["source_file"], value["row_number"]
    )):
        best.setdefault(row["identity"], row)
    return list(best.values()), errors


def optional_distance(left: float | None, right: float | None, scale: float) -> float:
    left_ok = left is not None and math.isfinite(float(left))
    right_ok = right is not None and math.isfinite(float(right))
    if left_ok and right_ok:
        return ((float(left) - float(right)) / scale) ** 2
    return 1.0


def pair_cost(draw: dict[str, Any], win: dict[str, Any]) -> float:
    if draw["competition_id"] != win["competition_id"]:
        return 1e9
    season_penalty = 0.0 if draw["season"] == win["season"] else 9.0
    home_share = ((draw["home_share_non_draw"] - win["home_share_non_draw"]) / 0.10) ** 2
    date_gap = (min(abs(draw["date_ordinal"] - win["date_ordinal"]), 730) / 180.0) ** 2
    total_line = optional_distance(draw["total_line"], win["total_line"], 0.5)
    over_probability = optional_distance(draw["over_probability"], win["over_probability"], 0.10)
    asian_line = optional_distance(draw["asian_line"], win["asian_line"], 0.5)
    return float(season_penalty + home_share + 0.25 * date_gap + 0.5 * total_line + 0.5 * over_probability + 0.5 * asian_line)


def select_draws(rows: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    by_competition: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["goal_difference"] == 0:
            by_competition.setdefault(str(row["competition_id"]), []).append(row)
    representatives: list[dict[str, Any]] = []
    for competition, values in sorted(by_competition.items()):
        values.sort(key=lambda row: (hash_rank(seed, row["identity"]), row["identity"]))
        representatives.append(values[0])
    representatives.sort(key=lambda row: (hash_rank(seed, f"competition|{row['competition_id']}"), row["competition_id"]))
    if len(representatives) < count:
        raise ScreenError(f"only {len(representatives)} competitions have eligible draw prices; need {count}")
    return representatives[:count]


def make_pairs(draws: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wins = [row for row in rows if abs(int(row["goal_difference"])) == 1]
    if not wins:
        raise ScreenError("no eligible one-goal wins")
    cost = np.asarray([[pair_cost(draw, win) for win in wins] for draw in draws], dtype=float)
    draw_index, win_index = linear_sum_assignment(cost)
    if len(draw_index) != len(draws):
        raise ScreenError("R22 matching did not cover every draw")
    pairs: list[dict[str, Any]] = []
    for left, right in zip(draw_index, win_index):
        draw = draws[int(left)]
        win = wins[int(right)]
        if draw["competition_id"] != win["competition_id"] or cost[int(left), int(right)] >= 1e8:
            raise ScreenError(f"no same-competition close win for {draw['identity']}")
        difference = float(draw["p_draw"] - win["p_draw"])
        result = "win" if difference > 1e-12 else "loss" if difference < -1e-12 else "tie"
        pairs.append({
            "competition_id": draw["competition_id"],
            "draw_identity": draw["identity"],
            "draw_score": f"{draw['home_goals_90']}-{draw['away_goals_90']}",
            "draw_source": draw["source_file"],
            "draw_market_fields": draw["market_fields"],
            "draw_price": draw["draw_price"],
            "draw_p_draw": draw["p_draw"],
            "close_win_identity": win["identity"],
            "close_win_score": f"{win['home_goals_90']}-{win['away_goals_90']}",
            "close_win_source": win["source_file"],
            "close_win_market_fields": win["market_fields"],
            "close_win_draw_price": win["draw_price"],
            "close_win_p_draw": win["p_draw"],
            "p_draw_difference": difference,
            "pair_result": result,
            "matching_cost": float(cost[int(left), int(right)]),
            "same_season": draw["season"] == win["season"],
            "date_gap_days": abs(draw["date_ordinal"] - win["date_ordinal"]),
            "draw_home_share_non_draw": draw["home_share_non_draw"],
            "win_home_share_non_draw": win["home_share_non_draw"],
            "draw_total_line": draw["total_line"],
            "win_total_line": win["total_line"],
            "draw_over_probability": draw["over_probability"],
            "win_over_probability": win["over_probability"],
            "draw_asian_line": draw["asian_line"],
            "win_asian_line": win["asian_line"],
        })
    return sorted(pairs, key=lambda row: (row["competition_id"], row["draw_identity"]))


def write_pairs(path: Path, pairs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(pairs[0].keys()) if pairs else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pairs)


def run(config: dict[str, Any], out_path: Path, pairs_path: Path) -> dict[str, Any]:
    rows, errors = extract_rows()
    contract = config["sample_contract"]
    count = int(contract["draws"])
    seed = int(contract["seed"])
    draws = select_draws(rows, count, seed)
    pairs = make_pairs(draws, rows)
    wins = sum(row["pair_result"] == "win" for row in pairs)
    losses = sum(row["pair_result"] == "loss" for row in pairs)
    ties = sum(row["pair_result"] == "tie" for row in pairs)
    passed = wins >= int(contract["pass_wins"])
    selected_identity = "\n".join(row["draw_identity"] for row in pairs)
    receipt = {
        "schema_version": "v511_market_draw_screen10_r22_status.1",
        "status": (
            "PASS_R22_MARKET_DRAW_SIGNAL_SCREEN10_ADVANCE_TO_30"
            if passed else "FAIL_R22_MARKET_DRAW_SIGNAL_SCREEN10_STOP"
        ),
        "classification": config["classification"],
        "formal_weight": 0,
        "eligible_market_rows": len(rows),
        "file_errors": errors,
        "sample": {
            "pairs": len(pairs),
            "competitions": len({row["competition_id"] for row in pairs}),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "accuracy": wins / len(pairs),
            "pass_wins": int(contract["pass_wins"]),
            "passed": passed,
            "mean_p_draw_difference": float(np.mean([row["p_draw_difference"] for row in pairs])),
            "median_p_draw_difference": float(np.median([row["p_draw_difference"] for row in pairs])),
            "selected_draw_identity_sha256": hashlib.sha256(selected_identity.encode("utf-8")).hexdigest(),
        },
        "sample_contract": contract,
        "market_contract": config["market_contract"],
        "matching_contract": config["matching_contract"],
        "ruling": {
            "advance_to_30_pair_gate": passed,
            "no_repeated_resampling_allowed": True,
            "retrospective_market_reference_only": True,
            "formal_pre_match_snapshot_claim_allowed": False,
            "model_training_performed": False,
            "manual_draw_offset_used": False,
            "class_weight_used": False,
            "threshold_tuning_used": False,
            "current_match_probability_allowed": False,
            "unified_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    write_pairs(pairs_path, pairs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return receipt


def self_test() -> None:
    probs = devig((2.0, 3.0, 4.0))
    if not math.isclose(sum(probs), 1.0, abs_tol=1e-12):
        raise ScreenError("devig self-test failed")
    left = {"competition_id": "X", "season": "1", "date_ordinal": 1, "home_share_non_draw": 0.5,
            "total_line": 2.5, "over_probability": 0.5, "asian_line": 0.0}
    right = dict(left)
    if pair_cost(left, right) != 0.0:
        raise ScreenError("pair cost self-test failed")
    print(json.dumps({"self_test": "PASS", "devig": probs}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    receipt = run(load_json(args.config), args.out, args.pairs)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
