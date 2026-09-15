#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

QUALIFIED = {
    "QUALIFIED_REUSABLE_TRACKING",
    "QUALIFIED_IDENTITY_RESULTS",
    "QUALIFIED_REUSABLE_GATED",
}


def validate(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "football3-nova-free-history-source-extension-open-tracking-v1":
        raise ValueError("unexpected schema_version")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be non-empty")
    ids = []
    qualified = 0
    open_rich = 0
    identity = 0
    for src in sources:
        required = ["source_id", "name", "provider", "url", "source_revision", "free_access", "registration_required", "license_status", "match_level", "data_class", "coverage", "historical_library_eligible", "n1_exact_feature_eligible", "status", "notes"]
        missing = [k for k in required if k not in src]
        if missing:
            raise ValueError(f"{src.get('source_id')}: missing {missing}")
        ids.append(src["source_id"])
        if src["historical_library_eligible"] and src["status"] not in QUALIFIED:
            raise ValueError(f"{src['source_id']}: eligible source must have qualified status")
        if src["historical_library_eligible"] and not src["free_access"]:
            raise ValueError(f"{src['source_id']}: permanent free library source must be free_access")
        if src["n1_exact_feature_eligible"]:
            raise ValueError(f"{src['source_id']}: extension must not silently qualify N1 exact feature route")
        if src["status"] in QUALIFIED:
            qualified += 1
        if src["status"] == "QUALIFIED_REUSABLE_TRACKING":
            open_rich += 1
        if src["status"] == "QUALIFIED_IDENTITY_RESULTS":
            identity += 1
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source_id")
    c = data.get("conclusion", {})
    expected = {
        "new_qualified_reusable_source_count": qualified,
        "new_open_tracking_or_event_source_count": open_rich,
        "new_identity_result_backbone_count": identity,
        "n1_big3_2024_25_exact_deep_ppda_licensed_free_coverage_complete": False,
        "n1_big3_status": "STOP_DATA_COVERAGE",
        "labels_opened": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    for key, value in expected.items():
        if c.get(key) != value:
            raise ValueError(f"conclusion mismatch {key}: {c.get(key)!r} != {value!r}")
    raw = path.read_bytes()
    return {
        "status": "PASS",
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "source_count": len(sources),
        "qualified_reusable_source_count": qualified,
        "qualified_tracking_source_count": open_rich,
        "qualified_identity_result_backbone_count": identity,
        "n1_big3_status": c["n1_big3_status"],
        "labels_opened": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--receipt", required=True)
    args = ap.parse_args()
    receipt = validate(Path(args.catalog))
    out = Path(args.receipt)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
