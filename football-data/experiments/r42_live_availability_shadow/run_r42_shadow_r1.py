#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_r42_shadow as r42  # noqa: E402


def team_match_score(target: str, candidate: str) -> float:
    t = r42.compact(target)
    c = r42.compact(candidate)
    if not t or not c:
        return 0.0
    if t == c:
        return 2.0
    aliases = {
        "manchestercity": {"mancity", "manchestercityfc", "manchestercity"},
        "crystalpalace": {"crystalpalacefc", "crystalpalace"},
    }
    if c in aliases.get(t, set()):
        return 1.9
    score = SequenceMatcher(None, t, c).ratio()
    if t in c or c in t:
        score += 0.35
    tt = set(r42.norm_text(target).split())
    ct = set(r42.norm_text(candidate).split())
    if tt and ct:
        score += 0.30 * (len(tt & ct) / len(tt | ct))
    return float(score)


def resolve_team_id(teams: pd.DataFrame, target: str):
    scored = []
    for x in teams.itertuples(index=False):
        score = team_match_score(target, str(x.name))
        scored.append((score, int(x.id), str(x.name)))
    scored.sort(reverse=True)
    if not scored:
        raise RuntimeError(f"no team candidates for {target}")
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None
    margin = top[0] - second[0] if second else 999.0
    if top[0] < 0.72 or margin < 0.06:
        raise RuntimeError(
            f"target team identity not uniquely resolvable for {target}: "
            f"top={scored[:5]}"
        )
    return top[1], {
        "target_name": target,
        "resolved_team_id": top[1],
        "resolved_dataset_name": top[2],
        "score": float(top[0]),
        "margin_to_second": float(margin),
        "top_candidates": [
            {"team_id": int(x[1]), "name": x[2], "score": float(x[0])}
            for x in scored[:5]
        ],
    }


def load_target_fixture_fixed(tmp: Path):
    fp = tmp / "fixtures.parquet"
    tp = tmp / "teams.parquet"
    r42.download(r42.FIXTURES_URL, fp)
    r42.download(r42.TEAMS_URL, tp)
    fixture_sha = r42.fsha(fp)
    if fixture_sha != r42.EXPECTED_FIXTURES_SHA256:
        raise RuntimeError(f"fixtures source drift: {fixture_sha}")

    teams = pd.read_parquet(tp, columns=["id", "name"])
    h, hres = resolve_team_id(teams, r42.TARGET["home_team"])
    a, ares = resolve_team_id(teams, r42.TARGET["away_team"])

    fx = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "status_norm", "is_played"],
    )
    fx["dt"] = pd.to_datetime(fx["date_utc"], utc=True)
    official_dt = pd.Timestamp(r42.TARGET["kickoff_at_utc"])
    pair = fx[(fx["home_team_id"] == h) & (fx["away_team_id"] == a)].copy()
    if pair.empty:
        raise RuntimeError(f"target fixture pair not found: home_id={h} away_id={a}")
    pair["distance_seconds"] = (pair["dt"] - official_dt).abs().dt.total_seconds()
    pair = pair.sort_values(["distance_seconds", "dt", "id"])
    x = pair.iloc[0]
    if float(x["distance_seconds"]) > 14 * 86400:
        raise RuntimeError(
            "no nearby target fixture within 14 days; candidates="
            + json.dumps(pair[["id", "date_utc", "league_id", "distance_seconds"]].head(5).to_dict("records"), default=str)
        )
    if len(pair) > 1 and float(pair.iloc[1]["distance_seconds"]) == float(x["distance_seconds"]):
        raise RuntimeError(
            "target fixture nearest-date tie; candidates="
            + json.dumps(pair[["id", "date_utc", "league_id", "distance_seconds"]].head(5).to_dict("records"), default=str)
        )

    source_dt = pd.Timestamp(x["dt"])
    schedule_delta_seconds = int((source_dt - official_dt).total_seconds())
    source_schedule_stale = bool(schedule_delta_seconds != 0)

    row = {
        # Current official match date controls strict-prior recency at prediction time.
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
        "home_team_identity": hres,
        "away_team_identity": ares,
        "official_current_kickoff_utc": official_dt.isoformat(),
        "source_snapshot_kickoff_utc": source_dt.isoformat(),
        "source_schedule_stale": source_schedule_stale,
        "source_schedule_delta_seconds": schedule_delta_seconds,
        "matching_rule": "unique team pair + nearest source-snapshot fixture within 14 days; current official kickoff remains the PIT cutoff and prediction date",
        "status_norm_in_source": str(x["status_norm"]),
        "is_played_in_source": bool(x["is_played"]),
        "fixtures_sha256": fixture_sha,
        "teams_sha256": r42.fsha(tp),
    }
    return row, meta, fp, tp


def run():
    r42.load_target_fixture = load_target_fixture_fixed
    r42.run()


def verify():
    r42.load_target_fixture = load_target_fixture_fixed
    r42.verify()
    summary = json.loads((r42.OUT / "summary_r42_shadow.json").read_text(encoding="utf-8"))
    meta = summary["source"]["target_fixture"]
    assert meta["matching_rule"].startswith("unique team pair")
    assert meta["official_current_kickoff_utc"].startswith("2026-08-28T19:00:00")
    print("R42_SHADOW_R1_IDENTITY_SCHEDULE_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42_shadow_r1.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
