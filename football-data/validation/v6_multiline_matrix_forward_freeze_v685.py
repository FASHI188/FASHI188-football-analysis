#!/usr/bin/env python3
"""V6.8.5 immutable prospective multiline-matrix freeze builder.

Consumes only already-validated formal prediction freezes produced by match_pipeline.freeze_prediction.
For each post-epoch formal freeze, it locates the nearest Kambi full-time ladder observed no later
than the formal freeze, projects the *stored final formal matrix* with the frozen V6.8.2 algorithm,
and stores both matrices plus evidence hashes in an immutable research sidecar.

No historical backfill. No post-match reprojection. No formal/runtime probability mutation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from platform_core import load_json, normalize_team_token, parse_iso_datetime, sha256_json
from v6_multiline_market_matrix_projection_v682 import project

CFG = ROOT / "config" / "v6_multiline_matrix_forward_v685.json"
FORMAL_FREEZES = ROOT / "prediction_freezes"
MARKETS = ROOT / "evidence" / "markets_prospective"
LADDER_FILE = ROOT / "evidence" / "market_ladders_v680" / "kambi_full_time_ladders.json"
CONSENSUS = ROOT / "evidence" / "market_consensus_prospective"
OUT_ROOT = ROOT / "forward" / "v6_multiline_matrix_freezes_v685"
STATUS = ROOT / "manifests" / "v6_multiline_matrix_forward_freeze_v685_status.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def integrity(formal_freeze: dict[str, Any]) -> tuple[bool, dict[str, bool]]:
    hashes = formal_freeze.get("hashes") or {}
    payload_without_hashes = {k: v for k, v in formal_freeze.items() if k != "hashes"}
    checks = {
        "match_context": hashes.get("match_context_sha256") == sha256_json(formal_freeze.get("match_context")),
        "calculation_output": hashes.get("calculation_output_sha256") == sha256_json(formal_freeze.get("calculation_output")),
        "validation_report": hashes.get("validation_report_sha256") == sha256_json(formal_freeze.get("validation_report")),
        "freeze_payload": hashes.get("freeze_payload_sha256") == sha256_json(payload_without_hashes),
    }
    return all(checks.values()), checks


def identity_tuple(competition_id: str, kickoff: str, home: str, away: str) -> tuple[str, str, str, str]:
    return competition_id, kickoff, normalize_team_token(home), normalize_team_token(away)


def market_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return identity_tuple(
        str(row.get("competition_id") or ""),
        str(row.get("kickoff_utc") or ""),
        str(row.get("home_team") or ""),
        str(row.get("away_team") or ""),
    )


def load_ladders() -> tuple[dict[str, dict[str, Any]], str | None]:
    if not LADDER_FILE.exists():
        return {}, None
    payload = load_json(LADDER_FILE)
    bundles = payload.get("bundles") or []
    by_raw = {str(row.get("raw_path") or ""): row for row in bundles if isinstance(row, dict) and row.get("raw_path")}
    return by_raw, file_sha(LADDER_FILE)


def select_kambi_snapshot(target: tuple[str, str, str, str], freeze_time: datetime, max_age: float) -> tuple[Path, dict[str, Any], float] | None:
    candidates = []
    for path in MARKETS.glob("*.json") if MARKETS.exists() else []:
        try:
            row = load_json(path)
            if str(row.get("provider_group") or "") != "kambi" or market_identity(row) != target:
                continue
            observed = parse_iso_datetime(str(row.get("source_observed_at_utc") or row.get("freeze_utc") or ""), "market_observed")
            kickoff = parse_iso_datetime(str(row.get("kickoff_utc") or ""), "kickoff")
            if observed > freeze_time or observed >= kickoff:
                continue
            age = (freeze_time - observed).total_seconds()
            if age < 0 or age > max_age:
                continue
            candidates.append((age, -observed.timestamp(), path, row))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], str(x[2])))
    age, _neg, path, row = candidates[0]
    return path, row, float(age)


def select_consensus(target: tuple[str, str, str, str], freeze_time: datetime, max_age: float, kambi_snapshot_sha: str | None) -> tuple[Path, dict[str, Any], float, bool] | None:
    candidates = []
    for path in CONSENSUS.glob("*.json") if CONSENSUS.exists() else []:
        try:
            row = load_json(path)
            if market_identity(row) != target or not bool(row.get("promotion_evidence_eligible")):
                continue
            observed = parse_iso_datetime(str(row.get("consensus_observed_at_utc") or ""), "consensus_observed")
            if observed > freeze_time:
                continue
            age = (freeze_time - observed).total_seconds()
            if age < 0 or age > max_age:
                continue
            alignment = row.get("alignment_evidence") or {}
            same_primary = bool(kambi_snapshot_sha) and alignment.get("kambi_primary_snapshot_sha256") == kambi_snapshot_sha
            candidates.append((not same_primary, age, -observed.timestamp(), path, row, same_primary))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2], str(x[3])))
    _same_sort, age, _neg, path, row, same_primary = candidates[0]
    return path, row, float(age), bool(same_primary)


def sidecar_path(identity: dict[str, Any], context_hash: str) -> Path:
    freeze = parse_iso_datetime(identity["freeze_time_utc"], "freeze_time_utc")
    name = "__".join([
        safe(str(identity["competition_id"])), safe(str(identity["home_team"])), safe(str(identity["away_team"])),
        freeze.strftime("%Y%m%dT%H%M%SZ"), context_hash[:12],
    ]) + ".json"
    return OUT_ROOT / name


def process_one(path: Path, cfg: dict[str, Any], ladders_by_raw: dict[str, dict[str, Any]], ladder_file_sha: str | None) -> tuple[str, dict[str, Any] | None]:
    try:
        formal_freeze = load_json(path)
        ok, checks = integrity(formal_freeze)
        if not ok:
            return "formal_freeze_integrity_fail", {"path": str(path.relative_to(ROOT)), "checks": checks}
        if (formal_freeze.get("validation_report") or {}).get("status") != "通过":
            return "formal_validation_not_pass", None
        identity = (formal_freeze.get("match_context") or {}).get("match_identity") or {}
        freeze_time = parse_iso_datetime(str(identity.get("freeze_time_utc") or ""), "freeze_time_utc")
        kickoff = parse_iso_datetime(str(identity.get("kickoff_utc") or ""), "kickoff_utc")
        epoch = parse_iso_datetime(cfg["epoch_freeze_timestamp_utc"], "epoch")
        if freeze_time < epoch:
            return "before_epoch", None
        if freeze_time >= kickoff:
            return "invalid_formal_timing", None
        context_hash = str((formal_freeze.get("match_context") or {}).get("context_hash") or "")
        if not context_hash:
            return "missing_context_hash", None
        out = sidecar_path(identity, context_hash)
        if out.exists():
            return "already_frozen", None
        calc = formal_freeze.get("calculation_output") or {}
        prior = ((calc.get("probabilities") or {}).get("score_matrix"))
        if not isinstance(prior, list) or not prior:
            return "formal_matrix_missing", None
        target = identity_tuple(str(identity.get("competition_id") or ""), kickoff.isoformat(), str(identity.get("home_team") or ""), str(identity.get("away_team") or ""))
        market = select_kambi_snapshot(target, freeze_time, float(cfg["maximum_ladder_age_seconds_at_formal_freeze"]))
        if market is None:
            return "no_fresh_kambi_snapshot", None
        market_path, market_row, market_age = market
        parent_raw = str(((market_row.get("source_adapter") or {}).get("parent_raw_evidence_path")) or "")
        bundle = ladders_by_raw.get(parent_raw)
        if not bundle:
            return "no_matching_full_ladder", {"market_path": str(market_path.relative_to(ROOT)), "parent_raw": parent_raw}
        bundle_observed = parse_iso_datetime(str(bundle.get("observed_at_utc") or ""), "bundle_observed")
        if bundle_observed > freeze_time:
            return "ladder_after_formal_freeze", None
        projection = project(prior, bundle)
        if projection.get("status") != "MULTILINE_MARKET_MATRIX_READY":
            return "projection_not_ready", {"projection_status": projection.get("status")}
        candidate = projection.get("candidate_matrix") or []
        consensus = select_consensus(
            target, freeze_time, float(cfg["independent_consensus_maximum_age_seconds"]), market_row.get("raw_snapshot_sha256")
        )
        consensus_block: dict[str, Any] | None = None
        promotion_eligible = False
        if consensus is not None:
            consensus_path, consensus_row, consensus_age, same_primary = consensus
            provider_groups = set(str(x) for x in (consensus_row.get("provider_groups") or []))
            skew = float(consensus_row.get("cross_provider_timestamp_spread_seconds") or 1e18)
            promotion_eligible = bool(
                same_primary
                and len(provider_groups) >= int(cfg["promotion_evidence_gate"]["minimum_provider_groups"])
                and skew <= float(cfg["promotion_evidence_gate"]["maximum_cross_provider_skew_seconds"])
                and consensus_row.get("promotion_evidence_eligible") is True
            )
            consensus_block = {
                "path": str(consensus_path.relative_to(ROOT)),
                "file_sha256": file_sha(consensus_path),
                "consensus_sha256": consensus_row.get("consensus_sha256"),
                "observed_at_utc": consensus_row.get("consensus_observed_at_utc"),
                "age_seconds_at_formal_freeze": consensus_age,
                "provider_groups": sorted(provider_groups),
                "cross_provider_timestamp_spread_seconds": skew,
                "same_kambi_primary_snapshot": same_primary,
                "promotion_evidence_eligible": promotion_eligible,
            }
        projection_audit = {k: v for k, v in projection.items() if k not in {"candidate_matrix", "total_goals_distribution", "score_diagnostics"}}
        payload_without_hash = {
            "schema_version": "V6.8.5-multiline-joint-matrix-forward-freeze-r1",
            "status": "FROZEN",
            "recorded_at_utc": utc_now().isoformat(),
            "epoch_freeze_timestamp_utc": cfg["epoch_freeze_timestamp_utc"],
            "fixture_identity": {
                "competition_id": identity.get("competition_id"), "season": identity.get("season"),
                "home_team": identity.get("home_team"), "away_team": identity.get("away_team"),
                "kickoff_utc": kickoff.isoformat(), "freeze_time_utc": freeze_time.isoformat(),
                "settlement": identity.get("settlement"),
            },
            "formal_source": {
                "freeze_path": str(path.relative_to(ROOT)),
                "freeze_file_sha256": file_sha(path),
                "freeze_id": formal_freeze.get("freeze_id"),
                "context_hash": context_hash,
                "formal_calculation_sha256": (formal_freeze.get("hashes") or {}).get("calculation_output_sha256"),
                "formal_matrix_sha256": sha256_json(prior),
                "formal_matrix": prior,
            },
            "market_evidence": {
                "kambi_snapshot_path": str(market_path.relative_to(ROOT)),
                "kambi_snapshot_file_sha256": file_sha(market_path),
                "kambi_raw_snapshot_sha256": market_row.get("raw_snapshot_sha256"),
                "kambi_observed_at_utc": market_row.get("source_observed_at_utc") or market_row.get("freeze_utc"),
                "market_age_seconds_at_formal_freeze": market_age,
                "raw_ladder_path": parent_raw,
                "raw_ladder_file_sha256": file_sha(ROOT / parent_raw) if parent_raw and (ROOT / parent_raw).exists() else None,
                "ladder_aggregate_path": str(LADDER_FILE.relative_to(ROOT)),
                "ladder_aggregate_sha256": ladder_file_sha,
                "bundle_sha256": sha256_json(bundle),
                "independent_consensus": consensus_block,
            },
            "candidate": {
                "architecture": "V6.8.2 minimum_KL_IPF_1x2_plus_multiple_half_goal_totals",
                "candidate_matrix_sha256": sha256_json(candidate),
                "candidate_matrix": candidate,
                "total_goals_distribution": projection.get("total_goals_distribution"),
                "score_diagnostics": projection.get("score_diagnostics"),
                "projection_audit": projection_audit,
            },
            "promotion_evidence_eligible": promotion_eligible,
            "governance": {
                "research_only": True,
                "formal_probability_change": False,
                "formal_weight_change": False,
                "postmatch_reprojection_forbidden": True,
                "postmatch_formal_prior_recalculation_forbidden": True,
                "settlement_must_score_stored_matrices_only": True,
                "historical_backfill": False,
            },
        }
        payload = {**payload_without_hash, "freeze_sha256": sha256_json(payload_without_hash)}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return "new_sidecar_frozen_promotion_eligible" if promotion_eligible else "new_sidecar_frozen_diagnostic", {"path": str(out.relative_to(ROOT))}
    except Exception as exc:
        return "exception", {"path": str(path.relative_to(ROOT)), "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    cfg = load_json(CFG)
    if cfg.get("status") != "FROZEN":
        raise SystemExit("V6.8.5 epoch is not FROZEN")
    ladders, ladder_sha = load_ladders()
    counts: dict[str, int] = {}
    details = []
    files = sorted(FORMAL_FREEZES.rglob("*.json")) if FORMAL_FREEZES.exists() else []
    for path in files:
        status, detail = process_one(path, cfg, ladders, ladder_sha)
        counts[status] = counts.get(status, 0) + 1
        if detail and status in {"formal_freeze_integrity_fail", "exception", "no_matching_full_ladder"}:
            details.append({"status": status, **detail})
    payload = {
        "schema_version": "V6.8.5-multiline-joint-matrix-forward-freeze-status-r1",
        "generated_at_utc": utc_now().isoformat(),
        "status": "PASS" if not any(k in counts for k in ("formal_freeze_integrity_fail", "exception")) else "WARN",
        "epoch_freeze_timestamp_utc": cfg["epoch_freeze_timestamp_utc"],
        "formal_freezes_scanned": len(files),
        "status_counts": dict(sorted(counts.items())),
        "sidecar_count": len(list(OUT_ROOT.glob("*.json"))) if OUT_ROOT.exists() else 0,
        "errors": details,
        "governance": cfg["governance"],
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] in {"PASS", "WARN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
