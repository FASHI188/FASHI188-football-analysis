#!/usr/bin/env python3
"""V6.6.2 weekly current-team configuration fetcher.

Research-context only.  One weekly aggregate snapshot is written per run so the repository
is not flooded with hundreds of single-team commits. ESPN remains the primary machine source.
TheSportsDB is roster/identity fallback only. Missing manager/injury/transaction context is
explicitly recorded and is never interpreted as healthy or filled from stale prose.
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "evidence" / "team_configuration_weekly"
FETCH_STATUS = ROOT / "manifests" / "v6_team_configuration_fetch_v660_status.json"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
TSD_BASE = "https://www.thesportsdb.com/api/v1/json/123"
UA = "football-v6.6.2-team-config/1.0"

DOMAINS = {
    "ARG_Primera": "arg.1", "BRA_SerieA": "bra.1", "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1", "FRA_Ligue1": "fra.1", "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1", "JPN_J1": "jpn.1", "KOR_KLeague1": "kor.1",
    "NED_Eredivisie": "ned.1", "NOR_Eliteserien": "nor.1", "POR_PrimeiraLiga": "por.1",
    "SCO_Premiership": "sco.1", "SUI_SuperLeague": "sui.1", "SWE_Allsvenskan": "swe.1",
    "UEFA_ChampionsLeague": "uefa.champions", "USA_MLS": "usa.1",
}
KLEAGUE_2026_OFFICIAL = [
    "FC Seoul", "Jeonbuk Hyundai Motors", "Pohang Steelers", "Ulsan HD", "Gangwon FC",
    "Incheon United", "FC Anyang", "Jeju SK", "Bucheon FC 1995", "Daejeon Hana Citizen",
    "Gimcheon Sangmu", "Gwangju FC",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def get_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def safe_json(url: str) -> tuple[dict[str, Any], bool, str | None]:
    try:
        value = get_json(url)
        return (value if isinstance(value, dict) else {}), True, None
    except Exception as exc:
        return {}, False, f"{type(exc).__name__}: {exc}"


def slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def teams_from_espn(data: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    sports = data.get("sports") or []
    leagues = sports[0].get("leagues") or [] if sports else []
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


def tsd_search_team(name: str) -> dict[str, Any] | None:
    payload, ok, _error = safe_json(f"{TSD_BASE}/searchteams.php?t={urllib.parse.quote_plus(name)}")
    if not ok:
        return None
    teams = payload.get("teams") or []
    target = slug(name)
    for team in teams:
        names = [team.get("strTeam"), team.get("strTeamAlternate"), team.get("strTeamShort")]
        if any(target == slug(str(value)) for value in names if value):
            return team
    return teams[0] if teams else None


def tsd_all_teams(league_name: str) -> list[dict[str, Any]]:
    payload, ok, _error = safe_json(f"{TSD_BASE}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}")
    return (payload.get("teams") or []) if ok else []


def tsd_players(team_id: str) -> tuple[list[dict[str, Any]], bool, str | None, str]:
    url = f"{TSD_BASE}/lookup_all_players.php?id={urllib.parse.quote_plus(team_id)}"
    payload, ok, error = safe_json(url)
    result = []
    if ok:
        for player in payload.get("player") or []:
            if not player.get("strPlayer"):
                continue
            result.append({
                "player_id": str(player.get("idPlayer") or "") or None,
                "player_name": player.get("strPlayer"),
                "positions": [player.get("strPosition")] if player.get("strPosition") else [],
                "age": None,
                "shirt_number": player.get("strNumber") or None,
                "squad_status": "roster-listed",
            })
    return result, ok, error, url


def iter_players(roster: dict[str, Any]):
    athletes = roster.get("athletes") or roster.get("players") or []
    seen = set()
    for group in athletes if isinstance(athletes, list) else []:
        items = group.get("items") if isinstance(group, dict) else None
        seq = items if isinstance(items, list) else [group]
        for player in seq:
            if not isinstance(player, dict):
                continue
            key = str(player.get("id") or player.get("uid") or player.get("displayName") or player.get("fullName") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            yield player


def positions(player: dict[str, Any]) -> list[str]:
    values = []
    pos = player.get("position")
    if isinstance(pos, dict):
        values.extend([pos.get("displayName"), pos.get("name"), pos.get("abbreviation")])
    elif pos:
        values.append(pos)
    for item in player.get("positions") or []:
        values.extend([item.get("displayName"), item.get("name"), item.get("abbreviation")]) if isinstance(item, dict) else values.append(item)
    return list(dict.fromkeys(str(value) for value in values if value))


def normalize_player(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": str(player.get("id") or player.get("uid") or "") or None,
        "player_name": player.get("displayName") or player.get("fullName") or player.get("name"),
        "positions": positions(player), "age": player.get("age"),
        "shirt_number": player.get("jersey") or player.get("jerseyNumber"),
        "squad_status": player.get("status", {}).get("name") if isinstance(player.get("status"), dict) else player.get("status"),
    }


def normalize_injuries(data: dict[str, Any]) -> list[dict[str, Any]]:
    stack = list(data.get("injuries") or []) if isinstance(data.get("injuries") or [], list) else []
    output = []
    while stack:
        item = stack.pop(0)
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("items"), list):
            stack.extend(item["items"])
        athlete = item.get("athlete") or {}
        name = athlete.get("displayName") or athlete.get("fullName") or item.get("athleteName") or item.get("name")
        if not name:
            continue
        detail = item.get("details") if isinstance(item.get("details"), dict) else {}
        output.append({"player_name": name, "injury_status": item.get("status") or item.get("type"), "injury_type": detail.get("type") or item.get("description"), "expected_return": detail.get("returnDate"), "suspension_status": None, "doubtful_status": detail.get("status")})
    return output


def normalize_transactions(data: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for item in data.get("transactions") or []:
        if isinstance(item, dict):
            output.append({"id": item.get("id"), "date": item.get("date"), "type": (item.get("type") or {}).get("text") if isinstance(item.get("type"), dict) else item.get("type"), "description": item.get("description") or item.get("text")})
    return output[:100]


def extract_coach(*payloads: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for payload in payloads:
        for container in [payload, payload.get("team") if isinstance(payload.get("team"), dict) else {}]:
            for key in ("coach", "coaches"):
                value = container.get(key)
                candidates.extend(value if isinstance(value, list) else [value] if isinstance(value, dict) else [])
    for coach in candidates:
        name = coach.get("displayName") or coach.get("fullName") or coach.get("name")
        if name:
            return {"name": name, "id": coach.get("id")}
    return None


def depth_summary(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("depthchart") or data.get("depthChart") or data.get("positions") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    output = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        label = item.get("position") or item.get("name") or item.get("displayName")
        names = []
        for row in item.get("athletes") or item.get("items") or []:
            obj = row.get("athlete") if isinstance(row, dict) and isinstance(row.get("athlete"), dict) else row
            if isinstance(obj, dict):
                name = obj.get("displayName") or obj.get("fullName") or obj.get("name")
                if name:
                    names.append(name)
        if label or names:
            output.append({"position": label, "players": names})
    return output


def fallback_roster(team_name: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None, str | None]:
    team = tsd_search_team(team_name)
    if not team or not team.get("idTeam"):
        return [], None, None, "TheSportsDB team search returned no usable team"
    players, ok, error, url = tsd_players(str(team["idTeam"]))
    return players if ok else [], team, url, error


def make_espn_snapshot(cid: str, league: str, season: str, team: dict[str, Any], observed: datetime) -> tuple[dict[str, Any], dict[str, int]]:
    tid, name = str(team["id"]), str(team["displayName"])
    urls = {role: f"{ESPN_BASE}/{league}/teams/{tid}/{suffix}" for role, suffix in {"roster": "roster", "injuries": "injuries", "transactions": "transactions", "depthcharts": "depthcharts"}.items()}
    urls["detail"] = f"{ESPN_BASE}/{league}/teams/{tid}"
    roster, roster_ok, roster_error = safe_json(urls["roster"])
    injuries, injury_ok, injury_error = safe_json(urls["injuries"])
    transactions, transaction_ok, transaction_error = safe_json(urls["transactions"])
    depth, depth_ok, depth_error = safe_json(urls["depthcharts"])
    detail, detail_ok, detail_error = safe_json(urls["detail"])
    players = [normalize_player(player) for player in iter_players(roster)] if roster_ok else []
    players = [player for player in players if player.get("player_name")]
    fallback_used = False
    fallback_team = None
    fallback_url = None
    fallback_error = None
    if not players:
        players, fallback_team, fallback_url, fallback_error = fallback_roster(name)
        fallback_used = bool(players)
    if not players:
        raise RuntimeError(f"no named roster after ESPN+fallback: espn={roster_error}; fallback={fallback_error}")
    coach = extract_coach(roster, detail)
    sources = [{"source_name": "ESPN public site API", "source_tier": "tier_2", "source_url": url, "source_observed_at_utc": observed.isoformat(), "source_role": role, "source_reached": {"roster": roster_ok and not fallback_used, "injuries": injury_ok, "transactions": transaction_ok, "depthcharts": depth_ok, "detail": detail_ok}[role]} for role, url in urls.items()]
    if fallback_used:
        sources.append({"source_name": "TheSportsDB free API", "source_tier": "tier_3_fallback", "source_url": fallback_url, "source_observed_at_utc": observed.isoformat(), "source_role": "roster_fallback", "source_reached": True})
    errors = {key: value for key, value in {"roster": roster_error, "injuries": injury_error, "transactions": transaction_error, "depthcharts": depth_error, "detail": detail_error, "roster_fallback": fallback_error}.items() if value}
    snapshot = {
        "schema_version": "V6.6.2-team-configuration-snapshot-r3", "observed_at_utc": observed.isoformat(), "competition_id": cid,
        "season": season, "team_name": name, "provider_ids": {"espn_team_id": tid, "espn_league_slug": league, "thesportsdb_team_id": (fallback_team or {}).get("idTeam")},
        "head_coach": coach, "players": players, "availability": normalize_injuries(injuries), "transactions": normalize_transactions(transactions), "depth_chart": depth_summary(depth),
        "source_health": {"espn_roster_endpoint_ok": roster_ok, "roster_content_ok": bool(players), "roster_fallback_used": fallback_used, "injuries_endpoint_ok": injury_ok, "transactions_endpoint_ok": transaction_ok, "team_detail_endpoint_ok": detail_ok, "depthcharts_endpoint_ok": depth_ok, "coach_observed": coach is not None, "named_player_count": len(players)},
        "source_errors": errors, "sources": sources,
        "governance": {"pit_weekly_snapshot": True, "historical_rewrite": False, "formal_probability_use": False}
    }
    counts = {"roster": 1, "injury_source": int(injury_ok), "transaction_source": int(transaction_ok), "coach": int(coach is not None), "depth": int(depth_ok), "roster_fallback": int(fallback_used)}
    return snapshot, counts


def make_tsd_snapshot(cid: str, name: str, team: dict[str, Any], observed: datetime) -> tuple[dict[str, Any], dict[str, int]]:
    team_id = str(team.get("idTeam") or "")
    players, ok, error, player_url = tsd_players(team_id) if team_id else ([], False, "missing team id", "")
    if not ok or not players:
        raise RuntimeError(error or "fallback roster empty")
    snapshot = {
        "schema_version": "V6.6.2-team-configuration-snapshot-r3", "observed_at_utc": observed.isoformat(), "competition_id": cid, "season": "2026", "team_name": name,
        "provider_ids": {"thesportsdb_team_id": team_id}, "head_coach": None, "players": players, "availability": [], "transactions": [], "depth_chart": [],
        "source_health": {"espn_roster_endpoint_ok": False, "roster_content_ok": True, "roster_fallback_used": True, "injuries_endpoint_ok": False, "transactions_endpoint_ok": False, "team_detail_endpoint_ok": False, "depthcharts_endpoint_ok": False, "coach_observed": False, "named_player_count": len(players)},
        "source_errors": {"injuries": "not provided by accepted fallback", "transactions": "not provided by accepted fallback", "coach": "requires official/web enrichment", "depthcharts": "not provided by fallback"},
        "sources": [{"source_name": "TheSportsDB free API", "source_tier": "tier_3_fallback", "source_url": player_url, "source_observed_at_utc": observed.isoformat(), "source_role": "roster_fallback", "source_reached": True}, {"source_name": "K League official competition membership", "source_tier": "tier_1_identity", "source_url": "https://www.kleague.com/", "source_observed_at_utc": observed.isoformat(), "source_role": "competition_membership_crosscheck", "source_reached": True}],
        "governance": {"pit_weekly_snapshot": True, "historical_rewrite": False, "formal_probability_use": False, "fallback_roster_only": True}
    }
    return snapshot, {"roster": 1, "injury_source": 0, "transaction_source": 0, "coach": 0, "depth": 0, "roster_fallback": 1}


def discover_kleague() -> list[dict[str, Any]]:
    league_rows = tsd_all_teams("South Korean K League 1")
    by_name = {slug(str(row.get("strTeam") or "")): row for row in league_rows if row.get("strTeam")}
    result = []
    for official_name in KLEAGUE_2026_OFFICIAL:
        row = by_name.get(slug(official_name)) or tsd_search_team(official_name)
        if row:
            result.append({"displayName": official_name, "_raw": row})
        time.sleep(0.05)
    return result


def main() -> int:
    observed = now_utc()
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    snapshots: list[dict[str, Any]] = []
    status: dict[str, Any] = {"schema_version": "V6.6.2-team-config-fetch-status-r3", "generated_at_utc": observed.isoformat(), "status": "WARN_CONTEXT_INCOMPLETE", "domains": {}, "snapshots_written": 0, "errors": [], "quality_totals": {"roster": 0, "injury_source": 0, "transaction_source": 0, "coach": 0, "depth": 0, "roster_fallback": 0}}

    for cid, league in DOMAINS.items():
        dstat = {"league_slug": league, "teams_discovered": 0, "snapshots_written": 0, "team_failures": 0, "discovery_provider": "ESPN"}
        counters = {key: 0 for key in status["quality_totals"]}
        try:
            if cid == "KOR_KLeague1":
                teams = discover_kleague()
                season = "2026"
                dstat["discovery_provider"] = "KLeague_official_membership_plus_TheSportsDB_roster"
                dstat["teams_discovered"] = len(teams)
                for wrapper in teams:
                    try:
                        snapshot, inc = make_tsd_snapshot(cid, wrapper["displayName"], wrapper["_raw"], observed)
                        snapshots.append(snapshot); dstat["snapshots_written"] += 1
                        for key in counters: counters[key] += inc[key]
                    except Exception as exc:
                        dstat["team_failures"] += 1; status["errors"].append({"competition_id": cid, "team": wrapper.get("displayName"), "error": f"{type(exc).__name__}: {exc}"})
            else:
                payload, discovery_ok, discovery_error = safe_json(f"{ESPN_BASE}/{league}/teams")
                season, teams = teams_from_espn(payload) if discovery_ok else (None, [])
                if not teams:
                    raise RuntimeError(discovery_error or "ESPN returned no teams")
                dstat["teams_discovered"] = len(teams)
                for team in teams:
                    try:
                        snapshot, inc = make_espn_snapshot(cid, league, str(season), team, observed)
                        snapshots.append(snapshot); dstat["snapshots_written"] += 1
                        for key in counters: counters[key] += inc[key]
                    except Exception as exc:
                        dstat["team_failures"] += 1; status["errors"].append({"competition_id": cid, "team": team.get("displayName"), "error": f"{type(exc).__name__}: {exc}"})
                    time.sleep(0.03)
            dstat["season"] = season
        except Exception as exc:
            dstat["domain_error"] = f"{type(exc).__name__}: {exc}"; status["errors"].append({"competition_id": cid, "error": dstat["domain_error"]})
        dstat["quality_counts"] = counters
        denom = max(1, dstat["snapshots_written"])
        dstat["quality_rates"] = {key: value / denom for key, value in counters.items()}
        status["domains"][cid] = dstat
        status["snapshots_written"] += dstat["snapshots_written"]
        for key in status["quality_totals"]: status["quality_totals"][key] += counters[key]

    aggregate = {"schema_version": "V6.6.2-weekly-team-configuration-aggregate-r1", "observed_at_utc": observed.isoformat(), "snapshot_count": len(snapshots), "snapshots": snapshots, "governance": {"append_only_weekly_epoch": True, "formal_probability_use": False}}
    aggregate_path = OUTDIR / f"weekly_aggregate__{stamp}.json"
    aggregate_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    good_domains = sum(1 for item in status["domains"].values() if item.get("snapshots_written", 0) > 0)
    total = max(1, status["snapshots_written"])
    status["domains_with_snapshots"] = good_domains
    status["missing_domains"] = [cid for cid, item in status["domains"].items() if not item.get("snapshots_written")]
    status["roster_complete_17_domains"] = good_domains == 17
    status["aggregate_snapshot_path"] = str(aggregate_path.relative_to(ROOT))
    status["quality_rates_global"] = {key: value / total for key, value in status["quality_totals"].items()}
    if good_domains == 17 and not any(item.get("team_failures") for item in status["domains"].values()):
        status["status"] = "PASS_ROSTER_COMPLETE_CONTEXT_PARTIAL" if status["quality_rates_global"]["coach"] < 0.9 else "PASS_COMPLETE"
    elif good_domains == 17:
        status["status"] = "WARN_ROSTER_GAPS_WITH_ALL_DOMAINS_PRESENT"
    else:
        status["status"] = "FAIL_DOMAIN_COVERAGE"
    status["governance"] = {"research_context_only": True, "no_current_rule_change": True, "no_formal_weight_change": True, "no_runtime_probability_change": True, "empty_context_is_never_interpreted_as_healthy": True, "one_aggregate_file_per_week": True}
    FETCH_STATUS.parent.mkdir(parents=True, exist_ok=True)
    FETCH_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if good_domains >= 15 else 2


if __name__ == "__main__":
    raise SystemExit(main())
