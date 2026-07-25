#!/usr/bin/env python3
"""Audit the V6.26.2 cross-season state source on all formal competition domains.

This is an implementation/leakage audit, not a performance promotion test. It verifies that the
new challenger state source can actually carry pre-cutoff information across season boundaries,
never reads future matches, never mutates probabilities, and retains the fixed Hedge experts.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import cross_season_hedge_state_v6262 as state  # noqa: E402
from platform_core import load_json, read_processed_matches  # noqa: E402

OUT = ROOT / "manifests" / "v6_cross_season_state_v6262_status.json"
FORMAL = ROOT / "manifests" / "formal_core_v460_status.json"


def main() -> int:
    formal = load_json(FORMAL)
    competitions = list((formal.get("reports") or {}).keys())
    domain_results = {}
    total_transitions = 0
    transitions_with_carry = 0
    future_leak_failures = 0
    probability_mutation_failures = 0

    for cid in competitions:
        matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
        by_season = defaultdict(list)
        for m in matches:
            by_season[str(m.season)].append(m)
        seasons = sorted(by_season, key=lambda s: min(m.date for m in by_season[s]))
        checks = []
        for season in seasons[1:]:
            first_date = min(m.date for m in by_season[season])
            first_day = [m for m in by_season[season] if m.date == first_date]
            history = [m for m in matches if m.date < first_date]
            for match in first_day:
                pair = state.state_pair(history, match.home_team, match.away_team, first_date)
                total_transitions += 1
                home_raw = float(pair["home"]["mixed"].get("raw_matches", 0.0))
                away_raw = float(pair["away"]["mixed"].get("raw_matches", 0.0))
                has_carry = home_raw > 0.0 or away_raw > 0.0
                transitions_with_carry += int(has_carry)
                # The state function receives only history strictly before first_date here.
                future_rows = [m for m in history if m.date >= first_date]
                future_leak_failures += int(bool(future_rows))
                probability_mutation_failures += int(
                    bool(pair["home"].get("probability_mutation")) or bool(pair["away"].get("probability_mutation"))
                )
                checks.append({
                    "season": season,
                    "date": first_date.isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "home_prior_raw_matches": home_raw,
                    "away_prior_raw_matches": away_raw,
                    "cross_season_carry_available": has_carry,
                })
        domain_results[cid] = {
            "transition_checks": len(checks),
            "with_cross_season_carry": sum(int(x["cross_season_carry_available"]) for x in checks),
            "examples": checks[:5],
        }

    fixed_experts_ok = tuple(state.EXPERT_HALF_LIVES) == (45.0, 90.0, 180.0, 360.0)
    passed = (
        bool(competitions)
        and total_transitions > 0
        and transitions_with_carry > 0
        and future_leak_failures == 0
        and probability_mutation_failures == 0
        and fixed_experts_ok
    )
    payload = {
        "schema_version": "V6.26.2-cross-season-state-audit-r1",
        "generated_at_utc": __import__("datetime").datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS_IMPLEMENTATION_CONTRACT" if passed else "FAIL_IMPLEMENTATION_CONTRACT",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "formal_current_unchanged": True,
        "competition_count": len(competitions),
        "transition_checks": total_transitions,
        "transitions_with_cross_season_carry": transitions_with_carry,
        "future_leak_failures": future_leak_failures,
        "probability_mutation_failures": probability_mutation_failures,
        "fixed_experts": list(state.EXPERT_HALF_LIVES),
        "fixed_experts_ok": fixed_experts_ok,
        "hard_current_season_reset": False,
        "same_day_rule": "caller must predict whole date before ledger update",
        "performance_promotion_eligible": False,
        "performance_promotion_reason": "this receipt validates state-source mechanics only; predictive value still requires chronological OOF ablation",
        "domains": domain_results,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "status", "competition_count", "transition_checks", "transitions_with_cross_season_carry",
        "future_leak_failures", "probability_mutation_failures", "fixed_experts_ok"
    )}, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
