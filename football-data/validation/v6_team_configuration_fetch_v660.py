#!/usr/bin/env python3
"""V6.6.0 weekly current-team configuration fetcher.

Primary machine-readable source is ESPN's public site API. It discovers the current league
season and teams, then captures roster, injuries and transactions for each team. Unsupported
or incomplete domains are recorded for later web/official-source augmentation; the fetcher
never invents missing players or availability.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "evidence" / "team_configuration_weekly"
FETCH_STATUS = ROOT / "manifests" / "v6_team_configuration_fetch_v660_status.json"

DOMAINS = {
    "ARG_Primera": "arg.1",
    "BRA_SerieA": "bra.1",
    "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1",
    "FRA_Ligue1": "fra.1",
    "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1",
    "JPN_J1": "jpn.1",
    "KOR_KLeague1": "kor.1",
    "NED_Eredivisie": "ned.1",
    "NOR_Eliteserien": "nor.1",
    "POR_PrimeiraLiga": "por.1",
    "SCO_Premiership": "sco.1",
    "SUI_SuperLeague": "sui.1",
    "SWE_Allsvenskan": "swe.1",
    "UEFA_ChampionsLeague": "uefa.champions",
    "USA_MLS": "usa.1",
}
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
UA = "football-v6.6-team-config/1.0"


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def slugify(value: str):
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:80]


def teams_from_payload(data):
    sports = data.get("sports") or []
    if not sports:
        return None, []
    leagues = sports[0].get("leagues") or []
    if not leagues:
        return None, []
    league = leagues[0]
    season = league.get("season") or {}
    season_name = season.get("displayName") or season.get("year")
    teams = []
    for wrapper in league.get("teams") or []:
        team = wrapper.get("team") or wrapper
        if team.get("id") and team.get("displayName"):
            teams.append(team)
    return str(season_name or "unknown"), teams


def iter_player_objects(roster):
    athletes = roster.get("athletes") or roster.get("players") or []
    seen = set()
    for group in athletes if isinstance(athletes, list) else []:
        items = group.get("items") if isinstance(group, dict) else None
        seq = items if isinstance(items, list) else [group]
        for p in seq:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or p.get("uid") or p.get("displayName") or p.get("fullName") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            yield p


def position_values(p):
    vals = []
    pos = p.get("position")
    if isinstance(pos, dict):
        vals.extend([pos.get("displayName"), pos.get("name"), pos.get("abbreviation")])
    elif pos:
        vals.append(pos)
    for x in p.get("positions") or []:
        if isinstance(x, dict):
            vals.extend([x.get("displayName"), x.get("name"), x.get("abbreviation")])
        else:
            vals.append(x)
    return list(dict.fromkeys(str(x) for x in vals if x))


def normalize_player(p):
    return {
        "player_id": str(p.get("id") or p.get("uid") or "") or None,
        "player_name": p.get("displayName") or p.get("fullName") or p.get("name"),
        "positions": position_values(p),
        "age": p.get("age"),
        "shirt_number": p.get("jersey") or p.get("jerseyNumber"),
        "squad_status": p.get("status", {}).get("name") if isinstance(p.get("status"), dict) else p.get("status"),
    }


def normalize_injuries(data):
    raw = data.get("injuries") or []
    out = []
    stack = list(raw) if isinstance(raw, list) else []
    while stack:
        x = stack.pop(0)
        if not isinstance(x, dict):
            continue
        if isinstance(x.get("items"), list):
            stack.extend(x["items"])
        athlete = x.get("athlete") or {}
        name = athlete.get("displayName") or athlete.get("fullName") or x.get("athleteName") or x.get("name")
        if not name:
            continue
        out.append({
            "player_name": name,
            "injury_status": x.get("status") or x.get("type"),
            "injury_type": x.get("details", {}).get("type") if isinstance(x.get("details"), dict) else x.get("description"),
            "expected_return": x.get("details", {}).get("returnDate") if isinstance(x.get("details"), dict) else None,
            "suspension_status": None,
            "doubtful_status": x.get("details", {}).get("status") if isinstance(x.get("details"), dict) else None,
        })
    return out


def normalize_transactions(data):
    raw = data.get("transactions") or []
    out = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        out.append({
            "id": x.get("id"),
            "date": x.get("date"),
            "type": (x.get("type") or {}).get("text") if isinstance(x.get("type"), dict) else x.get("type"),
            "description": x.get("description") or x.get("text"),
        })
    return out[:100]


def extract_coach(roster, team_detail):
    candidates = []
    for key in ("coach", "coaches"):
        v = roster.get(key)
        if isinstance(v, list): candidates.extend(v)
        elif isinstance(v, dict): candidates.append(v)
    for key in ("coach", "coaches"):
        v = team_detail.get(key) if isinstance(team_detail, dict) else None
        if isinstance(v, list): candidates.extend(v)
        elif isinstance(v, dict): candidates.append(v)
    for c in candidates:
        name = c.get("displayName") or c.get("fullName") or c.get("name")
        if name:
            return {"name": name, "id": c.get("id")}
    return None


def main():
    observed = now_utc()
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    status = {"schema_version": "V6.6.0-team-config-fetch-status-r1", "generated_at_utc": observed.isoformat(), "status": "PASS", "domains": {}, "snapshots_written": 0, "errors": []}

    for cid, league in DOMAINS.items():
        dstat = {"league_slug": league, "teams_discovered": 0, "snapshots_written": 0, "team_failures": 0}
        try:
            season, teams = teams_from_payload(get_json(f"{BASE}/{league}/teams"))
            dstat["season"] = season
            dstat["teams_discovered"] = len(teams)
            if not teams:
                raise RuntimeError("ESPN returned no teams")
            for team in teams:
                tid = str(team["id"])
                tname = str(team["displayName"])
                try:
                    roster = get_json(f"{BASE}/{league}/teams/{tid}/roster")
                    try: injuries = get_json(f"{BASE}/{league}/teams/{tid}/injuries")
                    except Exception: injuries = {}
                    try: transactions = get_json(f"{BASE}/{league}/teams/{tid}/transactions")
                    except Exception: transactions = {}
                    try: detail = get_json(f"{BASE}/{league}/teams/{tid}")
                    except Exception: detail = {}
                    players = [normalize_player(p) for p in iter_player_objects(roster)]
                    players = [p for p in players if p.get("player_name")]
                    snap = {
                        "schema_version": "V6.6.0-team-configuration-snapshot-r1",
                        "observed_at_utc": observed.isoformat(),
                        "competition_id": cid,
                        "season": season,
                        "team_name": tname,
                        "provider_ids": {"espn_team_id": tid, "espn_league_slug": league},
                        "head_coach": extract_coach(roster, detail),
                        "players": players,
                        "availability": normalize_injuries(injuries),
                        "transactions": normalize_transactions(transactions),
                        "sources": [{
                            "source_name": "ESPN public site API",
                            "source_tier": "tier_2",
                            "source_url": f"{BASE}/{league}/teams/{tid}/roster",
                            "source_observed_at_utc": observed.isoformat(),
                            "source_role": "team roster primary machine-readable weekly baseline"
                        }],
                        "governance": {"pit_weekly_snapshot": True, "historical_rewrite": False, "formal_probability_use": False}
                    }
                    filename = f"{cid}__{slugify(tname)}__{stamp}.json"
                    (OUTDIR / filename).write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
                    dstat["snapshots_written"] += 1
                    status["snapshots_written"] += 1
                    time.sleep(0.05)
                except Exception as exc:
                    dstat["team_failures"] += 1
                    status["errors"].append({"competition_id": cid, "team": tname, "error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            dstat["domain_error"] = f"{type(exc).__name__}: {exc}"
            status["errors"].append({"competition_id": cid, "error": dstat["domain_error"]})
        status["domains"][cid] = dstat

    good_domains = sum(1 for x in status["domains"].values() if x.get("snapshots_written", 0) > 0)
    status["domains_with_snapshots"] = good_domains
    status["status"] = "PASS" if good_domains >= 8 and status["snapshots_written"] >= 100 else "WARN"
    status["governance"] = {"research_context_only": True, "no_current_rule_change": True, "no_formal_weight_change": True, "no_runtime_probability_change": True}
    FETCH_STATUS.parent.mkdir(parents=True, exist_ok=True)
    FETCH_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
