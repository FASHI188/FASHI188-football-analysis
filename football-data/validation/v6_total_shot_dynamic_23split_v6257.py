#!/usr/bin/env python3
"""V6.25.7 dynamic shot-state 2-vs-3 total split challenger.

Research only; formal_weight=0.

V6.25.6 showed that season-average shot/SOT features can drift across seasons.
This challenger keeps the same low-dimensional 2-vs-3 conditional split and the
same nested-OOS alpha gate, but augments the pre-match state with current-season
recency and venue signals:
- last-5 shots for/against versus season team average;
- last-5 shots-on-target for/against versus season team average;
- home-team home-only shot/SOT state versus its all-venue state;
- away-team away-only shot/SOT state versus its all-venue state;
- combined recent shot and SOT pace.

Every feature uses only matches strictly before the prediction date. Recent
rates are shrunk by two equivalent season-average matches; venue rates by three.
Those constants are fixed ex ante and are not target-tuned.

The probability transformation still changes only the split of P(T=2)+P(T=3);
all other total buckets and the combined 2/3 mass stay unchanged.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_shot_23split_v6256 as base  # noqa: E402
from backtest_last_complete_season_all_domains_v470 import FORMAL_STATUS  # noqa: E402
from platform_core import PlatformError, load_json, normalize_team_token  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402
from v6_total_shot_feature_offset_v6253 import (  # noqa: E402
    _build_rows as original_build_rows,
    _stat_history,
)

OUT = ROOT / "manifests" / "v6_total_shot_dynamic_23split_v6257_status.json"
RECENT_N = 5
RECENT_PRIOR_MATCHES = 2.0
VENUE_PRIOR_MATCHES = 3.0
EPS = 1e-9

_original_feature = base._feature


def _team_perspective(history: list[Any], team: str) -> list[dict[str, float | bool]]:
    key = normalize_team_token(team)
    rows: list[dict[str, float | bool]] = []
    for r in history:
        hk = normalize_team_token(r.home_team)
        ak = normalize_team_token(r.away_team)
        if hk == key:
            rows.append({"sf": r.hs, "sa": r.as_, "sotf": r.hst, "sota": r.ast, "home": True})
        elif ak == key:
            rows.append({"sf": r.as_, "sa": r.hs, "sotf": r.ast, "sota": r.hst, "home": False})
    return rows


def _avg(rows: list[dict[str, float | bool]], key: str, default: float = 1.0) -> float:
    values = [float(r[key]) for r in rows]
    return sum(values) / len(values) if values else default


def _shrunk_recent(all_rows: list[dict[str, float | bool]], key: str) -> float:
    season = _avg(all_rows, key)
    recent = all_rows[-RECENT_N:]
    total = sum(float(r[key]) for r in recent) + RECENT_PRIOR_MATCHES * season
    return total / max(EPS, len(recent) + RECENT_PRIOR_MATCHES)


def _shrunk_venue(all_rows: list[dict[str, float | bool]], key: str, home: bool) -> float:
    season = _avg(all_rows, key)
    venue = [r for r in all_rows if bool(r["home"]) is home]
    total = sum(float(r[key]) for r in venue) + VENUE_PRIOR_MATCHES * season
    return total / max(EPS, len(venue) + VENUE_PRIOR_MATCHES)


def _dynamic_state(history: list[Any], home_team: str, away_team: str) -> list[float]:
    h = _team_perspective(history, home_team)
    a = _team_perspective(history, away_team)
    if not h or not a:
        return [0.0] * 14

    extras: list[float] = []
    for rows in (h, a):
        for key in ("sf", "sa", "sotf", "sota"):
            season = max(EPS, _avg(rows, key))
            recent = max(EPS, _shrunk_recent(rows, key))
            extras.append(math.log(recent / season))

    # Venue-specific attack/pressure state: home team at home, away team away.
    for rows, is_home in ((h, True), (a, False)):
        for key in ("sf", "sotf"):
            season = max(EPS, _avg(rows, key))
            venue = max(EPS, _shrunk_venue(rows, key, is_home))
            extras.append(math.log(venue / season))

    # Combined recent match tempo versus both teams' season baselines.
    h_recent_shots = _shrunk_recent(h, "sf") + _shrunk_recent(h, "sa")
    a_recent_shots = _shrunk_recent(a, "sf") + _shrunk_recent(a, "sa")
    h_season_shots = _avg(h, "sf") + _avg(h, "sa")
    a_season_shots = _avg(a, "sf") + _avg(a, "sa")
    extras.append(math.log(max(EPS, h_recent_shots + a_recent_shots) / max(EPS, h_season_shots + a_season_shots)))

    h_recent_sot = _shrunk_recent(h, "sotf") + _shrunk_recent(h, "sota")
    a_recent_sot = _shrunk_recent(a, "sotf") + _shrunk_recent(a, "sota")
    h_season_sot = _avg(h, "sotf") + _avg(h, "sota")
    a_season_sot = _avg(a, "sotf") + _avg(a, "sota")
    extras.append(math.log(max(EPS, h_recent_sot + a_recent_sot) / max(EPS, h_season_sot + a_season_sot)))
    return extras


def _build_rows_dynamic(cid: str, season: str, params: dict[str, float], config: dict[str, Any], stats: list[Any]) -> list[dict[str, Any]]:
    rows = original_build_rows(cid, season, params, config, stats)
    output: list[dict[str, Any]] = []
    for row in rows:
        cutoff = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
        history = _stat_history(stats, season, cutoff)
        item = dict(row)
        item["dynamic_shot_feature"] = _dynamic_state(history, row["home_team"], row["away_team"])
        output.append(item)
    return output


def _feature_dynamic(row: dict[str, Any]) -> list[float]:
    return [*_original_feature(row), *[float(v) for v in row.get("dynamic_shot_feature", [])]]


# Patch only the feature construction and row builder. All V6.25.6 fitting,
# nested-OOS gating, probability transformation and audit rules remain unchanged.
base._build_rows = _build_rows_dynamic
base._feature = _feature_dynamic


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    pool: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    alpha_counts = Counter()
    for cid in competitions:
        try:
            result, rows = base._domain(cid)
            reports[cid] = result
            if result.get("applied"):
                pool.extend(rows)
                alpha_counts[str(result.get("selected_alpha"))] += 1
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if not pool:
        raise PlatformError("no dynamic-shot 2v3 target predictions")

    full_base = _score(pool, "baseline_matrix")
    full_candidate = _score(pool, "candidate_matrix")
    sample_n = min(base.SAMPLE_N, len(pool))
    sampled = random.Random(base.SEED).sample(pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.7-dynamic-shot-2v3-split-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_DYNAMIC_SHOT_2V3_FORMAL_WEIGHT_0",
        "applied_domain_count": sum(1 for r in reports.values() if r.get("applied")),
        "eligible_target_pool_count": len(pool),
        "selected_alpha_domain_counts": dict(alpha_counts),
        "dynamic_feature_contract": {
            "recent_matches": RECENT_N,
            "recent_prior_matches": RECENT_PRIOR_MATCHES,
            "venue_prior_matches": VENUE_PRIOR_MATCHES,
            "same_day_stats_excluded": True,
            "features": "last5 shot/SOT for-against ratios, home-away venue state, combined recent shot/SOT pace",
        },
        "full_pool": {
            "baseline": full_base,
            "candidate": full_candidate,
            "baseline_total_log_loss": base._total_log_loss(pool, "baseline_matrix"),
            "candidate_total_log_loss": base._total_log_loss(pool, "candidate_matrix"),
            "delta": base._delta(full_base, full_candidate),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": base.SEED,
            "count": sample_n,
            "baseline": sample_base,
            "candidate": sample_candidate,
            "baseline_total_log_loss": base._total_log_loss(sampled, "baseline_matrix"),
            "candidate_total_log_loss": base._total_log_loss(sampled, "candidate_matrix"),
            "delta": base._delta(sample_base, sample_candidate),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "only_2_and_3_bucket_split_changed": True,
            "combined_p2_p3_mass_preserved": True,
            "shot_stats_prior_matches_only": True,
            "recent_and_venue_features_pre_match_only": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "alpha_selected_nested_prior_season_oos": True,
            "alpha_zero_exact_baseline_fallback": True,
            "alpha_selection_requires_nested_rps_nonworse": True,
            "historical_market_odds_used": False,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "status", "applied_domain_count", "eligible_target_pool_count", "selected_alpha_domain_counts", "full_pool", "random100", "failures"
    )}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
