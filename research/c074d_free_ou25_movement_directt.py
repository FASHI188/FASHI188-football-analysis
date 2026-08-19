#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SOURCE_REVISION = "279978313f9c16a210fa80e8986fa22f0f866fba"
TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
C_FIXED = 0.1
BOOTSTRAPS = 3000
BOOTSTRAP_SEED = 20260819
CLASSES = np.arange(8, dtype=int)
USECOLS = [
    "Date", "country", "league", "Season", "FTHG", "FTAG",
    "over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close",
]
ODD_COLS = ["over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close"]


def devig_over(over_odds: pd.Series, under_odds: pd.Series) -> pd.Series:
    io = 1.0 / over_odds
    iu = 1.0 / under_odds
    return io / (io + iu)


def logit(p: pd.Series) -> pd.Series:
    p = p.clip(1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def season_start(s: str) -> int:
    return int(str(s).split("-")[0])


def build_model(numeric_features: list[str]) -> Pipeline:
    prep = ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), numeric_features),
            ("league", OneHotEncoder(handle_unknown="ignore"), ["league_key"]),
        ],
        remainder="drop",
    )
    clf = LogisticRegression(
        C=C_FIXED,
        penalty="l2",
        solver="lbfgs",
        max_iter=2000,
        multi_class="multinomial",
        class_weight=None,
    )
    return Pipeline([("prep", prep), ("clf", clf)])


def aligned_proba(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    model_classes = model.named_steps["clf"].classes_.astype(int)
    out = np.zeros((len(X), len(CLASSES)), dtype=float)
    for j, cls in enumerate(model_classes):
        out[:, cls] = raw[:, j]
    row_sums = out.sum(axis=1)
    if not np.allclose(row_sums, 1.0, atol=1e-10):
        raise RuntimeError("probability conservation failed")
    return out


def row_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    n = len(y)
    one = np.zeros_like(p)
    one[np.arange(n), y] = 1.0
    ll = -np.log(np.clip(p[np.arange(n), y], 1e-15, 1.0))
    brier = np.square(p - one).sum(axis=1)
    cdf_p = np.cumsum(p, axis=1)[:, :-1]
    cdf_y = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cdf_p - cdf_y).sum(axis=1) / (len(CLASSES) - 1)
    top1 = (np.argmax(p, axis=1) == y).astype(float)
    top3_idx = np.argpartition(p, -3, axis=1)[:, -3:]
    top3 = np.array([float(y[i] in top3_idx[i]) for i in range(n)])
    return {"ll": ll, "brier": brier, "rps": rps, "top1": top1, "top3": top3}


def summarize(y: np.ndarray, p: np.ndarray, rows: dict[str, np.ndarray]) -> dict:
    auc3 = roc_auc_score((y >= 3).astype(int), p[:, 3:].sum(axis=1))
    return {
        "n": int(len(y)),
        "logloss": float(rows["ll"].mean()),
        "brier": float(rows["brier"].mean()),
        "rps": float(rows["rps"].mean()),
        "top1": float(rows["top1"].mean()),
        "top3": float(rows["top3"].mean()),
        "auc_t_ge_3": float(auc3),
        "probability_sum_max_abs_error": float(np.max(np.abs(p.sum(axis=1) - 1.0))),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-root", required=True)
    ap.add_argument("--out", default="artifacts/c074d_directt")
    args = ap.parse_args()

    root = Path(args.external_root)
    files = sorted(root.glob("data/**/*.csv"))
    if not files:
        raise SystemExit("no source csv files")

    frames = []
    for path in files:
        try:
            d = pd.read_csv(path, usecols=USECOLS, low_memory=False)
        except ValueError:
            continue
        d["source_file"] = str(path.relative_to(root))
        frames.append(d)
    if not frames:
        raise SystemExit("no files with frozen C074-D columns")

    df = pd.concat(frames, ignore_index=True)
    for c in ODD_COLS + ["FTHG", "FTAG"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    date_ok = pd.to_datetime(df["Date"], errors="coerce").notna()
    odds_ok = df[ODD_COLS].notna().all(axis=1) & (df[ODD_COLS] > 1.0).all(axis=1)
    goals_ok = df[["FTHG", "FTAG"]].notna().all(axis=1) & (df[["FTHG", "FTAG"]] >= 0).all(axis=1)
    season_ok = df["Season"].astype(str).str.match(r"^\d{4}-\d{4}$")
    df = df.loc[date_ok & odds_ok & goals_ok & season_ok].copy()

    df["T"] = np.minimum((df["FTHG"] + df["FTAG"]).astype(int), 7)
    df["league_key"] = df["country"].astype(str) + "|" + df["league"].astype(str)
    p_open = devig_over(df["over_2.5_open"], df["under_2.5_open"])
    p_close = devig_over(df["over_2.5_close"], df["under_2.5_close"])
    df["open_logit"] = logit(p_open)
    df["movement_logit"] = logit(p_close) - logit(p_open)
    df["season_start"] = df["Season"].map(season_start)

    fold_records = []
    all_y = []
    all_pb = []
    all_pc = []
    all_mb = {k: [] for k in ["ll", "brier", "rps", "top1", "top3"]}
    all_mc = {k: [] for k in ["ll", "brier", "rps", "top1", "top3"]}

    for test_season in TEST_SEASONS:
        test_start = season_start(test_season)
        train = df.loc[df["season_start"] < test_start].copy()
        test = df.loc[df["Season"] == test_season].copy()
        if train.empty or test.empty:
            raise RuntimeError(f"empty frozen fold {test_season}")

        y_train = train["T"].to_numpy(dtype=int)
        y_test = test["T"].to_numpy(dtype=int)
        if set(CLASSES) - set(np.unique(y_train)):
            raise RuntimeError(f"training fold {test_season} lacks a target class")

        baseline = build_model(["open_logit"])
        candidate = build_model(["open_logit", "movement_logit"])
        baseline.fit(train[["open_logit", "movement_logit", "league_key"]], y_train)
        candidate.fit(train[["open_logit", "movement_logit", "league_key"]], y_train)

        Xtest = test[["open_logit", "movement_logit", "league_key"]]
        pb = aligned_proba(baseline, Xtest)
        pc = aligned_proba(candidate, Xtest)
        mb = row_metrics(y_test, pb)
        mc = row_metrics(y_test, pc)
        sb = summarize(y_test, pb, mb)
        sc = summarize(y_test, pc, mc)

        fold_records.append({
            "test_season": test_season,
            "train_n": int(len(train)),
            "test_n": int(len(test)),
            "baseline": sb,
            "candidate": sc,
            "delta_candidate_minus_baseline": {
                "logloss": sc["logloss"] - sb["logloss"],
                "brier": sc["brier"] - sb["brier"],
                "rps": sc["rps"] - sb["rps"],
                "top1_pp": 100.0 * (sc["top1"] - sb["top1"]),
                "top3_pp": 100.0 * (sc["top3"] - sb["top3"]),
                "auc_t_ge_3": sc["auc_t_ge_3"] - sb["auc_t_ge_3"],
            },
        })
        all_y.append(y_test)
        all_pb.append(pb)
        all_pc.append(pc)
        for k in all_mb:
            all_mb[k].append(mb[k])
            all_mc[k].append(mc[k])

    y = np.concatenate(all_y)
    pb = np.vstack(all_pb)
    pc = np.vstack(all_pc)
    mb = {k: np.concatenate(v) for k, v in all_mb.items()}
    mc = {k: np.concatenate(v) for k, v in all_mc.items()}
    sb = summarize(y, pb, mb)
    sc = summarize(y, pc, mc)

    dll = mc["ll"] - mb["ll"]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    boot = np.empty(BOOTSTRAPS, dtype=float)
    n = len(dll)
    for b in range(BOOTSTRAPS):
        idx = rng.integers(0, n, size=n)
        boot[b] = float(dll[idx].mean())
    boot90 = [float(np.quantile(boot, 0.05)), float(np.quantile(boot, 0.95))]

    fold_ll_wins = int(sum(r["delta_candidate_minus_baseline"]["logloss"] < 0 for r in fold_records))
    deltas = {
        "logloss": sc["logloss"] - sb["logloss"],
        "brier": sc["brier"] - sb["brier"],
        "rps": sc["rps"] - sb["rps"],
        "top1_pp": 100.0 * (sc["top1"] - sb["top1"]),
        "top3_pp": 100.0 * (sc["top3"] - sb["top3"]),
        "auc_t_ge_3": sc["auc_t_ge_3"] - sb["auc_t_ge_3"],
    }
    gate = {
        "pooled_dlogloss_lt_0": deltas["logloss"] < 0,
        "fold_ll_wins_ge_4_of_5": fold_ll_wins >= 4,
        "pooled_dbrier_le_0": deltas["brier"] <= 0,
        "pooled_drps_le_0": deltas["rps"] <= 0,
        "bootstrap90_upper_dlogloss_lt_0": boot90[1] < 0,
    }
    passed = all(gate.values())

    summary = {
        "experiment": "C074-D",
        "contract": "research/C074D_CONTRACT.md",
        "research_only": True,
        "formal_weight": 0,
        "development_probe_not_confirmation": True,
        "source_repo": "nm2890/football-data",
        "source_revision": SOURCE_REVISION,
        "source_pit_boundary": "opening/closing semantics only; no immutable per-row quote timestamp; single O/U2.5 line",
        "rows_after_frozen_validity_filter": int(len(df)),
        "oos_rows": int(len(y)),
        "test_seasons": TEST_SEASONS,
        "model": {
            "type": "multinomial_logistic_regression",
            "C": C_FIXED,
            "baseline_features": ["open_logit", "league_key"],
            "candidate_added_feature": "movement_logit=logit(p_close_over)-logit(p_open_over)",
            "feature_search": 0,
            "C_search": 0,
            "alternate_folds": 0,
        },
        "baseline": sb,
        "candidate": sc,
        "delta_candidate_minus_baseline": deltas,
        "fold_ll_wins": fold_ll_wins,
        "paired_bootstrap": {
            "n": BOOTSTRAPS,
            "seed": BOOTSTRAP_SEED,
            "dlogloss_90_interval": boot90,
        },
        "gate_checks": gate,
        "terminal": "MOVEMENT_INCREMENT_PASS" if passed else "MOVEMENT_INCREMENT_NOT_ESTABLISHED",
        "folds": fold_records,
        "sealed_assets_touched": {
            "C071_reserve_52180": 0,
            "C070F_confirmation_1597": 0,
            "A05": 0,
            "protected": 0,
        },
        "scientific_boundary": (
            "A PASS establishes research-level incremental information from a coarse public O/U2.5 open-to-close movement signal "
            "for complete exact-T prediction under this frozen development probe. It does not establish formal timestamped dynamic multi-line OU, "
            "does not promote formal weight, and is not a sealed confirmation."
        ),
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame([
        {
            "test_season": r["test_season"],
            "train_n": r["train_n"],
            "test_n": r["test_n"],
            **{f"baseline_{k}": v for k, v in r["baseline"].items() if isinstance(v, (int, float))},
            **{f"candidate_{k}": v for k, v in r["candidate"].items() if isinstance(v, (int, float))},
            **{f"delta_{k}": v for k, v in r["delta_candidate_minus_baseline"].items()},
        }
        for r in fold_records
    ]).to_csv(out / "fold_metrics.csv", index=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
