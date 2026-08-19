#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import c074e_scorehistory_movement_directt as c074e

TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
MAX_SEASON_START = 2023
TAIL_RESIDUAL_MAX = 1e-8


def fit_r(frame: pd.DataFrame) -> float:
    e = frame["exact_total"].to_numpy(int) - 7
    cont = float(e.sum())
    exposures = float((e + 1).sum())
    if exposures <= 0:
        raise RuntimeError("no tail exposures")
    return float(cont / exposures)


def enumeration_k(r: float, limit: float = TAIL_RESIDUAL_MAX) -> int:
    k = 0
    while r ** (k + 1) > limit:
        k += 1
        if k > 500:
            raise RuntimeError("tail truncation failed")
    return k


def canonical_sha(obj: dict) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    raw = c074e.load_source(Path(a.external_root))
    raw["exact_total"] = (raw["FTHG"].astype(int) + raw["FTAG"].astype(int)).astype(int)
    feat = c074e.build_history_features(raw)
    exact_map = raw["exact_total"].to_dict()
    feat["exact_total"] = feat["row_id"].map(exact_map).astype(int)
    elig = feat.loc[feat["eligible"]].copy()
    dev = elig[(elig["season_start"] <= MAX_SEASON_START) & (elig["exact_total"] >= 7)].copy()
    if len(dev) < 300:
        raise RuntimeError(f"insufficient internal tail rows {len(dev)}")

    r = fit_r(dev)
    if not (0.0 < r < 1.0):
        raise RuntimeError(f"invalid geometric r {r}")
    k = enumeration_k(r)
    residual = float(r ** (k + 1))

    by_season = {}
    for season, g in dev.groupby("Season"):
        if len(g) >= 10:
            by_season[str(season)] = {"n": int(len(g)), "r_hat": fit_r(g), "mean_excess": float((g.exact_total - 7).mean())}
    by_league = {}
    for league, g in dev.groupby("league_key"):
        if len(g) >= 20:
            by_league[str(league)] = {"n": int(len(g)), "r_hat": fit_r(g), "mean_excess": float((g.exact_total - 7).mean())}

    expanding = {}
    for season in TEST_SEASONS:
        start = c074e.season_start(season)
        tr = elig[(elig["season_start"] < start) & (elig["exact_total"] >= 7)]
        te = elig[(elig["Season"] == season) & (elig["exact_total"] >= 7)]
        if len(tr) < 100 or len(te) < 20:
            raise RuntimeError(f"internal fold coverage drift {season}: train={len(tr)} test={len(te)}")
        expanding[season] = {
            "train_tail_n": int(len(tr)),
            "test_tail_n": int(len(te)),
            "train_r": fit_r(tr),
            "test_oracle_r_diagnostic": fit_r(te),
            "test_mean_excess": float((te.exact_total - 7).mean()),
        }

    parameter = {
        "family": "intercept-only geometric survival",
        "r": r,
        "pmf": "q(7+e|T>=7)=(1-r)*r^e",
        "predicted_mean_excess": float(r / (1.0 - r)),
        "adaptive_enumeration_K": int(k),
        "residual_after_K": residual,
        "development_horizon_max_season_start": MAX_SEASON_START,
        "development_tail_n": int(len(dev)),
        "source_revision": c074e.SOURCE_REVISION,
    }
    parameter["parameter_sha256"] = canonical_sha(parameter)

    summary = {
        "schema_version": "C075C_SIMPLE_TAIL_PARAMETER_FREEZE_V1",
        "status": "PARAMETER_FROZEN_INTERNAL_ONLY",
        "formal_weight": 0,
        "parameter": parameter,
        "internal_diagnostics": {
            "season_count_ge10_tail": int(len(by_season)),
            "league_count_ge20_tail": int(len(by_league)),
            "by_season": by_season,
            "by_league": by_league,
            "expanding_folds": expanding,
        },
        "external_label_boundary": {
            "OpenFootball_score_fields_selected": False,
            "OpenFootball_tail_membership_computed": False,
            "OpenFootball_model_or_metric_run": False,
        },
        "protected_boundaries": {
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "unified_matrix_generated": False,
        },
        "next": "commit exact parameter and frozen external stability gates before selecting any OpenFootball score field",
    }
    (out / "frozen_parameter.json").write_text(json.dumps(parameter, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
