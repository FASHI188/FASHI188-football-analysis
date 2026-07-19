#!/usr/bin/env python3
"""Prepare, calculate and validate a V4.6.x non-A single-match prediction."""
from __future__ import annotations

import argparse
import csv
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from football_v460_engine import calculation_from_context
from match_pipeline import prepare_match_context, validate_calculation_output
from oof_matrix_calibration import apply_oof_matrix_calibration
from platform_core import (
    ROOT,
    PlatformError,
    atomic_write_json,
    canonical_team_name,
    load_json,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
    sha256_json,
)


def _parse_overlay_date(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise PlatformError("supplemental completed match requires a date")
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    raise PlatformError(f"unsupported supplemental completed match date: {raw!r}")


@contextmanager
def _question_time_result_overlay(match_input: dict[str, Any], context: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Temporarily merge verified completed matches missing from persisted data.

    The overlay is point-in-time calculation input only. It is removed after the
    prediction and never silently committed to the persisted dataset.
    """
    supplied = match_input.get("supplemental_completed_matches") or []
    if not supplied:
        yield {
            "status": "不适用",
            "applied_matches": 0,
            "policy": "persisted dataset used without question-time result overlay",
        }
        return
    if not isinstance(supplied, list):
        raise PlatformError("supplemental_completed_matches must be a list")

    identity = context["match_identity"]
    competition_id = str(identity["competition_id"])
    season = str(identity.get("season") or match_input.get("season") or "").strip()
    freeze_time = parse_iso_datetime(identity["freeze_time_utc"], "freeze_time_utc")
    aliases = None

    existing = read_processed_matches(competition_id)
    existing_keys = {
        (
            str(item.season),
            item.date.date().isoformat(),
            normalize_team_token(item.home_team),
            normalize_team_token(item.away_team),
        )
        for item in existing
    }

    overlay_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    seen_overlay: set[tuple[str, str, str, str]] = set()

    for index, item in enumerate(supplied):
        if not isinstance(item, dict):
            raise PlatformError(f"supplemental_completed_matches[{index}] must be an object")
        item_season = str(item.get("season") or season).strip()
        if item_season != season:
            raise PlatformError(
                f"supplemental match season mismatch: {item_season!r} != target season {season!r}"
            )
        source_name = str(item.get("source_name") or "").strip()
        source_url = str(item.get("source_url") or "").strip()
        if not source_name or not source_url:
            raise PlatformError("supplemental completed match requires source_name and source_url")
        observed_at = parse_iso_datetime(
            item.get("observed_at_utc"),
            f"supplemental_completed_matches[{index}].observed_at_utc",
        )
        if observed_at > freeze_time:
            raise PlatformError("supplemental completed match evidence was observed after the prediction freeze")

        parsed_date = _parse_overlay_date(item.get("date") or item.get("match_date"))
        match_date = parsed_date.date()
        if match_date > freeze_time.date():
            raise PlatformError("supplemental completed match occurs after the prediction freeze date")
        if match_date == freeze_time.date():
            completed_at_raw = item.get("completed_at_utc")
            if not completed_at_raw:
                raise PlatformError(
                    "same-day supplemental result requires completed_at_utc; date-only evidence is insufficient"
                )
            completed_at = parse_iso_datetime(
                completed_at_raw,
                f"supplemental_completed_matches[{index}].completed_at_utc",
            )
            if completed_at >= freeze_time:
                raise PlatformError("same-day supplemental match was not completed before the prediction freeze")
            raise PlatformError(
                "same-day verified overlays are not yet safe in the date-only engine; refusing silent inclusion"
            )

        home_raw = str(item.get("home_team") or item.get("HomeTeam") or "").strip()
        away_raw = str(item.get("away_team") or item.get("AwayTeam") or "").strip()
        if not home_raw or not away_raw:
            raise PlatformError("supplemental completed match requires home_team and away_team")
        home = canonical_team_name(competition_id, home_raw, aliases)
        away = canonical_team_name(competition_id, away_raw, aliases)
        try:
            home_goals = int(item.get("home_goals", item.get("FTHG")))
            away_goals = int(item.get("away_goals", item.get("FTAG")))
        except (TypeError, ValueError) as exc:
            raise PlatformError("supplemental completed match requires integer home_goals and away_goals") from exc
        if home_goals < 0 or away_goals < 0:
            raise PlatformError("supplemental completed match goals cannot be negative")

        key = (
            item_season,
            match_date.isoformat(),
            normalize_team_token(home),
            normalize_team_token(away),
        )
        if key in existing_keys or key in seen_overlay:
            continue
        seen_overlay.add(key)
        overlay_rows.append({
            "competition_id": competition_id,
            "season": item_season,
            "stage": str(item.get("stage") or "regular_league"),
            "Date": match_date.strftime("%d/%m/%Y"),
            "HomeTeam": home,
            "AwayTeam": away,
            "FTHG": home_goals,
            "FTAG": away_goals,
        })
        audit_rows.append({
            "competition_id": competition_id,
            "season": item_season,
            "date": match_date.isoformat(),
            "home_team": home,
            "away_team": away,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "source_name": source_name,
            "source_url": source_url,
            "observed_at_utc": observed_at.isoformat(),
            "freeze_time_utc": freeze_time.isoformat(),
            "absent_from_persisted_dataset": True,
        })

    if not overlay_rows:
        yield {
            "status": "通过",
            "applied_matches": 0,
            "matches": [],
            "policy": "all supplied completed matches were already present after canonical deduplication",
        }
        return

    directory = ROOT / "processed" / competition_id
    directory.mkdir(parents=True, exist_ok=True)
    overlay_path = directory / f"__question_time_overlay_{context['context_hash'][:12]}.csv"
    fieldnames = ["competition_id", "season", "stage", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    try:
        with overlay_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(overlay_rows)
        yield {
            "status": "通过",
            "applied_matches": len(overlay_rows),
            "matches": audit_rows,
            "temporary_overlay_path": str(overlay_path.relative_to(ROOT)),
            "policy": "verified question-time completed-result overlay; calculation input only; removed after run",
        }
    finally:
        overlay_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--context-output", required=True)
    parser.add_argument("--calculation-output", required=True)
    parser.add_argument("--validation-output", required=True)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        match_input = load_json(Path(args.input))
        if not str(match_input.get("season") or "").strip():
            raise PlatformError("formal prediction input requires explicit season to prevent previous-season fallback")
        context = prepare_match_context(match_input)
        context["match_identity"]["season"] = str(match_input["season"]).strip()
        if match_input.get("supplemental_completed_matches"):
            context["supplemental_completed_matches"] = match_input["supplemental_completed_matches"]
        hash_payload = dict(context)
        hash_payload.pop("prepared_at_utc", None)
        hash_payload.pop("context_hash", None)
        context["context_hash"] = sha256_json(hash_payload)

        with _question_time_result_overlay(match_input, context) as overlay_audit:
            calculation = calculation_from_context(context)
        calculation["live_result_overlay_audit"] = overlay_audit

        evidence = context.get("data_freshness_evidence")
        if not isinstance(evidence, dict):
            raise PlatformError("formal prediction requires question-time official data freshness evidence")
        source_name = str(evidence.get("source_name") or "").strip()
        source_url = str(evidence.get("source_url") or "").strip()
        observed_at = parse_iso_datetime(evidence.get("observed_at_utc"), "data_freshness_evidence.observed_at_utc")
        freeze_time = parse_iso_datetime(context["match_identity"]["freeze_time_utc"], "freeze_time_utc")
        if not source_name or not source_url:
            raise PlatformError("data freshness evidence requires an identifiable official source name and URL")
        if observed_at > freeze_time:
            raise PlatformError("data freshness evidence was observed after the prediction freeze")
        try:
            expected_count = int(evidence["expected_history_matches"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlatformError("data freshness evidence requires integer expected_history_matches") from exc
        if expected_count < 0:
            raise PlatformError("expected_history_matches cannot be negative")
        expected_latest = str(evidence.get("latest_history_match_date") or "").strip() or None
        model_audit = calculation.get("model_audit", {})
        actual_count = int(model_audit.get("history_matches", -1))
        actual_latest = model_audit.get("latest_history_match_date")
        if actual_count != expected_count:
            raise PlatformError(
                f"point-in-time current-season history is incomplete after live overlay: engine_history={actual_count} official_expected={expected_count}"
            )
        if expected_latest is not None and actual_latest != expected_latest:
            raise PlatformError(
                f"point-in-time latest-date mismatch after live overlay: engine_latest={actual_latest} official_latest={expected_latest}"
            )
        calculation.setdefault("module_states", {})["data_freshness"] = "通过"
        calculation["data_freshness_audit"] = {
            "status": "通过",
            "source_name": source_name,
            "source_url": source_url,
            "observed_at_utc": observed_at.isoformat(),
            "expected_history_matches": expected_count,
            "engine_history_matches": actual_count,
            "expected_latest_history_match_date": expected_latest,
            "engine_latest_history_match_date": actual_latest,
            "live_overlay_matches": int(overlay_audit.get("applied_matches", 0)),
            "policy": "official question-time reconciliation with verified missing-result overlay before formal calculation",
        }

        calculation = apply_oof_matrix_calibration(context, calculation)
        validation = validate_calculation_output(context, calculation)
        if validation["status"] != "通过":
            raise PlatformError(f"formal calculation failed validation: {validation['errors']}")
        atomic_write_json(Path(args.context_output), context)
        atomic_write_json(Path(args.calculation_output), calculation)
        atomic_write_json(Path(args.validation_output), validation)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps({
            "context_hash": context["context_hash"],
            "validation_status": validation["status"],
            "live_result_overlay": calculation.get("live_result_overlay_audit", {}).get("status", "不适用"),
            "live_result_overlay_matches": calculation.get("live_result_overlay_audit", {}).get("applied_matches", 0),
            "oof_matrix_calibration": calculation.get("module_states", {}).get("oof_matrix_calibration", "不可用"),
            "confidence": calculation["conclusions"]["confidence_grade"],
            "top_score": calculation["conclusions"]["top_score"],
            "total_goals_primary": calculation["conclusions"]["total_goals_primary"],
            "price_status": calculation["conclusions"]["price_status"]
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
