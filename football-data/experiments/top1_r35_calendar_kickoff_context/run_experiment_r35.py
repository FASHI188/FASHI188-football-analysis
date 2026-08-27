#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.request
from collections import defaultdict
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

CALENDAR_NAMES = [
    "doy_sin", "doy_cos",
    "weekday_sin", "weekday_cos",
    "weekend",
]
KICKOFF_NAMES = [
    "utc_hour_sin", "utc_hour_cos",
    "utc_late_night", "utc_business_hours", "utc_evening",
]
FEATURE_SETS = {
    "CALENDAR_ONLY": CALENDAR_NAMES,
    "KICKOFF_UTC_ONLY": KICKOFF_NAMES,
    "CALENDAR_KICKOFF_CONTEXT": CALENDAR_NAMES + KICKOFF_NAMES,
}

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_TEST_GAIN_HITS = 1
MIN_POSITIVE_TEST_BLOCKS = 2
MAX_NEGATIVE_TEST_BLOCKS = 1
MAX_TEST_LOGLOSS_WORSEN = 0.001


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download_fixture_clock_metadata(game_ids):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "_fixtures_r35_clock.parquet"
    req = urllib.request.Request(FIX_URL, headers={"User-Agent": "football3-r35-calendar-kickoff"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)

    manifest = json.loads(
        (HERE.parent / "top1_r9b_xg_hf" / "data" / "source_manifest_r9b.json").read_text(encoding="utf-8")
    )
    got_sha = fsha(path)
    expected_sha = manifest["fixtures_sha256"]
    if got_sha != expected_sha:
        raise RuntimeError(f"R35 fixture snapshot drift: expected {expected_sha}, got {got_sha}")

    try:
        df = pd.read_parquet(path, columns=["id", "date_utc"])
    finally:
        try:
            path.unlink()
        except Exception:
            pass

    wanted = set(game_ids)
    df = df[df["id"].notna() & df["date_utc"].notna()].copy()
    df["game_id"] = df["id"].astype("int64").astype(str)
    df = df[df["game_id"].isin(wanted)].drop_duplicates("game_id")
    meta = {r.game_id: pd.Timestamp(r.date_utc) for r in df.itertuples(index=False)}
    missing = sorted(wanted - set(meta))
    if missing:
        raise RuntimeError(f"R35 missing clock metadata for {len(missing)} frozen rows; first={missing[:5]}")
    return meta, got_sha


def clock_features(ts: pd.Timestamp):
    if pd.isna(ts):
        raise RuntimeError("R35 null kickoff timestamp")
    dt = ts.to_pydatetime()
    doy = dt.timetuple().tm_yday
    weekday = dt.weekday()
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    return {
        "doy_sin": math.sin(2.0 * math.pi * (doy - 1) / 365.2425),
        "doy_cos": math.cos(2.0 * math.pi * (doy - 1) / 365.2425),
        "weekday_sin": math.sin(2.0 * math.pi * weekday / 7.0),
        "weekday_cos": math.cos(2.0 * math.pi * weekday / 7.0),
        "weekend": float(weekday >= 5),
        "utc_hour_sin": math.sin(2.0 * math.pi * hour / 24.0),
        "utc_hour_cos": math.cos(2.0 * math.pi * hour / 24.0),
        "utc_late_night": float(hour < 6.0),
        "utc_business_hours": float(9.0 <= hour < 17.0),
        "utc_evening": float(17.0 <= hour < 23.0),
    }


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    meta, fixture_sha = download_fixture_clock_metadata([r["game_id"] for r in rows])
    base = r9.S()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            ts = meta[row["game_id"]]
            if ts.date().isoformat() != row["date"]:
                raise RuntimeError(
                    f"R35 fixture date mismatch game={row['game_id']} frozen={row['date']} fixture={ts.date().isoformat()}"
                )
            pred.append(
                {
                    "date": day,
                    "y": r9.actual(row),
                    "raw": raw,
                    "context_features": clock_features(ts),
                }
            )
            pending.append((row, raw))

        for row, raw in pending:
            base.update(row, raw)

    return pred, fixture_sha


def x_for(rec, feature_names):
    return list(r9.feat_k1(rec["raw"])) + [
        float(rec["context_features"][name]) for name in feature_names
    ]


def fit_model(train, feature_names):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    model.fit([x_for(r, feature_names) for r in train], [r["y"] for r in train])
    return model


def decorate(model, rows, feature_names):
    pr = model.predict_proba([x_for(r, feature_names) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, row in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, row):
            v[int(cls)] = float(p)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def baseline_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    model.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    return model


def baseline_decorate(k1, rows):
    p = r34.r19.decorate_k1(k1, rows)
    return [{"date": r["date"], "y": r["y"], "P": q} for r, q in zip(rows, p)]


def metrics(rows):
    return r9.metrics([{"y": r["y"], "P": r["P"]} for r in rows], "P")


def date_blocks(rows, n=4):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), n)
    out = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist():
            out[d] = i
    return out


def paired_blocks(base_rows, candidate_rows):
    block_map = date_blocks(base_rows, 4)
    blocks = {
        str(i): {"count": 0, "base_hits": 0, "candidate_hits": 0, "net": 0}
        for i in range(4)
    }
    gain = loss = 0
    for b, c in zip(base_rows, candidate_rows):
        if b["date"] != c["date"] or b["y"] != c["y"]:
            raise RuntimeError("R35 paired rows misaligned")
        y = b["y"]
        cb = int(b["P"]["top1"] == y)
        cc = int(c["P"]["top1"] == y)
        gain += int(cc and not cb)
        loss += int(cb and not cc)
        z = blocks[str(block_map[b["date"]])]
        z["count"] += 1
        z["base_hits"] += cb
        z["candidate_hits"] += cc
    for z in blocks.values():
        z["net"] = z["candidate_hits"] - z["base_hits"]
    return {
        "challenger_gain": gain,
        "challenger_loss": loss,
        "net_hits": gain - loss,
        "positive_time_blocks": sum(int(z["net"] > 0) for z in blocks.values()),
        "negative_time_blocks": sum(int(z["net"] < 0) for z in blocks.values()),
        "time_blocks": blocks,
    }


def run():
    pred, fixture_sha = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    k1 = baseline_model(train)
    val_base = baseline_decorate(k1, val0)
    base_v = metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R35 K1 validation reproduction gate failed: {base_v['hits']}")

    r34_summary = json.loads(
        (R34_DIR / "results" / "summary_r34.json").read_text(encoding="utf-8")
    )
    if r34_summary["selected_feature_set"]["name"] != "AWAY_LOAD_ONLY":
        raise RuntimeError("R35 requires frozen R34 AWAY_LOAD_ONLY control")

    candidates = []
    models = {}
    for name, features in FEATURE_SETS.items():
        model = fit_model(train, features)
        models[name] = model
        val = decorate(model, val0, features)
        mv = metrics(val)
        paired = paired_blocks(val_base, val)
        gain = mv["hits"] - base_v["hits"]
        ll_delta = mv["logloss"] - base_v["logloss"]
        viable = (
            gain >= MIN_VALIDATION_GAIN_HITS
            and paired["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and paired["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
            and ll_delta <= MAX_VALIDATION_LOGLOSS_WORSEN
        )
        candidates.append(
            {
                "name": name,
                "features": features,
                "viable": viable,
                "validation": mv,
                "gain_hits": gain,
                "gain_top1_pp": 100.0 * (mv["top1_accuracy"] - base_v["top1_accuracy"]),
                "logloss_delta": ll_delta,
                "brier_delta": mv["brier"] - base_v["brier"],
                "rps_delta": mv["rps"] - base_v["rps"],
                "paired": paired,
            }
        )

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(
            viable,
            key=lambda x: (
                x["gain_hits"],
                x["paired"]["positive_time_blocks"],
                -x["paired"]["negative_time_blocks"],
                -x["logloss_delta"],
            ),
        )
        selected_name = selected["name"]
        test_base = baseline_decorate(k1, test0)
        base_t = metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R35 K1 test reproduction gate failed: {base_t['hits']}")
        test = decorate(models[selected_name], test0, FEATURE_SETS[selected_name])
        mt = metrics(test)
        paired_t = paired_blocks(test_base, test)
        test_gain = mt["hits"] - base_t["hits"]
        test_ll_delta = mt["logloss"] - base_t["logloss"]
        historical_test = {
            "baseline": base_t,
            "candidate": mt,
            "gain_hits": test_gain,
            "gain_top1_pp": 100.0 * (mt["top1_accuracy"] - base_t["top1_accuracy"]),
            "logloss_delta": test_ll_delta,
            "brier_delta": mt["brier"] - base_t["brier"],
            "rps_delta": mt["rps"] - base_t["rps"],
            "paired": paired_t,
        }
        historically_confirmed = (
            test_gain >= MIN_TEST_GAIN_HITS
            and paired_t["positive_time_blocks"] >= MIN_POSITIVE_TEST_BLOCKS
            and paired_t["negative_time_blocks"] <= MAX_NEGATIVE_TEST_BLOCKS
            and test_ll_delta <= MAX_TEST_LOGLOSS_WORSEN
        )
        stop_reason = None if historically_confirmed else "FROZEN_CALENDAR_KICKOFF_CONTEXT_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        historical_test = None
        historically_confirmed = False
        stop_reason = "NO_VALIDATION_ROBUST_CALENDAR_KICKOFF_CONTEXT_GAIN"

    summary = {
        "schema_version": "football3-top1-r35-calendar-kickoff-context",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_INDEPENDENT_STRICT_PREMATCH_INFORMATION_FAMILY",
        "formal_weight": 0,
        "governance": {
            "base_r34_commit": "d61774a09f3d841ebabc7ca03183e8bfe75882ae",
            "snapshot_rows": 20000,
            "fixture_snapshot_sha256": fixture_sha,
            "strict_prior_features": True,
            "fixture_kickoff_timestamp_used": True,
            "current_fixture_outcome_fields_accessed_for_context": False,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "weather_actuals_used": False,
            "venue_coordinates_used": False,
            "feature_family_grid_predeclared": True,
            "model_hyperparameter_search_used": False,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch005_labels_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "After R34's away-load signal remained unconfirmed by an unusable fresh cohort, do auditable pre-match calendar and kickoff-time states add stable 1X2 Top1 information beyond K1?",
        "prematch_information_family": {
            "family": "CALENDAR_AND_KICKOFF_TIME_CONTEXT",
            "causal_contract": "Only fixture kickoff timestamp and deterministic calendar transforms are added. No score, xG, odds, lineup, referee outcome, weather actual, or post-match field is used.",
            "candidate_feature_sets": FEATURE_SETS,
        },
        "selection_contract": {
            "min_validation_gain_hits": MIN_VALIDATION_GAIN_HITS,
            "min_positive_validation_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "max_validation_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN,
            "min_test_gain_hits": MIN_TEST_GAIN_HITS,
            "min_positive_test_blocks": MIN_POSITIVE_TEST_BLOCKS,
            "max_negative_test_blocks": MAX_NEGATIVE_TEST_BLOCKS,
            "max_test_logloss_worsen": MAX_TEST_LOGLOSS_WORSEN,
        },
        "controls": {
            "K1_validation": base_v,
            "R34_selected_feature_set": r34_summary["selected_feature_set"]["name"],
            "R34_validation_gain_hits": r34_summary["selected_feature_set"]["gain_hits"],
            "R34_historical_test_gain_hits": r34_summary["historical_test_confirmation"]["gain_hits"],
        },
        "validation_candidates": candidates,
        "selected_feature_set": selected,
        "historical_test_confirmation": historical_test,
        "decision": {
            "eligible_for_next_fresh_confirmation": historically_confirmed,
            "action": "LOCK_FRESH_CONFIRMATION_FOR_FROZEN_R35" if historically_confirmed else "DO_NOT_PROMOTE_R35",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "Continue another independent auditable prematch family; do not retrofit unavailable venue/weather data or tune on Batch005.",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r35.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r35.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "COMPLETE"
    assert g["strict_prior_features"] and g["fixture_kickoff_timestamp_used"]
    assert not g["current_fixture_outcome_fields_accessed_for_context"]
    assert g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["weather_actuals_used"] and not g["venue_coordinates_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch005_labels_used"] and not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation"]["hits"] == 2064
    assert len(s["validation_candidates"]) == len(FEATURE_SETS)
    if s["decision"]["eligible_for_next_fresh_confirmation"]:
        assert s["selected_feature_set"] is not None
        assert s["historical_test_confirmation"] is not None
    print("R35_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r35.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
