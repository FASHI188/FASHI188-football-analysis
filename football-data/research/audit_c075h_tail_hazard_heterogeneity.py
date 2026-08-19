#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from scipy.stats import chi2

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071

SCHEMA = "C075H_TAIL_HAZARD_HETEROGENEITY_AUDIT_V1"
FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM = 72180
Z90 = 1.6448536269514722
MINIMUMS = {
    ("league", "h1"): 30,
    ("league", "h2"): 15,
    ("calendar_year", "h1"): 100,
    ("calendar_year", "h2"): 40,
    ("league_year", "h1"): 15,
    ("league_year", "h2"): 8,
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


def wilson90(x: int, n: int) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = x / n
    z = Z90
    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return float(center - half), float(center + half)


def hazard_counts(frame: pd.DataFrame, hazard: str) -> tuple[int, int]:
    e = frame["excess"].to_numpy(int)
    if hazard == "h1":
        return int((e >= 1).sum()), int(len(e))
    if hazard == "h2":
        risk = e >= 1
        return int((e >= 2).sum()), int(risk.sum())
    if hazard == "h3":
        risk = e >= 2
        return int((e >= 3).sum()), int(risk.sum())
    raise ValueError(hazard)


def heterogeneity(rows: list[dict]) -> dict:
    if len(rows) < 2:
        return {"groups": len(rows), "Q": None, "df": None, "pvalue": None, "I2": None}
    x = np.asarray([r["success"] for r in rows], dtype=float)
    n = np.asarray([r["risk"] for r in rows], dtype=float)
    # Frozen continuity-corrected logit meta-analytic heterogeneity calculation.
    theta = np.log((x + 0.5) / (n - x + 0.5))
    var = 1.0 / (x + 0.5) + 1.0 / (n - x + 0.5)
    w = 1.0 / var
    theta_bar = float(np.sum(w * theta) / np.sum(w))
    q = float(np.sum(w * np.square(theta - theta_bar)))
    df = len(rows) - 1
    pvalue = float(chi2.sf(q, df))
    i2 = float(max(0.0, (q - df) / q)) if q > 0 else 0.0
    return {
        "groups": int(len(rows)),
        "Q": q,
        "df": int(df),
        "pvalue": pvalue,
        "I2": i2,
        "fixed_effect_logit": theta_bar,
        "fixed_effect_probability": float(1.0 / (1.0 + math.exp(-theta_bar))),
    }


def distribution_summary(rows: list[dict]) -> dict:
    if not rows:
        return {"groups": 0}
    p = np.asarray([r["proportion"] for r in rows], dtype=float)
    x = sum(int(r["success"]) for r in rows)
    n = sum(int(r["risk"]) for r in rows)
    return {
        "groups": int(len(rows)),
        "min": float(np.min(p)),
        "p25": float(np.quantile(p, 0.25)),
        "median": float(np.median(p)),
        "p75": float(np.quantile(p, 0.75)),
        "max": float(np.max(p)),
        "weighted_mean": float(x / n),
        "total_success": int(x),
        "total_risk": int(n),
    }


def make_group_rows(tail: pd.DataFrame, group_type: str, hazard: str, minimum: int, global_p: float) -> list[dict]:
    if group_type == "league":
        grouped = tail.groupby("league_id", sort=True)
    elif group_type == "calendar_year":
        grouped = tail.groupby("calendar_year", sort=True)
    elif group_type == "league_year":
        grouped = tail.groupby(["league_id", "calendar_year"], sort=True)
    else:
        raise ValueError(group_type)

    out = []
    for key, g in grouped:
        x, n = hazard_counts(g, hazard)
        if n < minimum:
            continue
        lo, hi = wilson90(x, n)
        if isinstance(key, tuple):
            key_text = "|".join(str(v) for v in key)
        else:
            key_text = str(key)
        out.append({
            "group_type": group_type,
            "group": key_text,
            "hazard": hazard,
            "success": int(x),
            "risk": int(n),
            "proportion": float(x / n),
            "wilson90_low": lo,
            "wilson90_high": hi,
            "global_probability_outside_cell_wilson90": bool(global_p < lo or global_p > hi),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    fp = Path(a.fixtures); sp = Path(a.stats); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    if sha256(fp) != FIX_SHA or sha256(sp) != STAT_SHA:
        raise RuntimeError("pinned source SHA mismatch")

    c071.utc = utc_ns

    # Identity reconstruction reads no goal/result labels.
    fixtures = pd.read_parquet(fp, columns=audit.FIXTURE_COLS)
    fixtures["date_utc"] = utc_ns(fixtures["date_utc"])
    fixtures = fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(["date_utc", "id"]).reset_index(drop=True)
    fixtures["id"] = fixtures["id"].astype("int64")
    stats = pd.read_parquet(sp, columns=audit.STAT_COLS)
    stats["known_at"] = utc_ns(stats["known_at"])
    stats = stats.dropna(subset=["fixture_id", "known_at"])
    eligible, _ = c071.eligible_identities(fixtures, stats)
    sealed = eligible[(eligible.date_utc >= DEV_CUTOFF) & (eligible.date_utc < CONFIRM_END)]
    if len(sealed) != EXPECTED_CONFIRM:
        raise RuntimeError(f"C071 sealed identity drift {len(sealed)} != {EXPECTED_CONFIRM}")

    # This helper applies a pyarrow predicate date_utc < 2024-01-01 at file-read time.
    labels = c071.read_dev_labels(fp)
    if len(labels) and not (labels["date_utc"] < DEV_CUTOFF).all():
        raise RuntimeError("development label horizon breach")
    dev_id = eligible[eligible.date_utc < DEV_CUTOFF].copy()
    dev = dev_id.merge(labels[["id", "goals_home", "goals_away"]], on="id", how="left", validate="one_to_one")
    dev = dev.dropna(subset=["goals_home", "goals_away"]).copy()
    dev["goals_home"] = dev.goals_home.astype(int)
    dev["goals_away"] = dev.goals_away.astype(int)
    dev["exact_total"] = dev.goals_home + dev.goals_away
    dev["excess"] = dev.exact_total - 7
    dev["calendar_year"] = dev.date_utc.dt.year.astype(int)
    tail = dev[dev.exact_total >= 7].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)

    global_hazards = {}
    for hz in ("h1", "h2", "h3"):
        x, n = hazard_counts(tail, hz)
        lo, hi = wilson90(x, n)
        global_hazards[hz] = {
            "success": int(x), "risk": int(n), "probability": float(x / n),
            "wilson90_low": lo, "wilson90_high": hi,
        }

    all_rows = []
    analyses = {}
    for group_type in ("league", "calendar_year", "league_year"):
        for hz in ("h1", "h2"):
            minimum = MINIMUMS[(group_type, hz)]
            rows = make_group_rows(tail, group_type, hz, minimum, global_hazards[hz]["probability"])
            all_rows.extend(rows)
            key = f"{group_type}_{hz}"
            analyses[key] = {
                "minimum_risk": int(minimum),
                "distribution": distribution_summary(rows),
                "heterogeneity": heterogeneity(rows),
                "cells_excluding_global_from_wilson90": int(sum(r["global_probability_outside_cell_wilson90"] for r in rows)),
            }

    ly = analyses["league_year_h1"]["heterogeneity"]
    lg = analyses["league_h1"]["heterogeneity"]
    strong_ly = bool(ly["groups"] >= 10 and ly["pvalue"] is not None and ly["pvalue"] < 0.01 and ly["I2"] >= 0.50)
    strong_lg = bool(lg["groups"] >= 5 and lg["pvalue"] is not None and lg["pvalue"] < 0.05 and lg["I2"] >= 0.30)
    if strong_ly and strong_lg:
        classification = "STRONG_DOMAIN_HETEROGENEITY"
    elif strong_ly or strong_lg:
        classification = "MODERATE_DOMAIN_HETEROGENEITY"
    else:
        classification = "HETEROGENEITY_NOT_ESTABLISHED"

    group_df = pd.DataFrame(all_rows)
    group_df.to_csv(out / "hazard_group_cells.csv", index=False)

    summary = {
        "schema_version": SCHEMA,
        "status": classification,
        "predictive_model_test": False,
        "formal_weight": 0,
        "claim_boundary": "structural descriptive audit only; cannot itself supply or promote q(k|T>=7)",
        "population": {
            "eligible_pre2024_completed": int(len(dev)),
            "tail_pre2024": int(len(tail)),
            "tail_date_min": str(tail.date_utc.min()),
            "tail_date_max": str(tail.date_utc.max()),
            "unique_leagues": int(tail.league_id.nunique()),
            "calendar_years": [int(x) for x in sorted(tail.calendar_year.unique())],
        },
        "global_hazards": global_hazards,
        "global_memoryless_diagnostic": {
            "h1_minus_h2": float(global_hazards["h1"]["probability"] - global_hazards["h2"]["probability"]),
            "h2_minus_h3": float(global_hazards["h2"]["probability"] - global_hazards["h3"]["probability"]),
            "note": "nested risk sets; differences are descriptive only, not treated as independent-sample tests",
        },
        "group_analyses": analyses,
        "classification_checks": {
            "league_year_h1_strong_condition": strong_ly,
            "league_h1_strong_condition": strong_lg,
        },
        "boundaries": {
            "C075C_consumed_tail_labels_used": False,
            "C075E_consumed_tail_labels_used": False,
            "C075F_scored_internal_OOS_used_as_new_target_source": False,
            "C071_post2024_goal_labels_opened": False,
            "C071_sealed_identity_count": int(len(sealed)),
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "model_fit": False,
            "unified_matrix_generated": False,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
