#!/usr/bin/env python3
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ingestion import jpn_j1_transition_official_v467 as jpn_official
import current_v2_retrospective_exact_history_v1 as exact
import current_v2_retrospective_replay_v1 as replay
import live_delta_acquisition_v1 as live
import runtime as rt

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-score-history-v1"
ERROR = "RETROSPECTIVE_SCORE_HISTORY_UNAVAILABLE"
LICENSE = "CC0-1.0"
LICENSE_BLOB = "670154e3538863b2d9891fd5483160fbdfc89164"

OPENFOOTBALL_REPOSITORY = "openfootball/football.json"
OPENFOOTBALL_PIN = "8aa4cd0ce0410b21037f063eeb4edd981081d85d"
OPENFOOTBALL_PIN_DATE = "2026-09-02"
OPENFOOTBALL_COVERAGE_END = datetime(2026, 9, 3, tzinfo=timezone.utc)
OPENFOOTBALL_FILES = {
    "ENG_PremierLeague": "en.1.json",
    "ESP_LaLiga": "es.1.json",
    "GER_Bundesliga": "de.1.json",
    "ITA_SerieA": "it.1.json",
    "FRA_Ligue1": "fr.1.json",
}
OPENFOOTBALL_TZ = {
    "ENG_PremierLeague": ZoneInfo("Europe/London"),
    "ESP_LaLiga": ZoneInfo("Europe/Madrid"),
    "GER_Bundesliga": ZoneInfo("Europe/Berlin"),
    "ITA_SerieA": ZoneInfo("Europe/Rome"),
    "FRA_Ligue1": ZoneInfo("Europe/Paris"),
}
OPENFOOTBALL_UPSTREAM = {
    "ENG_PremierLeague": {
        "repository": "openfootball/england",
        "commit": "ec25b557eeb0f5cefd233fbeeb43d340ce05142e",
        "commit_date": "2026-09-08",
        "path": "2026-27/1-premierleague.txt",
        "git_blob": "75723e627328b7bc218206e786a965cceff39841",
    },
    "ESP_LaLiga": {
        "repository": "openfootball/espana",
        "commit": "35aa00a19cafa72953cc92ccee060352c9ae6e39",
        "commit_date": "2026-09-08",
        "path": "2026-27/1-liga.txt",
        "git_blob": "d48358e2470961994745340082932cdddbd27c60",
    },
    "GER_Bundesliga": {
        "repository": "openfootball/deutschland",
        "commit": "b3039e75a649a251d75f190eb71a96c5d00576ba",
        "commit_date": "2026-09-08",
        "path": "2026-27/1-bundesliga.txt",
        "git_blob": "8dcdaa713f0d2778fbb594e95c2c4cb92555a01c",
    },
    "ITA_SerieA": {
        "repository": "openfootball/italy",
        "commit": "8ba9b3c44145de7a174b0c98bdaf5808798e14ad",
        "commit_date": "2026-09-08",
        "path": "2026-27/1-seriea.txt",
        "git_blob": "fb6f6705d6bb2bdb4ea8d769284080dcc8b3e6d3",
    },
    "FRA_Ligue1": {
        "repository": "openfootball/europe",
        "commit": "a475af95da7a3b0b811148d9479efc15f4a8fd65",
        "commit_date": "2026-09-08",
        "path": "france/2026-27_fr1.txt",
        "git_blob": "65146bff020d8c26365a4e9a9beb25592bd6b709",
    },
}
BIG5 = tuple(OPENFOOTBALL_FILES)
RESEARCH_DOMESTIC_SCOPE = BIG5 + ("JPN_J1", "KOR_KLeague1")
PUBLIC_SEASON = "2026/27"
JST = ZoneInfo("Asia/Tokyo")
SEOUL = ZoneInfo("Asia/Seoul")
JLEAGUE_2627_URL = (
    "https://data.j-league.or.jp/SFMS01/search?"
    "competition_frame_ids=1&competition_ids=725&competition_years=2026&tv_relay_station_name="
)

_DAY_RE = re.compile(r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(20\d{2}))?\s*$")
_SCORE_RE = re.compile(r"\s+(\d+)\s*-\s*(\d+)(?:\s+\([^\n]*\))?\s*$")
_TIME_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(.*)$")


def _failure(message: str, audit: dict[str, Any] | None = None):
    exc = rt.RuntimeGateError(f"{ERROR}: {message}")
    setattr(exc, "report", audit or {})
    raise exc


def _observed_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_json(url: str) -> tuple[Any, str]:
    payload, source_sha = live._fetch(url, headers={"Accept": "application/json"})
    try:
        obj = json.loads(payload.decode("utf-8-sig"))
    except Exception as exc:
        _failure(f"invalid JSON from {url}", {"url": url, "sha256": source_sha})
        raise AssertionError from exc
    return obj, source_sha


def _strict_identity(repo_root: Path, comp: str, raw: str, authority_names: set[str]) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    aliases = rt._read_aliases(repo_root).get(comp, {})
    if value in aliases:
        return str(aliases[value]).strip()
    if value in authority_names:
        return value
    token = rt._normalize_team(value)
    hits = sorted({n for n in authority_names if rt._normalize_team(n) == token})
    return hits[0] if len(hits) == 1 else None


def _source_native_identity(repo_root: Path, comp: str, raw: str, authority_names: set[str]) -> tuple[str | None, str]:
    strict = _strict_identity(repo_root, comp, raw, authority_names)
    if strict is not None:
        return strict, "FORMAL_OR_FROZEN_EXACT"
    value = str(raw or "").strip()
    if not value:
        return None, "EMPTY"
    # This is not a guessed alias. It preserves an exact source identity for a club
    # absent from the frozen authority (e.g. a newly promoted club). Team ids use
    # the platform's deterministic normalization; no edit-distance/fuzzy join exists.
    return value, "SOURCE_NATIVE_EXACT"


def _authority_names(rows: list[Any], comp: str) -> set[str]:
    out: set[str] = set()
    for row in rows:
        if row.competition_id == comp:
            out.add(str(row.home_team_name))
            out.add(str(row.away_team_name))
    return out


def _frozen_authority(repo_root: Path) -> tuple[dict[str, set[str]], dict[str, Any]]:
    frozen, report = rt.load_frozen_v1_history(repo_root)
    names = {comp: _authority_names(frozen, comp) for comp in RESEARCH_DOMESTIC_SCOPE}
    return names, {
        "source_class": "FROZEN_REPOSITORY_SNAPSHOT",
        "rows": len(frozen),
        "authority_counts": {comp: len(names[comp]) for comp in RESEARCH_DOMESTIC_SCOPE},
        "source_report": report,
    }


def _row(repo_root: Path, comp: str, season: str, kickoff: datetime,
         home: str, away: str, hg: int, ag: int, source: str, source_sha: str,
         authority_names: set[str], *, allow_source_native: bool = False) -> live.V1Row | None:
    if allow_source_native:
        ch, _ = _source_native_identity(repo_root, comp, home, authority_names)
        ca, _ = _source_native_identity(repo_root, comp, away, authority_names)
    else:
        ch = _strict_identity(repo_root, comp, home, authority_names)
        ca = _strict_identity(repo_root, comp, away, authority_names)
    if ch is None or ca is None:
        return None
    if rt._normalize_team(ch) == rt._normalize_team(ca):
        _failure(f"same-team identity after strict resolution: {comp}")
    fid = rt._fixture_id(comp, season, kickoff, ch, ca)
    return live.V1Row(
        fid, comp, season, kickoff, ch, ca,
        rt._global_team_id(ch), rt._global_team_id(ca),
        int(hg), int(ag), source, source_sha,
    )


def _openfootball_kickoff(comp: str, item: dict[str, Any], upper: datetime) -> datetime | None:
    date_raw = str(item.get("date") or "").strip()
    time_raw = str(item.get("time") or "").strip()
    if not date_raw:
        return None
    try:
        day = datetime.strptime(date_raw, "%Y-%m-%d")
    except ValueError as exc:
        _failure(f"OpenFootball date invalid: {date_raw}")
        raise AssertionError from exc
    if time_raw:
        try:
            hh, mm = map(int, time_raw.split(":", 1))
            local = day.replace(hour=hh, minute=mm, tzinfo=OPENFOOTBALL_TZ[comp])
        except Exception as exc:
            _failure(f"OpenFootball time invalid: {date_raw} {time_raw}")
            raise AssertionError from exc
        return local.astimezone(timezone.utc)
    # A date-only row on the excluded target UTC date is never score-readable.
    d = day.replace(tzinfo=timezone.utc)
    return d if d.date() < upper.date() else None


def _openfootball_score(item: dict[str, Any]) -> tuple[int, int] | None:
    score = item.get("score")
    if isinstance(score, dict):
        score = score.get("ft")
    if not isinstance(score, list) or len(score) != 2:
        return None
    try:
        hg, ag = int(score[0]), int(score[1])
    except Exception as exc:
        _failure("OpenFootball completed score invalid")
        raise AssertionError from exc
    if min(hg, ag) < 0 or max(hg, ag) > 30:
        _failure("OpenFootball score outside formal bounds")
    return hg, ag


def _openfootball_pinned_comp(repo_root: Path, comp: str, authority: set[str], lower: datetime, upper: datetime,
                              observed_at: str) -> tuple[list[live.V1Row], list[dict[str, Any]], int]:
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0
    capped_upper = min(upper, OPENFOOTBALL_COVERAGE_END)
    if lower >= capped_upper:
        return rows, sources, unresolved
    filename = OPENFOOTBALL_FILES[comp]
    for folder, season in (("2025-26", "2025/26"), ("2026-27", PUBLIC_SEASON)):
        url = f"https://raw.githubusercontent.com/{OPENFOOTBALL_REPOSITORY}/{OPENFOOTBALL_PIN}/{folder}/{filename}"
        try:
            obj, source_sha = _fetch_json(url)
        except Exception as exc:
            sources.append({
                "provider": "OPENFOOTBALL_PINNED_CC0", "competition_id": comp, "season": season,
                "url": url, "status": "UNAVAILABLE", "reason": str(exc), "observed_at": observed_at,
                "coverage_to_exclusive": capped_upper.isoformat(), "license": LICENSE,
            })
            continue
        matches = obj.get("matches") if isinstance(obj, dict) else None
        if not isinstance(matches, list):
            _failure("OpenFootball matches[] missing", {"url": url})
        used = 0
        scheduled = 0
        for item in matches:
            if not isinstance(item, dict):
                continue
            kickoff = _openfootball_kickoff(comp, item, capped_upper)
            if kickoff is None or not (lower <= kickoff < capped_upper):
                continue
            scheduled += 1
            # Trust boundary: score is touched only after the interval/UTC-day gate.
            score = _openfootball_score(item)
            if score is None:
                continue
            candidate = _row(
                repo_root, comp, season, kickoff,
                str(item.get("team1") or ""), str(item.get("team2") or ""),
                score[0], score[1], url, source_sha, authority, allow_source_native=True,
            )
            if candidate is None:
                unresolved += 1
                continue
            rows.append(candidate); used += 1
        sources.append({
            "provider": "OPENFOOTBALL_PINNED_CC0", "competition_id": comp, "season": season,
            "repository": OPENFOOTBALL_REPOSITORY, "commit": OPENFOOTBALL_PIN,
            "commit_date": OPENFOOTBALL_PIN_DATE, "path": f"{folder}/{filename}",
            "url": url, "sha256": source_sha, "license": LICENSE, "license_blob": LICENSE_BLOB,
            "status": "OBSERVED", "observed_at": observed_at, "rows_in_window": used,
            "scheduled_in_window": scheduled, "coverage_partition": season, "coverage_from": lower.isoformat(),
            "coverage_to_exclusive": capped_upper.isoformat(),
            "target_score_fields_read_before_eligibility_gate": False,
        })
    return rows, sources, unresolved


def _footballtxt_date(raw: str, current_year: int) -> tuple[datetime | None, int]:
    m = _DAY_RE.match(raw)
    if not m:
        return None, current_year
    month, day, year = m.groups()
    y = int(year) if year else current_year
    try:
        dt = datetime.strptime(f"{month} {day} {y}", "%b %d %Y")
    except ValueError as exc:
        _failure(f"OpenFootball upstream date invalid: {raw.strip()}")
        raise AssertionError from exc
    return dt, y


def _footballtxt_rows_for_comp(repo_root: Path, comp: str, authority: set[str], lower: datetime, upper: datetime,
                               observed_at: str) -> tuple[list[live.V1Row], dict[str, Any], int]:
    spec = OPENFOOTBALL_UPSTREAM[comp]
    url = f"https://raw.githubusercontent.com/{spec['repository']}/{spec['commit']}/{spec['path']}"
    try:
        payload, source_sha = live._fetch(url, headers={"Accept": "text/plain"})
    except Exception as exc:
        return [], {
            "provider": "OPENFOOTBALL_UPSTREAM_PINNED_CC0", "competition_id": comp,
            **spec, "url": url, "status": "UNAVAILABLE", "reason": str(exc),
            "observed_at": observed_at, "license": LICENSE,
        }, 0
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _failure("OpenFootball upstream UTF-8 decode failed", {"url": url, "sha256": source_sha})
        raise AssertionError from exc
    rows: list[live.V1Row] = []
    unresolved = 0
    scheduled = 0
    current_day: datetime | None = None
    current_year = 2026
    current_time: str | None = None
    for raw_line in text.splitlines():
        day, current_year = _footballtxt_date(raw_line, current_year)
        if day is not None:
            current_day = day
            current_time = None
            continue
        if current_day is None or " v " not in raw_line:
            continue
        body = raw_line
        tm = _TIME_RE.match(raw_line)
        if tm:
            current_time = tm.group(1)
            body = tm.group(2)
        if not current_time:
            continue
        try:
            hh, mm = map(int, current_time.split(":", 1))
            local = current_day.replace(hour=hh, minute=mm, tzinfo=OPENFOOTBALL_TZ[comp])
            kickoff = local.astimezone(timezone.utc)
        except Exception as exc:
            _failure(f"OpenFootball upstream kickoff invalid: {raw_line.strip()}")
            raise AssertionError from exc
        if not (lower <= kickoff < upper):
            continue
        scheduled += 1
        # Trust boundary: only now parse the FT score suffix.
        score_match = _SCORE_RE.search(body)
        if score_match is None:
            continue
        hg, ag = int(score_match.group(1)), int(score_match.group(2))
        if min(hg, ag) < 0 or max(hg, ag) > 30:
            _failure("OpenFootball upstream score outside formal bounds")
        identity = body[:score_match.start()].strip()
        if " v " not in identity:
            continue
        home, away = [x.strip() for x in identity.split(" v ", 1)]
        candidate = _row(repo_root, comp, PUBLIC_SEASON, kickoff, home, away, hg, ag,
                         url, source_sha, authority, allow_source_native=True)
        if candidate is None:
            unresolved += 1
            continue
        rows.append(candidate)
    source = {
        "provider": "OPENFOOTBALL_UPSTREAM_PINNED_CC0", "competition_id": comp,
        **spec, "url": url, "sha256": source_sha, "license": LICENSE, "license_blob": LICENSE_BLOB,
        "status": "OBSERVED", "observed_at": observed_at, "rows_in_window": len(rows),
        "scheduled_in_window": scheduled, "season": PUBLIC_SEASON, "coverage_partition": PUBLIC_SEASON,
        "coverage_from": lower.isoformat(),
        "coverage_to_exclusive": upper.isoformat(), "content_sha_bound_at_run": True,
        "target_score_fields_read_before_eligibility_gate": False,
    }
    return rows, source, unresolved


def _football_data_comp(repo_root: Path, comp: str, authority: set[str], lower: datetime, upper: datetime,
                        observed_at: str) -> tuple[list[live.V1Row], list[dict[str, Any]], int]:
    code = live.MAIN_EUROPE[comp]
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0
    for start in live._cross_year_starts(lower, upper):
        url = f"https://www.football-data.co.uk/mmz4281/{live._season_code(start)}/{code}.csv"
        try:
            payload, source_sha = live._fetch(url)
        except Exception as exc:
            sources.append({
                "provider": "FOOTBALL_DATA_GOVERNED", "competition_id": comp,
                "season": live._season_label_cross(start), "url": url, "status": "UNAVAILABLE",
                "reason": str(exc), "observed_at": observed_at,
            })
            continue
        raw_rows = live._decode_csv(payload)
        used = 0
        scheduled = 0
        for raw in raw_rows:
            kickoff = exact._main_kickoff(raw, start)
            if kickoff is None:
                continue
            if not str(raw.get("Time") or "").strip() and kickoff.date() == upper.date():
                continue
            if not (lower <= kickoff < upper):
                continue
            scheduled += 1
            home = str(raw.get("HomeTeam") or "").strip()
            away = str(raw.get("AwayTeam") or "").strip()
            if not home or not away:
                _failure(f"Football-Data identity incomplete: {comp}")
            # Trust boundary: goals are touched only after the exact time gate.
            goals = live._goals(raw)
            if goals is None:
                continue
            candidate = _row(repo_root, comp, live._season_label_cross(start), kickoff, home, away,
                             goals[0], goals[1], url, source_sha, authority, allow_source_native=True)
            if candidate is None:
                unresolved += 1
                continue
            rows.append(candidate); used += 1
        sources.append({
            "provider": "FOOTBALL_DATA_GOVERNED", "competition_id": comp,
            "season": live._season_label_cross(start), "url": url, "sha256": source_sha,
            "status": "OBSERVED", "observed_at": observed_at, "rows_in_window": used,
            "scheduled_in_window": scheduled, "coverage_partition": live._season_label_cross(start), "coverage_from": lower.isoformat(),
            "coverage_to_exclusive": upper.isoformat(),
            "target_score_fields_read_before_eligibility_gate": False,
        })
    return rows, sources, unresolved


def _jleague_rows_for_url(repo_root: Path, url: str, season: str, competition_token: str,
                          authority: set[str], lower: datetime, upper: datetime, observed_at: str,
                          provider: str) -> tuple[list[live.V1Row], dict[str, Any], int]:
    try:
        payload, source_sha = live._fetch(url, headers={"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ja,en;q=0.8"})
    except Exception as exc:
        return [], {
            "provider": provider, "competition_id": "JPN_J1", "season": season,
            "url": url, "status": "UNAVAILABLE", "reason": str(exc), "observed_at": observed_at,
        }, 0
    parser = jpn_official.TableParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    rows: list[live.V1Row] = []
    unresolved = 0
    scheduled = 0
    for cells in parser.rows:
        if len(cells) < 8 or cells[0] != season or competition_token not in cells[1]:
            continue
        try:
            date_iso = jpn_official._parse_date(cells[3])
            date = datetime.strptime(date_iso, "%Y-%m-%d")
        except Exception as exc:
            _failure(f"J.League date invalid: {cells[3]!r}")
            raise AssertionError from exc
        time_raw = str(cells[4] or "").strip()
        if not time_raw:
            if date.date() == upper.date():
                continue
            kickoff = date.replace(tzinfo=timezone.utc)
        else:
            try:
                hh, mm = map(int, time_raw.split(":", 1))
                kickoff = date.replace(hour=hh, minute=mm, tzinfo=JST).astimezone(timezone.utc)
            except Exception as exc:
                _failure(f"J.League kickoff invalid: {cells[3]!r} {time_raw!r}")
                raise AssertionError from exc
        if not (lower <= kickoff < upper):
            continue
        scheduled += 1
        # Trust boundary: score cell is not interpreted until after cutoff eligibility.
        score_text = str(cells[6] or "")
        if not re.search(r"\d+\s*-\s*\d+", score_text):
            continue
        try:
            hg, ag, _pkh, _pka = jpn_official._parse_score(score_text)
            home = jpn_official.TEAM_MAP[str(cells[5]).strip()]
            away = jpn_official.TEAM_MAP[str(cells[7]).strip()]
        except KeyError as exc:
            _failure(f"J.League governed team token unmapped: {exc.args[0]!r}")
            raise AssertionError from exc
        candidate = _row(repo_root, "JPN_J1", season, kickoff, home, away, hg, ag,
                         url, source_sha, authority, allow_source_native=True)
        if candidate is None:
            unresolved += 1
            continue
        rows.append(candidate)
    source = {
        "provider": provider, "competition_id": "JPN_J1", "season": season,
        "url": url, "sha256": source_sha, "status": "OBSERVED", "observed_at": observed_at,
        "source_owner": "Japan Professional Football League (J.League)",
        "governed_parser_path": "football-data/ingestion/jpn_j1_transition_official_v467.py",
        "rows_in_window": len(rows), "scheduled_in_window": scheduled, "coverage_partition": season,
        "coverage_from": lower.isoformat(), "coverage_to_exclusive": upper.isoformat(),
        "target_score_fields_read_before_eligibility_gate": False,
    }
    return rows, source, unresolved


def _jpn_rows(repo_root: Path, authority: set[str], lower: datetime, upper: datetime,
              observed_at: str) -> tuple[list[live.V1Row], list[dict[str, Any]], int]:
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0
    for url, season, token, provider in (
        (live.JPN_SPECIAL_URL, "2026_special", "Ｊ１百年構想", "JLEAGUE_OFFICIAL_2026_SPECIAL"),
        (JLEAGUE_2627_URL, PUBLIC_SEASON, "Ｊ１", "JLEAGUE_OFFICIAL_2026_27"),
    ):
        r, s, u = _jleague_rows_for_url(repo_root, url, season, token, authority, lower, upper, observed_at, provider)
        rows.extend(r); sources.append(s); unresolved += u
    return rows, sources, unresolved


def _month_ranges(lower: datetime, upper: datetime):
    cursor = datetime(lower.year, lower.month, 1, tzinfo=timezone.utc)
    while cursor < upper:
        year, month = cursor.year, cursor.month
        last = calendar.monthrange(year, month)[1]
        end = datetime(year, month, last, 23, 59, 59, tzinfo=timezone.utc) + timedelta(seconds=1)
        yield year, month, max(lower, cursor), min(upper, end)
        cursor = end


def _kor_rows(repo_root: Path, authority: set[str], lower: datetime, upper: datetime,
              observed_at: str) -> tuple[list[live.V1Row], list[dict[str, Any]], int]:
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0
    seen: set[str] = set()
    for year, month, month_lower, month_upper in _month_ranges(lower, upper):
        body = json.dumps({"year": str(year), "month": f"{month:02d}", "leagueId": 1}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        try:
            payload, source_sha = live._fetch(live.KOR_URL, data=body,
                headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json, text/plain, */*"})
        except Exception as exc:
            sources.append({
                "provider": "KLEAGUE_OFFICIAL", "competition_id": "KOR_KLeague1",
                "request": {"year": year, "month": month, "leagueId": 1}, "url": live.KOR_URL,
                "status": "UNAVAILABLE", "reason": str(exc), "observed_at": observed_at,
                "coverage_from": month_lower.isoformat(), "coverage_to_exclusive": month_upper.isoformat(),
            })
            continue
        try:
            obj = json.loads(payload.decode("utf-8-sig"))
        except Exception as exc:
            _failure("K League official source invalid JSON", {"sha256": source_sha})
            raise AssertionError from exc
        data = obj.get("data", obj) if isinstance(obj, dict) else {}
        schedule = data.get("scheduleList", []) if isinstance(data, dict) else []
        used = 0
        scheduled = 0
        for item in schedule or []:
            if not isinstance(item, dict):
                continue
            meet = str(item.get("meetName") or "")
            if "승강" in meet or "플레이오프" in meet:
                continue
            date = live._parse_date(str(item.get("gameDate") or "").replace(".", "-"))
            kickoff, reason = exact._kor_time(item, date, upper)
            if kickoff is None:
                unresolved += 1
                continue
            if not (lower <= kickoff < upper):
                continue
            scheduled += 1
            finished = str(item.get("gameStatus") or "") == "FE" or item.get("endYn") == "Y"
            if not finished:
                continue
            home = str(item.get("homeTeamName") or item.get("homeTeam") or "").strip()
            away = str(item.get("awayTeamName") or item.get("awayTeam") or "").strip()
            gid = str(item.get("gameId") or f"{year}|{month}|{date.date()}|{home}|{away}")
            if gid in seen:
                continue
            seen.add(gid)
            # Trust boundary: goal fields are touched only after time/cutoff eligibility.
            try:
                hg = int(float(str(item.get("homeGoal"))))
                ag = int(float(str(item.get("awayGoal"))))
            except Exception as exc:
                _failure("K League historical score invalid after eligibility gate")
                raise AssertionError from exc
            candidate = _row(repo_root, "KOR_KLeague1", str(year), kickoff, home, away, hg, ag,
                             live.KOR_URL, source_sha, authority, allow_source_native=True)
            if candidate is None:
                unresolved += 1
                continue
            rows.append(candidate); used += 1
        sources.append({
            "provider": "KLEAGUE_OFFICIAL", "competition_id": "KOR_KLeague1",
            "request": {"year": year, "month": month, "leagueId": 1}, "url": live.KOR_URL,
            "sha256": source_sha, "status": "OBSERVED", "observed_at": observed_at,
            "source_owner": "K League", "season": str(year), "rows_in_window": used, "scheduled_in_window": scheduled,
            "coverage_partition": f"{year:04d}-{month:02d}", "coverage_from": month_lower.isoformat(), "coverage_to_exclusive": month_upper.isoformat(),
            "target_score_fields_read_before_eligibility_gate": False,
        })
    return rows, sources, unresolved


def _match_key(row: live.V1Row):
    return (
        row.competition_id, row.season, row.kickoff.date().isoformat(),
        row.home_team_id, row.away_team_id,
    )


def _combine(public_groups: list[tuple[str, list[live.V1Row]]]):
    chosen: dict[tuple[Any, ...], live.V1Row] = {}
    provenance: dict[tuple[Any, ...], list[str]] = {}
    conflicts: list[dict[str, Any]] = []
    for provider, rows in public_groups:
        local_seen: dict[tuple[Any, ...], live.V1Row] = {}
        for row in rows:
            key = _match_key(row)
            dup = local_seen.get(key)
            if dup is not None and (dup.home_goals, dup.away_goals) != (row.home_goals, row.away_goals):
                conflicts.append({"competition_id": row.competition_id, "season": row.season,
                                  "kickoff_date": row.kickoff.date().isoformat(), "provider": provider,
                                  "reason": "within-provider score conflict"})
                continue
            local_seen[key] = row
            prior = chosen.get(key)
            if prior is not None and (prior.home_goals, prior.away_goals) != (row.home_goals, row.away_goals):
                conflicts.append({"competition_id": row.competition_id, "season": row.season,
                                  "kickoff_date": row.kickoff.date().isoformat(), "provider": provider,
                                  "prior_score": [prior.home_goals, prior.away_goals],
                                  "provider_score": [row.home_goals, row.away_goals]})
                continue
            if prior is None:
                chosen[key] = row
                provenance[key] = [provider]
            else:
                provenance[key].append(provider)
    if conflicts:
        _failure("cross-source score conflict", {"conflicts": conflicts[:20], "conflict_n": len(conflicts)})
    out = sorted(chosen.values(), key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    return out, provenance


def _domain_source_summary(comp: str, rows: list[live.V1Row], sources: list[dict[str, Any]]) -> dict[str, Any]:
    domain_rows = [r for r in rows if r.competition_id == comp]
    observed = [s for s in sources if s.get("competition_id") == comp and s.get("status") == "OBSERVED"]
    contributors = []
    for source in observed:
        if int(source.get("rows_in_window") or 0) > 0:
            contributors.append(str(source.get("provider")))
    selected = contributors[0] if contributors else (str(observed[0].get("provider")) if observed else None)
    return {
        "competition_id": comp,
        "status": "COVERED" if domain_rows else "NO_ROWS_IN_INTERVAL",
        "selected_source": selected,
        "contributing_sources": list(dict.fromkeys(contributors)),
        "fixture_count": len(domain_rows),
        "min_date": min((r.kickoff.date().isoformat() for r in domain_rows), default=None),
        "max_date": max((r.kickoff.date().isoformat() for r in domain_rows), default=None),
    }


def research_v1_rows(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], dict[str, Any]]:
    lower = lower.astimezone(timezone.utc)
    upper = upper.astimezone(timezone.utc)
    if not lower < upper:
        _failure("invalid retrospective score-history interval")
    observed_at = _observed_at()
    authority, frozen_report = _frozen_authority(repo_root)
    groups: list[tuple[str, list[live.V1Row]]] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0

    # Big5: governed research-only ladder. A legacy Football-Data outage is an
    # attempt-level failure, never a pre-provider fatal error.
    for comp in BIG5:
        p_rows, p_sources, p_unresolved = _openfootball_pinned_comp(
            repo_root, comp, authority[comp], lower, upper, observed_at)
        u_rows, u_source, u_unresolved = _footballtxt_rows_for_comp(
            repo_root, comp, authority[comp], lower, upper, observed_at)
        f_rows, f_sources, f_unresolved = _football_data_comp(
            repo_root, comp, authority[comp], lower, upper, observed_at)
        groups.extend([
            ("OPENFOOTBALL_PINNED_CC0", p_rows),
            ("OPENFOOTBALL_UPSTREAM_PINNED_CC0", u_rows),
            ("FOOTBALL_DATA_GOVERNED", f_rows),
        ])
        sources.extend(p_sources); sources.append(u_source); sources.extend(f_sources)
        unresolved += p_unresolved + u_unresolved + f_unresolved

    j_rows, j_sources, j_unresolved = _jpn_rows(repo_root, authority["JPN_J1"], lower, upper, observed_at)
    k_rows, k_sources, k_unresolved = _kor_rows(repo_root, authority["KOR_KLeague1"], lower, upper, observed_at)
    groups.extend([("JLEAGUE_OFFICIAL", j_rows), ("KLEAGUE_OFFICIAL", k_rows)])
    sources.extend(j_sources); sources.extend(k_sources)
    unresolved += j_unresolved + k_unresolved

    combined, provenance = _combine(groups)
    if any(r.kickoff >= upper for r in combined):
        _failure("combined provider crossed retrospective cutoff")

    coverage = {comp: _domain_source_summary(comp, combined, sources) for comp in RESEARCH_DOMESTIC_SCOPE}
    # A domain is unavailable only when the interval actually has a scheduled
    # observation in the governed source set but no eligible completed score row,
    # or when no legal source could be observed at all. An off-season empty interval
    # remains valid research state and is disclosed as NO_MATCHES_SCHEDULED.
    blocked: list[str] = []
    for comp, item in coverage.items():
        comp_sources = [s for s in sources if s.get("competition_id") == comp]
        observed = [s for s in comp_sources if s.get("status") == "OBSERVED"]
        partitions: dict[str, int] = {}
        for source in observed:
            part = str(source.get("coverage_partition") or source.get("season") or "default")
            partitions[part] = max(partitions.get(part, 0), int(source.get("scheduled_in_window") or 0))
        expected_scheduled = sum(partitions.values())
        item["expected_scheduled_fixture_count"] = expected_scheduled
        item["coverage_partitions"] = dict(sorted(partitions.items()))
        item["coverage_complete"] = item["fixture_count"] >= expected_scheduled if observed else False
        if item["fixture_count"] == 0 and observed and expected_scheduled == 0:
            item["status"] = "NO_MATCHES_SCHEDULED"
            item["coverage_complete"] = True
        elif not observed or item["fixture_count"] < expected_scheduled:
            item["status"] = "UNAVAILABLE"
            item["coverage_complete"] = False
            blocked.append(comp)
    if blocked:
        _failure("legal score-history coverage unavailable", {
            "blocked_competitions": blocked, "coverage": coverage, "sources": sources,
            "strict_pit_claimed": False, "mode": MODE,
        })

    report = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "mode": MODE,
        "classification": ["RETROSPECTIVE", "RESEARCH_ONLY", "NOT_ELIGIBLE_FOR_FORMAL_WIN_RATE", "NOT_ELIGIBLE_FOR_PROSPECTIVE_OOS"],
        "strict_pit_claimed": False,
        "observed_at": observed_at,
        "source_chain": [
            "FROZEN_REPOSITORY_SNAPSHOT",
            "OPENFOOTBALL_PINNED_CC0",
            "OPENFOOTBALL_UPSTREAM_PINNED_CC0",
            "FOOTBALL_DATA_GOVERNED_ATTEMPT_LEVEL",
            "JLEAGUE_OFFICIAL",
            "KLEAGUE_OFFICIAL",
        ],
        "from": lower.isoformat(),
        "to_exclusive": upper.isoformat(),
        "rows": len(combined),
        "strict_identity_unresolved": unresolved,
        "fuzzy_alias_used": False,
        "manual_score_used": False,
        "identity_only_score_fallback_used": False,
        "target_score_fields_read_before_eligibility_gate": False,
        "target_fixture_excluded_by_upper_bound": True,
        "target_utc_day_excluded_by_upper_bound": True,
        "public_only_reconstruction_research_only": True,
        "frozen_authority": frozen_report,
        "openfootball_pin": {
            "repository": OPENFOOTBALL_REPOSITORY,
            "commit": OPENFOOTBALL_PIN,
            "commit_date": OPENFOOTBALL_PIN_DATE,
            "coverage_to_exclusive": OPENFOOTBALL_COVERAGE_END.isoformat(),
            "license": LICENSE, "license_blob": LICENSE_BLOB,
            "covered_competitions": sorted(OPENFOOTBALL_FILES),
            "not_covered": [replay.UCL, "JPN_J1", "KOR_KLeague1"],
        },
        "openfootball_upstream": OPENFOOTBALL_UPSTREAM,
        "coverage": coverage,
        "sources": sources,
        "provenance_fixture_count": len(provenance),
        "coverage_policy": "competition+season+kickoff interval; legal sources are independent attempts; no aggregate legacy source prerequisite; duplicate/conflict fail-closed",
        "legacy_aggregate_exact_history_called": False,
    }
    return combined, report


def research_ucl_history(repo_root: Path, upper: datetime):
    try:
        rows, report = exact.ucl_history(repo_root, upper)
    except Exception as exc:
        _failure("UCL governed retrospective history unavailable", {"cause": f"{type(exc).__name__}: {exc}"})
    out = dict(report)
    out.update({
        "schema_version": SCHEMA,
        "mode": MODE,
        "classification": ["RETROSPECTIVE", "RESEARCH_ONLY", "NOT_ELIGIBLE_FOR_FORMAL_WIN_RATE", "NOT_ELIGIBLE_FOR_PROSPECTIVE_OOS"],
        "strict_pit_claimed": False,
        "source_chain": ["GOVERNED_UCL_REPOSITORY"],
        "openfootball_2026_27_ucl_covered": False,
        "fuzzy_alias_used": False,
        "manual_score_used": False,
        "target_score_fields_read_before_eligibility_gate": False,
    })
    return rows, out


def install(replay_module) -> dict[str, Any]:
    replay_module._research_v1_rows = research_v1_rows
    replay_module._ucl_history = research_ucl_history
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "classification": ["RETROSPECTIVE", "RESEARCH_ONLY", "NOT_ELIGIBLE_FOR_FORMAL_WIN_RATE", "NOT_ELIGIBLE_FOR_PROSPECTIVE_OOS"],
        "strict_pit_claimed": False,
        "source_chain": [
            "FROZEN_REPOSITORY_SNAPSHOT",
            "OPENFOOTBALL_PINNED_CC0",
            "OPENFOOTBALL_UPSTREAM_PINNED_CC0",
            "FOOTBALL_DATA_GOVERNED_ATTEMPT_LEVEL",
            "JLEAGUE_OFFICIAL",
            "KLEAGUE_OFFICIAL",
            "GOVERNED_UCL_REPOSITORY",
        ],
        "openfootball_pin": OPENFOOTBALL_PIN,
        "openfootball_upstream_commits": {k: v["commit"] for k, v in OPENFOOTBALL_UPSTREAM.items()},
        "fail_closed_error": ERROR,
        "legacy_aggregate_exact_history_called": False,
        "public_only_reconstruction_research_only": True,
        "fuzzy_alias_used": False,
        "manual_score_used": False,
        "model_or_current_or_weight_changed": False,
        "formal_scope_changed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
    }
