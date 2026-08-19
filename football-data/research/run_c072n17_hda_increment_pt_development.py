#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import math
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SCHEMA = "C072N17_HDA_INCREMENT_PT_DEVELOPMENT_V1"
INPUT_SHA256 = "b5c988c77f7f0855481297eb5878e52742a94145bc35499f29c8ac893a596997"
INPUT_IDENTITY_SHA256 = "65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559"
INPUT_ROWS = 2000
DEV_ROWS_FROZEN = 1734
RESERVE_ROWS_FROZEN = 266
ARTIFACT_ID = 9368768296
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
PAGE_SIZE = 500
MAX_POST_REQUESTS = 80
RESULT_HEADERS = ["id","matchDate","Country","League","Season","homeTeam","awayTeam","referee","FTHG","FTAG","FTR"]
PAGES = {
    "TR": "https://footiqo.com/database/leagues/turkey-super-lig/",
    "GR": "https://footiqo.com/database/leagues/greece-super-league/",
    "BR": "https://footiqo.com/database/leagues/brazil-serie-a/",
    "MLS": "https://footiqo.com/database/leagues/usa-mls/",
}
DEV_SEASONS = {
    "BR": [str(y) for y in range(2015, 2025)],
    "MLS": [str(y) for y in range(2015, 2025)],
    "GR": ["2018/2019","2019/2020","2020/2021","2021/2022","2022/2023","2023/2024"],
    "TR": ["2015/2016","2016/2017","2017/2018","2018/2019","2019/2020","2020/2021","2021/2022","2022/2023","2023/2024"],
}
TEST_YEARS = [2020, 2021, 2022, 2023, 2024]
OU_PAIRS = [("O05","U05"),("O15","U15"),("O25","U25"),("O35","U35"),("O45","U45")]
BASE_NUM = ["ou05_logit","ou15_logit","ou25_logit","ou35_logit","ou45_logit"]
CAND_NUM = BASE_NUM + ["hda_gap","hda_draw"]
PRICE_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
OUT_DIR = Path("football-data/research/c072n17")
SUMMARY_PATH = OUT_DIR / "summary.json"
PRED_PATH = OUT_DIR / "oos_predictions.csv"
JOIN_PATH = OUT_DIR / "development_join_audit.csv"


def norm(x) -> str:
    if x is None:
        return ""
    s = html_lib.unescape(str(x)).strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def price(x) -> float | None:
    m = PRICE_RE.search(norm(x))
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def resolve_historical_overview(page_html: str):
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
    if len(candidates) < 2:
        return None, None, []
    min_year = min(x[0] for x in candidates)
    historical = [x for x in candidates if x[0] == min_year]
    if len(historical) != 1:
        return None, None, []
    _, t, tid, seasons = historical[0]
    return t, tid, seasons


def payload(nonce: str, start: int, season: str) -> dict[str, str]:
    body = {"draw":"1","start":str(start),"length":str(PAGE_SIZE),"search[value]":"","search[regex]":"false",NONCE_FIELD:nonce}
    for i, h in enumerate(RESULT_HEADERS):
        body[f"columns[{i}][data]"] = str(i)
        body[f"columns[{i}][name]"] = h
        body[f"columns[{i}][searchable]"] = "true"
        body[f"columns[{i}][orderable]"] = "true"
        body[f"columns[{i}][search][value]"] = season if h == "Season" else ""
        body[f"columns[{i}][search][regex]"] = "false"
    return body


def as_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def read_input(path: Path) -> pd.DataFrame:
    if sha256_file(path) != INPUT_SHA256:
        raise RuntimeError("N16R1 selected CSV SHA mismatch")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if len(df) != INPUT_ROWS or df["identity_sha256"].nunique() != INPUT_ROWS:
        raise RuntimeError("N16R1 selected row/identity count mismatch")
    concat = "\n".join(df["identity_sha256"].tolist()) + "\n"
    if hashlib.sha256(concat.encode("utf-8")).hexdigest() != INPUT_IDENTITY_SHA256:
        raise RuntimeError("N16R1 ordered identity SHA mismatch")
    return df


def split_dev_reserve(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    allowed = {(code, season) for code, seasons in DEV_SEASONS.items() for season in seasons}
    mask = [(r.sourceCode, r.Season) in allowed for r in df.itertuples(index=False)]
    dev = df.loc[mask].copy()
    reserve = df.loc[[not x for x in mask]].copy()
    if len(dev) != DEV_ROWS_FROZEN or len(reserve) != RESERVE_ROWS_FROZEN:
        raise RuntimeError(f"frozen split mismatch dev={len(dev)} reserve={len(reserve)}")
    return dev, reserve


def fetch_authorized_results(dev: pd.DataFrame, summary: dict) -> pd.DataFrame:
    sess = requests.Session()
    sess.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-n17","Accept-Language":"en-US,en;q=0.9"})
    rows_all = []
    source_stats = {}
    fatal = False

    for code, url in PAGES.items():
        st = {"page_url":url,"seasons_requested":[],"post_requests":0,"rows":0}
        try:
            page = sess.get(url, timeout=45, allow_redirects=True)
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

        table, tid, vis = resolve_historical_overview(page.text)
        st["visible_seasons_selected_table"] = vis
        if table is None or tid is None:
            st["error"] = "OVERVIEW_HISTORICAL_TABLE_PROTOCOL_DRIFT"
            source_stats[code] = st
            fatal = True
            continue
        st["table_id"] = tid

        soup = BeautifulSoup(page.text, "html.parser")
        nonce_dom = f"wdtNonceFrontendServerSide_{tid}"
        nodes = [x for x in soup.find_all("input") if str(x.get("id","")) == nonce_dom and str(x.get("name","")) == nonce_dom]
        if len(nodes) != 1 or str(nodes[0].get("type","" )).lower() != "hidden":
            st["error"] = "NONCE_PROTOCOL_DRIFT"
            source_stats[code] = st
            fatal = True
            continue
        nonce = str(nodes[0].get("value") or "")
        if not nonce:
            st["error"] = "NONCE_EMPTY"
            source_stats[code] = st
            fatal = True
            continue

        req_headers = {"Accept":"application/json, text/javascript, */*; q=0.01","Content-Type":"application/x-www-form-urlencoded; charset=UTF-8","X-Requested-With":"XMLHttpRequest","Origin":"https://footiqo.com","Referer":url}
        code_rows = []
        wanted_ids = set(dev.loc[dev["sourceCode"] == code, "id"].astype(str))
        for season in DEV_SEASONS[code]:
            st["seasons_requested"].append(season)
            start = 0
            expected = None
            while True:
                if summary["result_table_post_requests"] >= MAX_POST_REQUESTS:
                    st["error"] = "POST_REQUEST_BUDGET_EXCEEDED"
                    fatal = True
                    break
                body = payload(nonce, start, season)
                summary["result_table_post_requests"] += 1
                st["post_requests"] += 1
                try:
                    rr = sess.post(AJAX, params={"action":ACTION,"table_id":str(tid)}, data=body, headers=req_headers, timeout=45, allow_redirects=True)
                except Exception as e:
                    st["error"] = f"AJAX_REQUEST:{type(e).__name__}"
                    fatal = True
                    body[NONCE_FIELD] = "<redacted>"
                    break
                finally:
                    body[NONCE_FIELD] = "<redacted>"
                if not (200 <= rr.status_code < 300):
                    st["error"] = "AJAX_HTTP"
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
                if expected is None:
                    expected = rf
                    if rf < 0 or rf > 1000:
                        st["error"] = "SEASON_FILTER_COUNT_OUT_OF_BOUNDS"
                        fatal = True
                        break
                elif rf != expected:
                    st["error"] = "SEASON_FILTER_COUNT_DRIFT"
                    fatal = True
                    break
                if any(not isinstance(row, list) or len(row) != len(RESULT_HEADERS) for row in data):
                    st["error"] = "ROW_SCHEMA_DRIFT"
                    fatal = True
                    break
                for row in data:
                    rid = norm(row[0])
                    season_value = norm(row[4])
                    if season_value != season:
                        st["error"] = "UNAUTHORIZED_SEASON_RETURNED"
                        fatal = True
                        break
                    if rid not in wanted_ids:
                        summary["transported_nonselected_rows_labels_not_decoded"] += 1
                        continue
                    mapped = {RESULT_HEADERS[i]: norm(row[i]) for i in range(10)}
                    mapped["sourceCode"] = code
                    code_rows.append(mapped)
                if fatal and st.get("error") == "UNAUTHORIZED_SEASON_RETURNED":
                    break
                start += len(data)
                if start >= rf:
                    break
                if not data:
                    st["error"] = "EMPTY_PAGE_BEFORE_FILTERED_COUNT"
                    fatal = True
                    break
            if fatal:
                break

        nonce = ""
        st["rows"] = len(code_rows)
        source_stats[code] = st
        rows_all.extend(code_rows)

    summary["source_stats"] = source_stats
    if fatal:
        summary["source_fetch_fatal"] = True
        return pd.DataFrame(columns=["sourceCode"] + RESULT_HEADERS[:10])
    res = pd.DataFrame(rows_all)
    if res.empty:
        return res
    summary["authorized_dev_result_rows_materialized"] = int(len(res))
    summary["authorized_dev_result_values_materialized"] = int(len(res) * 2)
    return res


def parse_goal(x) -> int | None:
    try:
        v = int(str(x).strip())
    except Exception:
        return None
    return v if v >= 0 else None


def audit_join(dev: pd.DataFrame, results: pd.DataFrame, summary: dict) -> pd.DataFrame:
    if results.empty:
        summary["join_rows"] = 0
        summary["join_coverage"] = 0.0
        return pd.DataFrame()
    dup = int(results.duplicated(["sourceCode","id"], keep=False).sum())
    summary["result_duplicate_sourcecode_id_rows"] = dup
    if dup:
        return pd.DataFrame()
    keep = ["sourceCode","id","matchDate","Country","League","Season","homeTeam","awayTeam","FTHG","FTAG"]
    joined = dev.merge(results[keep], on=["sourceCode","id"], how="left", suffixes=("_odds","_result"), validate="one_to_one")
    identity_ok = ((joined["Season_odds"] == joined["Season_result"]) & (joined["matchDate_odds"] == joined["matchDate_result"]) & (joined["homeTeam_odds"] == joined["homeTeam_result"]) & (joined["awayTeam_odds"] == joined["awayTeam_result"]))
    joined["identity_exact"] = identity_ok.fillna(False)
    joined["hg"] = joined["FTHG"].map(parse_goal)
    joined["ag"] = joined["FTAG"].map(parse_goal)
    complete = joined["identity_exact"] & joined["hg"].notna() & joined["ag"].notna()
    summary["join_rows"] = int(complete.sum())
    summary["join_coverage"] = float(complete.mean())
    summary["identity_mismatch_rows"] = int((~joined["identity_exact"] & joined["FTHG"].notna()).sum())
    audit_cols = ["identity_sha256","sourceCode","id","Season_odds","matchDate_odds","homeTeam_odds","awayTeam_odds","identity_exact","FTHG","FTAG"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    joined[audit_cols].to_csv(JOIN_PATH, index=False)
    return joined.loc[complete].copy()


def match_year(s: str) -> int:
    s = norm(s)
    for fmt in ("%d-%m-%y %H:%M","%d-%m-%Y %H:%M","%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).year
        except ValueError:
            pass
    raise ValueError(f"unparsed matchDate {s!r}")


def safe_logit(p: float | None) -> float:
    if p is None or not math.isfinite(p):
        return np.nan
    q = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(q / (1 - q))


def build_features(joined: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in joined.to_dict("records"):
        z = {"league": r["sourceCode"]}
        for (oc, uc), name in zip(OU_PAIRS, BASE_NUM):
            o, u = price(r.get(oc)), price(r.get(uc))
            p = None if o is None or u is None else (1/o) / ((1/o) + (1/u))
            z[name] = safe_logit(p)
        h, d, a = price(r.get("H")), price(r.get("D")), price(r.get("A"))
        if h is None or d is None or a is None:
            z["hda_gap"] = np.nan
            z["hda_draw"] = np.nan
        else:
            raw = np.array([1/h, 1/d, 1/a], dtype=float)
            ph, pd_, pa = raw / raw.sum()
            z["hda_gap"] = math.log(ph / pa)
            z["hda_draw"] = math.log(pd_ / math.sqrt(ph * pa))
        rows.append(z)
    return pd.DataFrame(rows, index=joined.index)


def pipeline(num_cols: list[str]) -> Pipeline:
    prep = ColumnTransformer([
        ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), num_cols),
        ("league", OneHotEncoder(handle_unknown="ignore"), ["league"]),
    ])
    return Pipeline([("prep", prep), ("clf", LogisticRegression(C=0.1, solver="lbfgs", max_iter=3000))])


def predict_full(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    classes = model.named_steps["clf"].classes_.astype(int)
    out = np.zeros((len(X), 8), dtype=float)
    for j, c in enumerate(classes):
        out[:, c] = raw[:, j]
    return out


def row_metrics(p: np.ndarray, y: np.ndarray):
    eps = 1e-15
    ll = -np.log(np.clip(p[np.arange(len(y)), y], eps, 1.0))
    one = np.eye(8)[y]
    brier = np.sum((p - one) ** 2, axis=1)
    cdfp = np.cumsum(p, axis=1)[:, :-1]
    cdfy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.sum((cdfp - cdfy) ** 2, axis=1) / 7.0
    top1 = np.argmax(p, axis=1) == y
    top3 = np.array([y[i] in np.argsort(p[i])[-3:] for i in range(len(y))], dtype=bool)
    return ll, brier, rps, top1, top3


def aggregate(ll, brier, rps, top1, top3) -> dict:
    return {"n":int(len(ll)),"logloss":float(np.mean(ll)),"brier":float(np.mean(brier)),"rps":float(np.mean(rps)),"top1":float(np.mean(top1)),"top3":float(np.mean(top3))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema":SCHEMA,"project_line":"football3","classification":"DEVELOPMENT_REPLICATION_NOT_CONFIRMATION",
        "parent_branch":"football3/c072n16r1-new2000-footiqo-protocol-correction-20260819","parent_head":"271578d8f9e33a5350b8b8c36bf03423a64de41d","n16r1_artifact_id":ARTIFACT_ID,
        "result_table_post_requests":0,"authorized_dev_result_rows_materialized":0,"authorized_dev_result_values_materialized":0,
        "transported_nonselected_rows_labels_not_decoded":0,"nonselected_target_values_decoded":0,"result_target_columns_decoded":["FTHG","FTAG"],
        "later_reserve_rows":RESERVE_ROWS_FROZEN,"later_reserve_target_values_materialized":0,
        "C073_C077_scientific_results_used":False,"cross_project_consumption_metadata_used_only":True,"C070F_confirmation1597_opened":False,
        "aleague_men_2025_26_target_opened":False,"aleague_women_2025_26_target_opened":False,"BTTS_feature_used":False,
        "model_fit_count":0,"model_score_count":0,"formal_weight":0,"bootstrap_reps":3000,"bootstrap_seed":72017,
        "target":"T=min(FTHG+FTAG,7)",
        "baseline":"five closing de-vig OU0.5..4.5 logits + league; median impute + scale + multinomial LR C=0.1",
        "candidate":"baseline + hda_gap=log(pH/pA) + hda_draw=log(pD/sqrt(pH*pA)); de-vig closing 1X2",
    }
    try:
        full = read_input(Path(args.input))
        dev, reserve = split_dev_reserve(full)
    except Exception as e:
        summary["terminal"] = "C072N17_INPUT_OR_SPLIT_STOP"
        summary["error"] = f"{type(e).__name__}:{e}"
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    summary["input_rows"] = int(len(full))
    summary["development_rows_frozen"] = int(len(dev))
    summary["reserve_rows_verified"] = int(len(reserve))
    summary["development_source_counts"] = {k:int(v) for k,v in dev["sourceCode"].value_counts().sort_index().items()}
    summary["reserve_source_counts"] = {k:int(v) for k,v in reserve["sourceCode"].value_counts().sort_index().items()}

    results = fetch_authorized_results(dev, summary)
    joined = audit_join(dev, results, summary)
    pre_gates = {
        "input_exact_2000":len(full)==INPUT_ROWS,"dev_exact_1734":len(dev)==DEV_ROWS_FROZEN,"reserve_exact_266":len(reserve)==RESERVE_ROWS_FROZEN,
        "reserve_target_values_zero":summary["later_reserve_target_values_materialized"]==0,"nonselected_target_values_zero":summary["nonselected_target_values_decoded"]==0,
        "source_fetch_not_fatal":not summary.get("source_fetch_fatal",False),"result_duplicate_rows_zero":summary.get("result_duplicate_sourcecode_id_rows",0)==0,
        "identity_mismatch_zero":summary.get("identity_mismatch_rows",0)==0,"join_coverage_ge_99pct":summary.get("join_coverage",0.0)>=0.99,
        "quarantine_science_unused":summary["C073_C077_scientific_results_used"] is False,
        "sealed_assets_unopened":not summary["C070F_confirmation1597_opened"] and not summary["aleague_men_2025_26_target_opened"] and not summary["aleague_women_2025_26_target_opened"],
    }
    summary["pre_model_gates"] = pre_gates
    if not all(pre_gates.values()):
        summary["terminal"] = "C072N17_TARGET_JOIN_OR_BOUNDARY_STOP"
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    joined["year"] = joined["matchDate_odds"].map(match_year)
    joined["T"] = np.minimum(joined["hg"].astype(int) + joined["ag"].astype(int), 7)
    X = build_features(joined)
    pred_rows = []
    fold_summary = {}
    pooled_idx, pooled_y, pooled_pb, pooled_pc = [], [], [], []

    for test_year in TEST_YEARS:
        tr = joined["year"] < test_year
        te = joined["year"] == test_year
        if tr.sum() == 0 or te.sum() == 0:
            fold_summary[str(test_year)] = {"error":"EMPTY_TRAIN_OR_TEST","train_n":int(tr.sum()),"test_n":int(te.sum())}
            continue
        ytr = joined.loc[tr,"T"].to_numpy(dtype=int)
        yte = joined.loc[te,"T"].to_numpy(dtype=int)
        xb, xc = pipeline(BASE_NUM), pipeline(CAND_NUM)
        xb.fit(X.loc[tr,["league"]+BASE_NUM], ytr)
        xc.fit(X.loc[tr,["league"]+CAND_NUM], ytr)
        summary["model_fit_count"] += 2
        pb = predict_full(xb, X.loc[te,["league"]+BASE_NUM])
        pc = predict_full(xc, X.loc[te,["league"]+CAND_NUM])
        summary["model_score_count"] += 2
        lb, bb, rb, t1b, t3b = row_metrics(pb, yte)
        lc, bc, rc, t1c, t3c = row_metrics(pc, yte)
        mb, mc = aggregate(lb,bb,rb,t1b,t3b), aggregate(lc,bc,rc,t1c,t3c)
        fold_summary[str(test_year)] = {"train_n":int(tr.sum()),"test_n":int(te.sum()),"baseline":mb,"candidate":mc,"delta":{k:float(mc[k]-mb[k]) for k in ("logloss","brier","rps","top1","top3")}}
        teidx = joined.index[te].tolist()
        pooled_idx.extend(teidx); pooled_y.extend(yte.tolist()); pooled_pb.append(pb); pooled_pc.append(pc)
        for j, idx in enumerate(teidx):
            pred_rows.append({"identity_sha256":joined.loc[idx,"identity_sha256"],"sourceCode":joined.loc[idx,"sourceCode"],"year":test_year,"T":int(yte[j]),"baseline_true_prob":float(pb[j,yte[j]]),"candidate_true_prob":float(pc[j,yte[j]])})

    summary["folds"] = fold_summary
    if len(pooled_y) == 0 or len(fold_summary) != 5 or any("error" in x for x in fold_summary.values()):
        summary["terminal"] = "C072N17_OOS_FOLD_STOP"
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    y = np.array(pooled_y,dtype=int); pb = np.vstack(pooled_pb); pc = np.vstack(pooled_pc)
    lb,bb,rb,t1b,t3b = row_metrics(pb,y); lc,bc,rc,t1c,t3c = row_metrics(pc,y)
    mb, mc = aggregate(lb,bb,rb,t1b,t3b), aggregate(lc,bc,rc,t1c,t3c)
    delta = {k:float(mc[k]-mb[k]) for k in ("logloss","brier","rps","top1","top3")}
    summary["pooled"] = {"baseline":mb,"candidate":mc,"delta":delta}
    dll = lc-lb; rng = np.random.default_rng(72017); n=len(dll); boots=np.empty(3000,dtype=float)
    for b in range(3000):
        ix=rng.integers(0,n,size=n); boots[b]=float(np.mean(dll[ix]))
    q05,q95=np.quantile(boots,[0.05,0.95])
    summary["bootstrap90_dlogloss"]={"mean":float(np.mean(boots)),"lower":float(q05),"upper":float(q95),"p_delta_lt_zero":float(np.mean(boots<0))}
    fold_wins=sum(v["delta"]["logloss"]<0 for v in fold_summary.values()); summary["fold_logloss_wins"]=int(fold_wins)
    oos_meta=joined.loc[pd.Index(pooled_idx),["sourceCode"]].copy().reset_index(drop=True)
    league_stats={}; league_wins=0
    for code in sorted(oos_meta["sourceCode"].unique()):
        mask=oos_meta["sourceCode"].to_numpy()==code; lbb=float(np.mean(lb[mask])); lcc=float(np.mean(lc[mask]))
        league_stats[code]={"n":int(mask.sum()),"baseline_logloss":lbb,"candidate_logloss":lcc,"delta_logloss":lcc-lbb}; league_wins += int(lcc<lbb)
    summary["league_oos"]=league_stats; summary["league_logloss_wins"]=int(league_wins)
    summary["max_probability_sum_abs_residual"]=float(max(np.max(np.abs(pb.sum(axis=1)-1)),np.max(np.abs(pc.sum(axis=1)-1))))
    scientific_gates={
        "pooled_dlogloss_lt_zero":delta["logloss"]<0,"bootstrap90_upper_lt_zero":q95<0,"brier_nonworse":delta["brier"]<=0,"rps_nonworse":delta["rps"]<=0,
        "fold_logloss_wins_ge_4of5":fold_wins>=4,"league_logloss_wins_ge_3of4":league_wins>=3,"probability_conservation":summary["max_probability_sum_abs_residual"]<=1e-10,
        "reserve_targets_still_zero":summary["later_reserve_target_values_materialized"]==0,
    }
    summary["scientific_gates"]=scientific_gates; summary["pass"]=bool(all(scientific_gates.values()))
    summary["terminal"]="C072N17_HDA_INCREMENT_PT_DEVELOPMENT_PASS" if summary["pass"] else "C072N17_HDA_INCREMENT_PT_DEVELOPMENT_PARK"
    pd.DataFrame(pred_rows).to_csv(PRED_PATH,index=False)
    SUMMARY_PATH.write_text(json.dumps(summary,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps(summary,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
