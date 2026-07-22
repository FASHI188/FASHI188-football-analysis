#!/usr/bin/env python3
"""V6.2.8 fresh disjoint confirmation of the frozen V6.2.7 home-only rule.

Pre-registered before observing this confirmation panel:
- same 17 domains and two evaluation seasons per domain;
- 50 fresh matches per season, excluding every V6.2.5 r3 identity (1,700 fresh rows total);
- outcome-blind deterministic SHA256 sampling with a new seed;
- V6.2.7 home threshold is frozen and cannot be retuned here;
- eligibility remains non-draw + V6/formal direction agreement, with only home surviving;
- primary confirmation is the fresh newer-season 850: >=150 selections and raw accuracy >=65%.

Research confirmation only. No CURRENT/formal/runtime/V6.1 pristine-forward mutation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
import v6_market_residual_fusion_v620 as v620
import v6_sampled_17domain_gate_v625_r2 as v625
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

OUT = ROOT / "manifests" / "v6_sampled_fresh_confirmation_v628_status.json"
PANEL_OUT = ROOT / "manifests" / "v6_sampled_fresh_confirmation_v628_panel.json"
OLD_CACHE = ROOT / "manifests" / "v6_sampled_17domain_scored_cache_v625_r3.json"
V627 = ROOT / "manifests" / "v6_sampled_direction_survival_v627_status.json"
V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
SEED = "V6.2.8-fresh-confirm-17domain-2season-50-v1"
N_PER_SEASON = 50
PRIMARY_MIN_SELECTIONS = 150
TARGET_ACCURACY = 0.65
Z90 = 1.6448536269514722


def _sample_key(row: dict[str, Any]) -> str:
    return hashlib.sha256((SEED + "|" + v625._identity(row)).encode("utf-8")).hexdigest()


def _wilson_lower(hits: int, count: int) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z = Z90
    denom = 1.0 + z * z / count
    centre = p + z * z / (2.0 * count)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count)
    return (centre - radius) / denom


def _metric(rows: list[dict[str, Any]], denominator: int) -> dict[str, Any]:
    count = len(rows)
    hits = sum(int(bool(r["hit"])) for r in rows)
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "wilson90_lower": _wilson_lower(hits, count),
        "coverage": count / denominator if denominator else 0.0,
        "predicted_direction_counts": dict(Counter(str(r["pick"]) for r in rows)),
    }


def _passes_frozen_rule(row: dict[str, Any], threshold: float) -> bool:
    return (
        bool(row["eligible_prior_selective"])
        and row["pick"] == "home"
        and float(row["confidence"]) >= threshold
    )


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    old_cache = load_json(OLD_CACHE)
    old_ids = {str(r["identity"]) for r in old_cache.get("rows", [])}
    if len(old_ids) != 1700:
        raise PlatformError(f"expected 1700 excluded old identities, found {len(old_ids)}")

    v627 = load_json(V627)
    if v627.get("survived_directions") != ["home"]:
        raise PlatformError(f"V6.2.7 survivor drift: {v627.get('survived_directions')}")
    home_rule = (v627.get("calibration_by_direction") or {}).get("home") or {}
    home_threshold = float(home_rule["threshold"])
    if not bool(home_rule.get("survives_65_calibration_gate")):
        raise PlatformError("V6.2.7 home direction did not survive calibration")

    selected = ((load_json(V601_STATUS).get("result") or {}).get("selected_candidate") or {})
    l2 = float(selected.get("l2", 1.0))
    pool_weight = float(selected.get("pool_weight", 0.75))
    draw_ratio = float(selected.get("draw_ratio", 0.80))

    domains = sorted((load_json(base.FORMAL_STATUS).get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 domains, found {len(domains)}")

    older_rows: list[dict[str, Any]] = []
    newer_rows: list[dict[str, Any]] = []
    panel: list[dict[str, Any]] = []
    by_domain: dict[str, Any] = {}

    for cid in domains:
        report = load_json(base.REPORT_ROOT / f"{cid}.json")
        completed = _completed_outer_seasons_last_complete_only(report)
        if len(completed) < 4:
            raise PlatformError(f"{cid}: insufficient completed seasons")
        seasons = completed[-4:]
        built = v620._build_domain_rows_with_identity(cid, seasons)
        ordered = sorted(built, key=_season_key)
        fit_seasons, older_eval, newer_eval = ordered[:2], ordered[2], ordered[3]
        fit_rows: list[dict[str, Any]] = []
        for season in fit_seasons:
            fit_rows.extend(built[season])
        older_model = base._fit_models(fit_rows, l2)
        newer_model = base._fit_models(fit_rows + built[older_eval], l2)
        domain = {"older_season": older_eval, "newer_season": newer_eval, "seasons": {}}

        for role, season, model, collector in (
            ("older", older_eval, older_model, older_rows),
            ("newer", newer_eval, newer_model, newer_rows),
        ):
            available = [r for r in built[season] if v625._identity(r) not in old_ids]
            if len(available) < N_PER_SEASON:
                raise PlatformError(f"{cid} {season}: only {len(available)} disjoint rows available")
            chosen = sorted(available, key=_sample_key)[:N_PER_SEASON]
            if len({v625._identity(r) for r in chosen}) != N_PER_SEASON:
                raise PlatformError(f"{cid} {season}: duplicate confirmation identity")
            scored = [v625._score_row(r, model, pool_weight, draw_ratio) for r in chosen]
            collector.extend(scored)
            selected_rows = [r for r in scored if _passes_frozen_rule(r, home_threshold)]
            domain["seasons"][season] = {
                "role": role,
                "available_disjoint_rows": len(available),
                "sampled_rows": len(chosen),
                "selected_by_frozen_rule": len(selected_rows),
                "selected_hits": sum(int(bool(r["hit"])) for r in selected_rows),
                "selected_accuracy": (
                    sum(int(bool(r["hit"])) for r in selected_rows) / len(selected_rows)
                    if selected_rows else None
                ),
            }
            for r in chosen:
                panel.append({
                    "competition_id": r["competition_id"],
                    "season": r["season"],
                    "role": role,
                    "date": r["date"],
                    "home_team": r["home_team"],
                    "away_team": r["away_team"],
                    "identity": v625._identity(r),
                    "sample_hash": _sample_key(r),
                })
        by_domain[cid] = domain

    if len(older_rows) != 850 or len(newer_rows) != 850:
        raise PlatformError(f"expected fresh 850+850, got {len(older_rows)}+{len(newer_rows)}")
    all_ids = {v625._identity(r) for r in older_rows + newer_rows}
    if len(all_ids) != 1700:
        raise PlatformError("fresh confirmation identities not unique")
    overlap = all_ids & old_ids
    if overlap:
        raise PlatformError(f"fresh confirmation overlaps old panel: {len(overlap)}")

    older_selected = [r for r in older_rows if _passes_frozen_rule(r, home_threshold)]
    newer_selected = [r for r in newer_rows if _passes_frozen_rule(r, home_threshold)]
    older_metric = _metric(older_selected, 850)
    newer_metric = _metric(newer_selected, 850)
    primary_gate_passed = (
        int(newer_metric["count"]) >= PRIMARY_MIN_SELECTIONS
        and newer_metric["accuracy"] is not None
        and float(newer_metric["accuracy"]) >= TARGET_ACCURACY
    )

    panel.sort(key=lambda r: (r["competition_id"], r["season"], r["sample_hash"]))
    panel_sha = hashlib.sha256("\n".join(r["sample_hash"] for r in panel).encode("utf-8")).hexdigest()
    atomic_write_json(PANEL_OUT, {
        "schema_version": "V6.2.8-fresh-disjoint-confirmation-panel-r1",
        "generated_at_utc": generated.isoformat(),
        "seed": SEED,
        "count": 1700,
        "old_panel_overlap_count": 0,
        "panel_sha256": panel_sha,
        "rows": panel,
    })

    payload = {
        "schema_version": "V6.2.8-fresh-disjoint-confirmation-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "pre_registered_design": {
            "competition_count": 17,
            "sample_per_season": 50,
            "fresh_total": 1700,
            "old_v625_identity_exclusion_count": 1700,
            "old_panel_overlap_allowed": False,
            "sample_seed": SEED,
            "sample_outcome_blind": True,
            "frozen_survived_direction": "home",
            "frozen_home_threshold": home_threshold,
            "frozen_eligibility": "non-draw + V6/formal direction agreement",
            "primary_evaluation": "fresh newer-season 850 only",
            "primary_min_selections": PRIMARY_MIN_SELECTIONS,
            "primary_target_raw_accuracy": TARGET_ACCURACY,
            "confirmation_parameters_tunable": False,
        },
        "fresh_older_850_secondary": older_metric,
        "fresh_newer_850_primary": {
            **newer_metric,
            "primary_gate_passed": primary_gate_passed,
        },
        "by_domain": by_domain,
        "audit": {
            "fresh_identity_count": 1700,
            "old_panel_overlap_count": 0,
            "panel_sha256": panel_sha,
            "v601_frozen_parameters": {"l2": l2, "pool_weight": pool_weight, "draw_ratio": draw_ratio},
        },
        "governance": {
            "fresh_confirmation_only": True,
            "confirmation_sample_used_for_tuning": False,
            "v627_rule_changed": False,
            "current_rule_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "automatic_promotion": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
