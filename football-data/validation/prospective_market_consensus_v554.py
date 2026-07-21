#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in __import__('sys').path:
    __import__('sys').path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import canonical_sha256, validate as validate_snapshot

OUT_ROOT = ROOT / "evidence" / "market_consensus_prospective"
MAX_SKEW_SECONDS = 300
MIN_INDEPENDENT_PROVIDERS = 2


def _dt(value: str) -> datetime:
    token = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(token)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must carry timezone: {value}")
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _same_identity(rows: list[dict[str, Any]], field: str) -> Any:
    values = {json.dumps(row.get(field), ensure_ascii=False, sort_keys=True) for row in rows}
    if len(values) != 1:
        raise ValueError(f"constituent snapshot identity mismatch: {field}")
    return rows[0].get(field)


def _mean_prices(rows: list[dict[str, Any]], surface: str, price_fields: tuple[str, ...]) -> dict[str, float]:
    return {field: mean(float(row[surface][field]) for row in rows) for field in price_fields}


def _line_consensus(rows: list[dict[str, Any]], surface: str, price_fields: tuple[str, ...]) -> dict[str, Any] | None:
    lines = [float(row[surface]["line"]) for row in rows]
    if max(lines) - min(lines) > 1e-9:
        return None
    return {"line": lines[0], **_mean_prices(rows, surface, price_fields)}


def _fixed_ou25_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the immutable fixed OU2.5 research reference for one provider.

    Prefer an explicitly stored research-reference surface. If the actually observed
    main OU line itself was 2.5, use that as a valid fallback. Never reinterpret a
    2.75/3.0 main line as 2.5.
    """
    research = ((row.get("research_reference_surfaces") or {}).get("over_under_2_5"))
    if isinstance(research, dict):
        try:
            line = float(research.get("line"))
            over = float(research["over"])
            under = float(research["under"])
            observed_at = str(research["observed_at_utc"])
        except Exception as exc:
            raise ValueError(f"invalid explicit OU2.5 research reference for provider {row.get('provider_group')}: {exc}") from exc
        if abs(line - 2.5) > 1e-9:
            raise ValueError(f"explicit OU2.5 research reference has wrong line: {line}")
        if over <= 1.0 or under <= 1.0:
            raise ValueError("explicit OU2.5 research reference decimal odds must be >1.0")
        return {"line": 2.5, "over": over, "under": under, "observed_at_utc": observed_at, "source": "research_reference_surfaces"}

    main_ou = row.get("over_under") or {}
    try:
        line = float(main_ou.get("line"))
    except Exception:
        return None
    if abs(line - 2.5) > 1e-9:
        return None
    observed_at = str((row.get("surface_observed_at_utc") or {}).get("over_under") or "")
    return {
        "line": 2.5,
        "over": float(main_ou["over"]),
        "under": float(main_ou["under"]),
        "observed_at_utc": observed_at,
        "source": "main_over_under_surface",
    }


def _fixed_ou25_consensus(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[datetime]]:
    references = [_fixed_ou25_row(row) for row in rows]
    if any(ref is None for ref in references):
        return None, []
    refs = [ref for ref in references if ref is not None]
    times = [_dt(str(ref["observed_at_utc"])) for ref in refs]
    return {
        "line": 2.5,
        "over": mean(float(ref["over"]) for ref in refs),
        "under": mean(float(ref["under"]) for ref in refs),
        "constituent_reference_sources": [str(ref["source"]) for ref in refs],
    }, times


def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < MIN_INDEPENDENT_PROVIDERS:
        raise ValueError(f"consensus requires at least {MIN_INDEPENDENT_PROVIDERS} valid independent provider snapshots")
    validations = [validate_snapshot(row) for row in rows]
    bad = [i for i, result in enumerate(validations) if not result.get("passed")]
    if bad:
        raise ValueError(f"invalid constituent V5.2.3 snapshots at indexes {bad}")

    for field in ("competition_id", "season", "home_team", "away_team", "kickoff_utc", "settlement_scope"):
        _same_identity(rows, field)

    provider_groups = [str(row["provider_group"]) for row in rows]
    if len(provider_groups) != len(set(provider_groups)):
        raise ValueError("provider_group values must be independent and unique inside a consensus")

    all_consensus_times: list[datetime] = []
    one_times: list[datetime] = []
    ou_times: list[datetime] = []
    ah_times: list[datetime] = []
    for row in rows:
        observed = row["surface_observed_at_utc"]
        one_times.append(_dt(observed["one_x_two"]))
        ah_times.append(_dt(observed["asian_handicap"]))
        ou_times.append(_dt(observed["over_under"]))
        all_consensus_times.extend((one_times[-1], ah_times[-1], ou_times[-1]))

    one = _mean_prices(rows, "one_x_two", ("home", "draw", "away"))
    ah = _line_consensus(rows, "asian_handicap", ("home", "away"))
    ou = _line_consensus(rows, "over_under", ("over", "under"))
    ou25, ou25_times = _fixed_ou25_consensus(rows)
    all_consensus_times.extend(ou25_times)

    global_skew = (max(all_consensus_times) - min(all_consensus_times)).total_seconds()
    if global_skew > MAX_SKEW_SECONDS:
        raise ValueError(f"cross-provider market consensus exceeds {MAX_SKEW_SECONDS}s synchronization window: {global_skew}")

    observed_at = max(all_consensus_times)
    kickoff = _dt(str(rows[0]["kickoff_utc"]))
    if observed_at >= kickoff:
        raise ValueError("consensus observation must precede kickoff")

    consensus_surface_times: dict[str, str] = {
        "one_x_two": _iso(max(one_times)),
        "asian_handicap": _iso(max(ah_times)),
        "over_under": _iso(max(ou_times)),
    }
    if ou25_times:
        consensus_surface_times["over_under_2_5"] = _iso(max(ou25_times))

    payload = {
        "schema_version": "V5.5.4-prospective-market-consensus-r1",
        "competition_id": rows[0]["competition_id"],
        "season": rows[0]["season"],
        "home_team": rows[0]["home_team"],
        "away_team": rows[0]["away_team"],
        "kickoff_utc": rows[0]["kickoff_utc"],
        "settlement_scope": rows[0]["settlement_scope"],
        "consensus_observed_at_utc": _iso(observed_at),
        "consensus_surface_observed_at_utc": consensus_surface_times,
        "cross_provider_timestamp_spread_seconds": global_skew,
        "provider_count": len(rows),
        "provider_groups": sorted(provider_groups),
        "provider_names": sorted(str(row["provider_name"]) for row in rows),
        "constituent_snapshot_sha256": sorted(str(row["raw_snapshot_sha256"]) for row in rows),
        "constituent_source_urls": sorted(str(row["source_url"]) for row in rows),
        "aggregation": "arithmetic_mean_decimal_prices_then_surface_devig_downstream",
        "one_x_two": one,
        "asian_handicap": ah,
        "over_under": ou,
        "over_under_2_5": ou25,
        "surface_consensus_eligibility": {
            "one_x_two": True,
            "asian_handicap": ah is not None,
            "over_under": ou is not None,
            "over_under_2_5": ou25 is not None,
        },
        "promotion_evidence_eligible": True,
        "retrospective_backfill": False,
        "formal_weight_change": False,
        "probability_change": False,
    }
    payload["consensus_sha256"] = canonical_sha256(payload)
    return payload


def validate_consensus(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "V5.5.4-prospective-market-consensus-r1":
        errors.append("SCHEMA_MISMATCH")
    if int(payload.get("provider_count") or 0) < MIN_INDEPENDENT_PROVIDERS:
        errors.append("INSUFFICIENT_INDEPENDENT_PROVIDERS")
    groups = list(payload.get("provider_groups") or [])
    if len(groups) != len(set(groups)) or len(groups) != int(payload.get("provider_count") or 0):
        errors.append("PROVIDER_GROUP_INDEPENDENCE_FAIL")
    if float(payload.get("cross_provider_timestamp_spread_seconds") or 1e9) > MAX_SKEW_SECONDS:
        errors.append("CONSENSUS_TIMESTAMP_SKEW_FAIL")

    one = payload.get("one_x_two") or {}
    for key in ("home", "draw", "away"):
        try:
            if float(one[key]) <= 1.0:
                errors.append(f"INVALID_1X2_{key.upper()}_PRICE")
        except Exception:
            errors.append(f"MISSING_1X2_{key.upper()}_PRICE")

    eligibility = payload.get("surface_consensus_eligibility") or {}
    if bool(eligibility.get("over_under_2_5")):
        ou25 = payload.get("over_under_2_5") or {}
        try:
            if abs(float(ou25["line"]) - 2.5) > 1e-9:
                errors.append("OU25_CONSENSUS_LINE_MISMATCH")
            if float(ou25["over"]) <= 1.0 or float(ou25["under"]) <= 1.0:
                errors.append("OU25_CONSENSUS_INVALID_PRICE")
            _dt(str((payload.get("consensus_surface_observed_at_utc") or {})["over_under_2_5"]))
        except Exception:
            errors.append("OU25_CONSENSUS_MALFORMED")

    expected_hash = str(payload.get("consensus_sha256") or "")
    unhashed = dict(payload)
    unhashed.pop("consensus_sha256", None)
    if expected_hash != canonical_sha256(unhashed):
        errors.append("CONSENSUS_HASH_MISMATCH")
    try:
        if _dt(str(payload.get("consensus_observed_at_utc"))) >= _dt(str(payload.get("kickoff_utc"))):
            errors.append("CONSENSUS_NOT_PREKICKOFF")
    except Exception:
        errors.append("CONSENSUS_TIMESTAMP_PARSE_FAIL")
    return {"passed": not errors, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshots", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args()
    rows = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.snapshots]
    payload = build(rows)
    result = validate_consensus(payload)
    if not result["passed"]:
        raise ValueError(f"consensus validation failed: {result['errors']}")
    if args.out:
        out = Path(args.out)
    else:
        freeze_token = payload["consensus_observed_at_utc"].replace(":", "").replace("+00:00", "Z")
        out = OUT_ROOT / f"{payload['competition_id']}__{payload['home_team']}__{payload['away_team']}__{freeze_token}__n{payload['provider_count']}.json"
    if out.exists():
        raise FileExistsError(f"immutable consensus already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "VALID_MARKET_CONSENSUS_WRITTEN", "path": str(out), "consensus_sha256": payload["consensus_sha256"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
