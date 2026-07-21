#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_strict_v5519 import build as build_strict, validate_consensus
from prospective_market_snapshot_v523 import canonical_sha256, validate as validate_snapshot

SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
BUNDLE_ROOT = ROOT / "evidence" / "market_line_bundles_prospective"
ALIGNED_ROOT = ROOT / "evidence" / "market_aligned_observations_prospective"
CONSENSUS_ROOT = ROOT / "evidence" / "market_consensus_prospective"
MANIFEST = ROOT / "manifests" / "exact_line_aligned_consensus_v5529_status.json"
ALLOWED_COMPETITIONS = {"POR_PrimeiraLiga", "ESP_LaLiga", "FRA_Ligue1", "GER_Bundesliga"}
MAX_SKEW_SECONDS = 300.0


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("competition_id") or ""),
        str(row.get("season") or ""),
        str(row.get("home_team") or ""),
        str(row.get("away_team") or ""),
        str(row.get("kickoff_utc") or ""),
    )


def exact_line(rows: list[dict[str, Any]], target: float) -> dict[str, Any] | None:
    matches = [row for row in rows if abs(float(row.get("line")) - float(target)) <= 1e-9]
    if not matches:
        return None
    return sorted(matches, key=lambda x: (float(x.get("overround_abs", 999.0)), float(x.get("balance_distance", 999.0)), int(x.get("offer_id", 0))))[0]


def load_fresh_marathon(since: datetime) -> dict[tuple[str, str, str, str, str], tuple[datetime, dict[str, Any], Path]]:
    out = {}
    for path in SNAPSHOT_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("provider_group") != "marathonbet" or row.get("competition_id") not in ALLOWED_COMPETITIONS:
                continue
            observed = dt(str(row.get("freeze_utc")))
            if observed < since:
                continue
            v = validate_snapshot(row)
            if not v.get("passed") or not v.get("formal_pit_eligible"):
                continue
            k = key(row)
            cur = out.get(k)
            if cur is None or observed > cur[0]:
                out[k] = (observed, row, path)
        except Exception:
            continue
    return out


def load_fresh_bundles(since: datetime) -> dict[tuple[str, str, str, str, str], tuple[datetime, dict[str, Any], Path]]:
    out = {}
    for path in BUNDLE_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("provider_group") != "kambi" or row.get("competition_id") not in ALLOWED_COMPETITIONS:
                continue
            observed = dt(str(row.get("observed_at_utc")))
            if observed < since:
                continue
            k = key(row)
            cur = out.get(k)
            if cur is None or observed > cur[0]:
                out[k] = (observed, row, path)
        except Exception:
            continue
    return out


def load_parent_kambi(bundle: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    rel = bundle.get("parent_snapshot_path")
    if not rel:
        raise ValueError("Kambi bundle parent snapshot path missing")
    path = ROOT / str(rel)
    row = json.loads(path.read_text(encoding="utf-8"))
    if row.get("provider_group") != "kambi":
        raise ValueError("bundle parent is not Kambi")
    if row.get("raw_snapshot_sha256") != bundle.get("parent_snapshot_sha256"):
        raise ValueError("bundle parent snapshot SHA mismatch")
    v = validate_snapshot(row)
    if not v.get("passed") or not v.get("formal_pit_eligible"):
        raise ValueError(f"bundle parent Kambi V5.2.3 invalid: {v.get('errors')}")
    return row, path


def aligned_path(row: dict[str, Any], bundle: dict[str, Any]) -> Path:
    token = str(row["freeze_utc"]).replace(":", "").replace("+00:00", "Z")
    return ALIGNED_ROOT / (
        f"{safe(row['competition_id'])}__{safe(row['home_team'])}__{safe(row['away_team'])}__"
        f"kambi_exact_line__{token}__{str(bundle['bundle_sha256'])[:12]}.json"
    )


def build_aligned_kambi(parent: dict[str, Any], bundle: dict[str, Any], marathon: dict[str, Any], bundle_path: Path) -> tuple[dict[str, Any], Path]:
    target_ah = float(marathon["asian_handicap"]["line"])
    target_ou = float(marathon["over_under"]["line"])
    ah = exact_line(list(bundle.get("asian_handicap_lines") or []), target_ah)
    ou = exact_line(list(bundle.get("over_under_lines") or []), target_ou)
    if ah is None or ou is None:
        missing = []
        if ah is None:
            missing.append(f"AH_{target_ah}")
        if ou is None:
            missing.append(f"OU_{target_ou}")
        raise LookupError("NO_EXACT_KAMBI_LINE:" + ",".join(missing))
    row = json.loads(json.dumps(parent))
    row["asian_handicap"] = {"line": float(ah["line"]), "home": float(ah["home"]), "away": float(ah["away"])}
    row["over_under"] = {"line": float(ou["line"]), "over": float(ou["over"]), "under": float(ou["under"])}
    adapter = dict(row.get("source_adapter") or {})
    adapter["exact_line_alignment"] = {
        "schema_version": "V5.5.29-exact-line-aligned-kambi-observation-r1",
        "target_provider_group": "marathonbet",
        "target_marathon_snapshot_sha256": marathon.get("raw_snapshot_sha256"),
        "target_ah_line": target_ah,
        "target_ou_line": target_ou,
        "kambi_bundle_path": str(bundle_path.relative_to(ROOT)),
        "kambi_bundle_sha256": bundle.get("bundle_sha256"),
        "selected_ah_offer_id": ah.get("offer_id"),
        "selected_ou_offer_id": ou.get("offer_id"),
        "selected_values_from_kambi_same_raw_observation_only": True,
        "interpolation_used": False,
        "synthetic_odds_used": False,
        "cross_provider_market_value_splicing": False,
        "purpose": "Comparison-only exact-line observation; preserve Kambi prices while selecting the directly quoted Kambi lines that exactly match the independent Marathonbet primary AH/OU lines.",
    }
    row["source_adapter"] = adapter
    row["comparison_observation"] = True
    row["primary_provider_snapshot_sha256"] = parent.get("raw_snapshot_sha256")
    row["promotion_semantics"] = {
        "standalone_single_provider_promotion_eligible": False,
        "may_enter_independent_exact_line_consensus": True,
        "requires_two_provider_time_and_strict_three_surface_gate": True,
    }
    row.pop("raw_snapshot_sha256", None)
    row["raw_snapshot_sha256"] = canonical_sha256(row)
    v = validate_snapshot(row)
    if not v.get("passed") or not v.get("formal_pit_eligible"):
        raise ValueError(f"aligned Kambi observation failed V5.2.3: {v.get('errors')}")
    out = aligned_path(row, bundle)
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        if existing.get("raw_snapshot_sha256") != row.get("raw_snapshot_sha256"):
            raise FileExistsError(f"immutable aligned Kambi observation collision: {out}")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row, out


def consensus_path(payload: dict[str, Any], bundle_sha: str) -> Path:
    token = str(payload["consensus_observed_at_utc"]).replace(":", "").replace("+00:00", "Z")
    return CONSENSUS_ROOT / (
        f"{safe(payload['competition_id'])}__{safe(payload['home_team'])}__{safe(payload['away_team'])}__"
        f"{token}__n2__strict_exact_line__{bundle_sha[:12]}.json"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-utc", required=True)
    args = parser.parse_args()
    since = dt(args.since_utc)
    marathon = load_fresh_marathon(since)
    bundles = load_fresh_bundles(since)
    shared = sorted(set(marathon) & set(bundles))
    receipt = {
        "schema_version": "V5.5.29-exact-line-aligned-consensus-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "since_utc": since.replace(microsecond=0).isoformat(),
        "status": "NO_EXACT_LINE_ALIGNED_CONSENSUS",
        "fresh_marathon_fixture_count": len(marathon),
        "fresh_kambi_bundle_fixture_count": len(bundles),
        "shared_fixture_count": len(shared),
        "exact_line_match_count": 0,
        "no_exact_line_match_count": 0,
        "timestamp_skew_fail_count": 0,
        "strict_consensus_count_written": 0,
        "promotion_evidence_eligible_count": 0,
        "results": [],
        "formal_model_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
    }
    for k in shared:
        m_obs, m, m_path = marathon[k]
        b_obs, bundle, b_path = bundles[k]
        cid, season, home, away, kickoff = k
        row = {
            "competition_id": cid,
            "season": season,
            "home_team": home,
            "away_team": away,
            "kickoff_utc": kickoff,
            "marathon_snapshot_path": str(m_path.relative_to(ROOT)),
            "kambi_bundle_path": str(b_path.relative_to(ROOT)),
            "status": "FAIL_CLOSED",
        }
        skew = abs((m_obs - b_obs).total_seconds())
        row["cross_provider_timestamp_spread_seconds"] = skew
        if skew > MAX_SKEW_SECONDS:
            receipt["timestamp_skew_fail_count"] += 1
            row["status"] = "TIMESTAMP_SKEW_FAIL_CLOSED"
            receipt["results"].append(row)
            continue
        try:
            parent, _ = load_parent_kambi(bundle)
            aligned, aligned_path_obj = build_aligned_kambi(parent, bundle, m, b_path)
            receipt["exact_line_match_count"] += 1
            payload = build_strict([aligned, m])
            strict_validation = validate_consensus(payload)
            if not strict_validation.get("passed") or not payload.get("promotion_evidence_eligible"):
                raise ValueError(f"strict exact-line consensus not promotion eligible: {strict_validation}; {payload.get('promotion_ineligibility_reasons')}")
            payload["schema_version"] = "V5.5.29-exact-line-aligned-independent-consensus-r1"
            payload["alignment_evidence"] = {
                "method": "EXACT_DIRECT_QUOTED_LINE_MATCH_ONLY",
                "kambi_aligned_observation_path": str(aligned_path_obj.relative_to(ROOT)),
                "kambi_aligned_observation_sha256": aligned.get("raw_snapshot_sha256"),
                "kambi_primary_snapshot_sha256": aligned.get("primary_provider_snapshot_sha256"),
                "kambi_multiline_bundle_path": str(b_path.relative_to(ROOT)),
                "kambi_multiline_bundle_sha256": bundle.get("bundle_sha256"),
                "marathon_snapshot_sha256": m.get("raw_snapshot_sha256"),
                "interpolation_used": False,
                "synthetic_odds_used": False,
                "cross_provider_market_value_splicing": False,
            }
            payload["promotion_evidence_eligible"] = True
            payload["promotion_sample_semantics"] = "One fixture/time/provider-pair observation. De-duplicate against any primary-line consensus for the same fixture and observation pair before model-promotion training."
            payload.pop("consensus_sha256", None)
            payload["consensus_sha256"] = canonical_sha256(payload)
            out = consensus_path(payload, str(bundle["bundle_sha256"]))
            created = False
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("consensus_sha256") != payload.get("consensus_sha256"):
                    raise FileExistsError(f"immutable exact-line consensus collision: {out}")
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt["strict_consensus_count_written"] += 1
                created = True
            receipt["promotion_evidence_eligible_count"] += 1
            row.update({
                "status": "EXACT_LINE_STRICT_CONSENSUS_WRITTEN" if created else "EXACT_LINE_STRICT_CONSENSUS_ALREADY_PRESENT",
                "aligned_kambi_observation_path": str(aligned_path_obj.relative_to(ROOT)),
                "consensus_path": str(out.relative_to(ROOT)),
                "ah_line": payload["asian_handicap"]["line"],
                "ou_line": payload["over_under"]["line"],
                "promotion_evidence_eligible": True,
            })
        except LookupError as exc:
            receipt["no_exact_line_match_count"] += 1
            row["status"] = "NO_EXACT_DIRECT_QUOTED_LINE_MATCH"
            row["error"] = str(exc)
        except Exception as exc:
            row["status"] = "EXACT_LINE_CONSENSUS_FAIL_CLOSED"
            row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["results"].append(row)
    if receipt["promotion_evidence_eligible_count"]:
        receipt["status"] = "PASS_EXACT_LINE_ALIGNED_CONSENSUS_AVAILABLE"
    receipt["policy"] = "Exact-line alignment may only choose a Kambi AH/OU line directly quoted in the same immutable Kambi event-detail observation that exactly equals the independent Marathonbet primary line. No interpolation, synthetic odds, probability reconstruction or cross-provider market-value splicing. Evidence does not self-promote or change formal weights/probabilities."
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
