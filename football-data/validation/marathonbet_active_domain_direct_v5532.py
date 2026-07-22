#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import marathonbet_active_domain_capture_v5532 as base

ROOT = base.ROOT
REGISTRY = base.REGISTRY
RAW_ROOT = base.RAW_ROOT
FORMAL_ROOT = base.FORMAL_ROOT
MANIFEST = base.MANIFEST
ALL_EVENTS_URL = "https://www.marathonbet.com/en/all-events.htm"
ORIGIN = "https://www.marathonbet.com"

EXACT_LABELS = {
    "USA_MLS": {"usa mls", "united states mls", "usa major league soccer", "major league soccer"},
    "BRA_SerieA": {"brazil serie a", "brazil brasileirao serie a", "brazil campeonato brasileiro serie a"},
    "ARG_Primera": {"argentina primera division", "argentina liga profesional", "argentina liga profesional de futbol"},
    "SWE_Allsvenskan": {"sweden allsvenskan"},
    "NOR_Eliteserien": {"norway eliteserien"},
    "KOR_KLeague1": {"south korea k league 1", "korea republic k league 1"},
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def strip_tags(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def discover_category_urls(raw: bytes) -> tuple[dict[str, dict[str, str]], list[dict[str, str]]]:
    text = raw.decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<a[^>]*class="[^"]*category-label-link[^"]*"[^>]*href="([^"]+)"[^>]*>\s*<h2[^>]*>(.*?)</h2>',
        re.I | re.S,
    )
    selected: dict[str, dict[str, str]] = {}
    candidates: list[dict[str, str]] = []
    for href, body in pattern.findall(text):
        label = strip_tags(body)
        token = base.norm(label)
        row = {"label": label, "normalized_label": token, "url": urljoin(ORIGIN, html.unescape(href))}
        for cid, allowed in EXACT_LABELS.items():
            if token in allowed:
                candidates.append({"competition_id": cid, **row})
                selected.setdefault(cid, row)
    return selected, candidates


def raw_path(cid: str, observed: str, digest: str) -> Path:
    token = observed.replace(":", "").replace("+00:00", "Z")
    return RAW_ROOT / f"{base.safe(cid)}__direct_category__{token}__{digest[:12]}.html"


def parse_page(lines: list[str], observed: str, cid: str, categories: list[str]) -> tuple[str | None, list[dict[str, Any]]]:
    section = base.find_section(lines, cid, categories)
    if section is None:
        category = None
        start, end = 0, len(lines)
    else:
        category, start, end = section
        start += 1
    fixtures = []
    i = start
    while i < end:
        fixture, next_i = base.parse_fixture(lines, i, observed)
        i = max(next_i, i + 1)
        if fixture is not None:
            fixtures.append(fixture)
    return category, fixtures


def build_snapshot(
    *,
    cid: str,
    season: str,
    fixture: dict[str, Any],
    home: str,
    away: str,
    observed: str,
    final_url: str,
    page_path: Path,
    page_sha: str,
    registry_sha: str,
    matched_category: str,
) -> dict[str, Any]:
    identity = fixture["identity_resolution"]
    snapshot: dict[str, Any] = {
        "competition_id": cid,
        "season": season,
        "home_team": home,
        "away_team": away,
        "kickoff_utc": fixture["kickoff_utc"],
        "settlement_scope": "90m_including_stoppage",
        "freeze_utc": observed,
        "accessed_at_utc": observed,
        "source_observed_at_utc": observed,
        "surface_observed_at_utc": {"one_x_two": observed, "asian_handicap": observed, "over_under": observed},
        "source_url": final_url,
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "one_x_two": fixture["one_x_two"],
        "asian_handicap": {key: fixture["asian_handicap"][key] for key in ("line", "home", "away")},
        "over_under": {key: fixture["over_under"][key] for key in ("line", "over", "under")},
        "source_adapter": {
            "schema_version": "V5.5.32-marathonbet-active-domain-direct-r1",
            "parent_raw_html_path": str(page_path.relative_to(ROOT)),
            "parent_raw_html_sha256": page_sha,
            "source_display_names": {"home": fixture["source_home"], "away": fixture["source_away"]},
            "identity_resolution": identity,
            "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
            "identity_registry_sha256": registry_sha,
            "category_discovery_source_url": ALL_EVENTS_URL,
            "direct_category_url": final_url,
            "html_timezone": "Europe/London",
            "displayed_kickoff": fixture["displayed_time"],
            "displayed_kickoff_converted_utc": fixture["kickoff_utc"],
            "matched_category": matched_category,
            "handicap_away_line_audit": fixture["asian_handicap"]["away_line"],
        },
        "observation_semantics": {
            "retrospective_backfill": False,
            "source_observed_at_utc": "fresh direct first-party league-page observation",
            "surface_observed_at_utc": "same direct league-page HTML response for 1X2/AH/OU",
        },
        "promotion_semantics": {
            "single_provider_pit_evidence": True,
            "independent_provider_consensus": False,
            "promotion_sample_eligible": False,
        },
    }
    snapshot["raw_snapshot_sha256"] = base.canonical_sha256(snapshot)
    return snapshot


def main() -> int:
    _, registry_sha, alias_maps = base.load_registry()
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.32-marathonbet-active-domain-direct-status-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_ACTIVE_DOMAIN_SNAPSHOTS",
        "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
        "identity_registry_sha256": registry_sha,
        "target_competition_count": len(base.TARGETS),
        "leagues": [],
        "formal_snapshot_count_written": 0,
        "formal_snapshot_count_available": 0,
        "complete_surface_fixture_count": 0,
        "unresolved_identity_count": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "promotion_sample_count_change": 0,
    }

    try:
        index_raw, index_url, index_status, index_headers, index_observed = base.fetch(ALL_EVENTS_URL)
        selected, candidates = discover_category_urls(index_raw)
        receipt["category_discovery"] = {
            "observed_at_utc": index_observed,
            "source_url": index_url,
            "http_status": index_status,
            "response_headers": index_headers,
            "raw_html_sha256": hashlib.sha256(index_raw).hexdigest(),
            "matched_candidates": candidates,
            "selected_competition_count": len(selected),
        }
    except Exception as exc:
        receipt["status"] = "CATEGORY_DISCOVERY_FAIL_CLOSED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 2

    for cid, cfg in base.TARGETS.items():
        aliases = alias_maps.get(cid) or {}
        chosen = selected.get(cid)
        league: dict[str, Any] = {
            "competition_id": cid,
            "season": cfg["season"],
            "status": "DIRECT_CATEGORY_URL_NOT_DISCOVERED",
            "fixtures": [],
            "discovered_category": chosen,
        }
        if not aliases:
            league["status"] = "IDENTITY_DOMAIN_UNAVAILABLE_FAIL_CLOSED"
            receipt["leagues"].append(league)
            continue
        if chosen is None:
            receipt["leagues"].append(league)
            continue

        try:
            raw, final_url, http_status, headers, observed = base.fetch(chosen["url"])
            digest = hashlib.sha256(raw).hexdigest()
            page_path = raw_path(cid, observed, digest)
            page_path.parent.mkdir(parents=True, exist_ok=True)
            if page_path.exists():
                if hashlib.sha256(page_path.read_bytes()).hexdigest() != digest:
                    raise FileExistsError(f"immutable direct Marathonbet page collision: {page_path}")
            else:
                page_path.write_bytes(raw)
            lines = base.clean_lines(raw)
            matched, fixtures = parse_page(lines, observed, cid, cfg["categories"])
            league["direct_page"] = {
                "observed_at_utc": observed,
                "final_url": final_url,
                "http_status": http_status,
                "response_headers": headers,
                "raw_html_sha256": digest,
                "raw_html_path": str(page_path.relative_to(ROOT)),
                "extracted_line_count": len(lines),
                "matched_category": matched or chosen["label"],
            }
        except Exception as exc:
            league["status"] = "DIRECT_CATEGORY_FETCH_FAIL_CLOSED"
            league["error"] = f"{type(exc).__name__}: {exc}"
            receipt["leagues"].append(league)
            continue

        parsed = written = available = 0
        for fixture in fixtures:
            if fixture.get("parse_status") != "COMPLETE_MAIN_SURFACES":
                league["fixtures"].append(fixture)
                continue
            parsed += 1
            receipt["complete_surface_fixture_count"] += 1
            home = aliases.get(base.norm(fixture["source_home"]))
            away = aliases.get(base.norm(fixture["source_away"]))
            fixture["identity_resolution"] = {
                "home": {"canonical": home, "source": "CURRENT_SEASON_EXACT_ALIAS" if home else "UNRESOLVED"},
                "away": {"canonical": away, "source": "CURRENT_SEASON_EXACT_ALIAS" if away else "UNRESOLVED"},
                "fuzzy_matching_used": False,
                "registry_sha256": registry_sha,
            }
            if home is None or away is None:
                fixture["formal_status"] = "IDENTITY_UNRESOLVED_FAIL_CLOSED"
                receipt["unresolved_identity_count"] += 1
                league["fixtures"].append(fixture)
                continue

            snapshot = build_snapshot(
                cid=cid,
                season=cfg["season"],
                fixture=fixture,
                home=home,
                away=away,
                observed=observed,
                final_url=final_url,
                page_path=page_path,
                page_sha=digest,
                registry_sha=registry_sha,
                matched_category=matched or chosen["label"],
            )
            validation = base.validate(snapshot)
            if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                fixture["formal_status"] = "V523_FAIL_CLOSED"
                fixture["v523_errors"] = validation.get("errors")
                league["fixtures"].append(fixture)
                continue
            out = base.formal_path(snapshot)
            if out.exists():
                existing = json.loads(out.read_text(encoding="utf-8"))
                if existing.get("raw_snapshot_sha256") != snapshot.get("raw_snapshot_sha256"):
                    raise FileExistsError(f"immutable Marathonbet PIT collision: {out}")
                fixture["formal_status"] = "ALREADY_PRESENT_IDENTICAL"
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                fixture["formal_status"] = "VALID_PIT_SNAPSHOT_WRITTEN"
                written += 1
                receipt["formal_snapshot_count_written"] += 1
            available += 1
            receipt["formal_snapshot_count_available"] += 1
            fixture["formal_snapshot_path"] = str(out.relative_to(ROOT))
            league["fixtures"].append(fixture)

        league.update({
            "status": "PASS_ACTIVE_DOMAIN_DIRECT_CAPTURE",
            "complete_surface_fixture_count": parsed,
            "formal_snapshot_count_written": written,
            "formal_snapshot_count_available": available,
        })
        receipt["leagues"].append(league)

    if receipt["formal_snapshot_count_available"]:
        receipt["status"] = "PASS_ACTIVE_DOMAIN_MARATHONBET_PIT"
    receipt["policy"] = (
        "The all-events page is used only to discover exact first-party direct league URLs. Each market snapshot is parsed from one immutable direct league-page response. "
        "Only exact current-season identities or hash-bound provider aliases are accepted. No fuzzy team matching, historical fallback, synthetic price or cross-page market splicing is allowed."
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "discovered_competition_count": len(selected),
        "formal_snapshot_count_available": receipt["formal_snapshot_count_available"],
        "unresolved_identity_count": receipt["unresolved_identity_count"],
        "league_statuses": {row["competition_id"]: row["status"] for row in receipt["leagues"]},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
