#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import requests
from bs4 import BeautifulSoup
from scipy.optimize import brentq, minimize
from scipy.special import gammaln

ZERO_DIR = Path("football-data/research/_c072n18b_zero_label_join")
TARGET_PATH = ZERO_DIR / "c072n18b_target550_zero_label.jsonl.gz"
DEV_IDS_PATH = ZERO_DIR / "c072n18b_dev400_ids.txt"
CONF_IDS_PATH = ZERO_DIR / "c072n18b_confirmation150_ids.txt"
N18B_SUMMARY = ZERO_DIR / "c072n18b_summary.json"
OUTDIR = Path("football-data/research/_c072n18c_development")
SUMMARY_PATH = OUTDIR / "c072n18c_summary.json"
PRED_PATH = OUTDIR / "c072n18c_oos_predictions.jsonl.gz"

EXPECTED_TARGET550_SHA = "2d995990fcadcbdc14a2f9fadc07c8aba306433f53a174fcf2a55c513005a386"
EXPECTED_DEV_IDS_SHA = "55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf"
EXPECTED_CONF_IDS_SHA = "774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f"
EXPECTED_DEV_N = 400
EXPECTED_CONF_N = 150
FEATURE_N = 16
RIDGE_LAMBDA = 1.0
BOOT_REPS = 3000
BOOT_SEED = 72018

RESULT_HEADERS = ["id","matchDate","Country","League","Season","homeTeam","awayTeam","referee","FTHG","FTAG","FTR"]
AJAX = "https://footiqo.com/wp-admin/admin-ajax.php"
ACTION = "get_wdtable"
NONCE_FIELD = "wdtNonce"
PAGES = {
    "EPL": ("https://footiqo.com/database/leagues/england-premier-league/", "2024/2025"),
    "LALIGA": ("https://footiqo.com/database/leagues/spain-laliga/", "2024/2025"),
    "BUNDESLIGA": ("https://footiqo.com/database/leagues/germany-bundesliga/", "2024/2025"),
    "SERIEA": ("https://footiqo.com/database/leagues/italy-serie-a/", "2024/2025"),
    "LIGUE1": ("https://footiqo.com/database/leagues/france-ligue-1/", "2024/2025"),
    "MLS": ("https://footiqo.com/database/leagues/usa-mls/", "2024"),
}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(x) -> str:
    if x is None:
        return ""
    s = str(x).strip()
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def read_zero_label():
    if not TARGET_PATH.exists() or not DEV_IDS_PATH.exists() or not CONF_IDS_PATH.exists() or not N18B_SUMMARY.exists():
        raise RuntimeError("N18B2 zero-label artifact missing")
    sx = json.loads(N18B_SUMMARY.read_text(encoding="utf-8"))
    if sx.get("status") != "PASS_N18B2_ZERO_LABEL_TARGET_MARKET_JOIN":
        raise RuntimeError(f"N18B2 status mismatch {sx.get('status')}")
    if sx.get("target550_sha256") != EXPECTED_TARGET550_SHA:
        raise RuntimeError("N18B2 target550 receipt hash mismatch")
    if sha256_file(DEV_IDS_PATH) != EXPECTED_DEV_IDS_SHA:
        raise RuntimeError("dev400 IDs file hash mismatch")
    if sha256_file(CONF_IDS_PATH) != EXPECTED_CONF_IDS_SHA:
        raise RuntimeError("confirmation150 IDs file hash mismatch")
    dev_ids = [int(x.strip()) for x in DEV_IDS_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    conf_ids = [int(x.strip()) for x in CONF_IDS_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(dev_ids) != EXPECTED_DEV_N or len(conf_ids) != EXPECTED_CONF_N or set(dev_ids) & set(conf_ids):
        raise RuntimeError("frozen dev/confirmation split mismatch")
    rows = []
    with gzip.open(TARGET_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            x = json.loads(line)
            rows.append(x)
    if len(rows) != 550:
        raise RuntimeError(f"target550 row count {len(rows)}")
    by_id = {int(x["footiqo_id"]): x for x in rows}
    if len(by_id) != 550 or any(i not in by_id for i in dev_ids + conf_ids):
        raise RuntimeError("target550 identity mismatch")
    dev = [by_id[i] for i in dev_ids]
    dev.sort(key=lambda x: (x["match_time_local"], int(x["footiqo_id"])))
    return dev, set(conf_ids), sx


def table_headers(t):
    h = [norm(x.get_text(" ", strip=True)) for x in t.find_all("th")]
    if not h:
        tr = t.find("tr")
        if tr:
            h = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["th", "td"])]
    return h


def visible_seasons(t, headers):
    if "Season" not in headers:
        return []
    j = headers.index("Season")
    vals = []
    for tr in t.find_all("tr")[1:]:
        cells = [norm(x.get_text(" ", strip=True)) for x in tr.find_all(["th", "td"])]
        if len(cells) > j and cells[j] and cells[j] != "Season":
            vals.append(cells[j])
    return sorted(set(vals))


def resolve_result_table(page_html: str, wanted_season: str):
    soup = BeautifulSoup(page_html, "html.parser")
    cand = []
    for t in soup.find_all("table"):
        h = table_headers(t)
        if h != RESULT_HEADERS:
            continue
        tid = str(t.get("data-wpdatatable_id", ""))
        if not tid.isdigit():
            continue
        seasons = visible_seasons(t, h)
        if wanted_season in seasons:
            cand.append((t, int(tid), seasons))
    if len(cand) != 1:
        raise RuntimeError(f"result table resolution expected 1 got {len(cand)} season={wanted_season}")
    return cand[0]


def payload(nonce: str, rid: int, season: str):
    b = {"draw":"1","start":"0","length":"10","search[value]":"","search[regex]":"false",NONCE_FIELD:nonce}
    for i, h in enumerate(RESULT_HEADERS):
        b[f"columns[{i}][data]"] = str(i)
        b[f"columns[{i}][name]"] = h
        b[f"columns[{i}][searchable]"] = "true"
        b[f"columns[{i}][orderable]"] = "true"
        if h == "id":
            sv = str(rid)
        elif h == "Season":
            sv = season
        else:
            sv = ""
        b[f"columns[{i}][search][value]"] = sv
        b[f"columns[{i}][search][regex]"] = "false"
    return b


def parse_goal(x):
    try:
        z = int(norm(x))
    except Exception:
        return None
    return z if z >= 0 else None


def fetch_dev_results(dev_rows, conf_ids):
    sess = requests.Session()
    sess.headers.update({
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-n18c",
        "Accept-Language":"en-US,en;q=0.9",
    })
    by_code = {}
    for code, (url, season) in PAGES.items():
        rr = sess.get(url, timeout=45, allow_redirects=True)
        if not (200 <= rr.status_code < 300):
            raise RuntimeError(f"PAGE_HTTP {code} {rr.status_code}")
        table, tid, seasons = resolve_result_table(rr.text, season)
        soup = BeautifulSoup(rr.text, "html.parser")
        nonce_dom = f"wdtNonceFrontendServerSide_{tid}"
        nodes = [x for x in soup.find_all("input") if str(x.get("id", "")) == nonce_dom and str(x.get("name", "")) == nonce_dom]
        if len(nodes) != 1 or not str(nodes[0].get("value") or ""):
            raise RuntimeError(f"NONCE_PROTOCOL {code}")
        by_code[code] = {"url":url,"season":season,"tid":tid,"nonce":str(nodes[0].get("value")),"visible":seasons}

    out = {}
    post_requests = 0
    requested_ids = []
    returned_ids = []
    req_headers = {"Accept":"application/json, text/javascript, */*; q=0.01","Content-Type":"application/x-www-form-urlencoded; charset=UTF-8","X-Requested-With":"XMLHttpRequest","Origin":"https://footiqo.com"}

    for z in dev_rows:
        rid = int(z["footiqo_id"])
        if rid in conf_ids:
            raise RuntimeError("confirmation ID reached result request layer")
        code = z["source_code"]
        c = by_code[code]
        body = payload(c["nonce"], rid, c["season"])
        headers = dict(req_headers)
        headers["Referer"] = c["url"]
        requested_ids.append(rid)
        post_requests += 1
        rr = sess.post(AJAX, params={"action":ACTION,"table_id":str(c["tid"])}, data=body, headers=headers, timeout=45, allow_redirects=True)
        body[NONCE_FIELD] = "<redacted>"
        if not (200 <= rr.status_code < 300):
            raise RuntimeError(f"AJAX_HTTP id={rid} status={rr.status_code}")
        x = rr.json()
        data = x.get("data") if isinstance(x, dict) else None
        rf = x.get("recordsFiltered") if isinstance(x, dict) else None
        try:
            rf = int(rf)
        except Exception:
            raise RuntimeError(f"AJAX_METADATA id={rid}")
        if rf != 1 or not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], list) or len(data[0]) != len(RESULT_HEADERS):
            raise RuntimeError(f"TARGET_ONLY_FILTER_FAILED id={rid} rf={rf} rows={0 if not isinstance(data,list) else len(data)}")
        row = data[0]
        got_id = int(norm(row[0]))
        returned_ids.append(got_id)
        if got_id != rid or got_id in conf_ids:
            raise RuntimeError(f"UNAUTHORIZED_RESULT_ID requested={rid} got={got_id}")
        mapped = {RESULT_HEADERS[i]: norm(row[i]) for i in range(len(RESULT_HEADERS))}
        hg, ag = parse_goal(mapped["FTHG"]), parse_goal(mapped["FTAG"])
        if hg is None or ag is None:
            raise RuntimeError(f"INVALID_SCORE id={rid}")
        # Identity must agree with the already-frozen Footiqo odds row.
        if mapped["Season"] != z["season"] or mapped["homeTeam"] != z["home_team"] or mapped["awayTeam"] != z["away_team"]:
            raise RuntimeError(f"IDENTITY_MISMATCH id={rid}")
        out[rid] = {"hg":hg,"ag":ag,"total_goals":hg+ag}

    if len(out) != EXPECTED_DEV_N or len(set(requested_ids)) != EXPECTED_DEV_N or set(requested_ids) & conf_ids:
        raise RuntimeError("authorized development result transport boundary failed")
    return out, {"post_requests":post_requests,"requested_dev_ids":len(requested_ids),"returned_dev_ids":len(returned_ids),"confirmation_ids_requested":0,"confirmation_rows_returned":0}


def poisson_tail_ge3(mu):
    return 1.0 - math.exp(-mu) * (1.0 + mu + 0.5 * mu * mu)


def market_mu(q):
    q = float(q)
    lo, hi = 0.05, 8.0
    if not (poisson_tail_ge3(lo) < q < poisson_tail_ge3(hi)):
        raise RuntimeError(f"q_over25 outside frozen inversion bounds q={q}")
    return float(brentq(lambda m: poisson_tail_ge3(m) - q, lo, hi, xtol=1e-12, rtol=1e-12, maxiter=200))


def nb2_logpmf(y, mu, alpha):
    y = np.asarray(y, dtype=float)
    mu = np.asarray(mu, dtype=float)
    r = 1.0 / alpha
    return gammaln(y+r)-gammaln(r)-gammaln(y+1.0) + r*(math.log(r)-np.log(r+mu)) + y*(np.log(mu)-np.log(r+mu))


def fit_model(X, y, mu_anchor, candidate):
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mu_anchor = np.asarray(mu_anchor, dtype=float)
    if candidate:
        mean = X.mean(axis=0)
        sd = X.std(axis=0)
        sd = np.where(sd < 1e-8, 1.0, sd)
        Z = (X-mean)/sd
        p0 = np.zeros(18, dtype=float)
        p0[-1] = math.log(0.10)
    else:
        mean = np.zeros(X.shape[1]); sd = np.ones(X.shape[1]); Z = X*0.0
        p0 = np.array([0.0, math.log(0.10)], dtype=float)

    def obj(p):
        if candidate:
            beta0 = p[0]; beta = p[1:17]; loga = p[17]
            eta = np.log(mu_anchor) + beta0 + Z.dot(beta)
            penalty = RIDGE_LAMBDA * float(np.dot(beta, beta))
        else:
            beta0 = p[0]; loga = p[1]
            eta = np.log(mu_anchor) + beta0
            penalty = 0.0
        if np.any(eta < -8.0) or np.any(eta > 8.0):
            return 1e12 + float(np.sum(np.maximum(np.abs(eta)-8.0, 0.0)))*1e9
        mu = np.exp(eta)
        alpha = math.exp(loga)
        ll = nb2_logpmf(y, mu, alpha)
        if not np.all(np.isfinite(ll)):
            return 1e15
        return -float(np.sum(ll)) + penalty

    bounds = [(None,None)]*(len(p0)-1) + [(math.log(0.0001), math.log(3.0))]
    res = minimize(obj, p0, method="L-BFGS-B", bounds=bounds, options={"maxiter":3000,"ftol":1e-12,"gtol":1e-8})
    if not res.success or not np.isfinite(res.fun):
        raise RuntimeError(f"OPTIMIZER_FAIL candidate={candidate} status={res.status} msg={res.message}")
    p = res.x
    if candidate:
        return {"beta0":float(p[0]),"beta":p[1:17].copy(),"alpha":float(math.exp(p[17])),"mean":mean,"sd":sd,"objective":float(res.fun)}
    return {"beta0":float(p[0]),"beta":np.zeros(16),"alpha":float(math.exp(p[1])),"mean":mean,"sd":sd,"objective":float(res.fun)}


def predict_probs(model, X, mu_anchor, candidate):
    X = np.asarray(X, dtype=float); mu_anchor=np.asarray(mu_anchor,dtype=float)
    if candidate:
        Z=(X-model["mean"])/model["sd"]
        eta=np.log(mu_anchor)+model["beta0"]+Z.dot(model["beta"])
    else:
        eta=np.log(mu_anchor)+model["beta0"]
    mu=np.exp(eta); alpha=model["alpha"]; r=1.0/alpha
    out=np.zeros((len(mu),8),dtype=float)
    for k in range(7):
        logp=gammaln(k+r)-gammaln(r)-gammaln(k+1.0)+r*(np.log(r)-np.log(r+mu))+k*(np.log(mu)-np.log(r+mu))
        out[:,k]=np.exp(logp)
    out[:,7]=1.0-out[:,:7].sum(axis=1)
    if np.any(~np.isfinite(out)) or np.any(out < -1e-12):
        raise RuntimeError("NONFINITE_OR_NEGATIVE_PROB")
    out=np.maximum(out,0.0)
    s=out.sum(axis=1)
    if np.max(np.abs(s-1.0)) > 1e-10:
        raise RuntimeError("PROBABILITY_NORMALIZATION_FAIL")
    return out, mu


def row_metrics(p,y):
    y=np.asarray(y,dtype=int); one=np.eye(8)[y]
    ll=-np.log(np.clip(p[np.arange(len(y)),y],1e-15,1.0))
    br=np.sum((p-one)**2,axis=1)
    rps=np.sum((np.cumsum(p,axis=1)[:,:-1]-np.cumsum(one,axis=1)[:,:-1])**2,axis=1)/7.0
    t1=np.argmax(p,axis=1)==y
    t3=np.array([y[i] in np.argsort(p[i])[-3:] for i in range(len(y))],dtype=bool)
    return ll,br,rps,t1,t3


def agg(m):
    ll,br,rps,t1,t3=m
    return {"n":len(ll),"logloss":float(np.mean(ll)),"brier":float(np.mean(br)),"rps":float(np.mean(rps)),"top1":float(np.mean(t1)),"top3":float(np.mean(t3))}


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    summary={
        "project":"football3","experiment":"C072-N18C","classification":"DEVELOPMENT_NOT_CONFIRMATION","formal_weight":0,
        "C073_C077_scientific_results_used":False,"C070F_confirmation1597_opened":False,"sealed_reserves_opened":False,
        "confirmation150_result_values_materialized":0,"confirmation150_result_requests":0,"bootstrap_reps":BOOT_REPS,"bootstrap_seed":BOOT_SEED,
    }
    try:
        dev, conf_ids, n18b = read_zero_label()
        results, transport = fetch_dev_results(dev, conf_ids)
        summary["transport"] = transport
        summary["development_result_rows_materialized"] = len(results)
        summary["development_result_values_materialized"] = len(results)*2

        ids=np.array([int(z["footiqo_id"]) for z in dev],dtype=int)
        X=np.array([z["features16"] for z in dev],dtype=float)
        if X.shape != (400,16) or not np.all(np.isfinite(X)):
            raise RuntimeError(f"feature matrix invalid {X.shape}")
        q=np.array([float(z["q_over25"]) for z in dev],dtype=float)
        mua=np.array([market_mu(v) for v in q],dtype=float)
        totals=np.array([results[int(i)]["total_goals"] for i in ids],dtype=int)
        y=np.minimum(totals,7)
        leagues=np.array([z["source_code"] for z in dev],dtype=object)

        folds=[(0,160,160,220),(0,220,220,280),(0,280,280,340),(0,340,340,400)]
        pred_rows=[]; fold_sum=[]; all_lb=[]; all_lc=[]; all_bb=[]; all_bc=[]; all_rb=[]; all_rc=[]; all_t1b=[]; all_t1c=[]; all_t3b=[]; all_t3c=[]; pool_idx=[]
        for fi,(a,b,c,d) in enumerate(folds,1):
            tr=np.arange(a,b); te=np.arange(c,d)
            mb=fit_model(X[tr],totals[tr],mua[tr],False)
            mc=fit_model(X[tr],totals[tr],mua[tr],True)
            pb,mub=predict_probs(mb,X[te],mua[te],False)
            pc,muc=predict_probs(mc,X[te],mua[te],True)
            rbm=row_metrics(pb,y[te]); rcm=row_metrics(pc,y[te])
            ab,ac=agg(rbm),agg(rcm)
            delta={k:ac[k]-ab[k] for k in ("logloss","brier","rps","top1","top3")}
            fold_sum.append({"fold":fi,"train_n":len(tr),"test_n":len(te),"baseline":ab,"candidate":ac,"delta":delta,"baseline_alpha":mb["alpha"],"candidate_alpha":mc["alpha"]})
            all_lb.extend(rbm[0]); all_lc.extend(rcm[0]); all_bb.extend(rbm[1]); all_bc.extend(rcm[1]); all_rb.extend(rbm[2]); all_rc.extend(rcm[2]); all_t1b.extend(rbm[3]); all_t1c.extend(rcm[3]); all_t3b.extend(rbm[4]); all_t3c.extend(rcm[4]); pool_idx.extend(te.tolist())
            for j,ix in enumerate(te):
                pred_rows.append({"footiqo_id":int(ids[ix]),"fold":fi,"source_code":str(leagues[ix]),"T":int(y[ix]),"baseline_probs":pb[j].tolist(),"candidate_probs":pc[j].tolist(),"mu_market":float(mua[ix]),"mu_baseline":float(mub[j]),"mu_candidate":float(muc[j])})

        mbp=agg((np.array(all_lb),np.array(all_bb),np.array(all_rb),np.array(all_t1b),np.array(all_t3b)))
        mcp=agg((np.array(all_lc),np.array(all_bc),np.array(all_rc),np.array(all_t1c),np.array(all_t3c)))
        delta={k:mcp[k]-mbp[k] for k in ("logloss","brier","rps","top1","top3")}
        dll=np.array(all_lc)-np.array(all_lb)
        rng=np.random.default_rng(BOOT_SEED)
        boots=np.empty(BOOT_REPS)
        n=len(dll)
        for i in range(BOOT_REPS):
            boots[i]=float(np.mean(dll[rng.integers(0,n,n)]))
        ci=[float(np.quantile(boots,0.05)),float(np.quantile(boots,0.95))]

        pidx=np.array(pool_idx,dtype=int)
        league_sum={}
        for lg in ["EPL","LALIGA","BUNDESLIGA","SERIEA","LIGUE1","MLS"]:
            m=leagues[pidx]==lg
            if not np.any(m):
                league_sum[lg]={"n":0,"dlogloss":None}
            else:
                league_sum[lg]={"n":int(m.sum()),"dlogloss":float(np.mean(np.array(all_lc)[m]-np.array(all_lb)[m]))}

        fold_wins=sum(1 for z in fold_sum if z["delta"]["logloss"] < 0)
        league_wins=sum(1 for z in league_sum.values() if z["n"]>0 and z["dlogloss"] < 0)
        gates={
            "pooled_dlogloss_lt0":delta["logloss"]<0,
            "bootstrap90_upper_lt0":ci[1]<0,
            "pooled_dbrier_le0":delta["brier"]<=0,
            "pooled_drps_le0":delta["rps"]<=0,
            "fold_wins_ge3of4":fold_wins>=3,
            "league_wins_ge4of6":league_wins>=4,
            "probability_audit":True,
            "confirmation_boundary_clean":transport["confirmation_ids_requested"]==0 and transport["confirmation_rows_returned"]==0,
        }
        passed=all(gates.values())
        breakthrough=passed and delta["logloss"]<=-0.010 and delta["rps"]<=-0.001 and fold_wins==4
        summary.update({
            "oos_n":n,"folds":fold_sum,"pooled":{"baseline":mbp,"candidate":mcp,"delta":delta},
            "bootstrap90_dlogloss":ci,"bootstrap_mean_dlogloss":float(np.mean(boots)),"p_dlogloss_lt0":float(np.mean(boots<0)),
            "league_dlogloss":league_sum,"fold_logloss_wins":fold_wins,"league_logloss_wins":league_wins,"gates":gates,
            "breakthrough_screen":bool(breakthrough),"terminal":"C072N18C_DEVELOPMENT_PASS" if passed else "C072N18C_DEVELOPMENT_PARK",
            "confirmation150_result_values_materialized":0,"confirmation150_result_requests":0,
        })
        with gzip.open(PRED_PATH,"wt",encoding="utf-8") as f:
            for r in pred_rows:
                f.write(json.dumps(r,sort_keys=True)+"\n")
    except Exception as e:
        summary["terminal"]="C072N18C_TECHNICAL_STOP"
        summary["error"]=f"{type(e).__name__}:{e}"

    SUMMARY_PATH.write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2,sort_keys=True))
    # Technical stop fails CI. Scientific PARK is a valid completed experiment.
    if summary["terminal"]=="C072N18C_TECHNICAL_STOP":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
