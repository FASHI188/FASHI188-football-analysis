#!/usr/bin/env python3
"""Independent-process replay receipt for the complete final football chain.

Unlike the earlier base-core replay, this verifier replays:
  base direct-total/conditional-allocation joint matrix
  -> eligible point-in-time OOF full-matrix calibration
  -> rebuilt 1X2/total-goals/BTTS marginals
  -> Asian-handicap and over/under settlement from that same final matrix.

It proves deterministic reproducibility only.  It does not prove predictive edge.
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

from football_v460_engine import ENGINE_PATH, calculation_from_context, load_config  # noqa: E402
from oof_matrix_calibration import (  # noqa: E402
    CALIBRATION_MODULE_PATH,
    apply_oof_matrix_calibration,
    calibrator_path,
)
from platform_core import (  # noqa: E402
    ROOT,
    PlatformError,
    atomic_write_json,
    load_json,
    load_registry,
    read_processed_matches,
    sha256_file,
    sha256_json,
    utc_now,
)

REPORT_ROOT = ROOT / "validation" / "reports" / "final_chain_replay_v463"
MANIFEST_PATH = ROOT / "manifests" / "final_chain_replay_v463_status.json"
MODEL_ROOT = ROOT / "models" / "formal_core_v460"
CORE_REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OOF_REPORT_ROOT = ROOT / "validation" / "reports" / "oof_matrix_calibration_v461"
SCRIPT_PATH = Path(__file__).resolve()
TOLERANCE = 1e-10
MIN_FIXTURES = 12
MAX_FIXTURES = 24


def _team_counts(history: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for match in history:
        counts[match.home_team] += 1
        counts[match.away_team] += 1
    return counts


def _context(competition_id: str, match: Any) -> dict[str, Any]:
    identity = {
        "competition_id": competition_id,
        "season": match.season,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "freeze_time_utc": match.date.isoformat(),
    }
    base = {
        "match_identity": identity,
        # Diagnostic lines are fixed replay probes, not claims about historical
        # tradable prices.  They verify that final-matrix market settlement is
        # deterministic and rebuilt after OOF calibration.
        "original_market_snapshot": {
            "asian_handicap": {"line": 0.0},
            "total_goals": {"line": 2.5},
        },
        "lineup_assessment": {"status": "部分通过"},
    }
    base["context_hash"] = sha256_json(base)
    return base


def _final_chain(context: dict[str, Any]) -> dict[str, Any]:
    base = calculation_from_context(context)
    final = apply_oof_matrix_calibration(context, base)
    if final.get("module_states", {}).get("oof_matrix_calibration") != "通过":
        raise PlatformError(
            f"final-chain replay requires OOF calibration pass: {final.get('calibration_audit')}"
        )
    return final


def _score_map(calculation: dict[str, Any]) -> dict[tuple[int, int], float]:
    return {
        (int(cell["home_goals"]), int(cell["away_goals"])): float(cell["probability"])
        for cell in calculation["probabilities"]["score_matrix"]
    }


def _max_difference(left: dict[str, Any], right: dict[str, Any]) -> float:
    diffs: list[float] = []
    lp, rp = left["probabilities"], right["probabilities"]
    for key in ("home", "draw", "away"):
        diffs.append(abs(float(lp["one_x_two"][key]) - float(rp["one_x_two"][key])))
    for key in ("0", "1", "2", "3", "4", "5", "6", "7+"):
        diffs.append(abs(float(lp["total_goals"][key]) - float(rp["total_goals"][key])))
    diffs.append(abs(float(lp.get("btts_yes", 0.0)) - float(rp.get("btts_yes", 0.0))))
    lm, rm = _score_map(left), _score_map(right)
    for key in set(lm) | set(rm):
        diffs.append(abs(lm.get(key, 0.0) - rm.get(key, 0.0)))
    for market_key in ("home_handicap", "over_total"):
        lmarket = (left.get("derived_markets") or {}).get(market_key) or {}
        rmarket = (right.get("derived_markets") or {}).get(market_key) or {}
        for key in ("win", "push", "loss"):
            diffs.append(abs(float(lmarket.get(key, 0.0)) - float(rmarket.get(key, 0.0))))
    return max(diffs or [0.0])


def _eligible_contexts(competition_id: str) -> list[dict[str, Any]]:
    model_path = MODEL_ROOT / competition_id / "model.json"
    cal_path = calibrator_path(competition_id)
    if not model_path.exists() or not cal_path.exists():
        return []
    model = load_json(model_path)
    calibrator = load_json(cal_path)
    parameter_map = model.get("point_in_time_parameters") or {}
    season_calibrators = calibrator.get("season_calibrators") or {}
    valid_seasons = set(parameter_map).intersection(season_calibrators)
    config = load_config()["validation"]
    warmup_comp = int(config["warmup_competition_matches"])
    warmup_team = int(config["warmup_team_matches"])

    matches = read_processed_matches(competition_id)
    by_season: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        if match.season in valid_seasons:
            by_season[match.season].append(match)

    contexts: list[dict[str, Any]] = []
    for season in sorted(by_season):
        history: list[Any] = []
        by_date: dict[Any, list[Any]] = defaultdict(list)
        for match in sorted(by_season[season], key=lambda item: (item.date, item.home_team, item.away_team)):
            by_date[match.date.date()].append(match)
        for date in sorted(by_date):
            counts = _team_counts(history)
            for match in sorted(by_date[date], key=lambda item: (item.home_team, item.away_team)):
                if len(history) < warmup_comp or counts[match.home_team] < warmup_team or counts[match.away_team] < warmup_team:
                    continue
                context = _context(competition_id, match)
                try:
                    reference = _final_chain(context)
                except PlatformError:
                    continue
                contexts.append({"context": context, "reference": reference})
            history.extend(by_date[date])
            history.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    if len(contexts) <= MAX_FIXTURES:
        return contexts
    indexes = sorted({round(i * (len(contexts) - 1) / (MAX_FIXTURES - 1)) for i in range(MAX_FIXTURES)})
    return [contexts[index] for index in indexes]


def _worker(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_final_chain(context) for context in contexts]


def _subprocess_replay(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="football-final-replay-") as temp_dir:
        input_path = Path(temp_dir) / "input.json"
        output_path = Path(temp_dir) / "output.json"
        input_path.write_text(json.dumps(contexts, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--worker-input", str(input_path), "--worker-output", str(output_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise PlatformError(f"independent final-chain replay subprocess failed: {completed.stderr or completed.stdout}")
        return json.loads(output_path.read_text(encoding="utf-8"))


def build_receipt(competition_id: str, *, write: bool = True) -> dict[str, Any]:
    model_path = MODEL_ROOT / competition_id / "model.json"
    core_path = CORE_REPORT_ROOT / f"{competition_id}.json"
    oof_report_path = OOF_REPORT_ROOT / f"{competition_id}.json"
    oof_artifact_path = calibrator_path(competition_id)
    required = [model_path, core_path, oof_report_path, oof_artifact_path]
    if not all(path.exists() for path in required):
        raise PlatformError(f"missing final-chain replay artifacts for {competition_id}")

    specs = _eligible_contexts(competition_id)
    if len(specs) < MIN_FIXTURES:
        raise PlatformError(f"insufficient final-chain replay fixtures: {len(specs)} < {MIN_FIXTURES}")
    contexts = [item["context"] for item in specs]
    replayed = _subprocess_replay(contexts)
    if len(replayed) != len(specs):
        raise PlatformError("final-chain subprocess returned mismatched fixture count")

    fixtures = []
    max_diff = 0.0
    for spec, actual in zip(specs, replayed):
        reference = spec["reference"]
        diff = _max_difference(reference, actual)
        max_diff = max(max_diff, diff)
        identity = spec["context"]["match_identity"]
        fixtures.append({
            "match_key": f"{identity['season']}|{identity['freeze_time_utc']}|{identity['home_team']}|{identity['away_team']}",
            "reference_final_probability_sha256": sha256_json(reference.get("probabilities")),
            "replay_final_probability_sha256": sha256_json(actual.get("probabilities")),
            "reference_derived_markets_sha256": sha256_json(reference.get("derived_markets")),
            "replay_derived_markets_sha256": sha256_json(actual.get("derived_markets")),
            "oof_status_reference": reference.get("module_states", {}).get("oof_matrix_calibration"),
            "oof_status_replay": actual.get("module_states", {}).get("oof_matrix_calibration"),
            "max_probability_or_settlement_difference": diff,
            "passed": diff <= TOLERANCE,
        })
    passed = len(fixtures) >= MIN_FIXTURES and max_diff <= TOLERANCE and all(item["passed"] for item in fixtures)
    receipt = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": "通过" if passed else "失败",
        "independent_process": True,
        "chain": [
            "base_unified_score_matrix",
            "point_in_time_oof_full_matrix_calibration",
            "rebuild_1x2_total_goals_btts",
            "rebuild_asian_handicap_settlement",
            "rebuild_over_under_settlement",
        ],
        "fixture_count": len(fixtures),
        "minimum_required_fixtures": MIN_FIXTURES,
        "tolerance": TOLERANCE,
        "max_probability_or_settlement_difference": max_diff,
        "engine_sha256": sha256_file(ENGINE_PATH),
        "calibration_code_sha256": sha256_file(CALIBRATION_MODULE_PATH),
        "model_artifact_sha256": sha256_file(model_path),
        "core_report_sha256": sha256_file(core_path),
        "oof_report_sha256": sha256_file(oof_report_path),
        "oof_artifact_sha256": sha256_file(oof_artifact_path),
        "replay_code_sha256": sha256_file(SCRIPT_PATH),
        "fixtures": fixtures,
        "governance_note": "This receipt proves deterministic replay of the final calibrated matrix and derived AH/OU settlements. It does not prove predictive accuracy or betting value.",
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
                "max_difference": receipt["max_probability_or_settlement_difference"],
            }
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": str(exc)})
    manifest = {
        "schema_version": "V4.6.3-evidence",
        "generated_at_utc": utc_now(),
        "competition_count_requested": len(ids),
        "competition_count_built": len(reports),
        "competition_count_failed": len(failures),
        "passed_count": sum(item["status"] == "通过" for item in reports.values()),
        "reports": reports,
        "failures": failures,
    }
    if write and not competition:
        atomic_write_json(MANIFEST_PATH, manifest)
    if failures:
        raise PlatformError(f"final-chain replay failed for {len(failures)} domains: {failures}")
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
        contexts = json.loads(Path(args.worker_input).read_text(encoding="utf-8"))
        output = _worker(contexts)
        Path(args.worker_output).write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
        return 0
    try:
        result = run_all(args.competition, write=not args.check_only)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
