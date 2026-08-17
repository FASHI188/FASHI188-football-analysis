#!/usr/bin/env python3
"""Build a second disjoint fixed500 from the same HF 2023/24 odds file.

Selection is identical to audit_hf_odds_pit_matchability_r2 except that this
cohort takes deterministic SHA-256 ranks 501-1000 (one-based) among eligible
fixtures. Result labels are never used for identity selection.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import audit_hf_odds_pit_matchability_r2 as base

OUT = Path("football-data/manifests/hf_odds_pit_matchability_batch2_r1.json")
OUT90 = Path("football-data/manifests/hf_odds_pit_fixed500_t90_batch2_r1.csv")
OUT5 = Path("football-data/manifests/hf_odds_pit_fixed500_t5_batch2_r1.csv")
START = 500
STOP = 1000


def identity(rec: dict) -> tuple[str, str, str, str]:
    return (
        str(rec["league_id"]),
        rec["kickoff_utc"].isoformat(),
        str(rec["home"]),
        str(rec["away"]),
    )


def write_slice(path: Path, arr: list, freeze_minutes: int) -> int:
    selected = arr[START:STOP]
    if len(selected) != 500:
        return len(selected)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "sha256_rank", "league_id", "kickoff_utc", "home_team", "away_team",
        "fthg", "ftag", "total_goals", "freeze_minutes", "quote_utc",
        "quote_age_to_cutoff_min", "odds_over_2.5", "odds_under_2.5",
        "fair_over_2.5", "fair_under_2.5",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for sha, rec, snap in selected:
            io = 1 / snap["over"]
            iu = 1 / snap["under"]
            den = io + iu
            w.writerow({
                "sha256_rank": sha,
                "league_id": rec["league_id"],
                "kickoff_utc": rec["kickoff_utc"].isoformat(),
                "home_team": rec["home"],
                "away_team": rec["away"],
                "fthg": rec["fthg"],
                "ftag": rec["ftag"],
                "total_goals": rec["fthg"] + rec["ftag"],
                "freeze_minutes": freeze_minutes,
                "quote_utc": snap["ts"].isoformat(),
                "quote_age_to_cutoff_min": round(snap["age_min"], 3),
                "odds_over_2.5": snap["over"],
                "odds_under_2.5": snap["under"],
                "fair_over_2.5": io / den,
                "fair_under_2.5": iu / den,
            })
    return len(selected)


def run() -> dict:
    if len(base.c90) < STOP or len(base.c5) < STOP:
        raise SystemExit(f"not enough eligible rows for ranks 501-1000: t90={len(base.c90)} t5={len(base.c5)}")

    first90 = {identity(rec) for _, rec, _ in base.c90[:START]}
    second90 = {identity(rec) for _, rec, _ in base.c90[START:STOP]}
    first5 = {identity(rec) for _, rec, _ in base.c5[:START]}
    second5 = {identity(rec) for _, rec, _ in base.c5[START:STOP]}
    overlap90 = len(first90 & second90)
    overlap5 = len(first5 & second5)
    if overlap90 or overlap5:
        raise SystemExit(f"batch overlap failure: t90={overlap90} t5={overlap5}")

    n90 = write_slice(OUT90, base.c90, 90)
    n5 = write_slice(OUT5, base.c5, 5)
    if n90 != 500 or n5 != 500:
        raise SystemExit(f"second fixed500 incomplete: t90={n90} t5={n5}")

    summary = {
        "schema_version": "hf-odds-pit-fixed500-batch2-r1",
        "status": "PASS_DISJOINT_SECOND_FIXED500_READY",
        "source_dataset": "fabul0us/football_odds_2023-24:match_odds.csv",
        "eligible_candidates": {"t90": len(base.c90), "t5": len(base.c5)},
        "rank_slice_zero_based": [START, STOP - 1],
        "rank_slice_one_based": [START + 1, STOP],
        "selected": {"t90": n90, "t5": n5},
        "overlap_with_first_fixed500": {"t90": overlap90, "t5": overlap5},
        "selection_guard": {
            "result_labels_used_for_selection": False,
            "all_quotes_at_or_before_freeze": True,
            "max_quote_age_to_cutoff_minutes": 1440,
            "deterministic_sha256_ordering": True,
            "same_selection_rule_as_first_batch": True,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "main_mutation": False,
            "formal_model_mutation": False,
            "current_mutation": False,
        },
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    run()
