#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
F5_DIR = ROOT / "football-data" / "experiments" / "r43f5_probability_weighted_technical_mixture"
if str(F5_DIR) not in sys.path:
    sys.path.insert(0, str(F5_DIR))
import run_r43f5 as f5  # noqa: E402

# Architecture-development only. All source blocks were consumed by earlier football3 research.
# Hyperparameters and gates below are fixed before scoring these blocks.
BLOCKS = [
    {"name": "block_40_20", "skip_newest_n": 20000, "selection": "valid_sorted_[-40000:-20000]"},
    {"name": "block_60_40", "skip_newest_n": 40000, "selection": "valid_sorted_[-60000:-40000]"},
    {"name": "block_80_60", "skip_newest_n": 60000, "selection": "valid_sorted_[-80000:-60000]"},
]

HGB_PARAMS = {
    "learning_rate": 0.04,
    "max_iter": 180,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 40,
    "l2_regularization": 2.0,
    "early_stopping": False,
    "random_state": 0,
}
BLEND_ALPHA = 0.50
RAW_FIELDS = [
    "p_home", "p_draw", "p_away",
    "mu_home", "mu_away", "mu_total",
    "latent_h", "latent_a",
    "home_history", "away_history", "comp_history",
    "xg_mu_home", "xg_mu_away", "xg_mu_total",
    "xg_home_for", "xg_home_against", "xg_away_for", "xg_away_against",
    "xg_weight_min",
]


def finite(v: float) -> float:
    x = float(v)
    return x if math.isfinite(x) else 0.0


def vector(x: dict) -> list[float]:
    raw = x["raw"]
    cf = x["context_weighted_cf"]
    vals = [finite(raw.get(k, 0.0)) for k in RAW_FIELDS]
    vals += [finite(cf.get(k, 0.0)) for k in (f5.BASE_NAMES + f5.TECH_NAMES)]
    vals += [finite(x.get("base_weighted_known", 0.0)), finite(x.get("context_weighted_known", 0.0))]
    return vals


def nonlinear_records(model, split: list[dict]) -> list[dict]:
    p = model.predict_proba(np.asarray([vector(x) for x in split], dtype=float))
    out = []
    for x, q in zip(split, p):
        out.append({
            "date": x["date"], "fixture_id": x["fixture_id"], "y": x["y"],
            "P": f5.r9.decorate(q),
        })
    return out


def blend_records(base: list[dict], alt: list[dict]) -> list[dict]:
    out = []
    for b, a in zip(base, alt):
        if b["fixture_id"] != a["fixture_id"]:
            raise RuntimeError("paired fixture drift")
        out.append({
            "date": b["date"], "fixture_id": b["fixture_id"], "y": b["y"],
            "P": f5.f3.blend_prob(b["P"], a["P"], BLEND_ALPHA),
        })
    return out


def build_block(spec: dict) -> dict:
    scratch = HERE / "scratch" / spec["name"]
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    r42i = f5.r42i
    old_here, old_out, old_skip, old_n = r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N
    old_f5_skip, old_f5_selection = f5.SKIP_NEWEST_N, f5.SELECTION
    try:
        f5.SKIP_NEWEST_N = int(spec["skip_newest_n"])
        f5.SELECTION = str(spec["selection"])
        r42i.HERE, r42i.OUT = scratch, scratch / "results"
        r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = int(spec["skip_newest_n"]), f5.BLOCK_SIZE
        rows, snap_meta = r42i.freeze_previous_20k()
    finally:
        r42i.HERE, r42i.OUT, r42i.SKIP_NEWEST_N, r42i.SNAPSHOT_N = old_here, old_out, old_skip, old_n
        f5.SKIP_NEWEST_N, f5.SELECTION = old_f5_skip, old_f5_selection

    if len(rows) != f5.BLOCK_SIZE:
        raise RuntimeError(f"source block length drift {spec['name']} n={len(rows)}")

    bp, cp, bt, ct, lineup_meta = f5.make_lineup_probabilities(rows)
    records, tech_meta = f5.build_outcome_records(rows, bp, cp, bt, ct, lineup_meta["lineup_train_end"])
    records = sorted(records, key=lambda x: (x["date"], x["fixture_id"]))
    if len(records) < f5.OUTCOME_TRAIN_TARGET + f5.OUTCOME_VAL_TARGET + f5.MIN_TEST_MATCHES:
        raise RuntimeError(f"undersized outcome cohort {spec['name']} n={len(records)}")

    b1 = f5.f3.boundary_date_safe(records, f5.OUTCOME_TRAIN_TARGET)
    b2 = f5.f3.boundary_date_safe(records, b1 + f5.OUTCOME_VAL_TARGET)
    train, val, test = records[:b1], records[b1:b2], records[b2:]

    names = f5.BASE_NAMES + f5.TECH_NAMES
    m0 = f5.fit_records(train, "base_cf", f5.BASE_NAMES)
    mt = f5.fit_records(train, "context_weighted_cf", names)

    def baseline(split):
        no = f5.score_model(m0, split, "base_cf", f5.BASE_NAMES)
        full = f5.score_model(mt, split, "context_weighted_cf", names)
        return f5.half_records(no, full)

    clf = HistGradientBoostingClassifier(loss="log_loss", **HGB_PARAMS)
    clf.fit(np.asarray([vector(x) for x in train], dtype=float), np.asarray([x["y"] for x in train], dtype=int))

    vb, tb = baseline(val), baseline(test)
    vn, tn = nonlinear_records(clf, val), nonlinear_records(clf, test)
    vc, tc = blend_records(vb, vn), blend_records(tb, tn)

    vd = f5.compare(vb, vc)
    td = f5.compare(tb, tc)
    full_td = f5.compare(tb, tn)
    blocks = f5.f3.paired_time_blocks(tb, tc)
    pos = sum(x["delta_logloss"] < 0 for x in blocks)
    neg = sum(x["delta_logloss"] > 0 for x in blocks)

    passed = bool(
        vd["logloss"] < 0 and vd["brier"] < 0 and vd["rps"] < 0
        and td["hits"] >= 0 and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
        and pos >= 3 and neg <= 1
    )

    return {
        "name": spec["name"], "selection": spec["selection"], "skip_newest_n": spec["skip_newest_n"],
        "source": {
            "first_date": rows[0]["date"], "last_date": rows[-1]["date"], "snapshot_rows": len(rows),
            "fixtures_sha256": snap_meta["fixtures_sha256"], "match_stats_sha256": snap_meta["match_stats_sha256"],
            **tech_meta,
        },
        "split": {
            "train_n": len(train), "val_n": len(val), "test_n": len(test),
            "train_dates": [train[0]["date"], train[-1]["date"]],
            "val_dates": [val[0]["date"], val[-1]["date"]],
            "test_dates": [test[0]["date"], test[-1]["date"]],
        },
        "validation_delta": vd,
        "test_delta": td,
        "full_nonlinear_test_delta_diagnostic": full_td,
        "positive_logloss_blocks": pos,
        "negative_logloss_blocks": neg,
        "gate_passed": passed,
    }


def weighted(blocks: list[dict], metric: str) -> float:
    den = sum(int(b["split"]["test_n"]) for b in blocks)
    return sum(int(b["split"]["test_n"]) * float(b["test_delta"][metric]) for b in blocks) / den


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    results = [build_block(b) for b in BLOCKS]
    total_n = sum(int(b["split"]["test_n"]) for b in results)
    aggregate = {
        "test_n": total_n,
        "delta_hits": sum(int(b["test_delta"]["hits"]) for b in results),
        "delta_accuracy_pp_weighted": weighted(results, "accuracy_pp"),
        "delta_logloss": weighted(results, "logloss"),
        "delta_brier": weighted(results, "brier"),
        "delta_rps": weighted(results, "rps"),
        "delta_draw_binary_logloss": weighted(results, "draw_binary_logloss"),
        "delta_draw_binary_brier": weighted(results, "draw_binary_brier"),
        "positive_logloss_blocks": sum(int(b["positive_logloss_blocks"]) for b in results),
        "negative_logloss_blocks": sum(int(b["negative_logloss_blocks"]) for b in results),
        "block_gates_passed": sum(bool(b["gate_passed"]) for b in results),
    }
    aggregate_pass = bool(
        aggregate["delta_hits"] >= 0
        and aggregate["delta_logloss"] < 0
        and aggregate["delta_brier"] < 0
        and aggregate["delta_rps"] < 0
        and aggregate["block_gates_passed"] >= 2
    )
    breakthrough_candidate = bool(
        aggregate_pass
        and aggregate["delta_accuracy_pp_weighted"] >= 1.0
        and sum(float(b["test_delta"]["accuracy_pp"]) >= 0 for b in results) >= 2
    )

    out = {
        "schema_version": "football3-r43j0-nonlinear-latent-state-v1",
        "status": "COMPLETE",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_NONLINEAR_LATENT_STATE_INTERACTION_MODEL_HISTORICAL_REPLICATION",
        "question": "Can a fixed-capacity nonlinear model of PIT raw latent goal state plus probabilistic-lineup technical context produce materially larger, repeatable OOS 1X2 gains than the current linear R43F5 outcome layer?",
        "governance": {
            "source_blocks_previously_consumed": True,
            "chronological_split_only": True,
            "test_used_for_hyperparameter_selection": False,
            "hyperparameters_fixed_before_scoring": True,
            "parameter_search": False,
            "threshold_search": False,
            "feature_search_after_test": False,
            "blend_alpha": BLEND_ALPHA,
            "blend_alpha_search": False,
            "no_manual_outcome_override": True,
            "no_manual_draw_override": True,
            "no_draw_threshold": True,
            "no_draw_class_weight": True,
            "odds_used": False,
            "r42l_lock_modified": False,
        },
        "model": {
            "family": "HistGradientBoostingClassifier multiclass log-loss",
            "hyperparameters": HGB_PARAMS,
            "raw_fields": RAW_FIELDS,
            "context_fields": f5.BASE_NAMES + f5.TECH_NAMES,
            "extra_fields": ["base_weighted_known", "context_weighted_known"],
            "primary_candidate": "fixed 0.5 geometric blend of R43F5 context-weighted half-tech baseline and nonlinear latent-state probability",
            "full_nonlinear_output": "diagnostic only; not primary gate",
        },
        "blocks": results,
        "aggregate": aggregate,
        "gate": {
            "passed": aggregate_pass,
            "breakthrough_candidate": breakthrough_candidate,
            "breakthrough_definition": "aggregate Top1 gain >=1.0pp, proper scores all improve, aggregate gate passes, and >=2/3 blocks have nonnegative Top1 delta",
            "action": (
                "FREEZE_R43J0_FOR_DISJOINT_FORWARD_CONFIRMATION" if breakthrough_candidate
                else "KEEP_AS_DEVELOPMENT_ONLY" if aggregate_pass
                else "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_THESE_TEST_BLOCKS"
            ),
        },
        "limitations": [
            "All three historical blocks have been consumed by earlier football3 research, so formal_weight is zero even if this architecture passes.",
            "A large historical effect is only a candidate breakthrough until frozen forward confirmation succeeds.",
            "This experiment changes model class, not source information; it cannot recover information absent from the PIT feature set.",
            "Market residual information is intentionally excluded and remains a separate future track.",
        ],
    }
    p = OUT / "summary_r43j0_nonlinear_latent_state.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "aggregate": aggregate, "gate": out["gate"]}, indent=2, ensure_ascii=False))
    shutil.rmtree(HERE / "scratch", ignore_errors=True)
    return out


def verify() -> None:
    d = json.loads((OUT / "summary_r43j0_nonlinear_latent_state.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    g = d["governance"]
    assert g["chronological_split_only"] is True
    assert g["test_used_for_hyperparameter_selection"] is False
    assert g["parameter_search"] is False and g["threshold_search"] is False
    assert g["blend_alpha"] == 0.5 and g["blend_alpha_search"] is False
    assert g["no_manual_outcome_override"] is True and g["no_manual_draw_override"] is True
    assert g["odds_used"] is False and g["r42l_lock_modified"] is False
    assert len(d["blocks"]) == 3 and all(int(b["split"]["test_n"]) >= 1000 for b in d["blocks"])
    print("R43J0 nonlinear latent-state contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command {cmd}")
