#!/usr/bin/env python3
"""V5.0.3 independent-domain replication of the frozen player-XI signal.

Discovery was completed on ESP_LaLiga and GER_Bundesliga. Before acquiring or
examining the replication-domain results, ENG_PremierLeague, ITA_SerieA and
FRA_Ligue1 were frozen in transfermarkt_lineup_map_v502.json as independent
replication domains. This script reuses the exact V5.0.2 profiles, sample gates,
chronological logic and bootstrap thresholds without modification.

Domain results remain formal_weight=0. A signal pass only permits later
competition-specific unified-matrix projection research. Complete PIT handicap
and availability evidence remain separate mandatory gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for path in (VALIDATION, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from platform_core import atomic_write_json, load_json, sha256_file  # noqa: E402
from player_xi_residual_signal_oof_v502 import PROFILES, validate_domain  # noqa: E402

MAP_PATH = ROOT / "config" / "transfermarkt_lineup_map_v502.json"
DISCOVERY_SCRIPT = ROOT / "validation" / "player_xi_residual_signal_oof_v502.py"
DISCOVERY_RECEIPT = ROOT / "manifests" / "player_xi_residual_signal_oof_v502_status.json"
OUT = ROOT / "manifests" / "player_xi_residual_replication_v503_status.json"
REPORT_DIR = ROOT / "manifests" / "player_xi_residual_replication_v503"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_profile_hash() -> str:
    payload = json.dumps(PROFILES, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run(*, write: bool) -> dict[str, Any]:
    config = load_json(MAP_PATH)
    domains = [str(item) for item in config.get("independent_replication_domains", [])]
    if domains != ["ENG_PremierLeague", "ITA_SerieA", "FRA_Ligue1"]:
        raise RuntimeError(f"replication domains changed after freeze: {domains}")
    replication_policy = config.get("replication_policy") or {}
    if not bool(replication_policy.get("profiles_frozen_before_replication")):
        raise RuntimeError("profiles were not formally frozen before replication")
    if not bool(replication_policy.get("no_threshold_relaxation")):
        raise RuntimeError("threshold relaxation is prohibited")
    if not DISCOVERY_RECEIPT.is_file():
        raise RuntimeError("discovery receipt missing")

    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for competition_id in domains:
        try:
            report = validate_domain(competition_id)
            reports[competition_id] = report
            if write:
                atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
        except Exception as exc:
            failures[competition_id] = str(exc)

    signal_pass = [
        competition_id
        for competition_id, report in reports.items()
        if report.get("status") == "PLAYER_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW"
    ]
    rejected = [
        competition_id
        for competition_id, report in reports.items()
        if report.get("status") == "REJECT_KEEP_FORMAL_WEIGHT_0"
    ]
    domain_pass_count = len(signal_pass)
    replication_interpretation = (
        "MULTI_DOMAIN_SIGNAL_REPLICATED_MATRIX_REVIEW_ALLOWED_PER_PASSING_DOMAIN"
        if domain_pass_count >= 2
        else "REPLICATION_INSUFFICIENT_KEEP_GLOBAL_PLAYER_XI_WEIGHT_0"
    )
    manifest = {
        "schema_version": "V5.0.3-player-xi-residual-independent-replication-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if not failures and len(reports) == len(domains) else "FAIL",
        "discovery_domains": ["ESP_LaLiga", "GER_Bundesliga"],
        "replication_domains_frozen_before_results": domains,
        "completed_domains": sorted(reports),
        "signal_pass_domains": sorted(signal_pass),
        "rejected_keep_formal_weight_0": sorted(rejected),
        "execution_failures": failures,
        "replication_interpretation": replication_interpretation,
        "frozen_implementation": {
            "profiles_sha256": canonical_profile_hash(),
            "discovery_script_path": DISCOVERY_SCRIPT.relative_to(ROOT.parent).as_posix(),
            "discovery_script_sha256": sha256_file(DISCOVERY_SCRIPT),
            "discovery_receipt_path": DISCOVERY_RECEIPT.relative_to(ROOT.parent).as_posix(),
            "discovery_receipt_sha256": sha256_file(DISCOVERY_RECEIPT),
            "profile_count": len(PROFILES),
            "threshold_or_profile_change": False,
        },
        "reports": {
            competition_id: {
                "status": report["status"],
                "outer_prediction_count": report["outer_prediction_count"],
                "selected_profiles": report["selected_profiles"],
                "pooled_metrics": report["pooled_metrics"],
                "paired_block_bootstrap": report["paired_block_bootstrap"],
                "checks": report["checks"],
            }
            for competition_id, report in reports.items()
        },
        "availability_evidence_status": "UNAVAILABLE_NOT_USED",
        "handicap_evidence_status": "UNAVAILABLE_NO_COMPLETE_POINT_IN_TIME_FROZEN_HANDICAP_LINES_IN_CURRENT_REPLAY",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Independent replication only. Passing domains may enter later matrix-projection research; no formal probability influence or global cross-domain promotion is authorized.",
    }
    if write:
        atomic_write_json(OUT, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    manifest = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
