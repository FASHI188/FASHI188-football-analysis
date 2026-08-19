#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N2_SOURCE_RESULT_V1"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
PAGES = {
    "EPL": "https://footiqo.com/database/leagues/england-premier-league/",
    "LL": "https://footiqo.com/database/leagues/spain-laliga/",
    "BL": "https://footiqo.com/database/leagues/germany-bundesliga/",
    "SA": "https://footiqo.com/database/leagues/italy-serie-a/",
    "L1": "https://footiqo.com/database/leagues/france-ligue-1/",
}
ODDS_COLS = [
    "id", "matchDate", "Country", "League", "Season", "homeTeam", "awayTeam",
    "H", "D", "A", "O05", "U05", "O15", "U15", "O25", "U25",
    "O35", "U35", "O45", "U45", "BTTSY", "BTTSN",
]
LINE_PAIRS = {
    "05": ("O05", "U05"),
    "15": ("O15", "U15"),
    "25": ("O25", "U25"),
    "35": ("O35", "U35"),
    "45": ("O45", "U45"),
}
OUT = Path("football-data/research/c072n2_source_summary.json")


def as_price(x: str) -> float | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def devig_over(o: float, u: float) -> float:
    io, iu = 1.0 / o, 1.0 / u
    return io / (io + iu)


def norm_header(x: str) -> str:
    return re.sub(r"\s+", " ", x.strip())


def extract_odds_rows(html: str, league_code: str) -> tuple[list[dict], dict]:
    marker = html.find(HEADING)
    if marker < 0:
        return [], {"heading_found": False, "matching_tables": 0}

    # Binding zero-label guard: parser receives ONLY the source substring after the odds heading.
    odds_html = html[marker:]
    soup = BeautifulSoup(odds_html, "html.parser")
    rows: list[dict] = []
    matching_tables = 0

    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        headers = [norm_header(x.get_text(" ", strip=True)) for x in header_cells]
        if not headers:
            first = table.find("tr")
            if first:
                headers = [norm_header(x.get_text(" ", strip=True)) for x in first.find_all(["th", "td"])]
        if not {"O15", "U15", "O25", "U25", "O35", "U35"}.issubset(set(headers)):
            continue
        matching_tables += 1
        idx = {h: i for i, h in enumerate(headers)}
        # Never select any result/score field; only fixed ODDS_COLS intersections are materialized.
        selected_cols = [c for c in ODDS_COLS if c in idx]
        for tr in table.find_all("tr")[1:]:
            cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
            if not cells or len(cells) < len(headers):
                continue
            r = {c: cells[idx[c]] for c in selected_cols}
            if not r.get("id") or not r.get("matchDate"):
                continue
            r["_source_league_code"] = league_code
            rows.append(r)
    return rows, {"heading_found": True, "matching_tables": matching_tables}


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-research",
        "Accept-Language": "en-US,en;q=0.9",
    })

    all_rows: list[dict] = []
    page_stats = {}
    access_errors = []
    for code, url in PAGES.items():
        try:
            r = s.get(url, timeout=45, allow_redirects=True)
            ok_http = 200 <= r.status_code < 300
            extracted, meta = extract_odds_rows(r.text if ok_http else "", code)
            page_stats[code] = {
                "url": url,
                "status_code": r.status_code,
                "bytes": len(r.content),
                "heading_found": meta["heading_found"],
                "matching_tables": meta["matching_tables"],
                "retained_rows": len(extracted),
            }
            if not ok_http or not meta["heading_found"]:
                access_errors.append(code)
            all_rows.extend(extracted)
        except Exception as exc:
            page_stats[code] = {"url": url, "error": repr(exc), "retained_rows": 0}
            access_errors.append(code)

    if access_errors:
        summary = {
            "schema": SCHEMA,
            "project_line": "football3",
            "terminal": "SOURCE_ACCESS_BLOCKED",
            "page_stats": page_stats,
            "blocked_pages": access_errors,
            "target_result_values_materialized": 0,
            "model_fit": 0,
            "model_score": 0,
            "C073_C077_quarantined": True,
            "C070F_confirmation1597_opened": False,
            "protected_opened": False,
            "formal_weight": 0,
        }
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    # Deduplicate repeated rendering of current/history tables mechanically by source + id + league + season.
    dedup: dict[tuple, dict] = {}
    duplicate_rows = 0
    for r in all_rows:
        key = (r.get("_source_league_code", ""), r.get("id", ""), r.get("League", ""), r.get("Season", ""))
        if key in dedup:
            duplicate_rows += 1
            continue
        dedup[key] = r
    rows = list(dedup.values())
    n = len(rows)

    seasons = {r.get("Season", "").strip() for r in rows if r.get("Season", "").strip()}
    counts = Counter(r.get("_source_league_code", "") for r in rows)

    valid_by_line = Counter()
    triple = 0
    allfive = 0
    monotone = 0
    for r in rows:
        ps = {}
        for line, (oc, uc) in LINE_PAIRS.items():
            o, u = as_price(r.get(oc, "")), as_price(r.get(uc, ""))
            if o is not None and u is not None:
                valid_by_line[line] += 1
                ps[line] = devig_over(o, u)
        if all(k in ps for k in ["15", "25", "35"]):
            triple += 1
        if all(k in ps for k in ["05", "15", "25", "35", "45"]):
            allfive += 1
            seq = [ps[k] for k in ["05", "15", "25", "35", "45"]]
            if all(seq[i] + 1e-12 >= seq[i + 1] for i in range(4)):
                monotone += 1

    frac = lambda x: float(x / n) if n else 0.0
    monotone_frac = float(monotone / allfive) if allfive else 0.0
    duplicate_frac = float(duplicate_rows / max(len(all_rows), 1))

    gates = {
        "all_five_pages": len(page_stats) == 5 and all(x.get("heading_found") for x in page_stats.values()),
        "odds_table_each_page": all(x.get("matching_tables", 0) >= 1 for x in page_stats.values()),
        "unique_rows_ge_4000": n >= 4000,
        "seasons_ge_5": len(seasons) >= 5,
        "four_leagues_ge_500": sum(1 for c in counts.values() if c >= 500) >= 4,
        "ou25_coverage_ge_90pct": frac(valid_by_line["25"]) >= 0.90,
        "ou15_25_35_coverage_ge_80pct": frac(triple) >= 0.80,
        "all_five_lines_coverage_ge_60pct": frac(allfive) >= 0.60,
        "all_five_monotone_ge_95pct": monotone_frac >= 0.95,
        "duplicate_rate_le_05pct": duplicate_frac <= 0.005,
        "zero_target_materialization": True,
        "zero_model": True,
    }
    passed = all(gates.values())
    summary = {
        "schema": SCHEMA,
        "project_line": "football3",
        "terminal": "C072N2_MULTILINE_SOURCE_PASS" if passed else "STOP_MULTILINE_SOURCE_COVERAGE",
        "page_stats": page_stats,
        "raw_retained_rows_before_dedup": len(all_rows),
        "unique_odds_identities": n,
        "league_row_counts": dict(counts),
        "season_count": len(seasons),
        "seasons": sorted(seasons),
        "valid_pair_counts": dict(valid_by_line),
        "ou25_coverage": frac(valid_by_line["25"]),
        "ou15_25_35_joint_coverage": frac(triple),
        "all_five_lines_joint_coverage": frac(allfive),
        "all_five_monotone_fraction": monotone_frac,
        "duplicate_render_rows": duplicate_rows,
        "duplicate_render_fraction": duplicate_frac,
        "gates": gates,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "market_semantics": "historical closing odds snapshot; immutable quote timestamp not established",
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
