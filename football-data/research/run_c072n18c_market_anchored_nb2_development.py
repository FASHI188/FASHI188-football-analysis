#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import html as html_lib
import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import requests
from bs4 import BeautifulSoup
from scipy.optimize import brentq, minimize
from scipy.special import gammaln

SCHEMA = "C072N18C_MARKET_ANCHORED_NB2_DEVELOPMENT_V1"
N18B_DIR = Path("football-data/research/_c072n18b_zero_label_join")
OUTDIR = Path("football-data/research/_c072n18c_development")
TARGET_PATH = N18B_DIR / "c072n18b_target550_zero_label.jsonl.gz"
DEV_IDS_PATH = N18B_DIR / "c072n18b_dev400_ids.txt"
CONF_IDS_PATH = N18B_DIR / "c072n18b_confirmation150_ids.txt"
N18B_SUMMARY = N18B_DIR / "c072n18b_summary.json"

EXPECTED_DEV_IDS_SHA = "55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf"
EXPECTED_CONF_IDS_SHA = "774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f"
EXPECTED_DEV_SEMANTIC_SHA = "dcc32269261fc8c3b1a86e4ea930539d518ee4e9f268c87478d9692f2a414fdd"
EXPECTED_CONF_SEMANTIC_SHA = "4c3a8afd917b1c3cd3c6f9de37af331c141d1e2fac314ca2e89ca085d09fae17"
EXPECTED_DEV_N = 400
EXPECTED_CONF_N = 150
EXPECTED_OOS_N = 278
EXPECTED_WARMUP_N = 122
EXPECTED_FOLD_N = {"F1": 68, "F2": 63, "F3": 87, "F4": 60}

AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
RESULT_HEADERS = ["id","matchDate","Country","League","Season","homeTeam","awayTeam","referee","FTHG","FTAG","FTR"]
PAGES = {
    "EPL": "https://footiqo.com/database/leagues/england-premier-league/",
    "LALIGA": "https://footiqo.com/database/leagues/spain-laliga/",
    "BUNDESLIGA": "https://footiqo.com/database/leagues/germany-bundesliga/",
    "SERIEA": "https://footiqo.com/database/leagues/italy-serie-a/",
    "LIGUE1": "https://footiqo.com/database/leagues/france-ligue-1/",
    "MLS": "https://footiqo.com/database/leagues/usa-mls/",
}
FOLDS = [
    ("F1", dt.date(2024, 9, 30), dt.date(2024, 10, 6)),
    ("F2", dt.date(2024, 10, 7), dt.date(2024, 10, 25)),
    ("F3", dt.date(2024, 10, 26), dt.date(2024, 11, 3)),
    ("F4", dt.date(2024, 11, 4), dt.date(2024, 11, 23)),
]
WARMUP_END = dt.date(2024, 9, 29)
BETA_PRIOR_SD = 0.25
BOOT_N = 5000
BOOT_SEED = 72018
MAX_POST_REQUESTS = 410

FEATURE_NAMES = [
    "home_own_xg_pm","home_opp_xg_pm","home_own_shots_pm","home_opp_shots_pm",
    "home_own_xg_per_shot","home_opp_xg_per_shot","home_own_highxg_pm","home_opp_highxg_pm",
    "away_own_xg_pm","away_opp_xg_pm","away_own_shots_pm","away_opp_shots_pm",
    "away_own_xg_per_shot","away_opp_xg_per_shot","away_own_highxg_pm","away_opp_highxg_pm",
]

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

def norm(x) -> str:
    if x is None:
        return ""
    s = html_lib.unescape(str(x)).strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def semantic_hash(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in rows:
        s = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        h.update(s.encode("utf-8"))
    return h.hexdigest()

def parse_provider_time(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(s).strip(), "%d-%m-%y %H:%M")
    except ValueError:
        return None

def parse_goal(x) -> int | None:
    try:
        v = int(str(x).strip())
    except Exception:
        return None
    return v if v >= 0 else None

def table_headers(t) -> list[str]:
    h = [norm(x.get_text(" ", strip=True)) for x in t.find_all("th")]
    if not h:
        tr = t.find("tr")
        if tr:
            h = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["th", "td"])]
    return h

def visible_seasons(t, headers: list[str]) -> list[str]:
    if "Season" not in headers:
        return []
    i = headers.index("Season")
    vals = []
    for tr in t.find_all("tr")[1:]:
        cells = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["td", "th"])]
        if len(cells) > i and cells[i] and cells[i] != "Season":
            vals.append(cells[i])
    return sorted(set(vals))

def start_year(s: str) -> int | None:
    m = re.match(r"\s*(\d{4})", s)
    return int(m.group(1)) if m else None

def resolve_historical_results_table(page_html: str):
    soup = BeautifulSoup(page_html, "html.parser")
    candidates = []
    for t in soup.find_all("table"):
        h = table_headers(t)
        if h != RESULT_HEADERS:
            continue
        raw_tid = str(t.get("data-wpdatatable_id", ""))
        if not raw_tid.isdigit():
            continue
        seasons = visible_seasons(t, h)
        yrs = [start_year(s) for s in seasons]
        yrs = [y for y in yrs if y is not None]
        if yrs:
            candidates.append((min(yrs), t, int(raw_tid), seasons))
    if not candidates:
        return None, None, []
    min_year = min(x[0] for x in candidates)
    historical = [x for x in candidates if x[0] == min_year]
    if len(historical) != 1:
        return None, None, []
    _, t, tid, seasons = historical[0]
    return t, tid, seasons

def id_payload(nonce: str, requested_id: str, season: str) -> dict[str, str]:
    body = {
        "draw": "1",
        "start": "0",
        "length": "10",
        "search[value]": "",
        "search[regex]": "false",
        NONCE_FIELD: nonce,
    }
    for i, h in enumerate(RESULT_HEADERS):
        body[f"columns[{i}][data]"] = str(i)
        body[f"columns[{i}][name]"] = h
        body[f"columns[{i}][searchable]"] = "true"
        body[f"columns[{i}][orderable]"] = "true"
        if h == "id":
            v = requested_id
        elif h == "Season":
            v = season
        else:
            v = ""
        body[f"columns[{i}][search][value]"] = v
        body[f"columns[{i}][search][regex]"] = "false"
    return body

def load_zero_label():
    if not all(p.exists() for p in (TARGET_PATH, DEV_IDS_PATH, CONF_IDS_PATH, N18B_SUMMARY)):
        raise RuntimeError("N18B2_ARTIFACT_MISSING")
    if sha256_file(DEV_IDS_PATH) != EXPECTED_DEV_IDS_SHA:
        raise RuntimeError("DEV400_ID_SHA_MISMATCH")
    if sha256_file(CONF_IDS_PATH) != EXPECTED_CONF_IDS_SHA:
        raise RuntimeError("CONF150_ID_SHA_MISMATCH")

    summary = json.loads(N18B_SUMMARY.read_text(encoding="utf-8"))
    if summary.get("status") != "PASS_N18B2_ZERO_LABEL_TARGET_MARKET_JOIN":
        raise RuntimeError(f"N18B2_STATUS_MISMATCH {summary.get('status')}")
    if int(summary.get("selected_rows", -1)) != 550 or int(summary.get("dev_rows", -1)) != 400 or int(summary.get("confirmation_rows", -1)) != 150:
        raise RuntimeError("N18B2_SPLIT_COUNT_MISMATCH")
    if int(summary.get("source_target_overlap", -1)) != 0:
        raise RuntimeError("N18B2_SOURCE_TARGET_OVERLAP")
    if int(summary.get("target_result_values_materialized", -1)) != 0:
        raise RuntimeError("N18B2_TARGET_VALUES_ALREADY_MATERIALIZED")

    rows = []
    with gzip.open(TARGET_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    if len(rows) != 550:
        raise RuntimeError(f"TARGET550_ROW_MISMATCH {len(rows)}")
    dev = [r for r in rows if r.get("split") == "DEVELOPMENT"]
    conf = [r for r in rows if r.get("split") == "CONFIRMATION_SEALED"]
    if len(dev) != EXPECTED_DEV_N or len(conf) != EXPECTED_CONF_N:
        raise RuntimeError("TARGET550_SPLIT_FIELD_MISMATCH")
    if semantic_hash(dev) != EXPECTED_DEV_SEMANTIC_SHA:
        raise RuntimeError("DEV400_SEMANTIC_SHA_MISMATCH")
    if semantic_hash(conf) != EXPECTED_CONF_SEMANTIC_SHA:
        raise RuntimeError("CONF150_SEMANTIC_SHA_MISMATCH")

    dev_ids = [str(r["footiqo_id"]) for r in dev]
    conf_ids = [str(r["footiqo_id"]) for r in conf]
    if set(dev_ids) & set(conf_ids):
        raise RuntimeError("DEV_CONFIRM_ID_OVERLAP")
    if dev_ids != [x.strip() for x in DEV_IDS_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]:
        raise RuntimeError("DEV_ID_ORDER_MISMATCH")
    if conf_ids != [x.strip() for x in CONF_IDS_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]:
        raise RuntimeError("CONF_ID_ORDER_MISMATCH")

    for r in rows:
        if len(r.get("features16", [])) != 16:
            raise RuntimeError("FEATURE_COUNT_MISMATCH")
        if not (0.0 < float(r["q_over25"]) < 1.0):
            raise RuntimeError("Q_OVER25_INVALID")
        if not all(math.isfinite(float(x)) for x in r["features16"]):
            raise RuntimeError("FEATURE_NONFINITE")
    return dev, conf

def fetch_dev_results(dev: list[dict], conf: list[dict], audit: dict) -> dict[str, tuple[int, int]]:
    conf_ids = {str(r["footiqo_id"]) for r in conf}
    dev_ids = {str(r["footiqo_id"]) for r in dev}
    requested_ids: list[str] = []
    results: dict[str, tuple[int, int]] = {}
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-n18c",
        "Accept-Language": "en-US,en;q=0.9",
    })
    post_requests = 0
    source_stats = {}

    by_code = {code: [] for code in PAGES}
    for row in dev:
        by_code[row["source_code"]].append(row)

    for code, url in PAGES.items():
        targets = sorted(by_code[code], key=lambda r: (r["match_time_local"], int(r["footiqo_id"])))
        st = {"page_url": url, "authorized_dev_ids": len(targets), "post_requests": 0, "rows_materialized": 0}
        page = sess.get(url, timeout=45, allow_redirects=True)
        st["page_status"] = page.status_code
        if not (200 <= page.status_code < 300):
            raise RuntimeError(f"RESULT_PAGE_HTTP {code} {page.status_code}")

        table, tid, vis = resolve_historical_results_table(page.text)
        st["visible_seasons_selected_table"] = vis
        st["table_id"] = tid
        if table is None or tid is None:
            raise RuntimeError(f"RESULT_TABLE_PROTOCOL_DRIFT {code}")

        soup = BeautifulSoup(page.text, "html.parser")
        nonce_dom = f"wdtNonceFrontendServerSide_{tid}"
        nodes = [x for x in soup.find_all("input") if str(x.get("id","")) == nonce_dom and str(x.get("name","")) == nonce_dom]
        if len(nodes) != 1 or str(nodes[0].get("type","")).lower() != "hidden":
            raise RuntimeError(f"RESULT_NONCE_PROTOCOL_DRIFT {code}")
        nonce = str(nodes[0].get("value") or "")
        if not nonce:
            raise RuntimeError(f"RESULT_NONCE_EMPTY {code}")

        req_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://footiqo.com",
            "Referer": url,
        }

        for target in targets:
            rid = str(target["footiqo_id"])
            if rid not in dev_ids or rid in conf_ids:
                raise RuntimeError(f"UNAUTHORIZED_RESULT_REQUEST_ID {rid}")
            if post_requests >= MAX_POST_REQUESTS:
                raise RuntimeError("RESULT_POST_REQUEST_BUDGET_EXCEEDED")

            body = id_payload(nonce, rid, str(target["season"]))
            requested_ids.append(rid)
            post_requests += 1
            st["post_requests"] += 1
            try:
                rr = sess.post(
                    AJAX,
                    params={"action": ACTION, "table_id": str(tid)},
                    data=body,
                    headers=req_headers,
                    timeout=45,
                    allow_redirects=True,
                )
            finally:
                body[NONCE_FIELD] = "<redacted>"
            if not (200 <= rr.status_code < 300):
                raise RuntimeError(f"RESULT_AJAX_HTTP {code} {rid} {rr.status_code}")
            try:
                obj = rr.json()
            except Exception as exc:
                raise RuntimeError(f"RESULT_AJAX_NON_JSON {code} {rid}") from exc
            if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
                raise RuntimeError(f"RESULT_AJAX_SHAPE {code} {rid}")
            data = obj["data"]
            try:
                rf = int(obj.get("recordsFiltered"))
            except Exception as exc:
                raise RuntimeError(f"RESULT_FILTER_COUNT_SHAPE {code} {rid}") from exc
            if rf != 1 or len(data) != 1:
                raise RuntimeError(f"RESULT_ID_FILTER_NOT_UNIQUE {code} {rid} rf={rf} rows={len(data)}")
            row = data[0]
            if not isinstance(row, list) or len(row) != len(RESULT_HEADERS):
                raise RuntimeError(f"RESULT_ROW_SCHEMA_DRIFT {code} {rid}")

            returned_id = norm(row[0])
            if returned_id != rid:
                raise RuntimeError(f"RESULT_RETURNED_WRONG_ID requested={rid} got={returned_id}")
            if returned_id in conf_ids:
                raise RuntimeError(f"CONFIRMATION_ID_RETURNED {returned_id}")

            returned_time = parse_provider_time(norm(row[1]))
            expected_time = dt.datetime.fromisoformat(target["match_time_local"])
            if returned_time is None or returned_time != expected_time:
                raise RuntimeError(f"RESULT_TIME_IDENTITY_MISMATCH {code} {rid}")
            if norm(row[4]) != norm(target["season"]):
                raise RuntimeError(f"RESULT_SEASON_IDENTITY_MISMATCH {code} {rid}")
            if norm(row[5]) != norm(target["home_team"]) or norm(row[6]) != norm(target["away_team"]):
                raise RuntimeError(f"RESULT_TEAM_IDENTITY_MISMATCH {code} {rid}")

            hg = parse_goal(row[8])
            ag = parse_goal(row[9])
            if hg is None or ag is None:
                raise RuntimeError(f"RESULT_GOAL_PARSE_FAIL {code} {rid}")
            if rid in results:
                raise RuntimeError(f"RESULT_DUPLICATE_ID {rid}")
            results[rid] = (hg, ag)
            st["rows_materialized"] += 1

        nonce = ""
        source_stats[code] = st

    if len(requested_ids) != EXPECTED_DEV_N or len(set(requested_ids)) != EXPECTED_DEV_N:
        raise RuntimeError(f"DEV_RESULT_REQUEST_COUNT_MISMATCH {len(requested_ids)}")
    if set(requested_ids) != dev_ids:
        raise RuntimeError("DEV_RESULT_REQUEST_SET_MISMATCH")
    if set(requested_ids) & conf_ids:
        raise RuntimeError("CONFIRMATION_REQUESTED")
    if len(results) != EXPECTED_DEV_N:
        raise RuntimeError(f"DEV_RESULT_MATERIALIZATION_COUNT_MISMATCH {len(results)}")

    req_text = "\n".join(requested_ids) + "\n"
    audit.update({
        "result_table_post_requests": post_requests,
        "requested_dev_ids": len(requested_ids),
        "requested_dev_ids_sha256": hashlib.sha256(req_text.encode("utf-8")).hexdigest(),
        "confirmation_ids_requested": 0,
        "authorized_dev_result_rows_materialized": len(results),
        "authorized_dev_numeric_goal_values_materialized": len(results) * 2,
        "confirmation_numeric_goal_values_materialized": 0,
        "source_stats": source_stats,
    })
    return results

def poisson_over25(mu: float) -> float:
    return 1.0 - math.exp(-mu) * (1.0 + mu + 0.5 * mu * mu)

def market_mu(q: float) -> float:
    q = float(q)
    if not (0.0 < q < 1.0):
        raise ValueError("q outside (0,1)")
    return brentq(lambda m: poisson_over25(m) - q, 1e-8, 20.0, xtol=1e-12, rtol=1e-12, maxiter=200)

def nb2_logpmf(y: np.ndarray, mu: np.ndarray, log_alpha: float) -> np.ndarray:
    alpha = math.exp(float(log_alpha))
    n = 1.0 / alpha
    p = n / (n + mu)
    return (
        gammaln(y + n) - gammaln(n) - gammaln(y + 1.0)
        + n * np.log(p) + y * np.log1p(-p)
    )

def fit_b0(y: np.ndarray, mu_market: np.ndarray):
    def obj(theta):
        b0, loga = theta
        mu = mu_market * np.exp(b0)
        lp = nb2_logpmf(y, mu, loga)
        if not np.all(np.isfinite(lp)):
            return 1e100
        return float(-np.sum(lp))
    res = minimize(
        obj,
        np.array([0.0, -2.0], dtype=float),
        method="L-BFGS-B",
        bounds=[(-1.0, 1.0), (-8.0, 2.0)],
        options={"maxiter": 2000},
    )
    if not res.success or not np.all(np.isfinite(res.x)):
        raise RuntimeError(f"B0_OPTIMIZER_FAIL {res.message}")
    return res

def fit_c(y: np.ndarray, mu_market: np.ndarray, z: np.ndarray, b0_res):
    init = np.zeros(18, dtype=float)
    init[0] = float(b0_res.x[0])
    init[-1] = float(b0_res.x[1])
    penalty_scale = BETA_PRIOR_SD ** 2

    def obj(theta):
        b0 = theta[0]
        beta = theta[1:17]
        loga = theta[17]
        eta = b0 + z @ beta
        mu = mu_market * np.exp(eta)
        lp = nb2_logpmf(y, mu, loga)
        if not np.all(np.isfinite(lp)):
            return 1e100
        penalty = 0.5 * float(np.dot(beta, beta)) / penalty_scale
        return float(-np.sum(lp) + penalty)

    bounds = [(-1.0, 1.0)] + [(-1.0, 1.0)] * 16 + [(-8.0, 2.0)]
    res = minimize(
        obj,
        init,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 2000},
    )
    if not res.success or not np.all(np.isfinite(res.x)):
        raise RuntimeError(f"C_OPTIMIZER_FAIL {res.message}")
    return res

def nb2_collapsed_probs(mu: np.ndarray, log_alpha: float) -> np.ndarray:
    out = np.zeros((len(mu), 8), dtype=float)
    for t in range(7):
        y = np.full(len(mu), float(t))
        out[:, t] = np.exp(nb2_logpmf(y, mu, log_alpha))
    s = out[:, :7].sum(axis=1)
    tail = 1.0 - s
    if np.any(tail < -1e-10) or np.any(~np.isfinite(out)):
        raise RuntimeError("NB2_PROBABILITY_NUMERICAL_FAILURE")
    tail = np.maximum(tail, 0.0)
    out[:, 7] = tail
    if np.max(np.abs(out.sum(axis=1) - 1.0)) > 1e-10:
        raise RuntimeError("NB2_PROBABILITY_CONSERVATION_FAIL")
    if np.any(out < 0.0) or np.any(out > 1.0 + 1e-12):
        raise RuntimeError("NB2_PROBABILITY_RANGE_FAIL")
    return out

def row_metrics(p: np.ndarray, y8: np.ndarray):
    eps = 1e-15
    ll = -np.log(np.clip(p[np.arange(len(y8)), y8], eps, 1.0))
    one = np.eye(8)[y8]
    brier = np.sum((p - one) ** 2, axis=1)
    cdfp = np.cumsum(p, axis=1)[:, :-1]
    cdfy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.sum((cdfp - cdfy) ** 2, axis=1) / 7.0
    top1 = np.argmax(p, axis=1) == y8
    top3 = np.array([y8[i] in np.argsort(p[i])[-3:] for i in range(len(y8))], dtype=bool)
    return ll, brier, rps, top1, top3

def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    summary = {
        "project": "football3",
        "experiment": "C072-N18C",
        "schema": SCHEMA,
        "evidence_class": "DEVELOPMENT",
        "started_at_utc": started,
        "parent_head": "847e775fec88a9852ff037b865b7cdb95f929ae3",
        "scientific_root": "e3e73c998020beef585cc459a69ea5b73b44ddb3",
        "C070F_confirmation1597_opened": False,
        "sealed_reserves_opened": False,
        "C073_C077_scientific_results_used": False,
        "confirmation150_opened": False,
        "confirmation_ids_requested": 0,
        "confirmation_numeric_goal_values_materialized": 0,
        "model_family": "NB2 Gamma-Poisson full support",
        "market_anchor": "de-vigged closing O/U2.5 q_over25 -> Poisson implied mu_market",
        "candidate_feature_count": 16,
        "beta_prior_sd": BETA_PRIOR_SD,
        "bootstrap_reps": BOOT_N,
        "bootstrap_seed": BOOT_SEED,
    }

    dev, conf = load_zero_label()
    summary["dev400_semantic_sha256"] = semantic_hash(dev)
    summary["confirmation150_semantic_sha256"] = semantic_hash(conf)
    summary["dev400_ids_sha256"] = sha256_file(DEV_IDS_PATH)
    summary["confirmation150_ids_sha256"] = sha256_file(CONF_IDS_PATH)

    dev.sort(key=lambda r: (r["match_time_local"], int(r["footiqo_id"])))
    warmup_n = sum(dt.datetime.fromisoformat(r["match_time_local"]).date() <= WARMUP_END for r in dev)
    if warmup_n != EXPECTED_WARMUP_N:
        raise RuntimeError(f"WARMUP_ZERO_LABEL_COUNT_MISMATCH {warmup_n}")
    for name, start, end in FOLDS:
        n = sum(start <= dt.datetime.fromisoformat(r["match_time_local"]).date() <= end for r in dev)
        if n != EXPECTED_FOLD_N[name]:
            raise RuntimeError(f"FOLD_ZERO_LABEL_COUNT_MISMATCH {name} {n}")

    results = fetch_dev_results(dev, conf, summary)
    summary["confirmation150_opened"] = False

    for r in dev:
        rid = str(r["footiqo_id"])
        hg, ag = results[rid]
        r["_hg"] = hg
        r["_ag"] = ag
        r["_total"] = hg + ag
        r["_date"] = dt.datetime.fromisoformat(r["match_time_local"]).date()
        r["_mu_market"] = market_mu(float(r["q_over25"]))

    oos_records = []
    fold_results = []
    optimizer_audit = []

    for fold_name, test_start, test_end in FOLDS:
        train = [r for r in dev if r["_date"] < test_start]
        test = [r for r in dev if test_start <= r["_date"] <= test_end]
        if len(test) != EXPECTED_FOLD_N[fold_name]:
            raise RuntimeError(f"FOLD_TEST_COUNT_DRIFT {fold_name}")
        if not train:
            raise RuntimeError(f"FOLD_EMPTY_TRAIN {fold_name}")

        Xtr = np.asarray([r["features16"] for r in train], dtype=float)
        Xte = np.asarray([r["features16"] for r in test], dtype=float)
        mean = Xtr.mean(axis=0)
        sd = Xtr.std(axis=0, ddof=0)
        if np.any(~np.isfinite(mean)) or np.any(~np.isfinite(sd)) or np.any(sd < 1e-12):
            raise RuntimeError(f"FEATURE_SCALER_DEGENERATE {fold_name}")
        Ztr = (Xtr - mean) / sd
        Zte = (Xte - mean) / sd

        ytr = np.asarray([r["_total"] for r in train], dtype=float)
        yte_exact = np.asarray([r["_total"] for r in test], dtype=int)
        yte8 = np.minimum(yte_exact, 7)
        mtr = np.asarray([r["_mu_market"] for r in train], dtype=float)
        mte = np.asarray([r["_mu_market"] for r in test], dtype=float)

        b0 = fit_b0(ytr, mtr)
        cand = fit_c(ytr, mtr, Ztr, b0)

        mu_b = mte * np.exp(float(b0.x[0]))
        mu_c = mte * np.exp(float(cand.x[0]) + Zte @ cand.x[1:17])
        pb = nb2_collapsed_probs(mu_b, float(b0.x[1]))
        pc = nb2_collapsed_probs(mu_c, float(cand.x[17]))

        b_ll, b_br, b_rps, b_t1, b_t3 = row_metrics(pb, yte8)
        c_ll, c_br, c_rps, c_t1, c_t3 = row_metrics(pc, yte8)

        fold_summary = {
            "fold": fold_name,
            "train_rows": len(train),
            "test_rows": len(test),
            "test_start": test_start.isoformat(),
            "test_end": test_end.isoformat(),
            "baseline_logloss": float(b_ll.mean()),
            "candidate_logloss": float(c_ll.mean()),
            "dlogloss": float((c_ll - b_ll).mean()),
            "baseline_brier": float(b_br.mean()),
            "candidate_brier": float(c_br.mean()),
            "dbrier": float((c_br - b_br).mean()),
            "baseline_rps": float(b_rps.mean()),
            "candidate_rps": float(c_rps.mean()),
            "drps": float((c_rps - b_rps).mean()),
        }
        fold_results.append(fold_summary)
        optimizer_audit.append({
            "fold": fold_name,
            "b0_success": bool(b0.success),
            "c_success": bool(cand.success),
            "b0_iterations": int(getattr(b0, "nit", -1)),
            "c_iterations": int(getattr(cand, "nit", -1)),
            "b0_intercept": float(b0.x[0]),
            "b0_alpha": float(math.exp(b0.x[1])),
            "c_intercept": float(cand.x[0]),
            "c_alpha": float(math.exp(cand.x[17])),
            "c_beta": [float(x) for x in cand.x[1:17]],
        })

        for i, r in enumerate(test):
            rec = {
                "fold": fold_name,
                "footiqo_id": int(r["footiqo_id"]),
                "match_time_local": r["match_time_local"],
                "source_code": r["source_code"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "total_goals": int(yte_exact[i]),
                "target8": int(yte8[i]),
                "mu_market": float(mte[i]),
                "mu_b0": float(mu_b[i]),
                "mu_c": float(mu_c[i]),
                "b0_ll": float(b_ll[i]),
                "c_ll": float(c_ll[i]),
                "d_ll": float(c_ll[i] - b_ll[i]),
                "b0_brier": float(b_br[i]),
                "c_brier": float(c_br[i]),
                "d_brier": float(c_br[i] - b_br[i]),
                "b0_rps": float(b_rps[i]),
                "c_rps": float(c_rps[i]),
                "d_rps": float(c_rps[i] - b_rps[i]),
                "b0_top1": bool(b_t1[i]),
                "c_top1": bool(c_t1[i]),
                "b0_top3": bool(b_t3[i]),
                "c_top3": bool(c_t3[i]),
                "b0_probs": [float(x) for x in pb[i]],
                "c_probs": [float(x) for x in pc[i]],
            }
            oos_records.append(rec)

    if len(oos_records) != EXPECTED_OOS_N:
        raise RuntimeError(f"OOS_COUNT_MISMATCH {len(oos_records)}")

    d_ll = np.asarray([r["d_ll"] for r in oos_records], dtype=float)
    d_br = np.asarray([r["d_brier"] for r in oos_records], dtype=float)
    d_rps = np.asarray([r["d_rps"] for r in oos_records], dtype=float)
    b_ll = np.asarray([r["b0_ll"] for r in oos_records], dtype=float)
    c_ll = np.asarray([r["c_ll"] for r in oos_records], dtype=float)
    b_br = np.asarray([r["b0_brier"] for r in oos_records], dtype=float)
    c_br = np.asarray([r["c_brier"] for r in oos_records], dtype=float)
    b_rps = np.asarray([r["b0_rps"] for r in oos_records], dtype=float)
    c_rps = np.asarray([r["c_rps"] for r in oos_records], dtype=float)

    rng = np.random.default_rng(BOOT_SEED)
    idx = rng.integers(0, len(d_ll), size=(BOOT_N, len(d_ll)))
    boot = d_ll[idx].mean(axis=1)
    ci90 = np.percentile(boot, [5.0, 95.0])

    league_results = []
    for code in sorted(PAGES):
        vals = [r["d_ll"] for r in oos_records if r["source_code"] == code]
        if not vals:
            raise RuntimeError(f"LEAGUE_OOS_EMPTY {code}")
        league_results.append({
            "source_code": code,
            "oos_rows": len(vals),
            "dlogloss": float(np.mean(vals)),
        })

    fold_wins = sum(x["dlogloss"] < 0.0 for x in fold_results)
    league_wins = sum(x["dlogloss"] < 0.0 for x in league_results)
    pooled_dll = float(d_ll.mean())
    pooled_dbr = float(d_br.mean())
    pooled_drps = float(d_rps.mean())

    gates = {
        "pooled_dlogloss_lt0": pooled_dll < 0.0,
        "bootstrap90_upper_lt0": float(ci90[1]) < 0.0,
        "pooled_dbrier_le0": pooled_dbr <= 0.0,
        "pooled_drps_le0": pooled_drps <= 0.0,
        "fold_wins_ge3of4": fold_wins >= 3,
        "league_wins_ge4of6": league_wins >= 4,
        "probability_identity_boundary_audits": True,
        "optimizer_all_folds_success": all(x["b0_success"] and x["c_success"] for x in optimizer_audit),
    }
    passed = all(gates.values())
    breakthrough = bool(
        passed
        and pooled_dll <= -0.010
        and pooled_drps <= -0.001
        and fold_wins == 4
    )
    verdict = (
        "C072N18C_MARKET_ANCHORED_NB2_DEVELOPMENT_PASS"
        if passed
        else "C072N18C_MARKET_ANCHORED_NB2_DEVELOPMENT_PARK"
    )

    summary.update({
        "finished_at_utc": utc_now(),
        "development_rows_opened": 400,
        "warmup_train_only_rows": EXPECTED_WARMUP_N,
        "strict_oos_rows": len(oos_records),
        "fold_results": fold_results,
        "league_results": league_results,
        "fold_logloss_wins": int(fold_wins),
        "league_logloss_wins": int(league_wins),
        "baseline_logloss": float(b_ll.mean()),
        "candidate_logloss": float(c_ll.mean()),
        "dlogloss": pooled_dll,
        "baseline_brier": float(b_br.mean()),
        "candidate_brier": float(c_br.mean()),
        "dbrier": pooled_dbr,
        "baseline_rps": float(b_rps.mean()),
        "candidate_rps": float(c_rps.mean()),
        "drps": pooled_drps,
        "baseline_top1": float(np.mean([r["b0_top1"] for r in oos_records])),
        "candidate_top1": float(np.mean([r["c_top1"] for r in oos_records])),
        "baseline_top3": float(np.mean([r["b0_top3"] for r in oos_records])),
        "candidate_top3": float(np.mean([r["c_top3"] for r in oos_records])),
        "bootstrap90_dlogloss": [float(ci90[0]), float(ci90[1])],
        "bootstrap_mean_dlogloss": float(boot.mean()),
        "bootstrap_p_dlogloss_lt0": float(np.mean(boot < 0.0)),
        "optimizer_audit": optimizer_audit,
        "pass_gates": gates,
        "breakthrough_screen_pass": breakthrough,
        "terminal_verdict": verdict,
        "confirmation150_opened": False,
        "confirmation_ids_requested": 0,
        "confirmation_numeric_goal_values_materialized": 0,
    })

    summary_path = OUTDIR / "c072n18c_summary.json"
    pred_path = OUTDIR / "c072n18c_oos_predictions.csv"
    label_path = OUTDIR / "c072n18c_dev400_labels.csv"

    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    with pred_path.open("w", encoding="utf-8", newline="") as f:
        fields = [
            "fold","footiqo_id","match_time_local","source_code","home_team","away_team","total_goals","target8",
            "mu_market","mu_b0","mu_c","b0_ll","c_ll","d_ll","b0_brier","c_brier","d_brier",
            "b0_rps","c_rps","d_rps","b0_top1","c_top1","b0_top3","c_top3",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in oos_records:
            w.writerow({k: r[k] for k in fields})
    with label_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["footiqo_id","source_code","match_time_local","home_team","away_team","FTHG","FTAG","total_goals"])
        for r in dev:
            w.writerow([r["footiqo_id"], r["source_code"], r["match_time_local"], r["home_team"], r["away_team"], r["_hg"], r["_ag"], r["_total"]])

    summary["predictions_sha256"] = sha256_file(pred_path)
    summary["dev400_labels_sha256"] = sha256_file(label_path)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "terminal_verdict": verdict,
        "breakthrough_screen_pass": breakthrough,
        "strict_oos_rows": len(oos_records),
        "baseline_logloss": summary["baseline_logloss"],
        "candidate_logloss": summary["candidate_logloss"],
        "dlogloss": summary["dlogloss"],
        "dbrier": summary["dbrier"],
        "drps": summary["drps"],
        "bootstrap90_dlogloss": summary["bootstrap90_dlogloss"],
        "fold_logloss_wins": fold_wins,
        "league_logloss_wins": league_wins,
        "confirmation150_opened": False,
        "confirmation_ids_requested": 0,
    }, indent=2, ensure_ascii=False, sort_keys=True))

if __name__ == "__main__":
    main()
