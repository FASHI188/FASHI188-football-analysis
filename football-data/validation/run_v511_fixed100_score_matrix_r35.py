#!/usr/bin/env python3
"""Execution wrapper for R35.

The fixed R35 sample is selected only from rows with complete O/U 2.5. Other
historical rows are still used for prequential score-model state updates. When
one of those non-evaluation rows lacks O/U, the O/U projection stage is skipped;
this cannot affect sample membership, labels, model updates, or the fixed
candidate behavior on the selected 100 rows.
"""
from __future__ import annotations

import evaluate_v511_fixed100_score_matrix_r35 as study

_original_candidate = study.candidate


def _candidate_with_history_fallback(prior, market, ou25, spec):
    if float(spec.get("ou_weight", 0.0)) > 0.0 and not ou25:
        spec = {**spec, "ou_weight": 0.0}
    return _original_candidate(prior, market, ou25, spec)


study.candidate = _candidate_with_history_fallback

if __name__ == "__main__":
    study.main()
