#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from direct_marathonbet_html_probe_v5516 import extract_text, fetch
from prospective_market_snapshot_v523 import canonical_sha256, validate

REGISTRY = ROOT / "config" / "active_domain_identity_registry_v5532.json"
RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "active_domains"
FORMAL_ROOT = ROOT / "evidence" / "markets_prospective"
MANIFEST = ROOT / "manifests" / "marathonbet_active_domain_capture_v5532_status.json"
BROAD_URL = "https://www.marathonbet.com/en/betting/Football"
LONDON = ZoneInfo("Europe/London")

TARGETS = {
    "USA_MLS": {
        "season": "2026",
        "categories": ["USA. MLS", "United States. MLS", "USA. Major League Soccer", "Major League Soccer"],
    },
    "BRA_SerieA": {
        "season": "2026",
        "categories": ["Brazil. Serie A", "Brazil. Brasileirao Serie A", "Brazil. Campeonato Brasileiro Serie A"],
    },
    "ARG_Primera": {
        "season": "2026",
        "categories": ["Argentina. Primera Division", "Argentina. Liga Profesional", "Argentina. Liga Profesional de Futbol"],
    },
    "SWE_Allsvenskan": {
        "season": "2026",
        "categories": ["Sweden. Allsvenskan"],
    },
    "NOR_Eliteserien": {
        "season": "2026",
        "categories": ["Norway. Eliteserien"],
    },
    "KOR_KLeague1": {
        "season": "2026",
        "categories": ["South Korea. K League 1", "South Korea. K-League 1", "Korea Republic. K League 1"],
    },
}


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def safe(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def price(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 1.0:
        raise ValueError(f"invalid decimal price: {value}")
    return result


def qline(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or abs(result * 4.0 - round(result * 4.0)) > 1e-9:
        raise ValueError(f"invalid quarter line: {value}")
    return result


def displayed_to_utc(token: str, observed_utc: str) -> str:
    probe = datetime.strptime(token, "%d %b %H:%M")
    observed = datetime.fromisoformat(observed_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    candidates = []
    for year in (observed.year - 1, observed.year, observed.year + 1):
        local = probe.replace(year=year, tzinfo=LONDON)
        utc = local.astimezone(timezone.utc)
        candidates.append(utc)
    future = [value for value in candidates if value >= observed.replace(microsecond=0)]
    selected = min(future, key=lambda value: value - observed) if future else min(candidates, key=lambda value: abs(value - observed))
    if selected - observed > __import__("datetime").timedelta(days=370):
        raise ValueError(f"displayed kickoff too far from observation: {token}")
    return selected.replace(microsecond=0).isoformat()


def load_registry() -> tuple[dict[str, Any], str, dict[str, dict[str, str]]]:
    raw = REGISTRY.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema_version") != "V5.5.32-active-domain-observed-identity-r1":
        raise ValueError("unexpected active-domain identity schema")
    maps: dict[str, dict[str, str]] = {}
    for cid, cfg in TARGETS.items():
        comp = (data.get("competitions") or {}).get(cid) or {}
        aliases: dict[str, str] = {}
        if str(comp.get("status") or "").startswith("PASS_"):
            for team in comp.get("teams") or []:
                canonical = str(team.get("canonical_name") or "").strip()
                if not canonical:
                    continue
                for value in [canonical, *(team.get("observed_variants") or [])]:
                    token = norm(value)
                    previous = aliases.get(token)
                    if previous is not None and previous != canonical:
                        raise ValueError(f"identity collision {cid}:{value}:{previous}/{canonical}")
                    aliases[token] = canonical
        maps[cid] = aliases
    return data, hashlib.sha256(raw).hexdigest(), maps


def clean_lines(raw: bytes) -> list[str]:
    return [line.strip() for line in extract_text(raw).splitlines() if line.strip()]


def find_section(lines: list[str], categories: list[str]) -> tuple[str, int, int] | None:
    category_set = set(categories)
    start = next((i for i, line in enumerate(lines) if line in category_set), None)
    if start is None:
        return None
    category = lines[start]
    end = len(lines)
    for j in range(start + 1, len(lines) - 1):
        if lines[j + 1] == "All Events Back" and lines[j] != category:
            end = j
            break
    return category, start, end


def parse_fixture(lines: list[str], index: int, observed: str) -> tuple[dict[str, Any] | None, int]:
    if " — " not in lines[index] or index + 1 >= len(lines):
        return None, index + 1
    dt_match = re.fullmatch(r"(\d{1,2} [A-Za-z]{3}) (\d{1,2}:\d{2})", lines[index + 1])
    if not dt_match:
        return None, index + 1
    home, away = [part.strip() for part in lines[index].split(" — ", 1)]
    end = min(len(lines), index + 28)
    for j in range(index + 2, min(len(lines) - 1, index + 40)):
        if " — " in lines[j] and re.fullmatch(r"\d{1,2} [A-Za-z]{3} \d{1,2}:\d{2}", lines[j + 1]):
            end = j
            break
    chunk = "\n".join(lines[index:end])
    h, a = re.escape(home), re.escape(away)
    one = re.search(rf"{h} to Win ([0-9.]+)\s+Draw ([0-9.]+)\s+{a} to Win ([0-9.]+)", chunk, re.I)
    ah = re.search(rf"{h} \(([+-]?[0-9.]+|0)\) ([0-9.]+)\s+{a} \(([+-]?[0-9.]+|0)\) ([0-9.]+)", chunk, re.I)
    ou = re.search(r"Under ([0-9.]+) ([0-9.]+)\s+Over ([0-9.]+) ([0-9.]+)", chunk, re.I)
    displayed = f"{dt_match.group(1)} {dt_match.group(2)}"
    base = {"source_home": home, "source_away": away, "displayed_time": displayed}
    if not one or not ah or not ou:
        return {**base, "parse_status": "INCOMPLETE_MAIN_SURFACES", "raw_block": chunk}, end
    home_line, away_line = qline(ah.group(1)), qline(ah.group(3))
    under_line, over_line = qline(ou.group(1)), qline(ou.group(3))
    if abs(home_line + away_line) > 1e-9 or abs(under_line - over_line) > 1e-9:
        return {**base, "parse_status": "LINE_SYMMETRY_FAIL", "raw_block": chunk}, end
    return {
        **base,
        "kickoff_utc": displayed_to_utc(displayed, observed),
        "one_x_two": {"home": price(one.group(1)), "draw": price(one.group(2)), "away": price(one.group(3))},
        "asian_handicap": {"line": home_line, "home": price(ah.group(2)), "away": price(ah.group(4)), "away_line": away_line},
        "over_under": {"line": under_line, "under": price(ou.group(2)), "over": price(ou.group(4))},
        "parse_status": "COMPLETE_MAIN_SURFACES",
    }, end


def formal_path(snapshot: dict[str, Any]) -> Path:
    token = snapshot["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return FORMAL_ROOT / f"{safe(snapshot['competition_id'])}__{safe(snapshot['home_team'])}__{safe(snapshot['away_team'])}__marathonbet__{token}.json"


def main() -> int:
    registry, registry_sha, alias_maps = load_registry()
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.32-marathonbet-active-domain-capture-r1",
        "generated_at_utc": now_utc(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_ACTIVE_DOMAIN_SNAPSHOTS",
        "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
        "identity_registry_sha256": registry_sha,
        "target_competition_count": len(TARGETS),
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
        raw, final_url, http_status, headers, observed = fetch(BROAD_URL)
        digest = hashlib.sha256(raw).hexdigest()
        token = observed.replace(":", "").replace("+00:00", "Z")
        raw_path = RAW_ROOT / f"all_active_domains__{token}__{digest[:12]}.html"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            raw_path.write_bytes(raw)
        lines = clean_lines(raw)
        receipt["broad_page"] = {
            "observed_at_utc": observed,
            "final_url": final_url,
            "http_status": http_status,
            "response_headers": headers,
            "raw_html_sha256": digest,
            "raw_html_path": str(raw_path.relative_to(ROOT)),
            "extracted_line_count": len(lines),
        }
    except Exception as exc:
        receipt["status"] = "BROAD_PAGE_FAIL_CLOSED"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 2

    for cid, cfg in TARGETS.items():
        aliases = alias_maps.get(cid) or {}
        league: dict[str, Any] = {
            "competition_id": cid,
            "season": cfg["season"],
            "category_candidates": cfg["categories"],
            "status": "CATEGORY_NOT_AVAILABLE",
            "fixtures": [],
        }
        if not aliases:
            league["status"] = "IDENTITY_DOMAIN_UNAVAILABLE_FAIL_CLOSED"
            receipt["leagues"].append(league)
            continue
        section = find_section(lines, cfg["categories"])
        if section is None:
            receipt["leagues"].append(league)
            continue
        category, start, end = section
        league["matched_category"] = category
        parsed = 0
        written = 0
        available = 0
        i = start + 1
        while i < end:
            fixture, next_i = parse_fixture(lines, i, observed)
            i = max(next_i, i + 1)
            if fixture is None:
                continue
            if fixture.get("parse_status") != "COMPLETE_MAIN_SURFACES":
                league["fixtures"].append(fixture)
                continue
            parsed += 1
            receipt["complete_surface_fixture_count"] += 1
            home = aliases.get(norm(fixture["source_home"]))
            away = aliases.get(norm(fixture["source_away"]))
            fixture["identity_resolution"] = {
                "home": {"canonical": home, "source": "CURRENT_SEASON_OBSERVED_EXACT" if home else "UNRESOLVED"},
                "away": {"canonical": away, "source": "CURRENT_SEASON_OBSERVED_EXACT" if away else "UNRESOLVED"},
                "fuzzy_matching_used": False,
                "registry_sha256": registry_sha,
            }
            if home is None or away is None:
                fixture["formal_status"] = "IDENTITY_UNRESOLVED_FAIL_CLOSED"
                receipt["unresolved_identity_count"] += 1
                league["fixtures"].append(fixture)
                continue
            snapshot: dict[str, Any] = {
                "competition_id": cid,
                "season": cfg["season"],
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
                "asian_handicap": {k: fixture["asian_handicap"][k] for k in ("line", "home", "away")},
                "over_under": {k: fixture["over_under"][k] for k in ("line", "over", "under")},
                "source_adapter": {
                    "schema_version": "V5.5.32-marathonbet-active-domain-capture-r1",
                    "parent_raw_html_path": str(raw_path.relative_to(ROOT)),
                    "parent_raw_html_sha256": digest,
                    "source_display_names": {"home": fixture["source_home"], "away": fixture["source_away"]},
                    "identity_resolution": fixture["identity_resolution"],
                    "identity_registry_path": str(REGISTRY.relative_to(ROOT)),
                    "identity_registry_sha256": registry_sha,
                    "html_timezone": "Europe/London",
                    "displayed_kickoff": fixture["displayed_time"],
                    "displayed_kickoff_converted_utc": fixture["kickoff_utc"],
                    "matched_category": category,
                    "handicap_away_line_audit": fixture["asian_handicap"]["away_line"],
                },
                "observation_semantics": {
                    "retrospective_backfill": False,
                    "source_observed_at_utc": "fresh direct first-party broad-page observation",
                    "surface_observed_at_utc": "same HTML response for 1X2/AH/OU",
                },
                "promotion_semantics": {
                    "single_provider_pit_evidence": True,
                    "independent_provider_consensus": False,
                    "promotion_sample_eligible": False,
                },
            }
            snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
            v = validate(snapshot)
            if not v.get("passed") or not v.get("formal_pit_eligible"):
                fixture["formal_status"] = "V523_FAIL_CLOSED"
                fixture["v523_errors"] = v.get("errors")
                league["fixtures"].append(fixture)
                continue
            out = formal_path(snapshot)
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
            "status": "PASS_ACTIVE_DOMAIN_SECTION_CAPTURED",
            "complete_surface_fixture_count": parsed,
            "formal_snapshot_count_written": written,
            "formal_snapshot_count_available": available,
        })
        receipt["leagues"].append(league)

    if receipt["formal_snapshot_count_available"]:
        receipt["status"] = "PASS_ACTIVE_DOMAIN_MARATHONBET_PIT"
    receipt["policy"] = (
        "Only exact identities observed in frozen current-season registered competition data are accepted. "
        "Fuzzy matching and historical fallback are prohibited. Every market surface must come from one fresh direct Marathonbet HTML response; "
        "single-provider evidence cannot change weights, probabilities or promotion samples."
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "formal_snapshot_count_available": receipt["formal_snapshot_count_available"],
        "unresolved_identity_count": receipt["unresolved_identity_count"],
        "league_statuses": {row["competition_id"]: row["status"] for row in receipt["leagues"]},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
