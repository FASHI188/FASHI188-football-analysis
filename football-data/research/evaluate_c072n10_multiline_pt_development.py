#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

N8_SHA = "e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082"
N9_MANIFEST_SHA = "027d7f4ff7cb72724115a731002d4d2048f10da420fdb8ac4955eb2782ba3a06"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
FILES = {
    "EPL": "data/england/premier-league.csv",
    "LL": "data/spain/laliga.csv",
    "BL": "data/germany/bundesliga.csv",
    "SA": "data/italy/serie-a.csv",
    "L1": "data/france/ligue-1.csv",
}
DEV_SEASONS = [
    "2015/2016","2016/2017","2017/2018","2018/2019",
    "2019/2020","2020/2021","2021/2022","2022/2023","2023/2024",
]
TEST_SEASONS = ["2019/2020","2020/2021","2021/2022","2022/2023","2023/2024"]
LINES = ["05","15","25","35","45"]
BASE_NUM = ["market_logit_25"]
CAND_NUM = ["market_logit_05","market_logit_15","market_logit_25","market_logit_35","market_logit_45"]
K = 8
BOOT_REPS = 3000
BOOT_SEED = 72009
SUMMARY = Path("football-data/research/c072n10_multiline_pt_development_summary.json")
PREDICTIONS = Path("football-data/research/c072n10_multiline_pt_development_predictions.csv")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/{path}"


def fetch(path: str) -> bytes:
    req = urllib.request.Request(raw_url(path), headers={"User-Agent": "football3-c072n10"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse_nonneg_int(x: str) -> int | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < 0 or not float(v).is_integer():
        return None
    return int(v)


def parse_price(x: str) -> float | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def market_logit(over: float, under: float) -> float:
    q = (1.0 / over) / ((1.0 / over) + (1.0 / under))
    q = min(max(q, 1e-6), 1.0 - 1e-6)
    return float(math.log(q / (1.0 - q)))


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def decode_authorized_targets(manifest: list[dict[str, str]]) -> tuple[dict[tuple[str, str], int], dict]:
    selected_by_code: dict[str, dict[int, dict[str, str]]] = {c: {} for c in FILES}
    for r in manifest:
        season = r["n8_Season"]
        if season not in DEV_SEASONS:
            continue
        code = r["sourceCode"]
        idx = int(r["label_source_row_index"])
        if idx in selected_by_code[code]:
            raise RuntimeError(f"duplicate selected source row {code}:{idx}")
        selected_by_code[code][idx] = r

    target_by_n8: dict[tuple[str, str], int] = {}
    file_meta = {}
    invalid_label_rows = 0
    decoded_rows = 0

    for code, source_path in FILES.items():
        raw = fetch(source_path)
        text = raw.decode("utf-8-sig")
        physical_lines = text.splitlines()
        if not physical_lines:
            raise RuntimeError(f"empty source {source_path}")
        header = next(csv.reader([physical_lines[0]]))
        idx = {name: i for i, name in enumerate(header)}
        required = {"Date","Season","HomeTeam","AwayTeam","FTHG","FTAG"}
        missing = sorted(required - set(idx))
        if missing:
            raise RuntimeError(f"{source_path}: missing {missing}")
        max_needed = max(idx[x] for x in required)
        selected = selected_by_code[code]

        for row_index in sorted(selected):
            # Critical boundary: only physical rows explicitly selected by the already-frozen
            # N9 DEVELOPMENT manifest are CSV-decoded. Every unselected row, including all
            # 2024/25 rows, is skipped without field parsing/indexing.
            physical_index = row_index + 1
            if physical_index >= len(physical_lines):
                raise RuntimeError(f"{source_path}: selected row index outside physical file: {row_index}")
            row = next(csv.reader([physical_lines[physical_index]]))
            if len(row) <= max_needed:
                raise RuntimeError(f"{source_path}: short selected row {row_index}")
            m = selected[row_index]
            # Identity verification proves the physical-row optimization matches the N9 pandas row index.
            if row[idx["Date"]] != m["label_Date"] or row[idx["Season"]] != m["label_Season"] \
               or row[idx["HomeTeam"]] != m["label_HomeTeam"] or row[idx["AwayTeam"]] != m["label_AwayTeam"]:
                raise RuntimeError(f"{source_path}: physical row identity mismatch at {row_index}")
            hg = parse_nonneg_int(row[idx["FTHG"]])
            ag = parse_nonneg_int(row[idx["FTAG"]])
            decoded_rows += 1
            if hg is None or ag is None:
                invalid_label_rows += 1
                continue
            key = (code, m["n8_id"])
            if key in target_by_n8:
                raise RuntimeError(f"duplicate target key {key}")
            target_by_n8[key] = min(hg + ag, 7)

        file_meta[code] = {
            "path": source_path,
            "revision": REV,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "authorized_development_rows": len(selected),
            "development_target_rows_decoded": len(selected),
            "unselected_rows_csv_decoded": 0,
            "target_values_2024_25_read": 0,
            "target_values_2025_26_read": 0,
        }

    return target_by_n8, {
        "files": file_meta,
        "authorized_development_rows_decoded": decoded_rows,
        "invalid_development_label_rows": invalid_label_rows,
        "target_values_2024_25_read": 0,
        "target_values_2025_26_read": 0,
    }


def build_model(num_cols: list[str]) -> Pipeline:
    numeric = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
    ])
    categorical = OneHotEncoder(handle_unknown="ignore")
    pre = ColumnTransformer([
        ("num", numeric, num_cols),
        ("league", categorical, ["sourceCode"]),
    ])
    clf = LogisticRegression(
        C=0.1,
        max_iter=3000,
        class_weight=None,
        random_state=0,
        solver="lbfgs",
    )
    return Pipeline([("pre", pre), ("clf", clf)])


def aligned_predict(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    classes = np.asarray(model.named_steps["clf"].classes_, dtype=int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, classes] = raw
    out = np.clip(out, 1e-15, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y, dtype=int)
    one = np.eye(K)[y]
    ll_rows = -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))
    brier_rows = np.sum((p - one) ** 2, axis=1)
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps_rows = np.sum((cp - cy) ** 2, axis=1) / (K - 1)
    top1 = np.argmax(p, axis=1)
    top3 = np.argsort(p, axis=1)[:, -3:]
    return {
        "n": int(len(y)),
        "log_loss": float(np.mean(ll_rows)),
        "brier": float(np.mean(brier_rows)),
        "rps": float(np.mean(rps_rows)),
        "top1_accuracy": float(np.mean(top1 == y)),
        "top3_accuracy": float(np.mean([y[i] in top3[i] for i in range(len(y))])),
        "top1_total_2_fraction": float(np.mean(top1 == 2)),
        "mean_entropy": float(np.mean(-np.sum(p * np.log(np.clip(p, 1e-15, 1.0)), axis=1))),
        "mean_top1_margin": float(np.mean(np.sort(p, axis=1)[:, -1] - np.sort(p, axis=1)[:, -2])),
        "max_probability_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))) if len(p) else 0.0,
    }


def delta(cand: dict, base: dict) -> dict:
    return {k: float(cand[k] - base[k]) for k in [
        "log_loss","brier","rps","top1_accuracy","top3_accuracy",
        "top1_total_2_fraction","mean_entropy","mean_top1_margin",
    ]}


def bootstrap_delta_ll(y: np.ndarray, p0: np.ndarray, p1: np.ndarray) -> dict:
    y = np.asarray(y, dtype=int)
    ix = np.arange(len(y))
    d = -np.log(np.clip(p1[ix, y], 1e-15, 1.0)) + np.log(np.clip(p0[ix, y], 1e-15, 1.0))
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    n = len(d)
    for i in range(BOOT_REPS):
        draw = rng.integers(0, n, n)
        sims[i] = float(d[draw].mean())
    return {
        "n": int(n), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta_log_loss": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "p_delta_lt_0": float(np.mean(sims < 0.0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n8-csv", required=True)
    ap.add_argument("--n9-manifest", required=True)
    args = ap.parse_args()
    n8_path = Path(args.n8_csv)
    n9_path = Path(args.n9_manifest)
    if sha256(n8_path) != N8_SHA:
        raise RuntimeError("N8 SHA mismatch")
    if sha256(n9_path) != N9_MANIFEST_SHA:
        raise RuntimeError("N9 manifest SHA mismatch")

    manifest = read_manifest(n9_path)
    dev_manifest = [r for r in manifest if r["n8_Season"] in DEV_SEASONS]
    if any(r["n8_Season"] in {"2024/2025","2025/2026"} for r in dev_manifest):
        raise RuntimeError("future season leaked into development manifest")

    target_by_n8, source_meta = decode_authorized_targets(dev_manifest)

    n8 = pd.read_csv(n8_path, dtype=str, keep_default_na=False)
    rows = []
    market_invalid = 0
    missing_label = 0
    for _, r in n8.iterrows():
        season = r["Season"]
        if season not in DEV_SEASONS:
            continue
        key = (r["sourceCode"], r["id"])
        if key not in target_by_n8:
            missing_label += 1
            continue
        values = {}
        valid = True
        for L in LINES:
            o = parse_price(r[f"O{L}"])
            u = parse_price(r[f"U{L}"])
            if o is None or u is None:
                valid = False
                break
            values[f"market_logit_{L}"] = market_logit(o, u)
        if not valid:
            market_invalid += 1
            continue
        rows.append({
            "sourceCode": r["sourceCode"],
            "n8_id": r["id"],
            "season": season,
            "target": int(target_by_n8[key]),
            **values,
        })

    data = pd.DataFrame(rows)
    if data.empty:
        raise RuntimeError("no paired development rows")
    order = {s: i for i, s in enumerate(DEV_SEASONS)}

    pred_parts = []
    folds = []
    fold_wins = 0
    max_resid = 0.0

    for test_season in TEST_SEASONS:
        ti = order[test_season]
        train = data[data["season"].map(order) < ti].copy()
        test = data[data["season"] == test_season].copy()
        if len(train) == 0 or len(test) == 0:
            raise RuntimeError(f"empty fold {test_season}")

        m0 = build_model(BASE_NUM)
        m1 = build_model(CAND_NUM)
        m0.fit(train[BASE_NUM + ["sourceCode"]], train["target"].astype(int))
        m1.fit(train[CAND_NUM + ["sourceCode"]], train["target"].astype(int))
        y = test["target"].to_numpy(dtype=int)
        p0 = aligned_predict(m0, test[BASE_NUM + ["sourceCode"]])
        p1 = aligned_predict(m1, test[CAND_NUM + ["sourceCode"]])
        b = metrics(y, p0)
        c = metrics(y, p1)
        d = delta(c, b)
        if d["log_loss"] < 0:
            fold_wins += 1
        max_resid = max(max_resid, b["max_probability_residual"], c["max_probability_residual"])
        folds.append({
            "test_season": test_season,
            "train_n": int(len(train)),
            "test_n": int(len(test)),
            "baseline": b,
            "candidate": c,
            "delta_candidate_minus_baseline": d,
        })
        pp = test[["sourceCode","n8_id","season","target"]].copy()
        for k in range(K):
            pp[f"base_p{k}"] = p0[:, k]
            pp[f"cand_p{k}"] = p1[:, k]
        pp["fold"] = test_season
        pred_parts.append(pp)

    pred = pd.concat(pred_parts, ignore_index=True)
    y = pred["target"].to_numpy(dtype=int)
    p0 = pred[[f"base_p{k}" for k in range(K)]].to_numpy(dtype=float)
    p1 = pred[[f"cand_p{k}" for k in range(K)]].to_numpy(dtype=float)
    base_pool = metrics(y, p0)
    cand_pool = metrics(y, p1)
    pooled_delta = delta(cand_pool, base_pool)
    boot = bootstrap_delta_ll(y, p0, p1)

    league_results = {}
    league_wins = 0
    for code in FILES:
        mask = pred["sourceCode"].to_numpy() == code
        yy = y[mask]
        bb = p0[mask]
        cc = p1[mask]
        if len(yy) == 0:
            league_results[code] = {"n": 0, "delta_log_loss": None}
            continue
        bm = metrics(yy, bb)
        cm = metrics(yy, cc)
        dll = cm["log_loss"] - bm["log_loss"]
        if dll < 0:
            league_wins += 1
        league_results[code] = {
            "n": int(len(yy)),
            "baseline_log_loss": bm["log_loss"],
            "candidate_log_loss": cm["log_loss"],
            "delta_log_loss": float(dll),
        }

    class_diag = {}
    for t in range(K):
        mask = y == t
        if not np.any(mask):
            class_diag[str(t)] = {"n": 0}
            continue
        class_diag[str(t)] = {
            "n": int(mask.sum()),
            "baseline_true_class_log_loss": float(np.mean(-np.log(np.clip(p0[mask, t], 1e-15, 1.0)))),
            "candidate_true_class_log_loss": float(np.mean(-np.log(np.clip(p1[mask, t], 1e-15, 1.0)))),
            "baseline_recall": float(np.mean(np.argmax(p0[mask], axis=1) == t)),
            "candidate_recall": float(np.mean(np.argmax(p1[mask], axis=1) == t)),
        }

    gates = {
        "five_folds_executed": len(folds) == 5 and all(f["test_n"] > 0 for f in folds),
        "pooled_dlogloss_lt_0": pooled_delta["log_loss"] < 0,
        "bootstrap90_upper_dlogloss_lt_0": boot["ci90_high"] < 0,
        "fold_logloss_wins_ge_4_of_5": fold_wins >= 4,
        "pooled_dbrier_le_0": pooled_delta["brier"] <= 0,
        "pooled_drps_le_0": pooled_delta["rps"] <= 0,
        "pooled_top1_delta_ge_0": pooled_delta["top1_accuracy"] >= 0,
        "pooled_top3_delta_ge_0": pooled_delta["top3_accuracy"] >= 0,
        "league_logloss_wins_ge_4_of_5": league_wins >= 4,
        "probability_residual_le_1e_12": max_resid <= 1e-12,
        "2024_25_target_values_read_zero": source_meta["target_values_2024_25_read"] == 0,
        "2025_26_target_values_read_zero": source_meta["target_values_2025_26_read"] == 0,
    }
    development_pass = all(gates.values())
    terminal = "C072N10_MULTILINE_PT_DEVELOPMENT_PASS" if development_pass else "C072N10_MULTILINE_PT_DEVELOPMENT_PARK"

    pred.to_csv(PREDICTIONS, index=False)
    summary = {
        "schema": "C072N10_MULTILINE_PT_DEVELOPMENT_V1",
        "project_line": "football3",
        "classification": "REPLICATION / REPRODUCTION",
        "terminal": terminal,
        "development_pass": development_pass,
        "n8_csv_sha256": sha256(n8_path),
        "n9_manifest_sha256": sha256(n9_path),
        "source_meta": source_meta,
        "paired_development_rows_all_seasons": int(len(data)),
        "development_oos_n": int(len(pred)),
        "market_invalid_authorized_rows_excluded": int(market_invalid),
        "authorized_rows_without_valid_label_excluded": int(missing_label),
        "folds": folds,
        "fold_logloss_wins": int(fold_wins),
        "pooled": {
            "baseline": base_pool,
            "candidate": cand_pool,
            "delta_candidate_minus_baseline": pooled_delta,
        },
        "paired_bootstrap_delta_log_loss": boot,
        "league_results": league_results,
        "league_logloss_wins": int(league_wins),
        "class_diagnostics": class_diag,
        "max_probability_residual": float(max_resid),
        "target_values_2024_25_read": 0,
        "target_values_2025_26_read": 0,
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
        "gates": gates,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
