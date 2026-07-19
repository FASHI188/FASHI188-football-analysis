#!/usr/bin/env python3
"""Build competition-level independent replay receipts for the formal football core.

For each registered competition, this module selects deterministic historical
point-in-time fixtures from seasons that have frozen point-in-time parameters.
The parent process computes reference probabilities; a separate Python process
reconstructs the same histories and recomputes the full joint distributions.
A receipt passes only when every probability object reproduces within 1e-10 and
all engine/config/model/report hashes remain bound to the current artifacts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from football_v460_engine import ENGINE_PATH, load_config, predict_from_history  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    MatchRow,
    PlatformError,
    atomic_write_json,
    load_json,
    load_registry,
    read_processed_matches,
    sha256_file,
    sha256_json,
    utc_now,
)

REPORT_ROOT = ROOT / "validation" / "reports" / "replay_v462"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
CORE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
MANIFEST_PATH = ROOT / "manifests" / "replay_v462_status.json"
CONFIG_PATH = ROOT / "config" / "formal_core_v460.json"
SCRIPT_PATH = Path(__file__).resolve()
TOLERANCE = 1e-10
MIN_FIXTURES = 12
MAX_FIXTURES = 24


def _team_counts(history: list[MatchRow]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in history:
        counts[match.home_team] += 1
        counts[match.away_team] += 1
    return counts


def _probability_payload(prediction: dict[str, Any]) -> dict[str, Any]:
    probabilities = prediction["probabilities"]
    return {
        "one_x_two": probabilities["one_x_two"],
        "total_goals": probabilities["total_goals"],
        "btts_yes": probabilities.get("btts_yes"),
        "score_matrix": probabilities["score_matrix"],
    }


def _score_map(payload: dict[str, Any]) -> dict[tuple[int, int], float]:
    return {
        (int(cell["home_goals"]), int(cell["away_goals"])): float(cell["probability"])
        for cell in payload["score_matrix"]
    }


def _max_difference(left: dict[str, Any], right: dict[str, Any]) -> float:
    diffs: list[float] = []
    for key in ("home", "draw", "away"):
        diffs.append(abs(float(left["one_x_two"][key]) - float(right["one_x_two"][key])))
    for key in ("0", "1", "2", "3", "4", "5", "6", "7+"):
        diffs.append(abs(float(left["total_goals"][key]) - float(right["total_goals"][key])))
    if left.get("btts_yes") is not None and right.get("btts_yes") is not None:
        diffs.append(abs(float(left["btts_yes"]) - float(right["btts_yes"])))
    lm, rm = _score_map(left), _score_map(right)
    for key in set(lm) | set(rm):
        diffs.append(abs(lm.get(key, 0.0) - rm.get(key, 0.0)))
    return max(diffs or [0.0])


def _history_before(matches: list[MatchRow], season: str, target: MatchRow) -> list[MatchRow]:
    return sorted(
        [m for m in matches if m.season == season and m.date.date() < target.date.date()],
        key=lambda item: (item.date, item.home_team, item.away_team),
    )


def _eligible_specs(competition_id: str, model: dict[str, Any]) -> list[dict[str, Any]]:
    config = load_config()["validation"]
    warmup_comp = int(config["warmup_competition_matches"])
    warmup_team = int(config["warmup_team_matches"])
    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[MatchRow]] = defaultdict(list)
    for match in matches:
        by_season[match.season].append(match)

    parameter_map = model.get("point_in_time_parameters") or {}
    eligible: list[dict[str, Any]] = []
    for season, params in parameter_map.items():
        season_matches = sorted(by_season.get(season, []), key=lambda x: (x.date, x.home_team, x.away_team))
        by_date: dict[Any, list[MatchRow]] = defaultdict(list)
        for match in season_matches:
            by_date[match.date.date()].append(match)
        history: list[MatchRow] = []
        for date in sorted(by_date):
            counts = _team_counts(history)
            for match in sorted(by_date[date], key=lambda x: (x.home_team, x.away_team)):
                if len(history) < warmup_comp or counts[match.home_team] < warmup_team or counts[match.away_team] < warmup_team:
                    continue
                try:
                    prediction = predict_from_history(
                        history,
                        competition_id,
                        season,
                        match.home_team,
                        match.away_team,
                        match.date,
                        params,
                        use_team_effects=True,
                    )
                except PlatformError:
                    continue
                eligible.append({
                    "competition_id": competition_id,
                    "season": season,
                    "date": match.date.date().isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "params": params,
                    "reference": _probability_payload(prediction),
                })
            history.extend(by_date[date])
            history.sort(key=lambda x: (x.date, x.home_team, x.away_team))

    if len(eligible) <= MAX_FIXTURES:
        return eligible
    indexes = sorted({round(i * (len(eligible) - 1) / (MAX_FIXTURES - 1)) for i in range(MAX_FIXTURES)})
    return [eligible[index] for index in indexes]


def _worker_predict(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache: dict[str, list[MatchRow]] = {}
    output: list[dict[str, Any]] = []
    for spec in specs:
        competition_id = spec["competition_id"]
        if competition_id not in cache:
            cache[competition_id] = read_processed_matches(competition_id)
        target = next(
            (
                match for match in cache[competition_id]
                if match.season == spec["season"]
                and match.date.date().isoformat() == spec["date"]
                and match.home_team == spec["home_team"]
                and match.away_team == spec["away_team"]
            ),
            None,
        )
        if target is None:
            raise PlatformError(f"replay target not found: {spec}")
        history = _history_before(cache[competition_id], spec["season"], target)
        prediction = predict_from_history(
            history,
            competition_id,
            spec["season"],
            spec["home_team"],
            spec["away_team"],
            target.date,
            spec["params"],
            use_team_effects=True,
        )
        output.append(_probability_payload(prediction))
    return output


def _subprocess_replay(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    worker_specs = [{key: value for key, value in spec.items() if key != "reference"} for spec in specs]
    with tempfile.TemporaryDirectory(prefix="football-replay-") as temp_dir:
        input_path = Path(temp_dir) / "worker_input.json"
        output_path = Path(temp_dir) / "worker_output.json"
        input_path.write_text(json.dumps(worker_specs, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--worker-input", str(input_path), "--worker-output", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise PlatformError(f"independent replay subprocess failed: {completed.stderr or completed.stdout}")
        return json.loads(output_path.read_text(encoding="utf-8"))


def build_receipt(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    model_path = MODEL_ROOT / competition_id / "model.json"
    core_path = CORE_REPORT_ROOT / f"{competition_id}.json"
    if not model_path.exists() or not core_path.exists():
        raise PlatformError(f"model/core report missing for replay: {competition_id}")
    model = load_json(model_path)
    specs = _eligible_specs(competition_id, model)
    if len(specs) < MIN_FIXTURES:
        raise PlatformError(f"insufficient deterministic replay fixtures: {len(specs)} < {MIN_FIXTURES}")
    replayed = _subprocess_replay(specs)
    if len(replayed) != len(specs):
        raise PlatformError("independent replay subprocess returned a mismatched fixture count")

    fixture_results = []
    max_diff = 0.0
    for spec, actual in zip(specs, replayed):
        diff = _max_difference(spec["reference"], actual)
        max_diff = max(max_diff, diff)
        fixture_results.append({
            "match_key": f"{spec['season']}|{spec['date']}|{spec['home_team']}|{spec['away_team']}",
            "reference_probability_sha256": sha256_json(spec["reference"]),
            "replay_probability_sha256": sha256_json(actual),
            "max_probability_difference": diff,
            "passed": diff <= TOLERANCE,
        })

    passed = len(specs) >= MIN_FIXTURES and max_diff <= TOLERANCE and all(item["passed"] for item in fixture_results)
    receipt = {
        "schema_version": "V4.6.2",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "通过" if passed else "失败",
        "independent_process": True,
        "replay_tolerance": TOLERANCE,
        "minimum_required_fixtures": MIN_FIXTURES,
        "fixture_count": len(specs),
        "max_probability_difference": max_diff,
        "engine_sha256": sha256_file(ENGINE_PATH),
        "config_sha256": sha256_file(CONFIG_PATH),
        "model_artifact_sha256": sha256_file(model_path),
        "core_report_sha256": sha256_file(core_path),
        "replay_code_sha256": sha256_file(SCRIPT_PATH),
        "fixtures": fixture_results,
        "governance_note": "Independent-process deterministic replay receipt. It proves reproducibility only; it does not prove predictive accuracy, market value, lineup quality, or A-grade eligibility by itself.",
    }
    if write:
        atomic_write_json(REPORT_ROOT / f"{competition_id}.json", receipt)
    return receipt


def run_all(competition: str | None = None, *, write: bool = True) -> dict[str, Any]:
    ids = [item["competition_id"] for item in load_registry()["competitions"]]
    if competition:
        if competition not in ids:
            raise PlatformError(f"unknown competition: {competition}")
        ids = [competition]
    reports: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    for competition_id in ids:
        try:
            receipt = build_receipt(competition_id, write=write)
            reports[competition_id] = {
                "status": receipt["status"],
                "fixture_count": receipt["fixture_count"],
                "max_probability_difference": receipt["max_probability_difference"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.2",
        "engine_sha256": sha256_file(ENGINE_PATH),
        "replay_code_sha256": sha256_file(SCRIPT_PATH),
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "replay_receipt_pass_count": sum(item["status"] == "通过" for item in reports.values()),
        "reports": reports,
        "failures": failures,
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"independent replay receipt build failed for {len(failures)} domains: {failures}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--worker-input")
    parser.add_argument("--worker-output")
    args = parser.parse_args()

    if args.worker_input:
        try:
            specs = json.loads(Path(args.worker_input).read_text(encoding="utf-8"))
            predictions = _worker_predict(specs)
            if not args.worker_output:
                raise PlatformError("--worker-output is required with --worker-input")
            Path(args.worker_output).write_text(json.dumps(predictions, ensure_ascii=False), encoding="utf-8")
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    try:
        manifest = run_all(args.competition, write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
