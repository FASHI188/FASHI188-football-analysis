#!/usr/bin/env python3
"""R29 fixed historical blind-reconstruction packet builder.

Reuses the exact deterministic R22 ten-pair sample. Public outputs contain only fixture
identity and pre-match market fields. Scores and labels are placed in a separate sealed
file so web/context reconstruction can be completed before labels are opened.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import evaluate_v511_market_draw_screen10_r22 as r22

ROOT = r22.ROOT
DEFAULT_CONFIG = ROOT / "config" / "v511_historical_blind_context_screen10_r29.json"
DEFAULT_STATUS = ROOT / "manifests" / "v511_historical_blind_context_screen10_r29_status.json"
DEFAULT_PUBLIC_JSON = ROOT / "manifests" / "v511_historical_blind_context_screen10_r29_public.json"
DEFAULT_PUBLIC_CSV = ROOT / "manifests" / "v511_historical_blind_context_screen10_r29_public.csv"
DEFAULT_HIDDEN = ROOT / "manifests" / "v511_historical_blind_context_screen10_r29_hidden_labels.json"


class BlindPacketError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BlindPacketError("config root must be object")
    return value


def field(headers: list[str], aliases: list[str]) -> str | None:
    return r22.field_name(headers, aliases)


def number(row: dict[str, Any], name: str | None) -> float | None:
    return r22.float_value(row, name)


def valid_price(value: float | None) -> bool:
    return value is not None and value > 1.0 and math.isfinite(value)


def read_source_row(source_file: str, row_number: int) -> tuple[dict[str, Any], list[str]]:
    path = ROOT / source_file
    if not path.is_file():
        raise BlindPacketError(f"missing source file: {source_file}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        for current, row in enumerate(reader, start=2):
            if current == int(row_number):
                return row, headers
    raise BlindPacketError(f"row {row_number} not found in {source_file}")


def triplet(row: dict[str, Any], headers: list[str], candidates: list[tuple[str, str, str]]) -> dict[str, Any] | None:
    for aliases in candidates:
        names = [field(headers, [alias]) for alias in aliases]
        if not all(names):
            continue
        values = [number(row, name) for name in names]
        if all(valid_price(value) for value in values):
            probs = r22.devig(tuple(float(value) for value in values))
            return {
                "fields": "/".join(str(name) for name in names),
                "prices": {"home": float(values[0]), "draw": float(values[1]), "away": float(values[2])},
                "de_vig": {"home": probs[0], "draw": probs[1], "away": probs[2]},
                "home_share_non_draw": probs[0] / (probs[0] + probs[2]),
            }
    return None


def two_way(row: dict[str, Any], headers: list[str], candidates: list[tuple[str, str]]) -> dict[str, Any] | None:
    for aliases in candidates:
        names = [field(headers, [alias]) for alias in aliases]
        if not all(names):
            continue
        values = [number(row, name) for name in names]
        if all(valid_price(value) for value in values):
            probs = r22.devig(tuple(float(value) for value in values))
            return {
                "fields": "/".join(str(name) for name in names),
                "prices": {"first": float(values[0]), "second": float(values[1])},
                "de_vig": {"first": probs[0], "second": probs[1]},
            }
    return None


def market_packet(source_row: dict[str, Any], headers: list[str]) -> dict[str, Any]:
    open_1x2 = triplet(source_row, headers, [
        ("PSH", "PSD", "PSA"), ("AvgH", "AvgD", "AvgA"),
        ("MaxH", "MaxD", "MaxA"), ("B365H", "B365D", "B365A"),
        ("home_odds", "draw_odds", "away_odds"),
    ])
    close_1x2 = triplet(source_row, headers, [
        ("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"),
        ("MaxCH", "MaxCD", "MaxCA"), ("B365CH", "B365CD", "B365CA"),
        ("PSH", "PSD", "PSA"), ("AvgH", "AvgD", "AvgA"),
        ("home_odds", "draw_odds", "away_odds"),
    ])
    open_ou = two_way(source_row, headers, [
        ("P>2.5", "P<2.5"), ("Avg>2.5", "Avg<2.5"),
        ("Max>2.5", "Max<2.5"), ("B365>2.5", "B365<2.5"),
        ("over_odds", "under_odds"),
    ])
    close_ou = two_way(source_row, headers, [
        ("PC>2.5", "PC<2.5"), ("AvgC>2.5", "AvgC<2.5"),
        ("MaxC>2.5", "MaxC<2.5"), ("B365C>2.5", "B365C<2.5"),
        ("P>2.5", "P<2.5"), ("Avg>2.5", "Avg<2.5"),
        ("over_odds", "under_odds"),
    ])
    total_line_field = field(headers, ["total_line", "over_under_line", "OUCh", "closing_total_line"])
    total_line = number(source_row, total_line_field)
    if total_line is None and (open_ou or close_ou):
        total_line = 2.5
    open_ah = two_way(source_row, headers, [
        ("PAHH", "PAHA"), ("AvgAHH", "AvgAHA"),
        ("MaxAHH", "MaxAHA"), ("B365AHH", "B365AHA"),
    ])
    close_ah = two_way(source_row, headers, [
        ("PCAHH", "PCAHA"), ("AvgCAHH", "AvgCAHA"),
        ("MaxCAHH", "MaxCAHA"), ("B365CAHH", "B365CAHA"),
        ("PAHH", "PAHA"), ("AvgAHH", "AvgAHA"),
    ])
    open_ah_line_field = field(headers, ["AHh", "asian_handicap_line", "handicap_line", "spread_line"])
    close_ah_line_field = field(headers, ["AHCh", "AHh", "asian_handicap_line", "handicap_line", "spread_line"])
    open_ah_line = number(source_row, open_ah_line_field)
    close_ah_line = number(source_row, close_ah_line_field)
    movement: dict[str, float | None] = {
        "draw_probability_change": None,
        "home_share_non_draw_change": None,
        "under_probability_change": None,
        "asian_abs_line_change": None,
    }
    if open_1x2 and close_1x2:
        movement["draw_probability_change"] = close_1x2["de_vig"]["draw"] - open_1x2["de_vig"]["draw"]
        movement["home_share_non_draw_change"] = close_1x2["home_share_non_draw"] - open_1x2["home_share_non_draw"]
    if open_ou and close_ou:
        movement["under_probability_change"] = close_ou["de_vig"]["second"] - open_ou["de_vig"]["second"]
    if open_ah_line is not None and close_ah_line is not None:
        movement["asian_abs_line_change"] = abs(close_ah_line) - abs(open_ah_line)
    return {
        "one_x_two": {"opening": open_1x2, "closing": close_1x2},
        "asian_handicap": {
            "opening_line": open_ah_line,
            "closing_line": close_ah_line,
            "opening_prices": open_ah,
            "closing_prices": close_ah,
        },
        "over_under": {
            "line": total_line,
            "opening_prices": open_ou,
            "closing_prices": close_ou,
        },
        "derived_movement": movement,
    }


def selected_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, errors = r22.extract_rows()
    if errors:
        raise BlindPacketError(f"R22 source errors: {errors}")
    draws = r22.select_draws(rows, 10, 51122)
    pairs = r22.make_pairs(draws, rows)
    by_identity = {row["identity"]: row for row in rows}
    public_pairs: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs, start=1):
        draw = by_identity[pair["draw_identity"]]
        win = by_identity[pair["close_win_identity"]]
        pair_key = sha256(f"51122|{draw['identity']}|{win['identity']}")
        draw_is_a = int(pair_key[-1], 16) % 2 == 0
        ordered = [("A", draw, "draw"), ("B", win, "one_goal_win")] if draw_is_a else [("A", win, "one_goal_win"), ("B", draw, "draw")]
        candidates = []
        label_row: dict[str, Any] = {"pair_id": f"R29-{index:02d}"}
        for side, row, label in ordered:
            source_row, headers = read_source_row(row["source_file"], int(row["row_number"]))
            candidates.append({
                "side": side,
                "competition_id": row["competition_id"],
                "season": row["season"],
                "match_date": row["date_key"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "source_file": row["source_file"],
                "source_row_number": row["row_number"],
                "market": market_packet(source_row, headers),
                "context_template": {
                    "task_accepts_draw": 0,
                    "attack_suppression": 0,
                    "source_records": [],
                    "notes": "unfilled until timestamped web reconstruction"
                },
            })
            label_row[f"side_{side}_label"] = label
            label_row[f"side_{side}_score"] = f"{row['home_goals_90']}-{row['away_goals_90']}"
        public_pairs.append({
            "pair_id": f"R29-{index:02d}",
            "pair_identity_sha256": pair_key,
            "candidates": candidates,
        })
        hidden.append(label_row)
    return public_pairs, hidden


def write_csv(path: Path, pairs: list[dict[str, Any]]) -> None:
    fields = [
        "pair_id", "side", "competition_id", "season", "match_date", "home_team", "away_team",
        "open_draw_probability", "close_draw_probability", "draw_probability_change",
        "open_total_line", "close_total_line", "under_probability_change",
        "open_asian_line", "close_asian_line", "asian_abs_line_change",
        "source_file", "source_row_number",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for pair in pairs:
            for candidate in pair["candidates"]:
                market = candidate["market"]
                open_1x2 = market["one_x_two"]["opening"]
                close_1x2 = market["one_x_two"]["closing"]
                movement = market["derived_movement"]
                writer.writerow({
                    "pair_id": pair["pair_id"],
                    "side": candidate["side"],
                    "competition_id": candidate["competition_id"],
                    "season": candidate["season"],
                    "match_date": candidate["match_date"],
                    "home_team": candidate["home_team"],
                    "away_team": candidate["away_team"],
                    "open_draw_probability": open_1x2["de_vig"]["draw"] if open_1x2 else None,
                    "close_draw_probability": close_1x2["de_vig"]["draw"] if close_1x2 else None,
                    "draw_probability_change": movement["draw_probability_change"],
                    "open_total_line": market["over_under"]["line"],
                    "close_total_line": market["over_under"]["line"],
                    "under_probability_change": movement["under_probability_change"],
                    "open_asian_line": market["asian_handicap"]["opening_line"],
                    "close_asian_line": market["asian_handicap"]["closing_line"],
                    "asian_abs_line_change": movement["asian_abs_line_change"],
                    "source_file": candidate["source_file"],
                    "source_row_number": candidate["source_row_number"],
                })


def run(config: dict[str, Any], status_path: Path, public_json: Path, public_csv: Path, hidden_path: Path) -> dict[str, Any]:
    pairs, hidden = selected_rows()
    hidden_payload = {
        "schema_version": "v511_historical_blind_context_screen10_r29_hidden_labels.1",
        "labels": hidden,
    }
    seal = sha256(canonical(hidden_payload))
    public_payload = {
        "schema_version": "v511_historical_blind_context_screen10_r29_public.1",
        "classification": config["classification"],
        "formal_weight": 0,
        "label_seal_sha256": seal,
        "sample_contract": config["sample_contract"],
        "blind_rubric": config["blind_rubric"],
        "pairs": pairs,
    }
    public_json.parent.mkdir(parents=True, exist_ok=True)
    public_json.write_text(json.dumps(public_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    hidden_path.write_text(json.dumps(hidden_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_csv(public_csv, pairs)
    public_text = public_json.read_text(encoding="utf-8")
    status = {
        "schema_version": "v511_historical_blind_context_screen10_r29_status.1",
        "status": "PASS_R29_FIXED_SAMPLE_BLIND_PACKET_BUILT",
        "classification": config["classification"],
        "formal_weight": 0,
        "pairs": len(pairs),
        "public_candidates": sum(len(pair["candidates"]) for pair in pairs),
        "label_seal_sha256": seal,
        "public_packet_sha256": sha256(public_text),
        "public_packet_contains_score_fields": any(token in public_text for token in ("side_A_score", "side_B_score", "home_goals_90", "away_goals_90")),
        "public_packet_contains_label_fields": any(token in public_text for token in ("side_A_label", "side_B_label", "one_goal_win", "actual_result")),
        "ruling": {
            "web_context_reconstruction_allowed": True,
            "hidden_labels_must_not_be_opened_before_predictions_frozen": True,
            "resampling_allowed": False,
            "pass_pairs": int(config["blind_rubric"]["pass_pairs"]),
            "formal_promotion_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    if status["public_packet_contains_score_fields"] or status["public_packet_contains_label_fields"]:
        raise BlindPacketError("public blind packet leaked score or label")
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return status


def self_test() -> None:
    sample = {"labels": [{"pair_id": "X", "side_A_label": "draw"}]}
    if sha256(canonical(sample)) != sha256(canonical(sample)):
        raise BlindPacketError("seal self-test failed")
    print(json.dumps({"self_test": "PASS"}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--public-json", type=Path, default=DEFAULT_PUBLIC_JSON)
    parser.add_argument("--public-csv", type=Path, default=DEFAULT_PUBLIC_CSV)
    parser.add_argument("--hidden-labels", type=Path, default=DEFAULT_HIDDEN)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(run(load_json(args.config), args.status, args.public_json, args.public_csv, args.hidden_labels), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
