from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import pathlib
import statistics


def load_framework(identity_wrapper: pathlib.Path):
    spec = importlib.util.spec_from_file_location("frozen_identity_framework", identity_wrapper)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen identity framework")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.base


def build_source_features(wys: list[dict], identity: dict[int, dict], lookback: int, min_prior: int) -> tuple[dict[int, dict], dict]:
    profiles: dict[int, dict[int, dict]] = {}
    raw_valid_team_match_n = 0
    accurate_xy_pass_n = 0
    for m in wys:
        z = {
            m["home_wys_id"]: {"accurate_xy": 0, "forward_gain": 0.0},
            m["away_wys_id"]: {"accurate_xy": 0, "forward_gain": 0.0},
        }
        for e in m["events"]:
            tid = int(e.get("teamId", 0) or 0)
            if tid not in z or e.get("eventName") != "Pass":
                continue
            tags = {int(t.get("id")) for t in (e.get("tags") or []) if isinstance(t, dict) and t.get("id") is not None}
            if 1801 not in tags:
                continue
            pos = e.get("positions") or []
            good = (
                len(pos) >= 2
                and all(isinstance(q, dict) for q in pos[:2])
                and all(isinstance(pos[i].get("x"), (int, float)) and isinstance(pos[i].get("y"), (int, float)) for i in (0, 1))
            )
            if not good:
                continue
            x0 = float(pos[0]["x"])
            x1 = float(pos[1]["x"])
            if not (0.0 <= x0 <= 100.0 and 0.0 <= x1 <= 100.0):
                raise RuntimeError(f"coordinate domain drift {m['wys_match_id']}: {x0},{x1}")
            z[tid]["accurate_xy"] += 1
            z[tid]["forward_gain"] += max(x1 - x0, 0.0)
            accurate_xy_pass_n += 1
        for tid, rec in z.items():
            if int(rec["accurate_xy"]) <= 0:
                raise RuntimeError(f"no accurate XY passes {m['wys_match_id']} team={tid}")
            rec["raw"] = float(rec["forward_gain"]) / int(rec["accurate_xy"])
            raw_valid_team_match_n += 1
        profiles[m["wys_match_id"]] = z

    states = collections.defaultdict(lambda: collections.deque(maxlen=lookback))
    bydate = collections.OrderedDict()
    for m in sorted(wys, key=lambda q: (q["date"], q["source_file"], q["wys_match_id"])):
        bydate.setdefault(m["date"], []).append(m)

    fmap: dict[int, dict] = {}
    covered = 0
    for _, batch in bydate.items():
        for m in batch:
            vals = []
            for tid in (m["home_wys_id"], m["away_wys_id"]):
                q = states[(m["source_file"], tid)]
                vals.append(statistics.fmean(q) if len(q) >= min_prior else None)
            rec = {"home_state": vals[0], "away_state": vals[1]}
            if vals[0] is not None and vals[1] is not None:
                rec["direction"] = float(vals[0] - vals[1])
                rec["openness"] = float(0.5 * (vals[0] + vals[1]))
                covered += 1
            uid = int(identity[m["wys_match_id"]]["id"])
            fmap[uid] = rec
        # Same-calendar-date isolation: current-day raw match features enter state only after
        # every fixture on that date has had its pre-match feature constructed.
        for m in batch:
            p = profiles[m["wys_match_id"]]
            states[(m["source_file"], m["home_wys_id"])].append(float(p[m["home_wys_id"]]["raw"]))
            states[(m["source_file"], m["away_wys_id"])].append(float(p[m["away_wys_id"]]["raw"]))

    if len(fmap) != 1826:
        raise RuntimeError(f"feature map identity drift: {len(fmap)}")
    return fmap, {
        "target_n": 1826,
        "raw_valid_team_match_n": raw_valid_team_match_n,
        "accurate_xy_pass_n": accurate_xy_pass_n,
        "bilateral_feature_n": covered,
        "bilateral_feature_coverage": covered / 1826.0,
        "lookback": lookback,
        "min_prior": min_prior,
        "same_calendar_date_isolation": True,
        "raw_feature": "sum(max(end_x-start_x,0) for accurate passes) / accurate_xy_pass_n",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", type=pathlib.Path, required=True)
    ap.add_argument("--db", type=pathlib.Path, required=True)
    ap.add_argument("--wys-root", type=pathlib.Path, required=True)
    ap.add_argument("--pure-engine", type=pathlib.Path, required=True)
    ap.add_argument("--identity-wrapper", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    c = json.loads(a.contract.read_text(encoding="utf-8"))
    if c["status"] != "FROZEN_BEFORE_OLD_SOURCE_SCREENING_EVALUATION":
        raise RuntimeError("candidate contract not frozen")
    if c["feature"]["lookback_completed_league_matches"] != 8 or c["feature"]["minimum_prior_matches"] != 3:
        raise RuntimeError("feature contract drift")
    if c["feature"]["distance_threshold"] is not None or not c["feature"]["no_threshold_search"]:
        raise RuntimeError("threshold contract drift")
    if c["model"]["fixed_ridge"] != 0.01 or not c["model"]["no_hyperparameter_grid"] or not c["model"]["no_rescue_if_fail"]:
        raise RuntimeError("model contract drift")

    b = load_framework(a.identity_wrapper)
    wys = b.parse_wyscout_readme(a.wys_root)
    b.recover_wyscout_team_ids(wys)
    hist, target = b.load_understat(a.db)
    ident, irec = b.map_identity(wys, target)
    fmap, faudit = build_source_features(wys, ident, 8, 3)
    baseline = b.build_baseline(hist, a.pure_engine, c["baseline"]["selected_parameters"])

    rows = []
    for u in target:
        uid = int(u["id"])
        hg, ag = int(u["h_goals"]), int(u["a_goals"])
        rec = {
            "understat_id": uid,
            "fixture_id": baseline[uid]["fixture_id"],
            "date": str(u["date"])[:10],
            "kickoff": str(u["date"]),
            "league": u["league"],
            "home": u["team_h"],
            "away": u["team_a"],
            "home_goals": hg,
            "away_goals": ag,
            "y": 0 if hg > ag else 1 if hg == ag else 2,
            "base_prob": baseline[uid]["base_prob"],
            "base_matrix": baseline[uid]["base_matrix"],
        }
        rec.update(fmap[uid])
        rows.append(rec)
    rows.sort(key=lambda r: (r["kickoff"], r["understat_id"]))

    cov = float(faudit["bilateral_feature_coverage"])
    if cov < float(c["gates"]["minimum_bilateral_feature_coverage"]):
        raise RuntimeError(f"feature coverage below frozen gate: {cov}")
    c40, c60, c80 = b.date_cut(rows, 0.40), b.date_cut(rows, 0.60), b.date_cut(rows, 0.80)
    folds = [(0, c40, c60), (0, c60, c80), (0, c80, len(rows))]

    pooled_base = []
    pooled_cand = []
    fold_results = []
    max_delta = 0.0
    for fi, (start, train_end, test_end) in enumerate(folds, 1):
        train = rows[start:train_end]
        test = rows[train_end:test_end]
        fit = b.fit_newton(train, float(c["model"]["fixed_ridge"]), int(c["model"]["max_iterations"]), float(c["model"]["convergence_abs_step"]))
        scored_base = []
        scored_cand = []
        for r in test:
            cp = b.predict_candidate(r, fit)
            max_delta = max(max_delta, max(abs(cp[i] - r["base_prob"][i]) for i in range(3)))
            cm = b.rescale_matrix(r["base_matrix"], r["base_prob"], cp)
            mb = b.metric(r["base_prob"], r["y"])
            mc = b.metric(cp, r["y"])
            mb["exact_score_ll"] = b.score_ll(r["base_matrix"], r["home_goals"], r["away_goals"])
            mb["total_goals_ll"] = b.total_ll(r["base_matrix"], r["home_goals"] + r["away_goals"])
            mc["exact_score_ll"] = b.score_ll(cm, r["home_goals"], r["away_goals"])
            mc["total_goals_ll"] = b.total_ll(cm, r["home_goals"] + r["away_goals"])
            scored_base.append(mb)
            scored_cand.append(mc)
            pooled_base.append(mb)
            pooled_cand.append(mc)
        ab, ac = b.agg(scored_base), b.agg(scored_cand)
        deltas = {"hits": ac["hits"] - ab["hits"], "top1_pp": 100.0 * (ac["top1_accuracy"] - ab["top1_accuracy"])}
        for key in ("logloss", "brier", "rps", "exact_score_ll", "total_goals_ll"):
            deltas[key] = ac[key] - ab[key]
        fold_results.append({
            "fold": fi,
            "train_n": len(train),
            "test_n": len(test),
            "train_end_date": train[-1]["date"],
            "test_first_date": test[0]["date"],
            "test_last_date": test[-1]["date"],
            "fit": fit,
            "baseline": ab,
            "candidate": ac,
            "deltas": deltas,
        })

    pb, pc = b.agg(pooled_base), b.agg(pooled_cand)
    pd = {"hits": pc["hits"] - pb["hits"], "top1_pp": 100.0 * (pc["top1_accuracy"] - pb["top1_accuracy"])}
    for key in ("logloss", "brier", "rps", "exact_score_ll", "total_goals_ll"):
        pd[key] = pc[key] - pb[key]
    fold_nonworse = sum(fr["deltas"]["logloss"] <= 0.0 and fr["deltas"]["rps"] <= 0.0 for fr in fold_results)
    g = c["gates"]
    checks = {
        "feature_coverage": cov >= float(g["minimum_bilateral_feature_coverage"]),
        "max_probability_delta": max_delta <= float(g["max_outcome_probability_abs_delta"]),
        "top1": pd["hits"] >= int(g["pooled_top1_hit_delta_min"]),
        "logloss": pd["logloss"] <= float(g["pooled_logloss_delta_max"]),
        "brier": pd["brier"] <= float(g["pooled_brier_delta_max"]),
        "rps": pd["rps"] <= float(g["pooled_rps_delta_max"]),
        "exact_score_ll": pd["exact_score_ll"] <= float(g["pooled_exact_score_logloss_delta_max"]),
        "total_goals_ll": pd["total_goals_ll"] <= float(g["pooled_total_goals_logloss_delta_max"]),
        "fold_consistency": fold_nonworse >= int(g["minimum_folds_with_logloss_and_rps_nonworse"]),
    }
    passed = all(checks.values())
    status = "PASS_PROGRESSION_OLD_SOURCE_SCREEN_PASS_REQUIRES_NEWER_SOURCE_CONFIRMATION" if passed else "PASS_PROGRESSION_OLD_SOURCE_SCREEN_FAIL_CLOSE_EXACT_FORMULATION_NO_RETUNE"
    result = {
        "schema_version": "football3-wyscout-pass-progression-old-source-screen-result-v1",
        "status": status,
        "screening_pass": passed,
        "research_class": c["research_class"],
        "target_n": len(rows),
        "rolling_oos_n": len(pooled_base),
        "feature_audit": faudit,
        "identity_audit": {k: v for k, v in irec.items() if k != "rows"},
        "fold_results": fold_results,
        "pooled": {"baseline": pb, "candidate": pc, "deltas": pd},
        "max_probability_abs_delta": max_delta,
        "folds_nonworse_logloss_and_rps": fold_nonworse,
        "checks": checks,
        "formal_weight": 0,
        "newer_source_confirmation_required_if_pass": True,
        "historical_confirmation_2023_opened": False,
        "prospective_1335_touched": False,
        "CURRENT_changed": False,
        "formal_model_changed": False,
        "retune_allowed": False,
    }
    (a.out / "PASS_PROGRESSION_SCREEN_RESULT.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (a.out / "IDENTITY_MAPPING.jsonl").write_text("".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in irec["rows"]), encoding="utf-8")
    print(json.dumps({"status": status, "coverage": cov, "rolling_oos_n": len(pooled_base), "pooled_deltas": pd, "checks": checks, "max_probability_abs_delta": max_delta}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
