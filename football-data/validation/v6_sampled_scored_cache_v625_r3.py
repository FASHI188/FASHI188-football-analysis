#!/usr/bin/env python3
"""Build a frozen scored cache for the V6.2.5 r2 1,700-match sample.

This is a research acceleration artifact. It preserves the exact V6.2.5 r2 identities and
recomputes each row once with the frozen V6.0.1 model parameters. Future selective-rule
experiments can read this cache instead of rebuilding all domain histories.
"""
from __future__ import annotations

import hashlib
import json
import sys
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

OUT = ROOT / "manifests" / "v6_sampled_17domain_scored_cache_v625_r3.json"
STATUS = ROOT / "manifests" / "v6_sampled_17domain_scored_cache_v625_r3_status.json"
V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
EXPECTED_PANEL_SHA = "487ebc28be9e541f530f2baab865a5a7bb4599384cc059b75f2dc867f50962cf"


def _cache_row(row: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "competition_id": row["competition_id"],
        "season": row["season"],
        "role": role,
        "date": row["date"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "identity": v625._identity(row),
        "sample_hash": v625._sample_key(row),
        "formal": {k: float(row["formal"][k]) for k in base.CLASSES},
        "q": {k: float(row["q"][k]) for k in base.CLASSES},
        "pick": row["pick"],
        "formal_pick": row["formal_pick"],
        "confidence": float(row["confidence"]),
        "eligible_prior_selective": bool(row["eligible_prior_selective"]),
        "actual_result": row["actual_result"],
        "hit": bool(row["hit"]),
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    domains = sorted((load_json(base.FORMAL_STATUS).get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 domains, found {len(domains)}")

    selected = ((load_json(V601_STATUS).get("result") or {}).get("selected_candidate") or {})
    l2 = float(selected.get("l2", 1.0))
    pool_weight = float(selected.get("pool_weight", 0.75))
    draw_ratio = float(selected.get("draw_ratio", 0.80))

    rows_out: list[dict[str, Any]] = []
    by_role = {"older": 0, "newer": 0}
    by_domain: dict[str, dict[str, int]] = {}

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
        by_domain[cid] = {"older": 0, "newer": 0}

        for role, season, model in (("older", older_eval, older_model), ("newer", newer_eval, newer_model)):
            chosen = sorted(list(built[season]), key=v625._sample_key)[:50]
            if len(chosen) != 50:
                raise PlatformError(f"{cid} {season}: sample size {len(chosen)}")
            for raw in chosen:
                scored = v625._score_row(raw, model, pool_weight, draw_ratio)
                rows_out.append(_cache_row(scored, role))
                by_role[role] += 1
                by_domain[cid][role] += 1

    if len(rows_out) != 1700 or by_role != {"older": 850, "newer": 850}:
        raise PlatformError(f"bad cache counts: total={len(rows_out)} roles={by_role}")
    if len({r["identity"] for r in rows_out}) != 1700:
        raise PlatformError("duplicate identities in scored cache")

    rows_out.sort(key=lambda r: (r["competition_id"], r["season"], r["sample_hash"]))
    panel_sha = hashlib.sha256("\n".join(r["sample_hash"] for r in rows_out).encode("utf-8")).hexdigest()
    if panel_sha != EXPECTED_PANEL_SHA:
        raise PlatformError(f"panel hash drift: {panel_sha} != {EXPECTED_PANEL_SHA}")

    content_digest = hashlib.sha256(
        "\n".join(
            f'{r["identity"]}|{r["q"]["home"]:.17g}|{r["q"]["draw"]:.17g}|{r["q"]["away"]:.17g}|{r["actual_result"]}'
            for r in rows_out
        ).encode("utf-8")
    ).hexdigest()

    atomic_write_json(OUT, {
        "schema_version": "V6.2.5-fixed-sampled-scored-cache-r3",
        "generated_at_utc": generated.isoformat(),
        "sample_seed": v625.SAMPLE_SEED,
        "panel_sha256": panel_sha,
        "content_sha256": content_digest,
        "count": 1700,
        "roles": by_role,
        "v601_frozen_parameters": {"l2": l2, "pool_weight": pool_weight, "draw_ratio": draw_ratio},
        "rows": rows_out,
    })
    atomic_write_json(STATUS, {
        "schema_version": "V6.2.5-fixed-sampled-scored-cache-status-r3",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "count": 1700,
        "roles": by_role,
        "domains": by_domain,
        "panel_sha256": panel_sha,
        "content_sha256": content_digest,
        "governance": {
            "research_cache_only": True,
            "sample_identity_changed": False,
            "model_parameters_changed": False,
            "current_rule_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    })
    print(json.dumps({"status": "PASS", "count": 1700, "panel_sha256": panel_sha, "content_sha256": content_digest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
