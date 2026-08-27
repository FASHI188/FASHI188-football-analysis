#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
ROOT = HERE.parents[2]
R9B_SNAPSHOT = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
HF_URLS = {
    "players": f"{HF}/players.parquet?download=true",
    "teams": f"{HF}/teams.parquet?download=true",
    "fixture_players": f"{HF}/fixture_players.parquet?download=true",
    "fixtures": f"{HF}/fixtures.parquet?download=true",
}

AVAILABILITY_COMMIT = "5c6286c24dd58b3c9a6b4f5ccbd0c6e6466cb7dd"
REEP_COMMIT = "0ec59faa5d81615b7a8200ae6121023a3bc14ce3"
R43A1_EVIDENCE_HEAD = "88f6bba5a1f6b341dd07d7cc70ea573ea5133b24"
AVAIL_ROOT = Path(os.environ.get("R43A2_AVAIL_ROOT", "/tmp/availability-data"))
REEP_PEOPLE = Path(os.environ.get("R43A2_REEP_PEOPLE", "/tmp/reep-people.csv"))
REEP_TEAMS = Path(os.environ.get("R43A2_REEP_TEAMS", "/tmp/reep-teams.csv"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, path: Path) -> None:
    if path.exists():
        return
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43a2/1"})
    with urllib.request.urlopen(req, timeout=600) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def numeric_key(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype("Int64")


def strict_one_to_one(df: pd.DataFrame, left: str, right: str) -> tuple[pd.DataFrame, dict]:
    x = df[[left, right]].dropna().drop_duplicates().copy()
    lc = x.groupby(left)[right].nunique()
    rc = x.groupby(right)[left].nunique()
    bad_l = set(lc[lc != 1].index.tolist())
    bad_r = set(rc[rc != 1].index.tolist())
    ok = x[~x[left].isin(bad_l) & ~x[right].isin(bad_r)].drop_duplicates([left, right])
    return ok, {
        "candidate_pairs": int(len(x)),
        "ambiguous_left_keys": int(len(bad_l)),
        "ambiguous_right_keys": int(len(bad_r)),
        "strict_pairs": int(len(ok)),
    }


def load_r9b_ids() -> set[int]:
    if not R9B_SNAPSHOT.exists():
        raise RuntimeError("R9b frozen 20k snapshot missing")
    out = set()
    with R9B_SNAPSHOT.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.add(int(r["game_id"]))
    if len(out) != 20000:
        raise RuntimeError(f"R9b fixture count drift: {len(out)}")
    return out


def parse_availability() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw = AVAIL_ROOT / "raw"
    if not raw.exists():
        raise RuntimeError(f"availability raw root missing: {raw}")
    player_rows = []
    club_rows = []
    status_counts = Counter()
    detail_rows = 0
    scraped_times = set()
    file_count = 0
    for path in sorted(raw.glob("*/*/*.json")):
        file_count += 1
        code = path.parts[-3]
        season = path.parts[-2]
        obj = json.loads(path.read_text(encoding="utf-8"))
        scraped_at = obj.get("scrapedAt")
        if scraped_at:
            scraped_times.add(str(scraped_at))
        club_tm = obj.get("tmId")
        club_rows.append({
            "competition_code": code,
            "season": str(season),
            "club": obj.get("club"),
            "club_tm": club_tm,
            "scraped_at": scraped_at,
        })
        for comp in obj.get("competitions", []):
            comp_code = comp.get("code") or code
            for p in comp.get("players", []):
                ptm = p.get("tmId")
                for m in p.get("matches", []):
                    status = str(m.get("status") or "unknown")
                    status_counts[status] += 1
                    if m.get("detail"):
                        detail_rows += 1
                    player_rows.append({
                        "competition_code": comp_code,
                        "season": str(season),
                        "club_tm": club_tm,
                        "club": obj.get("club"),
                        "player_tm": ptm,
                        "player": p.get("name"),
                        "position": p.get("position"),
                        "round": str(m.get("round")) if m.get("round") is not None else None,
                        "status": status,
                        "detail": m.get("detail"),
                        "scraped_at": scraped_at,
                    })
    pr = pd.DataFrame(player_rows)
    cr = pd.DataFrame(club_rows).drop_duplicates()
    if pr.empty or cr.empty:
        raise RuntimeError("availability source parsed zero rows")
    pr["player_tm"] = numeric_key(pr["player_tm"])
    pr["club_tm"] = numeric_key(pr["club_tm"])
    cr["club_tm"] = numeric_key(cr["club_tm"])
    meta = {
        "json_files": int(file_count),
        "competition_codes": sorted(set(pr["competition_code"].dropna().astype(str))),
        "seasons": sorted(set(pr["season"].dropna().astype(str))),
        "player_round_status_rows": int(len(pr)),
        "unique_player_tm_ids": int(pr["player_tm"].nunique()),
        "unique_club_tm_ids": int(cr["club_tm"].nunique()),
        "status_counts": dict(sorted(status_counts.items())),
        "rows_with_detail": int(detail_rows),
        "unique_scraped_at_values": int(len(scraped_times)),
    }
    return pr, cr, meta


def choose_col(cols, candidates, label):
    for c in candidates:
        if c in cols:
            return c
    raise RuntimeError(f"cannot find {label}; candidates={candidates} columns={sorted(cols)}")


def run() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    fixture_ids = load_r9b_ids()

    if not REEP_PEOPLE.exists() or not REEP_TEAMS.exists():
        raise RuntimeError("pinned Reep people/teams files missing")

    paths = {}
    for name, url in HF_URLS.items():
        p = DATA / f"{name}.parquet"
        download(url, p)
        paths[name] = p

    avail_players, avail_clubs, avail_meta = parse_availability()

    reep_people = pd.read_csv(
        REEP_PEOPLE,
        usecols=["key_wikidata", "name", "key_transfermarkt", "key_api_football"],
        low_memory=False,
    )
    reep_people["tm"] = numeric_key(reep_people["key_transfermarkt"])
    reep_people["api"] = numeric_key(reep_people["key_api_football"])
    person_cross, person_cross_diag = strict_one_to_one(reep_people, "tm", "api")

    reep_teams = pd.read_csv(
        REEP_TEAMS,
        usecols=["key_wikidata", "name", "key_transfermarkt", "key_api_football"],
        low_memory=False,
    )
    reep_teams["tm"] = numeric_key(reep_teams["key_transfermarkt"])
    reep_teams["api"] = numeric_key(reep_teams["key_api_football"])
    team_cross, team_cross_diag = strict_one_to_one(reep_teams, "tm", "api")

    players = pd.read_parquet(paths["players"])
    p_id = choose_col(players.columns, ["id", "player_id"], "soccer player internal id")
    p_api = choose_col(players.columns, ["api_football_id", "api_id"], "soccer player API-Football id")
    p_name = choose_col(players.columns, ["name", "player_name"], "soccer player name")
    players = players[[p_id, p_api, p_name]].copy()
    players[p_id] = numeric_key(players[p_id])
    players[p_api] = numeric_key(players[p_api])
    own_person_cross, own_person_diag = strict_one_to_one(players, p_api, p_id)

    teams = pd.read_parquet(paths["teams"])
    t_id = choose_col(teams.columns, ["id", "team_id"], "soccer team internal id")
    t_api = choose_col(teams.columns, ["api_football_id", "api_id"], "soccer team API-Football id")
    t_name = choose_col(teams.columns, ["name", "team_name"], "soccer team name")
    teams = teams[[t_id, t_api, t_name]].copy()
    teams[t_id] = numeric_key(teams[t_id])
    teams[t_api] = numeric_key(teams[t_api])
    own_team_cross, own_team_diag = strict_one_to_one(teams, t_api, t_id)

    person_bridge = person_cross.merge(own_person_cross, left_on="api", right_on=p_api, how="inner")
    person_bridge = person_bridge[["tm", "api", p_id]].drop_duplicates()
    person_bridge.columns = ["tm", "api", "soccer_player_id"]
    team_bridge = team_cross.merge(own_team_cross, left_on="api", right_on=t_api, how="inner")
    team_bridge = team_bridge[["tm", "api", t_id]].drop_duplicates()
    team_bridge.columns = ["tm", "api", "soccer_team_id"]

    avail_player_ids = set(int(x) for x in avail_players["player_tm"].dropna().unique())
    avail_club_ids = set(int(x) for x in avail_clubs["club_tm"].dropna().unique())
    mapped_avail_player_tm = avail_player_ids & set(int(x) for x in person_bridge["tm"].dropna().unique())
    mapped_avail_club_tm = avail_club_ids & set(int(x) for x in team_bridge["tm"].dropna().unique())

    fp = pd.read_parquet(paths["fixture_players"], columns=["fixture_id", "team_id", "player_id", "is_starter"])
    fp = fp[fp["fixture_id"].isin(fixture_ids)].copy()
    fp["player_id"] = numeric_key(fp["player_id"])
    fp["team_id"] = numeric_key(fp["team_id"])
    fp["is_starter"] = fp["is_starter"].fillna(False).astype(bool)
    mapped_soccer_players = set(int(x) for x in person_bridge[person_bridge["tm"].isin(mapped_avail_player_tm)]["soccer_player_id"].dropna().unique())
    mapped_soccer_teams = set(int(x) for x in team_bridge[team_bridge["tm"].isin(mapped_avail_club_tm)]["soccer_team_id"].dropna().unique())
    fp_avail_leagues = fp[fp["team_id"].isin(mapped_soccer_teams)]
    fp_player_mapped = fp_avail_leagues[fp_avail_leagues["player_id"].isin(mapped_soccer_players)]
    starters = fp_avail_leagues[fp_avail_leagues["is_starter"]]
    starter_mapped = starters[starters["player_id"].isin(mapped_soccer_players)]

    fixtures = pd.read_parquet(paths["fixtures"])
    fixture_columns = list(fixtures.columns)
    round_candidates = [c for c in fixture_columns if "round" in c.lower() or "matchday" in c.lower()]
    season_candidates = [c for c in fixture_columns if "season" in c.lower()]

    avail_player_round = avail_players[["competition_code", "season", "club_tm", "player_tm", "round", "status"]].copy()
    avail_player_round = avail_player_round.merge(person_bridge, left_on="player_tm", right_on="tm", how="left")
    avail_player_round = avail_player_round.merge(
        team_bridge.rename(columns={"tm": "club_tm_bridge", "api": "club_api"}),
        left_on="club_tm",
        right_on="club_tm_bridge",
        how="left",
    )
    mapped_status_rows = int(avail_player_round["soccer_player_id"].notna().sum())

    retrospective_only = True
    direct_pit_feature_allowed = False
    matchday_alignment_ready = bool(round_candidates and season_candidates)

    result = {
        "schema_version": "football3-r43a2-availability-source-identity-bridge-audit-v1",
        "status": "COMPLETE",
        "classification": "EXTERNAL_RETROSPECTIVE_AVAILABILITY_LABEL_SOURCE_AND_IDENTITY_BRIDGE_AUDIT_NO_MODEL_FIT",
        "formal_weight": 0,
        "r43a1_evidence_head": R43A1_EVIDENCE_HEAD,
        "pinned_external_sources": {
            "availability_data": {
                "repository": "withqwerty/availability-data",
                "commit": AVAILABILITY_COMMIT,
                "interpretation": "retrospectively scraped per-player per-matchday availability/status labels; not a historical prediction-time news snapshot",
            },
            "reep": {
                "repository": "withqwerty/reep",
                "commit": REEP_COMMIT,
                "people_sha256": sha256(REEP_PEOPLE),
                "teams_sha256": sha256(REEP_TEAMS),
                "interpretation": "identity bridge only; strict one-to-one Transfermarkt <-> API-Football keys retained",
            },
        },
        "governance": {
            "model_fit": False,
            "target_result_labels_used": False,
            "r42l_lock_modified": False,
            "availability_used_as_direct_historical_prematch_feature": direct_pit_feature_allowed,
            "availability_source_retrospective_only": retrospective_only,
            "injury_detail_or_expected_return_used_as_prematch_feature": False,
            "ambiguous_identity_pairs_discarded": True,
            "status_can_be_used_as_evaluation_or_training_target_only": True,
        },
        "availability_source": avail_meta,
        "identity_bridge": {
            "reep_person_crosswalk": person_cross_diag,
            "reep_team_crosswalk": team_cross_diag,
            "soccer_player_api_to_internal": own_person_diag,
            "soccer_team_api_to_internal": own_team_diag,
            "availability_unique_player_tm_ids": int(len(avail_player_ids)),
            "availability_player_tm_ids_mapped_through_reep_to_soccer": int(len(mapped_avail_player_tm)),
            "availability_player_identity_coverage": float(len(mapped_avail_player_tm) / len(avail_player_ids)) if avail_player_ids else 0.0,
            "availability_unique_club_tm_ids": int(len(avail_club_ids)),
            "availability_club_tm_ids_mapped_through_reep_to_soccer": int(len(mapped_avail_club_tm)),
            "availability_club_identity_coverage": float(len(mapped_avail_club_tm) / len(avail_club_ids)) if avail_club_ids else 0.0,
            "availability_status_rows_with_soccer_player_identity": mapped_status_rows,
            "availability_status_row_identity_coverage": float(mapped_status_rows / len(avail_player_round)) if len(avail_player_round) else 0.0,
        },
        "r9b_20k_overlap": {
            "fixture_player_rows_for_mapped_availability_clubs": int(len(fp_avail_leagues)),
            "fixture_player_rows_with_availability_mapped_player_identity": int(len(fp_player_mapped)),
            "player_row_identity_coverage_within_mapped_clubs": float(len(fp_player_mapped) / len(fp_avail_leagues)) if len(fp_avail_leagues) else 0.0,
            "starter_rows_for_mapped_availability_clubs": int(len(starters)),
            "starter_rows_with_availability_mapped_player_identity": int(len(starter_mapped)),
            "starter_identity_coverage_within_mapped_clubs": float(len(starter_mapped) / len(starters)) if len(starters) else 0.0,
            "mapped_soccer_team_ids": int(len(mapped_soccer_teams)),
            "mapped_soccer_player_ids": int(len(mapped_soccer_players)),
        },
        "fixture_schema_alignment": {
            "round_like_columns": round_candidates,
            "season_like_columns": season_candidates,
            "direct_round_season_alignment_possible_from_current_fixture_schema": matchday_alignment_ready,
            "warning": "Availability source records matchday round rather than fixture ID/date. Do not align by simple chronological order because postponed/reordered rounds exist. A deterministic competition-season-round fixture bridge is required before per-match status labels are joined.",
        },
        "gates": {
            "identity_bridge_viable": bool(len(mapped_avail_player_tm) > 0 and len(mapped_avail_club_tm) > 0),
            "availability_source_valid_as_retrospective_status_label": True,
            "availability_source_valid_as_direct_historical_prematch_feature": False,
            "per_fixture_status_join_ready": matchday_alignment_ready,
            "r43b_full_availability_feature_model_ready": False,
        },
        "next_action": {
            "step": "R43A3_MATCHDAY_AND_PIT_RECONSTRUCTION_PLUS_R43B0_LINEUP_BASELINE",
            "work": [
                "build deterministic competition-season-round fixture mapping; never infer postponed rounds by chronology",
                "reconstruct standings/competition state from completed fixtures only",
                "materialize coach tenure and recent-selection history from prior completed fixtures",
                "build R43B0 probabilistic P(start)/P(bench)/expected-minutes baseline from lagged lineup/minute history only",
                "use retrospective injury/suspension statuses only as auxiliary evaluation labels until genuine timestamped prematch availability snapshots exist",
            ],
        },
        "limitations": [
            "The availability dataset was scraped retrospectively and does not preserve the original timestamp at which each injury/suspension report became known to the market.",
            "Therefore it cannot be injected as a historical T-24h/T-6h feature without leakage; it is label/evaluation material only.",
            "Reep mappings are external identity data and may be incomplete; only strict one-to-one mappings are retained.",
            "This audit measures identity/source feasibility, not predictive gain.",
        ],
    }

    p = OUT / "summary_r43a2_availability_bridge_audit.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    p = OUT / "summary_r43a2_availability_bridge_audit.json"
    x = json.loads(p.read_text(encoding="utf-8"))
    assert x["status"] == "COMPLETE"
    assert x["formal_weight"] == 0
    assert x["governance"]["model_fit"] is False
    assert x["governance"]["r42l_lock_modified"] is False
    assert x["governance"]["availability_used_as_direct_historical_prematch_feature"] is False
    assert x["gates"]["availability_source_valid_as_direct_historical_prematch_feature"] is False
    assert x["gates"]["r43b_full_availability_feature_model_ready"] is False
    assert x["gates"]["identity_bridge_viable"] is True
    print("R43A2 availability identity bridge audit verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
