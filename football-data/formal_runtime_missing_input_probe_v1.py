#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "formal_fast_runtime_v1"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import runtime as rt

SCHEMA = "football3-missing-input-route-probe-v1"


def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj):
    return hashlib.sha256(canon(obj)).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    understat_db = Path(args.understat_db).resolve()
    confirmation_dir = Path(args.confirmation_dir).resolve()
    work = Path(args.work).resolve()
    out = Path(args.out).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    # Real supported-league future fixture identity. This probe is routing-only and never emits a prediction.
    comp = "FRA_Ligue1"
    season = "2026/27"
    home = "Lyon"
    away = "Auxerre"
    kickoff = datetime(2026, 9, 4, 17, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)
    fixture = {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": rt._global_team_id(home),
        "away_team_id": rt._global_team_id(away),
        "home_team_name": home,
        "away_team_name": away,
    }
    inp = {
        "schema_version": rt.INPUT_SCHEMA,
        "fixture": fixture,
        "cutoff": cutoff.isoformat(),
        "delta_coverage": {"schema_version": rt.DELTA_SCHEMA, "status": "UNKNOWN"},
        "model_delta": [],
    }
    input_path = out / "missing_input_runtime_input.json"
    input_path.write_bytes(canon(inp))
    bundle = work / "bundle"

    try:
        rt.predict_match(comp, home, away, kickoff, cutoff, bundle, input_path,
                         repo_root, understat_db, confirmation_dir, False)
    except rt.RuntimeGateError as exc:
        reason = str(exc)
        if "FORMAL_INPUT_DATA_INCOMPLETE" not in reason:
            raise
        result = {
            "schema_version": SCHEMA,
            "status": "PASS_EXPECTED_FAIL_CLOSED",
            "fixture": fixture,
            "cutoff": cutoff.isoformat(),
            "runtime_input_sha": sha(inp),
            "preconditions": {
                "cache_present": False,
                "delta_status": "UNKNOWN",
                "fast_eligible": False,
                "full_required": True,
            },
            "full_result": "FORMAL_INPUT_DATA_INCOMPLETE",
            "reason": reason,
            "prediction_sha": None,
            "receipt_sha": None,
            "state_bundle_created": bundle.exists(),
            "manual_or_auxiliary_fallback_used": False,
            "formal_head": rt.FORMAL_HEAD,
            "formal_current_sha256": rt.CURRENT_SHA256,
            "runtime_schema": rt.SCHEMA,
        }
        (out / "missing_input_probe.json").write_bytes(canon(result))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    raise rt.RuntimeGateError("missing-input route unexpectedly produced a formal prediction")


if __name__ == "__main__":
    raise SystemExit(main())
