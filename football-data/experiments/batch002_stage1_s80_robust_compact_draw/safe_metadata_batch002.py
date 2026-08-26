#!/usr/bin/env python3
from __future__ import annotations

import difflib
import json
from pathlib import Path

import pandas as pd


def _score(r18, query: str, names: list[str]) -> float:
    q = r18.norm(query)
    best = 0.0
    for n in names:
        if q == n:
            best = 1.0
        elif q in n or n in q:
            best = max(best, 0.90 * min(len(q), len(n)) / max(len(q), len(n)) + 0.08)
        best = max(best, difflib.SequenceMatcher(None, q, n).ratio())
    return float(best)


def safe_target_metadata(s2, lock):
    """Resolve Batch-002 target identity from metadata only, with a league/date/name fallback.

    Reads only fixture id, kickoff, league id, home team id, away team id plus team/league names.
    No result, status, score, odds, market, xG or post-match columns are read here.
    """
    r9, r18, r23 = s2.r9, s2.r18, s2.r23
    data = s2.DATA
    data.mkdir(parents=True, exist_ok=True)
    tp = data / "teams.parquet"
    lp = data / "leagues.parquet"
    fp = data / "fixtures_safe.parquet"
    r9.download(f"{s2.HF}/teams.parquet?download=true", tp)
    r9.download(f"{s2.HF}/leagues.parquet?download=true", lp)
    r9.download(r9.FIX_URL, fp)

    teams = pd.read_parquet(tp)
    leagues = pd.read_parquet(lp)
    fixtures = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id"],
    )
    fixtures["date_utc"] = pd.to_datetime(fixtures["date_utc"], utc=True)
    idx = r18.team_index(teams)
    names_by_id = {str(tid): list(names) for tid, names in idx}
    cm = {d: r23.comp_id(leagues, d) for d in s2.DIVS}

    resolved = []
    audit = []
    for z in lock["rows"]:
        div = z["division"]
        cid = cm[div][0]
        hid_auto, hc = r18.resolve(z["home"], idx)
        aid_auto, ac = r18.resolve(z["away"], idx)
        hid = s2.TEAM_ID_OVERRIDES.get((div, z["home"]), hid_auto)
        aid = s2.TEAM_ID_OVERRIDES.get((div, z["away"]), aid_auto)
        d0 = pd.Timestamp(z["date"], tz="UTC")
        rec = {
            "batch_index": z["batch_index"],
            "date": z["date"],
            "division": div,
            "home": z["home"],
            "away": z["away"],
            "competition_id": cid,
            "home_team_id_auto": hid_auto,
            "away_team_id_auto": aid_auto,
            "home_team_id": hid,
            "away_team_id": aid,
            "home_id_override_used": (div, z["home"]) in s2.TEAM_ID_OVERRIDES,
            "away_id_override_used": (div, z["away"]) in s2.TEAM_ID_OVERRIDES,
            "home_candidates": hc,
            "away_candidates": ac,
            "resolution_method": "primary",
        }

        m = fixtures.iloc[0:0]
        if hid is not None and aid is not None:
            m = fixtures[
                (fixtures["league_id"].astype(str) == str(cid))
                & (fixtures["home_team_id"].astype(str) == str(hid))
                & (fixtures["away_team_id"].astype(str) == str(aid))
                & (fixtures["date_utc"] >= d0 - pd.Timedelta(days=1))
                & (fixtures["date_utc"] < d0 + pd.Timedelta(days=2))
            ]
        rec["fixture_candidates_primary"] = [
            {"id": str(int(x.id)), "date_utc": x.date_utc.isoformat()}
            for x in m.itertuples(index=False)
        ]

        chosen = next(m.itertuples(index=False)) if len(m) == 1 else None
        if chosen is None:
            # Metadata-only fallback: same league, nearby scheduled date, then require strong
            # simultaneous home+away name agreement. The candidate result/status is never read.
            near = fixtures[
                (fixtures["league_id"].astype(str) == str(cid))
                & (fixtures["date_utc"] >= d0 - pd.Timedelta(days=10))
                & (fixtures["date_utc"] < d0 + pd.Timedelta(days=11))
            ]
            scored = []
            for x in near.itertuples(index=False):
                hh = str(int(x.home_team_id))
                aa = str(int(x.away_team_id))
                hs = _score(r18, z["home"], names_by_id.get(hh, []))
                ass = _score(r18, z["away"], names_by_id.get(aa, []))
                combined = (hs + ass) / 2.0
                gap_days = abs((x.date_utc - d0).total_seconds()) / 86400.0
                scored.append((combined, min(hs, ass), -gap_days, hh, aa, x))
            scored.sort(key=lambda q: (q[0], q[1], q[2]), reverse=True)
            rec["fixture_candidates_fallback"] = [
                {
                    "id": str(int(q[5].id)),
                    "date_utc": q[5].date_utc.isoformat(),
                    "home_team_id": q[3],
                    "away_team_id": q[4],
                    "combined_name_score": q[0],
                    "min_side_name_score": q[1],
                    "date_gap_days": -q[2],
                }
                for q in scored[:5]
            ]
            if scored:
                top = scored[0]
                second = scored[1] if len(scored) > 1 else None
                margin = top[0] - second[0] if second is not None else 1.0
                # Exact/near-exact simultaneous identity is accepted directly; otherwise require
                # a clear margin over the next metadata-only fixture candidate.
                safe = top[1] >= 0.74 and top[0] >= 0.82 and (top[0] >= 0.985 or margin >= 0.05)
                if safe:
                    hid, aid, chosen = top[3], top[4], top[5]
                    rec["home_team_id"] = hid
                    rec["away_team_id"] = aid
                    rec["resolution_method"] = "league_date_name_fallback"
                    rec["fallback_margin"] = margin

        audit.append(rec)
        if chosen is None:
            continue
        resolved.append({
            **z,
            "fixture_id": str(int(chosen.id)),
            "competition_id": str(cid),
            "home_team": str(hid),
            "away_team": str(aid),
            "kickoff_utc": chosen.date_utc.isoformat(),
            "nominal_cutoff_utc": (chosen.date_utc - pd.Timedelta(hours=24)).isoformat(),
        })

    for p in (tp, lp, fp):
        p.unlink(missing_ok=True)

    if len(resolved) != 100:
        (data / "mapping_audit_stage2.json").write_text(
            json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        raise RuntimeError(f"target mapping incomplete {len(resolved)}/100")
    return resolved, audit, cm
