#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "active_domain_pit_v5532_status.json"
FILES = {
    "identity": ROOT / "config" / "active_domain_identity_registry_v5532.json",
    "marathon": ROOT / "manifests" / "marathonbet_active_domain_capture_v5532_status.json",
    "kambi": ROOT / "manifests" / "kambi_active_domain_capture_v5532_status.json",
    "postprocess": ROOT / "manifests" / "active_domain_market_postprocess_v5532_status.json",
}
ACTIVE = {
    "USA_MLS",
    "BRA_SerieA",
    "ARG_Primera",
    "SWE_Allsvenskan",
    "NOR_Eliteserien",
    "KOR_KLeague1",
}


def load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    data = {name: load(path) for name, path in FILES.items()}
    identity = data["identity"]
    marathon = data["marathon"]
    kambi = data["kambi"]
    post = data["postprocess"]

    hard_errors = []
    if identity.get("schema_version") != "V5.5.32-active-domain-observed-identity-r1":
        hard_errors.append("IDENTITY_SCHEMA_MISMATCH")
    if int(identity.get("available_competition_count", 0)) <= 0:
        hard_errors.append("NO_ACTIVE_IDENTITY_DOMAIN")
    if marathon.get("provider_group") != "marathonbet":
        hard_errors.append("MARATHON_PROVIDER_GROUP_MISMATCH")
    if kambi.get("provider_group") != "kambi":
        hard_errors.append("KAMBI_PROVIDER_GROUP_MISMATCH")
    if kambi.get("identity_crosscheck_only_no_market_splicing") is not True:
        hard_errors.append("KAMBI_MARKET_SPLICING_GUARD_MISSING")
    for label, row in data.items():
        if row.get("formal_weight_change") is not False or row.get("probability_change") is not False:
            hard_errors.append(f"FORMAL_MODEL_MUTATION:{label}")
    if post.get("formal_model_promotion") is not False:
        hard_errors.append("POSTPROCESS_SELF_PROMOTION")

    event_counts = Counter(str(row.get("status") or "UNKNOWN") for row in (kambi.get("events") or []))
    identity_status = {
        cid: ((identity.get("competitions") or {}).get(cid) or {}).get("status")
        for cid in sorted(ACTIVE)
    }
    marathon_status = {
        row.get("competition_id"): row.get("status")
        for row in (marathon.get("leagues") or [])
        if row.get("competition_id") in ACTIVE
    }

    marathon_available = int(marathon.get("formal_snapshot_count_available", 0))
    kambi_available = int(kambi.get("formal_snapshot_count_available", 0))
    consensus_available = int(post.get("deduplicated_active_domain_observation_pair_count", 0))

    if hard_errors:
        status = "FAIL"
    elif consensus_available:
        status = "PASS_SYNCHRONIZED_ACTIVE_DOMAIN_EVIDENCE"
    elif marathon_available or kambi.get("target_group_event_count", 0):
        status = "PASS_PIPELINE_EXECUTED_NO_SYNCHRONIZED_PAIR"
    else:
        status = "PASS_PIPELINE_EXECUTED_NO_PROVIDER_OVERLAP"

    receipt = {
        "schema_version": "V5.5.32-active-domain-pit-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "hard_error_count": len(hard_errors),
        "hard_errors": hard_errors,
        "active_competitions": sorted(ACTIVE),
        "identity_available_competition_count": identity.get("available_competition_count", 0),
        "identity_status_by_competition": identity_status,
        "marathon_status_by_competition": marathon_status,
        "marathon_formal_snapshot_count_available": marathon_available,
        "kambi_target_group_event_count": kambi.get("target_group_event_count", 0),
        "kambi_crosschecked_event_count": kambi.get("crosschecked_event_count", 0),
        "kambi_formal_snapshot_count_available": kambi_available,
        "kambi_event_status_counts": dict(event_counts),
        "exact_line_active_domain_observation_pair_count": consensus_available,
        "formal_model_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "audit": {
            "single_provider_market_splicing_used": False,
            "fuzzy_identity_authorized": False,
            "historical_identity_fallback_authorized": False,
            "zero_sample_treated_as_model_success": False,
            "zero_sample_treated_as_data_availability_state": True,
        },
        "policy": (
            "PASS means the collection and governance chain executed without fabrication or formal-model mutation. "
            "It does not mean market evidence or a model promotion exists. Only deduplicated synchronized two-provider observations count as promotion evidence."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "hard_error_count": len(hard_errors),
        "marathon_available": marathon_available,
        "kambi_available": kambi_available,
        "active_consensus_pairs": consensus_available,
    }, ensure_ascii=False, indent=2))
    return 0 if not hard_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
