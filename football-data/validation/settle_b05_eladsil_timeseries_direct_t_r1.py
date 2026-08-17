#!/usr/bin/env python3
"""Settle exactly B05 after immutable zero-label freeze.

Governance boundary:
- consumes the already-frozen B05 feature artifact; never recomputes features;
- semantically dereferences score fields only for the 400 frozen B05 match_ids;
- non-target result rows are scanned only far enough to read their first CSV field
  (`match_id`) from raw bytes; their score/result columns are never parsed;
- B06+ remain unopened;
- fixed preregistered model/evaluation/gate; no tuning.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research" / "b05_eladsil_timeseries_direct_t_r1"
FREEZE = Path(os.environ.get("B05_FREEZE_DIR", "/tmp/b05-freeze"))
SOURCE_ZIP = Path(os.environ.get("B05_SOURCE_ZIP", "/tmp/elad/source.zip"))
OUT = ROOT / "manifests" / "b05_eladsil_timeseries_direct_t_r1"

EXPECTED = {
    "artifact_zip_sha256": "4e7f4f7b2ba95c20476c1769ef0b54e9bd7db13d98066eb822768ccd12cae66a",
    "source_zip_sha256": "20baff7a0d65fc667225187430af7b13eab4b27e56695a8bc4f907e8c498f6f9",
    "source_batch_manifest_sha256": "094edba4ae1e4955ef174f790011a4f49dc7013d3c51a842f64f70a7590f2f4e",
    "feature_packet_sha256": "338ed1d427820562eb92547b9dc9b5c0ca72ca87a5f19194433589d2ea8275c0",
    "prereg_sha256": "2772973cc64ebd4ee8ed1bb90b8ff47ca7e52e811a318c2015df45a6a8ab1f86",
    "freeze_json_sha256": "3e01703784b6e81df24ff42474b858f1b259c3460b51812e72c2270773c1620c",
    "source_headers_sha256": "b3ab3c6228354d3662610e926109da6fb6612ec73904430a29512d1a65de9cdb",
    "rows": 400,
}

ID_COLS = ["match_id", "kickoff", "competition", "home", "away"]
BASE_FEATURES = [
    "last_log_H_over_D",
    "last_log_A_over_D",
    "last_pD",
    "last_entropy",
    "last_quote_hours_before_kickoff",
]
TRAJECTORY_FEATURES = [
    "first_log_H_over_D",
    "first_log_A_over_D",
    "delta_log_H_over_D",
    "delta_log_A_over_D",
    "range_pH",
    "range_pD",
    "range_pA",
    "std_pH",
    "std_pD",
    "std_pA",
    "slope_log_H_over_D_per_hour",
    "slope_log_A_over_D_per_hour",
    "log1p_distinct_timestamps",
    "trajectory_span_hours",
]
CHALLENGER_FEATURES = BASE_FEATURES + TRAJECTORY_FEATURES


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_frozen_inputs() -> tuple[list[dict], dict, dict]:
    receipt = load_json(RESEARCH / "FREEZE_RECEIPT.json")
    if receipt["freeze_artifact_zip_sha256"] != EXPECTED["artifact_zip_sha256"]:
        raise RuntimeError("RECEIPT_ARTIFACT_SHA_MISMATCH")
    if receipt["feature_packet_sha256"] != EXPECTED["feature_packet_sha256"]:
        raise RuntimeError("RECEIPT_FEATURE_SHA_MISMATCH")
    if sha256(RESEARCH / "PREREG.md") != EXPECTED["prereg_sha256"]:
        raise RuntimeError("PREREG_SHA_MISMATCH")

    files = {
        "B05_features.csv": EXPECTED["feature_packet_sha256"],
        "B05_manifest.json": EXPECTED["source_batch_manifest_sha256"],
        "freeze.json": EXPECTED["freeze_json_sha256"],
        "source_headers.json": EXPECTED["source_headers_sha256"],
    }
    for name, expected in files.items():
        p = FREEZE / name
        if not p.exists() or sha256(p) != expected:
            raise RuntimeError(f"FROZEN_FILE_SHA_MISMATCH:{name}")

    freeze = load_json(FREEZE / "freeze.json")
    manifest = load_json(FREEZE / "B05_manifest.json")
    if freeze["status"] != "B05_ZERO_LABEL_FEATURE_PACKET_FROZEN":
        raise RuntimeError("FREEZE_STATUS_MISMATCH")
    if freeze["result_data_rows_read"] != 0 or freeze["outcome_values_dereferenced"] != 0:
        raise RuntimeError("FREEZE_LABEL_BOUNDARY_VIOLATED")
    if manifest["status"] != "SEALED_UNOPENED" or manifest["batch_id"] != "ELAD-PIT6H-B001":
        raise RuntimeError("B05_MANIFEST_STATUS_MISMATCH")
    if manifest["batch_size"] != EXPECTED["rows"]:
        raise RuntimeError("B05_MANIFEST_ROW_MISMATCH")

    with (FREEZE / "B05_features.csv").open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != EXPECTED["rows"] or len({r["match_id"] for r in rows}) != EXPECTED["rows"]:
        raise RuntimeError("FEATURE_ROWS_OR_IDS_MISMATCH")
    expected_cols = ID_COLS + CHALLENGER_FEATURES
    if list(rows[0].keys()) != expected_cols:
        raise RuntimeError(f"FEATURE_COLUMN_ORDER_MISMATCH:{list(rows[0].keys())}")
    manifest_ids = {str(x["match_id"]) for x in manifest["matches"]}
    feature_ids = {str(x["match_id"]) for x in rows}
    if manifest_ids != feature_ids:
        raise RuntimeError("FEATURE_MANIFEST_ID_MISMATCH")
    return rows, manifest, freeze


def raw_first_csv_field(raw_line: bytes) -> str:
    """Read only the first raw CSV field.

    match_id is the first field and is an integer-like token in this source. We do not
    invoke a CSV parser on non-target rows, so score/final_result values are never
    semantically dereferenced for sealed non-B05 matches.
    """
    token = raw_line.split(b",", 1)[0].strip().strip(b'"')
    try:
        return token.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuntimeError("NON_UTF8_MATCH_ID") from exc


def read_target_only_labels(target_rows: list[dict], manifest: dict) -> tuple[dict[str, int], dict]:
    if sha256(SOURCE_ZIP) != EXPECTED["source_zip_sha256"]:
        raise RuntimeError("SOURCE_ZIP_SHA_MISMATCH")
    target = {str(r["match_id"]) for r in target_rows}
    expected_meta = {
        str(m["match_id"]): (str(m["home"]), str(m["away"])) for m in manifest["matches"]
    }
    labels: dict[str, int] = {}
    scanned_non_target = 0
    parsed_target = 0

    with zipfile.ZipFile(SOURCE_ZIP) as z:
        names = [n for n in z.namelist() if n.endswith("Matches_Results.csv")]
        if len(names) != 1:
            raise RuntimeError(f"RESULT_MEMBER_COUNT:{names}")
        with z.open(names[0], "r") as fh:
            header_raw = fh.readline()
            header = next(csv.reader([header_raw.decode("utf-8-sig").rstrip("\r\n")]))
            expected_header = [
                "match_id", "date_start", "competition_name", "home_team_name",
                "away_team_name", "home_team_score", "away_team_score", "final_result",
            ]
            if header != expected_header:
                raise RuntimeError(f"RESULT_HEADER_MISMATCH:{header}")

            for raw in fh:
                mid = raw_first_csv_field(raw)
                if mid not in target:
                    scanned_non_target += 1
                    continue
                # Only approved B05 rows are fully parsed.
                row = next(csv.reader([raw.decode("utf-8").rstrip("\r\n")]))
                if len(row) != len(expected_header):
                    raise RuntimeError(f"TARGET_RESULT_FIELD_COUNT:{mid}:{len(row)}")
                if row[0] != mid:
                    raise RuntimeError(f"TARGET_RESULT_ID_PARSE_MISMATCH:{mid}")
                if mid in labels:
                    raise RuntimeError(f"DUPLICATE_TARGET_RESULT:{mid}")
                home, away = row[3], row[4]
                exp_home, exp_away = expected_meta[mid]
                if home != exp_home or away != exp_away:
                    raise RuntimeError(f"TARGET_IDENTITY_MISMATCH:{mid}:{home}:{away}")
                try:
                    gh = int(float(row[5]))
                    ga = int(float(row[6]))
                except Exception as exc:
                    raise RuntimeError(f"TARGET_SCORE_PARSE:{mid}:{row[5]}:{row[6]}") from exc
                if gh < 0 or ga < 0:
                    raise RuntimeError(f"TARGET_NEGATIVE_SCORE:{mid}")
                labels[mid] = min(gh + ga, 4)
                parsed_target += 1

    if parsed_target != EXPECTED["rows"] or set(labels) != target:
        missing = sorted(target - set(labels))[:20]
        raise RuntimeError(f"TARGET_LABEL_COVERAGE:{parsed_target}:{missing}")
    audit = {
        "approved_target_rows_parsed": parsed_target,
        "approved_target_score_pairs_dereferenced": parsed_target,
        "non_target_result_lines_id_scanned": scanned_non_target,
        "non_target_score_values_semantically_dereferenced": 0,
        "non_target_final_result_values_semantically_dereferenced": 0,
        "b06_plus_outcome_values_semantically_dereferenced": 0,
    }
    return labels, audit


def matrix(rows: list[dict], cols: list[str]) -> np.ndarray:
    out = np.array([[float(r[c]) for c in cols] for r in rows], dtype=float)
    if out.shape != (len(rows), len(cols)) or not np.isfinite(out).all():
        raise RuntimeError(f"INVALID_FEATURE_MATRIX:{out.shape}:{len(cols)}")
    return out


def fit_predict(x_train: np.ndarray, y_train: np.ndarray, x_eval: np.ndarray) -> np.ndarray:
    classes = np.unique(y_train)
    if len(classes) < 2:
        raise RuntimeError(f"TRAINING_FOLD_LT2_CLASSES:{classes.tolist()}")
    model = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        LogisticRegression(C=0.1, penalty="l2", solver="lbfgs", max_iter=4000),
    )
    model.fit(x_train, y_train)
    raw = model.predict_proba(x_eval)
    model_classes = model[-1].classes_.astype(int)
    p = np.zeros((len(x_eval), 5), dtype=float)
    for j, cls in enumerate(model_classes):
        if cls < 0 or cls > 4:
            raise RuntimeError(f"UNEXPECTED_MODEL_CLASS:{cls}")
        p[:, cls] = raw[:, j]
    p = np.clip(p, 1e-12, 1.0)
    p /= p.sum(axis=1, keepdims=True)
    return p


def multiclass_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    idx = np.arange(len(y))
    losses = -np.log(np.clip(p[idx, y], 1e-12, 1.0))
    one = np.eye(5)[y]
    brier = np.mean(np.sum((p - one) ** 2, axis=1))
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.mean(np.sum((cp - cy) ** 2, axis=1))
    out = {
        "rows": int(len(y)),
        "logloss": float(np.mean(losses)),
        "brier": float(brier),
        "rps": float(rps),
        "top1_accuracy": float(np.mean(np.argmax(p, axis=1) == y)),
    }
    per_class = {}
    for cls in range(5):
        mask = y == cls
        per_class[str(cls)] = {
            "n": int(mask.sum()),
            "mean_true_class_logloss": float(np.mean(losses[mask])) if mask.any() else None,
        }
    out["per_class"] = per_class
    y4 = (y == 4).astype(int)
    if len(np.unique(y4)) == 2:
        out["four_plus_auc"] = float(roc_auc_score(y4, p[:, 4]))
        out["four_plus_brier"] = float(np.mean((p[:, 4] - y4) ** 2))
    else:
        out["four_plus_auc"] = None
        out["four_plus_brier"] = None
    return out


def date_cluster_bootstrap(rows: list[dict], delta: np.ndarray, n: int = 10000, seed: int = 2026081705) -> dict:
    by_date = defaultdict(list)
    for i, r in enumerate(rows):
        by_date[str(r["kickoff"])[:10]].append(i)
    keys = sorted(by_date)
    if len(keys) < 2:
        raise RuntimeError("LT2_KICKOFF_DATE_CLUSTERS")
    rng = np.random.default_rng(seed)
    samples = np.empty(n, dtype=float)
    for b in range(n):
        chosen = rng.integers(0, len(keys), size=len(keys))
        vals = []
        for j in chosen:
            vals.extend(delta[by_date[keys[int(j)]]].tolist())
        samples[b] = float(np.mean(vals))
    return {
        "clusters": len(keys),
        "resamples": n,
        "seed": seed,
        "mean": float(np.mean(samples)),
        "p05": float(np.quantile(samples, 0.05)),
        "p50": float(np.quantile(samples, 0.50)),
        "p95": float(np.quantile(samples, 0.95)),
    }


def main():
    rows, manifest, freeze = verify_frozen_inputs()
    labels, label_audit = read_target_only_labels(rows, manifest)

    # Frozen feature CSV is already kickoff-ordered; verify monotonic ordering.
    keys = [(r["kickoff"], r["match_id"]) for r in rows]
    if keys != sorted(keys):
        raise RuntimeError("FROZEN_FEATURES_NOT_CHRONOLOGICAL")
    y_all = np.array([labels[r["match_id"]] for r in rows], dtype=int)
    xb = matrix(rows, BASE_FEATURES)
    xc = matrix(rows, CHALLENGER_FEATURES)

    fold_specs = [(0, 100, 100, 200), (0, 200, 200, 300), (0, 300, 300, 400)]
    p_base_parts = []
    p_chal_parts = []
    scored_rows = []
    fold_reports = []
    for fold, (tr0, tr1, ev0, ev1) in enumerate(fold_specs, start=1):
        ytr = y_all[tr0:tr1]
        yev = y_all[ev0:ev1]
        pb = fit_predict(xb[tr0:tr1], ytr, xb[ev0:ev1])
        pc = fit_predict(xc[tr0:tr1], ytr, xc[ev0:ev1])
        p_base_parts.append(pb)
        p_chal_parts.append(pc)
        scored_rows.extend(rows[ev0:ev1])
        fold_reports.append(
            {
                "fold": fold,
                "train_rows": int(tr1 - tr0),
                "eval_rows": int(ev1 - ev0),
                "train_class_counts": {str(k): int(v) for k, v in sorted(Counter(ytr.tolist()).items())},
                "eval_class_counts": {str(k): int(v) for k, v in sorted(Counter(yev.tolist()).items())},
                "baseline": multiclass_metrics(yev, pb),
                "challenger": multiclass_metrics(yev, pc),
            }
        )

    y = y_all[100:400]
    p_base = np.vstack(p_base_parts)
    p_chal = np.vstack(p_chal_parts)
    base = multiclass_metrics(y, p_base)
    chal = multiclass_metrics(y, p_chal)
    idx = np.arange(len(y))
    loss_base = -np.log(np.clip(p_base[idx, y], 1e-12, 1.0))
    loss_chal = -np.log(np.clip(p_chal[idx, y], 1e-12, 1.0))
    delta = loss_chal - loss_base
    boot = date_cluster_bootstrap(scored_rows, delta)
    delta_ll = float(chal["logloss"] - base["logloss"])
    development_signal = bool(
        delta_ll < 0.0
        and boot["p95"] < 0.0
        and chal["brier"] <= base["brier"]
        and chal["rps"] <= base["rps"]
    )
    verdict = (
        "RESEARCH_GATE_PASS_B05_1X2_TRAJECTORY_INCREMENTAL_T_SIGNAL"
        if development_signal
        else "RESEARCH_GATE_FAIL_B05_1X2_TRAJECTORY_INCREMENT_NOT_CONFIRMED"
    )

    per_match = []
    for i, r in enumerate(scored_rows):
        rec = {
            "match_id": r["match_id"],
            "kickoff": r["kickoff"],
            "competition": r["competition"],
            "home": r["home"],
            "away": r["away"],
            "total_class": int(y[i]),
            "baseline_true_class_loss": float(loss_base[i]),
            "challenger_true_class_loss": float(loss_chal[i]),
            "delta_loss_challenger_minus_baseline": float(delta[i]),
        }
        for cls in range(5):
            rec[f"p_base_T{cls if cls < 4 else '4plus'}"] = float(p_base[i, cls])
            rec[f"p_chal_T{cls if cls < 4 else '4plus'}"] = float(p_chal[i, cls])
        per_match.append(rec)

    result = {
        "schema_version": "B05-ELADSIL-TIMESERIES-DIRECT-T-R1",
        "status": "B05_SETTLED_ONCE_NO_PROMOTION",
        "verdict": verdict,
        "development_signal": development_signal,
        "source_contract": {
            "global_alias": "B05",
            "source_batch_id": "ELAD-PIT6H-B001",
            "package_rows_opened": 400,
            "oos_rows_scored": 300,
            "b01_b04_reused": False,
            "b06_plus_opened": False,
            "freeze_artifact_id": 9286252440,
            "freeze_feature_sha256": EXPECTED["feature_packet_sha256"],
            "prereg_sha256": EXPECTED["prereg_sha256"],
            "source_zip_sha256": EXPECTED["source_zip_sha256"],
        },
        "label_isolation_audit": label_audit,
        "target_class_counts_all_400": {str(k): int(v) for k, v in sorted(Counter(y_all.tolist()).items())},
        "target_class_counts_oos_300": {str(k): int(v) for k, v in sorted(Counter(y.tolist()).items())},
        "folds": fold_reports,
        "baseline": base,
        "challenger": chal,
        "delta_challenger_minus_baseline": {
            "logloss": delta_ll,
            "brier": float(chal["brier"] - base["brier"]),
            "rps": float(chal["rps"] - base["rps"]),
            "top1_accuracy_pp": float(100.0 * (chal["top1_accuracy"] - base["top1_accuracy"])),
        },
        "paired_kickoff_date_cluster_bootstrap_delta_logloss": boot,
        "gate_contract": {
            "delta_logloss_negative": bool(delta_ll < 0.0),
            "bootstrap_90_upper_below_zero": bool(boot["p95"] < 0.0),
            "brier_nonworse": bool(chal["brier"] <= base["brier"]),
            "rps_nonworse": bool(chal["rps"] <= base["rps"]),
        },
        "boundary": {
            "research_only": True,
            "formal_weight": 0,
            "formal_promotion_allowed": False,
            "main_mutation": False,
            "current_mutation": False,
            "post_result_tuning_on_b05_allowed": False,
            "b06_auto_open_allowed": False,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    summary_text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (OUT / "summary.json").write_text(summary_text, encoding="utf-8")
    (OUT / "summary.sha256").write_text(hashlib.sha256(summary_text.encode()).hexdigest() + "\n", encoding="ascii")
    if per_match:
        with (OUT / "per_match.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(per_match[0]))
            w.writeheader()
            w.writerows(per_match)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
