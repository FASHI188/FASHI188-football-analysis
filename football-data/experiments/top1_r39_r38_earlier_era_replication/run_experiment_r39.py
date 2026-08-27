#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R38_DIR = HERE.parent / "top1_r38_prior_match_process_form"
sys.path.insert(0, str(R38_DIR))
import run_experiment_r38 as r38  # noqa: E402

r9 = r38.r9
r34 = r38.r34

N = 20000
BURN = 4000
TRAIN = 8000
CONFIRM_HALF = 4000
FROZEN_FEATURE_SET = "MATCH_PROCESS_FORM_COMBINED"
MIN_CONFIRM_GAIN_HITS = 3
MIN_POSITIVE_BLOCKS = 2
MAX_NEGATIVE_BLOCKS = 1
MAX_LOGLOSS_WORSEN = 0.001
MIN_HALF_GAIN_HITS = 0


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r39-r38-replication"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def cohort_sha(df):
    h = hashlib.sha256()
    for r in df[["date", "id"]].itertuples(index=False):
        h.update(f"{r.date}|{int(r.id)}\n".encode("utf-8"))
    return h.hexdigest()


def load_preceding_20k():
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / "_fixtures_r39.parquet"
    sp = OUT / "_match_stats_r39.parquet"
    download(r38.FIX_URL, fp)
    download(r38.STAT_URL, sp)

    r38_summary = json.loads((R38_DIR / "results" / "summary_r38.json").read_text(encoding="utf-8"))
    f_sha = fsha(fp)
    s_sha = fsha(sp)
    if f_sha != r38_summary["source"]["fixtures_sha256"]:
        raise RuntimeError("R39 fixture source drift from frozen R38")
    if s_sha != r38_summary["source"]["match_stats_sha256"]:
        raise RuntimeError("R39 match_stats source drift from frozen R38")

    stat_cols = list(dict.fromkeys(r38.STAT_COLS + ["home_xg", "away_xg", "xg_covered", "xg_nulled"]))
    try:
        fx = pd.read_parquet(
            fp,
            columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "goals_home", "goals_away", "status_norm", "is_played"],
        )
        st = pd.read_parquet(sp, columns=stat_cols)
    finally:
        for p in (fp, sp):
            try:
                p.unlink()
            except Exception:
                pass

    st = st[
        (st["xg_covered"] == True)
        & (st["xg_nulled"] == False)
        & st["home_xg"].notna()
        & st["away_xg"].notna()
        & st["known_at"].notna()
    ].copy()
    fx = fx[
        (fx["is_played"] == True)
        & (fx["status_norm"] == "FT")
        & fx["goals_home"].notna()
        & fx["goals_away"].notna()
    ].copy()
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df["date"] = pd.to_datetime(df["date_utc"], utc=True).dt.date.astype(str)
    df["kickoff"] = pd.to_datetime(df["date_utc"], utc=True)
    df["known"] = pd.to_datetime(df["known_at"], utc=True)
    df = df[
        (df["known"] > df["kickoff"])
        & df["home_xg"].between(0, 6)
        & df["away_xg"].between(0, 6)
    ].sort_values(["date", "id"]).drop_duplicates("id")

    if len(df) < 2 * N:
        raise RuntimeError(f"R39 needs >= {2*N} valid rows, got {len(df)}")
    latest = df.tail(N)
    prior = df.iloc[-2 * N : -N].copy()
    if len(prior) != N:
        raise RuntimeError("R39 prior cohort length mismatch")
    if set(prior["id"]).intersection(set(latest["id"])):
        raise RuntimeError("R39 cohort overlaps R9b/R38 latest 20k")

    rows = []
    meta = {}
    for rec in prior.itertuples(index=False):
        gid = str(int(rec.id))
        row = {
            "date": rec.date,
            "game_id": gid,
            "competition_id": str(int(rec.league_id)),
            "home_team": str(int(rec.home_team_id)),
            "away_team": str(int(rec.away_team_id)),
            "home_goals": int(rec.goals_home),
            "away_goals": int(rec.goals_away),
            "home_xg": float(rec.home_xg),
            "away_xg": float(rec.away_xg),
            "xg_known_at": pd.Timestamp(rec.known).isoformat(),
        }
        rows.append(row)
        m = {c: getattr(rec, c) for c in r38.STAT_COLS if c != "fixture_id"}
        m["date_utc"] = pd.Timestamp(rec.kickoff).tz_convert(None)
        m["known_at"] = pd.Timestamp(rec.known).tz_convert(None)
        meta[gid] = m

    rows.sort(key=lambda z: (z["date"], z["game_id"]))
    manifest = {
        "source_valid_rows": int(len(df)),
        "selection": "immediately preceding 20000 valid FT xG rows before frozen R9b/R38 latest 20000",
        "rows": len(rows),
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "cohort_sha256": cohort_sha(prior),
        "latest_r38_first_date": str(latest.iloc[0]["date"]),
        "latest_r38_last_date": str(latest.iloc[-1]["date"]),
        "fixtures_sha256": f_sha,
        "match_stats_sha256": s_sha,
    }
    return rows, meta, manifest


def build_history(rows, meta):
    base = r9.S()
    state = r38.ProcessState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            m = meta[row["game_id"]]
            raw = base.pred(row)
            cf = state.features(row, m["date_utc"])
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw, m))
        for row, raw, m in pending:
            base.update(row, raw)
            state.update(row, m)
    return pred


def split(pred):
    b1 = r9.boundary(pred, BURN)
    b2 = r9.boundary(pred, b1 + TRAIN)
    b3 = r9.boundary(pred, b2 + CONFIRM_HALF)
    return pred[b1:b2], pred[b2:], pred[b2:b3], pred[b3:]


def evaluate_pair(base_rows, cand_rows):
    mb = r38.metrics(base_rows)
    mc = r38.metrics(cand_rows)
    return {
        "baseline": mb,
        "candidate": mc,
        "gain_hits": mc["hits"] - mb["hits"],
        "gain_top1_pp": 100.0 * (mc["top1_accuracy"] - mb["top1_accuracy"]),
        "logloss_delta": mc["logloss"] - mb["logloss"],
        "brier_delta": mc["brier"] - mb["brier"],
        "rps_delta": mc["rps"] - mb["rps"],
    }


def run():
    r34.r12.freeze_gate()
    frozen = json.loads((R38_DIR / "results" / "summary_r38.json").read_text(encoding="utf-8"))
    if frozen["selected_feature_set"]["name"] != FROZEN_FEATURE_SET:
        raise RuntimeError("R39 requires frozen R38 combined feature set")
    names = frozen["selected_feature_set"]["features"]
    if names != r38.FEATURE_SETS[FROZEN_FEATURE_SET]:
        raise RuntimeError("R39 R38 feature list drift")
    if r38.HALF_LIFE_DAYS != 180.0:
        raise RuntimeError("R39 R38 half-life drift")

    rows, meta, manifest = load_preceding_20k()
    pred = build_history(rows, meta)
    train, confirm, half1, half2 = split(pred)

    k1 = r38.baseline_model(train)
    model = r38.fit_model(train, names)
    base_confirm = r38.baseline_decorate(k1, confirm)
    cand_confirm = r38.decorate(model, confirm, names)
    base_h1 = r38.baseline_decorate(k1, half1)
    cand_h1 = r38.decorate(model, half1, names)
    base_h2 = r38.baseline_decorate(k1, half2)
    cand_h2 = r38.decorate(model, half2, names)

    full = evaluate_pair(base_confirm, cand_confirm)
    full["paired"] = r38.paired_blocks(base_confirm, cand_confirm)
    h1 = evaluate_pair(base_h1, cand_h1)
    h2 = evaluate_pair(base_h2, cand_h2)

    passed = (
        full["gain_hits"] >= MIN_CONFIRM_GAIN_HITS
        and full["paired"]["positive_time_blocks"] >= MIN_POSITIVE_BLOCKS
        and full["paired"]["negative_time_blocks"] <= MAX_NEGATIVE_BLOCKS
        and full["logloss_delta"] <= MAX_LOGLOSS_WORSEN
        and h1["gain_hits"] >= MIN_HALF_GAIN_HITS
        and h2["gain_hits"] >= MIN_HALF_GAIN_HITS
    )

    summary = {
        "schema_version": "football3-top1-r39-r38-earlier-era-replication",
        "status": "COMPLETE",
        "classification": "DISJOINT_EARLIER_ERA_HISTORICAL_REPLICATION_OF_FROZEN_R38",
        "formal_weight": 0,
        "governance": {
            "base_r38_commit": "3fda97a0c20c00607e0a43020eb38242070a1b02",
            "frozen_feature_set": FROZEN_FEATURE_SET,
            "frozen_feature_list": names,
            "frozen_half_life_days": r38.HALF_LIFE_DAYS,
            "frozen_logistic_C": 0.5,
            "feature_or_hyperparameter_search_used": False,
            "replication_cohort_selected_without_outcome_based_filtering": True,
            "replication_cohort_disjoint_from_r38_20k": True,
            "strict_prior_process_known_at_guard": True,
            "same_date_updates_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "batch005_labels_used": False,
            "replication_labels_used_for_design": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Does the fully frozen R38 combined match-process form replicate on the immediately preceding disjoint 20k era?",
        "cohort": manifest,
        "split": {
            "burn_target": BURN,
            "train_target": TRAIN,
            "confirmation_half_target": CONFIRM_HALF,
            "train_rows": len(train),
            "confirmation_rows": len(confirm),
            "confirmation_half1_rows": len(half1),
            "confirmation_half2_rows": len(half2),
        },
        "replication_contract": {
            "min_full_gain_hits": MIN_CONFIRM_GAIN_HITS,
            "min_positive_time_blocks": MIN_POSITIVE_BLOCKS,
            "max_negative_time_blocks": MAX_NEGATIVE_BLOCKS,
            "max_logloss_worsen": MAX_LOGLOSS_WORSEN,
            "min_each_half_gain_hits": MIN_HALF_GAIN_HITS,
        },
        "full_confirmation": full,
        "half1_confirmation": h1,
        "half2_confirmation": h2,
        "decision": {
            "historically_replicated": passed,
            "action": "KEEP_R38_FROZEN_AND_SEEK_TRUE_FRESH_CONFIRMATION" if passed else "R38_NOT_STABLE_ACROSS_DISJOINT_EARLIER_ERA",
            "stop_reason": None if passed else "FROZEN_R38_FAILED_DISJOINT_EARLIER_ERA_REPLICATION",
        },
        "interpretation_if_pass": "This would strengthen R38 as a development candidate but would still not constitute prospective fresh confirmation.",
        "interpretation_if_fail": "Do not rescue R38 by retuning on this replication cohort; move to a new independent information family.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r39.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r39.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "COMPLETE"
    assert g["frozen_feature_set"] == FROZEN_FEATURE_SET
    assert g["frozen_half_life_days"] == 180.0 and g["frozen_logistic_C"] == 0.5
    assert not g["feature_or_hyperparameter_search_used"]
    assert g["replication_cohort_selected_without_outcome_based_filtering"] and g["replication_cohort_disjoint_from_r38_20k"]
    assert g["strict_prior_process_known_at_guard"] and g["same_date_updates_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"] and not g["batch005_labels_used"]
    assert not g["replication_labels_used_for_design"] and not g["formal_promotion_allowed_from_this_run"]
    assert s["cohort"]["rows"] == N
    assert s["split"]["confirmation_rows"] == s["split"]["confirmation_half1_rows"] + s["split"]["confirmation_half2_rows"]
    print("R39_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r39.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
