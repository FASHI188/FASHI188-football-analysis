#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE.parent / "formal_fast_runtime_v1"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import runtime as rt  # exact frozen runtime wrapper on this branch
import test_runtime as tr  # acquisition/equivalence helpers over the same frozen corpus

SCHEMA = "football3-formal-gpt-gateway-v1"
BASELINE_RUNTIME_HEAD = "2d9eef417e6b570890a6729d554b8adfae020113"


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canon(obj))


def load_request(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if type(obj) is not dict or obj.get("schema_version") != SCHEMA:
        raise rt.RuntimeGateError("gateway request schema mismatch")
    return obj


def corpus(repo_root: Path, understat_db: Path, confirmation_dir: Path):
    return tr.production_corpus(repo_root, understat_db, confirmation_dir)


def target_payload(row: rt.HistoryFixture) -> dict[str, Any]:
    return tr.target_payload(row)


def cutoff_for(row: rt.HistoryFixture) -> datetime:
    return row.kickoff - timedelta(minutes=60)


def first_fixture(history: list[rt.HistoryFixture]) -> rt.HistoryFixture:
    sample = tr.mechanical_sample(history, 300)
    # Mid-sample is deterministic, comfortably inside frozen coverage, and leaves later fixtures for FAST reuse.
    return sample[150]


def next_fixture(history: list[rt.HistoryFixture], lower: datetime) -> rt.HistoryFixture:
    candidates = [r for r in history if cutoff_for(r) > lower and r.competition_id in set(rt.BIG5.values())]
    candidates.sort(key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    if not candidates:
        raise rt.RuntimeGateError("no frozen fixture available after cached cutoff")
    return candidates[0]


def build_input_for_frozen(row: rt.HistoryFixture, lower: datetime, upper: datetime,
                           history: list[rt.HistoryFixture], labels: dict[str, rt.XGLabel], status: str) -> dict[str, Any]:
    delta = tr.make_delta(history, labels, lower, upper, row.fixture_id) if status == "COMPLETE" else []
    return tr.runtime_input(row, lower, upper, delta, status)


def run_frozen_prediction(row: rt.HistoryFixture, inp: dict[str, Any], bundle: Path, input_path: Path,
                          receipt_path: Path, repo_root: Path, understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    write_json(input_path, inp)
    receipt = rt.predict_match(
        row.competition_id, row.home_team_name, row.away_team_name, row.kickoff, cutoff_for(row),
        bundle, input_path, repo_root, understat_db, confirmation_dir, True,
    )
    write_json(receipt_path, receipt)
    return receipt


def bootstrap_selftest(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    history, labels, source, identity = corpus(repo_root, understat_db, confirmation_dir)
    bundle = state_root / "bundle"
    # This probe intentionally proves FULL from trusted raw sources; a prior cache must not mask it.
    if bundle.exists():
        shutil.rmtree(bundle)
    row = first_fixture(history)
    upper = cutoff_for(row)
    # UNKNOWN coverage forces the production router to reject FAST and rebuild FULL from trusted raw sources.
    inp = tr.runtime_input(row, upper, upper, [], "UNKNOWN")
    receipt = run_frozen_prediction(row, inp, bundle, out / "runtime_input.json", out / "prediction_receipt.json",
                                    repo_root, understat_db, confirmation_dir)
    validated = rt.validate_bundle(bundle)
    if receipt["calculation_path"] != "FULL_REBUILD_PATH":
        raise rt.RuntimeGateError("bootstrap did not execute FULL_REBUILD_PATH")
    return {
        "status": "PASS", "probe": "BOOTSTRAP_FULL", "fixture": target_payload(row), "cutoff": upper.isoformat(),
        "input_sha": receipt["runtime_input_sha"], "state_sha": receipt["state_sha256"],
        "state_bundle_sha": receipt["state_bundle_sha"], "prediction_sha": receipt["prediction_sha"],
        "receipt_sha": receipt["receipt_sha"], "calculation_path": receipt["calculation_path"],
        "remote_state_ready": True, "bundle_cutoff": validated["meta"]["historical_cutoff"],
        "source_scope": validated["source"].get("source_scope"),
    }


def cache_reuse_probe(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                      understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    history, labels, source, identity = corpus(repo_root, understat_db, confirmation_dir)
    bundle = state_root / "bundle"
    loaded = rt.validate_bundle(bundle)
    lower = rt._parse_dt(loaded["meta"]["historical_cutoff"], "cached cutoff")
    row = next_fixture(history, lower)
    upper = cutoff_for(row)
    inp = build_input_for_frozen(row, lower, upper, history, labels, "COMPLETE")
    receipt = run_frozen_prediction(row, inp, bundle, out / "runtime_input.json", out / "prediction_receipt.json",
                                    repo_root, understat_db, confirmation_dir)
    validated = rt.validate_bundle(bundle)
    if receipt["calculation_path"] != "FAST_PATH":
        raise rt.RuntimeGateError("cache reuse probe did not execute FAST_PATH")
    return {
        "status": "PASS", "probe": "REMOTE_CACHE_FAST", "fixture": target_payload(row), "cutoff": upper.isoformat(),
        "input_sha": receipt["runtime_input_sha"], "state_sha": receipt["state_sha256"],
        "state_bundle_sha": receipt["state_bundle_sha"], "prediction_sha": receipt["prediction_sha"],
        "receipt_sha": receipt["receipt_sha"], "calculation_path": receipt["calculation_path"],
        "remote_state_reused": True, "bundle_cutoff": validated["meta"]["historical_cutoff"],
        "delta_records": len(inp["model_delta"]), "delta_records_sha": inp["delta_coverage"]["records_sha256"],
    }


def make_future_fixture(req: dict[str, Any]) -> tuple[dict[str, Any], datetime, datetime]:
    m = req.get("match")
    if type(m) is not dict:
        raise rt.RuntimeGateError("missing-data probe requires match object")
    comp = str(m.get("competition_id") or "")
    season = str(m.get("season") or "")
    home = str(m.get("home_team_name") or "").strip()
    away = str(m.get("away_team_name") or "").strip()
    kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "kickoff")
    cutoff = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
    if comp not in rt.FORMAL_SCOPE:
        raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")
    if cutoff > kickoff - timedelta(minutes=60):
        raise rt.RuntimeGateError("formal cutoff violates T_minus_60_minutes_or_earlier")
    if not season or not home or not away:
        raise rt.RuntimeGateError("canonical future fixture identity incomplete")
    fixture = {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp, "season": season, "kickoff": kickoff.isoformat(),
        "home_team_id": rt._global_team_id(home), "away_team_id": rt._global_team_id(away),
        "home_team_name": home, "away_team_name": away,
    }
    return fixture, kickoff, cutoff


def missing_data_probe(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    bundle = state_root / "bundle"
    before = rt.validate_bundle(bundle)
    before_sha = before["manifest"]["state_bundle_sha256"]
    fixture, kickoff, cutoff = make_future_fixture(req)
    inp = {
        "schema_version": rt.INPUT_SCHEMA, "fixture": fixture, "cutoff": cutoff.isoformat(),
        "delta_coverage": {"schema_version": rt.DELTA_SCHEMA, "status": "UNKNOWN"}, "model_delta": [],
    }
    write_json(out / "runtime_input.json", inp)
    try:
        rt.predict_match(fixture["competition_id"], fixture["home_team_name"], fixture["away_team_name"],
                         kickoff, cutoff, bundle, out / "runtime_input.json", repo_root, understat_db, confirmation_dir, False)
    except rt.RuntimeGateError as exc:
        after = rt.validate_bundle(bundle)
        after_sha = after["manifest"]["state_bundle_sha256"]
        reason = str(exc)
        if "FORMAL_INPUT_DATA_INCOMPLETE" not in reason:
            raise
        result = {
            "status": "PASS_EXPECTED_FAIL_CLOSED", "probe": "MISSING_DATA_FAIL_CLOSED", "fixture": fixture,
            "cutoff": cutoff.isoformat(), "input_sha": sha(inp), "reason": reason,
            "state_unchanged": before_sha == after_sha, "state_bundle_sha": after_sha,
            "prediction_sha": None, "receipt_sha": None,
        }
        write_json(out / "formal_gap.json", result)
        return result
    raise rt.RuntimeGateError("missing-data probe unexpectedly produced a formal prediction")


def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                   understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    """Formal request path. Frozen corpus is auto-acquired here; unverified post-freeze deltas fail closed."""
    history, labels, source, identity = corpus(repo_root, understat_db, confirmation_dir)
    m = req.get("match")
    if type(m) is not dict:
        raise rt.RuntimeGateError("prediction request missing match")
    comp = str(m.get("competition_id") or "")
    season = str(m.get("season") or "")
    home = str(m.get("home_team_name") or "").strip()
    away = str(m.get("away_team_name") or "").strip()
    kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "kickoff")
    cutoff = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
    if cutoff > kickoff - timedelta(minutes=60):
        raise rt.RuntimeGateError("formal cutoff violates T_minus_60_minutes_or_earlier")
    matches = [r for r in history if r.competition_id == comp and r.season == season and r.kickoff == kickoff
               and rt._normalize_team(r.home_team_name) == rt._normalize_team(home)
               and rt._normalize_team(r.away_team_name) == rt._normalize_team(away)]
    bundle = state_root / "bundle"
    if len(matches) == 1:
        row = matches[0]
        try:
            loaded = rt.validate_bundle(bundle)
            lower = rt._parse_dt(loaded["meta"]["historical_cutoff"], "cached cutoff")
        except rt.RuntimeGateError:
            lower = cutoff
        if bundle.exists() and lower <= cutoff:
            inp = build_input_for_frozen(row, lower, cutoff, history, labels, "COMPLETE")
        else:
            inp = tr.runtime_input(row, cutoff, cutoff, [], "UNKNOWN")
        receipt = run_frozen_prediction(row, inp, bundle, out / "runtime_input.json", out / "prediction_receipt.json",
                                        repo_root, understat_db, confirmation_dir)
        return {
            "status": "PASS", "probe": "PREDICTION", "fixture": target_payload(row), "cutoff": cutoff.isoformat(),
            "input_sha": receipt["runtime_input_sha"], "state_sha": receipt["state_sha256"],
            "state_bundle_sha": receipt["state_bundle_sha"], "prediction_sha": receipt["prediction_sha"],
            "receipt_sha": receipt["receipt_sha"], "calculation_path": receipt["calculation_path"],
        }

    # The target is beyond/absent from the trusted frozen acquisition universe. We deliberately do not invent a delta.
    fixture, kickoff, cutoff = make_future_fixture(req)
    inp = {
        "schema_version": rt.INPUT_SCHEMA, "fixture": fixture, "cutoff": cutoff.isoformat(),
        "delta_coverage": {"schema_version": rt.DELTA_SCHEMA, "status": "UNKNOWN"}, "model_delta": [],
    }
    write_json(out / "runtime_input.json", inp)
    try:
        receipt = rt.predict_match(comp, home, away, kickoff, cutoff, bundle, out / "runtime_input.json",
                                   repo_root, understat_db, confirmation_dir, False)
    except rt.RuntimeGateError as exc:
        result = {"status": "FORMAL_INPUT_DATA_INCOMPLETE", "probe": "PREDICTION", "fixture": fixture,
                  "cutoff": cutoff.isoformat(), "input_sha": sha(inp), "reason": str(exc),
                  "prediction_sha": None, "receipt_sha": None}
        write_json(out / "formal_gap.json", result)
        return result
    write_json(out / "prediction_receipt.json", receipt)
    return {"status": "PASS", "probe": "PREDICTION", "fixture": fixture, "cutoff": cutoff.isoformat(),
            "input_sha": receipt["runtime_input_sha"], "state_sha": receipt["state_sha256"],
            "state_bundle_sha": receipt["state_bundle_sha"], "prediction_sha": receipt["prediction_sha"],
            "receipt_sha": receipt["receipt_sha"], "calculation_path": receipt["calculation_path"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--state-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    req = load_request(Path(args.request))
    repo_root = Path(args.repo_root).resolve()
    understat_db = Path(args.understat_db).resolve()
    confirmation_dir = Path(args.confirmation_dir).resolve()
    state_root = Path(args.state_root).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)

    mode = str(req.get("mode") or "predict")
    try:
        if mode == "bootstrap_selftest":
            result = bootstrap_selftest(req, state_root, out, repo_root, understat_db, confirmation_dir)
        elif mode == "cache_reuse_probe":
            result = cache_reuse_probe(req, state_root, out, repo_root, understat_db, confirmation_dir)
        elif mode == "missing_data_probe":
            result = missing_data_probe(req, state_root, out, repo_root, understat_db, confirmation_dir)
        elif mode == "predict":
            result = normal_request(req, state_root, out, repo_root, understat_db, confirmation_dir)
        else:
            raise rt.RuntimeGateError(f"unknown gateway mode: {mode}")
    except rt.RuntimeGateError as exc:
        result = {"status": "FAIL_CLOSED", "probe": mode, "reason": str(exc), "prediction_sha": None, "receipt_sha": None}
        write_json(out / "formal_gap.json", result)
    result["gateway_schema"] = SCHEMA
    result["formal_head"] = rt.FORMAL_HEAD
    result["formal_current_sha256"] = rt.CURRENT_SHA256
    result["runtime_baseline_head"] = BASELINE_RUNTIME_HEAD
    result["request_sha"] = sha(req)
    write_json(out / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"PASS", "PASS_EXPECTED_FAIL_CLOSED", "FORMAL_INPUT_DATA_INCOMPLETE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
