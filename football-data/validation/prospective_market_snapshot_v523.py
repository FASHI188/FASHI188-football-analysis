#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config" / "prospective_market_snapshot_contract_v523.json"


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} missing")
    token = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(token)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} missing timezone")
    return parsed.astimezone(timezone.utc)


def _price(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(number) or number <= 1.0:
        raise ValueError(f"{field} must be decimal odds > 1")
    return number


def _line(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    # Quarter-line settlement is the registered market convention.
    if abs(number * 4.0 - round(number * 4.0)) > 1e-8:
        raise ValueError(f"{field} must be a quarter-line increment")
    return number


def canonical_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = dict(snapshot)
    payload.pop("raw_snapshot_sha256", None)
    payload.pop("validation", None)
    return payload


def canonical_sha256(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        canonical_payload(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate(snapshot: dict[str, Any]) -> dict[str, Any]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    errors = []

    for field in contract["required_fixture_identity"] + contract["required_freeze_fields"]:
        if field not in snapshot or snapshot[field] in (None, ""):
            errors.append(f"missing required field: {field}")

    kickoff = freeze = accessed = observed = None
    try:
        kickoff = _parse_utc(snapshot.get("kickoff_utc"), "kickoff_utc")
        freeze = _parse_utc(snapshot.get("freeze_utc"), "freeze_utc")
        accessed = _parse_utc(snapshot.get("accessed_at_utc"), "accessed_at_utc")
        observed = _parse_utc(snapshot.get("source_observed_at_utc"), "source_observed_at_utc")
    except Exception as exc:
        errors.append(str(exc))

    if kickoff and freeze and not freeze < kickoff:
        errors.append("freeze_utc must precede kickoff_utc")
    if kickoff and observed and not observed < kickoff:
        errors.append("source_observed_at_utc must precede kickoff_utc")
    if accessed and observed and observed > accessed:
        errors.append("source_observed_at_utc cannot be later than accessed_at_utc")

    surface_times = snapshot.get("surface_observed_at_utc")
    parsed_surface_times = {}
    if not isinstance(surface_times, dict):
        errors.append("surface_observed_at_utc must be an object")
    else:
        for surface in ("one_x_two", "asian_handicap", "over_under"):
            try:
                parsed_surface_times[surface] = _parse_utc(surface_times.get(surface), f"surface_observed_at_utc.{surface}")
            except Exception as exc:
                errors.append(str(exc))
        if kickoff:
            for surface, ts in parsed_surface_times.items():
                if not ts < kickoff:
                    errors.append(f"surface_observed_at_utc.{surface} must precede kickoff_utc")
        if len(parsed_surface_times) == 3:
            spread = (max(parsed_surface_times.values()) - min(parsed_surface_times.values())).total_seconds()
            if spread > float(contract["hard_gates"]["maximum_surface_timestamp_spread_seconds"]):
                errors.append(f"market surface timestamp spread {spread:.0f}s exceeds contract maximum")

    one = snapshot.get("one_x_two")
    if not isinstance(one, dict):
        errors.append("one_x_two must be an object")
    else:
        for key in ("home", "draw", "away"):
            try:
                _price(one.get(key), f"one_x_two.{key}")
            except Exception as exc:
                errors.append(str(exc))

    ah = snapshot.get("asian_handicap")
    if not isinstance(ah, dict):
        errors.append("asian_handicap must be an object")
    else:
        try:
            _line(ah.get("line"), "asian_handicap.line")
        except Exception as exc:
            errors.append(str(exc))
        for key in ("home", "away"):
            try:
                _price(ah.get(key), f"asian_handicap.{key}")
            except Exception as exc:
                errors.append(str(exc))

    ou = snapshot.get("over_under")
    if not isinstance(ou, dict):
        errors.append("over_under must be an object")
    else:
        try:
            _line(ou.get("line"), "over_under.line")
        except Exception as exc:
            errors.append(str(exc))
        for key in ("over", "under"):
            try:
                _price(ou.get(key), f"over_under.{key}")
            except Exception as exc:
                errors.append(str(exc))

    for field in ("source_url", "provider_name", "provider_group", "competition_id", "season", "home_team", "away_team", "settlement_scope"):
        if not str(snapshot.get(field) or "").strip():
            errors.append(f"{field} must be non-empty")

    computed_hash = canonical_sha256(snapshot)
    supplied_hash = str(snapshot.get("raw_snapshot_sha256") or "").strip().lower()
    if not supplied_hash:
        errors.append("raw_snapshot_sha256 missing")
    elif supplied_hash != computed_hash:
        errors.append("raw_snapshot_sha256 does not match canonical payload")

    return {
        "schema_version": "V5.2.3-prospective-market-snapshot-validation-r1",
        "validated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "passed": not errors,
        "errors": errors,
        "computed_raw_snapshot_sha256": computed_hash,
        "surface_timestamp_spread_seconds": (
            (max(parsed_surface_times.values()) - min(parsed_surface_times.values())).total_seconds()
            if len(parsed_surface_times) == 3 else None
        ),
        "formal_pit_eligible": not errors,
        "formal_probability_change_authorized": False,
        "note": "Validation establishes snapshot evidence integrity only. Actual market-residual use remains subject to CURRENT per-match synchronization/data-quality gates and unified-matrix audit."
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", help="Path to one prospective market JSON snapshot")
    parser.add_argument("--write-validation", action="store_true")
    args = parser.parse_args()
    path = Path(args.snapshot)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    result = validate(snapshot)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.write_validation:
        snapshot["validation"] = result
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
