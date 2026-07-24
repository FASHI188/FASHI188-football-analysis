#!/usr/bin/env python3
"""Research-only 1X2 parameter-variant search with chronological holdout.

Tests one-factor changes to the frozen V4.6/V5 formal-core parameter set. Candidate
selection uses only the earlier half of each completed target season; the later half is
held out for evaluation. No market data, lineup hindsight, or post-match features are
used in prediction. This script cannot promote or mutate formal parameters.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from football_v460_engine import (
    _merge_parameters,
    build_score_matrix,
    current_season_history,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from oof_matrix_calibration import load_oof_matrix_calibrator, temperature_scale_matrix
from platform_core import PlatformError, derive_score_marginals, load_json, read_processed_matches

FORMAL_STATUS = ROOT / "manifests" / "formal_core_v460_status.json"
REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OUT = ROOT / "manifests" / "v6_1x2_parameter_variants_v698_status.json"
SEED = 20260724
DIRECTIONS = ("home", "draw", "away")
CALENDAR_YEAR_DOMAINS = {
    "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1", "KOR_KLeague1",
    "BRA_SerieA", "ARG_Primera", "USA_MLS",
}

# One-factor changes only. This keeps attribution clear and limits search overfit.
VARIANTS = {
    "baseline": {},
    "half_life_90": {"half_life_days": 90.0},
    "half_life_120": {"half_life_days": 120.0},
    "half_life_240": {"half_life_days": 240.0},
    "half_life_365": {"half_life_days": 365.0},
    "team_prior_4": {"team_prior_matches": 4.0},
    "team_prior_12": {"team_prior_matches": 12.0},
    "team_prior_16": {"team_prior_matches": 16.0},
    "low_score_0": {"low_score_shrinkage": 0.0},
    "low_score_030": {"low_score_shrinkage": 0.30},
    "beta_conc_12": {"beta_binomial_concentration": 12.0},
    "beta_conc_36": {"beta_binomial_concentration": 36.0},
    "direct_total_035": {"direct_total_signal_weight": 0.35},
    "direct_total_100": {"direct_total_signal_weight": 1.0},
}


def _season(cid: str) -> str:
    return "2025" if cid in CALENDAR_YEAR_DOMAINS else "2025/26"


def _actual(hg: int, ag: int) -> str:
    return "home" if hg > ag else "draw" if hg == ag else "away"


def _fold(report: dict[str, Any], season: str) -> dict[str, Any]:
    items = [f for f in (report.get("folds") or []) if str(f.get("outer_season")) == season]
    if len(items) != 1:
        raise PlatformError(f"expected one outer fold for {season}, got {len(items)}")
    return items[0]


def _temperature(cid: str, season: str) -> float:
    loaded = load_oof_matrix_calibrator(cid)
    if loaded is None:
        return 1.0
    _, artifact = loaded
    item = (artifact.get("season_calibrators") or {}).get(season)
    return float(item.get("temperature", 1.0)) if isinstance(item, dict) else 1.0


def _predict(all_matches, home: str, away: str, cutoff, season: str, selected: dict[str, Any]):
    config = load_config()
    params = _merge_parameters(config, selected)
    _, history = current_season_history(all_matches, cutoff, season)
    state = fit_current_season_state(history, cutoff, params, config)
    means = expected_goals(state, home, away, params, config)
    factors = low_score_factors(state, params)
    return build_score_matrix(
        float(means["mu_home"]), float(means["mu_away"]),
        float(state["nb_dispersion_k"]), float(params["beta_binomial_concentration"]),
        int(config["max_total_goals_exact"]), factors,
    )


def _pick(matrix) -> str:
    p = derive_score_marginals(matrix)["1x2"]
    return max(DIRECTIONS, key=lambda k: float(p[k]))


def main() -> int:
    status = load_json(FORMAL_STATUS)
    competitions = sorted((status.get("reports") or {}).keys())
    per_variant: dict[str, list[dict[str, Any]]] = {name: [] for name in VARIANTS}
    failures = {}

    for cid in competitions:
        try:
            season = _season(cid)
            report = load_json(REPORT_ROOT / f"{cid}.json")
            fold = _fold(report, season)
            base_params = fold.get("selected_parameters")
            if not isinstance(base_params, dict):
                raise PlatformError("selected_parameters missing")
            all_matches = read_processed_matches(cid)
            matches = sorted([m for m in all_matches if str(m.season) == season], key=lambda m: (m.date, m.home_team, m.away_team))
            temp = _temperature(cid, season)

            # The same eligible matches are required for all variants. If any variant fails
            # on a match, that match is omitted for all variants to preserve paired comparison.
            candidate_rows = []
            for match in matches:
                matrices = {}
                try:
                    for name, override in VARIANTS.items():
                        params = dict(base_params)
                        params.update(override)
                        matrix = _predict(all_matches, match.home_team, match.away_team, match.date, season, params)
                        if abs(temp - 1.0) > 1e-15:
                            matrix = temperature_scale_matrix(matrix, temp)
                        matrices[name] = matrix
                except PlatformError:
                    continue
                candidate_rows.append((match, matrices))

            if not candidate_rows:
                raise PlatformError("no paired eligible matches")
            split = len(candidate_rows) // 2
            for index, (match, matrices) in enumerate(candidate_rows):
                part = "development" if index < split else "holdout"
                actual = _actual(int(match.home_goals), int(match.away_goals))
                for name, matrix in matrices.items():
                    per_variant[name].append({
                        "competition_id": cid,
                        "part": part,
                        "actual": actual,
                        "pick": _pick(matrix),
                    })
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        raise RuntimeError(f"competition failures: {failures}")

    metrics = {}
    for name, rows in per_variant.items():
        metrics[name] = {}
        for part in ("development", "holdout"):
            subset = [r for r in rows if r["part"] == part]
            hits = sum(1 for r in subset if r["pick"] == r["actual"])
            metrics[name][part] = {"count": len(subset), "hits": hits, "accuracy": hits / len(subset)}

    # Global variant chosen solely on development accuracy; tie-break baseline proximity
    # by the fixed VARIANTS order.
    best_global = max(VARIANTS.keys(), key=lambda n: metrics[n]["development"]["accuracy"])

    # Competition-specific choice: each competition uses only its own earlier-half results.
    per_comp_choice = {}
    comp_holdout_hits = comp_holdout_n = 0
    baseline_holdout_hits = baseline_holdout_n = 0
    for cid in competitions:
        dev_scores = {}
        for name, rows in per_variant.items():
            sub = [r for r in rows if r["competition_id"] == cid and r["part"] == "development"]
            dev_scores[name] = sum(1 for r in sub if r["pick"] == r["actual"]) / len(sub) if sub else -1.0
        chosen = max(VARIANTS.keys(), key=lambda n: dev_scores[n])
        chosen_hold = [r for r in per_variant[chosen] if r["competition_id"] == cid and r["part"] == "holdout"]
        base_hold = [r for r in per_variant["baseline"] if r["competition_id"] == cid and r["part"] == "holdout"]
        ch = sum(1 for r in chosen_hold if r["pick"] == r["actual"])
        bh = sum(1 for r in base_hold if r["pick"] == r["actual"])
        comp_holdout_hits += ch; comp_holdout_n += len(chosen_hold)
        baseline_holdout_hits += bh; baseline_holdout_n += len(base_hold)
        per_comp_choice[cid] = {
            "chosen_variant": chosen,
            "development_accuracy": dev_scores[chosen],
            "holdout_count": len(chosen_hold),
            "holdout_hits": ch,
            "holdout_accuracy": ch / len(chosen_hold) if chosen_hold else None,
            "baseline_holdout_accuracy": bh / len(base_hold) if base_hold else None,
        }

    # Repeated 100-match checks on the later-half holdout for the globally chosen variant.
    base_hold_rows = [r for r in per_variant["baseline"] if r["part"] == "holdout"]
    chosen_by_key = {(r["competition_id"], i): r for i, r in enumerate([r for r in per_variant[best_global] if r["part"] == "holdout"])}
    # per_variant rows are appended in identical order, so use positional pairing.
    chosen_hold_rows = [r for r in per_variant[best_global] if r["part"] == "holdout"]
    repeated = []
    for rep in range(30):
        rng = random.Random(SEED + 3000 + rep)
        idxs = rng.sample(range(len(base_hold_rows)), min(100, len(base_hold_rows)))
        b_hits = sum(1 for i in idxs if base_hold_rows[i]["pick"] == base_hold_rows[i]["actual"])
        c_hits = sum(1 for i in idxs if chosen_hold_rows[i]["pick"] == chosen_hold_rows[i]["actual"])
        n = len(idxs)
        repeated.append({"repeat": rep + 1, "n": n, "baseline_accuracy": b_hits/n, "chosen_accuracy": c_hits/n, "uplift_pp": (c_hits-b_hits)/n*100.0})

    payload = {
        "schema_version": "V6.9.8-1x2-parameter-variant-chronological-holdout-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "variant_count": len(VARIANTS),
        "variants": VARIANTS,
        "metrics": metrics,
        "global_selection": {
            "selected_on_development": best_global,
            "development_accuracy": metrics[best_global]["development"]["accuracy"],
            "holdout_accuracy": metrics[best_global]["holdout"]["accuracy"],
            "baseline_holdout_accuracy": metrics["baseline"]["holdout"]["accuracy"],
            "holdout_uplift_pp": (metrics[best_global]["holdout"]["accuracy"] - metrics["baseline"]["holdout"]["accuracy"]) * 100.0,
        },
        "competition_specific_selection": {
            "holdout_count": comp_holdout_n,
            "holdout_hits": comp_holdout_hits,
            "holdout_accuracy": comp_holdout_hits / comp_holdout_n,
            "baseline_holdout_accuracy": baseline_holdout_hits / baseline_holdout_n,
            "holdout_uplift_pp": (comp_holdout_hits/comp_holdout_n - baseline_holdout_hits/baseline_holdout_n)*100.0,
            "by_competition": per_comp_choice,
        },
        "repeated_100_from_holdout": {
            "baseline_mean": statistics.mean(r["baseline_accuracy"] for r in repeated),
            "chosen_mean": statistics.mean(r["chosen_accuracy"] for r in repeated),
            "uplift_pp_mean": statistics.mean(r["uplift_pp"] for r in repeated),
            "win_tie_loss": dict(Counter("win" if r["chosen_accuracy"] > r["baseline_accuracy"] else "tie" if r["chosen_accuracy"] == r["baseline_accuracy"] else "loss" for r in repeated)),
            "runs": repeated,
        },
        "governance": {
            "research_only": True,
            "selection_uses_earlier_half_only": True,
            "evaluation_uses_later_half_only": True,
            "historical_odds_used": False,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "global_selection": payload["global_selection"],
        "competition_specific_selection": payload["competition_specific_selection"],
        "repeated_100_from_holdout": {k:v for k,v in payload["repeated_100_from_holdout"].items() if k != "runs"},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
