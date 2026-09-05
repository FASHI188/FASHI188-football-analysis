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
import run_event_temporal_process_residual as evt

EPS = 1e-12
FEATURE = "concentration_fit_abs"


def fit_scaler(rows: list[dict]) -> tuple[float, float]:
    vals = [float(r[FEATURE]) for r in rows if r.get(FEATURE) is not None and math.isfinite(float(r[FEATURE]))]
    if not vals:
        raise RuntimeError("empty feature training rows")
    mean = sum(vals) / len(vals)
    sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / len(vals)) or 1.0
    return mean, sd


def fit_draw_offset(rows: list[dict], ridge: float) -> dict:
    active = [r for r in rows if r.get(FEATURE) is not None and math.isfinite(float(r[FEATURE]))]
    mean, sd = fit_scaler(active)
    X = [[1.0, (float(r[FEATURE]) - mean) / sd] for r in active]
    y = [int(r["y"] == 1) for r in active]
    offsets = [evt.logit(float(r["b_p"][1])) for r in active]
    beta = evt.fit_offset(X, y, offsets, ridge)
    return {"fit_n": len(active), "mean": mean, "sd": sd, "beta0": float(beta[0]), "beta1": float(beta[1])}


def apply_candidate(row: dict, params: dict) -> tuple[list[float], bool]:
    base = list(map(float, row["b_p"]))
    value = row.get(FEATURE)
    if value is None or not math.isfinite(float(value)):
        return base, False
    z = (float(value) - float(params["mean"])) / float(params["sd"])
    draw = evt.ds.sigmoid(evt.logit(base[1]) + float(params["beta0"]) + float(params["beta1"]) * z)
    side_mass = max(EPS, base[0] + base[2])
    cond_home = base[0] / side_mass
    cand = [(1.0 - draw) * cond_home, draw, (1.0 - draw) * (1.0 - cond_home)]
    if abs(sum(cand) - 1.0) > 1e-10 or min(cand) < 0.0:
        raise RuntimeError("invalid candidate probability")
    return cand, True


def exact_score_logloss(rows: list[dict], matrices: dict[str, list[list[float]]]) -> float:
    total = 0.0
    for r in rows:
        fid = r["fixture_id"]
        hg = int(r["home_goals"])
        ag = int(r["away_goals"])
        matrix = matrices[fid]
        if hg >= len(matrix) or ag >= len(matrix[hg]):
            raise RuntimeError(f"observed score outside matrix support {fid} {hg}-{ag}")
        total -= math.log(max(common.EPS, float(matrix[hg][ag])))
    return total / len(rows)


def metric_delta(base: dict, cand: dict) -> dict:
    return {
        "logloss": float(cand["logloss"] - base["logloss"]),
        "brier": float(cand["brier"] - base["brier"]),
        "rps": float(cand["rps"] - base["rps"]),
        "top1_pp": float((cand["top1"] - base["top1"]) * 100.0),
        "hits": int(cand["hits"] - base["hits"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    for arg in ("contract", "parent_residual", "v311", "v31", "usr1", "v2", "xg", "v1", "v1_result", "db", "xg_identity", "out"):
        ap.add_argument("--" + arg.replace("_", "-"), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    contract = json.loads(a.contract.read_text())
    parent = json.loads(a.parent_residual.read_text())
    assert contract["status"] == "FROZEN_BEFORE_CANDIDATE_SCORING"
    assert parent["classification"]["chance_burstiness"] == "ROBUST_DRAW_POST_B_RESIDUAL"
    assert parent["candidate_eligible_families"] == ["chance_burstiness"]
    assert parent["historical_confirmation_2023_labels_opened"] is False
    assert parent["prospective_1335_data_touched"] is False

    frozen = common.build_frozen_baseline(a, "event_burstiness_candidate")
    dev = [r for r in frozen["rows"] if r["season"] in (2020, 2021, 2022) and r["fixture_id"] in frozen["bmap"]]
    assert len(dev) == 5478
    fmap, event_audit = evt.temporal_feature_map(a.db)
    assert all(r["fixture_id"] in fmap for r in dev)

    wanted = {r["fixture_id"] for r in dev}
    snapshots, snapshot_receipt = bmod.make_snapshots(
        a.db, wanted, float(contract["frozen_bases"]["stage6_b_half_life"])
    )
    bprob: dict[str, list[float]] = {}
    bmat: dict[str, list[list[float]]] = {}
    rows: list[dict] = []
    for r in dev:
        fid = r["fixture_id"]
        p, _ = bmod.predict(
            frozen["bmap"][fid], snapshots.get(fid), float(contract["frozen_bases"]["stage6_b_coefficient"])
        )
        bp = list(map(float, p))
        bprob[fid] = bp
        bmat[fid] = common.region_rescale(frozen["bmats"][fid], frozen["bmap"][fid], bp)
        feat = dict(fmap[fid])
        hg = int(r["home_goals"])
        ag = int(r["away_goals"])
        feat.update(
            {
                "fixture_id": fid,
                "season": int(r["season"]),
                "home_goals": hg,
                "away_goals": ag,
                "y": 0 if hg > ag else 1 if hg == ag else 2,
                "b_p": bp,
            }
        )
        rows.append(feat)
    assert snapshot_receipt["active"] == 5463

    ridge = float(contract["candidate_transform"]["ridge"])
    candidate: dict[str, list[float]] = {}
    active_mask: dict[str, bool] = {}
    fit_receipts = []
    season_reports = []
    score_rows = [r for r in rows if r["season"] in (2021, 2022)]

    for season in (2021, 2022):
        train = [r for r in rows if 2020 <= r["season"] < season]
        test = [r for r in rows if r["season"] == season]
        params = fit_draw_offset(train, ridge)
        fit_receipts.append({"score_season": season, **params})
        for r in test:
            p, active = apply_candidate(r, params)
            candidate[r["fixture_id"]] = p
            active_mask[r["fixture_id"]] = active

        base_map = {r["fixture_id"]: bprob[r["fixture_id"]] for r in test}
        cand_map = {r["fixture_id"]: candidate[r["fixture_id"]] for r in test}
        bm = common.metrics(test, base_map)
        cm = common.metrics(test, cand_map)
        season_reports.append(
            {
                "season": season,
                "train_n": len(train),
                "test_n": len(test),
                "feature_active_n": sum(active_mask[r["fixture_id"]] for r in test),
                "baseline": bm,
                "candidate": cm,
                "deltas": metric_delta(bm, cm),
            }
        )

    base_score_map = {r["fixture_id"]: bprob[r["fixture_id"]] for r in score_rows}
    cand_score_map = {r["fixture_id"]: candidate[r["fixture_id"]] for r in score_rows}
    pooled_base = common.metrics(score_rows, base_score_map)
    pooled_cand = common.metrics(score_rows, cand_score_map)
    pooled_delta = metric_delta(pooled_base, pooled_cand)
    active_n = sum(active_mask[r["fixture_id"]] for r in score_rows)
    active_share = active_n / len(score_rows)

    cand_mats = {}
    matrix_error = 0.0
    for r in score_rows:
        fid = r["fixture_id"]
        cand_mats[fid] = common.region_rescale(bmat[fid], bprob[fid], candidate[fid])
        integrated = common.integrate_matrix(cand_mats[fid])
        matrix_error = max(matrix_error, max(abs(integrated[i] - candidate[fid][i]) for i in range(3)))
    exact_base = exact_score_logloss(score_rows, bmat)
    exact_cand = exact_score_logloss(score_rows, cand_mats)
    exact_delta = exact_cand - exact_base

    final_params = fit_draw_offset([r for r in rows if 2020 <= r["season"] <= 2022], ridge)
    final_param_receipt = {
        "fit_seasons": [2020, 2021, 2022],
        "feature": FEATURE,
        "feature_missing_policy": "EXACT_FALLBACK_FROZEN_STAGE6_B",
        "home_away_conditional_ratio_preserved": True,
        **final_params,
    }

    gates_cfg = contract["development_gates"]
    by_season = {r["season"]: r for r in season_reports}
    gates = {
        "feature_active_share": active_share >= float(gates_cfg["feature_active_share_min"]),
        "season_2021_1x2_logloss": by_season[2021]["deltas"]["logloss"] < float(gates_cfg["season_2021_1x2_logloss_delta_lt"]),
        "season_2022_1x2_logloss": by_season[2022]["deltas"]["logloss"] < float(gates_cfg["season_2022_1x2_logloss_delta_lt"]),
        "pooled_1x2_logloss": pooled_delta["logloss"] < float(gates_cfg["pooled_1x2_logloss_delta_lt"]),
        "pooled_brier": pooled_delta["brier"] <= float(gates_cfg["pooled_brier_delta_lte"]),
        "pooled_rps": pooled_delta["rps"] <= float(gates_cfg["pooled_rps_delta_lte"]),
        "pooled_top1": pooled_delta["hits"] >= int(gates_cfg["pooled_top1_net_correct_gte"]),
        "season_top1_nondegrade": sum(1 for s in (2021, 2022) if by_season[s]["deltas"]["hits"] >= 0) >= int(gates_cfg["season_top1_nondegrade_required"]),
        "pooled_exact_score_logloss": exact_delta < float(gates_cfg["pooled_exact_score_logloss_delta_lt"]),
        "matrix_1x2": matrix_error <= float(gates_cfg["matrix_1x2_max_abs_error_lte"]),
    }
    status = contract["terminal"]["pass"] if all(gates.values()) else contract["terminal"]["fail"]

    result = {
        "schema_version": "football3-event-burstiness-draw-candidate-result-v1",
        "status": status,
        "research_only": True,
        "development_scoring_seasons": [2021, 2022],
        "development_scoring_n": len(score_rows),
        "event_audit": event_audit,
        "feature_active_n": active_n,
        "feature_active_share": active_share,
        "stage6_b_active_n_2020_2022": snapshot_receipt["active"],
        "fit_receipts": fit_receipts,
        "final_frozen_parameter_receipt": final_param_receipt,
        "season_reports": season_reports,
        "pooled": {"baseline": pooled_base, "candidate": pooled_cand, "deltas": pooled_delta},
        "exact_score": {"baseline_logloss": exact_base, "candidate_logloss": exact_cand, "delta": exact_delta},
        "matrix_1x2_max_abs_error": matrix_error,
        "gates": gates,
        "failed_gates": [k for k, v in gates.items() if not v],
        "historical_confirmation_2023_labels_opened": False,
        "prospective_1335_data_touched": False,
        "formal_v2_changed": False,
        "frozen_v3_1_1_changed": False,
        "stage6_b_changed": False,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_enablement_changed": False,
        "promotion_allowed": False,
        "next_step": "IF_PASS_FREEZE_THIS_EXACT_PARAMETER_RECEIPT_FOR_POSSIBLE_ONE_SHOT_2023_CONFIRMATION_ONLY_AFTER_SEPARATE_USER_AUTHORIZATION;IF_FAIL_CLOSE_ROUTE_WITHOUT_RESCUE",
    }
    common.write_json(a.out / "event_burstiness_draw_candidate_result.json", result)
    common.write_json(a.out / "event_burstiness_draw_final_params.json", final_param_receipt)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
