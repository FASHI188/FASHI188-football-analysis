#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071

FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_POOL = 72180
SELECT_N = 20000
C = 0.1
ALPHA = 1.0
BOOT_REPS = 3000
BOOT_SEED = 73001
TOTALS = tuple(range(1, 7))
EVEN_TOTALS = (2, 4, 6)
BASE = list(c071.BASE)


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


c071.utc = utc_ns


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ids_sha(frame: pd.DataFrame) -> str:
    payload = "\n".join(str(int(x)) for x in frame["id"].tolist()) + "\n"
    return hashlib.sha256(payload.encode()).hexdigest()


def empty_opportunity_events() -> pd.DataFrame:
    return pd.DataFrame(columns=["team_id", "available_at", *c071.OPP_METRICS])


def model():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=C, solver="lbfgs", max_iter=2000, class_weight=None, random_state=0),
    )


def support(total: int) -> list[int]:
    return list(range(-total, total + 1, 2))


def aligned_predict(fitted, X: pd.DataFrame, classes: list[int]) -> np.ndarray:
    raw = fitted.predict_proba(X)
    fitted_classes = [int(x) for x in fitted.named_steps["logisticregression"].classes_]
    out = np.zeros((len(X), len(classes)), dtype=float)
    pos = {value: idx for idx, value in enumerate(classes)}
    for j, value in enumerate(fitted_classes):
        if value in pos:
            out[:, pos[value]] = raw[:, j]
    out = np.clip(out, 1e-15, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def fit_fingerprint(models: dict[int, object]) -> str:
    payload = {}
    for total, fitted in sorted(models.items()):
        lr = fitted.named_steps["logisticregression"]
        imp = fitted.named_steps["simpleimputer"]
        scaler = fitted.named_steps["standardscaler"]
        payload[str(total)] = {
            "classes": [int(x) for x in lr.classes_],
            "coef": np.asarray(lr.coef_, float).round(14).tolist(),
            "intercept": np.asarray(lr.intercept_, float).round(14).tolist(),
            "imputer_statistics": np.asarray(imp.statistics_, float).round(14).tolist(),
            "scaler_mean": np.asarray(scaler.mean_, float).round(14).tolist(),
            "scaler_scale": np.asarray(scaler.scale_, float).round(14).tolist(),
        }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_selected_labels(path: Path, selected_ids: list[int]) -> pd.DataFrame:
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=["id", "goals_home", "goals_away"],
        filter=ds.field("id").isin([int(x) for x in selected_ids]),
    )
    out = table.to_pandas()
    out["id"] = out["id"].astype("int64")
    if out["id"].duplicated().any():
        raise RuntimeError("duplicate selected confirmation label id")
    returned = set(int(x) for x in out["id"])
    expected = set(int(x) for x in selected_ids)
    if returned != expected or len(out) != SELECT_N:
        raise RuntimeError(f"selected label projection mismatch returned={len(out)} expected={SELECT_N}")
    if out[["goals_home", "goals_away"]].isna().any().any():
        raise RuntimeError("selected confirmation contains null goal labels")
    return out


def empirical_tables(dev: pd.DataFrame):
    pooled = {}
    by_league = {}
    for total in TOTALS:
        classes = support(total)
        sub = dev[dev["total"] == total]
        pooled[total] = {d: int((sub["D"] == d).sum()) for d in classes}
        for league, group in sub.groupby("league_id"):
            by_league[(int(league), total)] = {d: int((group["D"] == d).sum()) for d in classes}
    return pooled, by_league


def baseline_probability(leagues: np.ndarray, total: int, pooled, by_league) -> np.ndarray:
    classes = support(total)
    out = np.zeros((len(leagues), len(classes)), dtype=float)
    for i, league in enumerate(leagues):
        counts = by_league.get((int(league), total), pooled[total])
        vals = np.asarray([float(counts.get(d, 0)) + ALPHA for d in classes], dtype=float)
        out[i] = vals / vals.sum()
    return out


def component_rows(y: np.ndarray, p: np.ndarray, classes: list[int]) -> pd.DataFrame:
    pos = {value: idx for idx, value in enumerate(classes)}
    yi = np.asarray([pos[int(v)] for v in y], dtype=int)
    one = np.zeros_like(p)
    one[np.arange(len(y)), yi] = 1.0
    safe = np.clip(p, 1e-15, 1.0)
    ll = -np.log(safe[np.arange(len(y)), yi])
    brier = ((p - one) ** 2).sum(axis=1)
    if len(classes) > 1:
        rps = ((np.cumsum(p, axis=1)[:, :-1] - np.cumsum(one, axis=1)[:, :-1]) ** 2).sum(axis=1) / (len(classes) - 1)
    else:
        rps = np.zeros(len(y))
    top1 = (np.argmax(p, axis=1) == yi).astype(float)
    return pd.DataFrame({"logloss": ll, "brier": brier, "rps": rps, "top1": top1})


def summarize(frame: pd.DataFrame) -> dict[str, float]:
    return {c: float(frame[c].mean()) for c in frame.columns}


def binary_diag(y_draw: np.ndarray, p_draw: np.ndarray) -> dict[str, float | None]:
    p = np.clip(np.asarray(p_draw, float), 1e-15, 1 - 1e-15)
    y = np.asarray(y_draw, int)
    ll = float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))
    brier = float(np.mean((p - y) ** 2))
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None
    return {"n": int(len(y)), "logloss": ll, "brier": brier, "auc": auc}


def paired_bootstrap(deltas: np.ndarray) -> dict[str, float | int]:
    d = np.asarray(deltas, float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    chunk = 50
    cursor = 0
    while cursor < BOOT_REPS:
        nrep = min(chunk, BOOT_REPS - cursor)
        idx = rng.integers(0, len(d), size=(nrep, len(d)))
        sims[cursor:cursor+nrep] = d[idx].mean(axis=1)
        cursor += nrep
    return {
        "n": int(len(d)), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "probability_delta_lt_zero": float(np.mean(sims < 0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    fp, sp, out_dir = Path(args.fixtures), Path(args.stats), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if sha256(fp) != FIX_SHA or sha256(sp) != STAT_SHA:
        raise RuntimeError("pinned source SHA mismatch")

    # Zero-label identity reconstruction. No target goal/result columns are selected here.
    fixtures = pd.read_parquet(fp, columns=audit.FIXTURE_COLS)
    fixtures["date_utc"] = utc_ns(fixtures["date_utc"])
    fixtures = fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(["date_utc", "id"]).reset_index(drop=True)
    fixtures["id"] = fixtures["id"].astype("int64")
    stats = pd.read_parquet(sp, columns=audit.STAT_COLS)
    stats["known_at"] = utc_ns(stats["known_at"])
    stats = stats.dropna(subset=["fixture_id", "known_at"])
    eligible, _ = c071.eligible_identities(fixtures, stats)
    pool = eligible[(eligible.date_utc >= DEV_CUTOFF) & (eligible.date_utc < CONFIRM_END)].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    if len(pool) != EXPECTED_POOL:
        raise RuntimeError(f"C071 confirmation pool drift {len(pool)} != {EXPECTED_POOL}")
    selected = pool.iloc[:SELECT_N].copy().reset_index(drop=True)
    reserve = pool.iloc[SELECT_N:].copy().reset_index(drop=True)
    if len(reserve) != EXPECTED_POOL - SELECT_N:
        raise RuntimeError("reserve identity count drift")
    selected[audit.FIXTURE_COLS].to_csv(out_dir / "selected_confirmation_identity.csv", index=False)

    # Development fit uses only pre-2024 labels and is completed before any selected confirmation label is read.
    dev_labels = c071.read_dev_labels(fp)
    dev_id = eligible[eligible.date_utc < DEV_CUTOFF].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    dev = dev_id.merge(dev_labels[["id", "goals_home", "goals_away"]], on="id", how="left", validate="one_to_one").dropna(subset=["goals_home", "goals_away"]).reset_index(drop=True)
    dev["goals_home"] = dev.goals_home.astype(int)
    dev["goals_away"] = dev.goals_away.astype(int)
    dev["total"] = dev.goals_home + dev.goals_away
    dev["D"] = dev.goals_home - dev.goals_away
    dev_rteam, dev_rleague, _ = c071.result_events(dev_id, dev_labels)
    dev_feat = c071.build_features(dev, dev_rteam, dev_rleague, empty_opportunity_events())
    dev_feat["total"] = dev.total.to_numpy(int)
    dev_feat["D"] = dev.D.to_numpy(int)

    models = {}
    training_rows = {}
    for total in TOTALS:
        tr = dev_feat[dev_feat.total == total].copy()
        classes = support(total)
        observed = sorted(int(x) for x in tr.D.unique())
        if observed != classes:
            raise RuntimeError(f"development conditional-D support incomplete T={total}: observed={observed} expected={classes}")
        fitted = model()
        fitted.fit(tr[BASE], tr.D.to_numpy(int))
        models[total] = fitted
        training_rows[str(total)] = int(len(tr))
    pooled_counts, league_counts = empirical_tables(dev)
    fingerprint = fit_fingerprint(models)

    # Confirmation labels are opened only now, after model/specification freeze.
    selected_labels = read_selected_labels(fp, [int(x) for x in selected.id.tolist()])
    test = selected.merge(selected_labels, on="id", how="inner", validate="one_to_one")
    test["goals_home"] = test.goals_home.astype(int)
    test["goals_away"] = test.goals_away.astype(int)
    test["total"] = test.goals_home + test.goals_away
    test["D"] = test.goals_home - test.goals_away

    # Causal confirmation feature state: development outcomes plus earlier selected outcomes only.
    sel_rteam, sel_rleague, _ = c071.result_events(selected, selected_labels)
    all_rteam = pd.concat([dev_rteam, sel_rteam], ignore_index=True)
    all_rleague = pd.concat([dev_rleague, sel_rleague], ignore_index=True)
    test_feat = c071.build_features(test, all_rteam, all_rleague, empty_opportunity_events())
    test_feat["total"] = test.total.to_numpy(int)
    test_feat["D"] = test.D.to_numpy(int)

    rows = []
    per_total = {}
    even_y, even_pb, even_pc = [], [], []
    base_draw_calls = cand_draw_calls = actual_even_draws = base_draw_hits = cand_draw_hits = 0
    for total in TOTALS:
        te = test_feat[test_feat.total == total].copy()
        if not len(te):
            continue
        classes = support(total)
        y = te.D.to_numpy(int)
        pc = aligned_predict(models[total], te[BASE], classes)
        pb = baseline_probability(te.league_id.to_numpy(), total, pooled_counts, league_counts)
        bc = component_rows(y, pb, classes)
        cc = component_rows(y, pc, classes)
        delta = cc - bc
        temp = te[["id", "date_utc", "league_id", "total", "D"]].reset_index(drop=True)
        for name in bc.columns:
            temp[f"baseline_{name}"] = bc[name].to_numpy()
            temp[f"candidate_{name}"] = cc[name].to_numpy()
            temp[f"delta_{name}"] = delta[name].to_numpy()
        rows.append(temp)
        per_total[str(total)] = {
            "n": int(len(te)),
            "baseline": summarize(bc),
            "candidate": summarize(cc),
            "delta_candidate_minus_baseline": {c: float(delta[c].mean()) for c in delta.columns},
        }
        if total in EVEN_TOTALS:
            zero = classes.index(0)
            yd = (y == 0).astype(int)
            even_y.append(yd); even_pb.append(pb[:, zero]); even_pc.append(pc[:, zero])
            bpick = np.argmax(pb, axis=1) == zero
            cpick = np.argmax(pc, axis=1) == zero
            base_draw_calls += int(bpick.sum()); cand_draw_calls += int(cpick.sum())
            actual_even_draws += int(yd.sum())
            base_draw_hits += int((bpick & (yd == 1)).sum()); cand_draw_hits += int((cpick & (yd == 1)).sum())

    primary = pd.concat(rows, ignore_index=True).sort_values(["date_utc", "id"]).reset_index(drop=True)
    baseline_summary = {m: float(primary[f"baseline_{m}"].mean()) for m in ("logloss", "brier", "rps", "top1")}
    candidate_summary = {m: float(primary[f"candidate_{m}"].mean()) for m in ("logloss", "brier", "rps", "top1")}
    delta_summary = {m: float(primary[f"delta_{m}"].mean()) for m in ("logloss", "brier", "rps", "top1")}
    boot = paired_bootstrap(primary["delta_logloss"].to_numpy(float))

    ydraw = np.concatenate(even_y); pbdraw = np.concatenate(even_pb); pcdraw = np.concatenate(even_pc)
    bd = binary_diag(ydraw, pbdraw); cd = binary_diag(ydraw, pcdraw)
    draw_delta = {
        "logloss": float(cd["logloss"] - bd["logloss"]),
        "brier": float(cd["brier"] - bd["brier"]),
        "auc": None if bd["auc"] is None or cd["auc"] is None else float(cd["auc"] - bd["auc"]),
    }
    base_recall = base_draw_hits / actual_even_draws if actual_even_draws else None
    cand_recall = cand_draw_hits / actual_even_draws if actual_even_draws else None

    proper_checks = {
        "delta_logloss_lt_0": delta_summary["logloss"] < 0,
        "bootstrap_ci90_high_lt_0": boot["ci90_high"] < 0,
        "delta_brier_le_0": delta_summary["brier"] <= 0,
        "delta_rps_le_0": delta_summary["rps"] <= 0,
    }
    top1_checks = {
        "delta_top1_ge_0": delta_summary["top1"] >= 0,
        "evenT_draw_logloss_delta_le_0": draw_delta["logloss"] <= 0,
        "evenT_draw_brier_delta_le_0": draw_delta["brier"] <= 0,
    }
    passed = all(proper_checks.values()) and all(top1_checks.values())

    # Chronological quartiles are diagnostics only; they never alter the frozen candidate.
    quartiles = {}
    for q, idx in enumerate(np.array_split(np.arange(len(primary)), 4), start=1):
        part = primary.iloc[idx]
        quartiles[f"q{q}"] = {
            "n": int(len(part)),
            "start": str(part.date_utc.min()), "end": str(part.date_utc.max()),
            "delta_logloss": float(part.delta_logloss.mean()),
            "delta_brier": float(part.delta_brier.mean()),
            "delta_rps": float(part.delta_rps.mean()),
            "delta_top1": float(part.delta_top1.mean()),
        }

    summary = {
        "schema_version": "C073A_FRESH20K_CONDITIONAL_D_V1",
        "status": "PASS_FRESH20K_CONDITIONAL_D" if passed else "FAIL_FRESH20K_CONDITIONAL_D_NO_STABLE_INCREMENT",
        "scientific_scope": "conditional P(D|T,X) component only; no P(T), no unified score matrix, no exact-score claim",
        "source": {"fixtures_sha256": sha256(fp), "match_stats_sha256": sha256(sp)},
        "identity": {
            "parent_confirmation_pool": int(len(pool)),
            "selected_confirmation": int(len(selected)),
            "selected_ids_sha256": ids_sha(selected),
            "selected_start": str(selected.date_utc.min()),
            "selected_end": str(selected.date_utc.max()),
            "remaining_reserve": int(len(reserve)),
            "remaining_ids_sha256": ids_sha(reserve),
            "remaining_labels_opened": False,
        },
        "label_boundary": {
            "development_goal_rows_before_2024": int(len(dev)),
            "candidate_models_fit_before_confirmation_label_read": True,
            "frozen_model_fingerprint_before_confirmation_read": fingerprint,
            "selected_confirmation_goal_rows_returned": int(len(selected_labels)),
            "reserve_52180_goal_rows_returned": 0,
            "confirmation_scored_rows_primary_T1_to_T6": int(len(primary)),
            "T0_diagnostic_rows": int((test.total == 0).sum()),
            "T7plus_excluded_rows": int((test.total >= 7).sum()),
        },
        "training_rows_by_total": training_rows,
        "features": BASE,
        "candidate": {"C": C, "hyperparameter_search": False, "feature_search": False, "manual_draw_or_1_1_boost": False},
        "baseline": {"alpha": ALPHA, "family": "league_smoothed_empirical_conditional_D_with_pooled_fallback"},
        "primary": {
            "n": int(len(primary)),
            "baseline": baseline_summary,
            "candidate": candidate_summary,
            "delta_candidate_minus_baseline": delta_summary,
            "paired_bootstrap_delta_logloss": boot,
            "per_total": per_total,
            "chronological_quartiles_diagnostic": quartiles,
        },
        "evenT_D0": {
            "n": int(len(ydraw)), "actual_draws": int(actual_even_draws),
            "baseline_binary": bd, "candidate_binary": cd, "delta_candidate_minus_baseline": draw_delta,
            "baseline_D0_top1_calls": int(base_draw_calls), "candidate_D0_top1_calls": int(cand_draw_calls),
            "baseline_actual_draw_top1_hits": int(base_draw_hits), "candidate_actual_draw_top1_hits": int(cand_draw_hits),
            "baseline_actual_draw_top1_recall": base_recall, "candidate_actual_draw_top1_recall": cand_recall,
        },
        "gate": {"proper_score_checks": proper_checks, "top1_support_checks": top1_checks, "all_pass": passed},
        "boundaries": {
            "dynamic_multiline_OU_tested_here": False,
            "unified_matrix_generated": False,
            "exact_score_top1_claim_allowed": False,
            "formal_weight": 0,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "CURRENT_changed": False,
            "formal_model_changed": False,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
