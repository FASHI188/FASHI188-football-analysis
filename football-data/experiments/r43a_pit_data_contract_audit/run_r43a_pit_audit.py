#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9B_DATA = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf" / "data"
R9B_SNAPSHOT = R9B_DATA / "matches_r9b_xg_20000.csv"
R9B_MANIFEST = R9B_DATA / "source_manifest_r9b.json"

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
URLS = {
    "fixtures": f"{HF}/fixtures.parquet?download=true",
    "fixture_lineups": f"{HF}/fixture_lineups.parquet?download=true",
    "fixture_players": f"{HF}/fixture_players.parquet?download=true",
    "fixture_players_stats_flat": f"{HF}/fixture_players_stats_flat.parquet?download=true",
    "odds": f"{HF}/odds.parquet?download=true",
}

BASE_EVIDENCE_COMMIT = "aa05776c2a354ad6c85f53a7a6e39f7035b4d86a"
ROADMAP_COMMIT = "2227db67017891ddacae6cbe2c62f866a507b0bd"
EXPECTED_SNAPSHOT_ROWS = 20000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, path: Path) -> None:
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43a-pit-audit/1"})
    with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def pct(num: int, den: int) -> float:
    return float(num / den) if den else 0.0


def finite_quantiles(s: pd.Series) -> dict:
    x = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if x.empty:
        return {"n": 0}
    return {
        "n": int(len(x)),
        "p05": float(x.quantile(0.05)),
        "p50": float(x.quantile(0.50)),
        "p95": float(x.quantile(0.95)),
        "min": float(x.min()),
        "max": float(x.max()),
    }


def load_r9b_ids() -> tuple[set[int], dict]:
    if not R9B_SNAPSHOT.exists() or not R9B_MANIFEST.exists():
        raise RuntimeError("R9b frozen snapshot is missing; run run_experiment_r9b.py freeze first")
    ids = set()
    with R9B_SNAPSHOT.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ids.add(int(r["game_id"]))
    manifest = json.loads(R9B_MANIFEST.read_text(encoding="utf-8"))
    if len(ids) != EXPECTED_SNAPSHOT_ROWS:
        raise RuntimeError(f"expected {EXPECTED_SNAPSHOT_ROWS} R9b fixture ids, got {len(ids)}")
    return ids, manifest


def prior_history_contract() -> list[dict]:
    return [
        {
            "signal": "fixture_calendar_and_kickoff",
            "status": "AVAILABLE",
            "source": "fixtures.date_utc/league_id/team ids",
            "pit_rule": "kickoff calendar is usable only from a snapshot captured before prediction time; historical completed fixture times are safe as lagged history",
            "usable_now": True,
        },
        {
            "signal": "historical_lineup_membership",
            "status": "AVAILABLE_LAGGED_ONLY",
            "source": "fixture_players.is_starter",
            "pit_rule": "target-match lineup has no release timestamp in this dataset; only completed prior-match lineup membership may feed an earlier target",
            "usable_now": True,
        },
        {
            "signal": "historical_player_minutes",
            "status": "AVAILABLE_LAGGED_ONLY_WITH_MISSINGNESS",
            "source": "fixture_players.minutes plus fixture_players_stats_flat.games_minutes",
            "pit_rule": "minutes are postmatch facts and become usable only after the prior match is complete",
            "usable_now": True,
        },
        {
            "signal": "coach_and_formation_history",
            "status": "AVAILABLE_LAGGED_ONLY",
            "source": "fixture_lineups.coach_name/coach_api_id/formation",
            "pit_rule": "fixture_lineups has no known_at; never use the target-match row for an earlier prediction horizon; derive coach/style from prior completed matches only until a timestamped assignment source is added",
            "usable_now": True,
        },
        {
            "signal": "postmatch_team_stats_and_xg",
            "status": "AVAILABLE_WITH_KNOWN_AT",
            "source": "match_stats known_at contract already frozen by R9b/R42",
            "pit_rule": "same-date updates occur only after predictions; target-match stats never enter prematch features",
            "usable_now": True,
        },
        {
            "signal": "closing_1x2_market",
            "status": "AVAILABLE_CLOSING_ONLY_SEPARATE_TRACK",
            "source": "odds.home_win/draw/away_win/known_at",
            "pit_rule": "known_at is at/around kickoff; closing line is forbidden for any earlier prediction horizon and remains isolated from the football-blind track",
            "usable_now": False,
        },
        {
            "signal": "referee_identity",
            "status": "PARTIAL_IDENTITY_WITHOUT_ASSIGNMENT_TIMESTAMP",
            "source": "fixtures.referee_name",
            "pit_rule": "historical referee profile can be learned from completed matches, but target referee cannot be used until a separate assignment timestamp proves it was known before the prediction horizon",
            "usable_now": False,
        },
        {
            "signal": "injury_illness_suspension_history",
            "status": "MISSING_TIMESTAMPED_HISTORY",
            "source": None,
            "pit_rule": "requires status and status-change timestamp; missing record must never mean available",
            "usable_now": False,
        },
        {
            "signal": "training_return_minutes_restriction",
            "status": "MISSING_TIMESTAMPED_HISTORY",
            "source": None,
            "pit_rule": "requires timestamped return/training evidence or must remain unknown",
            "usable_now": False,
        },
        {
            "signal": "standings_and_competition_state",
            "status": "NOT_MATERIALIZED_AS_PIT_SNAPSHOT",
            "source": None,
            "pit_rule": "must reconstruct standings/aggregate/qualification state strictly from matches completed before target kickoff and competition rules",
            "usable_now": False,
        },
        {
            "signal": "travel_distance_timezones_venue",
            "status": "MISSING_GEO_CONTRACT",
            "source": None,
            "pit_rule": "requires stable venue/team location history and season-aware venue changes",
            "usable_now": False,
        },
        {
            "signal": "weather_forecast_snapshot",
            "status": "MISSING_HISTORICAL_FORECAST_TIMESTAMP",
            "source": None,
            "pit_rule": "observed match weather is not interchangeable with the forecast available at prediction time",
            "usable_now": False,
        },
        {
            "signal": "fixed_horizon_market_snapshots",
            "status": "MISSING",
            "source": None,
            "pit_rule": "need e.g. T-72h/T-24h/T-6h/T-1h timestamps; current closing-only odds cannot backfill these horizons",
            "usable_now": False,
        },
    ]


def run() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    fixture_ids, r9_manifest = load_r9b_ids()

    paths = {}
    for name, url in URLS.items():
        p = DATA / f"{name}.parquet"
        download(url, p)
        paths[name] = p

    fx = pd.read_parquet(
        paths["fixtures"],
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "referee_name", "created_at", "updated_at", "status_norm", "is_played"],
    )
    fx = fx[fx["id"].isin(fixture_ids)].copy()
    if fx["id"].nunique() != EXPECTED_SNAPSHOT_ROWS:
        raise RuntimeError(f"fixture coverage drift: {fx['id'].nunique()} / {EXPECTED_SNAPSHOT_ROWS}")
    fx["kickoff"] = pd.to_datetime(fx["date_utc"], utc=True)
    kickoff = fx.set_index("id")["kickoff"]

    lu = pd.read_parquet(
        paths["fixture_lineups"],
        columns=["fixture_id", "team_id", "team_name", "coach_name", "coach_api_id", "formation"],
    )
    lu = lu[lu["fixture_id"].isin(fixture_ids)].copy()

    fp = pd.read_parquet(
        paths["fixture_players"],
        columns=["fixture_id", "team_id", "player_id", "is_starter", "position", "minutes"],
    )
    fp = fp[fp["fixture_id"].isin(fixture_ids)].copy()
    fp["is_starter"] = fp["is_starter"].fillna(False).astype(bool)

    fps = pd.read_parquet(
        paths["fixture_players_stats_flat"],
        columns=["fixture_id", "player_id", "games_minutes", "games_substitute", "games_position"],
    )
    fps = fps[fps["fixture_id"].isin(fixture_ids)].copy()
    fps = fps.drop_duplicates(["fixture_id", "player_id"], keep="last")

    odds = pd.read_parquet(
        paths["odds"],
        columns=["fixture_id", "home_win", "draw", "away_win", "bookmaker", "source", "known_at"],
    )
    odds = odds[odds["fixture_id"].isin(fixture_ids)].copy()
    odds["known_at_ts"] = pd.to_datetime(odds["known_at"], utc=True, errors="coerce")
    odds["kickoff"] = odds["fixture_id"].map(kickoff)
    odds["known_minus_kickoff_min"] = (odds["known_at_ts"] - odds["kickoff"]).dt.total_seconds() / 60.0

    team_sides = EXPECTED_SNAPSHOT_ROWS * 2
    lineup_side_keys = lu[["fixture_id", "team_id"]].drop_duplicates()
    coach_known = lu[lu["coach_name"].notna() & (lu["coach_name"].astype(str).str.len() > 0)][["fixture_id", "team_id"]].drop_duplicates()
    formation_known = lu[lu["formation"].notna() & (lu["formation"].astype(str).str.len() > 0)][["fixture_id", "team_id"]].drop_duplicates()

    starter_counts = fp[fp["is_starter"]].groupby(["fixture_id", "team_id"])["player_id"].nunique()
    exact11_sides = int((starter_counts == 11).sum())
    starter_rows = fp[fp["is_starter"]][["fixture_id", "team_id", "player_id", "minutes"]].copy()
    starter_rows = starter_rows.merge(
        fps[["fixture_id", "player_id", "games_minutes"]],
        on=["fixture_id", "player_id"],
        how="left",
    )
    starter_rows["best_minutes"] = pd.to_numeric(starter_rows["minutes"], errors="coerce")
    gm = pd.to_numeric(starter_rows["games_minutes"], errors="coerce")
    starter_rows.loc[starter_rows["best_minutes"].isna(), "best_minutes"] = gm
    starter_minute_known = int(starter_rows["best_minutes"].notna().sum())

    referee_known = int(fx["referee_name"].notna().sum())
    odds_fixture_count = int(odds["fixture_id"].nunique())
    odds_known_at_count = int(odds["known_at_ts"].notna().sum())

    contracts = prior_history_contract()
    critical_missing = [
        x["signal"]
        for x in contracts
        if x["status"] in {
            "MISSING_TIMESTAMPED_HISTORY",
            "NOT_MATERIALIZED_AS_PIT_SNAPSHOT",
            "MISSING_GEO_CONTRACT",
            "MISSING_HISTORICAL_FORECAST_TIMESTAMP",
            "MISSING",
        }
    ]

    result = {
        "schema_version": "football3-r43a-pit-data-contract-audit-v1",
        "status": "COMPLETE",
        "classification": "PIT_SOURCE_CAPABILITY_AND_GAP_AUDIT_NO_MODEL_FIT_NO_TARGET_LABEL_USE",
        "formal_weight": 0,
        "base_evidence_commit": BASE_EVIDENCE_COMMIT,
        "roadmap_commit": ROADMAP_COMMIT,
        "question": "Which R43 prematch-context signals can be reconstructed point-in-time from the current frozen football data line, and which require new timestamped sources before modelling?",
        "governance": {
            "model_fit": False,
            "target_outcome_labels_used": False,
            "target_current_match_lineup_used_as_feature": False,
            "closing_odds_used_as_earlier_horizon_feature": False,
            "r42l_lock_modified": False,
            "missing_means_available": False,
            "audit_population": "R9b latest 20k frozen xG fixture ids, source tables inspected only for historical coverage and timestamp contract",
        },
        "r9b_source": {
            "snapshot_rows": EXPECTED_SNAPSHOT_ROWS,
            "first_date": r9_manifest.get("first_date"),
            "last_date": r9_manifest.get("last_date"),
            "snapshot_sha256": r9_manifest.get("snapshot_sha256"),
            "fixtures_sha256_from_r9b": r9_manifest.get("fixtures_sha256"),
            "match_stats_sha256_from_r9b": r9_manifest.get("match_stats_sha256"),
            "strict_prior_xg_contract": r9_manifest.get("strict_prior_xg_contract"),
        },
        "downloaded_source_hashes": {name: sha256(path) for name, path in paths.items()},
        "coverage_on_r9b_20k": {
            "fixture_rows": int(fx["id"].nunique()),
            "fixture_side_denominator": team_sides,
            "lineup_side_rows": int(len(lineup_side_keys)),
            "lineup_side_coverage": pct(len(lineup_side_keys), team_sides),
            "coach_side_rows": int(len(coach_known)),
            "coach_side_coverage": pct(len(coach_known), team_sides),
            "formation_side_rows": int(len(formation_known)),
            "formation_side_coverage": pct(len(formation_known), team_sides),
            "sides_with_exact_11_starters": exact11_sides,
            "exact_11_starter_side_coverage": pct(exact11_sides, team_sides),
            "starter_rows": int(len(starter_rows)),
            "starter_rows_with_minutes_from_either_player_table": starter_minute_known,
            "starter_minute_coverage": pct(starter_minute_known, len(starter_rows)),
            "referee_fixture_rows": referee_known,
            "referee_fixture_coverage": pct(referee_known, EXPECTED_SNAPSHOT_ROWS),
            "odds_fixture_rows": odds_fixture_count,
            "odds_fixture_coverage": pct(odds_fixture_count, EXPECTED_SNAPSHOT_ROWS),
            "odds_rows": int(len(odds)),
            "odds_rows_with_known_at": odds_known_at_count,
            "odds_known_at_minus_kickoff_minutes": finite_quantiles(odds["known_minus_kickoff_min"]),
        },
        "signal_contracts": contracts,
        "critical_missing_or_unmaterialized": critical_missing,
        "gates": {
            "current_source_contract_audit_passed": True,
            "historical_lineup_and_minutes_can_seed_r43b_c": bool(exact11_sides > 0 and starter_minute_known > 0),
            "r43a_full_pit_spine_ready_for_context_modelling": False,
            "reason": "Current source is sufficient for lagged lineup/player/coach history and postmatch known_at features, but it lacks timestamped historical availability/injury status and several other prediction-time context snapshots required by R43A.",
        },
        "next_action": {
            "step": "R43A2_SOURCE_ACQUISITION_AND_RECONSTRUCTION",
            "priority": [
                "timestamped injury/illness/suspension history",
                "historical availability/return status where reliable",
                "standings and competition-state reconstruction from completed fixtures",
                "venue/team geography for rest/travel features",
                "coach tenure reconstruction using only prior completed fixture evidence",
            ],
            "do_not_start_r43b_full_model_until": "availability source contract is PIT-safe; a lineup-only historical baseline may be built separately but cannot be called the full R43B availability model",
        },
        "limitations": [
            "fixture_lineups has no known_at timestamp, so target-match coach/formation/lineup fields are not admissible at an earlier prediction horizon.",
            "odds in the current dataset are predominantly closing lines known at/around kickoff and cannot be backfilled as T-24h or other earlier snapshots.",
            "referee_name lacks assignment timestamp, so the target referee is not PIT-safe for arbitrary earlier horizons.",
            "This audit checks source capability and coverage only; it does not claim predictive gain.",
        ],
    }

    p = OUT / "summary_r43a_pit_contract_audit.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    p = OUT / "summary_r43a_pit_contract_audit.json"
    x = json.loads(p.read_text(encoding="utf-8"))
    assert x["status"] == "COMPLETE"
    assert x["formal_weight"] == 0
    assert x["governance"]["model_fit"] is False
    assert x["governance"]["target_outcome_labels_used"] is False
    assert x["governance"]["target_current_match_lineup_used_as_feature"] is False
    assert x["governance"]["closing_odds_used_as_earlier_horizon_feature"] is False
    assert x["governance"]["r42l_lock_modified"] is False
    assert x["coverage_on_r9b_20k"]["fixture_rows"] == EXPECTED_SNAPSHOT_ROWS
    assert x["gates"]["current_source_contract_audit_passed"] is True
    assert x["gates"]["r43a_full_pit_spine_ready_for_context_modelling"] is False
    assert "injury_illness_suspension_history" in x["critical_missing_or_unmaterialized"]
    print("R43A PIT source contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
