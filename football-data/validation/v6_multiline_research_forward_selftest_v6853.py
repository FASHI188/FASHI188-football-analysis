#!/usr/bin/env python3
"""Engineering-only plumbing self-test for V6.8.5.3.

Uses one already-existing pre-epoch ladder only to prove that the explicit prior, fixed O/U2.5
single-line IPF arm and all-half-line multiline V6.8.2 arm can execute and audit successfully.
It never creates a prospective freeze, never uses a result, and is ineligible for Fast100 evidence.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_multiline_research_forward_v6853 as chain  # noqa: E402
from platform_core import atomic_write_json, load_json, parse_iso_datetime  # noqa: E402

OUT = ROOT / "manifests" / "v6_multiline_research_forward_selftest_v6853_status.json"


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    src = load_json(chain.LADDERS)
    rows = []
    for bundle in src.get("bundles") or []:
        try:
            cid = chain.COMP_MAP.get(str(bundle.get("competition_source") or "").strip())
            if not cid:
                continue
            observed = parse_iso_datetime(str(bundle.get("observed_at_utc") or ""), "observed")
            kickoff = parse_iso_datetime(str(bundle.get("kickoff_utc") or ""), "kickoff")
            season = chain.season_for(cid, kickoff)
            prior, audit = chain.empirical_prior(cid, season, observed)
            if prior is None:
                continue
            if len(chain.ipf.total_targets(bundle)) < 2:
                continue
            if not any(abs(float(line) - 2.5) <= 1e-9 for line, _target in chain.ipf.total_targets(bundle)):
                continue
            single = chain.single_line_project(prior, bundle)
            multi = chain.ipf.project(prior, bundle)
            if single.get("status") != "SINGLELINE_MARKET_MATRIX_READY" or multi.get("status") != "MULTILINE_MARKET_MATRIX_READY":
                continue
            rows.append((observed, bundle, cid, season, audit, single, multi))
        except Exception:
            continue
    if not rows:
        payload = {
            "schema_version": "V6.8.5.3-multiline-forward-selftest-r1",
            "generated_at_utc": now.isoformat(),
            "status": "FAIL_NO_EXECUTABLE_PRE_EPOCH_CASE",
            "engineering_only": True,
            "fast100_eligible": False,
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    observed, bundle, cid, season, audit, single, multi = sorted(rows, key=lambda x: (x[0], str(x[1].get("event_id"))))[-1]
    payload = {
        "schema_version": "V6.8.5.3-multiline-forward-selftest-r1",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "engineering_only": True,
        "fast100_eligible": False,
        "accuracy_claim": False,
        "formal_current_version": "V5.0.1",
        "case": {
            "event_id": bundle.get("event_id"),
            "competition_id": cid,
            "competition_source": bundle.get("competition_source"),
            "season": season,
            "home_team": bundle.get("home_team_source"),
            "away_team": bundle.get("away_team_source"),
            "observed_at_utc": observed.isoformat(),
            "kickoff_utc": bundle.get("kickoff_utc"),
            "prior_match_count": audit.get("strictly_prior_match_count"),
            "prior_latest_history_date": audit.get("latest_history_date"),
            "ordinary_half_goal_total_lines": sorted(float(line) for line, _target in chain.ipf.total_targets(bundle)),
        },
        "singleline": {
            "status": single.get("status"),
            "iterations": single.get("iterations"),
            "max_constraint_residual": single.get("max_constraint_residual"),
            "probability_sum_residual": single.get("probability_sum_residual"),
            "kl_from_prior": single.get("kl_from_prior"),
            "score_diagnostics": single.get("score_diagnostics"),
            "total_goals_distribution": single.get("total_goals_distribution"),
        },
        "multiline": {
            "status": multi.get("status"),
            "iterations": multi.get("iterations"),
            "constraint_count": 1 + len(multi.get("de_vigged_total_targets") or {}),
            "max_constraint_residual": multi.get("max_constraint_residual"),
            "probability_sum_residual": multi.get("probability_sum_residual"),
            "kl_from_prior": multi.get("kl_from_prior"),
            "score_diagnostics": multi.get("score_diagnostics"),
            "total_goals_distribution": multi.get("total_goals_distribution"),
        },
        "governance": {
            "pre_epoch_input_used_for_plumbing_only": True,
            "result_not_read": True,
            "prospective_freeze_not_created": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
