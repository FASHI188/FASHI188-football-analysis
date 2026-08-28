#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
F0_DIR = ROOT / "football-data" / "experiments" / "r43f0_coach_rotation_depth_lineup_older20k"
if str(F0_DIR) not in sys.path:
    sys.path.insert(0, str(F0_DIR))
import run_r43f0 as f0  # noqa: E402

SOURCE_R43F0_HEAD = "1581e8dfca000fc66d63313a5990bf7a803a8b77"


def load_latest20k_rows():
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    f0.download(f0.FIX_URL, fp)
    f0.download(f0.STAT_URL, sp)
    if f0.sha256(fp) != f0.EXPECTED_FIX_SHA:
        raise RuntimeError("fixtures source drift")
    if f0.sha256(sp) != f0.EXPECTED_STAT_SHA:
        raise RuntimeError("match_stats source drift")
    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"],
    )
    st = pd.read_parquet(sp, columns=["fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at"])
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["kick"] = pd.to_datetime(df["date_utc"], utc=True)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > df["kick"]) & (df["home_xg"].between(0, 6)) & (df["away_xg"].between(0, 6))]
    df["date"] = df["kick"].dt.date.astype(str)
    df = df.sort_values(["date", "id"]).drop_duplicates("id")
    if len(df) < 20000:
        raise RuntimeError(f"need >=20000 valid rows, got {len(df)}")
    sl = df.iloc[-20000:].copy()
    rows = [
        {"date": str(x.date), "fixture_id": int(x.id), "home_team": int(x.home_team_id), "away_team": int(x.away_team_id)}
        for x in sl.itertuples(index=False)
    ]
    return rows, {
        "fixtures_sha256": f0.sha256(fp),
        "match_stats_sha256": f0.sha256(sp),
        "valid_joined_rows": int(len(df)),
        "slice": "[-20000:]",
        "rows": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
    }


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    # Execute the exact frozen R43F0 mechanism; only the source slice and output directory change.
    f0.DATA = DATA
    f0.OUT = OUT
    f0.load_older20k_rows = load_latest20k_rows
    raw = f0.run()
    raw["schema_version"] = "football3-r43f1-context-lineup-latest20k-replication-v1"
    raw["classification"] = "EXACT_FROZEN_R43F0_MECHANISM_REPLICATION_ON_LATEST20K_ALREADY_USED_ERA"
    raw["source_r43f0_head"] = SOURCE_R43F0_HEAD
    raw.pop("source_r43e2_head", None)
    raw["governance"]["source_overlap_with_r43e0_e1_e2_scored_blocks"] = True
    raw["governance"]["source_overlap_with_r43f0_scored_block"] = False
    raw["governance"]["mechanism_changed_from_r43f0"] = False
    raw["governance"]["test_used_for_parameter_or_feature_selection"] = False
    if raw["gate"]["passed"]:
        raw["gate"]["action"] = "R43F0_CONTEXT_LINEUP_REPLICATES_ACROSS_TWO_ERAS_NEEDS_FORWARD_OR_1X2_TRANSLATION_CONFIRMATION"
    else:
        raw["gate"]["action"] = "DO_NOT_PROMOTE_R43F1_AND_DO_NOT_RETUNE_ON_THIS_TEST"
    raw["limitations"] = [
        "This latest20k era was previously used by other research stages, so this is replication evidence rather than a pristine untouched test.",
        "The R43F0 mechanism, feature list, model C, probability projection and gate are unchanged.",
        "Current-match coach identity and current-match lineup remain excluded before prediction; same-date updates remain post-prediction.",
        "This stage still changes no 1X2 probabilities and leaves R42L untouched.",
    ]
    p = OUT / "summary_r43f1_context_lineup_latest20k.json"
    p.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(raw, indent=2, ensure_ascii=False))
    return raw


def verify():
    d = json.loads((OUT / "summary_r43f1_context_lineup_latest20k.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE"
    assert d["formal_weight"] == 0
    assert d["source_r43f0_head"] == SOURCE_R43F0_HEAD
    g = d["governance"]
    assert g["source_overlap_with_r43f0_scored_block"] is False
    assert g["mechanism_changed_from_r43f0"] is False
    assert g["target_current_match_lineup_used_as_feature"] is False
    assert g["target_current_match_coach_used_for_prediction"] is False
    assert g["same_date_updates_before_prediction"] is False
    assert g["parameter_search"] is False
    assert g["test_used_for_parameter_or_feature_selection"] is False
    assert g["r42l_lock_modified"] is False
    assert d["split"]["date_safe"] is True
    print("R43F1 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": run, "verify": verify}[cmd]()
