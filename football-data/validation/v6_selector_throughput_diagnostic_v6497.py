#!/usr/bin/env python3
"""V6.49.7 selector throughput diagnostic.

Explains current V6.49.2 SELECT/ABSTAIN throughput without changing the frozen 0.55
selector threshold. It separates trained-domain threshold abstentions from unseen-domain
forced abstentions and compares the current trained-domain mix with the immutable
fixed1000 historical selection rates. V6.49.6 shadow selections remain diagnostic only.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "manifests"
QUEUE = M / "v6_context_priority_queue_v6493_status.json"
HIST_SELECTOR = M / "v6_hierarchical_market_selector_v6474_status.json"
FIXED1000 = M / "v6_1x2_fixed1000_v6130_status.json"
SHADOW = M / "v6_unseen_domain_shadow_v6496_status.json"
OUT = M / "v6_selector_throughput_diagnostic_v6497_status.json"


def load(path: Path) -> dict[str, Any]:
    x = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(x, dict):
        raise RuntimeError(f"not object: {path}")
    return x


def main() -> int:
    q = load(QUEUE)
    h = load(HIST_SELECTOR)
    f = load(FIXED1000)
    s = load(SHADOW)
    if q.get("status") != "PASS" or s.get("status") != "PASS":
        raise RuntimeError("current queue or unseen-domain shadow not PASS")

    rows = [r for r in (q.get("queue") or []) if isinstance(r, dict)]
    trained = set(str(x) for x in ((s.get("freeze") or {}).get("trained_domains") or []))
    unseen = set(str(x) for x in ((s.get("freeze") or {}).get("unseen_target_domains") or []))
    active_selected = [r for r in rows if r.get("selector_selected") is True]
    trained_rows = [r for r in rows if str(r.get("competition_id") or "") in trained]
    unseen_rows = [r for r in rows if str(r.get("competition_id") or "") in unseen]
    trained_abstain = [r for r in trained_rows if r.get("selector_selected") is not True]

    hist_metrics = ((h.get("fixed1000_test") or {}).get("metrics") or {})
    hist_selected_by_comp = {str(k): int(v) for k, v in (hist_metrics.get("by_competition_n") or {}).items()}
    fixed_by_comp = f.get("by_competition") or {}
    current_by_comp = Counter(str(r.get("competition_id") or "") for r in rows)
    current_trained_by_comp = Counter(str(r.get("competition_id") or "") for r in trained_rows)

    expected_selected = 0.0
    expected_components: dict[str, Any] = {}
    missing_hist_rate: list[str] = []
    for cid, current_n in sorted(current_trained_by_comp.items()):
        hist_pop = int(((fixed_by_comp.get(cid) or {}).get("count")) or 0)
        hist_sel = int(hist_selected_by_comp.get(cid, 0))
        if hist_pop <= 0:
            missing_hist_rate.append(cid)
            continue
        rate = hist_sel / hist_pop
        contribution = current_n * rate
        expected_selected += contribution
        expected_components[cid] = {
            "current_n": current_n,
            "fixed1000_population_n": hist_pop,
            "fixed1000_selected_n": hist_sel,
            "fixed1000_selected_rate": rate,
            "expected_current_selected_contribution": contribution,
        }

    shadow_pop = s.get("population") or {}
    shadow_selected = int(shadow_pop.get("shadow_selected_count") or 0)
    potential_selected_if_shadow_eventually_passes = len(active_selected) + shadow_selected
    current_n = len(rows)
    hist_coverage = float(hist_metrics.get("coverage") or 0.0)
    potential_coverage = potential_selected_if_shadow_eventually_passes / current_n if current_n else 0.0

    selected_by_comp = Counter(str(r.get("competition_id") or "") for r in active_selected)
    selected_by_direction = Counter(str(r.get("selector_pick") or "") for r in active_selected)
    selected_detail = [
        {
            "competition_id": r.get("competition_id"),
            "kickoff_at": r.get("kickoff_at"),
            "home_team": r.get("home_team"),
            "away_team": r.get("away_team"),
            "pick": r.get("selector_pick"),
            "pmax": r.get("selector_pmax"),
            "reliability": r.get("selector_reliability"),
        }
        for r in active_selected
    ]

    checks = {
        "queue_status_pass": q.get("status") == "PASS",
        "row_count_matches_future_fixture_count": current_n == int(q.get("future_fixture_count") or 0),
        "partition_complete": len(active_selected) + len(trained_abstain) + len(unseen_rows) == current_n,
        "all_unseen_rows_are_active_abstain": all(r.get("selector_selected") is not True for r in unseen_rows),
        "shadow_population_matches_current_unseen_rows": int(shadow_pop.get("eligible_unseen_prediction_count") or 0) == len(unseen_rows),
        "historical_rates_available_for_current_trained_domains": not missing_hist_rate,
        "no_probability_or_selection_mutation": True,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "V6.49.7-selector-throughput-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "formal_current_version": "V5.0.1",
        "status": status,
        "current_batch": {
            "fixture_count": current_n,
            "active_selected": len(active_selected),
            "active_selected_rate": len(active_selected) / current_n if current_n else 0.0,
            "trained_domain_fixture_count": len(trained_rows),
            "trained_domain_selected": len(active_selected),
            "trained_domain_selected_rate": len(active_selected) / len(trained_rows) if trained_rows else 0.0,
            "trained_domain_threshold_abstain": len(trained_abstain),
            "unseen_domain_forced_abstain": len(unseen_rows),
            "unseen_domain_share": len(unseen_rows) / current_n if current_n else 0.0,
            "by_competition": dict(sorted(current_by_comp.items())),
            "active_selected_by_competition": dict(sorted(selected_by_comp.items())),
            "active_selected_by_direction": dict(sorted(selected_by_direction.items())),
            "active_selected_detail": selected_detail,
        },
        "historical_mix_check": {
            "fixed1000_selector_coverage": hist_coverage,
            "expected_selected_given_current_trained_domain_mix": expected_selected,
            "observed_selected_in_current_trained_domains": len(active_selected),
            "observed_minus_expected": len(active_selected) - expected_selected,
            "observed_over_expected": (len(active_selected) / expected_selected) if expected_selected > 0 else None,
            "components": expected_components,
            "missing_historical_rate_domains": missing_hist_rate,
        },
        "unseen_domain_shadow_context": {
            "shadow_population": int(shadow_pop.get("eligible_unseen_prediction_count") or 0),
            "shadow_selected": shadow_selected,
            "shadow_selected_rate": shadow_selected / int(shadow_pop.get("eligible_unseen_prediction_count") or 1),
            "potential_selected_if_shadow_transfer_eventually_passes": potential_selected_if_shadow_eventually_passes,
            "potential_coverage_if_shadow_transfer_eventually_passes": potential_coverage,
            "historical_fixed1000_selector_coverage": hist_coverage,
            "potential_minus_historical_coverage": potential_coverage - hist_coverage,
            "active_effect_now": "NONE_SHADOW_ONLY",
        },
        "audit": {"checks": checks},
        "interpretation": (
            "Current low total selected rate is decomposed into trained-domain threshold abstention and unseen-domain forced abstention. "
            "No threshold change is justified merely by low current total coverage; trained-domain observed selection should be compared with its frozen historical domain-mix expectation."
        ),
        "governance": {
            "diagnostic_only": True,
            "selector_threshold_change": False,
            "active_selection_change": False,
            "probability_mutation": False,
            "shadow_promotion": False,
            "formal_weight": 0,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "current_batch": payload["current_batch"], "historical_mix_check": payload["historical_mix_check"], "shadow": payload["unseen_domain_shadow_context"]}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
