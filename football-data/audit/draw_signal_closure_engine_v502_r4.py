#!/usr/bin/env python3
"""Registry-backed façade with reconstructed-schedule PIT boundaries for V5.0.2."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import sys
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import draw_signal_closure_engine_v502_r3 as base
from draw_signal_closure_engine_v502_r3 import *  # noqa: F401,F403

STRICT_PIT_CLASSIFICATION = "PIT_SAFE_PREDICTION_TIME"
RECONSTRUCTED_CLASSIFICATION = "PIT_RECONSTRUCTED_SCHEDULE_FIELD_RESEARCH_ONLY"
UNVERIFIED_PIT_CLASSIFICATION = "PIT_CONTRACT_UNVERIFIED"
RECONSTRUCTED_SCOPE = "DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_ONLY"
SOURCE_INDEX_SCHEMA = "DRAW-SIGNAL-ASSET-REGISTRY-V502-1.1"
SOURCE_INDEX_TREE_SHA256 = "b6169486c1d481b355993168673b4b0d91cffaa82c7ca36948c1d60456a8c137"
SOURCE_INDEX_ENTRY_COUNT = 1648

_BASE_CLASSIFY_COLUMN = base.classify_column
_BASE_ASSESS_FIELD_CANDIDATE = base.assess_field_candidate
_BASE_BUILD_AUDIT = base.build_audit

# Historical K-League results were downloaded after the matches and have no
# repository-wide observed_at/available_at chain or archived raw payload. The
# round label is therefore usable only as a reconstructed research feature.
base.PIT_FIELD_CONTRACTS = dict(base.PIT_FIELD_CONTRACTS)
base.PIT_FIELD_CONTRACTS["round"] = {
    "classification": RECONSTRUCTED_CLASSIFICATION,
    "source": "football-data/processed/KOR_KLeague1/official_results.csv",
    "source_semantics": "official schedule round label from kleague.com/getScheduleList.do",
    "scope": ["KOR_KLeague1"],
    "observed_at_contract": "repository-wide historical observed_at/available_at unavailable",
    "raw_payload_archived": False,
    "historical_revision_risk": "post-hoc unified download; schedule revisions cannot be reconstructed from archived raw responses",
    "forward_pit_proof_required": True,
    "formal_promotion_authorized": False,
}
PIT_FIELD_CONTRACTS = base.PIT_FIELD_CONTRACTS


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_pit_field_contract(root: Path, field: str, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a strict prediction-time field contract; any omission fails closed."""
    required = {
        "classification",
        "source",
        "source_sha256",
        "profile_manifest",
        "profile_manifest_sha256",
        "request_manifest",
        "request_manifest_sha256",
        "source_semantics",
        "scope",
        "prediction_time_availability",
        "historical_revision_risk",
        "raw_payload_archived",
    }
    missing = sorted(required - set(contract))
    result: dict[str, Any] = {
        "field": field,
        "verified": False,
        "strict_pit_verified": False,
        "classification": UNVERIFIED_PIT_CLASSIFICATION,
        "formal_promotion_authorized": False,
    }
    if missing:
        result["reason"] = f"strict PIT contract keys missing: {missing}"
        return result
    if str(contract.get("classification")) != STRICT_PIT_CLASSIFICATION:
        result["reason"] = "contract does not request strict PIT classification"
        return result
    if list(contract.get("scope") or []) != ["KOR_KLeague1"]:
        result["reason"] = "strict PIT scope must equal KOR_KLeague1 only"
        return result

    paths = {
        "source": root / str(contract["source"]),
        "profile_manifest": root / str(contract["profile_manifest"]),
        "request_manifest": root / str(contract["request_manifest"]),
    }
    for key, path in paths.items():
        if not path.is_file():
            result["reason"] = f"{key} file missing"
            return result
        expected = str(contract[f"{key}_sha256"]).lower()
        actual = _sha256_file(path)
        if actual != expected:
            result["reason"] = f"{key} SHA mismatch"
            result[f"{key}_actual_sha256"] = actual
            return result

    try:
        with paths["source"].open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            columns = [str(value).strip().lower() for value in (csv.DictReader(handle).fieldnames or [])]
        json.loads(paths["profile_manifest"].read_text(encoding="utf-8"))
        json.loads(paths["request_manifest"].read_text(encoding="utf-8"))
    except Exception as exc:
        result["reason"] = f"source/manifest parse failure: {type(exc).__name__}: {exc}"
        return result
    if field.strip().lower() not in columns:
        result["reason"] = "field is absent from contracted source CSV"
        return result

    availability = contract.get("prediction_time_availability")
    if not isinstance(availability, Mapping):
        result["reason"] = "prediction_time_availability must be a mapping"
        return result
    timestamp_proven = bool(availability.get("observed_at")) or bool(availability.get("available_at"))
    exception_proven = bool(availability.get("intrinsic_schedule_exception_approved")) and bool(
        availability.get("exception_policy_id")
    )
    if not (timestamp_proven or exception_proven):
        result.update(
            {
                "reason": "observed_at/available_at unavailable and no approved intrinsic-schedule exception",
                "classification": RECONSTRUCTED_CLASSIFICATION,
                "forward_pit_proof_required": True,
            }
        )
        return result
    if contract.get("raw_payload_archived") is not True:
        result.update(
            {
                "reason": "raw source payload is not archived",
                "classification": RECONSTRUCTED_CLASSIFICATION,
                "forward_pit_proof_required": True,
            }
        )
        return result

    result.update(
        {
            "verified": True,
            "strict_pit_verified": True,
            "classification": STRICT_PIT_CLASSIFICATION,
            "reason": "strict PIT field contract verified",
            "scope": ["KOR_KLeague1"],
            "formal_promotion_authorized": False,
        }
    )
    return result


def classify_column(
    column: str,
    all_columns: Iterable[str],
    pit_contracts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str, str]:
    token = column.strip().lower()
    contracts = PIT_FIELD_CONTRACTS if pit_contracts is None else pit_contracts
    if token in contracts:
        contract = contracts[token]
        requested = str(contract.get("classification") or "")
        if requested == RECONSTRUCTED_CLASSIFICATION:
            return RECONSTRUCTED_CLASSIFICATION, str(
                contract.get("source_semantics") or "historically reconstructed schedule field"
            )
        if requested == STRICT_PIT_CLASSIFICATION and contract.get("strict_pit_verified") is True:
            return STRICT_PIT_CLASSIFICATION, str(contract.get("source_semantics") or "verified strict PIT contract")
        return UNVERIFIED_PIT_CLASSIFICATION, "field name or declaration exists without a verified strict PIT contract"
    return _BASE_CLASSIFY_COLUMN(column, all_columns, pit_contracts)


def assess_field_candidate(
    field: str,
    stat: Mapping[str, Any],
    *,
    all_columns: Iterable[str],
    total_rows: int,
    total_competitions: int,
    dataflow_index: Mapping[str, Sequence[Mapping[str, Any]]],
    pit_contracts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    row = _BASE_ASSESS_FIELD_CANDIDATE(
        field,
        stat,
        all_columns=all_columns,
        total_rows=total_rows,
        total_competitions=total_competitions,
        dataflow_index=dataflow_index,
        pit_contracts=pit_contracts,
    )
    reconstructed = row.get("classification") == RECONSTRUCTED_CLASSIFICATION
    reconstructed_candidate = bool(
        reconstructed
        and row.get("substantive_predictive_candidate")
        and row.get("untested")
        and row.get("eligible_domain_specific_scopes")
        and not row.get("qualifies_existing_pit_safe_untested")
    )
    row["qualifies_domain_specific_reconstructed_research_candidate"] = reconstructed_candidate
    row["forward_pit_proof_required"] = reconstructed
    row["formal_promotion_authorized"] = False
    # Compatibility bridge only: r3 profile_fields gathers domain candidates by
    # this key. build_audit separates reconstructed candidates before writing.
    if reconstructed_candidate:
        row["qualifies_domain_specific_pit_safe_untested"] = True
    return row


def make_preregistration(
    global_candidates: Sequence[Mapping[str, Any]],
    domain_candidates: Sequence[Mapping[str, Any]],
    route_closure: Mapping[str, Any],
) -> dict[str, Any]:
    features = []
    reconstructed_present = False
    for row in global_candidates:
        features.append({"field": row["field"], "scope": "GLOBAL", "evidence_sha256": base.canonical_sha(row)})
    for row in domain_candidates:
        reconstructed = row.get("classification") == RECONSTRUCTED_CLASSIFICATION
        reconstructed_present = reconstructed_present or reconstructed
        feature = {
            "field": row["field"],
            "scope": RECONSTRUCTED_SCOPE if reconstructed else "DOMAIN_SPECIFIC",
            "eligible_domains": row["eligible_domain_specific_scopes"],
            "evidence_sha256": base.canonical_sha(row),
        }
        if reconstructed:
            feature.update(
                {
                    "forward_pit_proof_required": True,
                    "formal_promotion_authorized": False,
                    "historical_evidence_status": "RECONSTRUCTED_NOT_STRICT_PIT",
                }
            )
        features.append(feature)
    return {
        "status": "PRE_REGISTERED_NOT_RUN",
        "features": features,
        "model": "domain-scoped multinomial logistic challenger with nested time-order validation",
        "hyperparameter_grid": {"l2": [0.1, 1.0, 10.0], "class_weight_draw": [1.0, 1.15, 1.3]},
        "outer_folds": "expanding-window within each eligible domain and season",
        "inner_selection": "training-only rolling validation; no random split",
        "primary_metric": "paired draw_F1 and macro_F1 improvement under full-1X2 noninferiority",
        "promotion_gates": {
            "accuracy_delta_min": -0.005,
            "log_loss_delta_max": 0.005,
            "brier_delta_max": 0.005,
            "rps_delta_max": 0.003,
            "draw_f1_paired_bootstrap_lower_bound_min": 0.0,
            "macro_f1_paired_bootstrap_lower_bound_min": 0.0,
            "minimum_draw_predictions_per_fold": 20,
        },
        "holdout_policy": "latest eligible chronological domain segment must be proven untouched before any run",
        "holdout_status": "NOT_YET_PROVEN_UNTOUCHED",
        "route_closure_blockers": [row["id"] for row in route_closure.get("unresolved", [])],
        "formal_weight": 0,
        "run_authorized": False,
        "formal_promotion_authorized": False,
        "forward_pit_proof_required": reconstructed_present,
    }


def decide_and_preregister(
    global_candidates: Sequence[Mapping[str, Any]],
    domain_candidates: Sequence[Mapping[str, Any]],
    route_closure: Mapping[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if global_candidates or domain_candidates:
        return base.POSITIVE_DECISION, make_preregistration(global_candidates, domain_candidates, route_closure)
    if bool(route_closure.get("blocks_exhausted")):
        return base.UNRESOLVED_DECISION, None
    return base.NEGATIVE_DECISION, None


def load_expected_asset_registry(root: Path, registry_path: Path | None = None) -> dict[str, Any]:
    path = registry_path or (root / "football-data" / "audit" / "draw_signal_asset_registry_v502.b85")
    registry_text = zlib.decompress(base64.b85decode(path.read_bytes())).decode("utf-8")
    entries = []
    metadata: dict[str, Any] = {"schema_version": SOURCE_INDEX_SCHEMA}
    for raw_line in registry_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "=" in line:
                key, value = line[1:].split("=", 1)
                metadata[key.strip()] = value.strip()
            continue
        flag, rel = line.split("\t", 1)
        if flag not in {"I", "E"}:
            raise ValueError(f"invalid registry flag: {flag}")
        entries.append({"path": rel, "expected_included": flag == "I"})
    if not entries:
        raise ValueError("expected asset registry entries missing")
    legacy_source_head = metadata.pop("source_head", None)
    provenance_path = path.with_name("draw_signal_asset_registry_v502.provenance.json")
    if not provenance_path.is_file():
        raise ValueError("registry provenance sidecar missing")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    metadata.update(provenance)
    metadata["legacy_source_head_status"] = (
        "SUPERSEDED_BY_SOURCE_INDEX_TREE_SHA256" if legacy_source_head else "NOT_PRESENT"
    )
    index_text = "".join(
        f"{'I' if row['expected_included'] else 'E'}\t{row['path']}\n"
        for row in sorted(entries, key=lambda value: str(value["path"]))
    )
    actual_index_sha = hashlib.sha256(index_text.encode("utf-8")).hexdigest()
    expected_index_sha = str(metadata.get("source_index_tree_sha256") or "")
    if expected_index_sha != SOURCE_INDEX_TREE_SHA256 or actual_index_sha != expected_index_sha:
        raise ValueError("source index tree SHA mismatch")
    if int(metadata.get("source_index_entry_count") or -1) != SOURCE_INDEX_ENTRY_COUNT or len(entries) != SOURCE_INDEX_ENTRY_COUNT:
        raise ValueError("source index entry count mismatch")
    metadata["entries"] = entries
    metadata["source_index_tree_sha256_verified"] = True
    return metadata


def build_audit(root: Path) -> dict[str, Any]:
    audit = _BASE_BUILD_AUDIT(root)
    feature = audit["feature_difference"]
    gathered = list(feature.get("DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES", []))
    reconstructed = [row for row in gathered if row.get("classification") == RECONSTRUCTED_CLASSIFICATION]
    strict = [row for row in gathered if row.get("classification") == STRICT_PIT_CLASSIFICATION]
    for row in reconstructed:
        row["qualifies_domain_specific_pit_safe_untested"] = False
        row["qualifies_domain_specific_reconstructed_research_candidate"] = True
    feature["DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES"] = strict
    feature["DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES"] = reconstructed
    all_domain = strict + reconstructed
    decision, preregistration = decide_and_preregister(
        feature.get("EXISTING_PIT_SAFE_UNTESTED_FEATURES", []),
        all_domain,
        audit["experiment_route_closure"],
    )
    audit["schema_version"] = "DRAW-SIGNAL-CLOSURE-AUDIT-V502-3.1"
    audit["decision"] = decision
    audit["preregistration"] = preregistration
    audit["strict_pit_contract_status"] = {
        "round": "RECONSTRUCTED_RESEARCH_ONLY_FORWARD_PIT_PROOF_REQUIRED",
        "formal_promotion_authorized": False,
    }
    registry = load_expected_asset_registry(root)
    audit["research_asset_registry_provenance"] = {
        "schema_version": registry.get("schema_version"),
        "source_index_tree_sha256": registry.get("source_index_tree_sha256"),
        "source_index_tree_sha256_verified": registry.get("source_index_tree_sha256_verified"),
        "source_index_entry_count": int(registry.get("source_index_entry_count", -1)),
        "generated_from_exact_head": registry.get("generated_from_exact_head"),
        "source_head_present": "source_head" in registry,
    }
    audit.pop("audit_sha256", None)
    audit["audit_sha256"] = base.canonical_sha(audit)
    return audit


def _markdown_features(feature: Mapping[str, Any], decision: str) -> str:
    strict = feature.get("DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES", [])
    reconstructed = feature.get("DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES", [])
    near = feature.get("UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED", [])
    lines = ["# 现有字段差集与域内候选", "", f"裁决：`{decision}`", ""]
    lines.append(
        f"全局严格PIT候选：{len(feature.get('EXISTING_PIT_SAFE_UNTESTED_FEATURES', []))}；"
        f"域内严格PIT候选：{len(strict)}；历史重建研究候选：{len(reconstructed)}；near-miss：{len(near)}。"
    )
    lines += ["", "## DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES", ""]
    for row in reconstructed:
        domains = ", ".join(
            f"{item['competition']} rows={item['rows']} seasons={item['season_count']} coverage={item['coverage']:.2%}"
            for item in row["eligible_domain_specific_scopes"]
        )
        lines.append(f"- `{row['field']}`：{domains}；forward_pit_proof_required=true；formal_promotion_authorized=false")
    lines += ["", "## Near-miss", ""]
    for row in near:
        lines.append(
            f"- `{row['field']}`：{row['classification']}；global={row['global_row_coverage']:.2%}；domains={row['competition_count']}"
        )
    return "\n".join(lines) + "\n"


# Patch r3 globals so its build/write helpers use the corrected contracts and gates.
base.classify_column = classify_column
base.assess_field_candidate = assess_field_candidate
base.make_preregistration = make_preregistration
base.decide_and_preregister = decide_and_preregister
base.load_expected_asset_registry = load_expected_asset_registry
base.build_audit = build_audit
base._markdown_features = _markdown_features


def write_audit(out: Path, audit: Mapping[str, Any]) -> None:
    return base.write_audit(out, audit)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(base.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    audit = build_audit(root)
    write_audit(args.output_dir, audit)
    feature = audit["feature_difference"]
    print(
        json.dumps(
            {
                "head": audit["head"],
                "experiments": audit["experiment_count"],
                "actual_assets": audit["complete_research_file_count"],
                "global_strict_pit_candidates": len(feature.get("EXISTING_PIT_SAFE_UNTESTED_FEATURES", [])),
                "domain_strict_pit_candidates": len(feature.get("DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES", [])),
                "domain_reconstructed_candidates": len(feature.get("DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES", [])),
                "near_miss": len(feature.get("UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED", [])),
                "unresolved_routes": len(audit["experiment_route_closure"]["unresolved"]),
                "decision": audit["decision"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
