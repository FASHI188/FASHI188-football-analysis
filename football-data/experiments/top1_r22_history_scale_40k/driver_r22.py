#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

import run_experiment_r22 as m


def freeze_extra_strictly_before_first_day(base_rows):
    m.DATA.mkdir(parents=True, exist_ok=True)
    fp = m.DATA / "fixtures.parquet"
    sp = m.DATA / "match_stats.parquet"
    m.r9.download(m.r9.FIX_URL, fp)
    m.r9.download(m.r9.STAT_URL, sp)

    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"],
    )
    st = pd.read_parquet(
        sp,
        columns=["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"],
    )
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["date"] = pd.to_datetime(df["date_utc"], utc=True).dt.date.astype(str)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[
        (df["known"] > pd.to_datetime(df["date_utc"], utc=True))
        & df["home_xg"].between(0, 6)
        & df["away_xg"].between(0, 6)
    ]
    df = df.sort_values(["date", "id"]).drop_duplicates("id")

    first_date = min(x["date"] for x in base_rows)
    pre = df[df["date"] < first_date]
    if len(pre) < m.EXTRA_N:
        raise RuntimeError(f"only {len(pre)} valid FT xG rows strictly before frozen R9b first day {first_date}; need {m.EXTRA_N}")
    ex = pre.tail(m.EXTRA_N)

    out = []
    for z in ex.itertuples(index=False):
        out.append(
            {
                "date": z.date,
                "game_id": str(int(z.id)),
                "competition_id": str(int(z.league_id)),
                "home_team": str(int(z.home_team_id)),
                "away_team": str(int(z.away_team_id)),
                "home_goals": int(z.goals_home),
                "away_goals": int(z.goals_away),
                "home_xg": float(z.home_xg),
                "away_xg": float(z.away_xg),
                "xg_known_at": z.known.isoformat(),
            }
        )

    base_ids = {x["game_id"] for x in base_rows}
    overlap = [x["game_id"] for x in out if x["game_id"] in base_ids]
    if overlap:
        raise RuntimeError(f"extra history overlaps frozen R9b snapshot count={len(overlap)}")
    if out[-1]["date"] >= first_date:
        raise RuntimeError("extra history strict-date boundary failed")

    p = m.DATA / "extra_r22_xg_20000.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=m.r9.FIELDS)
        w.writeheader()
        w.writerows(out)

    base_manifest = json.loads((m.r9.DATA / "source_manifest_r9b.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "football3-top1-r22-history-scale-40k",
        "status": "FROZEN_EXTRA_20000_STRICTLY_BEFORE_R9B_FIRST_DAY",
        "source_dataset": "eatpizzanot/soccer-dataset",
        "license": "CC-BY-4.0",
        "current_fixtures_sha256": m.r9.fsha(fp),
        "current_match_stats_sha256": m.r9.fsha(sp),
        "frozen_r9b_snapshot_sha256": base_manifest["snapshot_sha256"],
        "frozen_r9b_first_date": first_date,
        "frozen_r9b_last_date": max(x["date"] for x in base_rows),
        "extra_rows": len(out),
        "extra_first_date": out[0]["date"],
        "extra_last_date": out[-1]["date"],
        "extra_snapshot_sha256": m.r9.fsha(p),
        "selection": "latest 20000 currently-valid FT xG rows with date strictly earlier than exact frozen R9b first date; original frozen 20000 appended unchanged",
        "strict_prior_xg_contract": True,
        "same_day_boundary_excluded": True,
        "odds_used": False,
        "market_prices_used": False,
    }
    (m.DATA / "source_manifest_r22.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fp.unlink(missing_ok=True)
    sp.unlink(missing_ok=True)
    return out, manifest


m.freeze_extra = freeze_extra_strictly_before_first_day

try:
    m.run()
except Exception as exc:
    p = Path(__file__).resolve().parent / "results" / "error_r22.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"exception_type": type(exc).__name__, "message": str(exc)}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raise
