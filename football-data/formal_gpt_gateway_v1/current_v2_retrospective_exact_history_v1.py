#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import current_v2_retrospective_replay_v1 as replay
import live_delta_acquisition_v1 as live
import runtime as rt

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-exact-history-v1"
LONDON = ZoneInfo("Europe/London")
TOKYO = ZoneInfo("Asia/Tokyo")
SEOUL = ZoneInfo("Asia/Seoul")
CENTRAL_EUROPE = ZoneInfo("Europe/Zurich")


def _canonical_row(repo_root: Path, comp: str, season: str, kickoff: datetime,
                   home_raw: str, away_raw: str, hg: int, ag: int,
                   source: str, source_sha: str) -> live.V1Row:
    home = live._canonical(repo_root, comp, home_raw)
    away = live._canonical(repo_root, comp, away_raw)
    if not home or not away or rt._normalize_team(home) == rt._normalize_team(away):
        raise rt.RuntimeGateError(f"research exact identity invalid: {comp} {home_raw} v {away_raw}")
    fid = rt._fixture_id(comp, season, kickoff, home, away)
    return live.V1Row(
        fid, comp, season, kickoff, home, away,
        rt._global_team_id(home), rt._global_team_id(away),
        hg, ag, source, source_sha,
    )


def _main_kickoff(raw: dict[str, str], start: int) -> datetime | None:
    date_raw = str(raw.get("Date") or "").strip()
    time_raw = str(raw.get("Time") or "").strip()
    if not date_raw:
        return None
    date = live._parse_date(date_raw)
    if not time_raw:
        return date
    try:
        local = datetime.strptime(f"{date_raw} {time_raw}", "%d/%m/%Y %H:%M").replace(tzinfo=LONDON)
    except ValueError as exc:
        raise rt.RuntimeGateError(f"Football-Data exact kickoff invalid: {date_raw} {time_raw}") from exc
    return local.astimezone(timezone.utc)


def _main_rows_no_target_read(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved_same_day: list[dict[str, Any]] = []
    for comp, code in live.MAIN_EUROPE.items():
        for start in live._cross_year_starts(lower, upper):
            url = f"https://www.football-data.co.uk/mmz4281/{live._season_code(start)}/{code}.csv"
            try:
                payload, source_sha = live._fetch(url)
            except live.AcquisitionError:
                if datetime(start, 7, 1, tzinfo=timezone.utc) >= upper:
                    continue
                raise
            raw_rows = live._decode_csv(payload)
            used = 0
            for raw in raw_rows:
                kickoff = _main_kickoff(raw, start)
                if kickoff is None:
                    continue
                # Trust boundary: decide time eligibility before touching FTHG/FTAG.
                if not str(raw.get("Time") or "").strip() and kickoff.date() == upper.date():
                    unresolved_same_day.append({"competition_id": comp, "date": kickoff.date().isoformat(), "home": raw.get("HomeTeam"), "away": raw.get("AwayTeam")})
                    continue
                if not (lower <= kickoff < upper):
                    continue
                home = str(raw.get("HomeTeam") or "").strip()
                away = str(raw.get("AwayTeam") or "").strip()
                if not home or not away:
                    raise rt.RuntimeGateError(f"research main row identity incomplete: {comp}")
                goals = live._goals(raw)
                if goals is None:
                    continue
                rows.append(_canonical_row(repo_root, comp, live._season_label_cross(start), kickoff, home, away,
                                           goals[0], goals[1], url, source_sha))
                used += 1
            sources.append({"competition_id": comp, "url": url, "sha256": source_sha, "rows_in_window": used,
                            "source_class": "football_data_main_exact_time", "time_authority": "Football-Data Date+Time interpreted as Europe/London per governed cross-domain audit"})
    return rows, sources, unresolved_same_day


def _extra_kickoff(comp: str, raw: dict[str, str], date: datetime, upper: datetime) -> tuple[datetime | None, str]:
    time_raw = str(raw.get("Time") or "").strip()
    if comp == "JPN_J1" and time_raw:
        try:
            hh, mm = map(int, time_raw.split(":", 1))
            return datetime(date.year, date.month, date.day, hh, mm, tzinfo=TOKYO).astimezone(timezone.utc), "ASIA_TOKYO_SOURCE_TIME"
        except Exception as exc:
            raise rt.RuntimeGateError(f"JPN exact source time invalid: {time_raw}") from exc
    if date.date() == upper.date():
        return None, "SAME_DAY_TIME_UNRESOLVED"
    return date, "DATE_ONLY_SAFE_BEFORE_TARGET_DAY"


def _extra_rows_no_target_read(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for comp, (source_code, leagues) in live.EXTRA_ARCHIVE.items():
        url = f"https://www.football-data.co.uk/new/{source_code}.csv"
        payload, source_sha = live._fetch(url)
        raw_rows = live._decode_csv(payload)
        used = 0
        observed_leagues: set[str] = set()
        for raw in raw_rows:
            league = str(raw.get("League") or "").strip()
            if league:
                observed_leagues.add(league)
            if leagues and league and live._norm(league) not in {live._norm(x) for x in leagues}:
                continue
            date_raw = str(raw.get("Date") or "").strip()
            if not date_raw:
                continue
            date = live._parse_date(date_raw)
            kickoff, time_semantics = _extra_kickoff(comp, raw, date, upper)
            if kickoff is None:
                unresolved.append({"competition_id": comp, "date": date.date().isoformat(), "home": raw.get("HomeTeam"), "away": raw.get("AwayTeam"), "reason": time_semantics})
                continue
            if not (lower <= kickoff < upper):
                continue
            season = live._extra_season(comp, str(raw.get("Season") or ""), date)
            if season is None:
                continue
            home = str(raw.get("HomeTeam") or "").strip()
            away = str(raw.get("AwayTeam") or "").strip()
            if not home or not away:
                raise rt.RuntimeGateError(f"research extra row identity incomplete: {comp}")
            # Score fields are accessed only after the exact/conservative time gate.
            goals = live._goals(raw)
            if goals is None:
                continue
            rows.append(_canonical_row(repo_root, comp, season, kickoff, home, away, goals[0], goals[1], url, source_sha))
            used += 1
        sources.append({"competition_id": comp, "url": url, "sha256": source_sha, "rows_in_window": used,
                        "source_class": "football_data_extra_research_time_guard", "observed_leagues": sorted(observed_leagues)[:30]})
    return rows, sources, unresolved


def _kor_time(item: dict[str, Any], date: datetime, upper: datetime) -> tuple[datetime | None, str]:
    raw = str(item.get("gameTime") or item.get("gameStartTime") or item.get("startTime") or "").strip()
    if raw:
        token = raw.replace(":", "")
        if len(token) == 4 and token.isdigit():
            hh, mm = int(token[:2]), int(token[2:])
            return datetime(date.year, date.month, date.day, hh, mm, tzinfo=SEOUL).astimezone(timezone.utc), "OFFICIAL_KLEAGUE_TIME"
    if date.date() == upper.date():
        return None, "SAME_DAY_OFFICIAL_TIME_UNRESOLVED"
    return date, "DATE_ONLY_SAFE_BEFORE_TARGET_DAY"


def _kor_rows_no_target_read(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year in range(lower.year, upper.year + 1):
        for month in range(1, 13):
            body = json.dumps({"year": str(year), "month": f"{month:02d}", "leagueId": 1}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            payload, source_sha = live._fetch(live.KOR_URL, data=body, headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json, text/plain, */*"})
            try:
                obj = json.loads(payload.decode("utf-8-sig"))
            except Exception as exc:
                raise rt.RuntimeGateError("K League research source invalid JSON") from exc
            data = obj.get("data", obj) if isinstance(obj, dict) else {}
            schedule = data.get("scheduleList", []) if isinstance(data, dict) else []
            used = 0
            for item in schedule or []:
                if not isinstance(item, dict):
                    continue
                meet = str(item.get("meetName") or "")
                if "승강" in meet or "플레이오프" in meet:
                    continue
                finished = str(item.get("gameStatus") or "") == "FE" or item.get("endYn") == "Y"
                if not finished:
                    continue
                date = live._parse_date(str(item.get("gameDate") or "").replace(".", "-"))
                kickoff, reason = _kor_time(item, date, upper)
                if kickoff is None:
                    unresolved.append({"competition_id": "KOR_KLeague1", "date": date.date().isoformat(), "home": item.get("homeTeamName"), "away": item.get("awayTeamName"), "reason": reason})
                    continue
                if not (lower <= kickoff < upper):
                    continue
                home = str(item.get("homeTeamName") or item.get("homeTeam") or "").strip()
                away = str(item.get("awayTeamName") or item.get("awayTeam") or "").strip()
                gid = str(item.get("gameId") or f"{year}|{month}|{date.date()}|{home}|{away}")
                if gid in seen:
                    continue
                seen.add(gid)
                # Goal fields are touched only after finished/time/target gates.
                try:
                    hg = int(float(str(item.get("homeGoal"))))
                    ag = int(float(str(item.get("awayGoal"))))
                except Exception as exc:
                    raise rt.RuntimeGateError("K League historical score invalid after eligibility gate") from exc
                rows.append(_canonical_row(repo_root, "KOR_KLeague1", str(year), kickoff, home, away, hg, ag, live.KOR_URL, source_sha))
                used += 1
            sources.append({"competition_id": "KOR_KLeague1", "url": live.KOR_URL,
                            "request": {"year": year, "month": month, "leagueId": 1}, "sha256": source_sha,
                            "rows_in_window": used, "source_class": "official_kleague_exact_time_guard"})
    return rows, sources, unresolved


def research_v1_rows(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], dict[str, Any]]:
    lower = lower.astimezone(timezone.utc)
    upper = upper.astimezone(timezone.utc)
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for loader in (_main_rows_no_target_read, _extra_rows_no_target_read, _kor_rows_no_target_read):
        r, s, u = loader(repo_root, lower, upper)
        rows.extend(r); sources.extend(s); unresolved.extend(u)
    covered = {str(x.get("competition_id")) for x in sources}
    missing_routes = sorted(set(rt.FORMAL_SCOPE) - covered)
    if missing_routes:
        raise rt.RuntimeGateError(f"CURRENT_V2_RETROSPECTIVE_REPLAY source route incomplete: {missing_routes}")
    focus_unresolved = [x for x in unresolved if x.get("competition_id") in replay.RESEARCH_SCOPE]
    if focus_unresolved:
        raise rt.RuntimeGateError(
            "CURRENT_V2_RETROSPECTIVE_REPLAY_EXACT_KICKOFF_COVERAGE_LIMITATION: "
            f"same-day focus fixtures without governed time={len(focus_unresolved)}"
        )
    rows.sort(key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.competition_id, row.fixture_id)
        if key in seen:
            raise rt.RuntimeGateError(f"duplicate research exact V1 fixture: {key}")
        seen.add(key)
        if row.kickoff >= upper:
            raise rt.RuntimeGateError("research exact V1 source crossed target kickoff")
    return rows, {
        "status": "COMPLETE",
        "source_observation_semantics": "CURRENT_SOURCE_RESEARCH_RECONSTRUCTION",
        "strict_pit_claimed": False,
        "from": lower.isoformat(),
        "to_exclusive": upper.isoformat(),
        "history_selection": "historical_fixture.kickoff_at < target_fixture.kickoff_at",
        "rows": len(rows),
        "sources": sources,
        "same_day_unknown_nonfocus_excluded": [x for x in unresolved if x.get("competition_id") not in replay.RESEARCH_SCOPE],
        "target_score_fields_read_before_eligibility_gate": False,
    }


def research_xg_labels(rows: list[live.V1Row], lower: datetime, upper: datetime,
                       base_state: Any) -> tuple[dict[str, rt.XGLabel], dict[str, Any]]:
    del base_state
    if not rows:
        return {}, {"status": "COMPLETE", "joined_results": 0, "target_score_fields_read_before_eligibility_gate": False}
    index = {(r.competition_id, r.kickoff.date().isoformat(), rt._normalize_team(r.home_team_name), rt._normalize_team(r.away_team_name)): r
             for r in rows if r.competition_id in live.BIG5}
    out: dict[str, rt.XGLabel] = {}
    sources: list[dict[str, Any]] = []
    for comp, league in live.UNDERSTAT.items():
        for start in live._cross_year_starts(lower, upper):
            obj, source_sha, url = live._understat_payload(comp, league, start)
            joined = 0
            for item in obj.get("dates") or []:
                if not isinstance(item, dict):
                    continue
                raw_dt = str(item.get("datetime") or "").strip()
                if not raw_dt:
                    continue
                try:
                    actual = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                    if actual.tzinfo is None:
                        actual = actual.replace(tzinfo=timezone.utc)
                    actual = actual.astimezone(timezone.utc)
                except ValueError:
                    continue
                # Exact time gate before xG/goals access.
                if actual >= upper:
                    continue
                hraw = str((item.get("h") or {}).get("title") or "").strip()
                araw = str((item.get("a") or {}).get("title") or "").strip()
                key_candidates = [k for k in index if k[0] == comp and k[1] == actual.date().isoformat()
                                  and (k[2] == rt._normalize_team(hraw) or k[3] == rt._normalize_team(araw))]
                row = None
                for key in key_candidates:
                    candidate = index[key]
                    if rt._normalize_team(candidate.home_team_name) == rt._normalize_team(hraw) and rt._normalize_team(candidate.away_team_name) == rt._normalize_team(araw):
                        row = candidate; break
                if row is None:
                    continue
                if row.kickoff >= upper:
                    continue
                if not bool(item.get("isResult")):
                    continue
                xg = item.get("xG") or {}; goals = item.get("goals") or {}
                try:
                    hx, ax = float(xg.get("h")), float(xg.get("a"))
                    hg, ag = int(float(goals.get("h"))), int(float(goals.get("a")))
                except Exception as exc:
                    raise rt.RuntimeGateError("research xG/result payload invalid after time gate") from exc
                if (hg, ag) != (row.home_goals, row.away_goals):
                    raise rt.RuntimeGateError("research xG/V1 result conflict")
                release = row.kickoff + timedelta(hours=3)
                out[row.fixture_id] = rt.XGLabel(rt.hxg.ReleasedLabel(hg, ag, hx, ax, release), row.fixture_id, source_sha, row.kickoff.isoformat())
                joined += 1
            sources.append({"competition_id": comp, "season_start": start, "url": url, "sha256": source_sha, "joined": joined})
    missing = [r.fixture_id for r in rows if r.competition_id in live.BIG5 and r.fixture_id not in out]
    if missing:
        raise rt.RuntimeGateError(f"CURRENT_V2_RETROSPECTIVE_REPLAY Big5 xG coverage incomplete: {len(missing)}")
    return out, {
        "status": "COMPLETE",
        "joined_results": len(out),
        "source_observation_semantics": "CURRENT_SOURCE_RESEARCH_RECONSTRUCTION",
        "strict_pit_claimed": False,
        "research_release_adapter": "kickoff_plus_3h",
        "sources": sources,
        "target_score_fields_read_before_eligibility_gate": False,
    }


def _ucl_exact_kickoff(date: datetime, time_raw: str, upper: datetime) -> datetime | None:
    if time_raw:
        try:
            hh, mm = map(int, time_raw.split(":", 1))
            return datetime(date.year, date.month, date.day, hh, mm, tzinfo=CENTRAL_EUROPE).astimezone(timezone.utc)
        except Exception as exc:
            raise rt.RuntimeGateError(f"UCL exact kickoff invalid: {time_raw}") from exc
    if date.date() == upper.date():
        return None
    return date


def ucl_history(repo_root: Path, upper: datetime) -> tuple[list[rt.HistoryFixture], dict[str, Any]]:
    directory = repo_root / "football-data" / "processed" / replay.UCL
    if not directory.is_dir():
        raise rt.RuntimeGateError("UCL processed history directory missing")
    registry = replay._ucl_registry(repo_root)
    accepted: dict[str, str] = {}
    for item in registry["teams"]:
        canonical = str(item["uefa_name"])
        for name in item.get("accepted_exact_names") or []:
            key = replay._exact_key(str(name))
            prior = accepted.get(key)
            if prior is not None and prior != canonical:
                raise rt.RuntimeGateError("UCL accepted identity collision")
            accepted[key] = canonical
    rows: list[rt.HistoryFixture] = []
    files: dict[str, Any] = {}
    unresolved = 0
    seen: set[str] = set()
    for path in sorted(directory.glob("*.csv")):
        source_sha = replay._file_sha(path)
        used = 0
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for raw in csv.DictReader(f):
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                date_raw = str(raw.get("Date") or raw.get("date") or "").strip()
                if not season or not date_raw:
                    continue
                date = rt._parse_match_date(date_raw, season)
                kickoff = _ucl_exact_kickoff(date, str(raw.get("Time") or "").strip(), upper)
                if kickoff is None:
                    unresolved += 1
                    continue
                # Eligibility before scores.
                if kickoff >= upper:
                    continue
                home_raw = str(raw.get("HomeTeam") or raw.get("home_team") or "").strip()
                away_raw = str(raw.get("AwayTeam") or raw.get("away_team") or "").strip()
                home = accepted.get(replay._exact_key(home_raw), home_raw)
                away = accepted.get(replay._exact_key(away_raw), away_raw)
                if not home or not away or replay._exact_key(home) == replay._exact_key(away):
                    raise rt.RuntimeGateError("UCL historical identity invalid")
                try:
                    hg = int(str(raw.get("FTHG") or raw.get("home_goals") or "").strip())
                    ag = int(str(raw.get("FTAG") or raw.get("away_goals") or "").strip())
                except ValueError as exc:
                    raise rt.RuntimeGateError("UCL historical score invalid after eligibility gate") from exc
                fid = rt._fixture_id(replay.UCL, season, kickoff, home, away)
                if fid in seen:
                    raise rt.RuntimeGateError("duplicate UCL retrospective history fixture")
                seen.add(fid)
                rows.append(rt.HistoryFixture(fid, replay.UCL, season, kickoff, rt._global_team_id(home), rt._global_team_id(away),
                                              home, away, hg, ag, str(path.relative_to(repo_root)), source_sha))
                used += 1
        if used:
            files[str(path.relative_to(repo_root))] = {"sha256": source_sha, "used_rows": used}
    rows.sort(key=lambda r: (r.kickoff, r.fixture_id))
    if not rows:
        raise rt.RuntimeGateError("UCL retrospective history unavailable")
    return rows, {
        "status": "COMPLETE", "rows": len(rows), "files": files, "to_exclusive": upper.isoformat(),
        "same_day_time_unresolved_excluded": unresolved, "target_score_fields_read_before_eligibility_gate": False,
    }


def exact_history_upper(target_kickoff: datetime) -> datetime:
    return target_kickoff.astimezone(timezone.utc)


def install(replay_module) -> dict[str, Any]:
    replay_module._safe_history_upper = exact_history_upper
    replay_module._research_v1_rows = research_v1_rows
    replay_module._current_xg_labels = research_xg_labels
    replay_module._ucl_history = ucl_history
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "history_selection": "historical_fixture.kickoff_at < target_fixture.kickoff_at",
        "target_score_gate_before_score_field_access": True,
        "same_kickoff_predict_before_update": True,
        "strict_pit_claimed": False,
        "production_source_changed": False,
        "formal_scope_changed": False,
        "model_or_current_or_weight_changed": False,
    }
