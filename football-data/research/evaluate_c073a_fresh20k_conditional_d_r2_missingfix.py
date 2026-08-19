#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

import evaluate_c073a_fresh20k_conditional_d as core

_projection_audit = {
    "unique_selected_ids_projected": 0,
    "completed_nonnull_rows": 0,
    "null_result_rows": 0,
}


def read_same_selected_labels_no_replacement(path: Path, selected_ids: list[int]) -> pd.DataFrame:
    """Re-read only the already-consumed 20k IDs; drop null outcomes with zero replacement."""
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=["id", "goals_home", "goals_away"],
        filter=ds.field("id").isin([int(x) for x in selected_ids]),
    )
    out = table.to_pandas()
    out["id"] = out["id"].astype("int64")
    if out["id"].duplicated().any():
        raise RuntimeError("duplicate selected confirmation label id")
    returned = set(int(x) for x in out["id"])
    expected = set(int(x) for x in selected_ids)
    if returned != expected or len(out) != core.SELECT_N:
        raise RuntimeError(f"selected label projection mismatch returned={len(out)} expected={core.SELECT_N}")
    completed = out.dropna(subset=["goals_home", "goals_away"]).copy()
    _projection_audit["unique_selected_ids_projected"] = int(len(out))
    _projection_audit["completed_nonnull_rows"] = int(len(completed))
    _projection_audit["null_result_rows"] = int(len(out) - len(completed))
    return completed


core.read_selected_labels = read_same_selected_labels_no_replacement


def main() -> int:
    rc = core.main()
    # Core writes a summary after scoring the completed subset. Rewrite only the
    # label-accounting fields to reflect that all 20k target columns were projected,
    # while null rows were excluded with zero replacement. Scientific metrics/gates
    # are left byte-for-byte as produced by the frozen core evaluator.
    import sys
    args = sys.argv[1:]
    try:
        out_dir = Path(args[args.index("--out-dir") + 1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("--out-dir required for R2 accounting rewrite") from exc
    path = out_dir / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["schema_version"] = "C073A_FRESH20K_CONDITIONAL_D_V1_R2_MISSING_RESULT_ADJUDICATED"
    summary["label_boundary"]["selected_confirmation_goal_rows_returned"] = _projection_audit["unique_selected_ids_projected"]
    summary["label_boundary"]["selected_confirmation_completed_nonnull_rows"] = _projection_audit["completed_nonnull_rows"]
    summary["label_boundary"]["selected_confirmation_null_result_rows"] = _projection_audit["null_result_rows"]
    summary["label_boundary"]["null_result_replacement_count"] = 0
    summary["label_boundary"]["unique_confirmation_ids_ever_target_label_projected"] = core.SELECT_N
    summary["label_boundary"]["missing_result_adjudication"] = "same 20000 consumed IDs only; null rows excluded; no reserve replacement"
    summary["boundaries"]["remaining_52180_labels_opened"] = False
    summary["boundaries"]["sample_reselection_after_label_read"] = False
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "r2_accounting": _projection_audit,
        "scientific_status": summary["status"],
        "primary_n": summary["primary"]["n"],
        "gate": summary["gate"],
    }, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
