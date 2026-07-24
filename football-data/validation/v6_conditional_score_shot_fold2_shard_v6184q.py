#!/usr/bin/env python3
"""Execution shard for the already preregistered V6.18.4 second fold.

Not a new model or selection path. It runs only:
train 2022/23+2023/24 -> validate 2024/25 -> test 2025/26
for competition-control and shot arms, using the exact V6.18.4 C grid, validation hard
gates and selection rule. Rows come from V6.18.1c strict daily PIT. This shard may
provide an early accept/reject signal but cannot replace the full two-fold stability
receipt and cannot support promotion.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import v6_conditional_score_shot_challenge_v6184 as challenge
import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as datefix
import v6_strict_daily_pit_rows_v6181c as strict
from platform_core import derive_score_marginals

OUT = base.ROOT / "manifests" / "v6_conditional_score_shot_fold2_shard_v6184q_status.json"


def rows_strict():
    raw, _ = base.raw_stat_matches()
    lookup, _ = datefix.lagged_shot_lookup_fixed(raw)
    rows, meta = strict.strict_formal_score_rows(lookup)
    out = []
    for r in rows:
        x = dict(r)
        x["actual_total"] = int(x.pop("actual_total_raw"))
        out.append(x)
    return out, meta


def main() -> int:
    challenge.base.derive_score_marginals = derive_score_marginals
    rows, meta = rows_strict()
    shot_names, comps = challenge.shot_names_and_comps(rows)
    tr = ("2022/23", "2023/24")
    va = "2024/25"
    te = "2025/26"
    competition = challenge.fold(rows, tr, va, te, "competition", shot_names, comps)
    shot = challenge.fold(rows, tr, va, te, "shot", shot_names, comps)
    paired = None
    if competition.get("test_candidate") and shot.get("test_candidate"):
        paired = challenge.delta(shot["test_candidate"], competition["test_candidate"])
    payload = {
        "schema_version": "V6.18.4q-fold2-execution-shard-strict-daily-pit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "EXECUTION_SHARD_OF_PREREGISTERED_V6_18_4_NOT_NEW_SELECTION",
        "design": {
            "train_seasons": list(tr),
            "validation_season": va,
            "test_season": te,
            "modes": ["competition", "shot"],
            "candidate_C": list(challenge.CANDIDATE_C),
            "validation_gate_unchanged": True,
            "selection_rule_unchanged": True,
            "strict_daily_pit": True
        },
        "rows": len(rows),
        "competition": competition,
        "shot": shot,
        "shot_minus_competition": paired,
        "source_meta": meta,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "cannot_replace_full_v6184_two_fold_receipt": True,
            "cannot_support_promotion": True,
            "no_parameter_change_from_shard_result": True
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "competition_status": competition.get("status"),
        "competition_selected_C": (competition.get("selected") or {}).get("C"),
        "competition_test_delta": competition.get("test_delta"),
        "shot_status": shot.get("status"),
        "shot_selected_C": (shot.get("selected") or {}).get("C"),
        "shot_test_delta": shot.get("test_delta"),
        "shot_minus_competition": paired
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
