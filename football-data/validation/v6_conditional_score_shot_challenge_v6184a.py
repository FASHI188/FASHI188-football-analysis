#!/usr/bin/env python3
"""Pre-score strict-PIT/runtime-reference wrapper for V6.18.4.

No V6.18.4 scored receipt existed when these repairs were added. Repairs only:
1) replace formal row construction with V6.18.1c strict calendar-day PIT;
2) bind platform_core.derive_score_marginals for the audit helper, because the imported
   V6.18.1 base module does not export that symbol.

Model family, features, C grid, folds, validation gates and selection objective are
unchanged.
"""
from __future__ import annotations

import json

import v6_conditional_score_shot_challenge_v6184 as challenge
import v6_strict_daily_pit_rows_v6181c as strict
from platform_core import derive_score_marginals


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
    # Repair runtime reference only; this is the same formal marginal function used by V5.
    challenge.base.derive_score_marginals = derive_score_marginals
    code = challenge.main()
    path = challenge.OUT
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "V6.18.4-shot-conditional-score-strict-daily-pit-r3"
        payload["pre_score_repairs"] = {
            "same_date_formal_history_frozen": True,
            "same_date_warmup_counts_frozen": True,
            "marginal_audit_reference_repaired": True,
            "parameter_changes": False,
            "feature_changes": False,
            "candidate_grid_changes": False,
            "selection_gate_changes": False,
            "pre_repair_scored_receipt_existed": False
        }
        payload["governance"]["pre_strict_or_pre_reference_fix_v6184_inference_forbidden"] = True
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
