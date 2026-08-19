#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071
import evaluate_c073a_fresh20k_conditional_d as c073a

FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_POOL = 72180
SELECT_N = 20000
EXPECTED_SELECTED_SHA = "aaff97c9eaca165410e99c3b4a3d49384234280d37304159ef79edea762d88e5"
EXPECTED_COMPLETED = 19984
EXPECTED_NULL = 16
EXPECTED_RESERVE = 52180
TOTALS = tuple(range(1, 7))
BOOT_REPS = 3000
BOOT_SEED = 73101
BASE = list(c071.BASE)


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


c071.utc = utc_ns
c073a.utc_ns = utc_ns


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


def read_same_selected_labels(path: Path, selected_ids: list[int]) -> tuple[pd.DataFrame, int]:
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=["id", "goals_home", "goals_away"],
        filter=ds.field("id").isin([int(x) for x in selected_ids]),
    )
    out = table.to_pandas()
    out["id"] = out["id"].astype("int64")
    if out["id"].duplicated().any():
        raise RuntimeError("duplicate selected label id")
    if len(out) != SELECT_N or set(out.id.astype(int)) != set(int(x) for x in selected_ids):
        raise RuntimeError(f"same-20k label projection mismatch n={len(out)}")
    null_mask = out[["goals_home", "goals_away"]].isna().any(axis=1)
    null_n = int(null_mask.sum())
    completed = out.loc[~null_mask].copy().reset_index(drop=True)
    if len(completed) != EXPECTED_COMPLETED or null_n != EXPECTED_NULL:
        raise RuntimeError(f"C073-A missingness drift completed={len(completed)} null={null_n}")
    return completed, null_n


def score_support() -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for total in range(7):
        for d in range(-total, total + 1, 2):
            h = (total + d) // 2
            a = (total - d) // 2
            out.append((h, a))
    if len(out) != 28:
        raise RuntimeError("score support drift")
    return out


def multiclass_rows(y_idx: np.ndarray, p: np.ndarray) -> pd.DataFrame:
    n = len(y_idx)
    safe = np.clip(p, 1e-15, 1.0)
    true_p = safe[np.arange(n), y_idx]
    ll = -np.log(true_p)
    one = np.zeros_like(p)
    one[np.arange(n), y_idx] = 1.0
    brier = np.sum((p - one) ** 2, axis=1)
    order = np.argsort(-p, axis=1, kind="stable")
    top1 = (order[:, 0] == y_idx).astype(float)
    top3 = np.asarray([y_idx[i] in order[i, :3] for i in range(n)], dtype=float)
    rank = 1 + np.sum(p > true_p[:, None], axis=1)
    return pd.DataFrame({"logloss": ll, "brier": brier, "top1": top1, "top3": top3, "true_rank": rank.astype(float)})


def summarize_rows(frame: pd.DataFrame) -> dict[str, float]:
    return {c: float(frame[c].mean()) for c in frame.columns}


def bootstrap_delta(deltas: np.ndarray) -> dict[str, float | int]:
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
        "n": int(len(d)),
        "reps": BOOT_REPS,
        "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "probability_delta_lt_zero": float(np.mean(sims < 0)),
    }


def hda_probabilities(score_probs: np.ndarray, scores: list[tuple[int, int]]) -> np.ndarray:
    out = np.zeros((len(score_probs), 3), dtype=float)  # H,D,A
    for j, (h, a) in enumerate(scores):
        k = 0 if h > a else 1 if h == a else 2
        out[:, k] += score_probs[:, j]
    return out


def hda_rows(home: np.ndarray, away: np.ndarray, p: np.ndarray) -> pd.DataFrame:
    y = np.where(home > away, 0, np.where(home == away, 1, 2)).astype(int)
    return multiclass_rows(y, p)[["logloss", "brier", "top1"]]


def named_score_diag(
    name: str,
    score_idx: int,
    actual_idx: np.ndarray,
    baseline_p: np.ndarray,
    candidate_p: np.ndarray,
    baseline_rows: pd.DataFrame,
    candidate_rows: pd.DataFrame,
) -> dict:
    actual = actual_idx == score_idx
    b_top = np.argmax(baseline_p, axis=1) == score_idx
    c_top = np.argmax(candidate_p, axis=1) == score_idx
    b_order = np.argsort(-baseline_p, axis=1, kind="stable")[:, :3]
    c_order = np.argsort(-candidate_p, axis=1, kind="stable")[:, :3]
    b_top3 = np.asarray([score_idx in row for row in b_order])
    c_top3 = np.asarray([score_idx in row for row in c_order])
    n_actual = int(actual.sum())
    return {
        "score": name,
        "actual_rows": n_actual,
        "baseline": {
            "top1_calls": int(b_top.sum()),
            "top1_hits": int((actual & b_top).sum()),
            "actual_top1_recall": float((actual & b_top).sum() / n_actual) if n_actual else None,
            "actual_top3_recall": float((actual & b_top3).sum() / n_actual) if n_actual else None,
            "actual_mean_rank": float(baseline_rows.loc[actual, "true_rank"].mean()) if n_actual else None,
        },
        "candidate": {
            "top1_calls": int(c_top.sum()),
            "top1_hits": int((actual & c_top).sum()),
            "actual_top1_recall": float((actual & c_top).sum() / n_actual) if n_actual else None,
            "actual_top3_recall": float((actual & c_top3).sum() / n_actual) if n_actual else None,
            "actual_mean_rank": float(candidate_rows.loc[actual, "true_rank"].mean()) if n_actual else None,
        },
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

    # Reconstruct exactly the already-consumed C073-A identity universe without target labels.
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
        raise RuntimeError(f"pool drift {len(pool)}")
    selected = pool.iloc[:SELECT_N].copy().reset_index(drop=True)
    reserve = pool.iloc[SELECT_N:].copy().reset_index(drop=True)
    if ids_sha(selected) != EXPECTED_SELECTED_SHA or len(reserve) != EXPECTED_RESERVE:
        raise RuntimeError("selected identity drift")
    selected[audit.FIXTURE_COLS].to_csv(out_dir / "same_selected_20000_identity.csv", index=False)

    # All model fitting uses only pre-2024 development labels.
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

    # Frozen C071-B Direct-T baseline.
    direct_y = np.minimum(dev_feat.total.to_numpy(int), 7)
    direct_model = c071.model()
    direct_model.fit(dev_feat[BASE], direct_y)

    # Frozen C073-A conditional-D baseline and candidate.
    d_models = {}
    training_rows = {}
    for total in TOTALS:
        tr = dev_feat[dev_feat.total == total].copy()
        classes = c073a.support(total)
        observed = sorted(int(x) for x in tr.D.unique())
        if observed != classes:
            raise RuntimeError(f"D support incomplete T={total}: {observed}")
        fitted = c073a.model()
        fitted.fit(tr[BASE], tr.D.to_numpy(int))
        d_models[total] = fitted
        training_rows[str(total)] = int(len(tr))
    pooled_counts, league_counts = c073a.empirical_tables(dev)

    # Same already-consumed target columns only. Null-result identities are not replaced.
    completed_labels, null_n = read_same_selected_labels(fp, [int(x) for x in selected.id.tolist()])
    test = selected.merge(completed_labels, on="id", how="inner", validate="one_to_one").sort_values(["date_utc", "id"]).reset_index(drop=True)
    test["goals_home"] = test.goals_home.astype(int)
    test["goals_away"] = test.goals_away.astype(int)
    test["total"] = test.goals_home + test.goals_away
    test["D"] = test.goals_home - test.goals_away

    # Strict causal feature state: pre-2024 plus earlier outcomes from the same selected 20k only.
    sel_rteam, sel_rleague, _ = c071.result_events(selected, completed_labels)
    all_rteam = pd.concat([dev_rteam, sel_rteam], ignore_index=True)
    all_rleague = pd.concat([dev_rleague, sel_rleague], ignore_index=True)
    test_feat = c071.build_features(test, all_rteam, all_rleague, empty_opportunity_events())
    test_feat["goals_home"] = test.goals_home.to_numpy(int)
    test_feat["goals_away"] = test.goals_away.to_numpy(int)
    test_feat["total"] = test.total.to_numpy(int)
    test_feat["D"] = test.D.to_numpy(int)

    # Tail is intentionally unresolved. Bridge estimand is conditional on realized/evaluated T<=6.
    keep = test_feat.total.to_numpy(int) <= 6
    score_test = test_feat.loc[keep].reset_index(drop=True)
    if len(score_test) != EXPECTED_COMPLETED - int((test.total >= 7).sum()):
        raise RuntimeError("T<=6 bridge row mismatch")

    direct_full = c071.predict(direct_model, score_test[BASE])
    pt = direct_full[:, :7].copy()
    pt_sum = pt.sum(axis=1, keepdims=True)
    if np.any(pt_sum <= 0):
        raise RuntimeError("Direct-T conditional renormalization failed")
    pt /= pt_sum

    scores = score_support()
    score_pos = {score: i for i, score in enumerate(scores)}
    pb = np.zeros((len(score_test), len(scores)), dtype=float)
    pc = np.zeros_like(pb)

    # T=0 deterministic 0-0.
    z = score_pos[(0, 0)]
    pb[:, z] = pt[:, 0]
    pc[:, z] = pt[:, 0]

    leagues = score_test.league_id.to_numpy()
    for total in TOTALS:
        classes = c073a.support(total)
        b_d = c073a.baseline_probability(leagues, total, pooled_counts, league_counts)
        c_d = c073a.aligned_predict(d_models[total], score_test[BASE], classes)
        for j, d in enumerate(classes):
            h = (total + d) // 2
            a = (total - d) // 2
            col = score_pos[(h, a)]
            pb[:, col] = pt[:, total] * b_d[:, j]
            pc[:, col] = pt[:, total] * c_d[:, j]

    b_resid = np.abs(pb.sum(axis=1) - 1.0)
    c_resid = np.abs(pc.sum(axis=1) - 1.0)
    if float(b_resid.max()) > 1e-10 or float(c_resid.max()) > 1e-10:
        raise RuntimeError("bridge probability conservation failed")

    actual_scores = list(zip(score_test.goals_home.astype(int), score_test.goals_away.astype(int)))
    actual_idx = np.asarray([score_pos[(int(h), int(a))] for h, a in actual_scores], dtype=int)
    br = multiclass_rows(actual_idx, pb)
    cr = multiclass_rows(actual_idx, pc)
    delta = cr - br

    baseline_summary = summarize_rows(br)
    candidate_summary = summarize_rows(cr)
    delta_summary = {c: float(delta[c].mean()) for c in delta.columns}
    boot = bootstrap_delta(delta.logloss.to_numpy(float))

    bhda = hda_probabilities(pb, scores)
    chda = hda_probabilities(pc, scores)
    bhr = hda_rows(score_test.goals_home.to_numpy(int), score_test.goals_away.to_numpy(int), bhda)
    chr_ = hda_rows(score_test.goals_home.to_numpy(int), score_test.goals_away.to_numpy(int), chda)

    b_top_idx = np.argmax(pb, axis=1)
    c_top_idx = np.argmax(pc, axis=1)
    score_is_draw = np.asarray([h == a for h, a in scores], dtype=bool)
    actual_draw = score_test.goals_home.to_numpy(int) == score_test.goals_away.to_numpy(int)
    b_draw_top = score_is_draw[b_top_idx]
    c_draw_top = score_is_draw[c_top_idx]
    actual_draws = int(actual_draw.sum())

    named = {}
    for score in [(0,0), (1,1), (2,2), (3,3)]:
        name = f"{score[0]}-{score[1]}"
        named[name] = named_score_diag(name, score_pos[score], actual_idx, pb, pc, br, cr)

    # Chronological quartiles are diagnostic only, because these labels were already consumed by C073-A.
    chrono = {}
    quartile_wins = 0
    for q, idx in enumerate(np.array_split(np.arange(len(score_test)), 4), start=1):
        d_ll = float(delta.iloc[idx].logloss.mean())
        d_top1 = float(delta.iloc[idx].top1.mean())
        d_top3 = float(delta.iloc[idx].top3.mean())
        quartile_wins += int(d_ll < 0)
        chrono[f"q{q}"] = {
            "n": int(len(idx)),
            "start": str(score_test.iloc[idx[0]].date_utc),
            "end": str(score_test.iloc[idx[-1]].date_utc),
            "delta_logloss": d_ll,
            "delta_top1": d_top1,
            "delta_top3": d_top3,
        }

    supportive = bool(
        delta_summary["logloss"] < 0
        and delta_summary["top1"] >= 0
        and delta_summary["top3"] >= 0
        and quartile_wins >= 3
    )

    audit_rows = score_test[["id", "date_utc", "league_id", "goals_home", "goals_away", "total", "D"]].copy()
    for c in br.columns:
        audit_rows[f"baseline_{c}"] = br[c].to_numpy()
        audit_rows[f"candidate_{c}"] = cr[c].to_numpy()
        audit_rows[f"delta_{c}"] = delta[c].to_numpy()
    audit_rows["baseline_top1_score"] = [f"{scores[i][0]}-{scores[i][1]}" for i in b_top_idx]
    audit_rows["candidate_top1_score"] = [f"{scores[i][0]}-{scores[i][1]}" for i in c_top_idx]
    audit_rows.to_csv(out_dir / "bridge_match_metrics.csv", index=False)

    summary = {
        "schema_version": "C073B_SCORE_TOP1_BRIDGE_V1",
        "status": "POST_VIEW_BRIDGE_SUPPORTIVE" if supportive else "POST_VIEW_BRIDGE_NOT_SUPPORTIVE",
        "authority": "diagnostic_only_no_promotion",
        "identity": {
            "parent_pool": EXPECTED_POOL,
            "same_selected_20000": SELECT_N,
            "selected_ids_sha256": ids_sha(selected),
            "completed_nonnull": int(len(test)),
            "null_results": null_n,
            "reserve_52180_target_labels_opened": False,
            "new_target_label_ids_opened": 0,
        },
        "bridge_estimand": {
            "evaluated_rows_T0_to_T6": int(len(score_test)),
            "excluded_completed_T7plus": int((test.total >= 7).sum()),
            "score_support_count": len(scores),
            "tail_disaggregated": False,
            "direct_t_family": "C071-B frozen BASE logistic C=0.1",
            "direct_t_use": "P(T=0..6 | T<=6) diagnostic renormalization",
            "conditional_d_baseline": "C073-A league alpha1 empirical",
            "conditional_d_candidate": "C073-A fixed per-T logistic C=0.1",
        },
        "probability_audit": {
            "baseline_max_abs_sum_residual": float(b_resid.max()),
            "candidate_max_abs_sum_residual": float(c_resid.max()),
        },
        "exact_score": {
            "baseline": baseline_summary,
            "candidate": candidate_summary,
            "delta_candidate_minus_baseline": delta_summary,
            "paired_bootstrap_delta_logloss": boot,
            "chronological_quartiles": chrono,
            "quartile_logloss_wins": quartile_wins,
        },
        "draw_score_top1": {
            "actual_draws_T0_to_T6": actual_draws,
            "baseline_top1_draw_calls": int(b_draw_top.sum()),
            "candidate_top1_draw_calls": int(c_draw_top.sum()),
            "baseline_top1_draw_call_rate": float(b_draw_top.mean()),
            "candidate_top1_draw_call_rate": float(c_draw_top.mean()),
            "baseline_actual_draw_top1_hits": int((actual_draw & b_draw_top).sum()),
            "candidate_actual_draw_top1_hits": int((actual_draw & c_draw_top).sum()),
            "baseline_actual_draw_top1_recall": float((actual_draw & b_draw_top).sum() / actual_draws) if actual_draws else None,
            "candidate_actual_draw_top1_recall": float((actual_draw & c_draw_top).sum() / actual_draws) if actual_draws else None,
        },
        "named_draw_scores": named,
        "conditional_HDA_T0_to_T6": {
            "baseline": {c: float(bhr[c].mean()) for c in bhr.columns},
            "candidate": {c: float(chr_[c].mean()) for c in chr_.columns},
            "delta_candidate_minus_baseline": {c: float((chr_[c] - bhr[c]).mean()) for c in bhr.columns},
        },
        "diagnostic_rule": {
            "supportive": supportive,
            "requirements": {
                "exact_score_delta_logloss_lt_0": bool(delta_summary["logloss"] < 0),
                "exact_score_delta_top1_ge_0": bool(delta_summary["top1"] >= 0),
                "exact_score_delta_top3_ge_0": bool(delta_summary["top3"] >= 0),
                "chronological_quartile_LL_wins_ge_3of4": bool(quartile_wins >= 3),
            },
            "promotion_allowed": False,
            "reason": "bridge was defined after C073-A confirmation labels had already been opened",
        },
        "boundaries": {
            "unified_matrix_generated": False,
            "formal_exact_score_top1_claim_allowed": False,
            "T7plus_exact_tail_available": False,
            "feature_search": False,
            "hyperparameter_search": False,
            "manual_draw_or_1_1_boost": False,
            "sample_reselection": False,
            "formal_weight": 0,
            "CURRENT_changed": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
