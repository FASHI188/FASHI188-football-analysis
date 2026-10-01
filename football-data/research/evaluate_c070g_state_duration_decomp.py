#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import evaluate_c070f_first_two as f

SCHEMA_VERSION = "C070G_STATE_DURATION_DECOMP_V1"
BOOT_REPS = 2000
BOOT_SEED = 7307

MARKOV = list(f.MARKOV_FEATURES)
LEGACY = list(f.SEMIMARKOV_FEATURES)
STATE_CONTROLS = [
    "is_00", "is_11", "is_tied_2plus", "is_margin1",
    "is_late_tied", "is_post_equalizer_tied",
]
STATE_REF = MARKOV + ["duration_frac", "duration_frac_sq"] + STATE_CONTROLS
TIED_INT = ["duration_x_00", "duration_x_11", "duration_x_tied_2plus"]
MARGIN_INT = ["duration_x_margin1"]
CONTEXT_INT = ["duration_x_late_tied", "duration_x_post_equalizer_tied"]
COMBINED_INT = TIED_INT + MARGIN_INT + CONTEXT_INT
FEATURES = {
    "markov": MARKOV,
    "legacy_semimarkov": LEGACY,
    "state_control_reference": STATE_REF,
    "tied_score_family": STATE_REF + TIED_INT,
    "margin_family": STATE_REF + MARGIN_INT,
    "context_family": STATE_REF + CONTEXT_INT,
    "combined_primary": STATE_REF + COMBINED_INT,
}


def _augmented_minute_rows(raw: pd.DataFrame, prematch: pd.DataFrame):
    look = raw.set_index("id")
    rows = []
    mismatch = []
    multi = 0
    for _, m in prematch.iterrows():
        r = look.loc[int(m.match_id)]
        goals, ok, recon = f.parse_goals(r)
        if not ok:
            mismatch.append({
                "match_id": int(m.match_id), "block": m.block,
                "reconstructed": list(recon), "official": [int(m.hg), int(m.ag)],
            })
            if m.block == "calibration":
                raise RuntimeError("calibration reconstruction mismatch")
            continue
        by = collections.defaultdict(list)
        for g in goals:
            by[g[0]].append(g[-2])

        gh = ga = 0
        duration = 0
        post_equalizer_tied = False
        home, away = int(m.home), int(m.away)
        for minute in range(90):
            diff = gh - ga
            base = f.minute_feat(
                float(m.lambda_home), float(m.lambda_away), minute, diff, duration
            )
            tied = gh == ga
            is_00 = float(gh == 0 and ga == 0)
            is_11 = float(gh == 1 and ga == 1)
            is_tied_2plus = float(tied and gh >= 2)
            is_margin1 = float(abs(diff) == 1)
            is_late_tied = float(tied and minute >= 60)
            is_posteq = float(tied and post_equalizer_tied)
            df = float(base["duration_frac"])
            extras = {
                "home_goals_state": int(gh),
                "away_goals_state": int(ga),
                "is_00": is_00,
                "is_11": is_11,
                "is_tied_2plus": is_tied_2plus,
                "is_margin1": is_margin1,
                "is_late_tied": is_late_tied,
                "is_post_equalizer_tied": is_posteq,
                "duration_x_00": df * is_00,
                "duration_x_11": df * is_11,
                "duration_x_tied_2plus": df * is_tied_2plus,
                "duration_x_margin1": df * is_margin1,
                "duration_x_late_tied": df * is_late_tied,
                "duration_x_post_equalizer_tied": df * is_posteq,
            }
            gs = by.get(minute, [])
            if len(gs) == 0:
                outcome, include = 0, True
            elif len(gs) == 1:
                outcome, include = (1 if gs[0] == home else 2), True
            else:
                outcome, include = -1, False
                multi += 1
            rows.append({
                "match_id": int(m.match_id), "date": m.date, "dt": m["dt"],
                "block": m.block, "minute": minute,
                "include_structural": include, "outcome": outcome,
                **base, **extras,
            })

            if gs:
                for scorer in gs:
                    before = gh - ga
                    if scorer == home:
                        gh += 1
                    elif scorer == away:
                        ga += 1
                    after = gh - ga
                    post_equalizer_tied = bool(after == 0 and abs(before) == 1)
                duration = 0
            else:
                duration += 1

    mids = sorted(x["match_id"] for x in mismatch if x["block"] == "warmup")
    if mids != f.EXPECTED_WARMUP_MISMATCH:
        raise RuntimeError(f"parser mismatch drift {mids}")
    frame = pd.DataFrame(rows).sort_values(["dt", "match_id", "minute"]).reset_index(drop=True)
    return frame, mismatch, multi


def _fit(train: pd.DataFrame, features: list[str]):
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.1, max_iter=5000, class_weight=None, random_state=0),
    )
    model.fit(train[features], train["outcome"].to_numpy(int))
    classes = list(model.named_steps["logisticregression"].classes_)
    if classes != [0, 1, 2]:
        raise RuntimeError(f"class coverage drift {classes}")
    return model


def _metric(frame: pd.DataFrame, p: np.ndarray):
    return {
        "rows": int(len(frame)),
        "matches": int(frame["match_id"].nunique()),
        "log_loss": float(log_loss(frame["outcome"].to_numpy(int), p, labels=[0, 1, 2])),
    }


def _delta_loss(frame: pd.DataFrame, pref: np.ndarray, pcand: np.ndarray):
    y = frame["outcome"].to_numpy(int)
    idx = np.arange(len(y))
    l0 = -np.log(np.clip(pref[idx, y], 1e-15, 1.0))
    l1 = -np.log(np.clip(pcand[idx, y], 1e-15, 1.0))
    return l1 - l0


def _bootstrap(frame: pd.DataFrame, pref: np.ndarray, pcand: np.ndarray, seed: int):
    d = _delta_loss(frame, pref, pcand)
    per_match = pd.DataFrame({"match_id": frame.match_id.to_numpy(), "d": d}).groupby("match_id").d.mean().to_numpy(float)
    rng = np.random.default_rng(seed)
    sims = np.empty(BOOT_REPS, dtype=float)
    n = len(per_match)
    for i in range(BOOT_REPS):
        sims[i] = float(per_match[rng.integers(0, n, size=n)].mean())
    return {
        "match_count": int(n),
        "mean_delta_log_loss": float(per_match.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": BOOT_REPS,
        "seed": int(seed),
    }


def _folds(frame: pd.DataFrame):
    dates = sorted(frame["date"].unique())
    if len(dates) < 20:
        raise RuntimeError("insufficient dates")
    idx = [max(1, min(len(dates)-1, int(len(dates)*q))) for q in (0.4, 0.6, 0.8)]
    cuts = [dates[i] for i in idx]
    return [
        ("fold_1", cuts[0], cuts[0], cuts[1]),
        ("fold_2", cuts[1], cuts[1], cuts[2]),
        ("fold_3", cuts[2], cuts[2], None),
    ]


def _support(frame: pd.DataFrame):
    return {
        "structural_rows": int(len(frame)),
        "matches": int(frame.match_id.nunique()),
        "state_rows": {
            k: int(frame[k].sum())
            for k in STATE_CONTROLS
        },
        "state_match_coverage": {
            k: int(frame.loc[frame[k] > 0, "match_id"].nunique())
            for k in STATE_CONTROLS
        },
    }


def run(db: Path, manifest_path: Path, out: Path):
    manifest = f.load_manifest(manifest_path)
    raw = f.load_first_two(db, manifest)
    if collections.Counter(raw.block) != {"warmup": 1202, "calibration": 1201}:
        raise RuntimeError("first-two payload count drift")
    prematch = f.build_prematch(raw)
    minute, mismatches, multi = _augmented_minute_rows(raw, prematch)
    structural = minute[minute.include_structural].copy().reset_index(drop=True)

    fold_specs = _folds(structural)
    folds = {}
    pooled_rows = []
    pooled_probs = {name: [] for name in FEATURES}
    wins = {name: 0 for name in FEATURES if name != "state_control_reference"}

    for fold_name, train_end, test_start, test_end in fold_specs:
        train = structural[structural.date < train_end].copy()
        test = structural[structural.date >= test_start].copy()
        if test_end is not None:
            test = test[test.date < test_end].copy()
        if train.empty or test.empty:
            raise RuntimeError(f"empty split {fold_name}")
        models = {name: _fit(train, feats) for name, feats in FEATURES.items()}
        probs = {name: models[name].predict_proba(test[FEATURES[name]]) for name in FEATURES}
        metrics = {name: _metric(test, probs[name]) for name in FEATURES}
        ref_ll = metrics["state_control_reference"]["log_loss"]
        deltas = {name: float(metrics[name]["log_loss"] - ref_ll) for name in FEATURES if name != "state_control_reference"}
        for name, d in deltas.items():
            wins[name] += int(d < 0)
        folds[fold_name] = {
            "train_date_max_exclusive": str(train_end),
            "test_date_min_inclusive": str(test_start),
            "test_date_max_exclusive": str(test_end) if test_end is not None else None,
            "train_rows": int(len(train)), "train_matches": int(train.match_id.nunique()),
            "test_rows": int(len(test)), "test_matches": int(test.match_id.nunique()),
            "metrics": metrics,
            "delta_log_loss_vs_state_control_reference": deltas,
        }
        pooled_rows.append(test)
        for name in FEATURES:
            pooled_probs[name].append(probs[name])

    all_test = pd.concat(pooled_rows, ignore_index=True)
    p = {name: np.vstack(parts) for name, parts in pooled_probs.items()}
    pooled_metrics = {name: _metric(all_test, p[name]) for name in FEATURES}
    ref = p["state_control_reference"]
    comparisons = {}
    seeds = {
        "markov": 7310,
        "legacy_semimarkov": 7311,
        "tied_score_family": 7312,
        "margin_family": 7313,
        "context_family": 7314,
        "combined_primary": BOOT_SEED,
    }
    for name in seeds:
        boot = _bootstrap(all_test, ref, p[name], seeds[name])
        comparisons[name] = {
            "reference": "state_control_reference",
            "delta_log_loss": float(pooled_metrics[name]["log_loss"] - pooled_metrics["state_control_reference"]["log_loss"]),
            "fold_wins": int(wins[name]),
            "bootstrap": boot,
        }

    primary = comparisons["combined_primary"]
    signal = bool(
        primary["delta_log_loss"] < 0
        and primary["bootstrap"]["ci90_high"] < 0
        and primary["fold_wins"] >= 2
    )

    # Separate legacy question: reproduce generic Semi-Markov against the original Markov baseline.
    legacy_boot = _bootstrap(all_test, p["markov"], p["legacy_semimarkov"], 7315)
    legacy_vs_markov = {
        "delta_log_loss": float(pooled_metrics["legacy_semimarkov"]["log_loss"] - pooled_metrics["markov"]["log_loss"]),
        "bootstrap": legacy_boot,
        "fold_wins": int(sum(
            folds[k]["metrics"]["legacy_semimarkov"]["log_loss"] < folds[k]["metrics"]["markov"]["log_loss"]
            for k in folds
        )),
    }

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "C070G_POSTVIEW_DEVELOPMENT_COMPLETE",
        "verdict": "C070G_STATE_DURATION_DECOMP_DEVELOPMENT_SIGNAL" if signal else "C070G_STATE_DURATION_DECOMP_STABLE_INCREMENT_NOT_ESTABLISHED",
        "identity": {
            "manifest_sha256": f.EXPECTED_MANIFEST_SHA,
            "warmup": 1202, "calibration": 1201, "confirmation": 1597,
        },
        "payload_boundary": {
            "opened_blocks": ["warmup", "calibration"],
            "confirmation_payload_opened": False,
            "confirmation_score_rows_read": 0,
            "confirmation_event_rows_read": 0,
        },
        "parser": {
            "warmup_mismatch_ids": sorted(x["match_id"] for x in mismatches if x["block"] == "warmup"),
            "calibration_mismatch_count": int(sum(x["block"] == "calibration" for x in mismatches)),
            "multi_goal_bins_excluded": int(multi),
        },
        "support": _support(structural),
        "folds": folds,
        "pooled_metrics": pooled_metrics,
        "comparisons_vs_state_control_reference": comparisons,
        "legacy_semimarkov_vs_markov": legacy_vs_markov,
        "primary_gate": {
            "comparison": "combined_primary minus state_control_reference",
            "delta_log_loss_lt_0": bool(primary["delta_log_loss"] < 0),
            "bootstrap_ci90_high_lt_0": bool(primary["bootstrap"]["ci90_high"] < 0),
            "fold_wins_ge_2": bool(primary["fold_wins"] >= 2),
            "development_signal": signal,
        },
        "boundary": {
            "postview_development_only": True,
            "fresh_confirmation_claim_allowed": False,
            "confirmation_scored": False,
            "formal_weight": 0,
            "formal_promotion_allowed": False,
            "hyperparameter_search": False,
            "threshold_search": False,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    run(a.db, a.manifest, a.out)
