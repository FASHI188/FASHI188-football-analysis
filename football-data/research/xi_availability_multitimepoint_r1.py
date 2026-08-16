#!/usr/bin/env python3
"""Strict zero-label multi-timepoint wrapper for public availability snapshots.

This wrapper reuses xi_availability_public_coverage_r1.py and adds a PIT timing
contract. It never reads result labels, trains, scores, or mutates formal assets.

Slots:
- BASELINE: infrastructure validation only; never relabel as a timed PIT freeze.
- T-24h: capture target must equal kickoff minus 24 hours.
- T-6h: capture target must equal kickoff minus 6 hours.
- T-75min: availability-only snapshot near lineup release. This is NOT a
  confirmed-XI capture and must never be represented as one.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "research" / "eng_pl_2026_27_mw1_fixture_freeze_20260816.json"
TRIGGER = ROOT / "research" / "xi_availability_capture_trigger_r1.json"
COLLECTOR = ROOT / "research" / "xi_availability_public_coverage_r1.py"
OUT_ROOT = ROOT / "research" / "artifacts" / "xi_availability_multitimepoint_r1"

SLOT_OFFSETS = {
    "T-24h": timedelta(hours=24),
    "T-6h": timedelta(hours=6),
    "T-75min": timedelta(minutes=75),
}
SLOT_TOLERANCE = {
    "T-24h": timedelta(minutes=60),
    "T-6h": timedelta(minutes=45),
    "T-75min": timedelta(minutes=20),
}


class ContractError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("missing UTC timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ContractError(f"timestamp lacks timezone: {value}")
    return dt.astimezone(timezone.utc)


def load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError(f"{path}: expected JSON object")
    return data


def safe_capture_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContractError("capture_id is required")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(ch not in allowed for ch in text):
        raise ContractError("capture_id contains unsafe characters")
    return text


def write_stop(out: Path, payload: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "capture_status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    trigger = load_object(TRIGGER)
    fixtures_doc = load_object(FIXTURES)

    if trigger.get("research_only") is not True or trigger.get("label_access") is not False:
        raise ContractError("trigger must set research_only=true and label_access=false")
    if fixtures_doc.get("label_access") is not False:
        raise ContractError("fixture manifest must keep label_access=false")

    capture_id = safe_capture_id(trigger.get("capture_id"))
    slot = str(trigger.get("slot") or "").strip()
    if slot not in {"BASELINE", *SLOT_OFFSETS.keys()}:
        raise ContractError(f"unsupported slot: {slot}")

    target = parse_utc(trigger.get("capture_target_utc"))
    selected_ids = trigger.get("match_ids")
    if not isinstance(selected_ids, list) or not selected_ids:
        raise ContractError("match_ids must be a non-empty list")
    selected_ids = [str(x) for x in selected_ids]
    if len(set(selected_ids)) != len(selected_ids):
        raise ContractError("duplicate match_ids in trigger")

    fixture_rows = fixtures_doc.get("fixtures")
    if not isinstance(fixture_rows, list):
        raise ContractError("fixture manifest missing fixtures")
    by_id = {str(row.get("match_id")): row for row in fixture_rows if isinstance(row, dict)}
    missing = sorted(set(selected_ids) - set(by_id))
    if missing:
        raise ContractError(f"unknown match_ids: {missing}")

    now = utc_now()
    out = OUT_ROOT / capture_id
    contract: dict[str, Any] = {
        "schema_version": "xi-availability-multitimepoint-r1",
        "research_only": True,
        "label_access": False,
        "scientific_claim": "NONE",
        "capture_id": capture_id,
        "slot": slot,
        "capture_target_utc": iso(target),
        "runner_started_at_utc": iso(now),
        "match_ids": selected_ids,
        "timing_checks": [],
        "confirmed_xi": False,
        "confirmed_xi_claim_allowed": False,
    }

    if slot == "BASELINE":
        contract["timing_verdict"] = "BASELINE_NOT_A_TIMED_FREEZE"
    else:
        offset = SLOT_OFFSETS[slot]
        tolerance = SLOT_TOLERANCE[slot]
        timing_errors: list[str] = []
        for match_id in selected_ids:
            row = by_id[match_id]
            kickoff = parse_utc(row.get("kickoff_at_utc"))
            expected = kickoff - offset
            target_error = abs((target - expected).total_seconds())
            run_error = abs((now - target).total_seconds())
            check = {
                "match_id": match_id,
                "kickoff_at_utc": iso(kickoff),
                "expected_target_utc": iso(expected),
                "configured_target_utc": iso(target),
                "target_error_seconds": int(target_error),
                "runner_error_seconds": int(run_error),
                "runner_tolerance_seconds": int(tolerance.total_seconds()),
            }
            contract["timing_checks"].append(check)
            if target_error > 60:
                timing_errors.append(f"TARGET_MISMATCH:{match_id}:{int(target_error)}s")
            if run_error > tolerance.total_seconds():
                timing_errors.append(f"RUN_OUTSIDE_WINDOW:{match_id}:{int(run_error)}s")
        if timing_errors:
            contract["timing_verdict"] = "STOP_TIMING_PIT"
            contract["timing_errors"] = timing_errors
            write_stop(out, contract)
            print(json.dumps(contract, ensure_ascii=False, indent=2))
            return 3
        contract["timing_verdict"] = "PASS_TIMING_PIT"

    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(COLLECTOR),
        "--fixtures",
        str(FIXTURES),
        "--out",
        str(out),
    ]
    proc = subprocess.run(cmd, check=False)

    coverage_path = out / "coverage_status.json"
    if not coverage_path.exists():
        contract["collector_exit_code"] = proc.returncode
        contract["collector_verdict"] = "MISSING_COVERAGE_STATUS"
        write_stop(out, contract)
        return 4

    coverage = load_object(coverage_path)
    contract["collector_exit_code"] = proc.returncode
    contract["collector_verdict"] = coverage.get("verdict")
    contract["source_snapshots"] = coverage.get("source_snapshots")
    contract["source_errors"] = coverage.get("source_errors")
    contract["coverage"] = coverage.get("coverage")
    contract["hard_violations"] = coverage.get("hard_violations")
    contract["runner_finished_at_utc"] = iso(utc_now())
    contract["availability_snapshot_only"] = True
    contract["next_if_pass"] = (
        "For BASELINE: schedule true T-24h/T-6h freezes. For T-75min: keep this "
        "availability snapshot separate from the confirmed-XI collector."
    )

    verdict_ok = proc.returncode == 0 and coverage.get("verdict") == "PASS_ZERO_LABEL_PUBLIC_AVAILABILITY_COVERAGE"
    if slot == "BASELINE":
        contract["verdict"] = (
            "PASS_BASELINE_INFRASTRUCTURE_ONLY" if verdict_ok else "STOP_BASELINE_COLLECTION"
        )
    else:
        contract["verdict"] = (
            "PASS_ZERO_LABEL_TIMED_AVAILABILITY_FREEZE" if verdict_ok else "STOP_DATA_COVERAGE"
        )

    write_stop(out, contract)
    print(json.dumps(contract, ensure_ascii=False, indent=2))
    return 0 if verdict_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
