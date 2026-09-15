#!/usr/bin/env python3
"""Validate Football3 Nova global free historical-source catalog.

This validator keeps two questions separate:
1) may the source enter the permanent reusable historical library?
2) may the source satisfy the exact N1 2024/25 Big3 match-level deep+PPDA contract?

It never opens match labels and never modifies Formal V2/CURRENT/production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

TARGET_BIG3 = {"Bundesliga", "Serie_A", "Ligue_1"}
REUSABLE_LICENSE_STATES = {
    "VERIFIED_REUSABLE",
    "VERIFIED_REUSABLE_WITH_ATTRIBUTION_TERMS",
    "VERIFIED_REUSABLE_NONCOMMERCIAL_SHAREALIKE",
}


class CatalogError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise CatalogError("catalog root must be object")
    return data


def _nonnull_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{field} must be non-empty string")
    return value.strip()


def _validate_source(source: dict[str, Any]) -> None:
    required = {
        "source_id",
        "name",
        "provider",
        "url",
        "free_access",
        "registration_required",
        "license_status",
        "license_id",
        "match_level",
        "deep_ppda",
        "coverage",
        "historical_library_eligible",
        "n1_exact_feature_eligible",
        "n1_exact_scope",
        "status",
        "notes",
    }
    missing = sorted(required - set(source))
    if missing:
        raise CatalogError(f"source missing fields: {missing}")
    _nonnull_text(source["source_id"], "source_id")
    _nonnull_text(source["name"], "name")
    _nonnull_text(source["provider"], "provider")
    _nonnull_text(source["url"], "url")
    _nonnull_text(source["license_status"], "license_status")
    _nonnull_text(source["status"], "status")
    _nonnull_text(source["notes"], "notes")
    if not isinstance(source["free_access"], bool):
        raise CatalogError("free_access must be bool")
    if not isinstance(source["registration_required"], bool):
        raise CatalogError("registration_required must be bool")
    if source["match_level"] not in (True, False, None):
        raise CatalogError("match_level must be bool/null")
    if source["deep_ppda"] not in (True, False, None):
        raise CatalogError("deep_ppda must be bool/null")
    if not isinstance(source["coverage"], list) or not all(isinstance(x, str) for x in source["coverage"]):
        raise CatalogError("coverage must be list[str]")
    if not isinstance(source["n1_exact_scope"], list) or not all(isinstance(x, str) for x in source["n1_exact_scope"]):
        raise CatalogError("n1_exact_scope must be list[str]")
    if not isinstance(source["historical_library_eligible"], bool):
        raise CatalogError("historical_library_eligible must be bool")
    if not isinstance(source["n1_exact_feature_eligible"], bool):
        raise CatalogError("n1_exact_feature_eligible must be bool")

    if source["historical_library_eligible"]:
        if source["license_status"] not in REUSABLE_LICENSE_STATES:
            raise CatalogError(
                f"{source['source_id']}: eligible historical source lacks verified reusable licence"
            )
        if not source["free_access"]:
            raise CatalogError(f"{source['source_id']}: eligible historical source must be free")
        if source["license_id"] is None or not str(source["license_id"]).strip():
            raise CatalogError(f"{source['source_id']}: eligible historical source missing licence id")

    if source["n1_exact_feature_eligible"]:
        if not source["historical_library_eligible"]:
            raise CatalogError(f"{source['source_id']}: N1 exact source must also be reusable")
        if source["match_level"] is not True or source["deep_ppda"] is not True:
            raise CatalogError(f"{source['source_id']}: N1 exact source must be match-level deep+PPDA")
        if not source["n1_exact_scope"]:
            raise CatalogError(f"{source['source_id']}: N1 exact source missing scope")


def _big3_exact_coverage(sources: list[dict[str, Any]]) -> dict[str, bool]:
    result = {league: False for league in sorted(TARGET_BIG3)}
    for source in sources:
        if not source["n1_exact_feature_eligible"]:
            continue
        for item in source["n1_exact_scope"]:
            for league in TARGET_BIG3:
                if league in item and "2024/25" in item:
                    result[league] = True
    return result


def validate_catalog(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "football3-nova-free-history-source-catalog-v1":
        raise CatalogError("unexpected schema_version")
    target = data.get("target_exact_n1_scope")
    if not isinstance(target, dict):
        raise CatalogError("target_exact_n1_scope missing")
    if target.get("season") != "2024/25":
        raise CatalogError("N1 target season drift")
    if set(target.get("competitions", [])) != TARGET_BIG3:
        raise CatalogError("N1 Big3 target drift")
    if target.get("required_granularity") != "match_level":
        raise CatalogError("N1 granularity drift")
    if set(target.get("required_features", [])) != {"deep", "ppda"}:
        raise CatalogError("N1 feature contract drift")

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CatalogError("sources must be non-empty list")
    ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise CatalogError("source entry must be object")
        _validate_source(source)
        source_id = source["source_id"]
        if source_id in ids:
            raise CatalogError(f"duplicate source_id: {source_id}")
        ids.add(source_id)

    qualified = [s for s in sources if s["historical_library_eligible"]]
    blocked_license = [s for s in sources if str(s["status"]).startswith("BLOCKED_LICENSE")]
    exact = _big3_exact_coverage(sources)
    global_conclusion = data.get("global_conclusion")
    if not isinstance(global_conclusion, dict):
        raise CatalogError("global_conclusion missing")
    expected_complete = all(exact.values())
    if bool(global_conclusion.get("n1_big3_2024_25_exact_deep_ppda_licensed_free_coverage_complete")) != expected_complete:
        raise CatalogError("global exact-coverage conclusion inconsistent with source catalog")
    if expected_complete:
        expected_status = "READY_FOR_ZERO_LABEL_IDENTITY_COVERAGE"
    else:
        expected_status = "STOP_DATA_COVERAGE"
    if global_conclusion.get("n1_big3_status") != expected_status:
        raise CatalogError("N1 Big3 status inconsistent with exact coverage")
    if global_conclusion.get("labels_opened") != 0:
        raise CatalogError("source discovery must not open test labels")
    for field in ("formal_v2_changed", "current_changed", "production_changed"):
        if global_conclusion.get(field) is not False:
            raise CatalogError(f"{field} must remain false")

    return {
        "status": "PASS",
        "schema_version": data["schema_version"],
        "source_count": len(sources),
        "qualified_reusable_source_count": len(qualified),
        "blocked_license_source_count": len(blocked_license),
        "n1_big3_exact_coverage": exact,
        "n1_big3_status": expected_status,
        "labels_opened": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    catalog_path = Path(args.catalog)
    data = _load(catalog_path)
    receipt = validate_catalog(data)
    receipt["catalog_sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    out = Path(args.receipt)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
