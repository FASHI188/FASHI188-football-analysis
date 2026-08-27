#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R34_DIR = HERE.parent / "top1_r34_away_venue_sequence_context"
sys.path.insert(0, str(R34_DIR))
import run_experiment_r34 as r34  # noqa: E402

r9 = r34.r9

FIX_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/fixtures.parquet?download=true"
CUTOFF_DATE = "2026-07-04"
N = 100
SAFE_COLS = ["id", "date_utc", "league_id", "home_team_id", "away_team_id"]
REVEAL_CONTRACT = {
    "min_scorable_rows": 80,
    "min_candidate_gain_hits": 1,
    "min_positive_time_blocks": 2,
    "max_negative_time_blocks": 1,
    "max_logloss_worsen": 0.005,
}


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def jsha(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def download_safe_fixtures():
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "_fixtures_safe.parquet"
    req = urllib.request.Request(FIX_URL, headers={"User-Agent": "football3-batch005-lock"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    sha = fsha(path)
    df = pd.read_parquet(path, columns=SAFE_COLS)
    return path, sha, df


def load_r34_control():
    s = json.loads((R34_DIR / "results" / "summary_r34.json").read_text(encoding="utf-8"))
    if not s["batch005_decision"]["eligible"]:
        raise RuntimeError("Batch005 spend not authorized by frozen R34 historical confirmation")
    if not s["selected_feature_set"] or s["selected_feature_set"]["name"] != "AWAY_LOAD_ONLY":
        raise RuntimeError("Batch005 requires frozen R34 AWAY_LOAD_ONLY rule")
    return s


def lock():
    ctrl = load_r34_control()
    path, fixture_sha, df = download_safe_fixtures()
    try:
        x = df.copy()
        x = x[x["id"].notna() & x["date_utc"].notna() & x["league_id"].notna() & x["home_team_id"].notna() & x["away_team_id"].notna()]
        x["date"] = pd.to_datetime(x["date_utc"], utc=True).dt.date.astype(str)
        x = x[x["date"] > CUTOFF_DATE]
        x = x.sort_values(["date_utc", "id"]).drop_duplicates("id")
        if len(x) < N:
            raise RuntimeError(f"only {len(x)} safe post-cutoff fixtures; need {N}")
        rows = []
        for i, rec in enumerate(x.head(N).itertuples(index=False), 1):
            rows.append({
                "batch_index": i,
                "game_id": str(int(rec.id)),
                "date": str(rec.date),
                "date_utc": pd.Timestamp(rec.date_utc).isoformat(),
                "competition_id": str(int(rec.league_id)),
                "home_team": str(int(rec.home_team_id)),
                "away_team": str(int(rec.away_team_id)),
            })
    finally:
        try:
            path.unlink()
        except Exception:
            pass

    s = {
        "schema_version": "football3-batch005-r34-lock-v1",
        "status": "LOCKED",
        "purpose": "fresh untouched post-R9b-snapshot confirmation cohort for the validation/test-frozen R34 AWAY_LOAD_ONLY rule",
        "selection": {
            "source": "eatpizzanot/soccer-dataset fixtures.parquet",
            "cutoff_date_exclusive": CUTOFF_DATE,
            "target_rows": N,
            "ordering": ["date_utc", "id"],
            "selected_rows": len(rows),
            "first_date": rows[0]["date"],
            "last_date": rows[-1]["date"],
        },
        "governance": {
            "safe_fields_accessed": SAFE_COLS,
            "outcome_fields_accessed": False,
            "selection_uses_results": False,
            "selection_uses_odds": False,
            "selection_uses_xg": False,
            "selection_uses_postmatch_stats": False,
            "cohort_locked_after_R34_rule_frozen": True,
            "R34_validation_and_historical_test_complete_before_cohort_lock": True,
            "Batch005_not_used_for_R34_candidate_selection": True,
        },
        "r34_control": {
            "selected_feature_set": ctrl["selected_feature_set"]["name"],
            "validation_gain_hits": ctrl["selected_feature_set"]["gain_hits"],
            "historical_test_gain_hits": ctrl["historical_test_confirmation"]["gain_hits"],
            "historical_test_positive_time_blocks": ctrl["historical_test_confirmation"]["paired"]["positive_time_blocks"],
            "historical_test_negative_time_blocks": ctrl["historical_test_confirmation"]["paired"]["negative_time_blocks"],
        },
        "fixtures_safe_sha256": fixture_sha,
        "reveal_contract_predeclared_before_labels": REVEAL_CONTRACT,
        "cohort_sha256": jsha(rows),
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "batch005_locked_100.json").write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": s["status"], "selection": s["selection"], "cohort_sha256": s["cohort_sha256"]}, indent=2))


def verify_lock():
    s = json.loads((OUT / "batch005_locked_100.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "LOCKED" and len(s["rows"]) == N
    assert not g["outcome_fields_accessed"] and not g["selection_uses_results"]
    assert not g["selection_uses_odds"] and not g["selection_uses_xg"]
    assert g["cohort_locked_after_R34_rule_frozen"] and g["Batch005_not_used_for_R34_candidate_selection"]
    assert s["cohort_sha256"] == jsha(s["rows"])
    assert s["selection"]["first_date"] > CUTOFF_DATE
    print("BATCH005_LOCK_VERIFY_PASS")


def fit_frozen_models_and_state():
    pred = r34.build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    train = pred[b1:b2]
    k1 = r34.baseline_model(train)
    cand = r34.fit_model(train, r34.AWAY_LOAD_NAMES)

    rows = r9.load()
    base = r9.S()
    histories = defaultdict(list)
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            pending.append((row, raw))
        for row, raw in pending:
            base.update(row, raw)
            d = date.fromisoformat(row["date"])
            comp = row["competition_id"]
            histories[row["home_team"]].append({"date": d, "venue": "H", "comp": comp})
            histories[row["away_team"]].append({"date": d, "venue": "A", "comp": comp})
    return k1, cand, base, histories


def model_proba(model, X):
    pr = model.predict_proba([X])[0]
    classes = list(model[-1].classes_)
    v = np.zeros(3, dtype=float)
    for cls, p in zip(classes, pr):
        v[int(cls)] = float(p)
    v = np.clip(v, 1e-12, None)
    v /= v.sum()
    return r9.decorate(v)


def predict():
    lock_s = json.loads((OUT / "batch005_locked_100.json").read_text(encoding="utf-8"))
    if lock_s["cohort_sha256"] != jsha(lock_s["rows"]):
        raise RuntimeError("Batch005 cohort hash mismatch")
    ctrl = load_r34_control()
    k1, cand, base, histories = fit_frozen_models_and_state()

    out_rows = []
    by = defaultdict(list)
    for row in lock_s["rows"]:
        by[row["date"]].append(row)
    for ds in sorted(by):
        day_rows = sorted(by[ds], key=lambda z: (z["date_utc"], int(z["game_id"])))
        for row in day_rows:
            raw = base.pred(row)
            cf = r34.context_features(row, histories)
            k1p = model_proba(k1, list(r9.feat_k1(raw)))
            cp = model_proba(cand, list(r9.feat_k1(raw)) + [float(cf[n]) for n in r34.AWAY_LOAD_NAMES])
            out_rows.append({
                **row,
                "K1": k1p,
                "R34_AWAY_LOAD_ONLY": cp,
            })
        # Fixture occurrence/venue/competition is itself known after the date;
        # update only those strictly non-outcome states. Base strength is NOT updated
        # because Batch005 outcomes/xG remain hidden until reveal.
        d = date.fromisoformat(ds)
        for row in day_rows:
            comp = row["competition_id"]
            histories[row["home_team"]].append({"date": d, "venue": "H", "comp": comp})
            histories[row["away_team"]].append({"date": d, "venue": "A", "comp": comp})

    s = {
        "schema_version": "football3-batch005-r34-prediction-lock-v1",
        "status": "PREDICTIONS_LOCKED_LABELS_UNSEEN",
        "governance": {
            "cohort_sha256": lock_s["cohort_sha256"],
            "outcome_fields_accessed": False,
            "current_match_xg_accessed": False,
            "candidate_rule": "R34 AWAY_LOAD_ONLY frozen before Batch005 cohort selection",
            "candidate_retrained_or_tuned_on_Batch005": False,
            "base_strength_updated_with_Batch005_outcomes": False,
            "venue_history_updated_only_from_locked_fixture occurrence after each date": True,
            "reveal_contract_predeclared_before_labels": True,
        },
        "r34_control": lock_s["r34_control"],
        "reveal_contract": lock_s["reveal_contract_predeclared_before_labels"],
        "prediction_rows": len(out_rows),
        "prediction_sha256": jsha(out_rows),
        "rows": out_rows,
    }
    (OUT / "batch005_predictions_locked.json").write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": s["status"], "prediction_rows": s["prediction_rows"], "prediction_sha256": s["prediction_sha256"]}, indent=2))


def verify_predict():
    lock_s = json.loads((OUT / "batch005_locked_100.json").read_text(encoding="utf-8"))
    s = json.loads((OUT / "batch005_predictions_locked.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "PREDICTIONS_LOCKED_LABELS_UNSEEN"
    assert s["prediction_rows"] == N and len(s["rows"]) == N
    assert g["cohort_sha256"] == lock_s["cohort_sha256"]
    assert not g["outcome_fields_accessed"] and not g["current_match_xg_accessed"]
    assert not g["candidate_retrained_or_tuned_on_Batch005"] and not g["base_strength_updated_with_Batch005_outcomes"]
    assert s["prediction_sha256"] == jsha(s["rows"])
    print("BATCH005_PREDICTION_LOCK_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"lock", "verify_lock", "predict", "verify_predict"}:
        raise SystemExit("usage: lock_predict_batch005.py {lock|verify_lock|predict|verify_predict}")
    {"lock": lock, "verify_lock": verify_lock, "predict": predict, "verify_predict": verify_predict}[sys.argv[1]]()


if __name__ == "__main__":
    main()
