#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import current_v2_retrospective_exact_history_v1 as exact
import current_v2_retrospective_replay_v1 as replay
import live_delta_acquisition_v1 as live
import runtime as rt

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-score-history-v1"
ERROR = "RETROSPECTIVE_SCORE_HISTORY_UNAVAILABLE"

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
FIXTUREDOWNLOAD_SLUGS = {
    "ENG_PremierLeague": "epl-2026",
    "ESP_LaLiga": "la-liga-2026",
    "GER_Bundesliga": "bundesliga-2026",
    "ITA_SerieA": "serie-a-2026",
    "FRA_Ligue1": "ligue-1-2026",
    replay.UCL: "champions-league-2026",
}
PUBLIC_SEASON = "2026/27"


def _failure(message: str, audit: dict[str, Any] | None = None):
    exc = rt.RuntimeGateError(f"{ERROR}: {message}")
    setattr(exc, "report", audit or {})
    raise exc


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


def _authority_names(rows: list[live.V1Row], comp: str) -> set[str]:
    out: set[str] = set()
    for row in rows:
        if row.competition_id == comp:
            out.add(row.home_team_name)
            out.add(row.away_team_name)
    return out


def _row(repo_root: Path, comp: str, season: str, kickoff: datetime,
         home: str, away: str, hg: int, ag: int, source: str, source_sha: str,
         authority_names: set[str]) -> live.V1Row | None:
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


def _openfootball_rows(repo_root: Path, governed: list[live.V1Row], lower: datetime, upper: datetime):
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0
    capped_upper = min(upper, OPENFOOTBALL_COVERAGE_END)
    if lower >= capped_upper:
        return rows, sources, unresolved
    for comp, filename in OPENFOOTBALL_FILES.items():
        names = _authority_names(governed, comp)
        for folder, season in (("2025-26", "2025/26"), ("2026-27", "2026/27")):
            url = (
                f"https://raw.githubusercontent.com/{OPENFOOTBALL_REPOSITORY}/"
                f"{OPENFOOTBALL_PIN}/{folder}/{filename}"
            )
            try:
                obj, source_sha = _fetch_json(url)
            except Exception as exc:
                sources.append({
                    "provider": "OPENFOOTBALL_PINNED", "competition_id": comp, "season": season,
                    "url": url, "status": "UNAVAILABLE", "reason": str(exc),
                    "coverage_to_exclusive": capped_upper.isoformat(),
                })
                continue
            matches = obj.get("matches") if isinstance(obj, dict) else None
            if not isinstance(matches, list):
                _failure("OpenFootball matches[] missing", {"url": url})
            used = 0
            for item in matches:
                if not isinstance(item, dict):
                    continue
                kickoff = _openfootball_kickoff(comp, item, capped_upper)
                if kickoff is None or not (lower <= kickoff < capped_upper):
                    continue
                score = _openfootball_score(item)
                if score is None:
                    continue
                candidate = _row(
                    repo_root, comp, season, kickoff,
                    str(item.get("team1") or ""), str(item.get("team2") or ""),
                    score[0], score[1], url, source_sha, names,
                )
                if candidate is None:
                    unresolved += 1
                    continue
                rows.append(candidate)
                used += 1
            sources.append({
                "provider": "OPENFOOTBALL_PINNED", "competition_id": comp, "season": season,
                "url": url, "sha256": source_sha, "status": "OBSERVED",
                "rows_in_window": used, "coverage_from": lower.isoformat(),
                "coverage_to_exclusive": capped_upper.isoformat(),
                "pinned_commit": OPENFOOTBALL_PIN, "pinned_commit_date": OPENFOOTBALL_PIN_DATE,
            })
    return rows, sources, unresolved


def _fixturedownload_rows(repo_root: Path, governed: list[live.V1Row], lower: datetime, upper: datetime):
    rows: list[live.V1Row] = []
    sources: list[dict[str, Any]] = []
    unresolved = 0
    for comp, slug in FIXTUREDOWNLOAD_SLUGS.items():
        if comp == replay.UCL:
            continue
        names = _authority_names(governed, comp)
        url = f"https://fixturedownload.com/feed/json/{slug}"
        try:
            obj, source_sha = _fetch_json(url)
        except Exception as exc:
            sources.append({"provider": "FIXTUREDOWNLOAD_PUBLIC", "competition_id": comp, "season": PUBLIC_SEASON,
                            "url": url, "status": "UNAVAILABLE", "reason": str(exc)})
            continue
        if not isinstance(obj, list):
            _failure("FixtureDownload root must be list", {"url": url})
        used = 0
        for item in obj:
            if not isinstance(item, dict):
                continue
            raw_dt = str(item.get("DateUtc") or "").strip()
            if not raw_dt:
                continue
            try:
                kickoff = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                kickoff = kickoff.astimezone(timezone.utc)
            except ValueError as exc:
                _failure(f"FixtureDownload DateUtc invalid: {raw_dt}")
                raise AssertionError from exc
            if not (lower <= kickoff < upper):
                continue
            hs, aw = item.get("HomeTeamScore"), item.get("AwayTeamScore")
            if hs is None or aw is None:
                continue
            try:
                hg, ag = int(hs), int(aw)
            except Exception as exc:
                _failure("FixtureDownload score invalid")
                raise AssertionError from exc
            candidate = _row(
                repo_root, comp, PUBLIC_SEASON, kickoff,
                str(item.get("HomeTeam") or ""), str(item.get("AwayTeam") or ""),
                hg, ag, url, source_sha, names,
            )
            if candidate is None:
                unresolved += 1
                continue
            rows.append(candidate)
            used += 1
        sources.append({
            "provider": "FIXTUREDOWNLOAD_PUBLIC", "competition_id": comp, "season": PUBLIC_SEASON,
            "url": url, "sha256": source_sha, "status": "OBSERVED", "rows_in_window": used,
            "coverage_from": lower.isoformat(), "coverage_to_exclusive": upper.isoformat(),
            "content_sha_bound_at_run": True,
        })
    return rows, sources, unresolved


def _combine(governed: list[live.V1Row], public_groups: list[tuple[str, list[live.V1Row]]]):
    chosen: dict[str, live.V1Row] = {r.fixture_id: r for r in governed}
    provenance: dict[str, list[str]] = {r.fixture_id: ["FOOTBALL_DATA_OR_GOVERNED_OFFICIAL"] for r in governed}
    conflicts: list[dict[str, Any]] = []
    for provider, rows in public_groups:
        for row in rows:
            prior = chosen.get(row.fixture_id)
            if prior is not None and (prior.home_goals, prior.away_goals) != (row.home_goals, row.away_goals):
                conflicts.append({
                    "fixture_id": row.fixture_id, "provider": provider,
                    "prior_score": [prior.home_goals, prior.away_goals],
                    "provider_score": [row.home_goals, row.away_goals],
                })
                continue
            if prior is None:
                chosen[row.fixture_id] = row
                provenance[row.fixture_id] = [provider]
            else:
                provenance.setdefault(row.fixture_id, []).append(provider)
    if conflicts:
        _failure("cross-source score conflict", {"conflicts": conflicts[:20], "conflict_n": len(conflicts)})
    out = sorted(chosen.values(), key=lambda r: (r.kickoff, r.competition_id, r.fixture_id))
    return out, provenance


def research_v1_rows(repo_root: Path, lower: datetime, upper: datetime) -> tuple[list[live.V1Row], dict[str, Any]]:
    lower = lower.astimezone(timezone.utc)
    upper = upper.astimezone(timezone.utc)
    try:
        governed, governed_report = exact.research_v1_rows(repo_root, lower, upper)
    except Exception as exc:
        _failure("governed Football-Data/official history unavailable", {"cause": f"{type(exc).__name__}: {exc}"})
    open_rows, open_sources, open_unresolved = _openfootball_rows(repo_root, governed, lower, upper)
    fixture_rows, fixture_sources, fixture_unresolved = _fixturedownload_rows(repo_root, governed, lower, upper)
    combined, provenance = _combine(
        governed,
        [("OPENFOOTBALL_PINNED", open_rows), ("FIXTUREDOWNLOAD_PUBLIC", fixture_rows)],
    )
    if any(r.kickoff >= upper for r in combined):
        _failure("combined provider crossed retrospective cutoff")
    report = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "source_chain": [
            "FROZEN_REPOSITORY_SNAPSHOT",
            "OPENFOOTBALL_PINNED",
            "FIXTUREDOWNLOAD_PUBLIC",
            "FOOTBALL_DATA_OR_GOVERNED_OFFICIAL",
        ],
        "from": lower.isoformat(),
        "to_exclusive": upper.isoformat(),
        "rows": len(combined),
        "governed_rows": len(governed),
        "openfootball_rows": len(open_rows),
        "fixturedownload_rows": len(fixture_rows),
        "strict_identity_unresolved": open_unresolved + fixture_unresolved,
        "fuzzy_alias_used": False,
        "manual_score_used": False,
        "target_score_fields_read_before_eligibility_gate": False,
        "openfootball_pin": {
            "repository": OPENFOOTBALL_REPOSITORY,
            "commit": OPENFOOTBALL_PIN,
            "commit_date": OPENFOOTBALL_PIN_DATE,
            "coverage_to_exclusive": OPENFOOTBALL_COVERAGE_END.isoformat(),
            "covered_competitions": sorted(OPENFOOTBALL_FILES),
            "not_covered": [replay.UCL, "JPN_J1", "KOR_KLeague1"],
        },
        "sources": open_sources + fixture_sources + list(governed_report.get("sources") or []),
        "governed_source_report": governed_report,
        "provenance_fixture_count": len(provenance),
        "coverage_policy": "competition+season+kickoff interval; gaps fall through in source-chain order; unresolved public identity never guesses",
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
        "source_chain": [
            "FROZEN_REPOSITORY_SNAPSHOT",
            "OPENFOOTBALL_PINNED_NOT_COVERED",
            "FIXTUREDOWNLOAD_PUBLIC_AUDIT_ONLY",
            "GOVERNED_UCL_REPOSITORY",
        ],
        "openfootball_2026_27_ucl_covered": False,
        "fuzzy_alias_used": False,
        "manual_score_used": False,
    })
    return rows, out


def install(replay_module) -> dict[str, Any]:
    replay_module._research_v1_rows = research_v1_rows
    replay_module._ucl_history = research_ucl_history
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "source_chain": [
            "FROZEN_REPOSITORY_SNAPSHOT",
            "OPENFOOTBALL_PINNED",
            "FIXTUREDOWNLOAD_PUBLIC",
            "FOOTBALL_DATA_OR_GOVERNED_OFFICIAL",
        ],
        "openfootball_pin": OPENFOOTBALL_PIN,
        "openfootball_2026_27_covered_competitions": sorted(OPENFOOTBALL_FILES),
        "openfootball_2026_27_not_covered": [replay.UCL, "JPN_J1", "KOR_KLeague1"],
        "fail_closed_error": ERROR,
        "fuzzy_alias_used": False,
        "manual_score_used": False,
        "model_or_current_or_weight_changed": False,
        "formal_scope_changed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
    }
