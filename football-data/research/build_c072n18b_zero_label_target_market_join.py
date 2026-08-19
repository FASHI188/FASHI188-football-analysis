#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import html as html_lib
import json
import math
import os
import re
import shutil
import tempfile
import unicodedata
import urllib.request
from collections import Counter, defaultdict, deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------- Frozen N18B constants ----------------
RELEASE_API = "https://api.github.com/repos/JaseZiv/worldfootballR_data/releases/tags/fotmob_match_details"
RELEASE_ID = 79989708
ASSETS = {
    "EPL": ("47_match_details.csv", "28450c923e6091fc04cd7f40ff1c25d94d4a213edaeec64c2f189d24ac440006"),
    "LALIGA": ("87_match_details.csv", "7c3b692ac84c9ff2535c2fcbf11af5a9fd91625e2d2fdd0762b13a1f757f89fa"),
    "BUNDESLIGA": ("54_match_details.csv", "3480e08739337e8dabe41098c6c87b131853596d537f5f62a40046087f0f31f4"),
    "SERIEA": ("55_match_details.csv", "fe5c42d2fe8feb3c76e95e4d07f30cfc8874d82e7575fcc92f1e71c6c1b17076"),
    "LIGUE1": ("53_match_details.csv", "793ac1e76ef69e0adbd0a3c90b9ec4d6d3800f6150219991fafdb2e3de0b1fd8"),
    "MLS": ("130_match_details.csv", "e1d9f2b1d711ae06c2e04137208a01fb1bd736010012f127b2229651f00d603b"),
}
FOOTIQO_PAGES = [
    ("EPL", "https://footiqo.com/database/leagues/england-premier-league/", "2024/2025"),
    ("LALIGA", "https://footiqo.com/database/leagues/spain-laliga/", "2024/2025"),
    ("BUNDESLIGA", "https://footiqo.com/database/leagues/germany-bundesliga/", "2024/2025"),
    ("SERIEA", "https://footiqo.com/database/leagues/italy-serie-a/", "2024/2025"),
    ("LIGUE1", "https://footiqo.com/database/leagues/france-ligue-1/", "2024/2025"),
    ("MLS", "https://footiqo.com/database/leagues/usa-mls/", "2024"),
]
TARGET_START = dt.datetime(2024, 9, 18, 0, 0)
TARGET_END = dt.datetime(2024, 12, 31, 23, 59, 59)
MIN_HISTORY_MATCHES = 8
WINDOW_MATCHES = 10
HIGH_XG = 0.20
TARGET_N = 550
DEV_N = 400
CONF_N = 150

HEADING = "Historical Odds: 1X2, Over/Under Goals, BTTS"
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
PAGE_SIZE = 500
MAX_POST_REQUESTS = 80
ODDS_HEADERS = [
    "id","matchDate","Country","League","Season","homeTeam","awayTeam",
    "H","D","A","O05","U05","O15","U15","O25","U25",
    "O35","U35","O45","U45","BTTSY","BTTSN",
]
FORBIDDEN_TARGET_COLUMNS = {
    "FTHG","FTAG","FTR","HTHG","HTAG","HTR","score","result","total_goals","target"
}
OUTDIR = Path("football-data/research/_c072n18b_zero_label_join")

PRICE_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text(x) -> str:
    if x is None:
        return ""
    s = html_lib.unescape(str(x)).strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def norm_team(x: str) -> str:
    s = unicodedata.normalize("NFKD", str(x or ""))
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks = [t for t in s.split() if t]
    out = []
    for t in toks:
        if t in {"fc", "cf", "afc", "calcio"}:
            continue
        if t == "utd":
            t = "united"
        elif t == "st":
            t = "saint"
        out.append(t)
    return " ".join(out)


def parse_fotmob_time(s: str):
    if not s:
        return None
    for fmt in (
        "%a, %b %d, %Y, %H:%M UTC",
        "%a, %b %d, %Y, %I:%M %p UTC",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


def parse_footiqo_time(s: str):
    try:
        return dt.datetime.strptime(str(s).strip(), "%d-%m-%y %H:%M")
    except ValueError:
        return None


def to_int(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return int(float(x))
    except (TypeError, ValueError):
        return None


def to_float(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        v = float(str(x).replace(",", "."))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def price(x):
    m = PRICE_RE.search(norm_text(x))
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def fetch_release_assets():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "football3-n18b-zero-label/1.0",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(RELEASE_API, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        obj = json.load(resp)
    if int(obj.get("id") or -1) != RELEASE_ID:
        raise RuntimeError(f"SOURCE_RELEASE_MISMATCH {obj.get('id')}")
    by_name = {a.get("name"): a for a in obj.get("assets", [])}
    resolved = {}
    for code, (name, expected_sha) in ASSETS.items():
        if name not in by_name:
            raise RuntimeError(f"SOURCE_ASSET_MISSING {name}")
        resolved[code] = (by_name[name], expected_sha)
    return resolved


def download(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "football3-n18b-zero-label/1.0"})
    with urllib.request.urlopen(req, timeout=120) as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)


def build_history(resolved, tdir: Path):
    # Per source asset family, map normalized team name -> set(team ids).
    name_ids = {code: defaultdict(set) for code in ASSETS}
    team_display = {code: defaultdict(set) for code in ASSETS}
    # Full match representation; only identity + xG state fields are accessed.
    matches = {}
    source_identity_keys = set()
    raw_receipts = []

    for code, (asset, expected_sha) in resolved.items():
        name = asset["name"]
        p = tdir / name
        download(asset["browser_download_url"], p)
        digest = sha256_file(p)
        if digest != expected_sha:
            raise RuntimeError(f"SOURCE_SHA_MISMATCH {name} {digest} {expected_sha}")
        raw_receipts.append({"code": code, "asset_id": asset.get("id"), "name": name, "bytes": p.stat().st_size, "sha256": digest})

        with p.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            header = next(r)
            ix = {h: i for i, h in enumerate(header)}
            required = [
                "match_id","match_time_utc","home_team_id","home_team","away_team_id","away_team",
                "team_id","expected_goals"
            ]
            missing = [x for x in required if x not in ix]
            if missing:
                raise RuntimeError(f"SOURCE_SCHEMA_MISSING {name} {missing}")
            for row in r:
                mid = to_int(row[ix["match_id"]])
                when = parse_fotmob_time(row[ix["match_time_utc"]])
                hid = to_int(row[ix["home_team_id"]])
                aid = to_int(row[ix["away_team_id"]])
                hname = norm_text(row[ix["home_team"]])
                aname = norm_text(row[ix["away_team"]])
                tid = to_int(row[ix["team_id"]])
                xg = to_float(row[ix["expected_goals"]])
                if mid is None or when is None or hid is None or aid is None:
                    continue
                key = (code, mid)
                if key not in matches:
                    matches[key] = {
                        "code": code, "match_id": mid, "when": when,
                        "home_team_id": hid, "away_team_id": aid,
                        "home_team": hname, "away_team": aname,
                        "home_xg": [], "away_xg": [],
                    }
                    nh, na = norm_team(hname), norm_team(aname)
                    name_ids[code][nh].add(hid)
                    name_ids[code][na].add(aid)
                    team_display[code][nh].add(hname)
                    team_display[code][na].add(aname)
                    source_identity_keys.add((when.date().isoformat(), nh, na))
                if tid is None or xg is None or not (0.0 <= xg <= 1.5):
                    continue
                if tid == hid:
                    matches[key]["home_xg"].append(xg)
                elif tid == aid:
                    matches[key]["away_xg"].append(xg)

    usable = []
    team_history = {code: defaultdict(list) for code in ASSETS}
    for m in matches.values():
        hx = m["home_xg"]
        ax = m["away_xg"]
        if len(hx) + len(ax) < 6 or not hx or not ax:
            continue
        usable.append(m)
        home_row = {
            "when": m["when"], "match_id": m["match_id"],
            "own_xg": sum(hx), "opp_xg": sum(ax),
            "own_shots": len(hx), "opp_shots": len(ax),
            "own_high": sum(x >= HIGH_XG for x in hx), "opp_high": sum(x >= HIGH_XG for x in ax),
        }
        away_row = {
            "when": m["when"], "match_id": m["match_id"],
            "own_xg": sum(ax), "opp_xg": sum(hx),
            "own_shots": len(ax), "opp_shots": len(hx),
            "own_high": sum(x >= HIGH_XG for x in ax), "opp_high": sum(x >= HIGH_XG for x in hx),
        }
        team_history[m["code"]][m["home_team_id"]].append(home_row)
        team_history[m["code"]][m["away_team_id"]].append(away_row)

    for code in team_history:
        for tid in team_history[code]:
            team_history[code][tid].sort(key=lambda z: (z["when"], z["match_id"]))

    unique_name_id = {}
    for code in name_ids:
        unique_name_id[code] = {}
        for n, ids in name_ids[code].items():
            if n and len(ids) == 1:
                unique_name_id[code][n] = next(iter(ids))

    return {
        "usable_matches": usable,
        "team_history": team_history,
        "unique_name_id": unique_name_id,
        "team_display": team_display,
        "source_identity_keys": source_identity_keys,
        "raw_receipts": raw_receipts,
        "source_match_count": len(matches),
        "usable_match_count": len(usable),
    }


def table_headers(t):
    h = [norm_text(x.get_text(" ", strip=True)) for x in t.find_all("th")]
    if not h:
        first = t.find("tr")
        if first:
            h = [norm_text(x.get_text(" ", strip=True)) for x in first.find_all(["th", "td"])]
    return h


def visible_seasons(table, headers):
    if "Season" not in headers:
        return []
    idx = headers.index("Season")
    vals = []
    for tr in table.find_all("tr")[1:]:
        cells = [norm_text(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if len(cells) > idx and cells[idx] and cells[idx] != "Season":
            vals.append(cells[idx])
    return sorted(set(vals))


def resolve_historical_odds_table(page_html: str):
    marker = page_html.find(HEADING)
    if marker < 0:
        return None, None
    soup = BeautifulSoup(page_html[marker:], "html.parser")
    candidates = []
    for t in soup.find_all("table"):
        h = table_headers(t)
        if h != ODDS_HEADERS:
            continue
        raw_tid = str(t.get("data-wpdatatable_id", ""))
        if not raw_tid.isdigit():
            continue
        seasons = visible_seasons(t, h)
        if any(re.match(r"\s*2024", x) for x in seasons):
            candidates.append((t, int(raw_tid)))
    if len(candidates) != 1:
        return None, None
    return candidates[0]


def ajax_payload(nonce: str, start: int):
    body = {
        "draw": "1", "start": str(start), "length": str(PAGE_SIZE),
        "search[value]": "", "search[regex]": "false", NONCE_FIELD: nonce,
    }
    for i, h in enumerate(ODDS_HEADERS):
        body[f"columns[{i}][data]"] = str(i)
        body[f"columns[{i}][name]"] = h
        body[f"columns[{i}][searchable]"] = "true"
        body[f"columns[{i}][orderable]"] = "true"
        body[f"columns[{i}][search][value]"] = ""
        body[f"columns[{i}][search][regex]"] = "false"
    return body


def retrieve_footiqo_odds():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-n18b",
        "Accept-Language": "en-US,en;q=0.9",
    })
    rows = []
    stats = {}
    post_count = 0
    for code, url, wanted_season in FOOTIQO_PAGES:
        page = s.get(url, timeout=45, allow_redirects=True)
        if not (200 <= page.status_code < 300):
            raise RuntimeError(f"FOOTIQO_PAGE_HTTP {code} {page.status_code}")
        table, tid = resolve_historical_odds_table(page.text)
        if table is None or tid is None:
            raise RuntimeError(f"FOOTIQO_TABLE_PROTOCOL {code}")
        soup = BeautifulSoup(page.text, "html.parser")
        nonce_dom = f"wdtNonceFrontendServerSide_{tid}"
        nodes = [x for x in soup.find_all("input") if str(x.get("id", "")) == nonce_dom and str(x.get("name", "")) == nonce_dom]
        if len(nodes) != 1 or str(nodes[0].get("type", "")).lower() != "hidden":
            raise RuntimeError(f"FOOTIQO_NONCE_PROTOCOL {code}")
        nonce = str(nodes[0].get("value") or "")
        if not nonce:
            raise RuntimeError(f"FOOTIQO_NONCE_EMPTY {code}")
        req_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://footiqo.com", "Referer": url,
        }
        start = 0
        expected = None
        code_rows = []
        while True:
            if post_count >= MAX_POST_REQUESTS:
                raise RuntimeError("FOOTIQO_POST_BUDGET")
            body = ajax_payload(nonce, start)
            post_count += 1
            rr = s.post(AJAX, params={"action": ACTION, "table_id": str(tid)}, data=body, headers=req_headers, timeout=45, allow_redirects=True)
            body[NONCE_FIELD] = "<redacted>"
            if not (200 <= rr.status_code < 300):
                raise RuntimeError(f"FOOTIQO_AJAX_HTTP {code} {rr.status_code}")
            x = rr.json()
            rf = to_int(x.get("recordsFiltered"))
            data = x.get("data")
            if rf is None or not isinstance(data, list):
                raise RuntimeError(f"FOOTIQO_AJAX_SHAPE {code}")
            if expected is None:
                expected = rf
                if expected <= 0 or expected > 10000:
                    raise RuntimeError(f"FOOTIQO_COUNT_RANGE {code} {expected}")
            elif rf != expected:
                raise RuntimeError(f"FOOTIQO_COUNT_DRIFT {code}")
            for raw in data:
                if not isinstance(raw, list) or len(raw) != len(ODDS_HEADERS):
                    raise RuntimeError(f"FOOTIQO_ROW_SCHEMA {code}")
                mapped = {ODDS_HEADERS[i]: norm_text(raw[i]) for i in range(len(ODDS_HEADERS))}
                mapped["sourceCode"] = code
                code_rows.append(mapped)
            start += len(data)
            if start >= expected:
                break
            if not data:
                raise RuntimeError(f"FOOTIQO_EMPTY_PAGE {code}")
        nonce = ""
        filtered = []
        for r in code_rows:
            when = parse_footiqo_time(r["matchDate"])
            if when is None:
                continue
            if r["Season"] != wanted_season:
                continue
            if not (TARGET_START <= when <= TARGET_END):
                continue
            r["parsed_when"] = when
            filtered.append(r)
        stats[code] = {"table_id": tid, "retrieved_rows": len(code_rows), "target_window_rows": len(filtered), "wanted_season": wanted_season}
        rows.extend(filtered)
    return rows, stats, post_count


def historical_features(history_rows):
    # history_rows are already sorted and strictly prior-filtered.
    w = history_rows[-WINDOW_MATCHES:]
    n = len(w)
    if n < MIN_HISTORY_MATCHES:
        return None
    own_xg = sum(x["own_xg"] for x in w)
    opp_xg = sum(x["opp_xg"] for x in w)
    own_shots = sum(x["own_shots"] for x in w)
    opp_shots = sum(x["opp_shots"] for x in w)
    return [
        own_xg / n,
        opp_xg / n,
        own_shots / n,
        opp_shots / n,
        own_xg / own_shots if own_shots else None,
        opp_xg / opp_shots if opp_shots else None,
        sum(x["own_high"] for x in w) / n,
        sum(x["opp_high"] for x in w) / n,
    ]


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    resolved = fetch_release_assets()
    with tempfile.TemporaryDirectory(prefix="football3_n18b_") as td:
        history = build_history(resolved, Path(td))

    odds_rows, odds_stats, post_count = retrieve_footiqo_odds()

    mapping_receipt = {}
    eligible = []
    reasons = Counter()
    seen_target_identity = set()
    target_source_overlap = 0

    for r in odds_rows:
        code = r["sourceCode"]
        when = r["parsed_when"]
        hn, an = norm_team(r["homeTeam"]), norm_team(r["awayTeam"])
        target_key = (when.date().isoformat(), hn, an)
        if target_key in history["source_identity_keys"]:
            target_source_overlap += 1
            reasons["source_overlap"] += 1
            continue
        ident = (code, r["id"], r["matchDate"], hn, an)
        if ident in seen_target_identity:
            reasons["duplicate_target_identity"] += 1
            continue
        seen_target_identity.add(ident)

        hid = history["unique_name_id"].get(code, {}).get(hn)
        aid = history["unique_name_id"].get(code, {}).get(an)
        mapping_receipt[f"{code}|{r['homeTeam']}"] = {"normalized": hn, "source_team_id": hid}
        mapping_receipt[f"{code}|{r['awayTeam']}"] = {"normalized": an, "source_team_id": aid}
        if hid is None or aid is None:
            reasons["team_mapping"] += 1
            continue

        hhist = [x for x in history["team_history"][code].get(hid, []) if x["when"].replace(tzinfo=None) < when]
        ahist = [x for x in history["team_history"][code].get(aid, []) if x["when"].replace(tzinfo=None) < when]
        hf = historical_features(hhist)
        af = historical_features(ahist)
        if hf is None or af is None:
            reasons["history_lt8"] += 1
            continue
        o25, u25 = price(r["O25"]), price(r["U25"])
        if o25 is None or u25 is None:
            reasons["ou25_invalid"] += 1
            continue
        po, pu = 1.0 / o25, 1.0 / u25
        q = po / (po + pu)
        feats = hf + af
        if len(feats) != 16 or any(x is None or not math.isfinite(float(x)) for x in feats) or not (0.0 < q < 1.0):
            reasons["feature_nonfinite"] += 1
            continue
        eligible.append({
            "footiqo_id": to_int(r["id"]),
            "match_time_local": when.isoformat(),
            "source_code": code,
            "country": r["Country"], "league": r["League"], "season": r["Season"],
            "home_team": r["homeTeam"], "away_team": r["awayTeam"],
            "home_team_norm": hn, "away_team_norm": an,
            "fotmob_home_team_id": hid, "fotmob_away_team_id": aid,
            "o25": o25, "u25": u25, "q_over25": q,
            "history_home_matches_available": len(hhist),
            "history_away_matches_available": len(ahist),
            "features16": feats,
        })

    if target_source_overlap != 0:
        raise RuntimeError(f"N18B_SOURCE_TARGET_OVERLAP {target_source_overlap}")

    eligible.sort(key=lambda x: (x["match_time_local"], x["footiqo_id"] or 0))
    if len(eligible) < TARGET_N:
        summary = {
            "project": "football3", "experiment": "C072-N18B", "status": "STOP_COVERAGE",
            "started_at_utc": started, "finished_at_utc": utc_now(),
            "footiqo_window_rows": len(odds_rows), "eligible_rows": len(eligible),
            "required_rows": TARGET_N, "ineligibility_reasons": dict(reasons),
            "odds_stats": odds_stats, "post_requests": post_count,
            "source_match_count": history["source_match_count"], "source_usable_match_count": history["usable_match_count"],
            "source_target_overlap": target_source_overlap,
            "target_result_columns_requested_or_materialized": 0, "target_result_values_materialized": 0,
            "model_fit": 0, "target_score": 0,
            "C070F_confirmation1597_opened": False, "sealed_reserves_opened": False,
            "C073_C077_scientific_results_used": False,
        }
        (OUTDIR / "c072n18b_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
        raise SystemExit(f"STOP_COVERAGE eligible={len(eligible)} required={TARGET_N}")

    selected = eligible[:TARGET_N]
    for i, row in enumerate(selected):
        row["split"] = "DEVELOPMENT" if i < DEV_N else "CONFIRMATION_SEALED"

    data_path = OUTDIR / "c072n18b_target550_zero_label.jsonl.gz"
    dev_path = OUTDIR / "c072n18b_dev400_ids.txt"
    conf_path = OUTDIR / "c072n18b_confirmation150_ids.txt"
    map_path = OUTDIR / "c072n18b_team_mapping.json"
    with gzip.open(data_path, "wt", encoding="utf-8", compresslevel=6) as f:
        for row in selected:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    dev_ids = [str(x["footiqo_id"]) for x in selected[:DEV_N]]
    conf_ids = [str(x["footiqo_id"]) for x in selected[DEV_N:]]
    dev_path.write_text("\n".join(dev_ids) + "\n", encoding="utf-8")
    conf_path.write_text("\n".join(conf_ids) + "\n", encoding="utf-8")
    map_path.write_text(json.dumps(mapping_receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    by_code = Counter(x["source_code"] for x in selected)
    by_split = Counter(x["split"] for x in selected)
    summary = {
        "project": "football3", "experiment": "C072-N18B",
        "status": "PASS_N18B_ZERO_LABEL_TARGET_MARKET_JOIN",
        "started_at_utc": started, "finished_at_utc": utc_now(),
        "historical_source_release_id": RELEASE_ID,
        "source_match_count": history["source_match_count"],
        "source_usable_match_count": history["usable_match_count"],
        "footiqo_window_rows": len(odds_rows),
        "eligible_rows": len(eligible), "selected_rows": len(selected),
        "dev_rows": DEV_N, "confirmation_rows": CONF_N,
        "selected_source_counts": dict(sorted(by_code.items())),
        "split_counts": dict(sorted(by_split.items())),
        "ineligibility_reasons": dict(reasons), "odds_stats": odds_stats,
        "post_requests": post_count, "source_target_overlap": target_source_overlap,
        "first_target": selected[0]["match_time_local"], "last_target": selected[-1]["match_time_local"],
        "feature_count": 16, "history_window_matches": WINDOW_MATCHES, "min_history_matches": MIN_HISTORY_MATCHES,
        "market_input": "de-vigged Footiqo closing O/U2.5 q_over25 only",
        "target_result_columns_requested_or_materialized": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0, "target_score": 0,
        "C070F_confirmation1597_opened": False, "sealed_reserves_opened": False,
        "C073_C077_scientific_results_used": False,
        "target550_sha256": sha256_file(data_path),
        "dev400_ids_sha256": sha256_file(dev_path),
        "confirmation150_ids_sha256": sha256_file(conf_path),
        "team_mapping_sha256": sha256_file(map_path),
        "raw_fotmob_assets_retained": 0,
    }
    (OUTDIR / "c072n18b_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
