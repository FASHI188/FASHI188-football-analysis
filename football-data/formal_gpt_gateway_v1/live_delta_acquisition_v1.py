#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt

# Integration/data transport only. No model parameters, weights, or scientific code live here.
SCHEMA = "football3-live-delta-acquisition-v1"
USER_AGENT = "Mozilla/5.0 (compatible; Football3-Formal-GPT-Runner/1.0; prospective PIT acquisition)"

MAIN_EUROPE = {
    "ENG_PremierLeague": "E0",
    "ESP_LaLiga": "SP1",
    "GER_Bundesliga": "D1",
    "ITA_SerieA": "I1",
    "FRA_Ligue1": "F1",
    "NED_Eredivisie": "N1",
    "POR_PrimeiraLiga": "P1",
    "SCO_Premiership": "SC0",
}
EXTRA_ARCHIVE = {
    "ARG_Primera": ("ARG", ["Primera Division", "Primera División", "Liga Profesional"]),
    "BRA_SerieA": ("BRA", ["Serie A", "Brazil Serie A"]),
    "JPN_J1": ("JPN", ["J League", "J1 League", "J. League"]),
    "NOR_Eliteserien": ("NOR", ["Eliteserien"]),
    "SWE_Allsvenskan": ("SWE", ["Allsvenskan"]),
    "SUI_SuperLeague": ("SWZ", ["Super League", "Swiss Super League"]),
    "USA_MLS": ("USA", ["MLS", "Major League Soccer"]),
}
UNDERSTAT = {
    "ENG_PremierLeague": "EPL",
    "ESP_LaLiga": "La_liga",
    "GER_Bundesliga": "Bundesliga",
    "ITA_SerieA": "Serie_A",
    "FRA_Ligue1": "Ligue_1",
}
BIG5 = set(UNDERSTAT)
KOR_URL = "https://www.kleague.com/getScheduleList.do"
JPN_SPECIAL_URL = "https://data.j-league.or.jp/SFMS01/search?competition_frame_ids=35&competition_years=20261"
JPN_SPECIAL_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
JPN_2627_START = datetime(2026, 8, 7, tzinfo=timezone.utc)


class AcquisitionError(rt.RuntimeGateError):
    def __init__(self, message: str, report: dict[str, Any] | None = None):
        super().__init__(message)
        self.report = report or {}


@dataclass(frozen=True)
class V1Row:
    fixture_id: str
    competition_id: str
    season: str
    kickoff: datetime
    home_team_name: str
    away_team_name: str
    home_team_id: str
    away_team_id: str
    home_goals: int
    away_goals: int
    source: str
    source_sha256: str


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[bytes, str]:
    h = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            enc = str(response.headers.get("Content-Encoding") or "").lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise AcquisitionError(f"source fetch failed: {url}: {exc}") from exc
    if not raw:
        raise AcquisitionError(f"source returned empty payload: {url}")
    payload = gzip.decompress(raw) if enc == "gzip" or raw[:2] == b"\x1f\x8b" else raw
    return payload, sha_bytes(raw)


def _decode_csv(payload: bytes) -> list[dict[str, str]]:
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = payload.decode(enc)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise AcquisitionError("CSV encoding unsupported")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AcquisitionError("CSV header missing")
    return [{str(k or "").strip(): "" if v is None else str(v).strip() for k, v in r.items() if k is not None} for r in reader]


def _parse_date(raw: str) -> datetime:
    value = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise AcquisitionError(f"unsupported result date: {value!r}")


def _goals(row: dict[str, str]) -> tuple[int, int] | None:
    h = row.get("FTHG") or row.get("home_goals") or row.get("HG") or ""
    a = row.get("FTAG") or row.get("away_goals") or row.get("AG") or ""
    if h == "" or a == "":
        return None
    try:
        hg, ag = int(float(h)), int(float(a))
    except ValueError as exc:
        raise AcquisitionError("invalid goals in live result source") from exc
    if hg < 0 or ag < 0 or hg > 30 or ag > 30:
        raise AcquisitionError("goal outside formal bounds")
    return hg, ag


def _season_label_cross(start_year: int) -> str:
    return f"{start_year}/{str(start_year + 1)[2:]}"


def _season_code(start_year: int) -> str:
    return f"{str(start_year)[2:]}{str(start_year + 1)[2:]}"


def _cross_year_starts(lower: datetime, upper: datetime) -> list[int]:
    # Include the season containing the frozen boundary and every season through target cutoff.
    first = lower.year - 1 if lower.month <= 6 else lower.year
    last = upper.year - 1 if upper.month <= 6 else upper.year
    return list(range(first, last + 1))


def _canonical(repo_root: Path, comp: str, name: str) -> str:
    aliases = rt._read_aliases(repo_root)
    return rt._canonical_team(comp, name, aliases)


def _v1row(repo_root: Path, comp: str, season: str, date: datetime, home: str, away: str, hg: int, ag: int,
           source: str, source_sha: str) -> V1Row:
    h = _canonical(repo_root, comp, home)
    a = _canonical(repo_root, comp, away)
    if not h or not a or rt._normalize_team(h) == rt._normalize_team(a):
        raise AcquisitionError(f"invalid canonical team identity: {comp} {home} v {away}")
    ko = date.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    fid = rt._fixture_id(comp, season, ko, h, a)
    return V1Row(fid, comp, season, ko, h, a, rt._global_team_id(h), rt._global_team_id(a), hg, ag, source, source_sha)


def _main_rows(repo_root: Path, comp: str, source_code: str, lower: datetime, upper: datetime) -> tuple[list[V1Row], list[dict[str, Any]]]:
    out: list[V1Row] = []
    sources = []
    for start in _cross_year_starts(lower, upper):
        url = f"https://www.football-data.co.uk/mmz4281/{_season_code(start)}/{source_code}.csv"
        try:
            payload, payload_sha = _fetch(url)
        except AcquisitionError:
            # A season URL before its upstream publication is only ignorable if the interval cannot contain that season.
            season_start = datetime(start, 7, 1, tzinfo=timezone.utc)
            if season_start >= upper:
                continue
            raise
        rows = _decode_csv(payload)
        used = 0
        for raw in rows:
            g = _goals(raw)
            if g is None:
                continue
            d = _parse_date(raw.get("Date", ""))
            if not (lower <= d < upper):
                continue
            home = raw.get("HomeTeam", "").strip(); away = raw.get("AwayTeam", "").strip()
            if not home or not away:
                raise AcquisitionError(f"{comp}: completed row missing team")
            out.append(_v1row(repo_root, comp, _season_label_cross(start), d, home, away, g[0], g[1], url, payload_sha))
            used += 1
        sources.append({"competition_id": comp, "url": url, "sha256": payload_sha, "rows_in_window": used, "source_class": "football_data_main"})
    return out, sources


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _extra_season(comp: str, raw_season: str, date: datetime) -> str | None:
    s = str(raw_season or "").strip()
    if comp == "SUI_SuperLeague":
        m = re.fullmatch(r"(20\d{2})[/_-](20\d{2})", s)
        if m:
            return _season_label_cross(int(m.group(1)))
        if re.fullmatch(r"20\d{2}", s):
            return _season_label_cross(int(s))
        return None
    if comp == "JPN_J1":
        if date >= JPN_2627_START:
            return "2026/27" if date.year == 2026 else str(date.year)
        return None  # transition period is supplied only by the official J.League route below.
    return s or str(date.year)


def _extra_rows(repo_root: Path, comp: str, source_code: str, leagues: list[str], lower: datetime, upper: datetime) -> tuple[list[V1Row], list[dict[str, Any]]]:
    url = f"https://www.football-data.co.uk/new/{source_code}.csv"
    payload, payload_sha = _fetch(url)
    rows = _decode_csv(payload)
    selected: list[V1Row] = []
    observed_leagues: set[str] = set()
    for raw in rows:
        league = raw.get("League", "").strip()
        if league:
            observed_leagues.add(league)
        if leagues and league and _norm(league) not in {_norm(x) for x in leagues}:
            continue
        g = _goals(raw)
        if g is None:
            continue
        d = _parse_date(raw.get("Date", ""))
        if not (lower <= d < upper):
            continue
        season = _extra_season(comp, raw.get("Season", ""), d)
        if season is None:
            continue
        home = raw.get("HomeTeam", "").strip(); away = raw.get("AwayTeam", "").strip()
        if not home or not away:
            raise AcquisitionError(f"{comp}: completed extra row missing team")
        selected.append(_v1row(repo_root, comp, season, d, home, away, g[0], g[1], url, payload_sha))
    return selected, [{"competition_id": comp, "url": url, "sha256": payload_sha, "rows_in_window": len(selected),
                       "source_class": "football_data_extra", "observed_leagues": sorted(observed_leagues)[:30]}]


def _kor_rows(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[V1Row], list[dict[str, Any]]]:
    out: list[V1Row] = []
    sources = []
    years = range(lower.year, upper.year + 1)
    seen: set[str] = set()
    for year in years:
        for month in range(1, 13):
            body = json.dumps({"year": str(year), "month": f"{month:02d}", "leagueId": 1}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            payload, payload_sha = _fetch(KOR_URL, data=body, headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json, text/plain, */*"})
            try:
                obj = json.loads(payload.decode("utf-8-sig"))
            except Exception as exc:
                raise AcquisitionError("K League endpoint returned invalid JSON") from exc
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
                try:
                    hg = int(float(str(item.get("homeGoal")))); ag = int(float(str(item.get("awayGoal"))))
                except Exception:
                    continue
                d = _parse_date(str(item.get("gameDate") or "").replace(".", "-"))
                if not (lower <= d < upper):
                    continue
                home = str(item.get("homeTeamName") or item.get("homeTeam") or "").strip()
                away = str(item.get("awayTeamName") or item.get("awayTeam") or "").strip()
                gid = str(item.get("gameId") or f"{year}|{month}|{d.date()}|{home}|{away}")
                if gid in seen:
                    continue
                seen.add(gid)
                out.append(_v1row(repo_root, "KOR_KLeague1", str(year), d, home, away, hg, ag, KOR_URL, payload_sha)); used += 1
            sources.append({"competition_id": "KOR_KLeague1", "url": KOR_URL, "request": {"year": year, "month": month, "leagueId": 1},
                            "sha256": payload_sha, "rows_in_window": used, "source_class": "official_kleague"})
    return out, sources


def _jpn_special_rows(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[V1Row], list[dict[str, Any]]]:
    # Reuse the already-governed parser; it preserves 90-minute score and ignores PK score for model input.
    if upper <= JPN_SPECIAL_START or lower >= JPN_2627_START:
        return [], []
    import importlib.util
    path = repo_root / "football-data" / "ingestion" / "jpn_j1_transition_official_v467.py"
    spec = importlib.util.spec_from_file_location("football3_jpn_special_live", path)
    if spec is None or spec.loader is None:
        raise AcquisitionError("J.League official parser unavailable")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    payload, payload_sha = _fetch(JPN_SPECIAL_URL, headers={"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ja,en;q=0.8"})
    parsed = mod.parse_official_html(payload)
    # The governed parser validates full source identity/stage counts when the special competition is complete.
    mod.validate_rows(parsed)
    out = []
    for raw in parsed:
        d = _parse_date(raw["Date"])
        if lower <= d < upper:
            out.append(_v1row(repo_root, "JPN_J1", "2026_special", d, raw["HomeTeam"], raw["AwayTeam"],
                              int(raw["FTHG"]), int(raw["FTAG"]), JPN_SPECIAL_URL, payload_sha))
    return out, [{"competition_id": "JPN_J1", "url": JPN_SPECIAL_URL, "sha256": payload_sha,
                  "rows_in_window": len(out), "source_class": "official_jleague_2026_special", "full_source_rows": len(parsed)}]


def acquire_v1(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[V1Row], dict[str, Any]]:
    rows: list[V1Row] = []
    sources: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    for comp, code in MAIN_EUROPE.items():
        try:
            r, s = _main_rows(repo_root, comp, code, lower, upper); rows.extend(r); sources.extend(s)
        except Exception as exc:
            failures[comp] = f"{type(exc).__name__}: {exc}"
    for comp, (code, leagues) in EXTRA_ARCHIVE.items():
        try:
            r, s = _extra_rows(repo_root, comp, code, leagues, lower, upper); rows.extend(r); sources.extend(s)
        except Exception as exc:
            failures[comp] = f"{type(exc).__name__}: {exc}"
    try:
        r, s = _jpn_special_rows(repo_root, lower, upper); rows.extend(r); sources.extend(s)
    except Exception as exc:
        failures["JPN_J1_special"] = f"{type(exc).__name__}: {exc}"
    try:
        r, s = _kor_rows(repo_root, lower, upper); rows.extend(r); sources.extend(s)
    except Exception as exc:
        failures["KOR_KLeague1"] = f"{type(exc).__name__}: {exc}"

    # All 16 Frozen V1 formal domains affect global_comp; every designated route must be readable.
    covered = {str(x.get("competition_id")) for x in sources}
    missing_routes = sorted(set(rt.FORMAL_SCOPE) - covered)
    if failures or missing_routes:
        report = {"schema_version": SCHEMA, "stage": "V1_ACQUISITION", "failures": failures, "missing_routes": missing_routes,
                  "covered_routes": sorted(covered), "sources": sources}
        raise AcquisitionError("FORMAL_INPUT_DATA_INCOMPLETE: live V1 source set not complete", report)

    rows.sort(key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r.competition_id, r.fixture_id)
        if key in seen:
            raise AcquisitionError(f"duplicate live V1 fixture: {key}")
        seen.add(key)
    by_comp = {c: 0 for c in rt.FORMAL_SCOPE}
    for r in rows:
        by_comp[r.competition_id] += 1
    return rows, {"status": "COMPLETE", "sources": sources, "rows": len(rows), "rows_by_competition": by_comp}


def _team_lookup(v1_rows: list[V1Row]) -> dict[tuple[str, str], str]:
    d: dict[tuple[str, str], set[str]] = {}
    for r in v1_rows:
        for n in (r.home_team_name, r.away_team_name):
            d.setdefault((r.competition_id, rt._normalize_team(n)), set()).add(n)
    out = {}
    for k, values in d.items():
        if len(values) == 1:
            out[k] = next(iter(values))
    return out


def _understat_payload(comp: str, league: str, start_year: int) -> tuple[dict[str, Any], str, str]:
    url = f"https://understat.com/getLeagueData/{league}/{start_year}"
    payload, payload_sha = _fetch(url, headers={"Accept": "application/json,text/plain,*/*", "X-Requested-With": "XMLHttpRequest", "Referer": "https://understat.com/"})
    try:
        obj = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise AcquisitionError(f"Understat invalid JSON: {comp} {start_year}") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("dates"), list):
        raise AcquisitionError(f"Understat dates[] missing: {comp} {start_year}")
    return obj, payload_sha, url


def acquire_xg(v1_rows: list[V1Row], lower: datetime, upper: datetime, observed_at: datetime, state: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    lookup = _team_lookup(v1_rows)
    v1_index = {(r.competition_id, r.kickoff.date().isoformat(), rt._normalize_team(r.home_team_name), rt._normalize_team(r.away_team_name)): r for r in v1_rows}
    result_xg: dict[str, dict[str, Any]] = {}
    freezes: list[dict[str, Any]] = []
    sources = []
    failures = {}
    existing_pending = set(getattr(state, "pending", {}) or {})
    existing_seen = set(getattr(state, "seen", set()) or set())

    for comp, league in UNDERSTAT.items():
        for start in _cross_year_starts(lower, upper):
            try:
                obj, source_sha, url = _understat_payload(comp, league, start)
            except Exception as exc:
                failures[f"{comp}:{start}"] = f"{type(exc).__name__}: {exc}"; continue
            rows = obj.get("dates") or []
            joined = 0; scheduled_freezes = 0
            for item in rows:
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
                ko = actual.replace(hour=0, minute=0, second=0, microsecond=0)
                if ko >= upper:
                    continue
                hraw = str((item.get("h") or {}).get("title") or "").strip()
                araw = str((item.get("a") or {}).get("title") or "").strip()
                if not hraw or not araw:
                    continue
                h = lookup.get((comp, rt._normalize_team(hraw)), hraw)
                a = lookup.get((comp, rt._normalize_team(araw)), araw)
                season = _season_label_cross(start)
                fid = rt._fixture_id(comp, season, ko, h, a)
                is_result = bool(item.get("isResult"))

                # If this fixture has a V1 result row, that row is authoritative for formal identity/result semantics.
                k = (comp, ko.date().isoformat(), rt._normalize_team(h), rt._normalize_team(a))
                vr = v1_index.get(k)
                if vr is not None:
                    fid = vr.fixture_id; h = vr.home_team_name; a = vr.away_team_name; season = vr.season; ko = vr.kickoff

                if fid not in existing_pending and fid not in existing_seen and lower <= ko < upper:
                    freezes.append({
                        "event_type": "FIXTURE_FREEZE", "event_at": ko.isoformat(), "fixture_id": fid,
                        "competition_id": comp, "season": season, "home_team_id": rt._global_team_id(h),
                        "away_team_id": rt._global_team_id(a), "home_team_name": h, "away_team_name": a,
                        "kickoff": ko.isoformat(), "source": url, "source_content_sha256": source_sha,
                        "enters_v1": True, "enters_xg": True,
                    }); scheduled_freezes += 1

                if not is_result:
                    continue
                xg = item.get("xG") or {}; goals = item.get("goals") or {}
                try:
                    hx, ax = float(xg.get("h")), float(xg.get("a")); hg, ag = int(float(goals.get("h"))), int(float(goals.get("a")))
                except Exception:
                    continue
                if vr is None:
                    # A completed XG row without the designated V1 result source is not sufficient for formal replay.
                    continue
                if (hg, ag) != (vr.home_goals, vr.away_goals):
                    raise AcquisitionError(f"XG/V1 result conflict: {comp} {vr.kickoff.date()} {h} v {a}")
                result_xg[vr.fixture_id] = {"home_xg": hx, "away_xg": ax, "source": url, "source_sha256": source_sha}
                joined += 1
            sources.append({"competition_id": comp, "season_start": start, "url": url, "sha256": source_sha,
                            "dates_rows": len(rows), "result_joined": joined, "freeze_candidates": scheduled_freezes})
    if failures:
        raise AcquisitionError("FORMAL_INPUT_DATA_INCOMPLETE: Understat acquisition failed", {"stage": "XG_ACQUISITION", "failures": failures, "sources": sources})

    # Every newly acquired Big-5 V1 result in the interval must have a matching XG row. Fetch failure/missing join is not reclassified as 'no xG'.
    missing = [r.fixture_id for r in v1_rows if r.competition_id in BIG5 and r.fixture_id not in existing_seen and r.fixture_id not in result_xg]
    if missing:
        raise AcquisitionError("FORMAL_INPUT_DATA_INCOMPLETE: Big5 XG join incomplete", {"stage": "XG_JOIN", "missing_fixture_ids": missing[:100], "missing_n": len(missing), "sources": sources})
    return result_xg, freezes, {"status": "COMPLETE", "sources": sources, "joined_results": len(result_xg), "freeze_events": len(freezes)}


def acquire_verified_delta(repo_root: Path, lower: datetime, upper: datetime, target_fixture_id: str, state: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lower = lower.astimezone(timezone.utc); upper = upper.astimezone(timezone.utc)
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    if observed > upper:
        raise AcquisitionError("FORMAL_INPUT_DATA_INCOMPLETE: live acquisition occurred after requested cutoff",
                               {"observed_at": observed.isoformat(), "cutoff": upper.isoformat()})
    v1_rows, v1_report = acquire_v1(repo_root, lower, upper)
    xg_map, freeze_events, xg_report = acquire_xg(v1_rows, lower, upper, observed, state)

    seen_v1 = set(getattr(getattr(state, "base", None), "seen_fixtures", set()) or set())
    seen_xg = set(getattr(state, "seen", set()) or set())
    pending_xg = set(getattr(state, "pending", {}) or {})
    last_v1 = getattr(getattr(state, "base", None), "last_update_time", None)

    events: list[dict[str, Any]] = []
    # Only freezes not already represented by state are emitted.
    for e in freeze_events:
        if e["fixture_id"] == target_fixture_id or e["fixture_id"] in pending_xg or e["fixture_id"] in seen_xg:
            continue
        events.append(e)

    continuity_gaps = []
    for r in v1_rows:
        if r.fixture_id == target_fixture_id or r.fixture_id in seen_v1:
            continue
        if last_v1 is not None and r.kickoff < last_v1:
            continuity_gaps.append({"fixture_id": r.fixture_id, "reason": "unseen V1 result kickoff precedes cached V1 last_update_time"})
            continue
        enters_xg = r.competition_id in BIG5
        if enters_xg and r.fixture_id not in xg_map:
            continuity_gaps.append({"fixture_id": r.fixture_id, "reason": "Big5 result lacks verified XG"})
            continue
        if enters_xg and r.fixture_id not in pending_xg and r.fixture_id not in seen_xg and r.kickoff < lower:
            continuity_gaps.append({"fixture_id": r.fixture_id, "reason": "XG fixture freeze predates cached cutoff and is absent from pending state"})
            continue
        x = xg_map.get(r.fixture_id)
        source_sha = r.source_sha256 if x is None else sha_bytes((r.source_sha256 + "+" + str(x["source_sha256"])).encode("utf-8"))
        source = r.source if x is None else r.source + " + " + str(x["source"])
        e = {
            "event_type": "LABEL_RELEASE", "event_at": observed.isoformat(), "result_available_at": observed.isoformat(),
            "fixture_id": r.fixture_id, "competition_id": r.competition_id, "season": r.season,
            "home_team_id": r.home_team_id, "away_team_id": r.away_team_id,
            "home_team_name": r.home_team_name, "away_team_name": r.away_team_name, "kickoff": r.kickoff.isoformat(),
            "source": source, "source_content_sha256": source_sha, "enters_v1": True, "enters_xg": enters_xg,
            "home_goals": r.home_goals, "away_goals": r.away_goals,
        }
        if x is not None:
            e["home_xg"] = float(x["home_xg"]); e["away_xg"] = float(x["away_xg"])
        events.append(e)
    if continuity_gaps:
        raise AcquisitionError("FAST_DELTA_CONTINUITY_GAP_REQUIRES_FULL_REBUILD",
                               {"stage": "DELTA_CONTINUITY", "gaps": continuity_gaps[:100], "gap_n": len(continuity_gaps),
                                "v1": v1_report, "xg": xg_report})

    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events.sort(key=lambda e: (rt._parse_dt(str(e["event_at"]), "event_at"), order[e["event_type"]],
                               rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"])))
    # Let the frozen runtime itself enforce every event/PIT schema invariant before we claim COMPLETE.
    checked = rt._validate_delta_records(events, target_fixture_id, lower, upper)
    source_shas = sorted({str(e["source_content_sha256"]) for e in checked})
    source_set_sha = sha_bytes(canon(source_shas + [lower.isoformat(), upper.isoformat(), observed.isoformat()]))
    report = {
        "schema_version": SCHEMA, "status": "VERIFIED_COMPLETE", "observed_at": observed.isoformat(),
        "from": lower.isoformat(), "to": upper.isoformat(), "records": len(checked),
        "records_sha256": rt._sha_bytes(rt._canon_bytes(checked)), "source_set_sha256": source_set_sha,
        "v1": v1_report, "xg": xg_report,
        "xg_contract": "Big5 requires successful Understat join; non-Big5 has no XG delta route and remains exact Frozen V1 when challenger evidence is insufficient",
        "manual_or_auxiliary_fallback_used": False,
    }
    return checked, report
