#!/usr/bin/env python3
"""Correct V4.6.2 grade345 fold semantics after the staged migration.

The prior migration split one unseen season into two reporting blocks and counted
them as outer folds without re-selecting before each block.  That is useful for
subperiod diagnostics but is not a valid substitute for independent expanding-
window outer folds.  Restore the formal-core report to one fold per unseen season;
V4.6.3 rolling_outer_validation_v463.py supplies the corrected re-selected rolling
fold evidence separately.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    path = ROOT / "validation" / "nested_backtest_v460.py"
    text = path.read_text(encoding="utf-8")
    split_block = '''        if model_records:
            outer_records.extend(model_records)
            outer_baseline.extend(baseline_records)
            for block in _split_outer_time_blocks(model_records, baseline_records, blocks=2):
                fold_details.append({
                    "outer_fold_id": f"{outer_season}:T{int(block['block_index']) + 1}",
                    "outer_season": outer_season,
                    "prior_seasons": prior_seasons,
                    "selection_predictions": selection_count,
                    "selected_candidate_index": selected_index,
                    "selected_parameters": selected_candidate,
                    "test_start_date": block["test_start_date"],
                    "test_end_date": block["test_end_date"],
                    "outer_predictions": len(block["model_records"]),
                    "model_metrics": _aggregate(block["model_records"]),
                    "baseline_metrics": _aggregate(block["baseline_records"]),
                })
'''
    season_fold = '''        if model_records:
            outer_records.extend(model_records)
            outer_baseline.extend(baseline_records)
            fold_details.append({
                "outer_season": outer_season,
                "prior_seasons": prior_seasons,
                "selection_predictions": selection_count,
                "selected_candidate_index": selected_index,
                "selected_parameters": selected_candidate,
                "outer_predictions": len(model_records),
                "model_metrics": _aggregate(model_records),
                "baseline_metrics": _aggregate(baseline_records),
            })
'''
    if split_block in text:
        text = text.replace(split_block, season_fold, 1)
    elif '"outer_fold_id": f"{outer_season}:T' in text:
        raise RuntimeError("unexpected split-fold implementation; refusing silent patch")
    path.write_text(text, encoding="utf-8")
    print("Restored season-level formal-core fold counting; corrected rolling folds live in V4.6.3 evidence reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
