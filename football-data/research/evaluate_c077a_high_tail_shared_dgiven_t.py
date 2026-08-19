#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071

SCHEMA = "C077A_HIGH_TAIL_SHARED_DGIVENT_DEVELOPMENT_V1"
FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM = 72180
TEST_YEARS = [2019, 2020, 2021, 2022, 2023]
MIN_TRAIN_TAIL = 500
MIN_TEST_TAIL = 150
MIN_TRAIN_TRIALS = 3500
MIN_POOLED_TEST = 1200
ALPHA = 1.0
C_FIXED = 0.1
BOOT_REPS = 3000
BOOT_SEED = 77001
BASE_FEATURES = list(c071.BASE)
FEATURES = BASE_FEATURES + ["exact_total"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


def empty_opportunity_events() -> pd.DataFrame:
    return pd.DataFrame(columns=["team_id", "available_at", *c071.OPP_METRICS])


def pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(
            C=C_FIXED,
            solver="lbfgs",
            max_iter=2000,
            class_weight=None,
            random_state=0,
        ),
    )


def expand_goal_trials(frame: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    repeats = frame["exact_total"].to_numpy(int)
    if np.any(repeats <= 0):
        raise RuntimeError("nonpositive exact total in high-tail trial expansion")
    x = frame[FEATURES].loc[frame.index.repeat(repeats)].reset_index(drop=True)
    y_parts = []
    for h, t in zip(frame["goals_home"].to_numpy(int), repeats):
        if h < 0 or h > t:
            raise RuntimeError("invalid home goals relative to total")
        y_parts.append(np.r_[np.ones(h, dtype=int), np.zeros(t - h, dtype=int)])
    y = np.concatenate(y_parts) if y_parts else np.asarray([], dtype=int)
    if len(x) != len(y):
        raise RuntimeError("expanded trial length mismatch")
    return x, y


def binomial_vector(total: int, p_home: float) -> np.ndarray:
    p = float(np.clip(p_home, 1e-12, 1.0 - 1e-12))
    vals = np.asarray([
        math.comb(total, h) * (p ** h) * ((1.0 - p) ** (total - h))
        for h in range(total + 1)
    ], dtype=float)
    s = float(vals.sum())
    if not math.isfinite(s) or s <= 0:
        raise RuntimeError("invalid binomial probability vector")
    return vals / s


def empirical_tables(train: pd.DataFrame) -> dict[int, np.ndarray]:
    tables = {}
    for total, g in train.groupby("exact_total", sort=True):
        total = int(total)
        counts = np.zeros(total + 1, dtype=float)
        for h in g["goals_home"].to_numpy(int):
            if 0 <= h <= total:
                counts[h] += 1.0
        vals = counts + ALPHA
        tables[total] = vals / vals.sum()
    return tables


def baseline_vector(total: int, tables: dict[int, np.ndarray]) -> np.ndarray:
    if total in tables:
        return np.asarray(tables[total], dtype=float)
    return np.full(total + 1, 1.0 / (total + 1), dtype=float)


def score_row(y_h: int, probs: np.ndarray) -> dict[str, float]:
    p = np.asarray(probs, dtype=float)
    if y_h < 0 or y_h >= len(p):
        raise RuntimeError("target outside legal H support")
    if np.any(p < 0) or not np.isfinite(p).all():
        raise RuntimeError("invalid support probability")
    p = p / p.sum()
    one = np.zeros_like(p)
    one[y_h] = 1.0
    ll = -math.log(max(float(p[y_h]), 1e-300))
    brier = float(np.square(p - one).sum())
    if len(p) > 1:
        rps = float(np.square(np.cumsum(p)[:-1] - np.cumsum(one)[:-1]).sum() / (len(p) - 1))
    else:
        rps = 0.0
    top1 = float(int(np.argmax(p)) == y_h)
    return {"logloss": ll, "brier": brier, "rps": rps, "top1": top1}


def binary_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float | int | None]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-15, 1 - 1e-15)
    if len(y) == 0:
        return {"n": 0, "logloss": None, "brier": None}
    ll = float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))
    brier = float(np.mean(np.square(p - y)))
    return {"n": int(len(y)), "logloss": ll, "brier": brier}


def paired_bootstrap(delta: np.ndarray) -> dict[str, float | int]:
    d = np.asarray(delta, dtype=float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    for i in range(BOOT_REPS):
        idx = rng.integers(0, len(d), size=len(d))
        sims[i] = float(d[idx].mean())
    return {
        "n": int(len(d)), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "probability_delta_lt_zero": float(np.mean(sims < 0)),
    }


def structural_audit(total: int, probs: np.ndarray) -> tuple[int, float]:
    failures = 0
    p = np.asarray(probs, dtype=float)
    prob_residual = abs(float(p.sum()) - 1.0)
    for h, value in enumerate(p):
        d = 2 * h - total
        away = total - h
        if value < 0 or not math.isfinite(float(value)):
            failures += 1
        if (total - d) % 2 != 0 or h < 0 or away < 0:
            failures += 1
        if (total + d) // 2 != h or (total - d) // 2 != away:
            failures += 1
    return failures, prob_residual


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    fp = Path(a.fixtures); sp = Path(a.stats); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    if sha256(fp) != FIX_SHA or sha256(sp) != STAT_SHA:
        raise RuntimeError("pinned source SHA mismatch")

    c071.utc = utc_ns
    fixtures = pd.read_parquet(fp, columns=audit.FIXTURE_COLS)
    fixtures["date_utc"] = utc_ns(fixtures["date_utc"])
    fixtures = fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(["date_utc", "id"]).reset_index(drop=True)
    fixtures["id"] = fixtures.id.astype("int64")
    stats = pd.read_parquet(sp, columns=audit.STAT_COLS)
    stats["known_at"] = utc_ns(stats["known_at"])
    stats = stats.dropna(subset=["fixture_id", "known_at"])
    eligible, _ = c071.eligible_identities(fixtures, stats)
    sealed = eligible[(eligible.date_utc >= DEV_CUTOFF) & (eligible.date_utc < CONFIRM_END)]
    if len(sealed) != EXPECTED_CONFIRM:
        raise RuntimeError(f"sealed identity drift {len(sealed)} != {EXPECTED_CONFIRM}")

    labels = c071.read_dev_labels(fp)
    if len(labels) and not (labels.date_utc < DEV_CUTOFF).all():
        raise RuntimeError("development label horizon breach")
    dev_id = eligible[eligible.date_utc < DEV_CUTOFF].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    dev = dev_id.merge(labels[["id", "goals_home", "goals_away"]], on="id", how="left", validate="one_to_one")
    dev = dev.dropna(subset=["goals_home", "goals_away"]).copy().reset_index(drop=True)
    dev["goals_home"] = dev.goals_home.astype(int)
    dev["goals_away"] = dev.goals_away.astype(int)
    dev["exact_total"] = dev.goals_home + dev.goals_away
    dev["D"] = dev.goals_home - dev.goals_away
    dev["calendar_year"] = dev.date_utc.dt.year.astype(int)

    # Strict-PIT result history: all event timestamps are kickoff+105m and snapshots use only events before each target kickoff.
    rteam, rleague, _ = c071.result_events(dev_id, labels)
    feat = c071.build_features(dev, rteam, rleague, empty_opportunity_events())
    feat["goals_home"] = dev.goals_home.to_numpy(int)
    feat["goals_away"] = dev.goals_away.to_numpy(int)
    feat["exact_total"] = dev.exact_total.to_numpy(int)
    feat["D"] = dev.D.to_numpy(int)
    feat["calendar_year"] = dev.calendar_year.to_numpy(int)
    tail = feat[feat.exact_total >= 7].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)

    coverage = {}
    coverage_pass = True
    for year in TEST_YEARS:
        tr = tail[tail.calendar_year < year]
        te = tail[tail.calendar_year == year]
        trials = int(tr.exact_total.sum())
        coverage[str(year)] = {
            "train_tail_rows": int(len(tr)),
            "train_goal_allocation_trials": trials,
            "test_tail_rows": int(len(te)),
            "train_exact_T_values": [int(x) for x in sorted(tr.exact_total.unique())],
            "test_exact_T_values": [int(x) for x in sorted(te.exact_total.unique())],
        }
        coverage_pass &= len(tr) >= MIN_TRAIN_TAIL and len(te) >= MIN_TEST_TAIL and trials >= MIN_TRAIN_TRIALS
    pooled_expected = int(sum(v["test_tail_rows"] for v in coverage.values()))
    coverage_pass &= pooled_expected >= MIN_POOLED_TEST

    boundaries = {
        "exact_tail_created": False,
        "C076D_fresh_score_values_opened": False,
        "C075C_consumed_tail_labels_used": False,
        "C075E_consumed_tail_labels_used": False,
        "C071_post2024_goal_labels_opened": False,
        "C071_sealed_identity_count": int(len(sealed)),
        "C071_reserve_52180_opened": False,
        "C070F_confirmation1597_opened": False,
        "A05_opened": False,
        "protected_opened": False,
        "unified_matrix_generated": False,
        "formal_weight": 0,
    }

    if not coverage_pass:
        summary = {
            "schema_version": SCHEMA,
            "status": "STOP_COVERAGE",
            "scientific_effect_evaluated": False,
            "formal_weight": 0,
            "coverage": coverage,
            "pooled_test_tail_expected": pooled_expected,
            "boundaries": boundaries,
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    fold_summaries = {}
    pooled_rows = []
    max_prob_residual = 0.0
    mapping_failures = 0

    for year in TEST_YEARS:
        tr = tail[tail.calendar_year < year].copy()
        te = tail[tail.calendar_year == year].copy()
        baseline_tables = empirical_tables(tr)
        x_trials, y_trials = expand_goal_trials(tr)
        if sorted(np.unique(y_trials).tolist()) != [0, 1]:
            raise RuntimeError(f"candidate binary support incomplete in training year {year}")
        model = pipeline()
        model.fit(x_trials, y_trials)
        classes = list(model.named_steps["logisticregression"].classes_.astype(int))
        one_idx = classes.index(1)
        p_home = model.predict_proba(te[FEATURES])[:, one_idx]

        fold_records = []
        even_y = []
        even_pb = []
        even_pc = []
        actual_draws = 0
        candidate_draw_top1_hits = 0

        for i, row in enumerate(te.itertuples(index=False)):
            total = int(row.exact_total)
            h = int(row.goals_home)
            pb = baseline_vector(total, baseline_tables)
            pc = binomial_vector(total, float(p_home[i]))
            fb, rb = structural_audit(total, pb)
            fc, rc = structural_audit(total, pc)
            mapping_failures += fb + fc
            max_prob_residual = max(max_prob_residual, rb, rc)
            mb = score_row(h, pb)
            mc = score_row(h, pc)
            d = 2 * h - total
            rec = {
                "id": int(row.id), "date_utc": str(row.date_utc), "league_id": int(row.league_id),
                "year": int(year), "exact_total": total, "H": h, "D": d,
                "baseline_ll": mb["logloss"], "candidate_ll": mc["logloss"],
                "delta_ll": mc["logloss"] - mb["logloss"],
                "baseline_brier": mb["brier"], "candidate_brier": mc["brier"],
                "baseline_rps": mb["rps"], "candidate_rps": mc["rps"],
                "baseline_top1": mb["top1"], "candidate_top1": mc["top1"],
                "candidate_p_home_goal_share": float(p_home[i]),
            }
            fold_records.append(rec)
            if total % 2 == 0:
                draw_h = total // 2
                yd = int(h == draw_h)
                even_y.append(yd)
                even_pb.append(float(pb[draw_h]))
                even_pc.append(float(pc[draw_h]))
                if yd:
                    actual_draws += 1
                    if int(np.argmax(pc)) == draw_h:
                        candidate_draw_top1_hits += 1

        fr = pd.DataFrame(fold_records)
        pooled_rows.append(fr)
        bsum = {
            "logloss": float(fr.baseline_ll.mean()),
            "brier": float(fr.baseline_brier.mean()),
            "rps": float(fr.baseline_rps.mean()),
            "top1": float(fr.baseline_top1.mean()),
        }
        csum = {
            "logloss": float(fr.candidate_ll.mean()),
            "brier": float(fr.candidate_brier.mean()),
            "rps": float(fr.candidate_rps.mean()),
            "top1": float(fr.candidate_top1.mean()),
        }
        fold_summaries[str(year)] = {
            "train_tail_rows": int(len(tr)),
            "train_goal_allocation_trials": int(len(y_trials)),
            "test_tail_rows": int(len(te)),
            "baseline": bsum,
            "candidate": csum,
            "delta_candidate_minus_baseline": {m: float(csum[m] - bsum[m]) for m in bsum},
            "even_T_draw_baseline": binary_metrics(np.asarray(even_y), np.asarray(even_pb)),
            "even_T_draw_candidate": binary_metrics(np.asarray(even_y), np.asarray(even_pc)),
            "actual_even_T_draws": int(actual_draws),
            "candidate_draw_top1_recall": float(candidate_draw_top1_hits / actual_draws) if actual_draws else None,
        }

    rows = pd.concat(pooled_rows, ignore_index=True)
    rows.to_csv(out / "row_metrics.csv", index=False)
    baseline = {
        "logloss": float(rows.baseline_ll.mean()),
        "brier": float(rows.baseline_brier.mean()),
        "rps": float(rows.baseline_rps.mean()),
        "top1": float(rows.baseline_top1.mean()),
    }
    candidate = {
        "logloss": float(rows.candidate_ll.mean()),
        "brier": float(rows.candidate_brier.mean()),
        "rps": float(rows.candidate_rps.mean()),
        "top1": float(rows.candidate_top1.mean()),
    }
    delta = {m: float(candidate[m] - baseline[m]) for m in baseline}
    bootstrap = paired_bootstrap(rows.delta_ll.to_numpy(float))
    fold_wins = int(sum(fold_summaries[str(y)]["delta_candidate_minus_baseline"]["logloss"] < 0 for y in TEST_YEARS))
    gate = {
        "pooled_logloss_improves": bool(delta["logloss"] < 0),
        "bootstrap_90pct_upper_below_zero": bool(bootstrap["ci90_high"] < 0),
        "pooled_brier_nonworse": bool(delta["brier"] <= 0),
        "pooled_rps_nonworse": bool(delta["rps"] <= 0),
        "fold_logloss_wins_at_least_4_of_5": bool(fold_wins >= 4),
        "probability_sum_residual_within_1e_10": bool(max_prob_residual <= 1e-10),
        "all_support_mapping_checks_pass": bool(mapping_failures == 0),
    }
    passed = all(gate.values())

    summary = {
        "schema_version": SCHEMA,
        "status": "SCIENTIFIC_COMPONENT_PASS_DEVELOPMENT_ONLY" if passed else "FAIL_PARK",
        "scientific_effect_evaluated": True,
        "formal_weight": 0,
        "claim_boundary": "high-tail D|T component only. Exact-tail remains blocked, so unified matrix and exact score remain unavailable regardless of this result.",
        "population": {
            "eligible_pre2024_completed": int(len(dev)),
            "high_tail_rows": int(len(tail)),
            "pooled_oos_tail_rows": int(len(rows)),
            "coverage": coverage,
        },
        "baseline": baseline,
        "candidate": candidate,
        "delta_candidate_minus_baseline": delta,
        "paired_bootstrap_logloss_delta": bootstrap,
        "fold_logloss_wins": fold_wins,
        "folds": fold_summaries,
        "structural_audit": {
            "mapping_failures": int(mapping_failures),
            "max_probability_sum_abs_residual": float(max_prob_residual),
        },
        "gate": gate,
        "candidate_spec": {
            "family": "shared binomial H|T,X",
            "C": C_FIXED,
            "features": FEATURES,
            "feature_search": False,
            "C_search": False,
            "interaction_search": False,
            "per_T_models": False,
            "manual_draw_or_score_boost": False,
        },
        "boundaries": boundaries,
        "stopping_rule": "If FAIL, do not repair on these OOS labels by tuning C/features/interactions or separate per-T models.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": summary["status"],
        "pooled_oos_tail_rows": int(len(rows)),
        "baseline": baseline,
        "candidate": candidate,
        "delta": delta,
        "bootstrap": bootstrap,
        "fold_wins": fold_wins,
        "structural_audit": summary["structural_audit"],
        "gate": gate,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
