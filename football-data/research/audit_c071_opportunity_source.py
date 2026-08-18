#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

SCHEMA_VERSION = "C071_OPPORTUNITY_SOURCE_AUDIT_V1"
FIXTURE_COLS = ["id", "date_utc", "league_id", "home_team_id", "away_team_id"]
STAT_COLS = [
    "fixture_id", "home_shots_total", "away_shots_total",
    "home_shots_on_goal", "away_shots_on_goal",
    "home_shots_inside_box", "away_shots_inside_box",
    "home_shots_outside_box", "away_shots_outside_box",
    "home_penalties", "away_penalties", "known_at",
]
CORE = [c for c in STAT_COLS if c not in {"fixture_id", "known_at"}]
THRESHOLDS = [4, 6, 8, 10, 15, 20]
FORBIDDEN = {"goals_home", "goals_away", "btts", "result", "winner"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _schema(path: Path) -> list[str]:
    return pq.ParquetFile(path).schema_arrow.names


def _utc(s):
    return pd.to_datetime(s, utc=True, errors="coerce")


def prior_counts(fixtures: pd.DataFrame, hist: pd.DataFrame, threshold: int):
    # Each complete historical match contributes one availability timestamp to each team.
    avail = {}
    for team_id, g in hist.groupby("team_id", sort=False):
        arr = np.sort(g["known_at"].dropna().values.astype("datetime64[ns]"))
        avail[int(team_id)] = arr

    target_time = fixtures["date_utc"].values.astype("datetime64[ns]")
    home_n = np.zeros(len(fixtures), dtype=np.int32)
    away_n = np.zeros(len(fixtures), dtype=np.int32)
    for i, (h, a, t) in enumerate(zip(fixtures.home_team_id, fixtures.away_team_id, target_time)):
        ah = avail.get(int(h)) if pd.notna(h) else None
        aa = avail.get(int(a)) if pd.notna(a) else None
        if ah is not None:
            home_n[i] = int(np.searchsorted(ah, t, side="left"))
        if aa is not None:
            away_n[i] = int(np.searchsorted(aa, t, side="left"))
    eligible = (home_n >= threshold) & (away_n >= threshold)
    return home_n, away_n, eligible


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    fp, sp, out = Path(args.fixtures), Path(args.stats), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    f_schema, s_schema = _schema(fp), _schema(sp)
    missing_f = sorted(set(FIXTURE_COLS) - set(f_schema))
    missing_s = sorted(set(STAT_COLS) - set(s_schema))
    if missing_f or missing_s:
        raise RuntimeError(f"schema mismatch missing fixtures={missing_f} stats={missing_s}")
    if set(FIXTURE_COLS) & FORBIDDEN:
        raise RuntimeError("forbidden target label selected")

    # Column projection is a hard zero-label boundary: no result/goal columns are read.
    fixtures = pd.read_parquet(fp, columns=FIXTURE_COLS)
    stats = pd.read_parquet(sp, columns=STAT_COLS)
    if list(fixtures.columns) != FIXTURE_COLS:
        raise RuntimeError("fixture projection drift")
    fixtures["date_utc"] = _utc(fixtures["date_utc"])
    stats["known_at"] = _utc(stats["known_at"])
    fixtures = fixtures.dropna(subset=["id", "date_utc", "league_id", "home_team_id", "away_team_id"]).copy()
    fixtures["id"] = fixtures["id"].astype("int64")
    stats = stats.dropna(subset=["fixture_id", "known_at"]).copy()
    stats["fixture_id"] = stats["fixture_id"].astype("int64")

    if fixtures["id"].duplicated().any():
        raise RuntimeError("duplicate fixture identity")
    if stats["fixture_id"].duplicated().any():
        raise RuntimeError("match_stats not one-row-per-fixture")

    src = stats.merge(
        fixtures[["id", "date_utc", "league_id", "home_team_id", "away_team_id"]],
        left_on="fixture_id", right_on="id", how="inner", validate="one_to_one"
    )
    src["core_complete"] = src[CORE].notna().all(axis=1)
    pit_delta = (src["known_at"] - src["date_utc"]).dt.total_seconds()
    known_before_or_at_kickoff = int((pit_delta <= 0).sum())
    if known_before_or_at_kickoff:
        raise RuntimeError(f"post-match stats known_at integrity failed n={known_before_or_at_kickoff}")

    complete = src[src["core_complete"]].copy()
    home = pd.DataFrame({
        "fixture_id": complete.fixture_id, "league_id": complete.league_id,
        "team_id": complete.home_team_id, "known_at": complete.known_at,
    })
    away = pd.DataFrame({
        "fixture_id": complete.fixture_id, "league_id": complete.league_id,
        "team_id": complete.away_team_id, "known_at": complete.known_at,
    })
    hist = pd.concat([home, away], ignore_index=True)

    coverage = src[["fixture_id", "date_utc", "league_id", "core_complete"]].copy()
    coverage["calendar_year"] = coverage["date_utc"].dt.year
    cov = coverage.groupby(["league_id", "calendar_year"], as_index=False).agg(
        stats_rows=("fixture_id", "size"), complete_rows=("core_complete", "sum")
    )
    cov["complete_rate"] = cov.complete_rows / cov.stats_rows
    cov.sort_values(["calendar_year", "league_id"]).to_csv(out / "league_year_core_coverage.csv", index=False)

    threshold_summary = {}
    preferred_manifest = None
    for n in THRESHOLDS:
        hn, an, eligible = prior_counts(fixtures, hist, n)
        dates = fixtures.loc[eligible, "date_utc"]
        threshold_summary[str(n)] = {
            "eligible_targets": int(eligible.sum()),
            "eligible_fraction": float(eligible.mean()),
            "date_min": str(dates.min()) if len(dates) else None,
            "date_max": str(dates.max()) if len(dates) else None,
            "league_count": int(fixtures.loc[eligible, "league_id"].nunique()),
        }
        if n == 8:
            m = fixtures.loc[eligible, FIXTURE_COLS].copy()
            m["home_prior_complete_stats"] = hn[eligible]
            m["away_prior_complete_stats"] = an[eligible]
            preferred_manifest = m.sort_values(["date_utc", "id"])

    if preferred_manifest is None:
        raise RuntimeError("preferred manifest missing")
    preferred_manifest.to_csv(out / "eligible_identity_threshold8.csv", index=False)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "C071_SOURCE_AUDIT_COMPLETE",
        "source": {
            "fixtures_sha256": sha256(fp), "match_stats_sha256": sha256(sp),
            "fixtures_bytes": fp.stat().st_size, "match_stats_bytes": sp.stat().st_size,
            "fixtures_schema": f_schema, "match_stats_schema": s_schema,
        },
        "zero_label_boundary": {
            "fixture_columns_read": FIXTURE_COLS,
            "forbidden_outcome_columns_read": [],
            "target_outcomes_read": False,
            "model_fit": False, "scoring": False, "tuning": False,
        },
        "coverage": {
            "fixture_identity_rows": int(len(fixtures)),
            "match_stats_rows_projected": int(len(stats)),
            "stats_joined_to_fixture_identity": int(len(src)),
            "core_complete_stats_rows": int(src.core_complete.sum()),
            "core_complete_rate": float(src.core_complete.mean()) if len(src) else 0.0,
            "team_history_rows": int(len(hist)),
            "league_year_cells": int(len(cov)),
        },
        "pit": {
            "known_at_nonnull_joined": int(src.known_at.notna().sum()),
            "known_at_le_kickoff": known_before_or_at_kickoff,
            "median_known_delay_minutes": float(np.nanmedian(pit_delta) / 60.0),
            "min_known_delay_minutes": float(np.nanmin(pit_delta) / 60.0),
            "strict_history_rule": "known_at < target.date_utc",
        },
        "eligible_by_min_prior_complete_matches_each_team": threshold_summary,
        "threshold8_manifest_rows": int(len(preferred_manifest)),
        "boundaries": {
            "fresh_confirmation_claim_allowed": False,
            "formal_weight": 0,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
