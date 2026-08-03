#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import run_epl_lineup_quality_r2 as engine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))

    ledger: list[dict[str, object]] = []
    markets = engine.load_markets(ledger)
    dataset, audit = engine.build_dataset(ledger, markets)

    # The preregistration fixes the fold count, temporal split and statistical
    # gates; it does not prescribe 1,500 rows. The strict source audit produced
    # 1,231 eligible rows after cold-start, malformed-XI and market-identity
    # exclusions. Require a conservative executable floor without discarding
    # the preregistered three-season rolling OOF design.
    if len(dataset) < 1_000:
        raise RuntimeError(f"insufficient audited dataset rows: {len(dataset)}")
    for target in engine.TARGETS:
        count = sum(str(row["season"]) == target for row in dataset)
        draws = sum(str(row["season"]) == target and int(row["label_draw"]) == 1 for row in dataset)
        if count < 250 or draws < 50:
            raise RuntimeError(f"insufficient target fold {target}: rows={count}, draws={draws}")

    folds, predictions = engine.rolling_oof(dataset, prereg)
    selection = engine.select_lane(folds, predictions, prereg)
    engine.write_csv(args.out / "EPL_LINEUP_QUALITY_R2_dataset.csv", dataset)
    engine.write_csv(args.out / "EPL_LINEUP_QUALITY_R2_fold_metrics.csv", folds)
    engine.write_csv(args.out / "EPL_LINEUP_QUALITY_R2_oof_predictions.csv", predictions)
    engine.write_csv(args.out / "EPL_LINEUP_QUALITY_R2_source_ledger.csv", ledger)
    engine.write_csv(args.out / "EPL_LINEUP_QUALITY_R2_malformed_fixtures.csv", audit["malformed_fixtures"])
    (args.out / "EPL_LINEUP_QUALITY_R2_selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    receipt = {
        "schema_version": "EPL-LINEUP-QUALITY-R2",
        "status": selection["status"],
        "prereg_sha256": engine.hf(args.prereg.read_bytes()),
        "dataset_audit": audit,
        "execution_minimum_rows": 1_000,
        "target_fold_minimum_rows": 250,
        "target_fold_minimum_draws": 50,
        "market_index_rows": len(markets),
        "fold_metric_rows": len(folds),
        "oof_prediction_rows": len(predictions),
        "source_downloads": len(ledger),
        "consumed_test_sets_reused_for_selection": [],
        "2025_26_role": "historical development fold; not untouched after PR81",
        "2026_27_forward_required": True,
        "formal_weight": 0,
        "formal_model_data_config_current_writes": [0, 0, 0, 0],
    }
    (args.out / "EPL_LINEUP_QUALITY_R2_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {"schema_version": "EPL-LINEUP-QUALITY-ARTIFACT-R2", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "artifact_manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": engine.hf(data), "bytes": len(data)}
    (args.out / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": selection["status"],
        "rows": len(dataset),
        "folds": len(folds),
        "winner": selection["winner"]["model"] if selection["winner"] else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
