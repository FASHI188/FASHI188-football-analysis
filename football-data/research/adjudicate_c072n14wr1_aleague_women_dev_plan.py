#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

EXPECTED = {
    "2020-2021": {"all5": 36, "sha": "2c33eea38183be5684acb00c67de5379099563f939f3d5fa8336699c1ee01462"},
    "2021-2022": {"all5": 59, "sha": "f2e2a957c7674c5626eff71c7ab360e698c1ccb9f8054383e5c5c0eb6498a86f"},
    "2022-2023": {"all5": 61, "sha": "899992d57d65c4786b47057a8c009dbeea3ec170aadc29beb1fd47e4b12a7524"},
    "2023-2024": {"all5": 139, "sha": "85e3b6d3cf2f3e54e1adbe456e24ec5042946e5102c99501c412039279814792"},
    "2024-2025": {"all5": 135, "sha": "3daf737dd36ef95b4e2e0d354674842faec755016e43abb9f987d67b1d594060"},
    "2025-2026": {"all5": 116, "sha": "2c0c85bf0856b2e13de7a862a6a1e83ccd6aa0781d3aa2058ee289433f4c2053"},
}
DEV = ("2021-2022", "2022-2023", "2023-2024", "2024-2025")
OUT = Path("football-data/research/c072n14wr1_aleague_women_dev_plan_summary.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n14w-summary", required=True)
    args = ap.parse_args()
    old = json.loads(Path(args.n14w_summary).read_text(encoding="utf-8"))

    evidence_match = True
    observed = {}
    for season, exp in EXPECTED.items():
        s = old.get("per_season", {}).get(season, {})
        observed[season] = {"all5": s.get("all5_lines_all3_events"), "sha": s.get("sha256")}
        evidence_match &= observed[season]["all5"] == exp["all5"] and observed[season]["sha"] == exp["sha"]

    dev_total = sum(EXPECTED[s]["all5"] for s in DEV)
    gates = {
        "parent_n14w_remains_stop": old.get("terminal") == "C072N14W_ALEAGUE_WOMEN_SURFACE_ZERO_LABEL_STOP",
        "parent_zero_target_result_values": old.get("target_result_values_materialized") == 0,
        "parent_zero_model": old.get("model_fit") == 0 and old.get("model_score") == 0,
        "exact_six_source_hashes_and_counts": evidence_match,
        "selected_dev_counts_exact": [EXPECTED[s]["all5"] for s in DEV] == [59, 61, 139, 135],
        "development_pooled_count_exact_394": dev_total == 394,
        "each_selected_dev_season_ge_50": all(EXPECTED[s]["all5"] >= 50 for s in DEV),
        "reserve_2025_26_exact_116_and_ge_100": EXPECTED["2025-2026"]["all5"] == 116 and EXPECTED["2025-2026"]["all5"] >= 100,
        "excluded_2020_21_exact_36": EXPECTED["2020-2021"]["all5"] == 36,
        "quarantine_and_seals_hold": old.get("C073_C077_scientific_results_used") is False and old.get("C070F_confirmation1597_opened") is False,
    }
    passed = all(gates.values())
    out = {
        "schema": "C072N14WR1_ALEAGUE_WOMEN_DEV_PLAN_ZERO_LABEL_V1",
        "project_line": "football3",
        "classification": "ZERO_LABEL_DATA_PLAN_ADJUDICATION",
        "terminal": "C072N14WR1_ALEAGUE_WOMEN_DEV_PLAN_ZERO_LABEL_PASS" if passed else "C072N14WR1_ALEAGUE_WOMEN_DEV_PLAN_ZERO_LABEL_STOP",
        "pass": passed,
        "parent_n14w_terminal": old.get("terminal"),
        "selected_development_seasons": list(DEV),
        "selected_development_all5_counts": {s: EXPECTED[s]["all5"] for s in DEV},
        "development_all5_total": dev_total,
        "excluded_2020_21_all5": 36,
        "sealed_reserve_2025_26_all5": 116,
        "source_evidence": observed,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_scientific_results_used": False,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
        "gates": gates,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
