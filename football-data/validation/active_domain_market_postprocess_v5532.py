#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import exact_line_aligned_consensus_v5529 as exact_line
import kambi_multiline_bundle_v5528 as multiline
import promotion_consensus_registry_v5530 as registry

OUT = ROOT / "manifests" / "active_domain_market_postprocess_v5532_status.json"
ACTIVE = {
    "USA_MLS",
    "BRA_SerieA",
    "ARG_Primera",
    "SWE_Allsvenskan",
    "NOR_Eliteserien",
    "KOR_KLeague1",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def call_main(module, argv: list[str]) -> int:
    previous = sys.argv[:]
    try:
        sys.argv = [module.__file__ or module.__name__, *argv]
        return int(module.main())
    finally:
        sys.argv = previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-utc", required=True)
    args = parser.parse_args()

    multiline.ALLOWED_COMPETITIONS = set(multiline.ALLOWED_COMPETITIONS) | ACTIVE
    exact_line.ALLOWED_COMPETITIONS = set(exact_line.ALLOWED_COMPETITIONS) | ACTIVE
    registry.ALLOWED = set(registry.ALLOWED) | ACTIVE

    results = {}
    results["multiline_returncode"] = call_main(multiline, ["--since-utc", args.since_utc])
    if results["multiline_returncode"] != 0:
        raise SystemExit(results["multiline_returncode"])
    results["exact_line_returncode"] = call_main(exact_line, ["--since-utc", args.since_utc])
    if results["exact_line_returncode"] != 0:
        raise SystemExit(results["exact_line_returncode"])
    results["registry_returncode"] = call_main(registry, [])
    if results["registry_returncode"] != 0:
        raise SystemExit(results["registry_returncode"])

    bundle = load(ROOT / "manifests" / "kambi_multiline_bundle_v5528_status.json")
    consensus = load(ROOT / "manifests" / "exact_line_aligned_consensus_v5529_status.json")
    promotion = load(ROOT / "manifests" / "promotion_consensus_registry_v5530_status.json")

    for label, row in (("bundle", bundle), ("consensus", consensus), ("promotion", promotion)):
        if row.get("formal_weight_change") is not False or row.get("probability_change") is not False:
            raise SystemExit(f"{label} changed formal model")
    if bundle.get("promotion_sample_count_change") not in (0, None):
        raise SystemExit("multiline bundle changed promotion samples")
    if consensus.get("formal_model_promotion") is not False:
        raise SystemExit("exact-line evidence self-promoted")
    if promotion.get("formal_model_promotion") is not False:
        raise SystemExit("registry self-promoted")

    active_samples = [
        row for row in (promotion.get("samples") or [])
        if row.get("competition_id") in ACTIVE
    ]
    receipt = {
        "schema_version": "V5.5.32-active-domain-market-postprocess-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "since_utc": args.since_utc,
        "status": "PASS_ACTIVE_DOMAIN_PROMOTION_EVIDENCE_AVAILABLE" if active_samples else "PASS_NO_ACTIVE_DOMAIN_PROMOTION_EVIDENCE",
        "active_competitions": sorted(ACTIVE),
        "kambi_multiline_bundle_count_available": bundle.get("bundle_count_available", 0),
        "exact_line_shared_fixture_count": consensus.get("shared_fixture_count", 0),
        "exact_line_promotion_evidence_eligible_count": consensus.get("promotion_evidence_eligible_count", 0),
        "deduplicated_all_domain_observation_pair_count": promotion.get("unique_observation_pair_count", 0),
        "deduplicated_active_domain_observation_pair_count": len(active_samples),
        "active_domain_samples": active_samples,
        "formal_model_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": (
            "V5.5.32 only broadens the existing immutable multiline, exact-line and de-duplication machinery to registered active domains. "
            "It does not relax the two-provider, <=300-second, exact AH/OU line, complete 1X2/AH/OU or no-synthesis gates."
        ),
        "execution": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "active_domain_observation_pair_count": len(active_samples),
        "bundle_count_available": receipt["kambi_multiline_bundle_count_available"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
