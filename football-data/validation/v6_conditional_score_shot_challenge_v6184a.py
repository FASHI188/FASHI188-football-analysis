#!/usr/bin/env python3
"""Pre-score strict-PIT wrapper for V6.18.4.

No V6.18.4 scored receipt existed when this wrapper was added. It replaces only the
formal row-construction routine with V6.18.1c's strict calendar-day freeze. Model
family, features, C grid, folds, validation gates, and selection objective are unchanged.
"""
from __future__ import annotations

import json

import v6_conditional_score_shot_challenge_v6184 as challenge
import v6_strict_daily_pit_rows_v6181c as strict


def strict_build_rows(shot_lookup):
    rows, meta = strict.strict_formal_score_rows(shot_lookup)
    converted = []
    for r in rows:
        x = dict(r)
        x["actual_total"] = int(x.pop("actual_total_raw"))
        converted.append(x)
    return converted, meta


def main() -> int:
    challenge.build_rows = strict_build_rows
    code = challenge.main()
    path = challenge.OUT
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "V6.18.4-shot-conditional-score-strict-daily-pit-r2"
        payload["strict_pit_repair"] = {
            "same_date_formal_history_frozen": True,
            "same_date_warmup_counts_frozen": True,
            "parameter_changes": False,
            "feature_changes": False,
            "candidate_grid_changes": False,
            "selection_gate_changes": False,
            "pre_repair_scored_receipt_existed": False
        }
        payload["governance"]["pre_strict_v6184_inference_forbidden"] = True
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
