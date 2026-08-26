#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
DATA = HERE / "data"
R25 = HERE.parent / "top1_r25_fresh_s60_confirmation"
sys.path.insert(0, str(R25))
import run_experiment_r25 as r25  # noqa: E402

r23 = r25.r23
r18 = r25.r18
r24 = r25.r24
r9 = r25.r9

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
LOCK = HERE / "batch_lock_artifact" / "batch001_locked_100.json"
EXTRA = R25 / "r24_artifact" / "data" / "extra_r24_xg_60000.csv"
HISTORY_N = 60000
TRAIN_N = 24123
DIVS = ("E0", "D1", "I1", "SP1", "F1")
TOP1_NAME = {0: "HOME", 1: "DRAW", 2: "AWAY"}
# Metadata-only first-team disambiguation. These duplicate names exist in teams.parquet.
# IDs were resolved against league_id + home/away fixture identity only; no outcome fields were read.
TEAM_ID_OVERRIDES = {
    ("E0", "Arsenal"): "3",
    ("F1", "Lyon"): "224",
}


def utc_ts(x):
    return pd.to_datetime(x, utc=True)


def load_lock():
    s = json.loads(LOCK.read_text(encoding="utf-8"))
    if s["status"] != "LOCKED" or len(s["rows"]) != 100:
        raise RuntimeError("Batch-001 cohort lock mismatch")
    if s["governance"]["outcome_columns_read"] is not False:
        raise RuntimeError("Batch-001 lock governance mismatch")
    return s


def safe_target_metadata(lock):
    DATA.mkdir(parents=True, exist_ok=True)
    tp = DATA / "teams.parquet"
    lp = DATA / "leagues.parquet"
    fp = DATA / "fixtures_safe.parquet"
    r9.download(f"{HF}/teams.parquet?download=true", tp)
    r9.download(f"{HF}/leagues.parquet?download=true", lp)
    r9.download(r9.FIX_URL, fp)

    teams = pd.read_parquet(tp)
    leagues = pd.read_parquet(lp)
    # Governance: target fixture resolution intentionally reads NO outcome/status/stat columns.
    fixtures = pd.read_parquet(
        fp,
        columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id"],
    )
    fixtures["date_utc"] = pd.to_datetime(fixtures["date_utc"], utc=True)
    idx = r18.team_index(teams)
    cm = {d: r23.comp_id(leagues, d) for d in DIVS}

    resolved = []
    audit = []
    for z in lock["rows"]:
        div = z["division"]
        cid = cm[div][0]
        hid_auto, hc = r18.resolve(z["home"], idx)
        aid_auto, ac = r18.resolve(z["away"], idx)
        hid = TEAM_ID_OVERRIDES.get((div, z["home"]), hid_auto)
        aid = TEAM_ID_OVERRIDES.get((div, z["away"]), aid_auto)
        rec = {
            "batch_index": z["batch_index"],
            "date": z["date"],
            "division": div,
            "home": z["home"],
            "away": z["away"],
            "competition_id": cid,
            "home_team_id_auto": hid_auto,
            "away_team_id_auto": aid_auto,
            "home_team_id": hid,
            "away_team_id": aid,
            "home_id_override_used": (div, z["home"]) in TEAM_ID_OVERRIDES,
            "away_id_override_used": (div, z["away"]) in TEAM_ID_OVERRIDES,
            "home_candidates": hc,
            "away_candidates": ac,
        }
        if hid is None or aid is None:
            audit.append(rec)
            continue
        d0 = pd.Timestamp(z["date"], tz="UTC")
        m = fixtures[
            (fixtures["league_id"].astype(str) == str(cid))
            & (fixtures["home_team_id"].astype(str) == str(hid))
            & (fixtures["away_team_id"].astype(str) == str(aid))
            & (fixtures["date_utc"] >= d0 - pd.Timedelta(days=1))
            & (fixtures["date_utc"] < d0 + pd.Timedelta(days=2))
        ]
        rec["fixture_candidates"] = [
            {"id": str(int(x.id)), "date_utc": x.date_utc.isoformat()}
            for x in m.itertuples(index=False)
        ]
        audit.append(rec)
        if len(m) != 1:
            continue
        x = next(m.itertuples(index=False))
        resolved.append({
            **z,
            "fixture_id": str(int(x.id)),
            "competition_id": str(cid),
            "home_team": str(hid),
            "away_team": str(aid),
            "kickoff_utc": x.date_utc.isoformat(),
            "nominal_cutoff_utc": (x.date_utc - pd.Timedelta(hours=24)).isoformat(),
        })

    for p in (tp, lp, fp):
        p.unlink(missing_ok=True)
    if len(resolved) != 100:
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "mapping_audit_stage2.json").write_text(
            json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        raise RuntimeError(f"target mapping incomplete {len(resolved)}/100")
    return resolved, audit, cm


def load_frozen_pool():
    r25.select_s60()
    if not EXTRA.exists():
        raise RuntimeError(f"R24 frozen extra artifact missing: {EXTRA}")
    if r9.fsha(EXTRA) != r25.EXTRA_SHA:
        raise RuntimeError("R24 extra artifact hash mismatch")
    base = r9.load()
    extra = r25.load_extra_preserve(EXTRA)
    if len(base) != 20000 or len(extra) != 60000:
        raise RuntimeError(f"frozen pool size mismatch extra={len(extra)} base={len(base)}")
    if {x["game_id"] for x in extra} & {x["game_id"] for x in base}:
        raise RuntimeError("frozen history overlap")
    pool = extra + base
    for x in pool:
        x["_known"] = utc_ts(x["xg_known_at"])
    return pool


def build_date_model(pool, effective_cutoff):
    eligible = [x for x in pool if x["_known"] < effective_cutoff]
    eligible.sort(key=lambda x: (x["date"], x["game_id"]))
    if len(eligible) < HISTORY_N:
        raise RuntimeError(
            f"insufficient strict-prior history before {effective_cutoff.isoformat()}: {len(eligible)}"
        )
    window = eligible[-HISTORY_N:]
    clean = [{k: v for k, v in x.items() if k != "_known"} for x in window]
    hp, state = r23.hist(clean)
    if len(hp) != HISTORY_N:
        raise RuntimeError("history replay length mismatch")
    if len(hp) < TRAIN_N:
        raise RuntimeError("insufficient classifier train rows")
    model = r24.model(hp[-TRAIN_N:])
    return model, state, window[0], window[-1]


def run():
    lock = load_lock()
    targets, audit, cm = safe_target_metadata(lock)
    pool = load_frozen_pool()

    by_date = defaultdict(list)
    for z in targets:
        by_date[z["date"]].append(z)

    locked_predictions = []
    date_audit = []
    for day in sorted(by_date):
        q = sorted(by_date[day], key=lambda z: z["batch_index"])
        # Same-date discipline: one model/state for the whole date, using the earliest T-24h cutoff.
        effective_cutoff = min(utc_ts(z["nominal_cutoff_utc"]) for z in q)
        model, state, first_hist, last_hist = build_date_model(pool, effective_cutoff)
        date_audit.append({
            "date": day,
            "matches": len(q),
            "effective_cutoff_utc": effective_cutoff.isoformat(),
            "history_rows": HISTORY_N,
            "train_rows": TRAIN_N,
            "history_first_date": first_hist["date"],
            "history_last_date": last_hist["date"],
            "history_last_xg_known_at": last_hist["xg_known_at"],
        })
        for z in q:
            target = {
                "date": z["date"],
                "game_id": z["fixture_id"],
                "competition_id": z["competition_id"],
                "home_team": z["home_team"],
                "away_team": z["away_team"],
            }
            raw = state.pred(target)
            pred = r23.pred(model, raw)
            locked_predictions.append({
                "batch_index": z["batch_index"],
                "date": z["date"],
                "division": z["division"],
                "home": z["home"],
                "away": z["away"],
                "fixture_id": z["fixture_id"],
                "kickoff_utc": z["kickoff_utc"],
                "nominal_cutoff_utc": z["nominal_cutoff_utc"],
                "effective_same_date_cutoff_utc": effective_cutoff.isoformat(),
                "S60_replay": {
                    "p_home": pred["p_home"],
                    "p_draw": pred["p_draw"],
                    "p_away": pred["p_away"],
                    "top1": TOP1_NAME[pred["top1"]],
                },
                "raw_state": {
                    "p_home": raw["p_home"],
                    "p_draw": raw["p_draw"],
                    "p_away": raw["p_away"],
                    "mu_home": raw["mu_home"],
                    "mu_away": raw["mu_away"],
                    "xg_mu_home": raw["xg_mu_home"],
                    "xg_mu_away": raw["xg_mu_away"],
                    "home_history": raw["home_history"],
                    "away_history": raw["away_history"],
                    "xg_weight_min": raw["xg_weight_min"],
                },
                "prediction_status": "LOCKED_BASELINE_NO_RESULT_LABEL",
            })

    locked_predictions.sort(key=lambda z: z["batch_index"])
    summary = {
        "schema_version": "football3-batch001-stage2-historical-s60-replay-v1",
        "status": "PREDICTIONS_LOCKED_BASELINE_ONLY",
        "purpose": "strict historical replay of the locked S60 architecture before Batch-001 enrichment",
        "cohort_sha256": lock["cohort_sha256"],
        "rows": len(locked_predictions),
        "architecture": {
            "name": "S60",
            "history_rows": HISTORY_N,
            "classifier_train_rows": TRAIN_N,
            "classifier": "StandardScaler + multinomial LogisticRegression C=0.5 random_state=0",
            "future_trained_2026_07_weights_used": False,
            "weights_retrained_from_strict_prior_history_for_each_target_date": True,
        },
        "governance": {
            "target_fixture_outcome_columns_read": False,
            "target_result_labels_loaded_for_scoring": False,
            "target_same_date_results_used": False,
            "chronologically_prior_results_and_xg_used": True,
            "historical_xg_requires_known_at_before_effective_cutoff": True,
            "same_date_model_state_shared": True,
            "same_date_effective_cutoff_is_earliest_nominal_T_minus_24h": True,
            "metadata_team_id_overrides": {"E0:Arsenal": "3", "F1:Lyon": "224"},
            "metadata_overrides_use_outcomes": False,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "no_accuracy_or_result_metric_computed": True,
            "research_enrichment_not_yet_applied": True,
        },
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in cm.items()},
        "date_replay_audit": date_audit,
        "predictions": locked_predictions,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    (OUT / "batch001_s60_predictions_locked.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (DATA / "mapping_audit_stage2.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": summary["status"],
        "rows": summary["rows"],
        "cohort_sha256": summary["cohort_sha256"],
        "dates": len(date_audit),
    }, indent=2, ensure_ascii=False))


def verify():
    lock = load_lock()
    s = json.loads((OUT / "batch001_s60_predictions_locked.json").read_text(encoding="utf-8"))
    g = s["governance"]
    a = s["architecture"]
    p = s["predictions"]
    assert s["status"] == "PREDICTIONS_LOCKED_BASELINE_ONLY"
    assert s["cohort_sha256"] == lock["cohort_sha256"]
    assert s["rows"] == 100 and len(p) == 100
    assert [x["batch_index"] for x in p] == list(range(1, 101))
    assert a["history_rows"] == 60000 and a["classifier_train_rows"] == 24123
    assert not a["future_trained_2026_07_weights_used"]
    assert a["weights_retrained_from_strict_prior_history_for_each_target_date"]
    assert not g["target_fixture_outcome_columns_read"]
    assert not g["target_result_labels_loaded_for_scoring"]
    assert not g["target_same_date_results_used"]
    assert g["historical_xg_requires_known_at_before_effective_cutoff"]
    assert not g["metadata_overrides_use_outcomes"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["no_accuracy_or_result_metric_computed"]
    for x in p:
        q = x["S60_replay"]
        assert abs(q["p_home"] + q["p_draw"] + q["p_away"] - 1.0) < 1e-9
        assert q["top1"] in {"HOME", "DRAW", "AWAY"}
    print("BATCH001_STAGE2_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_stage2.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
