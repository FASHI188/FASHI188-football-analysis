#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_parent(path: Path):
    spec = importlib.util.spec_from_file_location("r39c_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import R39C parent")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_sha(obj) -> str:
    return sha_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registration", type=Path, required=True)
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--parent-code", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    reg = json.loads(args.registration.read_text(encoding="utf-8"))
    assert reg["status"] == "PRE_REGISTERED_IDENTITY_SELECTION_NO_LABEL_ACCESS"
    assert reg["hard_limits"]["winner_labels_allowed_in_this_stage"] is False
    assert reg["hard_limits"]["formal_weight"] == 0

    parent = load_parent(args.parent_code)
    score_names = {"score_home", "score_away"}
    for base in ("odds_series", "odds_series_b"):
        p = args.source_dir / f"{base}_no_scores.csv.gz"
        import gzip
        with gzip.open(p, "rt", encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f))
        assert not (score_names & set(header)), (p, score_names & set(header))
        assert header[:3] == ["match_id", "match_date", "match_time"]

    rows, _max_dt = parent.load_feature_rows(args.source_dir, {})
    start = datetime.fromisoformat(reg["eligibility"]["holdout_start"])
    training = [x for x in rows.values() if x["dt"] < start]
    holdout = [x for x in rows.values() if x["dt"] >= start]

    assert len(training) == reg["eligibility"]["expected_training_eligible_rows"], len(training)
    assert len(holdout) == reg["eligibility"]["expected_holdout_eligible_rows"], len(holdout)

    ordered = sorted(
        holdout,
        key=lambda x: sha_text(f'51139|{x["identity"]}')
    )
    old = ordered[0:100]
    new = ordered[100:200]
    assert len(old) == 100 and len(new) == 100
    old_ids = sorted(x["identity"] for x in old)
    new_ids = sorted(x["identity"] for x in new)
    old_sha = sha_text("\n".join(old_ids) + "\n")
    new_sha = sha_text("\n".join(new_ids) + "\n")
    assert old_sha == reg["parent_r39c"]["old_locked100_identity_set_sha256"], (
        old_sha, reg["parent_r39c"]["old_locked100_identity_set_sha256"]
    )
    assert not (set(old_ids) & set(new_ids))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "locked100_r39d.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["identity", "source_file", "match_id", "match_datetime"])
        for x in sorted(new, key=lambda z: (z["dt"], z["identity"])):
            w.writerow([x["identity"], x["source_file"], x["match_id"], x["dt"].isoformat()])

    receipt = {
        "schema_version": reg["schema_version"],
        "status": "LOCKED_R39D_SECOND_FIXED100_NO_LABELS",
        "registration_canonical_sha256": canonical_sha(reg),
        "training_eligible_rows": len(training),
        "holdout_eligible_rows": len(holdout),
        "old_r39c_locked_rows": len(old),
        "old_r39c_identity_set_sha256": old_sha,
        "new_r39d_locked_rows": len(new),
        "new_r39d_identity_set_sha256": new_sha,
        "old_new_overlap": 0,
        "new_locked100_csv_sha256": sha_file(csv_path),
        "selection": reg["selection"],
        "no_label_audit": {
            "python_input_score_columns_present": False,
            "original_score_files_opened_by_lock_script": 0,
            "score_values_accessed": 0,
            "result_values_accessed": 0,
            "prediction_metrics_computed": 0,
            "model_fits": 0,
            "thresholds_selected": 0
        },
        "hard_limits": reg["hard_limits"]
    }
    receipt_path = args.out_dir / "identity_lock_receipt_r39d.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
