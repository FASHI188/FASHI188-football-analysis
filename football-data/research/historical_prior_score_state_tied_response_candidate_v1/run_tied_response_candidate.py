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
import common
import run_stage6_pre_b as bmod
import run_event_temporal_process_residual as et

EPS = 1e-15
FEATURE = "pair_mean_tied_activity_rate_minus_all_activity_rate"
STATES = ("tied", "leading", "trailing")


def strict_logit(p: float) -> float:
    p = float(p)
    if not (0.0 < p < 1.0):
        raise RuntimeError(f"offset probability outside (0,1): {p}")
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0.0:
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


def score_state(hg: int, ag: int, side: str) -> str:
    if hg == ag:
        return "tied"
    if side == "h":
        return "leading" if hg > ag else "trailing"
    return "leading" if ag > hg else "trailing"


def blank_profile() -> dict[str, dict[str, float]]:
    return {
        s: {"minutes": 0.0, "xg_for": 0.0, "xg_against": 0.0}
        for s in STATES
    }


def summarize(events: list[dict], official_h: int, official_a: int) -> dict:
    hp, ap = blank_profile(), blank_profile()
    hg = ag = 0
    prev = 0
    invalid = 0
    valid_minutes: list[int] = []
    for e in events:
        try:
            minute = int(e["minute"])
            side = str(e["h_a"])
            xg = float(e["xG"])
            result = str(e["result"])
            situation = str(e["situation"])
        except Exception:
            invalid += 1
            continue
        if side not in ("h", "a") or minute < 0 or minute > 120 or not math.isfinite(xg) or xg < 0.0:
            invalid += 1
            continue
        valid_minutes.append(minute)
        if minute < prev:
            invalid += 1
            continue
        dtm = minute - prev
        hp[score_state(hg, ag, "h")]["minutes"] += dtm
        ap[score_state(hg, ag, "a")]["minutes"] += dtm
        if situation != "Penalty" and result != "OwnGoal":
            if side == "h":
                hs = score_state(hg, ag, "h")
                aas = score_state(hg, ag, "a")
                hp[hs]["xg_for"] += xg
                ap[aas]["xg_against"] += xg
            else:
                aas = score_state(hg, ag, "a")
                hs = score_state(hg, ag, "h")
                ap[aas]["xg_for"] += xg
                hp[hs]["xg_against"] += xg
        if result == "Goal":
            if side == "h":
                hg += 1
            else:
                ag += 1
        elif result == "OwnGoal":
            if side == "h":
                ag += 1
            else:
                hg += 1
        prev = minute
    end = min(120, max([90] + valid_minutes))
    dtm = max(0, end - prev)
    hp[score_state(hg, ag, "h")]["minutes"] += dtm
    ap[score_state(hg, ag, "a")]["minutes"] += dtm
    return {
        "home": hp,
        "away": ap,
        "reconstructed": [hg, ag],
        "score_exact": (hg, ag) == (official_h, official_a),
        "invalid_event_n": invalid,
    }


def profile_value(q: collections.deque, minimum_tied_minutes: float) -> float | None:
    if not q:
        return None
    tied_minutes = sum(float(x["tied"]["minutes"]) for x in q)
    if tied_minutes < minimum_tied_minutes:
        return None
    tied_xg = sum(float(x["tied"]["xg_for"]) + float(x["tied"]["xg_against"]) for x in q)
    all_minutes = sum(float(x[s]["minutes"]) for x in q for s in STATES)
    all_xg = sum(float(x[s]["xg_for"]) + float(x[s]["xg_against"]) for x in q for s in STATES)
    if all_minutes <= 0.0:
        return None
    return 90.0 * tied_xg / tied_minutes - 90.0 * all_xg / all_minutes


def tied_response_feature_map(
    db: pathlib.Path,
    lookback: int,
    minimum_prior: int,
    minimum_tied_minutes: float,
) -> tuple[dict[str, dict[str, float]], dict]:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    qs = ",".join("?" for _ in common.LEAGUES)
    games = [
        dict(r)
        for r in con.execute(
            f"""
            select id,fid,date,league,season,h_id,a_id,h_goals,a_goals
            from general_game_stats
            where league in ({qs}) and season between 2014 and 2022
            order by date,id
            """,
            common.LEAGUES,
        )
    ]
    events: dict[int, list[dict]] = collections.defaultdict(list)
    event_n = 0
    own_goal_n = 0
    for r in con.execute(
        f"""
        select e.id,e.match_id,e.h_a,e.minute,e.xG,e.situation,e.result
        from game_events e
        join general_game_stats g on g.id=e.match_id
        where g.league in ({qs}) and g.season between 2014 and 2022
        order by e.match_id,e.minute,e.id
        """,
        common.LEAGUES,
    ):
        d = dict(r)
        events[int(d["match_id"])].append(d)
        event_n += 1
        own_goal_n += int(str(d["result"]) == "OwnGoal")
    con.close()

    if len(games) != 16332:
        raise RuntimeError(f"history n drift: {len(games)}")
    if own_goal_n != 1343:
        raise RuntimeError(f"own-goal n drift: {own_goal_n}")

    summaries: dict[int, dict] = {}
    exact_n = 0
    invalid_n = 0
    for g in games:
        s = summarize(events[int(g["id"])], int(g["h_goals"]), int(g["a_goals"]))
        summaries[int(g["id"])] = s
        exact_n += int(s["score_exact"])
        invalid_n += int(s["invalid_event_n"])
    if exact_n != 16327:
        raise RuntimeError(f"exact score reconstruction drift: {exact_n}")
    if invalid_n != 0:
        raise RuntimeError(f"invalid event drift: {invalid_n}")

    hist: dict[tuple[str, int], collections.deque] = collections.defaultdict(
        lambda: collections.deque(maxlen=lookback)
    )
    bydate: collections.OrderedDict[str, list[dict]] = collections.OrderedDict()
    for g in games:
        bydate.setdefault(str(g["date"]), []).append(g)

    out: dict[str, dict[str, float]] = {}
    covered_n = 0
    target_n = 0
    for _, batch in bydate.items():
        batch = sorted(batch, key=lambda z: int(z["id"]))
        for g in batch:
            if int(g["season"]) < 2020:
                continue
            target_n += 1
            hk = (str(g["league"]), int(g["h_id"]))
            ak = (str(g["league"]), int(g["a_id"]))
            hq = hist.get(hk)
            aq = hist.get(ak)
            rec: dict[str, float] = {}
            if hq is not None and aq is not None and len(hq) >= minimum_prior and len(aq) >= minimum_prior:
                hv = profile_value(hq, minimum_tied_minutes)
                av = profile_value(aq, minimum_tied_minutes)
                if hv is not None and av is not None and math.isfinite(hv) and math.isfinite(av):
                    rec[FEATURE] = 0.5 * (hv + av)
                    covered_n += 1
            out[f"understat:{int(g['fid'])}"] = rec

        # Same-date isolation: target-date matches cannot enter another target's snapshot.
        for g in batch:
            s = summaries[int(g["id"])]
            if not s["score_exact"] or s["invalid_event_n"]:
                continue
            hist[(str(g["league"]), int(g["h_id"]))].append(s["home"])
            hist[(str(g["league"]), int(g["a_id"]))].append(s["away"])

    return out, {
        "history_game_n": len(games),
        "event_n": event_n,
        "own_goal_event_n": own_goal_n,
        "exact_final_score_n": exact_n,
        "invalid_event_n": invalid_n,
        "target_feature_map_n": target_n,
        "target_feature_covered_n": covered_n,
        "target_feature_coverage": covered_n / max(1, target_n),
    }


def standardization(rows: list[dict]) -> tuple[float, float]:
    vals = [
        float(r[FEATURE])
        for r in rows
        if r.get(FEATURE) is not None and math.isfinite(float(r[FEATURE]))
    ]
    if not vals:
        raise RuntimeError("no training feature values")
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
    return mu, sd


def design(rows: list[dict], mu: float, sd: float) -> list[list[float]]:
    X: list[list[float]] = []
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
    ph, _, pa = map(float, base)
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
    for arg in ("contract", "v311", "v31", "usr1", "v2", "xg", "v1", "v1_result", "db", "xg_identity", "out"):
        ap.add_argument("--" + arg.replace("_", "-"), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text())
    assert c["status"] == "FROZEN_BEFORE_CANDIDATE_EVALUATION"
    assert c["feature"]["family"] == "prior_score_state_tied_response"
    assert c["feature"]["key"] == FEATURE
    assert c["method"]["fixed_ridge"] == 0.01
    assert c["method"]["no_hyperparameter_grid"] is True
    assert c["method"]["no_threshold_search"] is True
    assert c["method"]["no_probability_clipping"] is True

    frozen = common.build_frozen_baseline(a, "prior_score_state_tied_response_candidate")
    dev = [
        r for r in frozen["rows"]
        if r["season"] in (2020, 2021, 2022) and r["fixture_id"] in frozen["bmap"]
    ]
    if len(dev) != 5478:
        raise RuntimeError(f"development_n drift: {len(dev)}")

    fmap, event_audit = tied_response_feature_map(
        a.db,
        int(c["feature"]["lookback_completed_matches"]),
        int(c["feature"]["minimum_clean_prior_matches"]),
        float(c["feature"]["minimum_tied_minutes"]),
    )
    wanted = {r["fixture_id"] for r in dev}
    snaps, snaprec = bmod.make_snapshots(
        a.db, wanted, float(c["frozen_bases"]["stage6_b_half_life"])
    )
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
        bmats[fid] = common.region_rescale(
            frozen["bmats"][fid], frozen["bmap"][fid], bprob[fid]
        )

    rows: list[dict] = []
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
    cand: dict[str, list[float]] = {}
    fold_results: list[dict] = []
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
            max_prob_delta = max(
                max_prob_delta,
                max(abs(cp[i] - float(r["b_prob"][i])) for i in range(3)),
            )

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

    cand_mats: dict[str, list[list[float]]] = {}
    matrix_err = 0.0
    for r in test_rows:
        fid = r["fixture_id"]
        m = common.region_rescale(bmats[fid], bprob[fid], cand[fid])
        cand_mats[fid] = m
        got = common.integrate_matrix(m)
        matrix_err = max(
            matrix_err, max(abs(got[i] - cand[fid][i]) for i in range(3))
        )

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
        "minimum_feature_coverage": coverage >= float(g["minimum_feature_coverage"]),
        "draw_logloss_each_fold_strict_improvement": all(
            f["deltas"]["draw_logloss"] < 0.0 for f in fold_results
        ),
        "pooled_1x2_logloss_nonworse": pooled_delta["logloss"] <= float(g["pooled_1x2_logloss_delta_max"]) + 1e-15,
        "pooled_1x2_brier_nonworse": pooled_delta["brier"] <= float(g["pooled_1x2_brier_delta_max"]) + 1e-15,
        "pooled_1x2_rps_nonworse": pooled_delta["rps"] <= float(g["pooled_1x2_rps_delta_max"]) + 1e-15,
        "pooled_top1_nondecrease": pooled_delta["hits"] >= int(g["pooled_top1_delta_hits_min"]),
        "each_fold_top1_nondecrease": all(
            f["deltas"]["hits"] >= int(g["each_fold_top1_delta_hits_min"])
            for f in fold_results
        ),
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
            "schema_version": "football3-prior-score-state-tied-response-frozen-params-v1",
            "status": "FROZEN_AFTER_DEVELOPMENT_PASS_BEFORE_ANY_CONFIRMATION",
            "feature_family": "prior_score_state_tied_response",
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
        common.write_json(a.out / "FROZEN_TIED_RESPONSE_PARAMS.json", final_params)

    out = {
        "schema_version": "football3-prior-score-state-tied-response-candidate-result-v1",
        "status": c["terminal"]["pass"] if scientific_pass else c["terminal"]["fail"],
        "scientific_pass": scientific_pass,
        "research_only": True,
        "formal_weight": 0,
        "development_n": len(rows),
        "rolling_oos_n": len(test_rows),
        "source_max_season_loaded": 2022,
        "coverage": coverage,
        "event_audit": event_audit,
        "stage6_b_active_n": snaprec["active"],
        "historical_confirmation_2023_labels_opened": False,
        "prospective_1335_data_touched": False,
        "feature_family": "prior_score_state_tied_response",
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
            else "CLOSE_TIED_RESPONSE_NO_RETUNE"
        ),
    }
    common.write_json(a.out / "tied_response_candidate_result.json", out)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
