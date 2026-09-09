#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import current_v2_retrospective_fixture_identity_provider_v1 as chain
import current_v2_retrospective_replay_acceptance_runner_v1 as legacy

acceptance = legacy.acceptance
live = legacy.live

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-replay-acceptance-provider-chain-v2"
HISTORY_SOURCE_SCHEMA = "football3-current-v2-retrospective-frozen-history-source-chain-v1"
ROOT = Path(__file__).resolve().parents[2]
INGESTION_MANIFEST = ROOT / "football-data/manifests/latest_ingestion.json"
FOOTBALL_DATA_HISTORY_PREFIX = "https://www.football-data.co.uk/mmz4281/"

ESPN_SLUGS = {
    "ENG_PremierLeague": "eng.1",
    "ESP_LaLiga": "esp.1",
    "GER_Bundesliga": "ger.1",
    "ITA_SerieA": "ita.1",
    "FRA_Ligue1": "fra.1",
    "JPN_J1": "jpn.1",
    "KOR_KLeague1": "kor.1",
}
DOMAIN_SLUGS = {
    "ENG_PremierLeague": "eng_premier_league",
    "ESP_LaLiga": "esp_laliga",
    "GER_Bundesliga": "ger_bundesliga",
    "ITA_SerieA": "ita_serie_a",
    "FRA_Ligue1": "fra_ligue_1",
    "JPN_J1": "jpn_j1",
    "KOR_KLeague1": "kor_kleague1",
    "UEFA_ChampionsLeague": "uefa_champions_league",
}


def _season(comp: str) -> str:
    if comp == "KOR_KLeague1":
        return "2026"
    return live._season_label_cross(2026)


def _content_sha_from_rows(rows: list[dict[str, Any]]) -> str:
    projection = [
        (
            str(r.get("competition_id") or ""),
            str(r.get("fixture_id") or ""),
            str(r.get("kickoff") or ""),
            str(r.get("home_team_name") or ""),
            str(r.get("away_team_name") or ""),
        )
        for r in rows
    ]
    raw = json.dumps(sorted(projection), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _provider_records(comp: str, rows: list[dict[str, Any]], source_identity: str, observed_at: str) -> list[dict[str, str]]:
    content_sha_default = _content_sha_from_rows(rows)
    out: list[dict[str, str]] = []
    for row in legacy._stable_fixture_rows(rows):
        out.append({
            "competition": comp,
            "fixture_identity": str(row["fixture_id"]),
            "kickoff": str(row["kickoff"]),
            "home_identity": str(row["home_team_name"]),
            "away_identity": str(row["away_team_name"]),
            "source_identity": str(row.get("provider_source_identity") or source_identity),
            "observed_at": str(row.get("provider_observed_at") or observed_at),
            "content_sha": str(row.get("source_sha256") or content_sha_default),
        })
    return out


def _manifest_rows_from_object(obj: Any, comp: str, upper) -> list[dict[str, Any]]:
    fixtures = obj.get("fixtures") if isinstance(obj, dict) else None
    if not isinstance(fixtures, list):
        raise live.AcquisitionError(f"FIXTURE_IDENTITY_MANIFEST_SCHEMA_INVALID:{comp}")
    source_identity = str(obj.get("source_identity") or "").strip()
    source_url = str(obj.get("source_url") or "").strip()
    observed_at = str(obj.get("observed_at") or "").strip()
    content_sha = str(obj.get("content_sha") or "").strip().lower()
    if not source_identity or not source_url or not observed_at or len(content_sha) != 64:
        raise live.AcquisitionError(f"FIXTURE_IDENTITY_MANIFEST_PROVENANCE_INCOMPLETE:{comp}")
    rows: list[dict[str, Any]] = []
    for item in fixtures:
        if not isinstance(item, dict):
            raise live.AcquisitionError(f"FIXTURE_IDENTITY_MANIFEST_FIXTURE_INVALID:{comp}")
        # Score/result/status/outcome fields may exist in the payload. This parser intentionally never reads them.
        if str(item.get("competition") or "") != comp:
            continue
        raw_kickoff = str(item.get("kickoff") or "").strip()
        home = str(item.get("home_team_name") or item.get("home_identity") or item.get("home") or "").strip()
        away = str(item.get("away_team_name") or item.get("away_identity") or item.get("away") or "").strip()
        fixture_identity = str(item.get("fixture_identity") or "").strip()
        if not raw_kickoff or not home or not away or not fixture_identity:
            raise live.AcquisitionError(f"FIXTURE_IDENTITY_MANIFEST_FIXTURE_INCOMPLETE:{comp}")
        kickoff = acceptance.rt._parse_dt(raw_kickoff, f"{comp} governed fixture identity kickoff")
        if not (acceptance.START <= kickoff < upper):
            continue
        row = acceptance._metadata(
            comp,
            str(item.get("season") or _season(comp)),
            kickoff,
            home,
            away,
            source_url,
            content_sha,
            "GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST",
        )
        row["governed_fixture_identity"] = fixture_identity
        row["provider_source_identity"] = source_identity
        row["provider_observed_at"] = observed_at
        rows.append(row)
    return legacy._stable_fixture_rows(rows)


def _manifest_candidates(comp: str, upper, explicit_path: Path | None = None) -> list[dict[str, Any]]:
    path = explicit_path or _manifest_path(comp)
    if path is None or not path.is_file():
        return []
    obj = json.loads(path.read_text(encoding="utf-8"))
    return _manifest_rows_from_object(obj, comp, upper)


def _manifest_path(comp: str) -> Path | None:
    slug = DOMAIN_SLUGS[comp]
    env = f"FOOTBALL3_{slug.upper()}_FROZEN_FIXTURE_MANIFEST"
    raw = os.environ.get(env, "").strip()
    if raw:
        return Path(raw).resolve()
    candidates = [
        ROOT / f"governance/football3/frozen_{slug}_fixture_identity_manifest_v1.json",
        ROOT / f"football-data/manifests/{slug}_fixture_identity_manifest_v1.json",
    ]
    if comp == "JPN_J1":
        candidates = list(legacy.DEFAULT_J1_MANIFESTS) + candidates
    for path in candidates:
        if path.is_file():
            return path
    return None


def _espn_candidates(comp: str, upper, audit: dict[str, Any]) -> list[dict[str, Any]]:
    slug = ESPN_SLUGS.get(comp)
    if not slug:
        raise live.AcquisitionError(f"NO_APPROVED_SCORE_BLIND_FALLBACK:{comp}")
    out: dict[str, dict[str, Any]] = {}
    day = acceptance.START.date()
    last_day = upper.date()
    while day <= last_day:
        token = day.strftime("%Y%m%d")
        url = f"{legacy.ESPN_BASE}/{slug}/scoreboard?dates={token}&limit=1000"
        payload, _transport_sha = live._fetch(url, headers=legacy.ESPN_HEADERS)
        obj = json.loads(payload.decode("utf-8-sig"))
        identity_rows = legacy._espn_identity_rows_from_object(obj, comp, upper, url)
        page_projection = [(k.isoformat(), h, a) for k, h, a in identity_rows]
        page_sha = hashlib.sha256(json.dumps(page_projection, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        for kickoff, home_raw, away_raw in identity_rows:
            row = acceptance._metadata(
                comp,
                _season(comp),
                kickoff,
                home_raw,
                away_raw,
                url,
                page_sha,
                "ESPN_PUBLIC_SOCCER_SCOREBOARD_NATIVE_UTC_IDENTITY_ONLY",
            )
            row["fixture_identity_fallback"] = "ESPN_PUBLIC_SOCCER_API_TIER_2"
            out[row["fixture_id"]] = row
        day += timedelta(days=1)
    rows = legacy._stable_fixture_rows(list(out.values()))
    audit.setdefault("fixture_identity_fallbacks", []).append({
        "competition_id": comp,
        "authority": "ESPN_PUBLIC_SOCCER_API_TIER_2",
        "resolved_fixture_count": len(rows),
        "result_fields_accessed": False,
        "fixture_set_sha": legacy._fixture_set_sha(rows),
    })
    return rows


def _run_provider_chain(
    comp: str,
    upper,
    audit: dict[str, Any],
    providers: list[tuple[str, int, Callable[[], list[dict[str, Any]]]]],
) -> list[dict[str, Any]]:
    cache: dict[str, list[dict[str, Any]]] = {}
    observed_at = acceptance._now().astimezone(timezone.utc).isoformat()

    def wrapped(name: str, loader: Callable[[], list[dict[str, Any]]]):
        def load_records():
            rows = legacy._stable_fixture_rows(loader())
            cache[name] = rows
            return _provider_records(comp, rows, name, observed_at)
        return load_records

    before = len(audit.setdefault("fixture_identity_provider_attempts", []))
    selected_records = chain.resolve(
        comp,
        [chain.Provider(name, priority, wrapped(name, loader)) for name, priority, loader in providers],
        audit,
        error_factory=live.AcquisitionError,
    )
    _ = selected_records
    for attempt in audit["fixture_identity_provider_attempts"][before:]:
        audit.setdefault("source_attempts", []).append({
            "source": attempt.get("provider"),
            "competition_id": comp,
            "provider_priority": attempt.get("priority"),
            "outcome": attempt.get("outcome"),
            "http_status": attempt.get("http_status"),
            "error": attempt.get("error"),
            "resolved_fixture_count": attempt.get("resolved_fixture_count"),
            "inventory_sha": attempt.get("inventory_sha"),
        })
    selected = audit["fixture_identity_selected_provider"][comp]
    rows = cache.get(selected) or []
    if not rows:
        raise live.AcquisitionError(f"FIXTURE_IDENTITY_SELECTED_PROVIDER_EMPTY:{comp}:{selected}")
    audit["fixture_identity_result_fields_accessed"] = False
    return rows


def install_common_fixture_identity_provider_chain(audit: dict[str, Any]) -> tuple[object, object]:
    original_main = acceptance._main_candidates
    original_j1 = acceptance._j1_candidates
    original_k1 = acceptance._k1_candidates
    original_ucl = acceptance._ucl_candidate

    def main_candidates(comp: str, upper):
        return _run_provider_chain(comp, upper, audit, [
            ("GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST", 1, lambda: _manifest_candidates(comp, upper)),
            ("ESPN_PUBLIC_SOCCER_API_TIER_2", 2, lambda: _espn_candidates(comp, upper, audit)),
            (f"FOOTBALL_DATA_{live.MAIN_EUROPE[comp]}_CSV", 3, lambda: original_main(comp, upper)),
        ])

    def j1_candidates(upper):
        return _run_provider_chain("JPN_J1", upper, audit, [
            ("GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST", 1, lambda: _manifest_candidates("JPN_J1", upper)),
            ("ESPN_PUBLIC_SOCCER_API_TIER_2", 2, lambda: _espn_candidates("JPN_J1", upper, audit)),
            ("FOOTBALL_DATA_JPN_CSV", 3, lambda: original_j1(upper)),
        ])

    def k1_candidates(upper):
        return _run_provider_chain("KOR_KLeague1", upper, audit, [
            ("GOVERNED_FROZEN_FIXTURE_IDENTITY_MANIFEST", 1, lambda: _manifest_candidates("KOR_KLeague1", upper)),
            ("ESPN_PUBLIC_SOCCER_API_TIER_2", 2, lambda: _espn_candidates("KOR_KLeague1", upper, audit)),
            ("OFFICIAL_KLEAGUE_SCHEDULE_API", 3, lambda: original_k1(upper)),
        ])

    def ucl_candidate(upper):
        rows = _run_provider_chain("UEFA_ChampionsLeague", upper, audit, [
            ("GOVERNED_UCL_36_TEAM_AUTHORITY_BRIDGE", 1, lambda: [original_ucl(upper)]),
        ])
        if len(rows) != 1:
            raise live.AcquisitionError("UCL_GOVERNED_AUTHORITY_FIXTURE_CARDINALITY_INVALID")
        return rows[0]

    acceptance._main_candidates = main_candidates
    acceptance._j1_candidates = j1_candidates
    acceptance._k1_candidates = k1_candidates
    acceptance._ucl_candidate = ucl_candidate
    audit["fixture_identity_provider_chain_schema"] = SCHEMA
    audit["provider_chain_supported_domains"] = list(acceptance.DOMAINS)
    audit["provider_chain_prospective_path_changed"] = False
    audit["provider_chain_strict_pit_path_changed"] = False
    audit["provider_chain_model_current_weights_changed"] = False
    return original_main, original_j1


def _history_manifest_index(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    manifest = root / "football-data/manifests/latest_ingestion.json"
    if not manifest.is_file():
        return {}
    try:
        obj = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        raise live.AcquisitionError(f"FROZEN_HISTORY_INGESTION_MANIFEST_INVALID:{exc}") from exc
    entries = obj.get("entries") if isinstance(obj, dict) else None
    if not isinstance(entries, list):
        raise live.AcquisitionError("FROZEN_HISTORY_INGESTION_MANIFEST_SCHEMA_INVALID")
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url.startswith(FOOTBALL_DATA_HISTORY_PREFIX) or str(entry.get("source_type") or "") != "main":
            continue
        previous = out.get(url)
        if previous is not None and previous != entry:
            raise live.AcquisitionError(f"FROZEN_HISTORY_INGESTION_DUPLICATE_URL:{url}")
        out[url] = entry
    return out


def _frozen_history_payload(url: str, entry: dict[str, Any], root: Path = ROOT) -> tuple[bytes, str, str]:
    if entry.get("download_status") != "downloaded" or entry.get("validated") is not True:
        raise live.AcquisitionError(f"FROZEN_HISTORY_SNAPSHOT_NOT_VALIDATED:{url}")
    raw_path_text = str(entry.get("raw_path") or "").strip()
    expected_sha = str(entry.get("raw_sha256") or "").strip().lower()
    if not raw_path_text or len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha):
        raise live.AcquisitionError(f"FROZEN_HISTORY_SNAPSHOT_PROVENANCE_INVALID:{url}")
    raw_path = Path(raw_path_text)
    if raw_path.is_absolute() or ".." in raw_path.parts:
        raise live.AcquisitionError(f"FROZEN_HISTORY_SNAPSHOT_PATH_INVALID:{url}")
    data_root = (root / "football-data").resolve()
    path = (data_root / raw_path).resolve()
    if path != data_root and data_root not in path.parents:
        raise live.AcquisitionError(f"FROZEN_HISTORY_SNAPSHOT_PATH_ESCAPE:{url}")
    if not path.is_file():
        raise live.AcquisitionError(f"FROZEN_HISTORY_SNAPSHOT_MISSING:{url}:{raw_path_text}")
    payload = path.read_bytes()
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != expected_sha:
        raise live.AcquisitionError(
            f"FROZEN_HISTORY_SNAPSHOT_SHA_MISMATCH:{url}:expected={expected_sha}:actual={actual_sha}"
        )
    return payload, actual_sha, raw_path.as_posix()


def install_frozen_history_source_chain(audit: dict[str, Any], root: Path = ROOT) -> object:
    original_fetch = live._fetch
    index = _history_manifest_index(root)
    audit["historical_source_chain_schema"] = HISTORY_SOURCE_SCHEMA
    audit["historical_frozen_snapshot_network_bypass"] = True
    audit["historical_frozen_snapshot_used"] = 0
    audit["historical_frozen_snapshot_result_fields_emitted"] = False

    def governed_fetch(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
                       timeout: int = 60):
        entry = index.get(url) if data is None and url.startswith(FOOTBALL_DATA_HISTORY_PREFIX) else None
        if entry is None:
            return original_fetch(url, data=data, headers=headers, timeout=timeout)
        try:
            payload, source_sha, raw_path = _frozen_history_payload(url, entry, root)
        except live.AcquisitionError as exc:
            attempt = {
                "source": "GOVERNED_FROZEN_INGESTION_SNAPSHOT",
                "url": url,
                "outcome": "FAIL",
                "error": str(exc),
            }
            audit.setdefault("source_attempts", []).append(attempt)
            if audit.get("first_authoritative_failure") is None:
                audit["first_authoritative_failure"] = {
                    "stage": "RETROSPECTIVE_EXACT_HISTORY_FROZEN_SOURCE",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            raise
        audit["historical_frozen_snapshot_used"] += 1
        audit.setdefault("source_attempts", []).append({
            "source": "GOVERNED_FROZEN_INGESTION_SNAPSHOT",
            "url": url,
            "outcome": "SUCCESS",
            "sha256": source_sha,
            "raw_path": raw_path,
            "network_attempted": False,
        })
        return payload, source_sha

    live._fetch = governed_fetch
    return original_fetch


def main() -> int:
    original_fixture_install = legacy.install_fixture_identity_fallback
    original_retry_install = legacy.install_retry

    def install_retry_with_frozen_history():
        original_raw_fetch, audit = original_retry_install()
        install_frozen_history_source_chain(audit)
        return original_raw_fetch, audit

    legacy.install_fixture_identity_fallback = install_common_fixture_identity_provider_chain
    legacy.install_retry = install_retry_with_frozen_history
    try:
        return int(legacy.main())
    finally:
        legacy.install_fixture_identity_fallback = original_fixture_install
        legacy.install_retry = original_retry_install


if __name__ == "__main__":
    raise SystemExit(main())
