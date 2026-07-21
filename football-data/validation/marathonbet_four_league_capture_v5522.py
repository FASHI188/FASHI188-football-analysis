#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from direct_marathonbet_html_probe_v5516 import extract_text, fetch
from prospective_market_snapshot_v523 import canonical_sha256, validate

RAW_ROOT = ROOT / "evidence" / "direct_provider_probes" / "marathonbet" / "league_pages"
FORMAL_ROOT = ROOT / "evidence" / "markets_prospective"
MANIFEST = ROOT / "manifests" / "marathonbet_four_league_capture_v5522_status.json"
LONDON = ZoneInfo("Europe/London")

LEAGUES = [
    {
        "competition_id": "POR_PrimeiraLiga",
        "season": "2026/27",
        "category": "Portugal. Primeira Liga",
        "url": "https://www.marathonbet.com/en/betting/Football/Portugal/Primeira%2BLiga%2B-%2B43058",
    },
    {
        "competition_id": "ESP_LaLiga",
        "season": "2026/27",
        "category": "Spain. La Liga",
        "url": "https://www.marathonbet.com/en/betting/Football/Spain%2B-%2B8727",
    },
    {
        "competition_id": "FRA_Ligue1",
        "season": "2026/27",
        "category": "France. Ligue 1",
        "url": "https://www.marathonbet.com/en/betting/Football/France%2B-%2B21532",
    },
    {
        "competition_id": "GER_Bundesliga",
        "season": "2026/27",
        "category": "Germany. Bundesliga",
        "url": "https://www.marathonbet.com/en/betting/Football/Germany/Bundesliga%2B-%2B22436",
    },
]

# Preserve the project's already-frozen canonical names for the four bootstrap identities.
IDENTITY_OVERRIDES = {
    ("ESP_LaLiga", "alaves"): "Deportivo Alavés",
    ("ESP_LaLiga", "getafe"): "Getafe CF",
    ("GER_Bundesliga", "bayern munich"): "FC Bayern München",
    ("GER_Bundesliga", "stuttgart"): "VfB Stuttgart",
    ("FRA_Ligue1", "marseille"): "Olympique de Marseille",
    ("FRA_Ligue1", "strasbourg"): "RC Strasbourg Alsace",
    ("POR_PrimeiraLiga", "estoril praia"): "Estoril Praia",
    ("POR_PrimeiraLiga", "estoril"): "Estoril Praia",
    ("POR_PrimeiraLiga", "fc famalicao"): "FC Famalicão",
    ("POR_PrimeiraLiga", "famalicao"): "FC Famalicão",
}


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"\b(fc|cf|vfb|rc|deportivo|olympique)\b", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def safe(value: str) -> str:
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


def season_year(month: int) -> int:
    return 2026 if month >= 7 else 2027


def displayed_to_utc(token: str) -> str:
    probe = datetime.strptime(f"2000 {token}", "%Y %d %b %H:%M")
    local = probe.replace(year=season_year(probe.month), tzinfo=LONDON)
    return local.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def load_team_names(cid: str) -> list[str]:
    path = ROOT / "team_strengths" / cid / "latest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(row.get("team_name")) for row in data.get("teams", []) if isinstance(row, dict) and row.get("team_name")]


def resolve_team(cid: str, source_name: str, candidates: list[str]) -> tuple[str | None, float, str]:
    override = IDENTITY_OVERRIDES.get((cid, norm(source_name)))
    if override:
        return override, 1.0, "OVERRIDE"
    token = norm(source_name)
    best_name = None
    best_score = -1.0
    for candidate in candidates:
        ct = norm(candidate)
        if not token or not ct:
            continue
        score = 1.0 if token == ct or token in ct or ct in token else SequenceMatcher(None, token, ct).ratio()
        if score > best_score:
            best_score = score
            best_name = candidate
    if best_name is None or best_score < 0.78:
        return None, max(best_score, 0.0), "UNRESOLVED"
    return best_name, best_score, "TEAM_STRENGTH_REGISTRY"


def clean_lines(raw: bytes) -> list[str]:
    text = extract_text(raw)
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_section(lines: list[str], category: str) -> tuple[int, int] | None:
    try:
        start = next(i for i, line in enumerate(lines) if line == category)
    except StopIteration:
        return None
    # Category headings are followed by "All Events Back". Stop at the next heading that has the same marker.
    end = len(lines)
    for j in range(start + 1, len(lines) - 1):
        if lines[j + 1] == "All Events Back" and lines[j] != category:
            end = j
            break
    return start, end


def parse_fixture_block(lines: list[str], index: int) -> tuple[dict[str, Any] | None, int]:
    if " — " not in lines[index] or index + 1 >= len(lines):
        return None, index + 1
    dt_match = re.fullmatch(r"(\d{1,2} [A-Za-z]{3}) (\d{1,2}:\d{2})", lines[index + 1])
    if not dt_match:
        return None, index + 1
    home, away = [part.strip() for part in lines[index].split(" — ", 1)]
    # Limit parsing to this fixture. Next fixture starts with " — " and a date-time on the next line.
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
    if not one or not ah or not ou:
        return {
            "source_home": home,
            "source_away": away,
            "displayed_time": f"{dt_match.group(1)} {dt_match.group(2)}",
            "parse_status": "INCOMPLETE_MAIN_SURFACES",
            "raw_block": chunk,
        }, end
    home_line, away_line = qline(ah.group(1)), qline(ah.group(3))
    under_line, over_line = qline(ou.group(1)), qline(ou.group(3))
    if abs(home_line + away_line) > 1e-9 or abs(under_line - over_line) > 1e-9:
        return {
            "source_home": home,
            "source_away": away,
            "displayed_time": f"{dt_match.group(1)} {dt_match.group(2)}",
            "parse_status": "LINE_SYMMETRY_FAIL",
            "raw_block": chunk,
        }, end
    return {
        "source_home": home,
        "source_away": away,
        "displayed_time": f"{dt_match.group(1)} {dt_match.group(2)}",
        "kickoff_utc": displayed_to_utc(f"{dt_match.group(1)} {dt_match.group(2)}"),
        "one_x_two": {"home": price(one.group(1)), "draw": price(one.group(2)), "away": price(one.group(3))},
        "asian_handicap": {"line": home_line, "home": price(ah.group(2)), "away": price(ah.group(4)), "away_line": away_line},
        "over_under": {"line": under_line, "under": price(ou.group(2)), "over": price(ou.group(4))},
        "parse_status": "COMPLETE_MAIN_SURFACES",
    }, end


def snapshot_path(row: dict[str, Any]) -> Path:
    token = row["freeze_utc"].replace(":", "").replace("+00:00", "Z")
    return FORMAL_ROOT / f"{safe(row['competition_id'])}__{safe(row['home_team'])}__{safe(row['away_team'])}__marathonbet__{token}.json"


def main() -> int:
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.22-marathonbet-four-league-capture-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "provider_name": "Marathonbet",
        "provider_group": "marathonbet",
        "status": "NO_FORMAL_SNAPSHOTS",
        "leagues": [],
        "formal_snapshot_count_written": 0,
        "complete_surface_fixture_count": 0,
        "unresolved_identity_count": 0,
        "promotion_sample_count_change": 0,
        "formal_weight_change": False,
        "probability_change": False,
    }
    for cfg in LEAGUES:
        cid = cfg["competition_id"]
        league_row: dict[str, Any] = {"competition_id": cid, "category": cfg["category"], "status": "CATEGORY_NOT_AVAILABLE", "fixtures": []}
        try:
            raw, final_url, http_status, headers, observed = fetch(cfg["url"])
            digest = hashlib.sha256(raw).hexdigest()
            token = observed.replace(":", "").replace("+00:00", "Z")
            html_path = RAW_ROOT / f"{safe(cid)}__{token}__{digest[:12]}.html"
            if not html_path.exists():
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_bytes(raw)
            lines = clean_lines(raw)
            section = find_section(lines, cfg["category"])
            league_row.update({"observed_at_utc": observed, "final_url": final_url, "http_status": http_status, "raw_html_sha256": digest, "raw_html_path": str(html_path.relative_to(ROOT))})
            if section is None:
                receipt["leagues"].append(league_row)
                continue
            team_names = load_team_names(cid)
            start, end = section
            i = start + 1
            parsed_count = 0
            written_count = 0
            while i < end:
                fixture, next_i = parse_fixture_block(lines, i)
                i = max(next_i, i + 1)
                if fixture is None:
                    continue
                if fixture.get("parse_status") != "COMPLETE_MAIN_SURFACES":
                    league_row["fixtures"].append(fixture)
                    continue
                parsed_count += 1
                receipt["complete_surface_fixture_count"] += 1
                home, hs, hsource = resolve_team(cid, fixture["source_home"], team_names)
                away, aws, asource = resolve_team(cid, fixture["source_away"], team_names)
                fixture["identity_resolution"] = {"home": {"canonical": home, "score": round(hs, 4), "source": hsource}, "away": {"canonical": away, "score": round(aws, 4), "source": asource}}
                if home is None or away is None:
                    fixture["formal_status"] = "IDENTITY_UNRESOLVED_FAIL_CLOSED"
                    receipt["unresolved_identity_count"] += 1
                    league_row["fixtures"].append(fixture)
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
                        "schema_version": "V5.5.22-marathonbet-four-league-capture-r1",
                        "parent_raw_html_path": str(html_path.relative_to(ROOT)),
                        "parent_raw_html_sha256": digest,
                        "source_display_names": {"home": fixture["source_home"], "away": fixture["source_away"]},
                        "identity_resolution": fixture["identity_resolution"],
                        "html_timezone": "Europe/London",
                        "displayed_kickoff": fixture["displayed_time"],
                        "displayed_kickoff_converted_utc": fixture["kickoff_utc"],
                        "category": cfg["category"],
                        "handicap_away_line_audit": fixture["asian_handicap"]["away_line"],
                    },
                    "observation_semantics": {"retrospective_backfill": False, "source_observed_at_utc": "fresh direct first-party league-page observation", "surface_observed_at_utc": "same HTML response for 1X2/AH/OU"},
                    "promotion_semantics": {"single_provider_pit_evidence": True, "independent_provider_consensus": False, "promotion_sample_eligible": False},
                }
                snapshot["raw_snapshot_sha256"] = canonical_sha256(snapshot)
                validation = validate(snapshot)
                if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                    fixture["formal_status"] = "V523_FAIL_CLOSED"
                    fixture["v523_errors"] = validation.get("errors")
                    league_row["fixtures"].append(fixture)
                    continue
                out = snapshot_path(snapshot)
                if not out.exists():
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                    receipt["formal_snapshot_count_written"] += 1
                    written_count += 1
                    fixture["formal_status"] = "VALID_PIT_SNAPSHOT_WRITTEN"
                else:
                    fixture["formal_status"] = "ALREADY_PRESENT"
                fixture["formal_snapshot_path"] = str(out.relative_to(ROOT))
                league_row["fixtures"].append(fixture)
            league_row["status"] = "PASS_LEAGUE_SECTION_CAPTURED"
            league_row["complete_surface_fixture_count"] = parsed_count
            league_row["formal_snapshot_count_written"] = written_count
        except Exception as exc:
            league_row["status"] = "LEAGUE_CAPTURE_FAIL_CLOSED"
            league_row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["leagues"].append(league_row)
    if receipt["formal_snapshot_count_written"]:
        receipt["status"] = "PASS_FOUR_LEAGUE_SINGLE_PROVIDER_PIT_EXPANSION"
    receipt["policy"] = "League-wide Marathonbet expansion remains single-provider evidence only. Identity must resolve through frozen project team-strength names or explicit overrides; unresolved clubs fail closed. No promotion sample or probability/weight change occurs without independent synchronized provider consensus."
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
