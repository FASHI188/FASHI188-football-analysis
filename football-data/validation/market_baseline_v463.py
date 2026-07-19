#!/usr/bin/env python3
"""Validate timestamped synchronized historical market evidence and LOMO readiness.

Expected JSONL path:
  football-data/markets/<competition_id>/historical_synchronized.jsonl

Each row must identify the match, actual 90-minute result, a decision freeze and a
market snapshot whose 1X2/AH/OU sources were observed before that freeze.  Missing
or retrospective-only market data produces an explicit unavailable report; it
never blocks the non-A base core and never fabricates A-grade evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import sys
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import calculation_from_context  # noqa: E402
from oof_matrix_calibration import apply_oof_matrix_calibration  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    PlatformError,
    atomic_write_json,
    load_registry,
    parse_iso_datetime,
    sha256_file,
    sha256_json,
    utc_now,
)

DATA_ROOT = ROOT / "markets"
REPORT_ROOT = ROOT / "validation" / "reports" / "market_baseline_v463"
MANIFEST_PATH = ROOT / "manifests" / "market_baseline_v463_status.json"
MIN_PREDICTIONS = 200
MAX_SOURCE_SKEW_SECONDS = 15 * 60
BOOTSTRAP_RESAMPLES = 500
SEED = 463


def _fair_three_way(market: dict[str, Any]) -> dict[str, float]:
    values = [float(market[key]) for key in ("home", "draw", "away")]
    if not all(math.isfinite(value) and value > 1.0 for value in values):
        raise PlatformError("invalid 1X2 decimal prices")
    inv = [1.0 / value for value in values]
    total = sum(inv)
    return {key: value / total for key, value in zip(("home", "draw", "away"), inv)}


def _load_rows(competition_id: str) -> tuple[Path, list[dict[str, Any]]]:
    path = DATA_ROOT / competition_id / "historical_synchronized.jsonl"
    if not path.exists():
        return path, []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PlatformError(f"invalid market JSONL {path}:{line_number}: {exc}") from exc
        if str(row.get("competition_id")) != competition_id:
            raise PlatformError(f"competition mismatch {path}:{line_number}")
        rows.append(row)
    return path, rows


def _validate_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    kickoff = parse_iso_datetime(row.get("kickoff_utc"), "kickoff_utc")
    freeze = parse_iso_datetime(row.get("freeze_time_utc"), "freeze_time_utc")
    if freeze >= kickoff:
        raise PlatformError("historical market freeze must be before kickoff")
    snapshot = row.get("market_snapshot")
    if not isinstance(snapshot, dict):
        raise PlatformError("market_snapshot missing")
    observed = parse_iso_datetime(snapshot.get("observed_at_utc"), "market_snapshot.observed_at_utc")
    if observed > freeze or observed >= kickoff:
        raise PlatformError("market snapshot observed after freeze/kickoff")
    sources = snapshot.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PlatformError("timestamped market sources missing")
    source_times = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("name"):
            raise PlatformError("invalid market source entry")
        timestamp = parse_iso_datetime(source.get("observed_at_utc"), "market source observed_at_utc")
        if timestamp > freeze or timestamp >= kickoff:
            raise PlatformError("market source timestamp after freeze/kickoff")
        source_times.append(timestamp)
    skew = int((max(source_times) - min(source_times)).total_seconds()) if source_times else 0
    if skew > MAX_SOURCE_SKEW_SECONDS:
        raise PlatformError(f"market source skew too large: {skew}s")
    one = snapshot.get("one_x_two")
    ah = snapshot.get("asian_handicap")
    ou = snapshot.get("total_goals")
    if not isinstance(one, dict) or not isinstance(ah, dict) or not isinstance(ou, dict):
        raise PlatformError("complete 1X2/AH/OU snapshot required")
    _fair_three_way(one)
    for market, price_keys in ((ah, ("home", "away")), (ou, ("over", "under"))):
        if not isinstance(market.get("line"), (int, float)):
            raise PlatformError("AH/OU line missing")
        values = [float(market[key]) for key in price_keys]
        if not all(math.isfinite(value) and value > 1.0 for value in values):
            raise PlatformError("invalid AH/OU prices")
    return {"kickoff": kickoff, "freeze": freeze, "snapshot": snapshot, "source_skew_seconds": skew}


def _context(row: dict[str, Any], checked: dict[str, Any]) -> dict[str, Any]:
    identity = {
        "competition_id": row["competition_id"],
        "season": str(row["season"]),
        "home_team": str(row["home_team"]),
        "away_team": str(row["away_team"]),
        "freeze_time_utc": checked["freeze"].isoformat(),
    }
    base = {
        "match_identity": identity,
        "original_market_snapshot": checked["snapshot"],
        "lineup_assessment": {"status": "部分通过"},
    }
    base["context_hash"] = sha256_json(base)
    return base


def _bootstrap_upper(differences: list[float]) -> float | None:
    if not differences:
        return None
    blocks = [differences[index:index + 20] for index in range(0, len(differences), 20)]
    rng = random.Random(SEED)
    values = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        selected = [rng.choice(blocks) for _ in blocks]
        flat = [value for block in selected for value in block]
        values.append(mean(flat))
    values.sort()
    return values[min(len(values) - 1, int(0.975 * len(values)))]


def validate_competition(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    path, rows = _load_rows(competition_id)
    if not rows:
        report = {
            "schema_version": "V4.6.3-evidence",
            "generated_at_utc": utc_now(),
            "competition_id": competition_id,
            "status": "MARKET_DATA_UNAVAILABLE",
            "timestamped_synchronized": False,
            "prediction_count": 0,
            "data_path": str(path.relative_to(ROOT)),
            "model_minus_market_log_loss_ci95_upper": None,
            "reason": "No timestamped synchronized historical 1X2/AH/OU dataset is installed. Retrospective closing prices without original quote timestamps are not accepted.",
        }
        if write:
            atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
        return report

    records = []
    errors = []
    for index, row in enumerate(rows):
        try:
            checked = _validate_snapshot(row)
            context = _context(row, checked)
            calculation = apply_oof_matrix_calibration(context, calculation_from_context(context))
            if calculation.get("module_states", {}).get("oof_matrix_calibration") != "通过":
                raise PlatformError("OOF final matrix unavailable at historical freeze")
            hg, ag = int(row["home_goals"]), int(row["away_goals"])
            outcome = "home" if hg > ag else "draw" if hg == ag else "away"
            model_one = calculation["probabilities"]["one_x_two"]
            market_one = _fair_three_way(checked["snapshot"]["one_x_two"])
            model_log = -math.log(max(1e-15, float(model_one[outcome])))
            market_log = -math.log(max(1e-15, float(market_one[outcome])))
            records.append({
                "match_key": f"{row['season']}|{row['kickoff_utc']}|{row['home_team']}|{row['away_team']}",
                "outcome": outcome,
                "model_log_loss": model_log,
                "market_log_loss": market_log,
                "difference": model_log - market_log,
                "source_skew_seconds": checked["source_skew_seconds"],
            })
        except Exception as exc:
            errors.append({"row": index + 1, "error": str(exc)})

    differences = [record["difference"] for record in records]
    ci_upper = _bootstrap_upper(differences)
    sufficient = len(records) >= MIN_PREDICTIONS
    passed = sufficient and ci_upper is not None and ci_upper <= 0.005 and not errors
    report = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "MARKET_BASELINE_VALIDATED" if passed else "MARKET_BASELINE_NOT_A",
        "timestamped_synchronized": not errors,
        "prediction_count": len(records),
        "minimum_predictions": MIN_PREDICTIONS,
        "mean_model_log_loss": mean(record["model_log_loss"] for record in records) if records else None,
        "mean_market_log_loss": mean(record["market_log_loss"] for record in records) if records else None,
        "mean_model_minus_market_log_loss": mean(differences) if differences else None,
        "model_minus_market_log_loss_ci95_upper": ci_upper,
        "maximum_source_skew_seconds": max((record["source_skew_seconds"] for record in records), default=None),
        "data_path": str(path.relative_to(ROOT)),
        "data_sha256": sha256_file(path),
        "invalid_rows": errors,
        "governance_note": "This is a market non-inferiority gate only. Formal EV and market coordination require separate LOMO/time-ordered validation and do not become valid merely because this report passes.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", report)
    return report


def run_all(*, write: bool = True) -> dict[str, Any]:
    reports = {}
    failures = []
    for item in load_registry()["competitions"]:
        cid = item["competition_id"]
        try:
            report = validate_competition(cid, write=write)
            reports[cid] = {
                "status": report["status"],
                "prediction_count": report["prediction_count"],
                "timestamped_synchronized": report["timestamped_synchronized"],
            }
        except Exception as exc:
            failures.append({"competition_id": cid, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "reports": reports,
        "failures": failures,
    }
    if write:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"market baseline validation failed: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    if args.competition:
        result = validate_competition(args.competition, write=not args.check_only)
    else:
        result = run_all(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
