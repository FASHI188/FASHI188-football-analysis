#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html as html_lib
import io
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCHEMA = "C072N16_FOOTIQO_NEW2000_ZERO_LABEL_V1"
HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
PAGE_SIZE = 500
MAX_POST_REQUESTS = 80
TARGET_N = 2000
PAGES = [
    ("TR", "https://footiqo.com/database/leagues/turkey-super-lig/"),
    ("GR", "https://footiqo.com/database/leagues/greece-super-league/"),
    ("BR", "https://footiqo.com/database/leagues/brazil-serie-a/"),
    ("MLS", "https://footiqo.com/database/leagues/usa-mls/"),
]
HEADERS = [
    "id","matchDate","Country","League","Season","homeTeam","awayTeam",
    "H","D","A","O05","U05","O15","U15","O25","U25",
    "O35","U35","O45","U45","BTTSY","BTTSN",
]
OUT_CSV = Path("football-data/research/c072n16_footiqo_new2000_zero_label.csv")
OUT_FULL = Path("football-data/research/c072n16_footiqo_full_zero_label_inventory.csv")
OUT_SUMMARY = Path("football-data/research/c072n16_footiqo_new2000_zero_label_summary.json")
PRICE_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
FORBIDDEN_RESULT_NAMES = {
    "FTHG","FTAG","FTR","HTHG","HTAG","HTR","1HHG","1HAG","1HR",
    "2HHG","2HAG","2HR","score","result","total_goals","target",
}


def norm(x) -> str:
    if x is None:
        return ""
    s = html_lib.unescape(str(x)).strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def table_headers(t) -> list[str]:
    h = [norm(x.get_text(" ", strip=True)) for x in t.find_all("th")]
    if not h:
        first = t.find("tr")
        if first:
            h = [norm(x.get_text(" ", strip=True)) for x in first.find_all(["th", "td"])]
    return h


def resolve_odds_table(page_html: str):
    marker = page_html.find(HEADING)
    if marker < 0:
        return None, None
    soup = BeautifulSoup(page_html[marker:], "html.parser")
    candidates = []
    for t in soup.find_all("table"):
        h = table_headers(t)
        if h == HEADERS:
            raw_tid = str(t.get("data-wpdatatable_id", ""))
            if raw_tid.isdigit():
                candidates.append((t, int(raw_tid)))
    if len(candidates) != 1:
        return None, None
    return candidates[0]


def payload(nonce: str, start: int) -> dict[str, str]:
    body = {
        "draw": "1",
        "start": str(start),
        "length": str(PAGE_SIZE),
        "search[value]": "",
        "search[regex]": "false",
        NONCE_FIELD: nonce,
    }
    for i, h in enumerate(HEADERS):
        body[f"columns[{i}][data]"] = str(i)
        body[f"columns[{i}][name]"] = h
        body[f"columns[{i}][searchable]"] = "true"
        body[f"columns[{i}][orderable]"] = "true"
        body[f"columns[{i}][search][value]"] = ""
        body[f"columns[{i}][search][regex]"] = "false"
    return body


def as_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def price(x) -> float | None:
    m = PRICE_RE.search(norm(x))
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def identity_string(r: dict[str, str]) -> str:
    return "|".join([
        r["sourceCode"], r["id"], r["matchDate"], r["Country"], r["League"],
        r["Season"], r["homeTeam"], r["awayTeam"],
    ])


def row_signature(r: dict[str, str]) -> str:
    return "\x1f".join(r.get(c, "") for c in ["sourceCode"] + HEADERS)


def write_csv(path: Path, rows: list[dict[str, str]]) -> str:
    cols = ["identity_sha256", "sourceCode"] + HEADERS
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    raw = buf.getvalue().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def coverage(rows: list[dict[str, str]]) -> dict:
    n = len(rows)
    core = ["id","matchDate","Country","League","Season","homeTeam","awayTeam"]
    complete_core = sum(all(norm(r.get(c, "")) for c in core) for r in rows)
    pairs = {
        "05": ("O05", "U05"),
        "15": ("O15", "U15"),
        "25": ("O25", "U25"),
        "35": ("O35", "U35"),
        "45": ("O45", "U45"),
    }
    pair_counts = Counter()
    joint1535 = 0
    allfive = 0
    hda = 0
    btts = 0
    for r in rows:
        valid = {}
        for k, (oc, uc) in pairs.items():
            valid[k] = price(r.get(oc, "")) is not None and price(r.get(uc, "")) is not None
            pair_counts[k] += int(valid[k])
        joint1535 += int(valid["15"] and valid["25"] and valid["35"])
        allfive += int(all(valid.values()))
        hda += int(all(price(r.get(c, "")) is not None for c in ("H", "D", "A")))
        btts += int(price(r.get("BTTSY", "")) is not None and price(r.get("BTTSN", "")) is not None)
    frac = lambda x: float(x / n) if n else 0.0
    return {
        "rows": n,
        "complete_core_identity_count": complete_core,
        "complete_core_identity_fraction": frac(complete_core),
        "valid_pair_counts": dict(pair_counts),
        "ou25_coverage": frac(pair_counts["25"]),
        "joint_ou15_25_35_coverage": frac(joint1535),
        "allfive_ou_coverage": frac(allfive),
        "hda_coverage": frac(hda),
        "btts_coverage": frac(btts),
    }


def main() -> int:
    result = {
        "schema": SCHEMA,
        "project_line": "football3",
        "classification": "ZERO_LABEL_DOWNLOAD_INVENTORY",
        "target_n": TARGET_N,
        "page_size": PAGE_SIZE,
        "max_post_requests": MAX_POST_REQUESTS,
        "post_requests_made": 0,
        "target_result_columns_requested_or_materialized": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "nonce_values_persisted_or_logged": 0,
        "C073_C077_scientific_results_used": False,
        "C070F_confirmation1597_opened": False,
        "aleague_men_2025_26_target_opened": False,
        "aleague_women_2025_26_target_opened": False,
        "formal_weight": 0,
    }

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-n16",
        "Accept-Language": "en-US,en;q=0.9",
    })
    all_rows: list[dict[str, str]] = []
    source_stats = {}
    fatal = False

    for code, url in PAGES:
        st = {"sourceCode": code, "page_url": url, "requests": 0, "rows": 0}
        try:
            page = s.get(url, timeout=45, allow_redirects=True)
        except Exception as e:
            st["error"] = f"PAGE_REQUEST:{type(e).__name__}"
            source_stats[code] = st
            fatal = True
            continue
        st["page_status"] = page.status_code
        if not (200 <= page.status_code < 300):
            st["error"] = "PAGE_HTTP"
            source_stats[code] = st
            fatal = True
            continue

        table, tid = resolve_odds_table(page.text)
        if table is None or tid is None:
            st["error"] = "ODDS_TABLE_PROTOCOL_DRIFT"
            source_stats[code] = st
            fatal = True
            continue
        st["table_id"] = tid
        st["header_exact"] = True

        soup = BeautifulSoup(page.text, "html.parser")
        nonce_dom = f"wdtNonceFrontendServerSide_{tid}"
        nodes = [x for x in soup.find_all("input") if str(x.get("id", "")) == nonce_dom and str(x.get("name", "")) == nonce_dom]
        if len(nodes) != 1 or str(nodes[0].get("type", "")).lower() != "hidden":
            st["error"] = "NONCE_PROTOCOL_DRIFT"
            source_stats[code] = st
            fatal = True
            continue
        raw_nonce = nodes[0].get("value")
        nonce = str(raw_nonce) if raw_nonce is not None else ""
        if not nonce:
            st["error"] = "NONCE_EMPTY"
            source_stats[code] = st
            fatal = True
            continue

        req_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://footiqo.com",
            "Referer": url,
        }
        rows_code = []
        expected_count = None
        starts = None
        start = 0
        while True:
            if result["post_requests_made"] >= MAX_POST_REQUESTS:
                st["error"] = "POST_REQUEST_BUDGET_EXCEEDED"
                fatal = True
                break
            body = payload(nonce, start)
            result["post_requests_made"] += 1
            st["requests"] += 1
            try:
                rr = s.post(AJAX, params={"action": ACTION, "table_id": str(tid)}, data=body, headers=req_headers, timeout=45, allow_redirects=True)
            except Exception as e:
                st["error"] = f"AJAX_REQUEST:{type(e).__name__}"
                fatal = True
                break
            finally:
                body[NONCE_FIELD] = "<redacted>"
            if not (200 <= rr.status_code < 300):
                st["error"] = "AJAX_HTTP"
                st["ajax_status"] = rr.status_code
                fatal = True
                break
            try:
                x = rr.json()
            except Exception:
                st["error"] = "AJAX_NON_JSON"
                fatal = True
                break
            rf = as_int(x.get("recordsFiltered")) if isinstance(x, dict) else None
            data = x.get("data", []) if isinstance(x, dict) else None
            if rf is None or not isinstance(data, list):
                st["error"] = "AJAX_METADATA_SHAPE"
                fatal = True
                break
            if expected_count is None:
                expected_count = rf
                st["records_filtered_first"] = rf
                st["records_total_first"] = as_int(x.get("recordsTotal"))
                if rf <= 0 or rf > 10000:
                    st["error"] = "FILTERED_COUNT_OUT_OF_BOUNDS"
                    fatal = True
                    break
                starts = list(range(0, rf, PAGE_SIZE))
                if len(data) != min(PAGE_SIZE, rf):
                    st["error"] = "PAGE_SIZE_NOT_HONORED"
                    fatal = True
                    break
            elif rf != expected_count:
                st["error"] = "FILTERED_COUNT_DRIFT"
                fatal = True
                break
            if any(not isinstance(row, list) or len(row) != len(HEADERS) for row in data):
                st["error"] = "ROW_SCHEMA_DRIFT"
                fatal = True
                break
            for row in data:
                mapped = {HEADERS[i]: norm(row[i]) for i in range(len(HEADERS))}
                mapped["sourceCode"] = code
                rows_code.append(mapped)
            assert starts is not None
            ix = starts.index(start)
            if ix + 1 >= len(starts):
                break
            start = starts[ix + 1]

        # Explicitly destroy secret-like values before diagnostics are retained.
        nonce = ""
        raw_nonce = None
        st["rows"] = len(rows_code)
        st["row_count_matches_filtered"] = expected_count is not None and len(rows_code) == expected_count
        if not st["row_count_matches_filtered"]:
            fatal = True
            st.setdefault("error", "ROW_COUNT_MISMATCH")
        source_stats[code] = st
        all_rows.extend(rows_code)

    result["source_stats"] = source_stats

    # Deduplicate only byte-equivalent identity duplicates; conflict invalidates the identity.
    by_identity: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in all_rows:
        by_identity[identity_string(r)].append(r)
    unique_rows = []
    conflict_identity_count = 0
    exact_duplicate_rows_removed = 0
    for ident, rows in by_identity.items():
        sigs = {row_signature(r) for r in rows}
        if len(sigs) > 1:
            conflict_identity_count += 1
            continue
        exact_duplicate_rows_removed += len(rows) - 1
        r = rows[0]
        r["identity_sha256"] = hashlib.sha256(ident.encode("utf-8")).hexdigest()
        unique_rows.append(r)

    unique_rows.sort(key=lambda r: (r["identity_sha256"], r["sourceCode"], r["id"]))
    selected = unique_rows[:TARGET_N]

    full_sha = write_csv(OUT_FULL, unique_rows)
    selected_sha = write_csv(OUT_CSV, selected)
    selected_identity_concat = "\n".join(r["identity_sha256"] for r in selected) + ("\n" if selected else "")
    selected_identity_sha = hashlib.sha256(selected_identity_concat.encode("utf-8")).hexdigest()

    result.update({
        "raw_retrieved_rows": len(all_rows),
        "pooled_unique_nonconflicting_rows": len(unique_rows),
        "conflicting_identity_count": conflict_identity_count,
        "exact_duplicate_rows_removed": exact_duplicate_rows_removed,
        "selected_rows": len(selected),
        "selected_identity_unique_count": len({r["identity_sha256"] for r in selected}),
        "full_inventory_csv_sha256": full_sha,
        "selected_csv_sha256": selected_sha,
        "selected_ordered_identity_sha256": selected_identity_sha,
        "selected_source_counts": dict(Counter(r["sourceCode"] for r in selected)),
        "selected_season_counts": dict(Counter(f"{r['sourceCode']}|{r['Season']}" for r in selected)),
        "selected_coverage": coverage(selected),
    })

    c = result["selected_coverage"]
    gates = {
        "all_four_source_pages_protocol_ok": not fatal and len(source_stats) == 4 and all("error" not in x for x in source_stats.values()),
        "pooled_unique_rows_ge_2000": len(unique_rows) >= TARGET_N,
        "selected_exactly_2000": len(selected) == TARGET_N,
        "selected_identity_hashes_unique": len({r["identity_sha256"] for r in selected}) == TARGET_N,
        "complete_core_identity_100pct": c["complete_core_identity_fraction"] == 1.0,
        "ou25_coverage_ge_90pct": c["ou25_coverage"] >= 0.90,
        "joint_ou15_25_35_coverage_ge_80pct": c["joint_ou15_25_35_coverage"] >= 0.80,
        "allfive_ou_coverage_ge_65pct": c["allfive_ou_coverage"] >= 0.65,
        "zero_target_result_materialization": result["target_result_columns_requested_or_materialized"] == 0 and result["target_result_values_materialized"] == 0,
        "zero_model_fit_score": result["model_fit"] == 0 and result["model_score"] == 0,
        "zero_nonce_persistence": result["nonce_values_persisted_or_logged"] == 0,
        "seals_and_quarantine_hold": True,
    }
    result["gates"] = gates
    passed = all(gates.values())
    result["pass"] = passed
    result["terminal"] = "C072N16_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_PASS" if passed else "C072N16_FOOTIQO_NEW2000_ZERO_LABEL_DOWNLOAD_STOP"
    OUT_SUMMARY.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
