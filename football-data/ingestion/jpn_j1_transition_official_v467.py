#!/usr/bin/env python3
"""Official J.League 2026 special-season ingestion for JPN_J1.

This route intentionally keeps the 2026 100 Year Vision League separate from
ordinary J1 seasons and from the 2026/27 target season.  It records 90-minute
scores only for the formal football settlement surface; penalty shootout scores
are retained as audit metadata and never overwrite the 90-minute result.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_URL = (
    "https://data.j-league.or.jp/SFMS01/search?"
    "competition_frame_ids=35&competition_years=20261"
)
COMPETITION_ID = "JPN_J1"
SEASON = "2026_special"
EXPECTED_TOTAL = 200
EXPECTED_STAGE_COUNTS = {
    "transition_regional_east": 90,
    "transition_regional_west": 90,
    "transition_playoff_round": 20,
}

TEAM_MAP = {
    "鹿島": "Kashima Antlers",
    "水戸": "Mito HollyHock",
    "浦和": "Urawa Reds",
    "千葉": "JEF United Chiba",
    "柏": "Kashiwa Reysol",
    "FC東京": "FC Tokyo",
    "東京Ｖ": "Tokyo Verdy",
    "町田": "Machida Zelvia",
    "川崎Ｆ": "Kawasaki Frontale",
    "横浜FM": "Yokohama F. Marinos",
    "清水": "Shimizu S-Pulse",
    "名古屋": "Nagoya Grampus",
    "京都": "Kyoto Sanga",
    "Ｇ大阪": "Gamba Osaka",
    "Ｃ大阪": "Cerezo Osaka",
    "神戸": "Vissel Kobe",
    "岡山": "Fagiano Okayama",
    "広島": "Sanfrecce Hiroshima",
    "福岡": "Avispa Fukuoka",
    "長崎": "V-Varen Nagasaki",
}


class DataError(RuntimeError):
    pass


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_tr = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_tr = True
            self.row = []
        elif self.in_tr and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_tr and tag in {"td", "th"} and self.in_cell:
            text = re.sub(r"\s+", " ", html.unescape("".join(self.cell_parts))).strip()
            self.row.append(text)
            self.in_cell = False
            self.cell_parts = []
        elif tag == "tr" and self.in_tr:
            if self.row:
                self.rows.append(self.row)
            self.in_tr = False
            self.row = []


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_html(url: str = OFFICIAL_URL, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FASHI188-football-analysis/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise DataError(f"official J.League request failed: {exc}") from exc
    if not body:
        raise DataError("official J.League response body is empty")
    return body


def _parse_date(raw: str) -> str:
    match = re.search(r"(\d{2})/(\d{2})/(\d{2})", raw)
    if not match:
        raise DataError(f"unsupported official J.League date: {raw!r}")
    yy, mm, dd = map(int, match.groups())
    return f"20{yy:02d}-{mm:02d}-{dd:02d}"


def _parse_score(raw: str) -> tuple[int, int, int | None, int | None]:
    score = re.search(r"(\d+)\s*-\s*(\d+)", raw)
    if not score:
        raise DataError(f"match has no finished 90-minute score: {raw!r}")
    home, away = map(int, score.groups())
    pk = re.search(r"PK\s*(\d+)\s*-\s*(\d+)", raw, flags=re.IGNORECASE)
    if pk:
        pk_home, pk_away = map(int, pk.groups())
    else:
        pk_home = pk_away = None
    return home, away, pk_home, pk_away


def _stage(competition_name: str) -> str:
    if "EAST" in competition_name:
        return "transition_regional_east"
    if "WEST" in competition_name:
        return "transition_regional_west"
    if "プレーオフ" in competition_name:
        return "transition_playoff_round"
    raise DataError(f"unexpected 2026 special J1 stage: {competition_name!r}")


def parse_official_html(body: bytes) -> list[dict[str, Any]]:
    text = body.decode("utf-8", errors="replace")
    parser = TableParser()
    parser.feed(text)
    rows: list[dict[str, Any]] = []
    for cells in parser.rows:
        if len(cells) < 8:
            continue
        if cells[0] != "2026特別":
            continue
        if "Ｊ１百年構想" not in cells[1]:
            continue
        try:
            home = TEAM_MAP[cells[5]]
            away = TEAM_MAP[cells[7]]
        except KeyError as exc:
            raise DataError(f"unmapped official J1 team token: {exc.args[0]!r}") from exc
        home_goals, away_goals, pk_home, pk_away = _parse_score(cells[6])
        rows.append(
            {
                "competition_id": COMPETITION_ID,
                "season": SEASON,
                "source_season": "2026特別",
                "stage": _stage(cells[1]),
                "source_code": "data.j-league.or.jp/SFMS01",
                "Date": _parse_date(cells[3]),
                "Time": cells[4],
                "HomeTeam": home,
                "AwayTeam": away,
                "FTHG": home_goals,
                "FTAG": away_goals,
                "FTR": "H" if home_goals > away_goals else "D" if home_goals == away_goals else "A",
                "official_competition": cells[1],
                "official_round": cells[2],
                "official_score_text": cells[6],
                "penalty_home": "" if pk_home is None else pk_home,
                "penalty_away": "" if pk_away is None else pk_away,
                "settlement_scope": "90_minutes_including_stoppage",
            }
        )
    return rows


def validate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != EXPECTED_TOTAL:
        raise DataError(f"official 2026 special J1 row count {len(rows)} != {EXPECTED_TOTAL}")

    seen: set[tuple[str, str, str]] = set()
    stage_counts = {key: 0 for key in EXPECTED_STAGE_COUNTS}
    appearances: dict[str, int] = {team: 0 for team in TEAM_MAP.values()}
    penalty_matches = 0
    for row in rows:
        key = (row["Date"], row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataError(f"duplicate official 2026 special J1 match: {key}")
        seen.add(key)
        stage = row["stage"]
        if stage not in stage_counts:
            raise DataError(f"unexpected stage in normalized row: {stage}")
        stage_counts[stage] += 1
        appearances[row["HomeTeam"]] = appearances.get(row["HomeTeam"], 0) + 1
        appearances[row["AwayTeam"]] = appearances.get(row["AwayTeam"], 0) + 1
        if row["penalty_home"] != "":
            penalty_matches += 1

    if stage_counts != EXPECTED_STAGE_COUNTS:
        raise DataError(f"stage counts mismatch: {stage_counts} != {EXPECTED_STAGE_COUNTS}")
    if len(appearances) != 20 or any(count != 20 for count in appearances.values()):
        raise DataError(f"team appearance audit failed: {appearances}")

    return {
        "match_count": len(rows),
        "stage_counts": stage_counts,
        "team_count": len(appearances),
        "team_appearances": dict(sorted(appearances.items())),
        "penalty_decided_draws": penalty_matches,
        "probability_input_score_scope": "90_minute_scores_only",
        "penalty_shootout_used_for_formal_score": False,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run() -> dict[str, Any]:
    body = fetch_html()
    rows = parse_official_html(body)
    audit = validate_rows(rows)
    fetched_at = utc_now()
    body_hash = hashlib.sha256(body).hexdigest()

    processed = ROOT / "processed" / COMPETITION_ID / "official_2026_special.csv"
    source_manifest = ROOT / "raw" / COMPETITION_ID / "official_2026_special_source.json"
    status_path = ROOT / "manifests" / "jpn_j1_2026_special_official_v467_status.json"
    write_csv(processed, rows)

    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    source_manifest.write_text(
        json.dumps(
            {
                "schema_version": "V4.6.7-jpn-j1-official-transition-source",
                "competition_id": COMPETITION_ID,
                "season": SEASON,
                "source": OFFICIAL_URL,
                "source_owner": "Japan Professional Football League (J.League)",
                "fetched_at_utc": fetched_at,
                "response_sha256": body_hash,
                "raw_body_archived": False,
                "processed_path": str(processed.relative_to(ROOT)),
                "audit": audit,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    status = {
        "schema_version": "V4.6.7-jpn-j1-official-transition-route",
        "competition_id": COMPETITION_ID,
        "season": SEASON,
        "generated_at_utc": fetched_at,
        "status": "OFFICIAL_TRANSITION_ROUTE_VALIDATED",
        "formal_weight_change": False,
        "source": OFFICIAL_URL,
        "response_sha256": body_hash,
        "processed_path": str(processed.relative_to(ROOT)),
        "transition_season_is_separate_domain": True,
        "must_not_pool_into_2026_27_target_season": True,
        "settlement_scope": "90_minutes_including_stoppage",
        "audit": audit,
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        status = run()
    except DataError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
