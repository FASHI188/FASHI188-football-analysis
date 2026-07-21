#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from kambi_v523_adapter_v5511 import extract_full_time_ah, extract_full_time_ou
from prospective_market_snapshot_v523 import canonical_sha256, validate

SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
BUNDLE_ROOT = ROOT / "evidence" / "market_line_bundles_prospective"
MANIFEST = ROOT / "manifests" / "kambi_multiline_bundle_v5528_status.json"
ALLOWED_COMPETITIONS = {"POR_PrimeiraLiga", "ESP_LaLiga", "FRA_Ligue1", "GER_Bundesliga"}


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def safe(value: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def choose_per_line(candidates: list[dict[str, Any]], *, market: str) -> list[dict[str, Any]]:
    grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[round(float(row["line"]), 6)].append(row)
    result = []
    for line in sorted(grouped):
        pool = grouped[line]
        selected = sorted(
            pool,
            key=lambda x: (
                float(x.get("overround_abs", 999.0)),
                float(x.get("balance_distance", 999.0)),
                int(x.get("offer_id", 0)),
            ),
        )[0]
        if market == "ah":
            result.append({
                "line": float(selected["line"]),
                "away_line": float(selected["away_line"]),
                "home": float(selected["home"]),
                "away": float(selected["away"]),
                "offer_id": int(selected["offer_id"]),
                "main_line_tag": bool(selected.get("main_line_tag")),
                "overround_abs": float(selected.get("overround_abs", 0.0)),
                "balance_distance": float(selected.get("balance_distance", 0.0)),
                "provider_changed_at_utc": list(selected.get("provider_changed_at_utc") or []),
                "same_line_candidate_count": len(pool),
            })
        else:
            result.append({
                "line": float(selected["line"]),
                "over": float(selected["over"]),
                "under": float(selected["under"]),
                "offer_id": int(selected["offer_id"]),
                "main_line_tag": bool(selected.get("main_line_tag")),
                "overround_abs": float(selected.get("overround_abs", 0.0)),
                "balance_distance": float(selected.get("balance_distance", 0.0)),
                "provider_changed_at_utc": list(selected.get("provider_changed_at_utc") or []),
                "same_line_candidate_count": len(pool),
            })
    return result


def bundle_path(snapshot: dict[str, Any]) -> Path:
    token = str(snapshot["freeze_utc"]).replace(":", "").replace("+00:00", "Z")
    return BUNDLE_ROOT / (
        f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__"
        f"kambi_multiline__{token}.json"
    )


def build_for_snapshot(snapshot: dict[str, Any], snapshot_path: Path) -> tuple[dict[str, Any], Path]:
    validation = validate(snapshot)
    if not validation.get("passed") or not validation.get("formal_pit_eligible"):
        raise ValueError(f"parent Kambi snapshot invalid: {validation.get('errors')}")
    if snapshot.get("provider_group") != "kambi":
        raise ValueError("parent snapshot is not Kambi")
    source_adapter = snapshot.get("source_adapter") or {}
    raw_rel = source_adapter.get("parent_raw_evidence_path")
    if not raw_rel:
        raise ValueError("parent raw Kambi evidence path missing")
    raw_path = ROOT / str(raw_rel)
    envelope = json.loads(raw_path.read_text(encoding="utf-8"))
    expected_raw_sha = str(source_adapter.get("parent_raw_response_sha256") or "")
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else None
    if payload is None:
        raise ValueError("raw Kambi payload missing")
    encoded_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    # Envelope payload_sha256 is the hash of raw HTTP bytes; retain it as authority. Re-serialization is audit-only.
    envelope_raw_sha = str(envelope.get("payload_sha256") or "")
    if expected_raw_sha and envelope_raw_sha and expected_raw_sha != envelope_raw_sha:
        raise ValueError("parent raw response SHA mismatch")
    display_names = source_adapter.get("source_display_names") or {}
    source_home = str(display_names.get("home") or snapshot.get("home_team") or "")
    source_away = str(display_names.get("away") or snapshot.get("away_team") or "")
    _, ah_candidates = extract_full_time_ah(payload, source_home, source_away)
    _, ou_candidates = extract_full_time_ou(payload)
    ah_lines = choose_per_line(ah_candidates, market="ah")
    ou_lines = choose_per_line(ou_candidates, market="ou")
    if not ah_lines or not ou_lines:
        raise ValueError("no valid Kambi full-time multiline AH/OU candidates")
    bundle: dict[str, Any] = {
        "schema_version": "V5.5.28-kambi-exact-multiline-bundle-r1",
        "competition_id": snapshot["competition_id"],
        "season": snapshot["season"],
        "home_team": snapshot["home_team"],
        "away_team": snapshot["away_team"],
        "kickoff_utc": snapshot["kickoff_utc"],
        "settlement_scope": snapshot["settlement_scope"],
        "observed_at_utc": snapshot["freeze_utc"],
        "provider_name": snapshot.get("provider_name"),
        "provider_group": "kambi",
        "parent_snapshot_path": str(snapshot_path.relative_to(ROOT)),
        "parent_snapshot_sha256": snapshot.get("raw_snapshot_sha256"),
        "parent_raw_evidence_path": str(raw_rel),
        "parent_raw_response_sha256": envelope_raw_sha,
        "parent_payload_reserialized_sha256_audit_only": hashlib.sha256(encoded_payload).hexdigest(),
        "asian_handicap_lines": ah_lines,
        "over_under_lines": ou_lines,
        "top_level_snapshot_lines": {
            "asian_handicap": snapshot.get("asian_handicap"),
            "over_under": snapshot.get("over_under"),
        },
        "selection_policy": "Keep every distinct directly quoted Full-Time quarter AH/OU line from the same immutable Kambi event-detail observation. If duplicate offers exist at one line, choose lowest absolute overround, then closest-to-balanced, then offer_id. No interpolation, no synthetic odds, no cross-provider surface splicing.",
        "promotion_semantics": {
            "standalone_promotion_sample_eligible": False,
            "may_support_exact_line_cross_provider_comparison": True,
            "requires_independent_provider_same_line_and_time_gate": True,
        },
        "formal_weight_change": False,
        "probability_change": False,
    }
    bundle["bundle_sha256"] = canonical_sha256(bundle)
    return bundle, bundle_path(snapshot)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-utc")
    args = parser.parse_args()
    since = dt(args.since_utc) if args.since_utc else None
    receipt = {
        "schema_version": "V5.5.28-kambi-multiline-bundle-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "since_utc": since.replace(microsecond=0).isoformat() if since else None,
        "status": "NO_BUNDLES_WRITTEN",
        "eligible_parent_snapshot_count": 0,
        "bundle_count_written": 0,
        "bundle_count_available": 0,
        "failed_count": 0,
        "results": [],
        "formal_weight_change": False,
        "probability_change": False,
        "promotion_sample_count_change": 0,
    }
    for path in SNAPSHOT_ROOT.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            if snapshot.get("provider_group") != "kambi" or snapshot.get("competition_id") not in ALLOWED_COMPETITIONS:
                continue
            observed = dt(str(snapshot.get("freeze_utc")))
            if since and observed < since:
                continue
            receipt["eligible_parent_snapshot_count"] += 1
            bundle, out = build_for_snapshot(snapshot, path)
            created = False
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("bundle_sha256") != bundle.get("bundle_sha256"):
                    raise FileExistsError(f"immutable multiline bundle collision: {out}")
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["bundle_count_written"] += 1
                created = True
            receipt["bundle_count_available"] += 1
            receipt["results"].append({
                "competition_id": bundle["competition_id"],
                "home_team": bundle["home_team"],
                "away_team": bundle["away_team"],
                "observed_at_utc": bundle["observed_at_utc"],
                "asian_handicap_line_count": len(bundle["asian_handicap_lines"]),
                "over_under_line_count": len(bundle["over_under_lines"]),
                "path": str(out.relative_to(ROOT)),
                "created": created,
            })
        except Exception as exc:
            receipt["failed_count"] += 1
            receipt["results"].append({"parent_path": str(path.relative_to(ROOT)), "status": "FAIL_CLOSED", "error": f"{type(exc).__name__}: {exc}"})
    if receipt["bundle_count_available"]:
        receipt["status"] = "PASS_KAMBI_MULTILINE_BUNDLES_AVAILABLE"
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
