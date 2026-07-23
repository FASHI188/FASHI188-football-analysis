#!/usr/bin/env python3
"""V6.6.1 weekly current-team configuration fetcher.

Research-context collector only.  It creates immutable point-in-time weekly snapshots and
never changes formal probabilities.  ESPN is the primary machine-readable source.  If a
league discovery endpoint is unavailable (currently K League 1), TheSportsDB is used only
as a roster/team-identity fallback.  Missing coach/injury/transaction/depth information is
reported explicitly instead of being silently treated as complete.
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

# Only used when ESPN cannot enumerate a competition.  The 2026 K League 1 membership is
# externally auditable; the fallback provider supplies IDs/rosters, not injury truth.
TSD_LEAGUE_FALLBACK = {"KOR_KLeague1": "South Korean K League 1"}
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
TSD_BASE = "https://www.thesportsdb.com/api/v1/json/123"
UA = "football-v6.6.1-team-config/1.0"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def get_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def safe_json(url: str) -> tuple[dict[str, Any], bool, str | None]:
    try:
        payload = get_json(url)
        return (payload if isinstance(payload, dict) else {}), True, None
    except Exception as exc:  # absence is evidence and is recorded, not silently promoted
        return {}, False, f"{type(exc).__name__}: {exc}"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:80]


def teams_from_espn(data: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    sports = data.get("sports") or []
    if not sports:
        return None, []
    leagues = sports[0].get("leagues") or []
    if not leagues:
        return None, []
    league = leagues[0]
    season = league.get("season") or {}
    season_name = season.get("displayName") or season.get("year")
    teams: list[dict[str, Any]] = []
    for wrapper in league.get("teams") or []:
        team = wrapper.get("team") or wrapper
        if team.get("id") and team.get("displayName"):
            teams.append(team)
    return str(season_name or "unknown"), teams


def discover_tsd_teams(league_name: str) -> list[dict[str, Any]]:
    url = f"{TSD_BASE}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    payload = get_json(url)
    result = []
    for team in (payload or {}).get("teams") or []:
        if team.get("idTeam") and team.get("strTeam"):
            result.append({
                "id": str(team["idTeam"]),
                "displayName": str(team["strTeam"]),
                "_provider": "thesportsdb",
                "_raw": team,
            })
    return result


def iter_espn_players(roster: dict[str, Any]):
    athletes = roster.get("athletes") or roster.get("players") or []
    seen = set()
    for group in athletes if isinstance(athletes, list) else []:
        items = group.get("items") if isinstance(group, dict) else None
        seq = items if isinstance(items, list) else [group]
        for player in seq:
            if not isinstance(player, dict):
                continue
            pid = str(player.get("id") or player.get("uid") or player.get("displayName") or player.get("fullName") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            yield player


def position_values(player: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    pos = player.get("position")
    if isinstance(pos, dict):
        values.extend([pos.get("displayName"), pos.get("name"), pos.get("abbreviation")])
    elif pos:
        values.append(pos)
    for item in player.get("positions") or []:
        if isinstance(item, dict):
            values.extend([item.get("displayName"), item.get("name"), item.get("abbreviation")])
        else:
            values.append(item)
    return list(dict.fromkeys(str(value) for value in values if value))


def normalize_espn_player(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": str(player.get("id") or player.get("uid") or "") or None,
        "player_name": player.get("displayName") or player.get("fullName") or player.get("name"),
        "positions": position_values(player),
        "age": player.get("age"),
        "shirt_number": player.get("jersey") or player.get("jerseyNumber"),
        "squad_status": player.get("status", {}).get("name") if isinstance(player.get("status"), dict) else player.get("status"),
    }


def tsd_players(team_id: str) -> tuple[list[dict[str, Any]], bool, str | None, str]:
    url = f"{TSD_BASE}/lookup_all_players.php?id={urllib.parse.quote_plus(team_id)}"
    payload, ok, error = safe_json(url)
    players = []
    if ok:
        for player in payload.get("player") or []:
            name = player.get("strPlayer")
            if not name:
                continue
            players.append({
                "player_id": str(player.get("idPlayer") or "") or None,
                "player_name": name,
                "positions": [player.get("strPosition")] if player.get("strPosition") else [],
                "age": None,
                "shirt_number": player.get("strNumber") or None,
                "squad_status": "roster-listed",
            })
    return players, ok, error, url


def normalize_injuries(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("injuries") or []
    output = []
    stack = list(raw) if isinstance(raw, list) else []
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
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        output.append({
            "player_name": name,
            "injury_status": item.get("status") or item.get("type"),
            "injury_type": details.get("type") or item.get("description"),
            "expected_return": details.get("returnDate"),
            "suspension_status": None,
            "doubtful_status": details.get("status"),
        })
    return output


def normalize_transactions(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("transactions") or []
    output = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        output.append({
            "id": item.get("id"),
            "date": item.get("date"),
            "type": (item.get("type") or {}).get("text") if isinstance(item.get("type"), dict) else item.get("type"),
            "description": item.get("description") or item.get("text"),
        })
    return output[:100]


def extract_coach(*payloads: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("coach", "coaches"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, dict):
                candidates.append(value)
        # ESPN sometimes nests team metadata.
        team = payload.get("team")
        if isinstance(team, dict):
            for key in ("coach", "coaches"):
                value = team.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
                elif isinstance(value, dict):
                    candidates.append(value)
    for coach in candidates:
        name = coach.get("displayName") or coach.get("fullName") or coach.get("name")
        if name:
            return {"name": name, "id": coach.get("id")}
    return None


def depthchart_summary(data: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    raw = data.get("depthchart") or data.get("depthChart") or data.get("positions") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        label = item.get("position") or item.get("name") or item.get("displayName")
        athletes = item.get("athletes") or item.get("items") or []
        names = []
        for athlete in athletes if isinstance(athletes, list) else []:
            if isinstance(athlete, dict):
                obj = athlete.get("athlete") if isinstance(athlete.get("athlete"), dict) else athlete
                name = obj.get("displayName") or obj.get("fullName") or obj.get("name")
                if name:
                    names.append(name)
        if label or names:
            output.append({"position": label, "players": names})
    return output


def write_snapshot(snapshot: dict[str, Any], cid: str, team_name: str, stamp: str) -> None:
    filename = f"{cid}__{slugify(team_name)}__{stamp}.json"
    (OUTDIR / filename).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def espn_team_snapshot(cid: str, league: str, season: str, team: dict[str, Any], observed: datetime, stamp: str) -> tuple[dict[str, Any], dict[str, int]]:
    tid, tname = str(team["id"]), str(team["displayName"])
    urls = {
        "roster": f"{ESPN_BASE}/{league}/teams/{tid}/roster",
        "injuries": f"{ESPN_BASE}/{league}/teams/{tid}/injuries",
        "transactions": f"{ESPN_BASE}/{league}/teams/{tid}/transactions",
        "detail": f"{ESPN_BASE}/{league}/teams/{tid}",
        "depthcharts": f"{ESPN_BASE}/{league}/teams/{tid}/depthcharts",
    }
    roster, roster_ok, roster_error = safe_json(urls["roster"])
    if not roster_ok:
        raise RuntimeError(f"roster unavailable: {roster_error}")
    injuries, injuries_ok, injuries_error = safe_json(urls["injuries"])
    transactions, transactions_ok, transactions_error = safe_json(urls["transactions"])
    detail, detail_ok, detail_error = safe_json(urls["detail"])
    depth, depth_ok, depth_error = safe_json(urls["depthcharts"])
    players = [normalize_espn_player(player) for player in iter_espn_players(roster)]
    players = [player for player in players if player.get("player_name")]
    if not players:
        raise RuntimeError("roster endpoint returned zero named players")
    coach = extract_coach(roster, detail)
    health = {
        "roster_endpoint_ok": roster_ok,
        "injuries_endpoint_ok": injuries_ok,
        "transactions_endpoint_ok": transactions_ok,
        "team_detail_endpoint_ok": detail_ok,
        "depthcharts_endpoint_ok": depth_ok,
        "coach_observed": coach is not None,
        "named_player_count": len(players),
        "availability_record_count": len(normalize_injuries(injuries)),
        "transaction_record_count": len(normalize_transactions(transactions)),
    }
    errors = {key: value for key, value in {
        "roster": roster_error, "injuries": injuries_error, "transactions": transactions_error,
        "detail": detail_error, "depthcharts": depth_error,
    }.items() if value}
    sources = []
    for role, url in urls.items():
        ok = bool(health.get(f"{role}_endpoint_ok", role == "detail" and detail_ok))
        sources.append({
            "source_name": "ESPN public site API",
            "source_tier": "tier_2",
            "source_url": url,
            "source_observed_at_utc": observed.isoformat(),
            "source_role": role,
            "source_reached": ok,
        })
    snapshot = {
        "schema_version": "V6.6.1-team-configuration-snapshot-r2",
        "observed_at_utc": observed.isoformat(),
        "competition_id": cid,
        "season": season,
        "team_name": tname,
        "provider_ids": {"espn_team_id": tid, "espn_league_slug": league},
        "head_coach": coach,
        "players": players,
        "availability": normalize_injuries(injuries),
        "transactions": normalize_transactions(transactions),
        "depth_chart": depthchart_summary(depth),
        "source_health": health,
        "source_errors": errors,
        "sources": sources,
        "governance": {"pit_weekly_snapshot": True, "historical_rewrite": False, "formal_probability_use": False},
    }
    write_snapshot(snapshot, cid, tname, stamp)
    return snapshot, {"roster": 1, "injury_source": int(injuries_ok), "transaction_source": int(transactions_ok), "coach": int(coach is not None), "depth": int(depth_ok)}


def tsd_fallback_domain(cid: str, league_name: str, observed: datetime, stamp: str) -> tuple[str, list[dict[str, Any]], dict[str, int], list[str]]:
    teams = discover_tsd_teams(league_name)
    counts = {"roster": 0, "injury_source": 0, "transaction_source": 0, "coach": 0, "depth": 0}
    errors: list[str] = []
    for team in teams:
        tid, tname = str(team["id"]), str(team["displayName"])
        players, ok, error, player_url = tsd_players(tid)
        if not ok or not players:
            errors.append(f"{tname}: {error or 'zero players'}")
            continue
        raw = team.get("_raw") or {}
        coach_name = raw.get("strManager") or raw.get("strCoach")
        coach = {"name": coach_name, "id": None} if coach_name else None
        counts["roster"] += 1
        counts["coach"] += int(coach is not None)
        snapshot = {
            "schema_version": "V6.6.1-team-configuration-snapshot-r2",
            "observed_at_utc": observed.isoformat(),
            "competition_id": cid,
            "season": "2026",
            "team_name": tname,
            "provider_ids": {"thesportsdb_team_id": tid, "fallback_league_name": league_name},
            "head_coach": coach,
            "players": players,
            "availability": [],
            "transactions": [],
            "depth_chart": [],
            "source_health": {
                "roster_endpoint_ok": True,
                "injuries_endpoint_ok": False,
                "transactions_endpoint_ok": False,
                "team_detail_endpoint_ok": True,
                "depthcharts_endpoint_ok": False,
                "coach_observed": coach is not None,
                "named_player_count": len(players),
                "availability_record_count": 0,
                "transaction_record_count": 0,
            },
            "source_errors": {"injuries": "fallback provider not accepted as injury truth", "transactions": "fallback provider not accepted as transaction truth", "depthcharts": "not available"},
            "sources": [
                {"source_name": "TheSportsDB free API", "source_tier": "tier_3_fallback", "source_url": player_url, "source_observed_at_utc": observed.isoformat(), "source_role": "roster_fallback", "source_reached": True},
                {"source_name": "K League official competition membership", "source_tier": "tier_1_identity", "source_url": "https://www.kleague.com/", "source_observed_at_utc": observed.isoformat(), "source_role": "competition_membership_crosscheck", "source_reached": True},
            ],
            "governance": {"pit_weekly_snapshot": True, "historical_rewrite": False, "formal_probability_use": False, "fallback_roster_only": True},
        }
        write_snapshot(snapshot, cid, tname, stamp)
    return "2026", teams, counts, errors


def main() -> int:
    observed = now_utc()
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    OUTDIR.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "schema_version": "V6.6.1-team-config-fetch-status-r2",
        "generated_at_utc": observed.isoformat(),
        "status": "WARN_CONTEXT_INCOMPLETE",
        "domains": {},
        "snapshots_written": 0,
        "errors": [],
        "quality_totals": {"roster": 0, "injury_source": 0, "transaction_source": 0, "coach": 0, "depth": 0},
    }

    for cid, league in DOMAINS.items():
        dstat: dict[str, Any] = {"league_slug": league, "teams_discovered": 0, "snapshots_written": 0, "team_failures": 0, "discovery_provider": "ESPN"}
        counters = {"roster": 0, "injury_source": 0, "transaction_source": 0, "coach": 0, "depth": 0}
        try:
            payload, discovery_ok, discovery_error = safe_json(f"{ESPN_BASE}/{league}/teams")
            season, teams = teams_from_espn(payload) if discovery_ok else (None, [])
            if not teams and cid in TSD_LEAGUE_FALLBACK:
                dstat["espn_discovery_error"] = discovery_error or "zero teams"
                dstat["discovery_provider"] = "TheSportsDB_fallback_with_official_membership_crosscheck"
                season, teams, counters, fallback_errors = tsd_fallback_domain(cid, TSD_LEAGUE_FALLBACK[cid], observed, stamp)
                dstat["teams_discovered"] = len(teams)
                dstat["snapshots_written"] = counters["roster"]
                dstat["team_failures"] = len(fallback_errors)
                if fallback_errors:
                    status["errors"].extend({"competition_id": cid, "error": error} for error in fallback_errors)
            else:
                if not teams:
                    raise RuntimeError(discovery_error or "ESPN returned no teams")
                dstat["season"] = season
                dstat["teams_discovered"] = len(teams)
                for team in teams:
                    try:
                        _snapshot, inc = espn_team_snapshot(cid, league, str(season), team, observed, stamp)
                        dstat["snapshots_written"] += 1
                        for key in counters:
                            counters[key] += inc[key]
                        time.sleep(0.03)
                    except Exception as exc:
                        dstat["team_failures"] += 1
                        status["errors"].append({"competition_id": cid, "team": team.get("displayName"), "error": f"{type(exc).__name__}: {exc}"})
            dstat["season"] = season
        except Exception as exc:
            dstat["domain_error"] = f"{type(exc).__name__}: {exc}"
            status["errors"].append({"competition_id": cid, "error": dstat["domain_error"]})
        dstat["quality_counts"] = counters
        denom = max(1, int(dstat.get("snapshots_written", 0)))
        dstat["quality_rates"] = {key: value / denom for key, value in counters.items()}
        status["domains"][cid] = dstat
        status["snapshots_written"] += int(dstat.get("snapshots_written", 0))
        for key in status["quality_totals"]:
            status["quality_totals"][key] += counters[key]

    good_domains = sum(1 for item in status["domains"].values() if item.get("snapshots_written", 0) > 0)
    status["domains_with_snapshots"] = good_domains
    status["missing_domains"] = [cid for cid, item in status["domains"].items() if not item.get("snapshots_written")]
    status["roster_complete_17_domains"] = good_domains == len(DOMAINS)
    total_snapshots = max(1, status["snapshots_written"])
    status["quality_rates_global"] = {key: value / total_snapshots for key, value in status["quality_totals"].items()}
    if good_domains == 17 and all(item.get("team_failures", 0) == 0 for item in status["domains"].values()):
        context_rate = min(status["quality_rates_global"]["injury_source"], status["quality_rates_global"]["transaction_source"])
        status["status"] = "PASS_COMPLETE" if context_rate >= 0.90 else "PASS_ROSTER_COMPLETE_CONTEXT_PARTIAL"
    elif good_domains >= 15:
        status["status"] = "WARN_DOMAIN_OR_ROSTER_GAPS"
    else:
        status["status"] = "FAIL_INSUFFICIENT_DOMAIN_COVERAGE"
    status["governance"] = {
        "research_context_only": True,
        "no_current_rule_change": True,
        "no_formal_weight_change": True,
        "no_runtime_probability_change": True,
        "empty_context_is_never_interpreted_as_healthy": True,
    }
    FETCH_STATUS.parent.mkdir(parents=True, exist_ok=True)
    FETCH_STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if good_domains >= 15 else 2


if __name__ == "__main__":
    raise SystemExit(main())
