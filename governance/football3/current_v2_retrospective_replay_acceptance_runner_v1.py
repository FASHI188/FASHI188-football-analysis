#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import current_v2_retrospective_replay_acceptance_v1 as acceptance
import live_delta_acquisition_v1 as live

SCHEMA = "football3-current-v2-retrospective-replay-acceptance-transport-retry-v3"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
MAX_ORIGINAL_ATTEMPTS = 3
MAX_BROWSER_ATTEMPTS = 3
FOOTBALL_DATA_PREFIX = "https://www.football-data.co.uk/"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_LEAGUE = {
    "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1",
    "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1",
    "FRA_Ligue1": "fra.1",
}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,application/json,*/*",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.football-data.co.uk/data.php",
}
ESPN_HEADERS = {
    "User-Agent": "football3-current-v2-retrospective-acceptance/1.0",
    "Accept": "application/json",
    "Accept-Encoding": "identity",
}
TRANSIENT_TOKENS = (
    "HTTP Error 429",
    "HTTP Error 500",
    "HTTP Error 502",
    "HTTP Error 503",
    "HTTP Error 504",
    "timed out",
    "Temporary failure",
    "Connection reset",
    "Remote end closed connection",
)


def _is_transient(exc: Exception) -> bool:
    text = str(exc)
    return any(token in text for token in TRANSIENT_TOKENS)


def install_retry() -> tuple[object, dict[str, Any]]:
    original = live._fetch
    cache: dict[tuple[str, bytes, tuple[tuple[str, str], ...]], tuple[bytes, str]] = {}
    audit: dict[str, Any] = {
        "schema_version": SCHEMA,
        "mode": MODE,
        "formal_authority_changed": False,
        "formal_source_url_changed": False,
        "model_or_current_or_weight_changed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
        "calls": 0,
        "cache_hits": 0,
        "original_transport_success": 0,
        "browser_compatibility_fallback_success": 0,
        "fixture_identity_fallback_authority": "ESPN_PUBLIC_SOCCER_API_TIER_2",
        "fixture_identity_fallbacks": [],
        "fixture_identity_result_fields_accessed": False,
        "failures": [],
    }

    def retry_fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
                    timeout: int = 60):
        key = (url, data or b"", tuple(sorted((headers or {}).items())))
        if key in cache:
            audit["cache_hits"] += 1
            return cache[key]
        audit["calls"] += 1
        last: Exception | None = None
        for attempt in range(1, MAX_ORIGINAL_ATTEMPTS + 1):
            try:
                value = original(url, data=data, headers=headers, timeout=timeout)
                audit["original_transport_success"] += 1
                cache[key] = value
                return value
            except live.AcquisitionError as exc:
                last = exc
                if not _is_transient(exc) or attempt >= MAX_ORIGINAL_ATTEMPTS:
                    break
                time.sleep(float(attempt * 2))

        # Same governed authority and same URL. Only HTTP compatibility headers change.
        if url.startswith(FOOTBALL_DATA_PREFIX):
            compat_headers = dict(headers or {})
            compat_headers.update(BROWSER_HEADERS)
            for attempt in range(1, MAX_BROWSER_ATTEMPTS + 1):
                try:
                    value = original(url, data=data, headers=compat_headers, timeout=timeout)
                    audit["browser_compatibility_fallback_success"] += 1
                    cache[key] = value
                    return value
                except live.AcquisitionError as exc:
                    last = exc
                    if not _is_transient(exc) or attempt >= MAX_BROWSER_ATTEMPTS:
                        break
                    time.sleep(float(attempt * 2))

        audit["failures"].append({
            "url": url,
            "error_type": type(last).__name__ if last is not None else "UNKNOWN",
            "transient": bool(last is not None and _is_transient(last)),
        })
        if isinstance(last, live.AcquisitionError):
            raise last
        raise live.AcquisitionError(f"acceptance transport failed: {url}: {last}") from last

    live._fetch = retry_fetch
    return original, audit


def _espn_main_candidates(comp: str, upper, audit: dict[str, Any]) -> list[dict[str, Any]]:
    slug = ESPN_LEAGUE.get(comp)
    if not slug:
        raise live.AcquisitionError(f"no governed ESPN fixture identity mapping for {comp}")
    out: dict[str, dict[str, Any]] = {}
    day = acceptance.START.date()
    last_day = upper.date()
    observed_urls: list[str] = []
    while day <= last_day:
        token = day.strftime("%Y%m%d")
        url = f"{ESPN_BASE}/{slug}/scoreboard?dates={token}&limit=1000"
        payload, source_sha = live._fetch(url, headers=ESPN_HEADERS)
        observed_urls.append(url)
        obj = json.loads(payload.decode("utf-8-sig"))
        events = obj.get("events") if isinstance(obj, dict) else []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            raw_kickoff = str(event.get("date") or "").strip()
            if not raw_kickoff:
                continue
            kickoff = acceptance.rt._parse_dt(raw_kickoff, "ESPN acceptance fixture kickoff")
            if not (acceptance.START <= kickoff < upper):
                continue
            competitions = event.get("competitions") or []
            if not competitions or not isinstance(competitions[0], dict):
                continue
            competitors = competitions[0].get("competitors") or []
            home_raw = ""
            away_raw = ""
            for item in competitors:
                if not isinstance(item, dict):
                    continue
                side = str(item.get("homeAway") or "").strip().lower()
                team = item.get("team") or {}
                if not isinstance(team, dict):
                    continue
                team_name = str(team.get("displayName") or team.get("name") or "").strip()
                if side == "home":
                    home_raw = team_name
                elif side == "away":
                    away_raw = team_name
            if not home_raw or not away_raw:
                continue
            row = acceptance._metadata(
                comp,
                live._season_label_cross(2026),
                kickoff,
                home_raw,
                away_raw,
                url,
                source_sha,
                "ESPN_PUBLIC_SOCCER_SCOREBOARD_NATIVE_UTC_IDENTITY_ONLY",
            )
            row["fixture_identity_fallback"] = "ESPN_PUBLIC_SOCCER_API_TIER_2"
            out[row["fixture_id"]] = row
        day += timedelta(days=1)
    rows = sorted(out.values(), key=lambda x: (x["kickoff"], x["fixture_id"]))
    audit["fixture_identity_fallbacks"].append({
        "competition_id": comp,
        "authority": "ESPN_PUBLIC_SOCCER_API_TIER_2",
        "date_pages": len(observed_urls),
        "resolved_fixture_count": len(rows),
        "result_fields_accessed": False,
    })
    return rows


def install_fixture_identity_fallback(audit: dict[str, Any]) -> object:
    original_main_candidates = acceptance._main_candidates

    def resilient_main_candidates(comp: str, upper):
        try:
            return original_main_candidates(comp, upper)
        except live.AcquisitionError as exc:
            if comp not in ESPN_LEAGUE or "football-data.co.uk" not in str(exc):
                raise
            return _espn_main_candidates(comp, upper, audit)

    acceptance._main_candidates = resilient_main_candidates
    return original_main_candidates


def _work_dir() -> Path | None:
    if "--work" not in sys.argv:
        return None
    try:
        return Path(sys.argv[sys.argv.index("--work") + 1]).resolve()
    except Exception:
        return None


def main() -> int:
    original_fetch, audit = install_retry()
    original_main_candidates = install_fixture_identity_fallback(audit)
    rc = 1
    try:
        rc = int(acceptance.main())
        return rc
    finally:
        acceptance._main_candidates = original_main_candidates
        live._fetch = original_fetch
        work = _work_dir()
        if work is not None:
            work.mkdir(parents=True, exist_ok=True)
            audit["status"] = "PASS" if rc == 0 else "FAIL"
            (work / "acceptance_transport_audit.json").write_text(
                json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
                encoding="utf-8",
            )


if __name__ == "__main__":
    raise SystemExit(main())
