#!/usr/bin/env python3
"""Evaluate the second disjoint HF timestamped O/U fixed500.

This reuses the exact Direct-T model/evaluation shell from R1. Only the fixed
sample paths are changed to the deterministic SHA ranks 501-1000 cohort.
"""
from __future__ import annotations

import json
from pathlib import Path

import evaluate_hf_odds_fixed500_direct_t_r1 as base

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "manifests" / "hf_odds_pit_matchability_batch2_r1.json"
OUT = ROOT / "manifests" / "hf_odds_fixed500_batch2_r1.json"
ROWS_OUT = ROOT / "manifests" / "hf_odds_fixed500_batch2_r1_rows.csv"
T90 = ROOT / "manifests" / "hf_odds_pit_fixed500_t90_batch2_r1.csv"
T5 = ROOT / "manifests" / "hf_odds_pit_fixed500_t5_batch2_r1.csv"


def run() -> dict:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit["status"] != "PASS_DISJOINT_SECOND_FIXED500_READY":
        raise base.ResearchError(f"batch2 audit not ready: {audit['status']}")
    if audit["overlap_with_first_fixed500"]["t90"] != 0:
        raise base.ResearchError("batch2 T-90 overlaps first fixed500")
    if audit["overlap_with_first_fixed500"]["t5"] != 0:
        raise base.ResearchError("batch2 T-5 overlaps first fixed500")

    base.OUT = OUT
    base.ROWS_OUT = ROWS_OUT
    base.T90 = T90
    base.T5 = T5
    result = base.run()
    result["schema_version"] = "HF_ODDS_FIXED500_BATCH2_R1"
    result["classification"] = "RESEARCH_ONLY_DISJOINT_SECOND_FIXED500_SAME_SOURCE"
    result["sample"]["cohort"] = "same_file_sha_ranks_501_1000"
    result["sample"]["overlap_with_first_fixed500_t90"] = 0
    result["sample"]["overlap_with_first_fixed500_t5"] = 0
    result["sample"]["independent_of_first_batch_by_fixture_identity"] = True
    result["interpretation_guard"]["same_source_as_first_batch"] = True
    result["interpretation_guard"]["second_batch_not_used_for_tuning"] = True
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "scientific_verdict": x["scientific_verdict"],
        "sample": x["sample"],
        "primary_t90": x["primary_t90"],
        "sensitivity_t5": x["sensitivity_t5"],
        "governance": x["governance"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
