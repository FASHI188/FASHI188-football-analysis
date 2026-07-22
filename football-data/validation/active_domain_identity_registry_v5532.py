#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "active_domain_identity_registry_v5532.json"

TARGETS = {
    "USA_MLS": "2026",
    "BRA_SerieA": "2026",
    "ARG_Primera": "2026",
    "SWE_Allsvenskan": "2026",
    "NOR_Eliteserien": "2026",
    "KOR_KLeague1": "2026",
}

HOME_KEYS = (
    "home_team", "hometeam", "home", "home_name", "homename",
    "team_home", "local_team", "localteam", "host", "home_club",
)
AWAY_KEYS = (
    "away_team", "awayteam", "away", "away_name", "awayname",
    "team_away", "visitor_team", "visitorteam", "guest", "away_club",
)
SEASON_KEYS = ("season", "campaign", "competition_season", "season_id", "year")
DATE_KEYS = ("date", "match_date", "kickoff", "kickoff_utc", "datetime", "utc_date", "start")


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def canonical_key_map(row: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in row:
        token = norm(key).replace(" ", "_")
        if token and token not in result:
            result[token] = key
    return result


def first_key(mapping: dict[str, str], candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in mapping:
            return mapping[candidate]
    return None


def target_row(row: dict[str, Any], *, season: str, source_path: Path) -> bool:
    keys = canonical_key_map(row)
    season_key = first_key(keys, SEASON_KEYS)
    if season_key is not None and str(row.get(season_key) or "").strip():
        value = str(row.get(season_key) or "").strip()
        return value == season or value.startswith(season + "/") or value.startswith(season + "-")

    for candidate in DATE_KEYS:
        actual = keys.get(candidate)
        if actual is None:
            continue
        value = str(row.get(actual) or "")
        match = re.search(r"(?:^|\D)(20\d{2})(?:\D|$)", value)
        if match:
            return match.group(1) == season

    return season in source_path.as_posix()


def teams_from_rows(rows: Iterable[dict[str, Any]], *, season: str, source_path: Path) -> tuple[Counter[str], int]:
    teams: Counter[str] = Counter()
    accepted_rows = 0
    for row in rows:
        if not isinstance(row, dict) or not target_row(row, season=season, source_path=source_path):
            continue
        keys = canonical_key_map(row)
        home_key = first_key(keys, HOME_KEYS)
        away_key = first_key(keys, AWAY_KEYS)
        if home_key is None or away_key is None:
            continue
        home = str(row.get(home_key) or "").strip()
        away = str(row.get(away_key) or "").strip()
        if not home or not away or norm(home) == norm(away):
            continue
        teams[home] += 1
        teams[away] += 1
        accepted_rows += 1
    return teams, accepted_rows


def json_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("matches", "fixtures", "rows", "data", "results", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    values = list(payload.values())
    if values and all(isinstance(row, dict) for row in values):
        return [row for row in values if isinstance(row, dict)]
    return [payload]


def read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".json":
        return json_rows(json.loads(path.read_text(encoding="utf-8")))
    if suffix in {".jsonl", ".ndjson"}:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return rows
    return []


def candidate_roots(cid: str) -> list[Path]:
    direct = [
        ROOT / "processed" / cid,
        ROOT / "data" / "processed" / cid,
        ROOT / "processed_data" / cid,
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for path in direct:
        if path.is_dir() and path not in seen:
            result.append(path)
            seen.add(path)
    if not result:
        for path in ROOT.rglob(cid):
            if path.is_dir() and "processed" in path.as_posix().lower() and path not in seen:
                result.append(path)
                seen.add(path)
    return sorted(result)


def source_files(cid: str) -> list[Path]:
    result: list[Path] = []
    for base in candidate_roots(cid):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".csv", ".json", ".jsonl", ".ndjson"}:
                result.append(path)
    return sorted(set(result))


def build_competition(cid: str, season: str) -> dict[str, Any]:
    variants_by_norm: dict[str, Counter[str]] = defaultdict(Counter)
    files = []
    accepted_rows = 0
    read_errors = []

    for path in source_files(cid):
        try:
            raw = path.read_bytes()
            rows = read_rows(path)
            teams, accepted = teams_from_rows(rows, season=season, source_path=path)
            if not accepted:
                continue
            accepted_rows += accepted
            for value, count in teams.items():
                token = norm(value)
                if token:
                    variants_by_norm[token][value] += count
            files.append({
                "path": str(path.relative_to(ROOT)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "accepted_current_season_rows": accepted,
            })
        except Exception as exc:
            read_errors.append({
                "path": str(path.relative_to(ROOT)),
                "error": f"{type(exc).__name__}: {exc}",
            })

    teams = []
    for token in sorted(variants_by_norm):
        counts = variants_by_norm[token]
        canonical = sorted(counts, key=lambda value: (-counts[value], len(value), value))[0]
        teams.append({
            "canonical_name": canonical,
            "normalized_identity": token,
            "observed_variants": sorted(counts),
            "observation_count": sum(counts.values()),
        })

    team_count = len(teams)
    status = "PASS_CURRENT_SEASON_OBSERVED_IDENTITY" if team_count >= 8 and accepted_rows >= 4 else "FAIL_CLOSED_INSUFFICIENT_CURRENT_SEASON_IDENTITY"
    return {
        "competition_id": cid,
        "season": season,
        "status": status,
        "team_count": team_count,
        "accepted_current_season_match_rows": accepted_rows,
        "teams": teams,
        "source_files": files,
        "read_errors": read_errors,
        "historical_season_rows_accepted": False,
        "fuzzy_matching_authorized": False,
    }


def main() -> int:
    competitions = {cid: build_competition(cid, season) for cid, season in TARGETS.items()}
    available = sum(1 for row in competitions.values() if row["status"].startswith("PASS_"))
    receipt = {
        "schema_version": "V5.5.32-active-domain-observed-identity-r1",
        "generated_at_utc": now_utc(),
        "status": "PASS_ALL_TARGETS" if available == len(TARGETS) else ("PASS_PARTIAL_TARGETS" if available else "FAIL_NO_TARGET_IDENTITY"),
        "target_competition_count": len(TARGETS),
        "available_competition_count": available,
        "competitions": competitions,
        "formal_weight_change": False,
        "probability_change": False,
        "promotion_sample_count_change": 0,
        "policy": (
            "Identity is derived only from current-season rows already frozen in registered processed competition domains. "
            "It does not create team strength or probability mass. Exact normalized identities and observed variants are allowed; "
            "fuzzy substitution, historical-season fallback and cross-club guessing are prohibited. Missing clubs fail closed per fixture."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "available_competition_count": available,
        "team_counts": {cid: row["team_count"] for cid, row in competitions.items()},
    }, ensure_ascii=False, indent=2))
    return 0 if available else 2


if __name__ == "__main__":
    raise SystemExit(main())
