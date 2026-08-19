#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

BASE = Path(__file__).with_name("download_c072n16_footiqo_new2000_zero_label.py")
spec = importlib.util.spec_from_file_location("c072n16_base", BASE)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load N16 base downloader")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

OUT_CSV = Path("football-data/research/c072n16r1_footiqo_new2000_zero_label.csv")
OUT_FULL = Path("football-data/research/c072n16r1_footiqo_full_zero_label_inventory.csv")
OUT_SUMMARY = Path("football-data/research/c072n16r1_footiqo_new2000_zero_label_summary.json")


def visible_seasons(table, headers: list[str]) -> list[str]:
    if "Season" not in headers:
        return []
    idx = headers.index("Season")
    vals = []
    for tr in table.find_all("tr")[1:]:
        cells = [m.norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if len(cells) > idx and cells[idx] and cells[idx] != "Season":
            vals.append(cells[idx])
    return sorted(set(vals))


def has_pre2025_start(seasons: list[str]) -> bool:
    for s in seasons:
        mm = re.match(r"\s*(\d{4})", s)
        if mm and int(mm.group(1)) <= 2024:
            return True
    return False


def resolve_historical_odds_table(page_html: str):
    marker = page_html.find(m.HEADING)
    if marker < 0:
        return None, None
    soup = m.BeautifulSoup(page_html[marker:], "html.parser")
    historical = []
    for table in soup.find_all("table"):
        headers = m.table_headers(table)
        if headers != m.HEADERS:
            continue
        raw_tid = str(table.get("data-wpdatatable_id", ""))
        if not raw_tid.isdigit():
            continue
        seasons = visible_seasons(table, headers)
        if has_pre2025_start(seasons):
            historical.append((table, int(raw_tid)))
    if len(historical) != 1:
        return None, None
    return historical[0]


def main() -> int:
    # Protocol-only overrides. Selection/source/gates remain the frozen N16 implementation.
    m.resolve_odds_table = resolve_historical_odds_table
    m.OUT_CSV = OUT_CSV
    m.OUT_FULL = OUT_FULL
    m.OUT_SUMMARY = OUT_SUMMARY
    rc = m.main()
    s = json.loads(OUT_SUMMARY.read_text(encoding="utf-8"))
    s["schema"] = "C072N16R1_FOOTIQO_NEW2000_ZERO_LABEL_V1"
    s["parent_n16_terminal"] = "C072N16_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_STOP"
    s["protocol_correction"] = "historical exact-schema table selected only when visible Season metadata contains start-year <=2024"
    if s.get("pass"):
        s["terminal"] = "C072N16R1_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_PASS"
    else:
        s["terminal"] = "C072N16R1_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_STOP"
    OUT_SUMMARY.write_text(json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "terminal": s["terminal"],
        "raw_retrieved_rows": s.get("raw_retrieved_rows"),
        "pooled_unique_nonconflicting_rows": s.get("pooled_unique_nonconflicting_rows"),
        "selected_rows": s.get("selected_rows"),
        "selected_source_counts": s.get("selected_source_counts"),
        "selected_coverage": s.get("selected_coverage"),
        "selected_csv_sha256": s.get("selected_csv_sha256"),
        "selected_ordered_identity_sha256": s.get("selected_ordered_identity_sha256"),
        "gates": s.get("gates"),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
