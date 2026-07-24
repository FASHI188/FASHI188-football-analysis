#!/usr/bin/env python3
"""V6.18.8f immutable Understat alias freeze.

Governance-only. Reads the PASS V6.18.8 identity-closure receipt and freezes the exact
qualified platform-token -> Understat-token mappings before any xG P(T) or P(D|T,X)
model is allowed to run.

The frozen asset is deterministic: no current web fetch and no current timestamp inside
the asset. Reruns must reproduce byte-identical content. If an existing asset differs,
fail closed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manifests" / "v6_understat_alias_closure_v6188_status.json"
SOURCE_CODE = ROOT / "validation" / "v6_understat_alias_closure_v6188.py"
ASSET = ROOT / "models" / "challengers_v6188" / "understat_aliases_v6188.json"
OUT = ROOT / "manifests" / "v6_understat_alias_freeze_v6188f_status.json"
EXPECTED_SCHEMA = "V6.18.8-understat-iterative-schedule-anchor-alias-closure-r1"
MIN_RATE = 0.90


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_bytes(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit("V6.18.8 receipt missing")
    source_raw = SOURCE.read_bytes()
    source = json.loads(source_raw.decode("utf-8"))
    if source.get("schema_version") != EXPECTED_SCHEMA or source.get("status") != "PASS":
        raise SystemExit(f"V6.18.8 PASS receipt required: {source.get('schema_version')} {source.get('status')}")
    gate = source.get("xg_research_coverage_gate") or {}
    if gate.get("pass") is not True:
        raise SystemExit("V6.18.8 xG research coverage gate is not PASS")
    domain_rates = source.get("domain_rates") or {}
    if not domain_rates or any(float(v) < MIN_RATE for v in domain_rates.values()):
        raise SystemExit(f"domain coverage below {MIN_RATE}: {domain_rates}")

    frozen_domains = {}
    reverse_checks = {}
    total = 0
    for cid in sorted((source.get("domains") or {}).keys()):
        d = source["domains"][cid]
        aliases = d.get("qualified_aliases") or {}
        mapping = {}
        reverse = {}
        for ptoken in sorted(aliases):
            rec = aliases[ptoken]
            utoken = str(rec.get("understat_token") or "")
            if not utoken or ptoken == utoken:
                raise SystemExit(f"invalid alias {cid}: {ptoken}->{utoken}")
            if utoken in reverse and reverse[utoken] != ptoken:
                raise SystemExit(f"reverse alias conflict {cid}: {utoken}")
            evidence = rec.get("evidence") or []
            evidence_digest = sha256_bytes(canonical_bytes(evidence))
            mapping[ptoken] = {
                "understat_token": utoken,
                "qualification": rec.get("qualification"),
                "evidence_type": rec.get("evidence_type"),
                "observations": int(rec.get("observations") or 0),
                "token_similarity": rec.get("token_similarity"),
                "evidence_sha256": evidence_digest,
            }
            reverse[utoken] = ptoken
        declared = int(d.get("qualified_alias_count") or 0)
        if declared != len(mapping):
            raise SystemExit(f"alias-count mismatch {cid}: declared={declared} actual={len(mapping)}")
        frozen_domains[cid] = {
            "coverage_rate_at_freeze": float((d.get("post_alias_aggregate") or {}).get("state_attach_rate")),
            "alias_count": len(mapping),
            "aliases": mapping,
        }
        reverse_checks[cid] = len(reverse)
        total += len(mapping)

    declared_total = int(source.get("qualified_alias_count") or 0)
    if total != declared_total:
        raise SystemExit(f"total alias-count mismatch declared={declared_total} actual={total}")

    asset = {
        "schema_version": "V6.18.8f-understat-alias-freeze-r1",
        "formal_current_version": "V5.0.1",
        "source_schema_version": EXPECTED_SCHEMA,
        "source_generated_at_utc": source.get("generated_at_utc"),
        "source_receipt_sha256": sha256_bytes(source_raw),
        "source_code_sha256": sha256_file(SOURCE_CODE),
        "coverage_gate_at_freeze": {
            "threshold": MIN_RATE,
            "aggregate_rate": float((source.get("aggregate") or {}).get("state_attach_rate")),
            "domain_rates": {k: float(v) for k, v in sorted(domain_rates.items())},
            "pass": True,
        },
        "qualified_alias_count": total,
        "domains": frozen_domains,
        "governance": {
            "immutable": True,
            "one_to_one_per_domain": True,
            "fuzzy_training_rows": False,
            "web_refetch_on_model_run": False,
            "model_may_only_use_exact_fixture_identity_after_alias_mapping": True,
        },
    }
    asset_bytes = canonical_bytes(asset)
    ASSET.parent.mkdir(parents=True, exist_ok=True)
    if ASSET.exists() and ASSET.read_bytes() != asset_bytes:
        raise SystemExit("existing V6.18.8f alias asset drift detected; fail closed")
    ASSET.write_bytes(asset_bytes)

    receipt = {
        "schema_version": "V6.18.8f-understat-alias-freeze-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "source_receipt_sha256": asset["source_receipt_sha256"],
        "source_code_sha256": asset["source_code_sha256"],
        "alias_asset_path": str(ASSET.relative_to(ROOT)),
        "alias_asset_sha256": sha256_bytes(asset_bytes),
        "qualified_alias_count": total,
        "domain_alias_counts": {cid: d["alias_count"] for cid, d in frozen_domains.items()},
        "domain_rates": asset["coverage_gate_at_freeze"]["domain_rates"],
        "aggregate_rate": asset["coverage_gate_at_freeze"]["aggregate_rate"],
        "reverse_one_to_one_counts": reverse_checks,
        "xg_model_research_identity_gate": "PASS",
        "allowed_next_step": "strict-PIT xG incremental challengers for direct P(T) and conditional P(D|T,X); formal_weight remains 0",
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "alias_changes_after_freeze_forbidden_without_new_version": True,
            "fuzzy_training_rows_forbidden": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
