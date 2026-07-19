#!/usr/bin/env python3
"""Question-time formal single-match runner.

This wrapper preserves the validated V4.6.x engine hash while adding a live-only
same-day result overlay for matches already completed before the user's current
freeze time. It is intentionally disabled for historical replay so date-only
rows cannot create look-ahead leakage.
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import football_v460_engine as engine_module
import run_formal_prediction_v460 as base_runner
from platform_core import (
    ROOT,
    PlatformError,
    canonical_team_name,
    normalize_team_token,
    parse_iso_datetime,
    read_processed_matches,
)

LIVE_FREEZE_TOLERANCE_SECONDS = 2 * 60 * 60


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


def _is_live_question_time(freeze_time: datetime) -> bool:
    now = datetime.now(timezone.utc)
    delta = abs((now - freeze_time).total_seconds())
    return delta <= LIVE_FREEZE_TOLERANCE_SECONDS


@contextmanager
def _live_question_time_result_overlay(match_input: dict[str, Any], context: dict[str, Any]) -> Iterator[dict[str, Any]]:
    supplied = match_input.get("supplemental_completed_matches") or []
    if not supplied:
        yield {
            "status": "不适用",
            "applied_matches": 0,
            "same_day_live_override": False,
            "policy": "persisted dataset used without question-time result overlay",
        }
        return
    if not isinstance(supplied, list):
        raise PlatformError("supplemental_completed_matches must be a list")

    identity = context["match_identity"]
    competition_id = str(identity["competition_id"])
    season = str(identity.get("season") or match_input.get("season") or "").strip()
    freeze_time = parse_iso_datetime(identity["freeze_time_utc"], "freeze_time_utc")

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
    same_day_live_override = False

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

        completed_at_iso = None
        if match_date == freeze_time.date():
            if not _is_live_question_time(freeze_time):
                raise PlatformError(
                    "same-day result overlay is live-only; historical replay requires exact timestamped source rows"
                )
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
            completed_at_iso = completed_at.isoformat()
            same_day_live_override = True

        home_raw = str(item.get("home_team") or item.get("HomeTeam") or "").strip()
        away_raw = str(item.get("away_team") or item.get("AwayTeam") or "").strip()
        if not home_raw or not away_raw:
            raise PlatformError("supplemental completed match requires home_team and away_team")
        home = canonical_team_name(competition_id, home_raw)
        away = canonical_team_name(competition_id, away_raw)
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
            "completed_at_utc": completed_at_iso,
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
            "same_day_live_override": False,
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
            "same_day_live_override": same_day_live_override,
            "temporary_overlay_path": str(overlay_path.relative_to(ROOT)),
            "policy": (
                "verified question-time completed-result overlay; live same-day rows are allowed only when exact "
                "completion time is before a near-current freeze; calculation input only; removed after run"
            ),
        }
    finally:
        overlay_path.unlink(missing_ok=True)


def main() -> int:
    original_overlay = base_runner._question_time_result_overlay
    original_cutoff_gate = engine_module._strictly_before_cutoff_date

    @contextmanager
    def patched_overlay(match_input: dict[str, Any], context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        with _live_question_time_result_overlay(match_input, context) as audit:
            if audit.get("same_day_live_override"):
                # Live-only safety: as of a near-current question-time freeze, any persisted
                # result row on the same calendar date is already completed; scheduled rows
                # have no final score and are excluded by read_processed_matches.
                engine_module._strictly_before_cutoff_date = lambda match_date, cutoff: match_date < cutoff
            try:
                yield audit
            finally:
                engine_module._strictly_before_cutoff_date = original_cutoff_gate

    base_runner._question_time_result_overlay = patched_overlay
    try:
        return base_runner.main()
    finally:
        base_runner._question_time_result_overlay = original_overlay
        engine_module._strictly_before_cutoff_date = original_cutoff_gate


if __name__ == "__main__":
    raise SystemExit(main())
