#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R9_DIR = HERE.parent / "top1_r9b_xg_hf"
sys.path.insert(0, str(R9_DIR))
import run_experiment_r9b as r9  # noqa: E402

EXTRA_N = 20000
EXTENDED_N = 40000
TRAIN_MULTIPLIER = 2


def build_preds(rows):
    st = r9.S()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = st.pred(row)
            pred.append({"date": day, "game_id": row["game_id"], "y": r9.actual(row), "raw": raw})
            pending.append((row, raw))
        for row, raw in pending:
            st.update(row, raw)
    return pred


def fit_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([r9.feat_k1(x["raw"]) for x in train], [x["y"] for x in train])
    return m


def attach(rows, model, key):
    probs = model.predict_proba([r9.feat_k1(x["raw"]) for x in rows])
    classes = list(model[-1].classes_)
    for rec, pr in zip(rows, probs):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, pr):
            v[int(cls)] = float(p)
        rec[key] = r9.decorate(v)


def delta(a, b):
    return {
        "hits": a["hits"] - b["hits"],
        "top1_pp": 100 * (a["top1_accuracy"] - b["top1_accuracy"]),
        "logloss": a["logloss"] - b["logloss"],
        "brier": a["brier"] - b["brier"],
        "rps": a["rps"] - b["rps"],
    }


def freeze_extra(base_rows):
    DATA.mkdir(parents=True, exist_ok=True)
    fp = DATA / "fixtures.parquet"
    sp = DATA / "match_stats.parquet"
    r9.download(r9.FIX_URL, fp)
    r9.download(r9.STAT_URL, sp)

    fx = pd.read_parquet(fp, columns=["id","date_utc","league_id","home_team_id","away_team_id","goals_home","goals_away","status_norm","is_played"])
    st = pd.read_parquet(sp, columns=["fixture_id","home_xg","away_xg","xg_covered","xg_nulled","known_at"])
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False) & st["home_xg"].notna() & st["away_xg"].notna()]
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT") & fx["goals_home"].notna() & fx["goals_away"].notna()]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["date"] = pd.to_datetime(df["date_utc"], utc=True).dt.date.astype(str)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[(df["known"] > pd.to_datetime(df["date_utc"], utc=True)) & df["home_xg"].between(0,6) & df["away_xg"].between(0,6)]
    df = df.sort_values(["date","id"]).drop_duplicates("id")

    first_date = base_rows[0]["date"]
    first_id = int(base_rows[0]["game_id"])
    pre = df[(df["date"] < first_date) | ((df["date"] == first_date) & (df["id"] < first_id))]
    if len(pre) < EXTRA_N:
        raise RuntimeError(f"only {len(pre)} strict-prior valid xG rows before frozen baseline; need {EXTRA_N}")
    ex = pre.tail(EXTRA_N)

    out = []
    for z in ex.itertuples(index=False):
        out.append({
            "date": z.date,
            "game_id": str(int(z.id)),
            "competition_id": str(int(z.league_id)),
            "home_team": str(int(z.home_team_id)),
            "away_team": str(int(z.away_team_id)),
            "home_goals": int(z.goals_home),
            "away_goals": int(z.goals_away),
            "home_xg": float(z.home_xg),
            "away_xg": float(z.away_xg),
            "xg_known_at": z.known.isoformat(),
        })
    ids = {x["game_id"] for x in base_rows}
    if any(x["game_id"] in ids for x in out):
        raise RuntimeError("extra history overlaps frozen R9b snapshot")
    if (out[-1]["date"], int(out[-1]["game_id"])) >= (first_date, first_id):
        raise RuntimeError("extra history chronology boundary failed")

    p = DATA / "extra_r22_xg_20000.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=r9.FIELDS); w.writeheader(); w.writerows(out)
    base_manifest = json.loads((r9.DATA / "source_manifest_r9b.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "football3-top1-r22-history-scale-40k",
        "status": "FROZEN_EXTRA_20000_BEFORE_EXACT_R9B",
        "source_dataset": "eatpizzanot/soccer-dataset",
        "license": "CC-BY-4.0",
        "current_fixtures_sha256": r9.fsha(fp),
        "current_match_stats_sha256": r9.fsha(sp),
        "frozen_r9b_snapshot_sha256": base_manifest["snapshot_sha256"],
        "frozen_r9b_first_date": base_rows[0]["date"],
        "frozen_r9b_last_date": base_rows[-1]["date"],
        "extra_rows": len(out),
        "extra_first_date": out[0]["date"],
        "extra_last_date": out[-1]["date"],
        "extra_snapshot_sha256": r9.fsha(p),
        "selection": "latest 20000 currently-valid FT xG rows strictly before the exact frozen R9b first (date,id); frozen original 20000 appended unchanged",
        "strict_prior_xg_contract": True,
        "odds_used": False,
        "market_prices_used": False,
    }
    (DATA / "source_manifest_r22.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fp.unlink(missing_ok=True); sp.unlink(missing_ok=True)
    return out, manifest


def run():
    base_rows = r9.load()
    base_pred = build_preds(base_rows)
    b1 = r9.boundary(base_pred, r9.TARGET_BURN)
    b2 = r9.boundary(base_pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(base_pred, b2 + r9.TARGET_VAL)
    base_train = base_pred[b1:b2]
    base_val = base_pred[b2:b3]
    base_test = base_pred[b3:]

    base_model = fit_model(base_train)
    attach(base_val, base_model, "B20")
    attach(base_test, base_model, "B20")
    bv = r9.metrics(base_val, "B20"); bt = r9.metrics(base_test, "B20")
    if bv["hits"] != 2064 or bt["hits"] != 1877:
        raise RuntimeError("R22 baseline reproduction gate failed")

    extra, manifest = freeze_extra(base_rows)
    ext_rows = extra + base_rows
    if len(ext_rows) != EXTENDED_N:
        raise RuntimeError("R22 extended row count mismatch")
    ext_pred = build_preds(ext_rows)
    off = EXTRA_N
    ext_same_train = ext_pred[off + b1 : off + b2]
    ext_val = ext_pred[off + b2 : off + b3]
    ext_test = ext_pred[off + b3 :]
    train16_start = off + b2 - TRAIN_MULTIPLIER * len(base_train)
    ext_train16 = ext_pred[train16_start : off + b2]
    if len(ext_train16) != TRAIN_MULTIPLIER * len(base_train):
        raise RuntimeError("R22 doubled train count mismatch")

    if [x["game_id"] for x in ext_val] != [x["game_id"] for x in base_val]:
        raise RuntimeError("R22 validation identity mismatch")
    if [x["game_id"] for x in ext_test] != [x["game_id"] for x in base_test]:
        raise RuntimeError("R22 test identity mismatch")
    if [x["y"] for x in ext_val] != [x["y"] for x in base_val] or [x["y"] for x in ext_test] != [x["y"] for x in base_test]:
        raise RuntimeError("R22 target label mismatch")

    m8 = fit_model(ext_same_train)
    m16 = fit_model(ext_train16)
    for rows in (ext_val, ext_test):
        attach(rows, m8, "H40_T8")
        attach(rows, m16, "H40_T16")
    v8 = r9.metrics(ext_val, "H40_T8"); t8 = r9.metrics(ext_test, "H40_T8")
    v16 = r9.metrics(ext_val, "H40_T16"); t16 = r9.metrics(ext_test, "H40_T16")

    summary = {
        "schema_version": "football3-top1-r22-history-scale-40k",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_FIXED_TAIL_HISTORY_SCALE_AUDIT",
        "formal_weight": 0,
        "governance": {
            "original_snapshot_rows": len(base_rows),
            "extra_strictly_prior_rows": len(extra),
            "extended_rows": len(ext_rows),
            "validation_identity_exactly_same_as_R9b": True,
            "test_identity_exactly_same_as_R9b": True,
            "validation_count": len(base_val),
            "test_count": len(base_test),
            "same_date_results_and_xg_withheld": True,
            "strict_prior_xg": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "hyperparameter_search_used": False,
            "regularization_C_fixed": 0.5,
            "formal_promotion_allowed_from_this_run": False,
        },
        "source_manifest": manifest,
        "models": {
            "B20": f"exact R9b K1 baseline; {len(base_train)} classifier training rows",
            "H40_T8": f"20k extra earlier state history; same {len(ext_same_train)} classifier training labels as baseline window",
            "H40_T16": f"20k extra earlier state history; doubled classifier training window {len(ext_train16)}",
        },
        "validation": {
            "B20": bv,
            "H40_T8": v8,
            "H40_T16": v16,
            "delta_H40_T8_minus_B20": delta(v8, bv),
            "delta_H40_T16_minus_B20": delta(v16, bv),
        },
        "test": {
            "B20": bt,
            "H40_T8": t8,
            "H40_T16": t16,
            "delta_H40_T8_minus_B20": delta(t8, bt),
            "delta_H40_T16_minus_B20": delta(t16, bt),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r22.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r22.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["original_snapshot_rows"] == 20000 and g["extra_strictly_prior_rows"] == 20000 and g["extended_rows"] == 40000
    assert g["validation_identity_exactly_same_as_R9b"] and g["test_identity_exactly_same_as_R9b"]
    assert g["same_date_results_and_xg_withheld"] and g["strict_prior_xg"]
    assert not g["odds_used"] and not g["market_prices_used"] and not g["hyperparameter_search_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["validation"]["B20"]["hits"] == 2064 and s["test"]["B20"]["hits"] == 1877
    print("R22_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r22.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
