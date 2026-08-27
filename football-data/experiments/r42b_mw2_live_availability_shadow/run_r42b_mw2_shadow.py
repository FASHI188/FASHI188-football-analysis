#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42_DIR = HERE.parent / "r42_live_availability_shadow"
sys.path.insert(0, str(R42_DIR))
import run_r42_shadow_r1 as r42r1  # noqa: E402

r42 = r42r1.r42
PIT_SNAPSHOT = HERE / "inputs" / "prematch_events_v2_snapshot.jsonl"
PIT_SNAPSHOT_BLOB_SHA = "0279012a5c3aba8cf64148061d1c9dc7a1a3d581"
PIT_SOURCE_HEAD = "eed32f3f105ecda107b8ea49da779627dcc7d9c2"
EXPECTED_PL_LEAGUE_ID = 1

TARGETS = [
    {
        "key": "ENG_PL_MW2_TOT_NEW",
        "competition": "Premier League",
        "home_team": "Tottenham Hotspur",
        "away_team": "Newcastle United",
        "kickoff_at_utc": "2026-08-29T16:30:00Z",
        "target_record_ids": ["2bce72ff23c04760e52b94bb634ce5a3dadcbd0e77a09bb10972b5adcb94c5e0"],
    },
    {
        "key": "ENG_PL_MW2_AVL_ARS",
        "competition": "Premier League",
        "home_team": "Aston Villa",
        "away_team": "Arsenal",
        "kickoff_at_utc": "2026-08-31T19:00:00Z",
        "target_record_ids": ["d979014ce914567c93d85196c4b3700875d97b124956ca781002e06a32aad0d9"],
    },
]


def team_candidates(teams: pd.DataFrame, target: str):
    scored = []
    for x in teams.itertuples(index=False):
        score = float(r42r1.team_match_score(target, str(x.name)))
        if score >= 0.72:
            scored.append((score, int(x.id), str(x.name)))
    scored.sort(reverse=True)
    if not scored:
        raise RuntimeError(f"no credible team candidates for {target}")
    return scored[:20]


def load_target_fixture_pair_fixed(tmp: Path):
    """Resolve duplicate team names through the target home/away fixture pair.

    R42-r1 resolves each team independently. That correctly refuses ambiguous exact
    duplicates such as the two dataset rows named Arsenal. R42B instead carries all
    credible name candidates into the frozen Premier League fixture table and selects
    the unique home/away pair nearest the current official kickoff within 14 days.
    No outcome or post-match field is used.
    """
    fp = tmp / "fixtures.parquet"
    tp = tmp / "teams.parquet"
    r42.download(r42.FIXTURES_URL, fp)
    r42.download(r42.TEAMS_URL, tp)
    fixture_sha = r42.fsha(fp)
    if fixture_sha != r42.EXPECTED_FIXTURES_SHA256:
        raise RuntimeError(f"fixtures source drift: {fixture_sha}")

    teams = pd.read_parquet(tp, columns=["id", "name"])
    home_candidates = team_candidates(teams, r42.TARGET["home_team"])
    away_candidates = team_candidates(teams, r42.TARGET["away_team"])
    hs = {pid: score for score, pid, _ in home_candidates}
    aas = {pid: score for score, pid, _ in away_candidates}
    names = {int(x.id): str(x.name) for x in teams.itertuples(index=False)}

    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "status_norm", "is_played"],
    )
    fx["dt"] = pd.to_datetime(fx["date_utc"], utc=True)
    official_dt = pd.Timestamp(r42.TARGET["kickoff_at_utc"])
    pair = fx[
        (fx["league_id"] == EXPECTED_PL_LEAGUE_ID)
        & fx["home_team_id"].isin(hs)
        & fx["away_team_id"].isin(aas)
    ].copy()
    if pair.empty:
        raise RuntimeError(
            "no Premier League fixture joins credible team candidates: "
            f"home={home_candidates[:5]} away={away_candidates[:5]}"
        )
    pair["distance_seconds"] = (pair["dt"] - official_dt).abs().dt.total_seconds()
    pair["name_score"] = pair["home_team_id"].map(hs) + pair["away_team_id"].map(aas)
    pair = pair[pair["distance_seconds"] <= 14 * 86400].copy()
    if pair.empty:
        raise RuntimeError("no candidate team-pair fixture within 14 days of official kickoff")
    pair = pair.sort_values(["distance_seconds", "name_score", "dt", "id"], ascending=[True, False, True, True])
    x = pair.iloc[0]
    if len(pair) > 1:
        y = pair.iloc[1]
        if float(y["distance_seconds"]) == float(x["distance_seconds"]) and float(y["name_score"]) == float(x["name_score"]):
            raise RuntimeError(
                "team-pair fixture resolution tie: "
                + json.dumps(pair[["id", "date_utc", "home_team_id", "away_team_id", "distance_seconds", "name_score"]].head(5).to_dict("records"), default=str)
            )

    h, a = int(x["home_team_id"]), int(x["away_team_id"])
    source_dt = pd.Timestamp(x["dt"])
    schedule_delta_seconds = int((source_dt - official_dt).total_seconds())
    row = {
        "date": official_dt.date().isoformat(),
        "game_id": str(int(x["id"])),
        "competition_id": str(int(x["league_id"])),
        "home_team": str(h),
        "away_team": str(a),
    }
    meta = {
        "fixture_id": int(x["id"]),
        "league_id": int(x["league_id"]),
        "home_team_id": h,
        "away_team_id": a,
        "home_team_identity": {
            "target_name": r42.TARGET["home_team"],
            "resolved_team_id": h,
            "resolved_dataset_name": names[h],
            "score": float(hs[h]),
            "resolution_rule": "name candidates joined through unique target fixture pair",
            "top_candidates": [
                {"team_id": int(pid), "name": name, "score": float(score)}
                for score, pid, name in home_candidates[:5]
            ],
        },
        "away_team_identity": {
            "target_name": r42.TARGET["away_team"],
            "resolved_team_id": a,
            "resolved_dataset_name": names[a],
            "score": float(aas[a]),
            "resolution_rule": "name candidates joined through unique target fixture pair",
            "top_candidates": [
                {"team_id": int(pid), "name": name, "score": float(score)}
                for score, pid, name in away_candidates[:5]
            ],
        },
        "official_current_kickoff_utc": official_dt.isoformat(),
        "source_snapshot_kickoff_utc": source_dt.isoformat(),
        "source_schedule_stale": bool(schedule_delta_seconds != 0),
        "source_schedule_delta_seconds": schedule_delta_seconds,
        "matching_rule": "credible team-name candidate sets + Premier League league_id=1 + unique home/away fixture pair + nearest source-snapshot kickoff within 14 days; current official kickoff remains PIT cutoff",
        "status_norm_in_source": str(x["status_norm"]),
        "is_played_in_source": bool(x["is_played"]),
        "fixtures_sha256": fixture_sha,
        "teams_sha256": r42.fsha(tp),
    }
    return row, meta, fp, tp


def run_one(target: dict):
    target_dir = OUT / target["key"]
    target_dir.mkdir(parents=True, exist_ok=True)
    r42.TARGET = {
        "competition": target["competition"],
        "home_team": target["home_team"],
        "away_team": target["away_team"],
        "kickoff_at_utc": target["kickoff_at_utc"],
    }
    r42.LEDGER_PATH = PIT_SNAPSHOT
    r42.OUT = target_dir
    r42.load_target_fixture = load_target_fixture_pair_fixed
    r42.run()
    return json.loads((target_dir / "summary_r42_shadow.json").read_text(encoding="utf-8"))


def run():
    if not PIT_SNAPSHOT.is_file():
        raise RuntimeError(f"missing frozen PIT snapshot: {PIT_SNAPSHOT}")
    rows = [json.loads(x) for x in PIT_SNAPSHOT.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = {x["record_id"] for x in rows}
    assert len(rows) == 7, len(rows)
    for target in TARGETS:
        assert set(target["target_record_ids"]).issubset(ids)

    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for target in TARGETS:
        results.append({"target": target, "summary": run_one(target)})

    aggregate = {
        "schema_version": "football3-r42b-mw2-live-availability-shadow-v1",
        "status": "COMPLETE",
        "classification": "PROSPECTIVE_PIT_MECHANISM_SHADOW_ONLY_NOT_FORMAL_PREDICTION",
        "formal_weight": 0,
        "governance": {
            "pit_snapshot_source_head": PIT_SOURCE_HEAD,
            "pit_snapshot_blob_sha": PIT_SNAPSHOT_BLOB_SHA,
            "pit_snapshot_records": len(rows),
            "result_labels_accessed": False,
            "postmatch_target_data_used": False,
            "current_match_confirmed_xi_used": False,
            "absence_weight_refit": False,
            "probability_retuning": False,
            "same_R42_mechanism_reused_without_parameter_search": True,
            "duplicate_team_name_resolution_uses_fixture_identity_only": True
        },
        "targets": results,
        "interpretation_rule": "These are shadow diagnostics. A PIT event only changes the expected-XI player layer when its resolved player is present in the strict-prior expected XI; doubtful is shown separately from confirmed out/suspended and is never silently upgraded."
    }
    (OUT / "summary_r42b_mw2.json").write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, indent=2, ensure_ascii=False))


def verify():
    d = json.loads((OUT / "summary_r42b_mw2.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["pit_snapshot_records"] == 7
    assert g["result_labels_accessed"] is False and g["postmatch_target_data_used"] is False
    assert g["absence_weight_refit"] is False and g["probability_retuning"] is False
    assert g["duplicate_team_name_resolution_uses_fixture_identity_only"] is True
    assert len(d["targets"]) == 2
    for item in d["targets"]:
        t = item["target"]
        s = item["summary"]
        assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
        assert s["governance"]["result_label_accessed"] is False
        assert s["governance"]["target_postmatch_data_used"] is False
        assert s["source"]["target_fixture"]["league_id"] == EXPECTED_PL_LEAGUE_ID
        assert s["source"]["target_fixture"]["official_current_kickoff_utc"].startswith(t["kickoff_at_utc"].replace("Z", ""))
        live_ids = {x["record_id"] for x in s["entity_resolution"]}
        assert set(t["target_record_ids"]).issubset(live_ids)
        probs = s["shadow_probabilities"]
        for name in ("K1_without_live_player_layer", "R40C_before_live_availability", "R42_confirmed_out_only", "R42_confirmed_out_plus_doubtful_as_out"):
            p = probs[name]
            assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-10
    print("R42B_MW2_LIVE_AVAILABILITY_SHADOW_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42b_mw2_shadow.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
