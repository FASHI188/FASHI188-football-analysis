#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import urllib.request
from pathlib import Path

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FD = {
    "2023/24": "https://www.football-data.co.uk/mmz4281/2324/E0.csv",
    "2024/25": "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
    "2025/26": "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
}
ALIASES = {
    "manchesterunited": "manutd", "manunited": "manutd", "manutd": "manutd",
    "manchestercity": "mancity", "mancity": "mancity",
    "nottinghamforest": "nottmforest", "nottmforest": "nottmforest",
    "tottenhamhotspur": "spurs", "tottenham": "spurs", "spurs": "spurs",
    "wolverhamptonwanderers": "wolves", "wolverhampton": "wolves", "wolves": "wolves",
    "newcastleunited": "newcastle", "newcastle": "newcastle",
    "westhamunited": "westham", "westham": "westham",
    "brightonandhovealbion": "brighton", "brighton": "brighton",
    "sheffieldunited": "sheffieldutd", "sheffieldutd": "sheffieldutd",
    "lutontown": "luton", "luton": "luton",
    "leicestercity": "leicester", "leicester": "leicester",
    "ipswichtown": "ipswich", "ipswich": "ipswich",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(value: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(value).lower().replace("&", "and").replace("'", ""))
    return ALIASES.get(key, key)


def date_norm(value: str) -> str:
    from datetime import datetime
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(value)


def download_csv(url: str, ledger: list[dict]) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "EPL lineup draw static research"})
    with urllib.request.urlopen(req, timeout=120) as response:
        data = response.read()
    ledger.append({"url": url, "sha256": sha256_bytes(data), "bytes": len(data)})
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            return list(csv.DictReader(io.StringIO(text)))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("csv", data, 0, 1, "no supported encoding")


def odds(row: dict) -> tuple[float, float, float] | None:
    triplets = [
        ("AvgCH", "AvgCD", "AvgCA"),
        ("MaxCH", "MaxCD", "MaxCA"),
        ("B365CH", "B365CD", "B365CA"),
        ("AvgH", "AvgD", "AvgA"),
        ("MaxH", "MaxD", "MaxA"),
        ("B365H", "B365D", "B365A"),
    ]
    for fields in triplets:
        try:
            values = tuple(float(row[f]) for f in fields)
        except (KeyError, TypeError, ValueError):
            continue
        if all(v > 1.0 and math.isfinite(v) for v in values):
            return values
    return None


def binary_metrics(y: np.ndarray, score: np.ndarray, selected: np.ndarray) -> dict:
    return {
        "pr_auc": float(average_precision_score(y, score)),
        "roc_auc": float(roc_auc_score(y, score)),
        "log_loss": float(log_loss(y, np.column_stack([1 - score, score]), labels=[0, 1])),
        "brier": float(brier_score_loss(y, score)),
        "coverage": float(np.mean(selected)),
        "selected": int(np.sum(selected)),
        "precision": float(precision_score(y, selected, zero_division=0)),
        "recall": float(recall_score(y, selected, zero_division=0)),
        "f1": float(f1_score(y, selected, zero_division=0)),
        "tp": int(np.sum((y == 1) & selected)),
        "fp": int(np.sum((y == 0) & selected)),
        "fn": int(np.sum((y == 1) & (~selected))),
    }


def gate_accuracy(labels: np.ndarray, base: np.ndarray, selected: np.ndarray) -> tuple[float, float, float]:
    gated = np.where(selected, "D", base)
    baseline = float(accuracy_score(labels, base))
    gate = float(accuracy_score(labels, gated))
    return baseline, gate, gate - baseline


def make_model(model_id: str, feature_count: int, spec: dict):
    columns = list(range(feature_count))
    if model_id == "logistic":
        cfg = next(x for x in spec["models"] if x["id"] == model_id)
        return Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=cfg["C"], max_iter=cfg["max_iter"], random_state=20260803)),
        ])
    cfg = next(x for x in spec["models"] if x["id"] == model_id)
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("model", HistGradientBoostingClassifier(
            learning_rate=cfg["learning_rate"], max_iter=cfg["max_iter"],
            max_leaf_nodes=cfg["max_leaf_nodes"], min_samples_leaf=cfg["min_samples_leaf"],
            l2_regularization=cfg["l2_regularization"], random_state=20260803,
        )),
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lineups", type=Path, required=True)
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    spec = json.loads(args.prereg.read_text(encoding="utf-8"))

    with args.lineups.open("r", encoding="utf-8-sig", newline="") as handle:
        lineup_rows = list(csv.DictReader(handle))

    ledger: list[dict] = []
    market_index = {}
    for season, url in FD.items():
        for row in download_csv(url, ledger):
            try:
                key = (season, date_norm(row["Date"]), norm(row["HomeTeam"]), norm(row["AwayTeam"]))
            except Exception:
                continue
            market_index[key] = row

    rows = []
    for row in lineup_rows:
        key = (row["season"], row["date"], norm(row["home_team"]), norm(row["away_team"]))
        market = market_index.get(key)
        if market is None:
            continue
        prices = odds(market)
        if prices is None:
            continue
        inv = np.array([1 / x for x in prices], dtype=float)
        fair = inv / inv.sum()
        out = dict(row)
        out["fair_h"], out["fair_d"], out["fair_a"] = map(float, fair)
        out["overround"] = float(inv.sum())
        out["draw_vs_side_gap"] = float(fair[1] - max(fair[0], fair[2]))
        out["home_away_symmetry"] = float(abs(fair[0] - fair[2]))
        out["market_argmax"] = ("H", "D", "A")[int(np.argmax(fair))]
        rows.append(out)

    derived = [
        "min_starter_continuity", "continuity_gap", "total_starter_changes",
        "any_gk_changed", "total_def_changes", "total_mid_changes", "total_fwd_changes",
    ]
    for row in rows:
        def val(name: str) -> float:
            try:
                return float(row[name])
            except (KeyError, TypeError, ValueError):
                return float("nan")
        hc, ac = val("home_starter_continuity"), val("away_starter_continuity")
        row["min_starter_continuity"] = min(hc, ac) if np.isfinite(hc) and np.isfinite(ac) else float("nan")
        row["continuity_gap"] = abs(hc - ac) if np.isfinite(hc) and np.isfinite(ac) else float("nan")
        for target, fields in {
            "total_starter_changes": ("home_starter_changes", "away_starter_changes"),
            "total_def_changes": ("home_def_changes", "away_def_changes"),
            "total_mid_changes": ("home_mid_changes", "away_mid_changes"),
            "total_fwd_changes": ("home_fwd_changes", "away_fwd_changes"),
        }.items():
            values = [val(f) for f in fields]
            row[target] = sum(values) if all(np.isfinite(x) for x in values) else float("nan")
        gks = [val("home_gk_changed"), val("away_gk_changed")]
        row["any_gk_changed"] = max(gks) if all(np.isfinite(x) for x in gks) else float("nan")

    train = [r for r in rows if r["season"] == "2023/24"]
    valid = [r for r in rows if r["season"] == "2024/25"]
    test = [r for r in rows if r["season"] == "2025/26" and str(r["untouched_2025_26_test_eligible"]) == "1"]
    if min(len(train), len(valid), len(test)) < 200:
        raise ValueError(f"insufficient split rows: train={len(train)} valid={len(valid)} test={len(test)}")

    def matrix(part: list[dict], features: list[str]) -> np.ndarray:
        result = []
        for row in part:
            vals = []
            for f in features:
                try:
                    vals.append(float(row[f]))
                except (KeyError, TypeError, ValueError):
                    vals.append(float("nan"))
            result.append(vals)
        return np.asarray(result, dtype=float)

    y_train = np.asarray([r["label_result"] == "D" for r in train], dtype=int)
    y_valid = np.asarray([r["label_result"] == "D" for r in valid], dtype=int)
    y_test = np.asarray([r["label_result"] == "D" for r in test], dtype=int)
    labels_valid = np.asarray([r["label_result"] for r in valid])
    labels_test = np.asarray([r["label_result"] for r in test])
    base_valid = np.asarray([r["market_argmax"] for r in valid])
    base_test = np.asarray([r["market_argmax"] for r in test])

    lane_results = []
    prediction_rows = []
    fitted = {}
    for model_id in ("logistic", "hgb"):
        for feature_set in ("market", "market_plus_lineup"):
            features = spec["feature_sets"][feature_set]
            model = make_model(model_id, len(features), spec)
            model.fit(matrix(train, features), y_train)
            valid_score = np.clip(model.predict_proba(matrix(valid, features))[:, 1], 1e-6, 1 - 1e-6)
            candidates = []
            for coverage in spec["target_coverages"]:
                k = max(1, int(round(len(valid) * coverage)))
                threshold = float(np.sort(valid_score)[-k])
                selected = valid_score >= threshold
                bm = binary_metrics(y_valid, valid_score, selected)
                baseline_acc, gate_acc, delta = gate_accuracy(labels_valid, base_valid, selected)
                bm.update({
                    "target_coverage": coverage, "threshold": threshold,
                    "baseline_1x2_accuracy": baseline_acc, "gate_1x2_accuracy": gate_acc,
                    "accuracy_delta": delta,
                })
                candidates.append(bm)
            good = [c for c in candidates if c["accuracy_delta"] >= spec["validation_selection"]["constraints"]["complete_1x2_accuracy_delta_min"] and c["selected"] >= spec["validation_selection"]["constraints"]["selected_min"]]
            pool = good or candidates
            chosen = sorted(pool, key=lambda c: (-c["f1"], -c["precision"], -c["recall"], c["coverage"]))[0]

            test_score = np.clip(model.predict_proba(matrix(test, features))[:, 1], 1e-6, 1 - 1e-6)
            test_selected = test_score >= chosen["threshold"]
            tm = binary_metrics(y_test, test_score, test_selected)
            baseline_acc, gate_acc, delta = gate_accuracy(labels_test, base_test, test_selected)
            tm.update({
                "model": model_id, "feature_set": feature_set,
                "features": len(features), "train_rows": len(train), "validation_rows": len(valid), "test_rows": len(test),
                "validation_target_coverage": chosen["target_coverage"], "frozen_threshold": chosen["threshold"],
                "validation_metrics": chosen,
                "baseline_1x2_accuracy": baseline_acc, "gate_1x2_accuracy": gate_acc,
                "accuracy_delta": delta,
            })
            lane_results.append(tm)
            fitted[(model_id, feature_set)] = {"score": test_score, "selected": test_selected}
            for row, score, selected in zip(test, test_score, test_selected):
                prediction_rows.append({
                    "model": model_id, "feature_set": feature_set,
                    "match_identity": row["match_identity"], "date": row["date"],
                    "home_team": row["home_team"], "away_team": row["away_team"],
                    "label_result": row["label_result"], "market_argmax": row["market_argmax"],
                    "score": score, "threshold": chosen["threshold"], "selected": int(selected),
                    "gate_result": "D" if selected else row["market_argmax"],
                })

    gate = spec["test_research_gate"]
    paired = []
    rng = np.random.default_rng(20260803)
    for model_id in ("logistic", "hgb"):
        market = next(r for r in lane_results if r["model"] == model_id and r["feature_set"] == "market")
        lineup = next(r for r in lane_results if r["model"] == model_id and r["feature_set"] == "market_plus_lineup")
        delta_pr = lineup["pr_auc"] - market["pr_auc"]
        delta_roc = lineup["roc_auc"] - market["roc_auc"]
        deltas = []
        market_score = fitted[(model_id, "market")]["score"]
        lineup_score = fitted[(model_id, "market_plus_lineup")]["score"]
        for _ in range(1000):
            idx = rng.integers(0, len(test), len(test))
            yb = y_test[idx]
            if len(np.unique(yb)) < 2:
                continue
            deltas.append(float(average_precision_score(yb, lineup_score[idx]) - average_precision_score(yb, market_score[idx])))
        ci = np.quantile(deltas, [0.05, 0.5, 0.95]).tolist() if deltas else [float("nan")] * 3
        passes = {
            "paired_pr_auc_delta": delta_pr >= gate["paired_pr_auc_delta_min"],
            "paired_roc_auc_delta": delta_roc >= gate["paired_roc_auc_delta_min"],
            "precision": lineup["precision"] >= gate["draw_precision_min"],
            "recall": lineup["recall"] >= gate["draw_recall_min"],
            "f1": lineup["f1"] >= gate["draw_f1_min"],
            "accuracy_delta": lineup["accuracy_delta"] >= gate["complete_1x2_accuracy_delta_min"],
            "selected": lineup["selected"] >= gate["selected_min"],
        }
        paired.append({
            "model": model_id,
            "market_test": market,
            "lineup_test": lineup,
            "paired_pr_auc_delta": delta_pr,
            "paired_roc_auc_delta": delta_roc,
            "paired_pr_auc_delta_bootstrap_90pct": {"p05": ci[0], "p50": ci[1], "p95": ci[2]},
            "gate_checks": passes,
            "gate_pass": all(passes.values()),
        })

    status = "PASS_RESEARCH_CHALLENGER_ONLY" if any(x["gate_pass"] for x in paired) else "FAIL_RESEARCH_GATE"
    result = {
        "schema_version": "EPL-LINEUP-DRAW-INCREMENT-R1",
        "status": status,
        "research_only": True,
        "formal_weight": 0,
        "split_rows": {"train_2023_24": len(train), "validation_2024_25": len(valid), "untouched_test_2025_26": len(test)},
        "test_draws": int(y_test.sum()),
        "lanes": lane_results,
        "paired_lineup_increment": paired,
        "source_ledger": ledger,
    }
    (args.out / "EPL_LINEUP_DRAW_R1_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_fields = [
        "model", "feature_set", "train_rows", "validation_rows", "test_rows", "validation_target_coverage",
        "frozen_threshold", "pr_auc", "roc_auc", "log_loss", "brier", "coverage", "selected",
        "precision", "recall", "f1", "tp", "fp", "fn", "baseline_1x2_accuracy", "gate_1x2_accuracy", "accuracy_delta",
    ]
    with (args.out / "EPL_LINEUP_DRAW_R1_lane_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(lane_results)
    pred_fields = ["model", "feature_set", "match_identity", "date", "home_team", "away_team", "label_result", "market_argmax", "score", "threshold", "selected", "gate_result"]
    with (args.out / "EPL_LINEUP_DRAW_R1_test_predictions.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pred_fields)
        writer.writeheader(); writer.writerows(prediction_rows)
    with (args.out / "EPL_LINEUP_DRAW_R1_source_ledger.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["url", "sha256", "bytes"])
        writer.writeheader(); writer.writerows(ledger)

    manifest = {"schema_version": "EPL-LINEUP-DRAW-ARTIFACT-R1", "files": {}}
    for path in sorted(args.out.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            data = path.read_bytes()
            manifest["files"][path.name] = {"sha256": sha256_bytes(data), "bytes": len(data)}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "test_rows": len(test), "test_draws": int(y_test.sum()), "paired": paired}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
