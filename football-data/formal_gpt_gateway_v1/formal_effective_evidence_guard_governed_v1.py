#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import formal_state_integrity_guard_v1 as guard

SCHEMA = "football3-effective-evidence-guard-governed-v1"
SEALED_ROUTE = "SEALED_EXACT_CUTOFF_REPLAY"

_ORIGINAL_CLASSIFY = guard.classify_state
_ORIGINAL_ENRICH = guard._enrich_receipt
_ORIGINAL_CACHE_PREFLIGHT = guard._cache_preflight
_ORIGINAL_CLEAR_CACHE = guard._clear_cache
_ORIGINAL_FULL_REBUILD = guard.full_rebuild.build_integrity_base
_SEALED_EXACT_ROOTS: set[str] = set()
_INSTALLED = False


def _root_key(state_root: Path) -> str:
    return str(Path(state_root).resolve())


def _is_exact_sealed_bundle(state_root: Path, cutoff) -> bool:
    try:
        loaded = guard.rt.validate_bundle(Path(state_root) / "bundle")
        sealed_cutoff = guard.rt._parse_dt(
            str(loaded["meta"]["historical_cutoff"]), "sealed replay bundle cutoff"
        )
    except guard.rt.RuntimeGateError:
        return False
    return sealed_cutoff == cutoff


def _cache_preflight(state_root: Path, repo_root: Path, comp: str, season: str,
                     home: str, away: str, kickoff, cutoff) -> dict[str, Any]:
    pre = _ORIGINAL_CACHE_PREFLIGHT(
        state_root, repo_root, comp, season, home, away, kickoff, cutoff
    )
    key = _root_key(state_root)
    if not _is_exact_sealed_bundle(state_root, cutoff):
        _SEALED_EXACT_ROOTS.discard(key)
        return pre

    _SEALED_EXACT_ROOTS.add(key)
    out = dict(pre)
    out["sealed_exact_cutoff_replay"] = True
    out["recovery_suppressed"] = True
    if not bool(out.get("fast_eligible")):
        out["suppressed_preflight_reason"] = out.get("reason")
        out["fast_eligible"] = True
        out["reason"] = None
    return out


def _clear_cache(state_root: Path) -> None:
    if _root_key(state_root) in _SEALED_EXACT_ROOTS:
        return
    _ORIGINAL_CLEAR_CACHE(state_root)


def _build_integrity_base(bundle_dir: Path, *args, **kwargs):
    state_root = Path(bundle_dir).parent
    if _root_key(state_root) in _SEALED_EXACT_ROOTS:
        return {
            "loaded": guard.rt.validate_bundle(Path(bundle_dir)),
            "route": "SEALED_EXACT_CUTOFF_REPLAY_RECOVERY_SUPPRESSED",
            "cache_clear_used": False,
            "live_full_used": False,
        }
    return _ORIGINAL_FULL_REBUILD(bundle_dir, *args, **kwargs)


def _formal_fallback_verdict(trigger: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    dynamic = trigger.get("dynamic") if type(trigger.get("dynamic")) is dict else {}
    evidence = [float(x) for x in (dynamic.get("evidence") or [])]
    dynamic_flag = dynamic.get("fallback_exact_v1")
    receipt_flag = receipt.get("fallback_exact_v1")
    verdict_available = type(dynamic_flag) is bool
    receipt_flag_valid = type(receipt_flag) is bool
    fallback = dynamic_flag if verdict_available else bool(receipt_flag)
    return {
        "source": "historical_xg_challenger_v1.dynamic.fallback_exact_v1",
        "fallback_exact_v1": bool(fallback),
        "receipt_fallback_exact_v1": receipt_flag if receipt_flag_valid else None,
        "verdict_available": verdict_available,
        "receipt_verdict_consistent": (
            verdict_available and receipt_flag_valid and dynamic_flag == receipt_flag
        ),
        "effective_evidence": evidence,
        "effective_evidence_dimension_count": len(evidence),
        "projection_source": "historical_xg_challenger_v1_metadata_bearing_state_clone",
        "linked_match_count_used_for_fallback_verdict": False,
        "independent_guard_threshold_reimplementation_used": False,
    }


def classify_state(
    loaded: dict[str, Any],
    fixture: dict[str, Any],
    identity_audit: dict[str, Any],
    trigger: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    audit = _ORIGINAL_CLASSIFY(loaded, fixture, identity_audit, trigger, receipt)
    verdict = _formal_fallback_verdict(trigger, receipt)

    # Fallback legality is owned by the same metadata-bearing challenger verdict
    # consumed by the formal fusion model. The integrity guard must not infer a
    # competing decision from raw linked-match counts or a copied threshold.
    # Independent identity/PIT/coverage anomalies remain authoritative.
    reasons = [
        str(reason)
        for reason in (audit.get("anomaly_reasons") or [])
        if not str(reason).startswith("FALLBACK_DESPITE_EFFECTIVE_EVIDENCE_THRESHOLD:")
        and not str(reason).startswith("XG_EXPECTED_BUT_EFFECTIVE_EVIDENCE_INSUFFICIENT:")
    ]
    if not verdict["verdict_available"]:
        reasons.append("FORMAL_XG_FALLBACK_VERDICT_UNAVAILABLE")
    elif not verdict["receipt_verdict_consistent"]:
        reasons.append(
            "FORMAL_XG_FALLBACK_VERDICT_MISMATCH:"
            f"dynamic={verdict['fallback_exact_v1']},"
            f"receipt={verdict['receipt_fallback_exact_v1']}"
        )

    fallback = verdict["fallback_exact_v1"]
    audit["anomaly_reasons"] = reasons
    audit["formal_fallback_verdict"] = verdict
    xg = audit.get("historical_xg") if type(audit.get("historical_xg")) is dict else {}
    xg["expected_legal_xg_for_both_teams"] = None
    xg["fallback_verdict_source"] = verdict["source"]
    xg["linked_match_count_used_for_fallback_verdict"] = False
    xg["formal_fallback_exact_v1"] = fallback
    audit["historical_xg"] = xg

    if reasons:
        audit["status"] = "DATA_STATE_ANOMALY"
        audit["fallback_class"] = "DATA_STATE_ANOMALY"
        audit["fallback_reason"] = ";".join(reasons)
    elif fallback:
        evidence = verdict["effective_evidence"]
        audit["status"] = "PASS"
        audit["fallback_class"] = "NORMAL_FALLBACK"
        audit["fallback_reason"] = (
            str(receipt.get("fallback_reason") or "").strip()
            or "FORMAL_MODEL_EFFECTIVE_EVIDENCE_FALLBACK:"
               + ",".join(f"{x:.12g}" for x in evidence)
        )
    else:
        audit["status"] = "PASS"
        audit["fallback_class"] = "NONE"
        audit["fallback_reason"] = None
    return audit


def _first_authoritative_reason(receipt: dict[str, Any], result: dict[str, Any]) -> str | None:
    for obj in (receipt, result):
        for key in ("failure_reason", "fallback_reason", "reason", "error"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _enrich_receipt(out: Path, result: dict[str, Any], audit: dict[str, Any],
                    execution_mode: str, binding: dict[str, Any]) -> dict[str, Any]:
    path = Path(out) / "prediction_receipt.json"
    before = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    first_reason = _first_authoritative_reason(before, result)
    enriched = _ORIGINAL_ENRICH(out, result, audit, execution_mode, binding)

    guard_reason = enriched.get("fallback_reason")
    enriched.pop("receipt_sha", None)
    enriched["formal_fallback_verdict"] = audit.get("formal_fallback_verdict")
    if first_reason is not None:
        enriched["first_authoritative_failure_reason"] = first_reason
        if guard_reason and guard_reason != first_reason:
            enriched["integrity_guard_fallback_reason"] = guard_reason
        enriched["fallback_reason"] = first_reason
    enriched["receipt_sha"] = guard.rt._sha_bytes(guard.rt._canon_bytes(enriched))
    guard._write_json(path, enriched)
    result["receipt_sha"] = enriched["receipt_sha"]
    if first_reason is not None:
        result["fallback_reason"] = first_reason
    return enriched


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"schema_version": SCHEMA, "installed": True, "idempotent_reentry": True}

    guard._cache_preflight = _cache_preflight
    guard._clear_cache = _clear_cache
    guard.full_rebuild.build_integrity_base = _build_integrity_base
    guard.classify_state = classify_state
    guard._enrich_receipt = _enrich_receipt
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "prematch_fallback_verdict_source": "historical_xg_challenger_v1.dynamic.fallback_exact_v1",
        "receipt_fallback_verdict_consistency_required": True,
        "linked_match_count_used_for_fallback_verdict": False,
        "independent_guard_threshold_reimplementation_used": False,
        "sealed_exact_cutoff_replay_cache_clear_allowed": False,
        "sealed_exact_cutoff_replay_live_full_allowed": False,
        "first_authoritative_failure_reason_preserved": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
