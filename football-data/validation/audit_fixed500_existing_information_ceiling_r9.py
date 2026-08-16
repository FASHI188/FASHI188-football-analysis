#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "processed"
OUT = ROOT / "manifests" / "fixed500_existing_information_ceiling_r9.json"

FIXED500_SHA = "6e76c2580b03043ef0a6ae003013c70fa328176bd6d3b21c2908c2e5fdf2f375"

EXPERIMENTS = [
    {
        "id": "R1",
        "family": "market_total_compact_single_OU",
        "target": "T",
        "cohort_n": 220,
        "verdict": "STABLE_POSITIVE",
        "key_metric": {"core_logloss": 1.897727217978227, "challenger_logloss": 1.8732344140393749, "delta": -0.0244928039388522},
        "bootstrap": {"full_market_vs_core_logloss_p95": -0.00790204384365452},
        "run": 31952313177,
        "artifact": 9265009470,
    },
    {
        "id": "R2",
        "family": "rich_OU_open_close_dispersion",
        "target": "T",
        "cohort_n": 220,
        "verdict": "REJECT_HARMFUL_OVERFIT",
        "key_metric": {"single_ou_logloss": 1.8732344140393749, "rich_ou_logloss": 2.1285004},
        "run": 31952646053,
        "artifact": 9265108564,
    },
    {
        "id": "R4",
        "family": "compact_1x2_AH_conditional_draw_reweight",
        "target": "conditional_GD_and_HDA",
        "cohort_n": 220,
        "verdict": "STABLE_POSITIVE_PROPER_SCORE_BUT_ZERO_TOP1_DRAWS",
        "key_metric": {
            "hda_logloss_baseline": 1.0361686430252313,
            "hda_logloss_challenger": 1.0316916,
            "draw_probability_logloss_baseline": 0.6001731,
            "draw_probability_logloss_challenger": 0.5958533,
            "top1_draw_calls": 0,
        },
        "bootstrap": {"hda_logloss_p95": -0.0002040, "draw_probability_logloss_p95": -0.000146},
        "run": 31953315560,
        "artifact": 9265274222,
    },
    {
        "id": "R5",
        "family": "oracle_T_routing_diagnostic",
        "target": "draw_ranking_bottleneck",
        "cohort_n": 214,
        "verdict": "PARITY_ROUTING_DOMINANT_DIAGNOSTIC_ONLY",
        "key_metric": {
            "actual_draws_T0_6": 63,
            "predicted_T_top1_draw_hits": 0,
            "oracle_parity_top1_draw_calls": 105,
            "oracle_parity_top1_draw_hits": 56,
            "oracle_exact_T_top1_draw_calls": 88,
            "oracle_exact_T_top1_draw_hits": 51,
        },
        "run": 31953661220,
        "artifact": 9265359062,
    },
    {
        "id": "R6",
        "family": "historical_score_parity_persistence",
        "target": "P(T_even)",
        "cohort_n": 500,
        "verdict": "REJECT_NO_SIGNAL_OR_HARMFUL",
        "key_metric": {"core_logloss": 0.6948291, "compact_parity_logloss": 0.6977634, "full_parity_logloss": 0.6985695},
        "bootstrap": {"compact_vs_core_logloss_p05": 0.001240},
        "run": 31953893101,
        "artifact": 9265428772,
    },
    {
        "id": "R7",
        "family": "lagged_shots_sot_corners_match_stats",
        "target": "T_and_P(T_even)",
        "cohort_n": 220,
        "verdict": "REJECT_STABLY_HARMFUL",
        "key_metric": {
            "ou_T_logloss": 1.8732344140393749,
            "ou_plus_compact_stats_T_logloss": 2.2206655,
            "ou_parity_logloss": 0.6945771,
            "ou_plus_compact_stats_parity_logloss": 0.6972507,
        },
        "bootstrap": {"T_logloss_p05": 0.01248, "parity_logloss_p05": "positive"},
        "run": 31954270474,
        "artifact": 9265532144,
    },
    {
        "id": "R8B",
        "family": "referee_prior_match_style",
        "target": "T_and_P(T_even)",
        "cohort_n": {"ge3": 80, "ge5": 48},
        "verdict": "NO_STABLE_SIGNAL_OR_COVERAGE_LIMITED",
        "key_metric": {"stable_T_any_tier": False, "stable_parity_any_tier": False, "stable_T_both_tiers": False, "stable_parity_both_tiers": False},
        "run": 31954752088,
        "artifact": 9265652422,
        "artifact_digest": "sha256:901912c4bdd8b543d6c7062ba41fa7379b328503ba953a137be6e854ca8596e5",
    },
]


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9><]+", "", str(value or "").casefold())


IDENTITY = {
    "div", "league", "competition", "competitionid", "season", "date", "time", "kickoff", "kickoffutc", "kickoffat",
    "hometeam", "awayteam", "home", "away", "sourcefile", "rownumber",
}
RESULT = {"fthg", "ftag", "ftr", "hthg", "htag", "htr", "homegoals90", "awaygoals90", "actualresult", "result", "totalscore"}
IN_MATCH = {"hs", "as", "hst", "ast", "hf", "af", "hc", "ac", "hy", "ay", "hr", "ar"}
REFEREE = {"referee"}
METADATA = {
    "country", "leagueid", "source", "sourceurl", "url", "matchid", "fixtureid", "eventid", "round", "matchweek", "gameweek", "status",
}


def classify(header: str) -> str:
    h = norm(header)
    if h in IDENTITY:
        return "identity_schedule"
    if h in RESULT:
        return "result_or_halftime_label"
    if h in IN_MATCH:
        return "current_match_stat_post_kickoff"
    if h in REFEREE:
        return "referee_assignment"
    if h in METADATA:
        return "metadata"
    # Market columns: known football-data patterns and generic odds/spread/total aliases.
    if any(token in h for token in ("odds", "handicap", "spread", "overunder", "totalline")):
        return "market_reference"
    if ">2.5" in h or "<2.5" in h:
        return "market_reference"
    # Common bookmaker prefixes and 1X2/AH suffixes.
    bookmaker_prefixes = ("b365", "ps", "avg", "max", "bfe", "bfd", "mgm", "bmgm", "bv", "bw", "cl", "lb", "wh", "vc", "iw", "sb", "gb")
    market_suffixes = ("h", "d", "a", "ch", "cd", "ca", "ahh", "aha", "cahh", "caha")
    if h.startswith(bookmaker_prefixes) and (h.endswith(market_suffixes) or "2.5" in h):
        return "market_reference"
    # Audit-derived fields that may exist in generated/processed ledgers.
    if h in {"resultconsistent", "totalgoals", "goaldifference", "totalclass", "exacttotal", "exactparity"}:
        return "derived_result_label"
    return "unclassified"


def scan_headers() -> dict[str, Any]:
    categories: dict[str, set[str]] = defaultdict(set)
    file_counts = Counter()
    files = sorted(PROCESSED.rglob("*.csv"))
    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                headers = next(reader, [])
        except Exception as exc:
            file_counts["errors"] += 1
            categories["read_error"].add(f"{path.relative_to(ROOT)}::{type(exc).__name__}")
            continue
        file_counts["files"] += 1
        for header in headers:
            categories[classify(header)].add(str(header))
    return {
        "counts": dict(file_counts),
        "categories": {k: sorted(v) for k, v in sorted(categories.items())},
    }


def run() -> dict[str, Any]:
    inventory = scan_headers()
    unclassified = inventory["categories"].get("unclassified", [])
    # Unknown columns are not automatically deemed usable; this audit forces explicit review.
    if unclassified:
        verdict = "UNCLASSIFIED_EXISTING_COLUMNS_REMAIN_REVIEW_REQUIRED"
    else:
        verdict = "NO_UNTESTED_EXISTING_PREMATCH_FIELD_FAMILY_FOUND_PARITY_GAP_REMAINS"

    family_status = {
        "stable_for_T": ["compact_single_OU"],
        "stable_for_conditional_draw_probability": ["compact_1x2_plus_AH_targeted_reweight"],
        "not_stable_for_parity": [
            "single_OU", "full_market_for_parity", "historical_score_parity_persistence",
            "lagged_shots_sot_corners", "referee_history",
        ],
        "rejected_due_harm_or_overfit": ["rich_OU", "lagged_match_stats", "historical_parity_persistence"],
        "oracle_diagnostic_only": ["true_parity", "true_exact_T"],
    }
    scientific_boundary = {
        "current_best_T_information": "compact single OU market reference",
        "current_best_conditional_GD_information": "compact 1X2 + AH targeted near-balance reweight",
        "current_unresolved_bottleneck": "pre-match prediction of T parity / routing",
        "historical_recency_or_persistence_features_exhausted": True,
        "no_scientifically_verified_existing_historical_parity_signal": True,
        "next_if_existing_data_only": "stop feature fishing; only structural synthesis/audit remains justified unless a genuinely unused pre-match field family is found",
        "next_if_new_state_information_allowed": "lineups/availability/team-news/weather/rest/referee assignments with true PIT capture or richer timestamped total-goal market state",
    }
    result = {
        "schema_version": "FIXED500_EXISTING_INFORMATION_CEILING_R9",
        "status": "COMPLETED_RESEARCH_GOVERNANCE_AUDIT",
        "scientific_verdict": verdict,
        "sample_anchor": {"fixed500_identity_sha256": FIXED500_SHA, "fixed500_n": 500, "same_market_cohort_n": 220},
        "processed_header_inventory": inventory,
        "experiment_evidence": EXPERIMENTS,
        "family_status": family_status,
        "scientific_boundary": scientific_boundary,
        "interpretation_guard": {
            "not_a_new_model_fit": True,
            "does_not_claim_mathematical_impossibility": True,
            "claim_scope": "tested existing repository field families and currently inventoried processed CSV columns",
            "historical_market_references_not_strict_PIT": True,
            "oracle_results_not_deployable": True,
            "no_threshold_search": True,
            "no_new_sample": True,
        },
        "governance": {
            "formal_weight": 0,
            "provider_requests": 0,
            "new_data_collection": False,
            "new_sample_consumed": False,
            "latest_position4_confirmation_opened": False,
            "formal_model_mutation": False,
            "formal_data_mutation": False,
            "formal_config_mutation": False,
            "current_mutation": False,
            "main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": verdict,
        "inventory_counts": inventory["counts"],
        "categories": {k: len(v) for k, v in inventory["categories"].items()},
        "unclassified": unclassified,
        "family_status": family_status,
        "boundary": scientific_boundary,
    }, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
