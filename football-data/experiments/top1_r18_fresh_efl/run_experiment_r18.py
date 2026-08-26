#!/usr/bin/env python3
from __future__ import annotations

import csv
import difflib
import io
import json
import math
import sys
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R14_DIR = HERE.parent / "top1_r14_compact_draw"
R17_DIR = HERE.parent / "top1_r17_fresh_forward"
sys.path.insert(0, str(R14_DIR)); sys.path.insert(0, str(R17_DIR))
import run_experiment_r14 as r14  # noqa: E402
import run_experiment_r17 as r17  # noqa: E402

r12 = r14.r12
r9 = r14.r9
START = "2026-07-05"
END = "2026-08-25"
DRAW_GATE = 0.08
DIVISIONS = {"E1": "2", "E2": "3", "E3": "4"}
URL = "https://www.football-data.co.uk/mmz4281/2627/{div}.csv"
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
SAFE_COLUMNS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    for x in ("football club", " fc", "fc ", " afc", "&", ".", "-", "'", "/"):
        s = s.replace(x, " ")
    return " ".join(s.split())


def download_csv(div):
    req = urllib.request.Request(URL.format(div=div), headers={"User-Agent": "football3-r18-fresh-confirmation/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in SAFE_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise RuntimeError(f"{div} missing safe columns {missing}")
    rows = []
    for z in reader:
        try:
            d = datetime.strptime(z["Date"].strip(), "%d/%m/%Y").date().isoformat()
        except ValueError:
            try:
                d = datetime.strptime(z["Date"].strip(), "%d/%m/%y").date().isoformat()
            except ValueError:
                continue
        if not (START <= d <= END):
            continue
        if not z.get("HomeTeam") or not z.get("AwayTeam") or z.get("FTHG", "") == "" or z.get("FTAG", "") == "":
            continue
        rows.append({
            "date": d,
            "home_name": z["HomeTeam"].strip(),
            "away_name": z["AwayTeam"].strip(),
            "home_goals": int(float(z["FTHG"])),
            "away_goals": int(float(z["FTAG"])),
        })
    return rows


def team_index(df):
    out = []
    for row in df.itertuples(index=False):
        d = row._asdict(); tid = str(int(d["id"])); names = []
        for c in ("fd_name", "name"):
            v = d.get(c)
            if v is not None and str(v).lower() != "nan":
                names.append(norm(v))
        if names:
            out.append((tid, sorted(set(names))))
    return out


def resolve(name, idx):
    q = norm(name); scored = []
    for tid, names in idx:
        best = 0.0
        for n in names:
            if q == n: best = 1.0
            elif q in n or n in q: best = max(best, .90 * min(len(q), len(n)) / max(len(q), len(n)) + .08)
            best = max(best, difflib.SequenceMatcher(None, q, n).ratio())
        if best >= .60:
            scored.append((best, tid, names))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < .74:
        return None, scored[:3]
    if len(scored) > 1 and scored[0][0] < .99 and scored[0][0] - scored[1][0] < .05:
        return None, scored[:3]
    return scored[0][1], scored[:3]


def decorate(model, X):
    pr = model.predict_proba(X); classes = list(model[-1].classes_); out = []
    for row in pr:
        v = np.zeros(3, dtype=float)
        for c, p in zip(classes, row): v[int(c)] = float(p)
        v = np.clip(v, 1e-12, None); v /= v.sum(); out.append(r9.decorate(v))
    return out


def decision_metrics(rows, key):
    picks = [0,0,0]; hits = [0,0,0]; total = 0
    for r in rows:
        t = int(r[key]); y = int(r["y"]); picks[t] += 1; hits[t] += int(t == y); total += int(t == y)
    return {"count": len(rows), "hits": total, "top1_accuracy": total/len(rows),
            "top1_picks": {"home":picks[0],"draw":picks[1],"away":picks[2]},
            "top1_hits": {"home":hits[0],"draw":hits[1],"away":hits[2]}}


def run():
    DATA.mkdir(parents=True, exist_ok=True); r12.freeze_gate(); base_rows = r9.load()
    locked = json.loads((HERE.parent / "top1_r15_draw_gate" / "results" / "summary_r15.json").read_text(encoding="utf-8"))
    if float(locked["selected_threshold"]) != DRAW_GATE:
        raise RuntimeError("R18 R15 gate lock mismatch")

    # Build historical state and fit locked K1/K3 before fresh source retrieval.
    bs = r9.S(); ds = r12.DrawState(); hist = []; by = defaultdict(list)
    for x in base_rows: by[x["date"]].append(x)
    for day in sorted(by):
        pending = []
        for x in sorted(by[day], key=lambda z:z["game_id"]):
            raw = bs.pred(x); df = ds.features(x, raw)
            hist.append({"date": day, "y": r9.actual(x), "raw": raw, "df": df}); pending.append((x,raw))
        for x,raw in pending: bs.update(x,raw); ds.update(x)
    b1 = r9.boundary(hist, r9.TARGET_BURN); b2 = r9.boundary(hist, b1+r9.TARGET_TRAIN); train = hist[b1:b2]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y = [r["y"] for r in train]
    def mdl(): return make_pipeline(StandardScaler(), LogisticRegression(C=.5, max_iter=3000, random_state=0))
    k1 = mdl(); k3 = mdl()
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y)
    k3.fit([r9.feat_k1(r["raw"]) + r14.compact(r["df"]) for r in train], y)

    # Metadata only; no current rating and no market data.
    tp = HERE / "_teams.parquet"
    r9.download(f"{HF}/teams.parquet?download=true", tp)
    teams = pd.read_parquet(tp); tp.unlink(missing_ok=True); idx = team_index(teams)

    fresh = []; audit = []; source_counts = {}
    for div,cid in DIVISIONS.items():
        src = download_csv(div); source_counts[div] = len(src)
        for i,z in enumerate(src):
            hid,hc = resolve(z["home_name"],idx); aid,ac = resolve(z["away_name"],idx)
            audit.append({"division":div,"home":z["home_name"],"home_id":hid,"home_candidates":hc,"away":z["away_name"],"away_id":aid,"away_candidates":ac})
            if hid is None or aid is None: continue
            fresh.append({"date":z["date"],"game_id":f"FD_{div}_{z['date']}_{i:03d}","competition_id":cid,"home_team":hid,"away_team":aid,
                          "home_goals":z["home_goals"],"away_goals":z["away_goals"],"home_xg":0.0,"away_xg":0.0,"division":div})
    mapped = len(fresh); candidates = len(audit); coverage = mapped/candidates if candidates else 0.0
    if mapped < 60 or coverage < .90:
        unresolved = [x for x in audit if x["home_id"] is None or x["away_id"] is None]
        (DATA/"diagnostic_r18.json").write_text(json.dumps({"rows":mapped,"candidates":candidates,"coverage":coverage,"unresolved":unresolved},indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        raise RuntimeError(f"R18 fresh cohort gate failed rows={mapped} coverage={coverage:.3f}")
    fresh.sort(key=lambda x:(x["date"],x["game_id"]))
    (DATA/"fresh_cohort_r18.json").write_text(json.dumps({"start":START,"end":END,"source_counts":source_counts,"mapped_coverage":coverage,"rows":fresh,"mapping_audit":audit},indent=2,ensure_ascii=False)+"\n",encoding="utf-8")

    scored = []; fby = defaultdict(list)
    for x in fresh: fby[x["date"]].append(x)
    for day in sorted(fby):
        pending = []
        for x in sorted(fby[day],key=lambda z:z["game_id"]):
            raw = bs.pred(x); df = ds.features(x,raw)
            p1 = decorate(k1,[r9.feat_k1(raw)])[0]
            p3 = decorate(k3,[r9.feat_k1(raw)+r14.compact(df)])[0]
            edge = p3["p_draw"] - max(p3["p_home"],p3["p_away"])
            g1 = 1 if p1["top1"] != 1 and p3["top1"] == 1 and edge >= DRAW_GATE else p1["top1"]
            scored.append({"date":day,"division":x["division"],"y":r9.actual(x),"K1":p1,"K3":p3,"G1_top1":g1})
            pending.append((x,raw))
        for x,raw in pending:
            r17.goal_only_base_update(bs,x,raw); ds.update(x)

    k1m = r9.metrics(scored,"K1"); k3m = r9.metrics(scored,"K3"); g1m = decision_metrics(scored,"G1_top1")
    per_div = {}
    for div in DIVISIONS:
        z = [r for r in scored if r["division"] == div]
        per_div[div] = {"count":len(z),"K1":r9.metrics(z,"K1"),"K3":r9.metrics(z,"K3"),"G1":decision_metrics(z,"G1_top1")}
    summary = {
      "schema_version":"football3-top1-r18-fresh-efl-confirmation","status":"COMPLETE","classification":"FRESH_FORWARD_CONFIRMATION_2","formal_weight":0,
      "governance":{"base_snapshot_last_date":"2026-07-04","fresh_start":START,"fresh_end":END,"fresh_rows":len(scored),"mapped_coverage":coverage,
        "domain":"EFL Championship League One League Two only; disjoint from R17 Understat leagues","rules_committed_before_fresh_label_retrieval":True,"same_date_results_withheld":True,
        "fresh_xg_used":False,"compatible_historical_xg_state_frozen_after_base":True,"source_file_may_contain_market_columns":True,"market_columns_loaded_into_dataframe":False,
        "safe_columns_read":list(SAFE_COLUMNS),"odds_used":False,"market_prices_used":False,"manual_match_adjustment":False,"hyperparameter_search_on_fresh":False,"formal_promotion_allowed_from_this_run":False},
      "locked_models":{"K1":"R9b baseline","K3":"R14 compact draw model","G1":f"R15 K1 draw gate threshold {DRAW_GATE}"},"source_counts":source_counts,
      "K1":k1m,"K3":k3m,"G1":g1m,"delta_K3_minus_K1":{"hits":k3m["hits"]-k1m["hits"],"top1_pp":100*(k3m["top1_accuracy"]-k1m["top1_accuracy"]),
        "logloss":k3m["logloss"]-k1m["logloss"],"brier":k3m["brier"]-k1m["brier"],"rps":k3m["rps"]-k1m["rps"]},"per_division":per_div}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/"summary_r18.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(summary,indent=2))


def verify():
    s=json.loads((OUT/"summary_r18.json").read_text(encoding="utf-8")); g=s["governance"]
    assert g["fresh_rows"]>=60 and g["mapped_coverage"]>=.90
    assert g["domain"].startswith("EFL") and g["rules_committed_before_fresh_label_retrieval"] and g["same_date_results_withheld"]
    assert not g["fresh_xg_used"] and g["compatible_historical_xg_state_frozen_after_base"]
    assert g["source_file_may_contain_market_columns"] and not g["market_columns_loaded_into_dataframe"]
    assert not g["odds_used"] and not g["market_prices_used"] and not g["hyperparameter_search_on_fresh"]
    assert not g["formal_promotion_allowed_from_this_run"]
    print("R18_VERIFY_PASS")

if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in {"run","verify"}: raise SystemExit("usage: run_experiment_r18.py {run|verify}")
    {"run":run,"verify":verify}[sys.argv[1]]()
