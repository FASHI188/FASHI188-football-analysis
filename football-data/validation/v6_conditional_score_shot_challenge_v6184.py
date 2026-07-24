#!/usr/bin/env python3
"""V6.18.4 shot-informed conditional score-allocation challenger.

Research-only. This challenge preserves the formal total-goals distribution exactly and
only challenges P(H | T=t, X), equivalently the home/away allocation conditional on a
given exact total t. Exact totals 0..6 are preserved cell-group by cell-group; all 7+
cells are left unchanged. Therefore P(T=0..6,7+) is invariant by construction.

Four arms:
A formal matrix
B conditional calibration: formal P(H|T=t) only
C competition control: B + competition one-hot
D shot: C + strictly lagged shot/SOT/corner process features

Models are separate multinomial logistic regressions for each t=1..6 where training
support is sufficient. Candidate regularization is selected on an earlier validation
season only. A candidate is eligible only if validation exact-score LogLoss, 1X2 RPS,
and 1X2 LogLoss are all non-inferior to the formal matrix and total-marginal invariance
passes. Only then may exact-score Top-1/Top-3 choose among eligible candidates.

Chronological folds:
- train 2022/23; validate 2023/24; test 2024/25
- train 2022/23+2023/24; validate 2024/25; test 2025/26

2025/26 has already been inspected elsewhere, so all results are retrospective OOS
research, never promotion-grade prospective evidence. formal_weight=0.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as fix

OUT = base.ROOT / "manifests" / "v6_conditional_score_shot_challenge_v6184_status.json"
TOTALS_TO_MODEL = tuple(range(1, 7))
CANDIDATE_C = (0.01, 0.03, 0.1, 0.3, 1.0)
MIN_TRAIN_PER_TOTAL = 120
EPS = 1e-15
TOL = 1e-10


def build_rows(shot_lookup):
    cfg = base.load_config()
    rows = []
    meta = {}
    for cid in base.COMPS:
        params_map = base.ou.params_by_season(cid)
        matches = [m for m in base.read_processed_matches(cid) if str(m.season) in base.SEASONS]
        byseason = defaultdict(list)
        for m in matches:
            byseason[str(m.season)].append(m)
        meta[cid] = {}
        for season in base.SEASONS:
            sm = byseason.get(season, [])
            params = params_map.get(season)
            if not sm or not params:
                meta[cid][season] = {"matches": len(sm), "rows": 0, "reason": "NO_MATCHES_OR_PARAMS"}
                continue
            temp = base.ou.calibrator(cid, season)
            bydate = defaultdict(list)
            for m in sm:
                bydate[m.date].append(m)
            hist = []
            hc = Counter(); ac = Counter(); count = 0; failures = 0
            warmc = int(cfg["validation"]["warmup_competition_matches"])
            warmt = int(cfg["validation"]["warmup_team_matches"])
            for dt in sorted(bydate):
                for m in sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team)):
                    key = (cid, season, m.date.isoformat(), m.home_team, m.away_team)
                    feat = shot_lookup.get(key)
                    if feat is not None and len(hist) >= warmc and hc[m.home_team] >= warmt and ac[m.away_team] >= warmt:
                        try:
                            pred = base.predict_from_history(
                                hist, cid, season, m.home_team, m.away_team, m.date,
                                selected_parameters=params, use_team_effects=True,
                            )
                            matrix = base.temperature_scale_matrix(pred["probabilities"]["score_matrix"], temp)
                            rows.append({
                                "competition_id": cid,
                                "season": season,
                                "date": m.date.isoformat(),
                                "home_team": m.home_team,
                                "away_team": m.away_team,
                                "home_goals": int(m.home_goals),
                                "away_goals": int(m.away_goals),
                                "actual_total": int(m.home_goals) + int(m.away_goals),
                                "formal_matrix": matrix,
                                "shots": feat,
                            })
                            count += 1
                        except Exception:
                            failures += 1
                    hist.append(m); hc[m.home_team] += 1; ac[m.away_team] += 1
            meta[cid][season] = {"matches": len(sm), "rows": count, "prediction_failures": failures}
    rows.sort(key=lambda r: (r["season"], r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    return rows, meta


def matrix_map(matrix):
    return {(int(c["home_goals"]), int(c["away_goals"])): float(c["probability"]) for c in matrix}


def conditional_prior(matrix, total):
    mp = matrix_map(matrix)
    probs = [max(EPS, mp.get((h, total - h), 0.0)) for h in range(total + 1)]
    mass = sum(probs)
    if mass <= 0:
        return 0.0, [1.0 / (total + 1)] * (total + 1)
    return mass, [p / mass for p in probs]


def shot_names_and_comps(rows):
    shot_names = sorted(rows[0]["shots"])
    comps = sorted({r["competition_id"] for r in rows})
    return shot_names, comps


def xvec(row, total, mode, shot_names, comps):
    _, cond = conditional_prior(row["formal_matrix"], total)
    x = [math.log(max(EPS, p)) for p in cond]
    if mode in {"competition", "shot"}:
        x.extend(1.0 if row["competition_id"] == c else 0.0 for c in comps)
    if mode == "shot":
        x.extend(float(row["shots"][k]) for k in shot_names)
    return x


def fit_models(rows, mode, c_value, shot_names, comps):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    models = {}
    support = {}
    for total in TOTALS_TO_MODEL:
        sub = [r for r in rows if r["actual_total"] == total]
        classes = sorted({r["home_goals"] for r in sub})
        expected = list(range(total + 1))
        support[str(total)] = {"rows": len(sub), "classes": classes, "expected_classes": expected}
        if len(sub) < MIN_TRAIN_PER_TOTAL or classes != expected:
            continue
        X = [xvec(r, total, mode, shot_names, comps) for r in sub]
        y = [r["home_goals"] for r in sub]
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=float(c_value), max_iter=4000, solver="lbfgs"),
        )
        model.fit(X, y)
        models[total] = model
    return models, support


def model_cond(model, row, total, mode, shot_names, comps):
    arr = model.predict_proba([xvec(row, total, mode, shot_names, comps)])[0]
    classes = [int(x) for x in model.classes_]
    q = [0.0] * (total + 1)
    for cls, v in zip(classes, arr):
        q[cls] = float(v)
    s = sum(q)
    return [v / s for v in q]


def adjusted_matrix(row, models, mode, shot_names, comps):
    prior = row["formal_matrix"]
    mp = matrix_map(prior)
    out = []
    for c in prior:
        h, a = int(c["home_goals"]), int(c["away_goals"])
        t = h + a
        if t not in models:
            out.append({"home_goals": h, "away_goals": a, "probability": float(c["probability"])})
    for t, model in models.items():
        cells = [(h, t - h) for h in range(t + 1)]
        mass = sum(mp.get(cell, 0.0) for cell in cells)
        if mass <= 0:
            continue
        q = model_cond(model, row, t, mode, shot_names, comps)
        for h in range(t + 1):
            out.append({"home_goals": h, "away_goals": t - h, "probability": mass * q[h]})
    s = sum(float(c["probability"]) for c in out)
    if not math.isfinite(s) or s <= 0:
        raise RuntimeError("invalid adjusted score mass")
    return [{**c, "probability": float(c["probability"]) / s} for c in out]


def result_index(h, a):
    return 0 if h > a else 1 if h == a else 2


def total_bucket_vector(matrix):
    out = [0.0] * 8
    for c in matrix:
        t = int(c["home_goals"]) + int(c["away_goals"])
        out[min(7, t)] += float(c["probability"])
    return out


def one_x_two(matrix):
    m = base.derive_score_marginals(matrix)["1x2"]
    return [float(m["home"]), float(m["draw"]), float(m["away"])]


def row_metrics(row, matrix):
    hg, ag = row["home_goals"], row["away_goals"]
    ranked = sorted(matrix, key=lambda c: (-float(c["probability"]), int(c["home_goals"]), int(c["away_goals"])))
    top1 = int(bool(ranked) and (int(ranked[0]["home_goals"]), int(ranked[0]["away_goals"])) == (hg, ag))
    top3 = int(any((int(c["home_goals"]), int(c["away_goals"])) == (hg, ag) for c in ranked[:3]))
    pscore = next((float(c["probability"]) for c in matrix if int(c["home_goals"]) == hg and int(c["away_goals"]) == ag), 0.0)
    p1 = one_x_two(matrix)
    y = result_index(hg, ag)
    one_log = -math.log(max(EPS, p1[y]))
    brier = sum((p1[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
    cp = 0.0; rps = 0.0
    for k in range(2):
        cp += p1[k]
        cy = 1.0 if y <= k else 0.0
        rps += (cp - cy) ** 2
    rps /= 2.0
    return {"score_top1": top1, "score_top3": top3, "score_logloss": -math.log(max(EPS, pscore)), "one_x_two_logloss": one_log, "one_x_two_brier": brier, "one_x_two_rps": rps}


def aggregate(rows, getter):
    acc = {"count": 0, "score_top1": 0, "score_top3": 0, "score_logloss": 0.0, "one_x_two_logloss": 0.0, "one_x_two_brier": 0.0, "one_x_two_rps": 0.0, "max_total_residual": 0.0, "max_probability_residual": 0.0}
    for r in rows:
        m = getter(r)
        rm = row_metrics(r, m)
        acc["count"] += 1
        for k in ("score_top1", "score_top3"):
            acc[k] += rm[k]
        for k in ("score_logloss", "one_x_two_logloss", "one_x_two_brier", "one_x_two_rps"):
            acc[k] += rm[k]
        a = total_bucket_vector(r["formal_matrix"]); b = total_bucket_vector(m)
        acc["max_total_residual"] = max(acc["max_total_residual"], max(abs(a[i] - b[i]) for i in range(8)))
        acc["max_probability_residual"] = max(acc["max_probability_residual"], abs(sum(float(c["probability"]) for c in m) - 1.0))
    n = acc["count"]
    return {
        "count": n,
        "score_top1": acc["score_top1"],
        "score_top1_rate": acc["score_top1"] / n if n else None,
        "score_top3": acc["score_top3"],
        "score_top3_rate": acc["score_top3"] / n if n else None,
        "score_logloss": acc["score_logloss"] / n if n else None,
        "one_x_two_logloss": acc["one_x_two_logloss"] / n if n else None,
        "one_x_two_brier": acc["one_x_two_brier"] / n if n else None,
        "one_x_two_rps": acc["one_x_two_rps"] / n if n else None,
        "max_total_residual": acc["max_total_residual"],
        "max_probability_residual": acc["max_probability_residual"],
    }


def eligible(candidate, baseline):
    return bool(
        candidate["score_logloss"] <= baseline["score_logloss"]
        and candidate["one_x_two_logloss"] <= baseline["one_x_two_logloss"]
        and candidate["one_x_two_rps"] <= baseline["one_x_two_rps"]
        and candidate["max_total_residual"] <= TOL
        and candidate["max_probability_residual"] <= TOL
    )


def select(train, valid, mode, shot_names, comps):
    formal = aggregate(valid, lambda r: r["formal_matrix"])
    board = []
    for c in CANDIDATE_C:
        models, support = fit_models(train, mode, c, shot_names, comps)
        m = aggregate(valid, lambda r, models=models: adjusted_matrix(r, models, mode, shot_names, comps))
        m["C"] = c; m["modelled_totals"] = sorted(models); m["support"] = support; m["proper_noninferior"] = eligible(m, formal)
        board.append(m)
    ok = [m for m in board if m["proper_noninferior"]]
    chosen = max(ok, key=lambda m: (m["score_top1_rate"], m["score_top3_rate"], -m["score_logloss"], -m["C"])) if ok else None
    return formal, board, chosen


def delta(a, b):
    return {
        "score_top1_rate": a["score_top1_rate"] - b["score_top1_rate"],
        "score_top3_rate": a["score_top3_rate"] - b["score_top3_rate"],
        "score_logloss": a["score_logloss"] - b["score_logloss"],
        "one_x_two_logloss": a["one_x_two_logloss"] - b["one_x_two_logloss"],
        "one_x_two_rps": a["one_x_two_rps"] - b["one_x_two_rps"],
    }


def fold(rows, train_seasons, valid_season, test_season, mode, shot_names, comps):
    train = [r for r in rows if r["season"] in set(train_seasons)]
    valid = [r for r in rows if r["season"] == valid_season]
    test = [r for r in rows if r["season"] == test_season]
    formal_valid, board, chosen = select(train, valid, mode, shot_names, comps)
    out = {"train_seasons": list(train_seasons), "valid_season": valid_season, "test_season": test_season, "rows": {"train": len(train), "valid": len(valid), "test": len(test)}, "validation_formal": formal_valid, "leaderboard": board, "selected": chosen}
    if chosen is None:
        out["status"] = "NO_PROPER_NONINFERIOR_CANDIDATE"
        return out
    models, support = fit_models(train + valid, mode, chosen["C"], shot_names, comps)
    formal_test = aggregate(test, lambda r: r["formal_matrix"])
    cand_test = aggregate(test, lambda r: adjusted_matrix(r, models, mode, shot_names, comps))
    out.update({"status": "PASS", "test_formal": formal_test, "test_candidate": cand_test, "test_delta": delta(cand_test, formal_test), "final_modelled_totals": sorted(models), "final_support": support})
    return out


def main():
    raw, _ = base.raw_stat_matches()
    lookup, _ = fix.lagged_shot_lookup_fixed(raw)
    rows, meta = build_rows(lookup)
    shot_names, comps = shot_names_and_comps(rows)
    designs = [(("2022/23",), "2023/24", "2024/25"), (("2022/23", "2023/24"), "2024/25", "2025/26")]
    results = {mode: [] for mode in ("calibration", "competition", "shot")}
    for tr, va, te in designs:
        for mode in results:
            results[mode].append(fold(rows, tr, va, te, mode, shot_names, comps))

    paired = []
    for i in range(len(designs)):
        c = results["competition"][i]
        s = results["shot"][i]
        item = {"fold": i + 1, "test_season": designs[i][2], "competition_status": c["status"], "shot_status": s["status"]}
        if c.get("test_candidate") and s.get("test_candidate"):
            item["shot_minus_competition"] = delta(s["test_candidate"], c["test_candidate"])
        paired.append(item)

    payload = {
        "schema_version": "V6.18.4-shot-conditional-score-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_CHRONOLOGICAL_OOS_RESEARCH_NOT_PROSPECTIVE",
        "design": {
            "target": "P(H|T=t,X) for exact t=1..6; 0 and 7+ identity",
            "invariant": "formal P(T=0..6,7+) exactly preserved",
            "candidate_C": list(CANDIDATE_C),
            "validation_hard_gate": "score LogLoss, 1X2 LogLoss and 1X2 RPS all non-inferior; total/probability residual <=1e-10",
            "selection_after_gate": "maximize exact score Top1 then Top3",
            "current_match_stats_used": False,
            "market_used": False,
        },
        "rows": len(rows),
        "shot_feature_names": shot_names,
        "competitions": comps,
        "results": results,
        "paired_shot_vs_competition": paired,
        "source_meta": meta,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "no_2025_26_tuning": True,
            "promotion_requires_new_prospective_joint_matrix_evidence": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "paired": paired, "summary": {mode: [{"test": r["test_season"], "status": r["status"], "C": (r.get("selected") or {}).get("C"), "delta": r.get("test_delta")} for r in vals] for mode, vals in results.items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
