from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "stage6_pre_b_deep_ppda"))
sys.path.insert(0, str(ROOT / "research" / "historical_event_temporal_process_residual_v1"))
import common
import run_stage6_pre_b as bmod
import run_event_temporal_process_residual as et

EPS = 1e-15
FEATURE = "shot_bodypart_hhi_fit_abs"


def strict_logit(p: float) -> float:
    p = float(p)
    if not (0.0 < p < 1.0):
        raise RuntimeError(f"offset probability outside (0,1): {p}")
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def binary_ll(y: list[int], p: list[float]) -> float:
    if not y:
        raise RuntimeError("empty binary score")
    total = 0.0
    for yy, pp in zip(y, p):
        if not (0.0 < pp < 1.0):
            raise RuntimeError("candidate binary probability outside (0,1)")
        total -= math.log(pp if yy else (1.0 - pp))
    return total / len(y)


def load_feature_receipts(path: pathlib.Path) -> tuple[dict[str, dict], dict]:
    out: dict[str, dict] = {}
    n = 0
    covered = 0
    forbidden_true = []
    max_season = -1
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            fid = str(r["fixture_id"])
            if fid in out:
                raise RuntimeError(f"duplicate source receipt fixture_id: {fid}")
            for key in ("target_current_event_access", "target_result_access", "target_score_access", "target_match_xg_access"):
                if r.get(key) is not False:
                    forbidden_true.append((fid, key, r.get(key)))
            value = r.get(FEATURE)
            if value is not None:
                value = float(value)
                if not math.isfinite(value) or value < 0.0:
                    raise RuntimeError(f"invalid source feature {fid}: {value}")
                covered += 1
            out[fid] = r
            n += 1
            max_season = max(max_season, int(r["season"]))
    if forbidden_true:
        raise RuntimeError(f"source receipt forbidden-access drift: {forbidden_true[:3]}")
    return out, {"receipt_n": n, "covered_n": covered, "coverage": covered / n if n else 0.0, "max_season": max_season}


def standardization(rows: list[dict]) -> tuple[float, float]:
    vals = [float(r[FEATURE]) for r in rows if r.get(FEATURE) is not None and math.isfinite(float(r[FEATURE]))]
    if not vals:
        raise RuntimeError("no training feature values")
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
    return mu, sd


def design(rows: list[dict], mu: float, sd: float) -> list[list[float]]:
    X = []
    for r in rows:
        value = r.get(FEATURE)
        z = 0.0 if value is None else (float(value) - mu) / sd
        X.append([1.0, z])
    return X


def fit_fold(train: list[dict], test: list[dict], ridge: float):
    mu, sd = standardization(train)
    Xtr = design(train, mu, sd)
    Xte = design(test, mu, sd)
    ytr = [int(r["y"] == 1) for r in train]
    offsets = [strict_logit(float(r["b_prob"][1])) for r in train]
    beta = et.fit_offset(Xtr, ytr, offsets, ridge)
    pred_draw = [
        sigmoid(strict_logit(float(r["b_prob"][1])) + beta[0] + beta[1] * x[1])
        for r, x in zip(test, Xte)
    ]
    return {"mean": mu, "sd": sd, "beta": list(map(float, beta))}, pred_draw


def candidate_1x2(base: list[float], draw: float) -> list[float]:
    ph, pd, pa = map(float, base)
    denom = ph + pa
    if denom <= 0.0:
        raise RuntimeError("Frozen B non-draw mass invalid")
    qh = ph / denom
    out = [(1.0 - draw) * qh, draw, (1.0 - draw) * (1.0 - qh)]
    if min(out) < 0.0 or abs(sum(out) - 1.0) > 1e-12:
        raise RuntimeError("candidate 1X2 invalid")
    return out


def exact_score_ll(rows: list[dict], mats: dict[str, list[list[float]]]) -> float:
    total = 0.0
    for r in rows:
        m = mats[r["fixture_id"]]
        h, a = int(r["home_goals"]), int(r["away_goals"])
        p = float(m[h][a]) if h < len(m) and a < len(m[h]) else EPS
        total -= math.log(max(EPS, p))
    return total / len(rows)


def total_goals_ll(rows: list[dict], mats: dict[str, list[list[float]]]) -> float:
    total = 0.0
    for r in rows:
        t = int(r["home_goals"]) + int(r["away_goals"])
        m = mats[r["fixture_id"]]
        p = sum(float(v) for h, row in enumerate(m) for a, v in enumerate(row) if h + a == t)
        total -= math.log(max(EPS, p))
    return total / len(rows)


def ratio_error(base: list[float], cand: list[float]) -> float:
    qb = float(base[0]) / max(EPS, float(base[0]) + float(base[2]))
    qc = float(cand[0]) / max(EPS, float(cand[0]) + float(cand[2]))
    return abs(qb - qc)


def main() -> int:
    ap = argparse.ArgumentParser()
    for arg in ("contract", "source_receipts", "v311", "v31", "usr1", "v2", "xg", "v1", "v1_result", "db", "xg_identity", "out"):
        ap.add_argument("--" + arg.replace("_", "-"), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text())
    assert c["status"] == "FROZEN_BEFORE_CANDIDATE_EVALUATION"
    assert c["feature"]["family"] == "shot_bodypart_concentration"
    assert c["feature"]["key"] == FEATURE
    assert c["feature"]["source_receipt_is_authoritative"] is True
    assert c["method"]["fixed_ridge"] == 0.01
    assert c["method"]["no_candidate_family_search"] is True
    assert c["method"]["no_hyperparameter_grid"] is True
    assert c["method"]["no_threshold_search"] is True
    assert c["method"]["no_posthoc_calibration"] is True
    assert c["method"]["no_probability_clipping"] is True
    assert c["method"]["no_retune_after_result"] is True

    fmap, source_audit = load_feature_receipts(a.source_receipts)
    if source_audit["receipt_n"] != 5478 or source_audit["max_season"] != 2022:
        raise RuntimeError(f"source receipt cohort drift: {source_audit}")
    if source_audit["coverage"] < float(c["gates"]["minimum_feature_coverage"]):
        raise RuntimeError(f"source feature coverage unexpectedly low: {source_audit['coverage']}")

    frozen = common.build_frozen_baseline(a, "shot_bodypart_candidate")
    dev = [r for r in frozen["rows"] if r["season"] in (2020, 2021, 2022) and r["fixture_id"] in frozen["bmap"]]
    if len(dev) != 5478:
        raise RuntimeError(f"development_n drift: {len(dev)}")
    if set(r["fixture_id"] for r in dev) != set(fmap):
        missing = sorted(set(r["fixture_id"] for r in dev) - set(fmap))[:5]
        extra = sorted(set(fmap) - set(r["fixture_id"] for r in dev))[:5]
        raise RuntimeError(f"source/baseline identity mismatch missing={missing} extra={extra}")

    wanted = {r["fixture_id"] for r in dev}
    snaps, snaprec = bmod.make_snapshots(a.db, wanted, float(c["frozen_bases"]["stage6_b_half_life"]))
    bprob: dict[str, list[float]] = {}
    bmats: dict[str, list[list[float]]] = {}
    for r in dev:
        fid = r["fixture_id"]
        p, _ = bmod.predict(
            frozen["bmap"][fid],
            snaps.get(fid),
            float(c["frozen_bases"]["stage6_b_coefficient"]),
        )
        bprob[fid] = list(map(float, p))
        bmats[fid] = common.region_rescale(frozen["bmats"][fid], frozen["bmap"][fid], bprob[fid])

    rows = []
    for r in dev:
        fid = r["fixture_id"]
        src = fmap[fid]
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        y = 0 if hg > ag else 1 if hg == ag else 2
        rec = dict(r)
        rec[FEATURE] = src.get(FEATURE)
        rec["y"] = y
        rec["b_prob"] = bprob[fid]
        rows.append(rec)

    coverage = sum(r.get(FEATURE) is not None for r in rows) / len(rows)
    if abs(coverage - float(source_audit["coverage"])) > 1e-15:
        raise RuntimeError(f"feature coverage identity drift source={source_audit['coverage']} merged={coverage}")
    if coverage < float(c["gates"]["minimum_feature_coverage"]):
        raise RuntimeError(f"feature coverage unexpectedly low: {coverage}")

    ridge = float(c["method"]["fixed_ridge"])
    cand: dict[str, list[float]] = {}
    fold_results = []
    max_ratio_err = 0.0
    max_prob_delta = 0.0

    for season in (2021, 2022):
        train = [r for r in rows if r["season"] < season]
        test = [r for r in rows if r["season"] == season]
        params, draw_pred = fit_fold(train, test, ridge)
        for r, pd in zip(test, draw_pred):
            fid = r["fixture_id"]
            cp = candidate_1x2(r["b_prob"], pd)
            cand[fid] = cp
            max_ratio_err = max(max_ratio_err, ratio_error(r["b_prob"], cp))
            max_prob_delta = max(max_prob_delta, max(abs(cp[i] - float(r["b_prob"][i])) for i in range(3)))

        base_fold = {r["fixture_id"]: r["b_prob"] for r in test}
        cand_fold = {r["fixture_id"]: cand[r["fixture_id"]] for r in test}
        bm = common.metrics(test, base_fold)
        cm = common.metrics(test, cand_fold)
        draw_y = [int(r["y"] == 1) for r in test]
        base_draw_ll = binary_ll(draw_y, [float(r["b_prob"][1]) for r in test])
        cand_draw_ll = binary_ll(draw_y, [float(cand[r["fixture_id"]][1]) for r in test])
        fold_results.append({
            "season": season,
            "train_n": len(train),
            "test_n": len(test),
            "fit": params,
            "baseline": bm,
            "candidate": cm,
            "deltas": {
                "hits": cm["hits"] - bm["hits"],
                "top1_pp": (cm["top1"] - bm["top1"]) * 100.0,
                "logloss": cm["logloss"] - bm["logloss"],
                "brier": cm["brier"] - bm["brier"],
                "rps": cm["rps"] - bm["rps"],
                "draw_logloss": cand_draw_ll - base_draw_ll,
            },
            "draw_logloss": {"baseline": base_draw_ll, "candidate": cand_draw_ll},
        })

    test_rows = [r for r in rows if r["season"] in (2021, 2022)]
    base_test = {r["fixture_id"]: r["b_prob"] for r in test_rows}
    if len(cand) != len(test_rows):
        raise RuntimeError("candidate map incomplete")
    base_metrics = common.metrics(test_rows, base_test)
    cand_metrics = common.metrics(test_rows, cand)

    cand_mats = {}
    matrix_err = 0.0
    for r in test_rows:
        fid = r["fixture_id"]
        m = common.region_rescale(bmats[fid], bprob[fid], cand[fid])
        cand_mats[fid] = m
        got = common.integrate_matrix(m)
        matrix_err = max(matrix_err, max(abs(got[i] - cand[fid][i]) for i in range(3)))

    base_exact = exact_score_ll(test_rows, bmats)
    cand_exact = exact_score_ll(test_rows, cand_mats)
    base_total = total_goals_ll(test_rows, bmats)
    cand_total = total_goals_ll(test_rows, cand_mats)

    pooled_delta = {
        "hits": cand_metrics["hits"] - base_metrics["hits"],
        "top1_pp": (cand_metrics["top1"] - base_metrics["top1"]) * 100.0,
        "logloss": cand_metrics["logloss"] - base_metrics["logloss"],
        "brier": cand_metrics["brier"] - base_metrics["brier"],
        "rps": cand_metrics["rps"] - base_metrics["rps"],
        "exact_score_logloss": cand_exact - base_exact,
        "total_goals_logloss": cand_total - base_total,
    }

    g = c["gates"]
    checks = {
        "draw_logloss_each_fold_strict_improvement": all(f["deltas"]["draw_logloss"] < 0.0 for f in fold_results),
        "pooled_1x2_logloss_nonworse": pooled_delta["logloss"] <= float(g["pooled_1x2_logloss_delta_max"]) + 1e-15,
        "pooled_1x2_brier_nonworse": pooled_delta["brier"] <= float(g["pooled_1x2_brier_delta_max"]) + 1e-15,
        "pooled_1x2_rps_nonworse": pooled_delta["rps"] <= float(g["pooled_1x2_rps_delta_max"]) + 1e-15,
        "pooled_top1_nondecrease": pooled_delta["hits"] >= int(g["pooled_top1_delta_hits_min"]),
        "each_fold_top1_nondecrease": all(f["deltas"]["hits"] >= int(g["each_fold_top1_delta_hits_min"]) for f in fold_results),
        "pooled_exact_score_logloss_nonworse": pooled_delta["exact_score_logloss"] <= float(g["pooled_exact_score_logloss_delta_max"]) + 1e-15,
        "pooled_total_goals_logloss_nonworse": pooled_delta["total_goals_logloss"] <= float(g["pooled_total_goals_logloss_delta_max"]) + 1e-15,
        "max_outcome_probability_delta": max_prob_delta <= float(g["max_outcome_probability_abs_delta"]) + 1e-15,
        "conditional_home_away_ratio_preserved": max_ratio_err <= float(g["conditional_home_away_ratio_max_abs_error"]) + 1e-15,
        "matrix_1x2_identity": matrix_err <= float(g["matrix_1x2_max_abs_error"]) + 1e-15,
    }
    scientific_pass = all(checks.values())

    final_params = None
    if scientific_pass:
        mu, sd = standardization(rows)
        X = design(rows, mu, sd)
        y = [int(r["y"] == 1) for r in rows]
        offsets = [strict_logit(float(r["b_prob"][1])) for r in rows]
        beta = et.fit_offset(X, y, offsets, ridge)
        final_params = {
            "schema_version": "football3-shot-bodypart-frozen-params-v1",
            "status": "FROZEN_AFTER_DEVELOPMENT_PASS_BEFORE_ANY_CONFIRMATION",
            "feature_family": "shot_bodypart_concentration",
            "feature_key": FEATURE,
            "standardization_mean": mu,
            "standardization_sd": sd,
            "beta_intercept": float(beta[0]),
            "beta_slope": float(beta[1]),
            "fixed_ridge": ridge,
            "training_seasons": [2020, 2021, 2022],
            "training_n": len(rows),
            "formal_weight": 0,
            "historical_confirmation_2023_labels_opened": False,
            "prospective_1335_data_touched": False,
        }
        common.write_json(a.out / "FROZEN_SHOT_BODYPART_PARAMS.json", final_params)

    out = {
        "schema_version": "football3-prior-shot-bodypart-candidate-result-v1",
        "status": c["terminal"]["pass"] if scientific_pass else c["terminal"]["fail"],
        "scientific_pass": scientific_pass,
        "research_only": True,
        "formal_weight": 0,
        "development_n": len(rows),
        "rolling_oos_n": len(test_rows),
        "source_max_season_loaded": source_audit["max_season"],
        "coverage": coverage,
        "source_receipt_audit": source_audit,
        "stage6_b_active_n": snaprec["active"],
        "historical_confirmation_2023_labels_opened": False,
        "prospective_1335_data_touched": False,
        "feature_family": "shot_bodypart_concentration",
        "feature_key": FEATURE,
        "fold_results": fold_results,
        "pooled": {
            "baseline": base_metrics,
            "candidate": cand_metrics,
            "deltas": pooled_delta,
            "exact_score_logloss": {"baseline": base_exact, "candidate": cand_exact},
            "total_goals_logloss": {"baseline": base_total, "candidate": cand_total},
        },
        "max_outcome_probability_abs_delta": max_prob_delta,
        "conditional_home_away_ratio_max_abs_error": max_ratio_err,
        "matrix_1x2_max_abs_error": matrix_err,
        "checks": checks,
        "frozen_params_written": final_params is not None,
        "next_step": (
            "FIND_AND_PREREGISTER_GENUINELY_UNCONSUMED_HISTORICAL_CONFIRMATION_COHORT"
            if scientific_pass
            else "CLOSE_SHOT_BODYPART_NO_RETUNE"
        ),
    }
    common.write_json(a.out / "shot_bodypart_candidate_result.json", out)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
