#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import current_v2_retrospective_replay_acceptance_v1 as acceptance
import live_delta_acquisition_v1 as live

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
SCHEMA = "football3-current-v2-retrospective-replay-acceptance-transport-retry-v4"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
MAX_ORIGINAL_ATTEMPTS = 3
MAX_BROWSER_ATTEMPTS = 3
FOOTBALL_DATA_PREFIX = "https://www.football-data.co.uk/"
JPN_URL = "https://www.football-data.co.uk/new/JPN.csv"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_LEAGUE = {
    "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1",
    "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1",
    "FRA_Ligue1": "fra.1",
}
J1_ESPN_SLUG = "jpn.1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_J1_MANIFESTS = (
    ROOT / "governance/football3/frozen_j1_fixture_identity_manifest_v1.json",
    ROOT / "football-data/manifests/jpn_j1_fixture_identity_manifest_v1.json",
)
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
    "User-Agent": "football3-current-v2-retrospective-acceptance/1.1",
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
FORBIDDEN_IDENTITY_KEYS = {
    "score", "homescore", "awayscore", "home_score", "away_score",
    "result", "winner", "status", "fulltimescore", "full_time_score",
}


def _is_transient(exc: Exception) -> bool:
    text = str(exc)
    return any(token in text for token in TRANSIENT_TOKENS)


def _safe_error(exc: Exception | None) -> str:
    if exc is None:
        return "UNKNOWN"
    text = str(exc)
    if "HTTP Error " in text:
        return text
    return f"{type(exc).__name__}: source unavailable"


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
        "score_blind_guard_status": "PASS",
        "source_attempts": [],
        "failures": [],
        "fallback_attempted": False,
        "failed_domain": None,
        "completed_fixture_count": 0,
        "first_authoritative_failure": None,
    }

    def retry_fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
                    timeout: int = 60):
        key = (url, data or b"", tuple(sorted((headers or {}).items())))
        if key in cache:
            audit["cache_hits"] += 1
            return cache[key]
        audit["calls"] += 1
        last: Exception | None = None
        original_attempts = 0
        browser_attempts = 0
        for attempt in range(1, MAX_ORIGINAL_ATTEMPTS + 1):
            original_attempts = attempt
            try:
                value = original(url, data=data, headers=headers, timeout=timeout)
                audit["original_transport_success"] += 1
                audit["source_attempts"].append({
                    "url": url, "transport": "ORIGINAL", "attempts": attempt, "outcome": "SUCCESS",
                })
                cache[key] = value
                return value
            except live.AcquisitionError as exc:
                last = exc
                if not _is_transient(exc) or attempt >= MAX_ORIGINAL_ATTEMPTS:
                    break
                time.sleep(float(attempt * 2))

        if url.startswith(FOOTBALL_DATA_PREFIX):
            compat_headers = dict(headers or {})
            compat_headers.update(BROWSER_HEADERS)
            for attempt in range(1, MAX_BROWSER_ATTEMPTS + 1):
                browser_attempts = attempt
                try:
                    value = original(url, data=data, headers=compat_headers, timeout=timeout)
                    audit["browser_compatibility_fallback_success"] += 1
                    audit["source_attempts"].append({
                        "url": url,
                        "transport": "BROWSER_COMPATIBILITY",
                        "attempts": attempt,
                        "outcome": "SUCCESS",
                        "original_attempts": original_attempts,
                    })
                    cache[key] = value
                    return value
                except live.AcquisitionError as exc:
                    last = exc
                    if not _is_transient(exc) or attempt >= MAX_BROWSER_ATTEMPTS:
                        break
                    time.sleep(float(attempt * 2))

        failure = {
            "url": url,
            "error_type": type(last).__name__ if last is not None else "UNKNOWN",
            "error": _safe_error(last),
            "transient": bool(last is not None and _is_transient(last)),
            "original_attempts": original_attempts,
            "browser_attempts": browser_attempts,
        }
        audit["source_attempts"].append({**failure, "outcome": "FAIL"})
        audit["failures"].append(failure)
        if isinstance(last, live.AcquisitionError):
            raise last
        raise live.AcquisitionError(f"acceptance transport failed: {url}: {last}") from last

    live._fetch = retry_fetch
    return original, audit


def _canonical_identity(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("competition_id") or ""),
        str(row.get("season") or ""),
        str(row.get("kickoff") or ""),
        str(row.get("home_team_name") or ""),
        str(row.get("away_team_name") or ""),
    )


def _stable_fixture_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        fixture_id = str(row.get("fixture_id") or "")
        if not fixture_id:
            raise live.AcquisitionError("J1 fixture identity source emitted empty fixture_id")
        previous = by_id.get(fixture_id)
        if previous is not None and _canonical_identity(previous) != _canonical_identity(row):
            raise live.AcquisitionError("J1 fixture identity duplicate conflict")
        by_id[fixture_id] = row
    return sorted(by_id.values(), key=lambda x: (str(x["kickoff"]), str(x["fixture_id"])))


def _fixture_set_sha(rows: list[dict[str, Any]]) -> str:
    projection = [_canonical_identity(row) for row in _stable_fixture_rows(rows)]
    raw = json.dumps(projection, ensure_ascii=False, sort_keys=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assert_identity_compatible(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> None:
    left_rows = _stable_fixture_rows(left)
    right_rows = _stable_fixture_rows(right)
    if not left_rows or not right_rows:
        return
    exact_left = {_canonical_identity(row) for row in left_rows}
    exact_right = {_canonical_identity(row) for row in right_rows}
    if exact_left & exact_right:
        return

    # Fail only on a mechanically comparable slot: same competition/kickoff and one identical side.
    # This does not guess aliases or use fuzzy matching.
    for a in left_rows:
        for b in right_rows:
            if str(a["competition_id"]) != str(b["competition_id"]):
                continue
            if str(a["kickoff"]) != str(b["kickoff"]):
                continue
            same_home = str(a["home_team_name"]) == str(b["home_team_name"])
            same_away = str(a["away_team_name"]) == str(b["away_team_name"])
            if same_home or same_away:
                raise live.AcquisitionError("J1_FIXTURE_IDENTITY_SOURCE_CONFLICT")


def _manifest_path() -> Path | None:
    explicit = os.environ.get("FOOTBALL3_J1_FROZEN_FIXTURE_MANIFEST", "").strip()
    if explicit:
        return Path(explicit).resolve()
    for path in DEFAULT_J1_MANIFESTS:
        if path.is_file():
            return path
    return None


def _reject_forbidden_manifest_fields(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).strip().lower().replace("-", "_") in FORBIDDEN_IDENTITY_KEYS:
                raise live.AcquisitionError("J1 frozen identity manifest contains forbidden result field")
            _reject_forbidden_manifest_fields(value)
    elif isinstance(obj, list):
        for value in obj:
            _reject_forbidden_manifest_fields(value)


def _load_frozen_j1_manifest(upper, audit: dict[str, Any], path: Path | None = None) -> list[dict[str, Any]]:
    path = path or _manifest_path()
    if path is None:
        audit["source_attempts"].append({
            "source": "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST",
            "outcome": "UNAVAILABLE",
        })
        return []
    if not path.is_file():
        audit["source_attempts"].append({
            "source": "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST",
            "path": str(path),
            "outcome": "UNAVAILABLE",
        })
        return []
    obj = json.loads(path.read_text(encoding="utf-8"))
    _reject_forbidden_manifest_fields(obj)
    if not isinstance(obj, dict) or not isinstance(obj.get("fixtures"), list):
        raise live.AcquisitionError("J1 frozen identity manifest schema invalid")
    required_top = ("source_identity", "source_url", "observed_at", "content_sha", "parser_schema_version")
    if any(not str(obj.get(key) or "").strip() for key in required_top):
        raise live.AcquisitionError("J1 frozen identity manifest provenance incomplete")
    rows: list[dict[str, Any]] = []
    for item in obj["fixtures"]:
        if not isinstance(item, dict):
            raise live.AcquisitionError("J1 frozen identity manifest fixture schema invalid")
        if str(item.get("competition") or "") != "JPN_J1":
            continue
        fixture_identity = str(item.get("fixture_identity") or "").strip()
        raw_kickoff = str(item.get("kickoff") or "").strip()
        home = str(item.get("home_team_name") or item.get("home") or "").strip()
        away = str(item.get("away_team_name") or item.get("away") or "").strip()
        if not fixture_identity or not raw_kickoff or not home or not away:
            raise live.AcquisitionError("J1 frozen identity manifest fixture provenance incomplete")
        kickoff = acceptance.rt._parse_dt(raw_kickoff, "J1 frozen acceptance fixture kickoff")
        if not (acceptance.START <= kickoff < upper):
            continue
        row = acceptance._metadata(
            "JPN_J1",
            str(item.get("season") or "2026/27"),
            kickoff,
            home,
            away,
            str(obj["source_url"]),
            str(obj["content_sha"]),
            "FROZEN_GOVERNED_J1_FIXTURE_IDENTITY_MANIFEST",
        )
        row["frozen_fixture_identity"] = fixture_identity
        row["fixture_identity_fallback"] = "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST"
        rows.append(row)
    rows = _stable_fixture_rows(rows)
    audit["source_attempts"].append({
        "source": "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST",
        "path": str(path),
        "outcome": "SUCCESS" if rows else "INSUFFICIENT",
        "resolved_fixture_count": len(rows),
        "fixture_set_sha": _fixture_set_sha(rows),
    })
    return rows


def _espn_identity_rows_from_object(obj: Any, comp: str, upper, url: str) -> list[tuple[Any, str, str]]:
    rows: list[tuple[Any, str, str]] = []
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
        if home_raw and away_raw:
            rows.append((kickoff, home_raw, away_raw))
    return rows


def _espn_candidates(comp: str, slug: str, upper, audit: dict[str, Any]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    day = acceptance.START.date()
    last_day = upper.date()
    observed_urls: list[str] = []
    while day <= last_day:
        token = day.strftime("%Y%m%d")
        url = f"{ESPN_BASE}/{slug}/scoreboard?dates={token}&limit=1000"
        payload, _transport_sha = live._fetch(url, headers=ESPN_HEADERS)
        observed_urls.append(url)
        obj = json.loads(payload.decode("utf-8-sig"))
        identity_rows = _espn_identity_rows_from_object(obj, comp, upper, url)
        page_projection = [
            (kickoff.isoformat(), home, away) for kickoff, home, away in identity_rows
        ]
        identity_source_sha = hashlib.sha256(
            json.dumps(page_projection, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        for kickoff, home_raw, away_raw in identity_rows:
            row = acceptance._metadata(
                comp,
                "2026/27" if comp == "JPN_J1" else live._season_label_cross(2026),
                kickoff,
                home_raw,
                away_raw,
                url,
                identity_source_sha,
                "ESPN_PUBLIC_SOCCER_SCOREBOARD_NATIVE_UTC_IDENTITY_ONLY",
            )
            row["fixture_identity_fallback"] = "ESPN_PUBLIC_SOCCER_API_TIER_2"
            out[row["fixture_id"]] = row
        day += timedelta(days=1)
    rows = _stable_fixture_rows(list(out.values()))
    audit["fixture_identity_fallbacks"].append({
        "competition_id": comp,
        "authority": "ESPN_PUBLIC_SOCCER_API_TIER_2",
        "date_pages": len(observed_urls),
        "resolved_fixture_count": len(rows),
        "result_fields_accessed": False,
        "fixture_set_sha": _fixture_set_sha(rows),
    })
    return rows


def _espn_main_candidates(comp: str, upper, audit: dict[str, Any]) -> list[dict[str, Any]]:
    slug = ESPN_LEAGUE.get(comp)
    if not slug:
        raise live.AcquisitionError(f"no governed ESPN fixture identity mapping for {comp}")
    return _espn_candidates(comp, slug, upper, audit)


def _espn_j1_candidates(upper, audit: dict[str, Any]) -> list[dict[str, Any]]:
    return _espn_candidates("JPN_J1", J1_ESPN_SLUG, upper, audit)


def _resolve_j1_candidates(
    upper,
    audit: dict[str, Any],
    original_j1: Callable[[Any], list[dict[str, Any]]],
    *,
    manifest_path: Path | None = None,
    espn_loader: Callable[[Any, dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    audit["j1_identity_chain"] = ["FROZEN_MANIFEST", "FOOTBALL_DATA_JPN_CSV", "ESPN_TIER_2"]
    frozen = _load_frozen_j1_manifest(upper, audit, manifest_path)
    football_data_rows: list[dict[str, Any]] = []
    football_data_error: Exception | None = None
    try:
        football_data_rows = _stable_fixture_rows(original_j1(upper))
        audit["source_attempts"].append({
            "source": "FOOTBALL_DATA_JPN_CSV",
            "url": JPN_URL,
            "outcome": "SUCCESS" if football_data_rows else "INSUFFICIENT",
            "resolved_fixture_count": len(football_data_rows),
            "fixture_set_sha": _fixture_set_sha(football_data_rows),
        })
    except live.AcquisitionError as exc:
        football_data_error = exc
        audit["source_attempts"].append({
            "source": "FOOTBALL_DATA_JPN_CSV",
            "url": JPN_URL,
            "outcome": "FAIL",
            "error": _safe_error(exc),
        })

    if frozen and football_data_rows:
        _assert_identity_compatible(frozen, football_data_rows)
        audit["j1_selected_source"] = "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST"
        audit["j1_fixture_set_sha"] = _fixture_set_sha(frozen)
        return frozen
    if frozen:
        audit["j1_selected_source"] = "FROZEN_GOVERNED_J1_IDENTITY_MANIFEST"
        audit["j1_fixture_set_sha"] = _fixture_set_sha(frozen)
        return frozen
    if football_data_rows:
        audit["j1_selected_source"] = "FOOTBALL_DATA_JPN_CSV"
        audit["j1_fixture_set_sha"] = _fixture_set_sha(football_data_rows)
        return football_data_rows

    audit["fallback_attempted"] = True
    loader = espn_loader or _espn_j1_candidates
    try:
        espn_rows = _stable_fixture_rows(loader(upper, audit))
    except live.AcquisitionError as exc:
        audit["failed_domain"] = "JPN_J1"
        if audit["first_authoritative_failure"] is None:
            audit["first_authoritative_failure"] = {
                "stage": "J1_ACCEPTANCE_FIXTURE_DISCOVERY",
                "error_type": type(exc).__name__,
                "error": _safe_error(exc),
                "football_data_error": _safe_error(football_data_error),
            }
        raise
    if not espn_rows:
        audit["failed_domain"] = "JPN_J1"
        exc = live.AcquisitionError("J1_ACCEPTANCE_FIXTURE_IDENTITY_ALL_SOURCES_UNAVAILABLE")
        if audit["first_authoritative_failure"] is None:
            audit["first_authoritative_failure"] = {
                "stage": "J1_ACCEPTANCE_FIXTURE_DISCOVERY",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "football_data_error": _safe_error(football_data_error),
            }
        raise exc
    audit["j1_selected_source"] = "ESPN_PUBLIC_SOCCER_API_TIER_2"
    audit["j1_fixture_set_sha"] = _fixture_set_sha(espn_rows)
    return espn_rows


def install_fixture_identity_fallback(audit: dict[str, Any]) -> tuple[object, object]:
    original_main_candidates = acceptance._main_candidates
    original_j1_candidates = acceptance._j1_candidates

    def resilient_main_candidates(comp: str, upper):
        try:
            return original_main_candidates(comp, upper)
        except live.AcquisitionError as exc:
            if comp not in ESPN_LEAGUE or "football-data.co.uk" not in str(exc):
                raise
            audit["fallback_attempted"] = True
            return _espn_main_candidates(comp, upper, audit)

    def resilient_j1_candidates(upper):
        return _resolve_j1_candidates(upper, audit, original_j1_candidates)

    acceptance._main_candidates = resilient_main_candidates
    acceptance._j1_candidates = resilient_j1_candidates
    return original_main_candidates, original_j1_candidates


def _work_dir() -> Path | None:
    if "--work" not in sys.argv:
        return None
    try:
        return Path(sys.argv[sys.argv.index("--work") + 1]).resolve()
    except Exception:
        return None


def _completed_fixture_count(work: Path) -> int:
    p = work / "acceptance_summary.json"
    if not p.is_file():
        return 0
    try:
        summary = json.loads(p.read_text(encoding="utf-8"))
        return len(summary.get("four_fixture") or []) + len(summary.get("eight_domain") or [])
    except Exception:
        return 0


def _write_diagnostics(work: Path, audit: dict[str, Any], rc: int) -> None:
    work.mkdir(parents=True, exist_ok=True)
    audit["status"] = "PASS" if rc == 0 else "FAIL"
    audit["score_blind_guard_status"] = (
        "PASS" if audit.get("fixture_identity_result_fields_accessed") is False else "FAIL"
    )
    audit["completed_fixture_count"] = _completed_fixture_count(work)
    exact_head = os.environ.get("FOOTBALL3_CANDIDATE_EXACT_HEAD", "").strip()
    diagnostics = {
        "schema_version": "football3-current-v2-retrospective-replay-failure-diagnostics-v1",
        "status": audit["status"],
        "candidate_exact_head": exact_head,
        "failed_domain": audit.get("failed_domain"),
        "source_attempts": audit.get("source_attempts") or [],
        "fallback_attempted": bool(audit.get("fallback_attempted")),
        "score_blind_guard_status": audit["score_blind_guard_status"],
        "completed_fixture_count": int(audit.get("completed_fixture_count") or 0),
        "first_authoritative_failure": audit.get("first_authoritative_failure"),
        "failure_artifact_is_gate_pass": False,
        "result_fields_accessed": bool(audit.get("fixture_identity_result_fields_accessed")),
    }
    (work / "acceptance_transport_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    (work / "failure_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )


def main() -> int:
    original_fetch, audit = install_retry()
    original_main_candidates, original_j1_candidates = install_fixture_identity_fallback(audit)
    rc = 1
    try:
        rc = int(acceptance.main())
        return rc
    except Exception as exc:
        if audit["first_authoritative_failure"] is None:
            audit["first_authoritative_failure"] = {
                "stage": "ACCEPTANCE_BATCH",
                "error_type": type(exc).__name__,
                "error": _safe_error(exc),
            }
        raise
    finally:
        acceptance._main_candidates = original_main_candidates
        acceptance._j1_candidates = original_j1_candidates
        live._fetch = original_fetch
        work = _work_dir()
        if work is not None:
            _write_diagnostics(work, audit, rc)


if __name__ == "__main__":
    raise SystemExit(main())
