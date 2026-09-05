from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "stage6_pre_b_deep_ppda"))
sys.path.insert(0, str(ROOT / "research" / "historical_event_temporal_process_residual_v1"))
sys.path.insert(0, str(ROOT / "research" / "historical_prior_shooter_xg_responsibility_candidate_v1"))
import common
import run_stage6_pre_b as bmod
import run_event_temporal_process_residual as et
import run_shooter_xg_responsibility_candidate as sh

FEATURE = "pair_mean_xgbuildup_hhi"
sh.FEATURE = FEATURE
EPS = 1e-15


def lineup_side_profile(rows):
    d = collections.defaultdict(float)
    for r in rows:
        pid = int(r["player_id"])
        v = float(r["xGBuildup"])
        if not math.isfinite(v) or v < 0.0:
            raise RuntimeError(f"invalid xGBuildup={v}")
        d[pid] += v
    return dict(d)


def aggregate_hhi(q, minimum_prior):
    if len(q) < minimum_prior:
        return None
    d = collections.defaultdict(float)
    for p in q:
        for pid, v in p.items():
            d[int(pid)] += float(v)
    total = sum(d.values())
    if total <= 0.0:
        return None
    value = sum((v / total) ** 2 for v in d.values())
    if not math.isfinite(value) or value <= 0.0 or value > 1.0 + 1e-12:
        raise RuntimeError(f"invalid xGBuildup HHI={value}")
    return float(value)


def xgbuildup_feature_map(db: pathlib.Path, lookback: int, minimum_prior: int):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    qs = ",".join("?" for _ in common.LEAGUES)
    games = [dict(r) for r in con.execute(
        f"""select id,fid,date,league,season,h_id,a_id
            from general_game_stats
            where league in ({qs}) and season between 2014 and 2022
            order by date,id""", common.LEAGUES)]
    lineup = collections.defaultdict(lambda: {"h": [], "a": []})
    lineup_n = player_known = xgb_known = 0
    for r in con.execute(
        f"""select l.match_id,l.h_a,l.player_id,l.xGBuildup
            from lineup_stats l
            join general_game_stats g on g.id=l.match_id
            where g.league in ({qs}) and g.season between 2014 and 2022
            order by l.match_id,l.player_id""", common.LEAGUES):
        lineup_n += 1
        if r["player_id"] is not None:
            player_known += 1
        if r["xGBuildup"] is not None:
            xgb_known += 1
        if r["player_id"] is None or r["xGBuildup"] is None:
            continue
        side = str(r["h_a"])
        if side not in ("h", "a"):
            raise RuntimeError(f"invalid lineup side {side}")
        lineup[int(r["match_id"])][side].append(dict(r))
    con.close()
    if len(games) != 16332:
        raise RuntimeError(f"history n drift: {len(games)}")
    if lineup_n != 467531 or player_known != 467531 or xgb_known != 467531:
        raise RuntimeError(f"lineup source identity drift: n={lineup_n} pid={player_known} xgb={xgb_known}")

    profiles = {}
    for g in games:
        mid = int(g["id"])
        profiles[mid] = {
            "h": lineup_side_profile(lineup[mid]["h"]),
            "a": lineup_side_profile(lineup[mid]["a"]),
        }

    states = collections.defaultdict(lambda: collections.deque(maxlen=lookback))
    bydate = collections.OrderedDict()
    for g in games:
        bydate.setdefault(str(g["date"])[:10], []).append(g)

    out = {}
    target_n = covered_n = 0
    season_counts = collections.Counter()
    covered_by_season = collections.Counter()
    for _, batch in bydate.items():
        batch = sorted(batch, key=lambda z: int(z["id"]))
        for g in batch:
            season = int(g["season"])
            if season not in (2020, 2021, 2022):
                continue
            target_n += 1
            season_counts[season] += 1
            hk = (str(g["league"]), int(g["h_id"]))
            ak = (str(g["league"]), int(g["a_id"]))
            hv = aggregate_hhi(states[hk], minimum_prior)
            av = aggregate_hhi(states[ak], minimum_prior)
            rec = {}
            if hv is not None and av is not None:
                rec[FEATURE] = 0.5 * (hv + av)
                covered_n += 1
                covered_by_season[season] += 1
            out[f"understat:{int(g['fid'])}"] = rec
        for g in batch:
            mid = int(g["id"])
            states[(str(g["league"]), int(g["h_id"]))].append(profiles[mid]["h"])
            states[(str(g["league"]), int(g["a_id"]))].append(profiles[mid]["a"])

    if target_n != 5478 or dict(sorted(season_counts.items())) != {2020: 1826, 2021: 1826, 2022: 1826}:
        raise RuntimeError(f"target identity drift: n={target_n} seasons={dict(season_counts)}")
    if covered_n != 5433:
        raise RuntimeError(f"source feature coverage drift: {covered_n}/{target_n}")
    return out, {
        "history_n": len(games),
        "target_n": target_n,
        "target_by_season": dict(sorted(season_counts.items())),
        "lineup_row_n": lineup_n,
        "player_id_nonnull_n": player_known,
        "player_id_nonnull_rate": player_known / lineup_n,
        "xgbuildup_nonnull_n": xgb_known,
        "xgbuildup_nonnull_rate": xgb_known / lineup_n,
        "bilateral_feature_n": covered_n,
        "bilateral_feature_coverage": covered_n / target_n,
        "bilateral_feature_by_season": dict(sorted(covered_by_season.items())),
    }


def main():
    ap = argparse.ArgumentParser()
    for arg in ("contract","v311","v31","usr1","v2","xg","v1","v1_result","db","xg_identity","out"):
        ap.add_argument("--" + arg.replace("_", "-"), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text())
    assert c["status"] == "FROZEN_BEFORE_CANDIDATE_EVALUATION"
    assert c["feature"]["family"] == "prior_xgbuildup_responsibility_concentration"
    assert c["feature"]["key"] == FEATURE
    assert c["feature"]["lookback_completed_league_matches"] == 8
    assert c["feature"]["minimum_prior_matches"] == 3
    assert c["feature"]["same_calendar_date_isolation"] is True
    assert c["method"]["axis"] == "draw_only"
    assert c["method"]["fixed_ridge"] == 0.01
    assert c["method"]["no_hyperparameter_grid"] is True
    assert c["method"]["no_threshold_search"] is True
    assert c["method"]["no_probability_clipping"] is True
    assert c["method"]["no_rescue_if_fail"] is True

    frozen = common.build_frozen_baseline(a, "prior_xgbuildup_responsibility_candidate")
    dev = [r for r in frozen["rows"] if r["season"] in (2020, 2021, 2022) and r["fixture_id"] in frozen["bmap"]]
    if len(dev) != 5478:
        raise RuntimeError(f"development_n drift: {len(dev)}")

    fmap, source_audit = xgbuildup_feature_map(
        a.db, int(c["feature"]["lookback_completed_league_matches"]), int(c["feature"]["minimum_prior_matches"])
    )
    if abs(source_audit["bilateral_feature_coverage"] - float(c["parent_source"]["source_feature_coverage"])) > 1e-15:
        raise RuntimeError("source PASS coverage does not reproduce")

    wanted = {r["fixture_id"] for r in dev}
    snaps, snaprec = bmod.make_snapshots(a.db, wanted, float(c["frozen_bases"]["stage6_b_half_life"]))
    bprob, bmats = {}, {}
    for r in dev:
        fid = r["fixture_id"]
        p, _ = bmod.predict(frozen["bmap"][fid], snaps.get(fid), float(c["frozen_bases"]["stage6_b_coefficient"]))
        bprob[fid] = list(map(float, p))
        bmats[fid] = common.region_rescale(frozen["bmats"][fid], frozen["bmap"][fid], bprob[fid])

    rows = []
    for r in dev:
        fid = r["fixture_id"]
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        y = 0 if hg > ag else 1 if hg == ag else 2
        rec = dict(r)
        rec.update(fmap.get(fid, {}))
        rec["y"] = y
        rec["b_prob"] = bprob[fid]
        rows.append(rec)

    coverage = sum(r.get(FEATURE) is not None for r in rows) / len(rows)
    ridge = float(c["method"]["fixed_ridge"])
    cand, fold_results = {}, []
    max_ratio_err = max_prob_delta = 0.0

    for season in (2021, 2022):
        train = [r for r in rows if r["season"] < season]
        test = [r for r in rows if r["season"] == season]
        params, draw_pred = sh.fit_fold(train, test, ridge)
        for r, pd in zip(test, draw_pred):
            fid = r["fixture_id"]
            cp = sh.candidate_1x2(r["b_prob"], pd)
            cand[fid] = cp
            max_ratio_err = max(max_ratio_err, sh.ratio_error(r["b_prob"], cp))
            max_prob_delta = max(max_prob_delta, max(abs(cp[i] - float(r["b_prob"][i])) for i in range(3)))
        bm = common.metrics(test, {r["fixture_id"]: r["b_prob"] for r in test})
        cm = common.metrics(test, {r["fixture_id"]: cand[r["fixture_id"]] for r in test})
        draw_y = [int(r["y"] == 1) for r in test]
        base_draw_ll = sh.binary_ll(draw_y, [float(r["b_prob"][1]) for r in test])
        cand_draw_ll = sh.binary_ll(draw_y, [float(cand[r["fixture_id"]][1]) for r in test])
        fold_results.append({
            "season": season, "train_n": len(train), "test_n": len(test), "fit": params,
            "baseline": bm, "candidate": cm,
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
    if len(cand) != len(test_rows):
        raise RuntimeError("candidate map incomplete")
    base_test = {r["fixture_id"]: r["b_prob"] for r in test_rows}
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

    base_exact = sh.exact_score_ll(test_rows, bmats)
    cand_exact = sh.exact_score_ll(test_rows, cand_mats)
    base_total = sh.total_goals_ll(test_rows, bmats)
    cand_total = sh.total_goals_ll(test_rows, cand_mats)
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
        "minimum_feature_coverage": coverage >= float(g["minimum_feature_coverage"]),
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
        mu, sd = sh.standardization(rows)
        X = sh.design(rows, mu, sd)
        y = [int(r["y"] == 1) for r in rows]
        offsets = [sh.strict_logit(float(r["b_prob"][1])) for r in rows]
        beta = et.fit_offset(X, y, offsets, ridge)
        final_params = {
            "schema_version": "football3-xgbuildup-responsibility-frozen-params-v1",
            "status": "FROZEN_AFTER_DEVELOPMENT_PASS_BEFORE_ANY_CONFIRMATION",
            "feature_family": c["feature"]["family"], "feature_key": FEATURE,
            "standardization_mean": mu, "standardization_sd": sd,
            "beta_intercept": float(beta[0]), "beta_slope": float(beta[1]),
            "fixed_ridge": ridge, "training_seasons": [2020, 2021, 2022], "training_n": len(rows),
            "formal_weight": 0, "historical_confirmation_2023_labels_opened": False,
            "prospective_1335_data_touched": False,
        }
        common.write_json(a.out / "FROZEN_XGBUILDUP_RESPONSIBILITY_PARAMS.json", final_params)

    out = {
        "schema_version": "football3-xgbuildup-responsibility-candidate-result-v1",
        "status": c["terminal"]["pass"] if scientific_pass else c["terminal"]["fail"],
        "scientific_pass": scientific_pass, "research_only": True, "formal_weight": 0,
        "development_n": len(rows), "rolling_oos_n": len(test_rows), "source_max_season_loaded": 2022,
        "historical_confirmation_2023_labels_opened": False, "prospective_1335_data_touched": False,
        "source_audit_reproduction": source_audit, "coverage": coverage,
        "feature_family": c["feature"]["family"], "feature_key": FEATURE,
        "fold_results": fold_results,
        "pooled": {
            "baseline": base_metrics, "candidate": cand_metrics, "deltas": pooled_delta,
            "exact_score_logloss": {"baseline": base_exact, "candidate": cand_exact},
            "total_goals_logloss": {"baseline": base_total, "candidate": cand_total},
        },
        "max_outcome_probability_abs_delta": max_prob_delta,
        "conditional_home_away_ratio_max_abs_error": max_ratio_err,
        "matrix_1x2_max_abs_error": matrix_err,
        "checks": checks, "frozen_params_written": final_params is not None,
        "next_step": "FIND_UNCONSUMED_CONFIRMATION_COHORT" if scientific_pass else "CLOSE_XGBUILDUP_RESPONSIBILITY_NO_RETUNE",
    }
    common.write_json(a.out / "xgbuildup_responsibility_candidate_result.json", out)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
