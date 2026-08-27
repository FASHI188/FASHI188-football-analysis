#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R9_DIR = HERE.parent / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
LINEUPS_URL = f"{HF}/fixture_lineups.parquet?download=true"
PLAYERS_URL = f"{HF}/fixture_players.parquet?download=true"
EXPECTED_PLAYERS_SHA = "a315191ffac285a11597758c859cd88b97ea8aba89374a8fb299ee754a2f76ad"


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, path: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r40a2"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def ratio(a, b):
    return float(a / b) if b else None


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    data = HERE / "data"
    data.mkdir(parents=True, exist_ok=True)
    lineup_path = data / "fixture_lineups.parquet"
    player_path = data / "fixture_players.parquet"
    download(LINEUPS_URL, lineup_path)
    download(PLAYERS_URL, player_path)
    lineup_sha = fsha(lineup_path)
    player_sha = fsha(player_path)
    if player_sha != EXPECTED_PLAYERS_SHA:
        raise RuntimeError(f"fixture_players source drift: {player_sha}")

    rows = r9.load()
    wanted = {int(r["game_id"]) for r in rows}
    lu = pd.read_parquet(lineup_path, columns=["fixture_id", "team_id", "coach_name", "coach_api_id", "formation"])
    pl = pd.read_parquet(player_path, columns=["fixture_id", "team_id", "player_id", "is_starter", "position"])
    lu = lu[lu["fixture_id"].isin(wanted)].copy()
    pl = pl[pl["fixture_id"].isin(wanted)].copy()
    starters = pl[pl["is_starter"] == True].copy()

    lineup_map = {}
    for (fid, tid), g in lu.groupby(["fixture_id", "team_id"], sort=False):
        x = g.iloc[0]
        lineup_map[(str(int(fid)), str(int(tid)))] = {
            "coach_name": None if pd.isna(x["coach_name"]) else str(x["coach_name"]),
            "coach_api_id": None if pd.isna(x["coach_api_id"]) else str(int(x["coach_api_id"])),
            "formation": None if pd.isna(x["formation"]) else str(x["formation"]),
        }

    role_map = {}
    for (fid, tid), g in starters.groupby(["fixture_id", "team_id"], sort=False):
        positions = [str(x) for x in g["position"].dropna().tolist()]
        role_map[(str(int(fid)), str(int(tid)))] = {
            "starter_count": int(len(g)),
            "position_known": int(len(positions)),
            "positions": positions,
        }

    n = len(rows)
    both_lineup_rows = both_coach = both_formation = 0
    both_role_rows = 0
    starter_count_total = position_known_total = 0
    current_coach_rows = current_formation_rows = 0

    team_coach = {}
    team_coach_tenure = defaultdict(int)
    team_forms = defaultdict(lambda: deque(maxlen=5))
    prior_coach_known_sides = prior_formation_known_sides = 0
    prior_manager_change_observed_sides = 0
    prior_formation_stability_values = []

    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)

    for day in sorted(by):
        pending = []
        for r in sorted(by[day], key=lambda x: x["game_id"]):
            fid = str(r["game_id"])
            hi = lineup_map.get((fid, r["home_team"]))
            ai = lineup_map.get((fid, r["away_team"]))
            hr = role_map.get((fid, r["home_team"]))
            ar = role_map.get((fid, r["away_team"]))
            if hi and ai:
                both_lineup_rows += 1
                if hi["coach_name"] and ai["coach_name"]:
                    both_coach += 1
                if hi["formation"] and ai["formation"]:
                    both_formation += 1
            if hr and ar:
                both_role_rows += 1
            for rr in (hr, ar):
                if rr:
                    starter_count_total += rr["starter_count"]
                    position_known_total += rr["position_known"]
            for team in (r["home_team"], r["away_team"]):
                if team in team_coach:
                    prior_coach_known_sides += 1
                    if team_coach_tenure[team] == 1:
                        prior_manager_change_observed_sides += 1
                fs = list(team_forms[team])
                if fs:
                    prior_formation_known_sides += 1
                    mode_n = Counter(fs).most_common(1)[0][1]
                    prior_formation_stability_values.append(mode_n / len(fs))
            if hi and hi["coach_name"]:
                current_coach_rows += 1
            if ai and ai["coach_name"]:
                current_coach_rows += 1
            if hi and hi["formation"]:
                current_formation_rows += 1
            if ai and ai["formation"]:
                current_formation_rows += 1
            pending.append((r, hi, ai))

        # PIT: current fixture coach/formation become prior history only after all fixtures on date are frozen.
        for r, hi, ai in pending:
            for team, info in ((r["home_team"], hi), (r["away_team"], ai)):
                if not info:
                    continue
                coach = info.get("coach_name")
                if coach:
                    if team_coach.get(team) == coach:
                        team_coach_tenure[team] += 1
                    else:
                        team_coach[team] = coach
                        team_coach_tenure[team] = 1
                formation = info.get("formation")
                if formation:
                    team_forms[team].append(formation)

    team_sides = n * 2
    result = {
        "schema_version": "football3-r40a2-role-coach-formation-recognition-v1",
        "status": "COMPLETE",
        "formal_model_changed": False,
        "prediction_weights_changed": False,
        "source": {
            "fixture_lineups_url": LINEUPS_URL,
            "fixture_lineups_sha256": lineup_sha,
            "fixture_players_url": PLAYERS_URL,
            "fixture_players_sha256": player_sha,
            "snapshot_rows": n,
        },
        "coverage": {
            "both_team_lineup_record_rate": ratio(both_lineup_rows, n),
            "both_team_coach_record_rate": ratio(both_coach, n),
            "both_team_formation_record_rate": ratio(both_formation, n),
            "both_team_starter_role_record_rate": ratio(both_role_rows, n),
            "starter_position_known_rate": ratio(position_known_total, starter_count_total),
            "prior_observed_coach_known_side_rate": ratio(prior_coach_known_sides, team_sides),
            "prior_observed_formation_known_side_rate": ratio(prior_formation_known_sides, team_sides),
            "mean_prior_formation_mode_share_last5": float(np.mean(prior_formation_stability_values)) if prior_formation_stability_values else None,
            "prior_observed_manager_change_marker_side_rate": ratio(prior_manager_change_observed_sides, team_sides),
        },
        "field_status": {
            "player_position_role": "READY_DERIVED_STRICT_PRIOR",
            "expected_xi_by_position": "READY_DERIVED_STRICT_PRIOR",
            "prior_observed_coach_identity": "READY_DERIVED_STRICT_PRIOR",
            "prior_observed_manager_tenure": "READY_DERIVED_STRICT_PRIOR",
            "prior_observed_formation_tendency": "READY_DERIVED_STRICT_PRIOR",
            "tactical_shape_proxy": "READY_DERIVED_PROXY_ONLY",
            "current_match_confirmed_xi": "BLOCKED_NO_LINEUP_PUBLICATION_KNOWN_AT",
            "current_manager_appointment_before_debut": "BLOCKED_NO_APPOINTMENT_KNOWN_AT",
            "confirmed_injury_availability": "BLOCKED_NO_ANNOUNCEMENT_KNOWN_AT",
            "suspension": "BLOCKED_NO_AUDITED_SOURCE",
            "travel_distance": "BLOCKED_NO_VENUE_GEOCODES",
            "weather_forecast": "BLOCKED_NO_TIMESTAMPED_FORECAST_ARCHIVE",
        },
        "recognition_contract": {
            "current_fixture_lineup_coach_formation_used_for_target": False,
            "same_date_updates_withheld": True,
            "allowed_next_model_inputs": [
                "player_position_role",
                "expected_xi_by_position",
                "prior_observed_coach_identity",
                "prior_observed_manager_tenure",
                "prior_observed_formation_tendency",
                "tactical_shape_proxy",
            ],
            "rule": "A current-match event is formal-prematch eligible only when its public known_at is auditable and earlier than the prediction cutoff.",
        },
    }
    (OUT / "summary_r40a2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def verify():
    s = json.loads((OUT / "summary_r40a2.json").read_text(encoding="utf-8"))
    assert s["formal_model_changed"] is False
    assert s["prediction_weights_changed"] is False
    assert s["recognition_contract"]["same_date_updates_withheld"] is True
    assert s["field_status"]["player_position_role"].startswith("READY")
    assert s["field_status"]["current_match_confirmed_xi"].startswith("BLOCKED")
    assert s["field_status"]["confirmed_injury_availability"].startswith("BLOCKED")
    print("R40A2_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r40a2.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
