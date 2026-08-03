#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

R2_DIR = Path(__file__).resolve().parents[1] / "draw_lineup_quality_r2"
sys.path.insert(0, str(R2_DIR))
import run_epl_lineup_quality_r2 as engine  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def expanded_fields(bases: list[str]) -> list[str]:
    return [f"{side}_{base}" for base in bases for side in ("home", "away")] + [f"diff_{base}" for base in bases]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--r2-prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    audit_spec = json.loads(args.audit.read_text(encoding="utf-8"))
    r2_prereg = json.loads(args.r2_prereg.read_text(encoding="utf-8"))
    ledger: list[dict[str, object]] = []
    markets = engine.load_markets(ledger)
    dataset, dataset_audit = engine.build_dataset(ledger, markets)
    if len(dataset) < 1_000:
        raise RuntimeError(f"insufficient audited rows: {len(dataset)}")

    all_quality = [feature for feature in engine.QUALITY_FEATURES]
    family_features: dict[str, list[str]] = {}
    for family, bases in audit_spec["families"].items():
        family_features[family] = all_quality if family == "all" else expanded_fields(bases)

    season_order = list(engine.SEASONS)
    fold_metrics: list[dict[str, object]] = []
    oof_scores: list[dict[str, object]] = []
    for target in audit_spec["target_seasons"]:
        target_index = season_order.index(target)
        train_seasons = set(season_order[:target_index])
        train = [row for row in dataset if str(row["season"]) in train_seasons]
        test = [row for row in dataset if str(row["season"]) == target]
        y_train = np.asarray([int(row["label_draw"]) for row in train])
        y_test = np.asarray([int(row["label_draw"]) for row in test])
        for model_name in audit_spec["models"]:
            market_model = engine.model_for(model_name, r2_prereg)
            market_model.fit(engine.matrix(train, engine.MARKET_FEATURES), y_train)
            market_score = market_model.predict_proba(engine.matrix(test, engine.MARKET_FEATURES))[:, 1]
            market_metrics = engine.metrics(y_test, market_score)
            for family, quality_fields in family_features.items():
                model = engine.model_for(model_name, r2_prereg)
                features = engine.MARKET_FEATURES + quality_fields
                model.fit(engine.matrix(train, features), y_train)
                score = model.predict_proba(engine.matrix(test, features))[:, 1]
                result = engine.metrics(y_test, score)
                fold_metrics.append({
                    "target_season": target,
                    "train_seasons": ";".join(sorted(train_seasons)),
                    "model": model_name,
                    "family": family,
                    "rows": len(test),
                    "draws": int(y_test.sum()),
                    "market_pr_auc": market_metrics["pr_auc"],
                    "family_pr_auc": result["pr_auc"],
                    "pr_auc_increment": result["pr_auc"] - market_metrics["pr_auc"],
                    "market_roc_auc": market_metrics["roc_auc"],
                    "family_roc_auc": result["roc_auc"],
                    "roc_auc_increment": result["roc_auc"] - market_metrics["roc_auc"],
                    "market_log_loss": market_metrics["log_loss"],
                    "family_log_loss": result["log_loss"],
                    "log_loss_increment": result["log_loss"] - market_metrics["log_loss"],
                    "market_brier": market_metrics["brier"],
                    "family_brier": result["brier"],
                    "brier_increment": result["brier"] - market_metrics["brier"],
                })
                for index, row in enumerate(test):
                    oof_scores.append({
                        "target_season": target,
                        "fixture": row["fixture"],
                        "date": row["date"],
                        "home_team": row["home_team"],
                        "away_team": row["away_team"],
                        "label_draw": row["label_draw"],
                        "model": model_name,
                        "family": family,
                        "market_score": float(market_score[index]),
                        "family_score": float(score[index]),
                    })

    filter_spec = audit_spec["forward_challenger_filter"]
    candidates: list[dict[str, object]] = []
    for model_name in audit_spec["models"]:
        for family in family_features:
            rows = [row for row in fold_metrics if row["model"] == model_name and row["family"] == family]
            pr = [float(row["pr_auc_increment"]) for row in rows]
            roc = [float(row["roc_auc_increment"]) for row in rows]
            ll = [float(row["log_loss_increment"]) for row in rows]
            br = [float(row["brier_increment"]) for row in rows]
            checks = {
                "positive_pr_auc_folds": sum(value > 0 for value in pr) >= int(filter_spec["positive_pr_auc_folds"]),
                "positive_roc_auc_folds": sum(value > 0 for value in roc) >= int(filter_spec["positive_roc_auc_folds"]),
                "median_pr_auc_increment": float(np.median(pr)) >= float(filter_spec["minimum_median_pr_auc_increment"]),
                "worst_pr_auc_increment": min(pr) >= float(filter_spec["minimum_worst_pr_auc_increment"]),
                "median_log_loss_increment": float(np.median(ll)) <= float(filter_spec["maximum_median_log_loss_increment"]),
                "median_brier_increment": float(np.median(br)) <= float(filter_spec["maximum_median_brier_increment"]),
            }
            candidates.append({
                "model": model_name,
                "family": family,
                "folds": rows,
                "median_pr_auc_increment": float(np.median(pr)),
                "worst_pr_auc_increment": min(pr),
                "median_roc_auc_increment": float(np.median(roc)),
                "median_log_loss_increment": float(np.median(ll)),
                "median_brier_increment": float(np.median(br)),
                "checks": checks,
                "forward_challenger_eligible": all(checks.values()),
            })
    eligible = [row for row in candidates if row["forward_challenger_eligible"]]
    eligible.sort(key=lambda row: (row["median_pr_auc_increment"], row["worst_pr_auc_increment"], row["median_roc_auc_increment"]), reverse=True)
    winner = eligible[0] if eligible else None
    thresholds: list[dict[str, float]] = []
    if winner:
        scores = np.asarray([
            float(row["family_score"]) for row in oof_scores
            if row["model"] == winner["model"] and row["family"] == winner["family"]
        ])
        for coverage in audit_spec["forward_threshold_coverages"]:
            thresholds.append({
                "coverage": float(coverage),
                "threshold": float(np.quantile(scores, 1.0 - float(coverage))),
            })

    result = {
        "schema_version": "EPL-LINEUP-QUALITY-COMPONENT-RESULT-R3",
        "status": "POST_HOC_FORWARD_CHALLENGER_FROZEN" if winner else "NO_COMPONENT_FORWARD_CHALLENGER",
        "post_hoc_historical_discovery": True,
        "independent_historical_validation": False,
        "candidates": candidates,
        "winner": winner,
        "forward_thresholds": thresholds,
        "formal_weight": 0,
        "promotion_allowed": False,
        "minimum_new_2026_27_test_matches": 200,
        "minimum_new_2026_27_draws": 40,
    }
    write_csv(args.out / "EPL_LINEUP_QUALITY_COMPONENT_R3_fold_metrics.csv", fold_metrics)
    write_csv(args.out / "EPL_LINEUP_QUALITY_COMPONENT_R3_oof_scores.csv", oof_scores)
    write_csv(args.out / "EPL_LINEUP_QUALITY_COMPONENT_R3_source_ledger.csv", ledger)
    (args.out / "EPL_LINEUP_QUALITY_COMPONENT_R3_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": "EPL-LINEUP-QUALITY-COMPONENT-R3",
        "status": result["status"],
        "audit_spec_sha256": sha256(args.audit.read_bytes()),
        "r2_prereg_sha256": sha256(args.r2_prereg.read_bytes()),
        "dataset_rows": len(dataset),
        "dataset_audit": dataset_audit,
        "fold_metric_rows": len(fold_metrics),
        "oof_score_rows": len(oof_scores),
        "source_downloads": len(ledger),
        "historical_independent_claim_allowed": False,
        "2026_27_forward_required": True,
        "formal_weight": 0,
        "formal_model_data_config_current_writes": [0, 0, 0, 0],
    }
    (args.out / "EPL_LINEUP_QUALITY_COMPONENT_R3_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "EPL-LINEUP-QUALITY-COMPONENT-ARTIFACT-R3", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "artifact_manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": sha256(data), "bytes": len(data)}
    (args.out / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "winner": {"model": winner["model"], "family": winner["family"]} if winner else None,
        "dataset_rows": len(dataset),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
