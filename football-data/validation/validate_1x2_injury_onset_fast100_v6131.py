#!/usr/bin/env python3
"""V6.13.1 research-only Fast100: recent injury-onset shock in expected XI.

Only injury `from_date` is permitted. `end_date`, `days_missed`, and `games_missed` are
loaded by the source CSV but are never read into prediction features, filters, labels, or
sample selection. Expected XI is built only from same-season club lineups strictly before
the target match. Target actual XI is never used as an input.

The source does not preserve the original public announcement timestamp of each injury,
so this is RETROSPECTIVE_RESEARCH_ONLY and can never directly change formal CURRENT.
"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

import validate_1x2_pit_lineup_increment_v6117c as fixed

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_1x2_injury_onset_fast100_v6131_status.json"
INJURY_URL = "https://raw.githubusercontent.com/salimt/football-datasets/main/datalake/transfermarkt/player_injuries/player_injuries.csv"
TARGET_SEASONS = {"2024/25", "2025/26"}


def _pid(value) -> str:
    token = str(value or "").strip()
    if ":" in token:
        token = token.rsplit(":", 1)[-1]
    return token


def _season_norm(value: str) -> str:
    token = str(value or "").strip()
    if len(token) == 5 and token[2] == "/":
        first = int(token[:2])
        # Dataset rows in our scope are 24/25 and 25/26.
        return f"20{first:02d}/{token[3:]}"
    return token


def load_injury_onsets():
    req = urllib.request.Request(INJURY_URL, headers={"User-Agent": "football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=180) as response:
        raw = response.read()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames or [])
    required = {"player_id", "season_name", "injury_reason", "from_date"}
    missing = sorted(required - set(columns))
    if missing:
        raise RuntimeError(f"injury source missing required columns: {missing}")

    by_player = defaultdict(list)
    rows = scope_rows = 0
    season_counts = defaultdict(int)
    reason_counts = defaultdict(int)
    for row in reader:
        rows += 1
        season = _season_norm(row.get("season_name"))
        if season not in TARGET_SEASONS:
            continue
        player = _pid(row.get("player_id"))
        from_token = str(row.get("from_date") or "").strip()[:10]
        if not player or not from_token:
            continue
        try:
            onset = date.fromisoformat(from_token)
        except ValueError:
            continue
        reason = str(row.get("injury_reason") or "").strip()
        # Deliberately store ONLY fields allowed by this experiment.
        by_player[player].append((onset, reason, season))
        scope_rows += 1
        season_counts[season] += 1
        reason_counts[reason or "<blank>"] += 1
    for player in by_player:
        by_player[player].sort(key=lambda item: item[0])
    return by_player, {
        "url": INJURY_URL,
        "bytes": len(raw),
        "rows": rows,
        "scope_rows": scope_rows,
        "players_with_scope_onsets": len(by_player),
        "season_counts": dict(sorted(season_counts.items())),
        "top_reasons": sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20],
        "columns": columns,
        "forbidden_columns_not_used": ["end_date", "days_missed", "games_missed"],
    }


def recent_onsets(history, target: date, max_gap: int):
    count = 0
    reasons = []
    for onset, reason, _season in reversed(history):
        gap = (target - onset).days
        if gap <= 0:
            continue
        if gap > max_gap:
            break
        count += 1
        reasons.append(reason)
    return count, reasons


def build_rows(injuries):
    matches = fixed.base._load_matches()
    lineups = {cid: fixed.base._load_lineups(cid) for cid in fixed.base.COMPETITIONS}
    team_hist = defaultdict(list)
    out = []
    for r in matches:
        cid = r["competition_id"]
        season = r["season"]
        ds = str(r["date"])[:10]
        try:
            target = date.fromisoformat(ds)
        except ValueError:
            continue
        hk = (cid, season, r["home"])
        ak = (cid, season, r["away"])
        hp = fixed.base._predicted_xi(team_hist[hk])
        ap = fixed.base._predicted_xi(team_hist[ak])
        if hp is not None and ap is not None and season in TARGET_SEASONS:
            vals = {}
            for side, xi in (("home", hp[0]), ("away", ap[0])):
                for window in (7, 14):
                    players = 0
                    onsets = 0
                    for raw_player in xi:
                        c, _reasons = recent_onsets(injuries.get(_pid(raw_player), []), target, window)
                        if c:
                            players += 1
                            onsets += c
                    vals[f"{side}_injured_players_{window}"] = players
                    vals[f"{side}_injury_onsets_{window}"] = onsets
            p = r["opening"]
            fav = ("home", "draw", "away")[int(np.argmax(np.asarray(p, dtype=float)))]
            for window in (7, 14):
                fp = vals[f"home_injured_players_{window}"] if fav == "home" else vals[f"away_injured_players_{window}"] if fav == "away" else 0
                dp = vals[f"away_injured_players_{window}"] if fav == "home" else vals[f"home_injured_players_{window}"] if fav == "away" else 0
                vals[f"fav_injured_players_{window}"] = fp
                vals[f"dog_injured_players_{window}"] = dp
                vals[f"injury_player_diff_{window}"] = fp - dp
            out.append({**r, "date": ds, "fav": fav, **vals})

        # Strictly after target feature creation: observed target lineup enters future history.
        hi = lineups[cid].get((season, ds, r["home"]))
        ai = lineups[cid].get((season, ds, r["away"]))
        if hi:
            team_hist[hk].append((ds, tuple(hi["starters"])))
        if ai:
            team_hist[ak].append((ds, tuple(ai["starters"])))
    return out


def correct(r):
    return r["fav"] == r["actual"]


def stat(rows, gate=lambda r: True):
    selected = [r for r in rows if gate(r)]
    hits = sum(correct(r) for r in selected)
    return {
        "count": len(selected),
        "hits": hits,
        "accuracy": hits / len(selected) if selected else None,
    }


def metrics(rows):
    return {
        "all_affected14": stat(rows),
        "any_7d": stat(rows, lambda r: r["home_injured_players_7"] or r["away_injured_players_7"]),
        "favorite_any_7d": stat(rows, lambda r: r["fav_injured_players_7"] >= 1),
        "favorite_any_14d": stat(rows, lambda r: r["fav_injured_players_14"] >= 1),
        "favorite_2plus_14d": stat(rows, lambda r: r["fav_injured_players_14"] >= 2),
        "favorite_more_injuries_14d": stat(rows, lambda r: r["injury_player_diff_14"] >= 1),
        "p_ge_0.58": stat(rows, lambda r: max(r["opening"]) >= 0.58),
        "p_ge_0.58_exclude_fav_any_7d": stat(rows, lambda r: max(r["opening"]) >= 0.58 and r["fav_injured_players_7"] == 0),
        "p_ge_0.58_exclude_fav_any_14d": stat(rows, lambda r: max(r["opening"]) >= 0.58 and r["fav_injured_players_14"] == 0),
        "p_ge_0.58_exclude_fav_2plus_14d": stat(rows, lambda r: max(r["opening"]) >= 0.58 and r["fav_injured_players_14"] < 2),
        "p_ge_0.60": stat(rows, lambda r: max(r["opening"]) >= 0.60),
        "p_ge_0.60_exclude_fav_any_14d": stat(rows, lambda r: max(r["opening"]) >= 0.60 and r["fav_injured_players_14"] == 0),
    }


def main():
    injuries, source = load_injury_onsets()
    rows = build_rows(injuries)
    scope = [r for r in rows if r["season"] in TARGET_SEASONS]
    affected = [r for r in scope if r["home_injured_players_14"] or r["away_injured_players_14"]]
    if len(affected) < 100:
        raise RuntimeError(f"insufficient two-season injury-onset affected matches: {len(affected)}")
    test = affected[-100:]
    by_season = {season: metrics([r for r in test if r["season"] == season]) for season in sorted(TARGET_SEASONS)}
    payload = {
        "schema_version": "V6.13.1-injury-onset-fast100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_ONLY_INJURY_ONSET_DATE_NO_ORIGINAL_PUBLICATION_TIMESTAMP",
        "governance": {
            "only_injury_from_date_used": True,
            "injury_end_date_forbidden": True,
            "days_missed_forbidden": True,
            "games_missed_forbidden": True,
            "expected_xi_same_season_prior_only": True,
            "target_actual_xi_excluded": True,
            "exposure_selection_outcome_independent": True,
            "test_matches": 100,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
        "source": source,
        "sample": {
            "two_season_feature_rows": len(scope),
            "two_season_affected14": len(affected),
            "test_first": test[0]["date"],
            "test_last": test[-1]["date"],
            "test_by_season": {season: sum(r["season"] == season for r in test) for season in sorted(TARGET_SEASONS)},
        },
        "test": metrics(test),
        "by_season": by_season,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sample": payload["sample"], "test": payload["test"], "by_season": by_season}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
