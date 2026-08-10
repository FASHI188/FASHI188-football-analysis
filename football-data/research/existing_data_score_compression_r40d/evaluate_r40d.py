#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from datetime import date
from pathlib import Path

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"

SEEDS = {
    "r39u": "R39U_FIXED100_20260810",
    "r39v": "R39V_DISJOINT_FIXED100_20260810",
    "r39w": "R39W_BALANCE_INTENSITY_FIXED100_20260810",
    "r39x": "R39X_NONLINEAR_REGIME_FIXED100_20260810",
    "r39y": "R39Y_REGIME_REPLICATION_FIXED100_20260810",
    "r39z": "R39Z_ORDINAL_BAND_FIXED100_20260810",
    "r40a": "R40A_VARYING_ORDINAL_FIXED100_20260810",
    "r40b": "R40B_TEAM_DRAW_STATE_FIXED100_20260810",
}
EXPECTED_SHA = {
    "r39u": "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6",
    "r39v": "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b",
    "r39w": "df64b2999f447a1b42a69326ca5db3e4132af624d14d147deda06042b6d3f5c8",
    "r39x": "5de03fc4d0e52fda74c10dd8430cbe0a2f3db43fbd97529608222083d70270ba",
    "r39y": "de0c755f6e828a3778d8da9e830bba988ade7ebb347b208f20260872c4fdbc57",
    "r39z": "ffb26e35e1f98196cfb2842a559b0d22380a4b89165889351dfbf6f99aaf61a2",
    "r40a": "64d7c671987dac7bdd92c1eb76370e28c62f9157c9071861af33d59a0f23d7b2",
    "r40b": "f6c2d7ae058d5a9955f0f80fecdf18bbbed519499cd433ead20731e9f0e8500e",
}
LABELS = ("H", "D", "A")


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r40d", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def num(v: str) -> float:
    try:
        x = float(str(v).strip())
    except Exception:
        return math.nan
    return x if math.isfinite(x) else math.nan


def raw_key(raw: dict[str, str]) -> str | None:
    try:
        dt = date.fromisoformat(str(raw.get("date", "")).strip())
    except Exception:
        return None
    comp = str(raw.get("competition_id", "")).strip()
    season = str(raw.get("season", "")).strip()
    home = str(raw.get("home_team", "")).strip()
    away = str(raw.get("away_team", "")).strip()
    if not all((comp, season, home, away)):
        return None
    return f"{comp}|{season}|{dt.isoformat()}|{home}|{away}"


def load_scores(root: Path) -> tuple[dict[str, tuple[float, float, float, float]], dict[str, int]]:
    scores: dict[str, tuple[float, float, float, float]] = {}
    checked = 0
    consistency_fail = 0
    for p in sorted(root.glob("football-data/training_datasets/*/point_in_time.csv")):
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            for raw in rd:
                lab = str(raw.get("label_result", "")).strip().upper()
                if lab not in LABELS:
                    continue
                key = raw_key(raw)
                if key is None:
                    continue
                hg = num(raw.get("label_home_goals", ""))
                ag = num(raw.get("label_away_goals", ""))
                tg = num(raw.get("label_total_goals", ""))
                gd = num(raw.get("label_goal_difference", ""))
                if not all(math.isfinite(v) for v in (hg, ag, tg, gd)):
                    continue
                checked += 1
                if abs((hg + ag) - tg) > 1e-9 or abs((hg - ag) - gd) > 1e-9:
                    consistency_fail += 1
                    continue
                if key in scores:
                    raise RuntimeError(f"DUPLICATE_SCORE_KEY:{key}")
                scores[key] = (hg, ag, tg, abs(gd))
    if consistency_fail:
        raise RuntimeError(f"SCORE_LABEL_CONSISTENCY_FAILURES:{consistency_fail}")
    return scores, {"checked_score_rows": checked, "consistency_failures": consistency_fail}


def eb_mean(history: list[float], window: int, comp_mean: float, alpha: float) -> float:
    sub = history[-window:]
    return (sum(sub) + alpha * comp_mean) / (len(sub) + alpha)


def eb_rate(history: list[bool], window: int, comp_rate: float, alpha: float) -> float:
    sub = history[-window:]
    return (sum(sub) + alpha * comp_rate) / (len(sub) + alpha)


def build_score_state_features(by_comp: dict[str, list], scores: dict[str, tuple[float, float, float, float]], window: int, alpha: float):
    features: dict[str, tuple[float, ...]] = {}
    update_rows = 0
    for comp, rs in sorted(by_comp.items()):
        team_total: dict[str, list[float]] = {}
        team_low2: dict[str, list[bool]] = {}
        team_margin: dict[str, list[float]] = {}
        home_role_low2: dict[str, list[bool]] = {}
        away_role_low2: dict[str, list[bool]] = {}
        comp_n = 0
        comp_total_sum = 0.0
        comp_low2_n = 0
        comp_margin_sum = 0.0
        i = 0
        while i < len(rs):
            d = rs[i].dt
            j = i
            while j < len(rs) and rs[j].dt == d:
                j += 1
            if comp_n > 0:
                c_total = comp_total_sum / comp_n
                c_low2 = comp_low2_n / comp_n
                c_margin = comp_margin_sum / comp_n
                for r in rs[i:j]:
                    if r.key not in scores:
                        continue
                    h_total = eb_mean(team_total.get(r.home, []), window, c_total, alpha)
                    a_total = eb_mean(team_total.get(r.away, []), window, c_total, alpha)
                    h_low2 = eb_rate(team_low2.get(r.home, []), window, c_low2, alpha)
                    a_low2 = eb_rate(team_low2.get(r.away, []), window, c_low2, alpha)
                    h_margin = eb_mean(team_margin.get(r.home, []), window, c_margin, alpha)
                    a_margin = eb_mean(team_margin.get(r.away, []), window, c_margin, alpha)
                    hh_low2 = eb_rate(home_role_low2.get(r.home, []), window, c_low2, alpha)
                    aa_low2 = eb_rate(away_role_low2.get(r.away, []), window, c_low2, alpha)
                    features[r.key] = (h_total, a_total, h_low2, a_low2, h_margin, a_margin, hh_low2, aa_low2)
            # Same-date scores are added only after all feature rows for this date are frozen.
            for r in rs[i:j]:
                s = scores.get(r.key)
                if s is None:
                    continue
                _, _, total, margin = s
                low2 = total <= 2.0
                for team in (r.home, r.away):
                    team_total.setdefault(team, []).append(total)
                    team_low2.setdefault(team, []).append(low2)
                    team_margin.setdefault(team, []).append(margin)
                home_role_low2.setdefault(r.home, []).append(low2)
                away_role_low2.setdefault(r.away, []).append(low2)
                comp_n += 1
                comp_total_sum += total
                comp_low2_n += int(low2)
                comp_margin_sum += margin
                update_rows += 1
            i = j
    return features, update_rows


def verify_sample(u, rows, name: str) -> list[str]:
    keys = [t.key for t, _ in rows]
    got = u.sample_sha(keys)
    want = EXPECTED_SHA[name]
    if got != want:
        raise RuntimeError(f"{name.upper()}_SAMPLE_IDENTITY_DRIFT:{got}:{want}")
    return keys


def combine(p_draw: float, p_home_given_non_draw: float) -> dict[str, float]:
    p = {
        "D": p_draw,
        "H": (1.0 - p_draw) * p_home_given_non_draw,
        "A": (1.0 - p_draw) * (1.0 - p_home_given_non_draw),
    }
    total = sum(p.values())
    if abs(total - 1.0) > 1e-10:
        raise RuntimeError(f"PROBABILITY_CONSERVATION_FAIL:{total}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R40D-EXISTING-DATA-SCORE-COMPRESSION-1.0"
    state = pre["score_state"]
    model = pre["model"]
    window = int(state["window"])
    alpha = float(state["empirical_bayes_prior_strength"])
    assert window == 10 and alpha == 5.0
    assert len(state["features"]) == 8
    assert float(model["draw_l2"]) == 10.0
    assert model["manual_draw_boost"] is False
    assert model["class_weighting"] is False
    assert model["candidate_grid"] is False
    assert model["post_result_tuning"] is False
    assert model["abstention"] is False
    hard = pre["hard_boundaries"]
    assert hard["external_network_requests"] == 0 and hard["football_api_requests"] == 0
    assert hard["new_data_collection"] is False

    w = load_r39w_module()
    u = w.load_r39u_module()
    rows = u.load_rows(args.root)
    scores, score_audit = load_scores(args.root)
    if len(scores) != len(rows):
        missing = [r.key for r in rows if r.key not in scores]
        raise RuntimeError(f"SCORE_COVERAGE_MISMATCH:{len(scores)}:{len(rows)}:{missing[:5]}")

    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = w.build_eligible(u, rows, min_prior)

    excluded: set[str] = set()
    consumed: dict[str, list[str]] = {}
    for name in ("r39u", "r39v", "r39w", "r39x", "r39y", "r39z", "r40a", "r40b"):
        old = w.hashed_sample(eligible, SEEDS[name], 100, excluded)
        keys = verify_sample(u, old, name)
        consumed[name] = keys
        excluded.update(keys)

    target_sample = w.hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(target_sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R40D_SAMPLE")
    sample_keys = [t.key for t, _ in target_sample]
    overlaps = {name: len(set(sample_keys) & set(keys)) for name, keys in consumed.items()}
    if any(overlaps.values()):
        raise RuntimeError(f"R40D_OVERLAP_INVALID:{overlaps}")

    feature_map, score_updates = build_score_state_features(by_comp, scores, window, alpha)
    missing_targets = [t.key for t, _ in target_sample if t.key not in feature_map]
    if missing_targets:
        raise RuntimeError(f"R40D_TARGET_SCORE_STATE_MISSING:{missing_targets[:5]}")

    challenger = []
    baseline = []
    audit = []
    for t, _ in target_sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        train = [r for r in prior if r.key in feature_map]
        if len(train) < 100:
            raise RuntimeError(f"INSUFFICIENT_SCORE_STATE_TRAIN:{t.key}:{len(train)}")

        Xd, xd = w.standardize([feature_map[r.key] for r in train], feature_map[t.key], u.quantile)
        yd = [1 if r.label == "D" else 0 for r in train]
        bdraw, draw_iters = w.fit_logistic(Xd, yd, float(model["draw_l2"]))
        pD_score = w.predict_prob(bdraw, xd)

        nd = [r for r in prior if r.label != "D"]
        Xs, xs = w.standardize([w.side_features(r.x) for r in nd], w.side_features(t.x), u.quantile)
        ys = [1 if r.label == "H" else 0 for r in nd]
        bside, side_iters = w.fit_logistic(Xs, ys, 10.0)
        pHnd = w.predict_prob(bside, xs)
        pc = combine(pD_score, pHnd)
        challenger.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pc),
            "probabilities": {lab: round(pc[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior), "score_state_train_n": len(train),
        })

        Xb, xb = w.standardize([w.draw_features(r.x) for r in prior], w.draw_features(t.x), u.quantile)
        yb = [1 if r.label == "D" else 0 for r in prior]
        bbase, base_draw_iters = w.fit_logistic(Xb, yb, 10.0)
        pD_base = w.predict_prob(bbase, xb)
        pb = combine(pD_base, pHnd)
        baseline.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pb),
            "probabilities": {lab: round(pb[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
        })

        audit.append({
            "key": t.key,
            "score_state_features": {name: value for name, value in zip(state["features"], feature_map[t.key])},
            "score_state_train_n": len(train), "prior_pool_n": len(prior),
            "pD_score_state": pD_score, "pD_baseline": pD_base, "pH_given_non_draw": pHnd,
            "draw_solver_iterations": draw_iters,
            "baseline_draw_solver_iterations": base_draw_iters,
            "side_solver_iterations": side_iters,
            "probability_sum_challenger": sum(pc.values()),
            "probability_sum_baseline": sum(pb.values()),
        })

    cm = u.metrics(challenger)
    bm = u.metrics(baseline)
    ydraw = [r["actual"] == "D" for r in challenger]
    cauc = w.auc_binary([float(r["probabilities"]["D"]) for r in challenger], ydraw)
    bauc = w.auc_binary([float(r["probabilities"]["D"]) for r in baseline], ydraw)
    cf1 = cm["draw_f1"] if cm["draw_f1"] is not None else -1.0
    bf1 = bm["draw_f1"] if bm["draw_f1"] is not None else -1.0
    crec = cm["draw_recall"] if cm["draw_recall"] is not None else -1.0

    gate = {
        "draw_auc_plus_0_02_and_gt_0_56": cauc >= bauc + 0.02 and cauc > 0.56,
        "draw_f1_ge_0_20_and_plus_0_08": cf1 >= 0.20 and cf1 >= bf1 + 0.08,
        "draw_recall_ge_0_15": crec >= 0.15,
        "proper_score_noninferior_within_0_01": cm["log_loss"] <= bm["log_loss"] + 0.01 and cm["brier"] <= bm["brier"] + 0.01,
        "accuracy_noninferior_within_0_02": cm["accuracy"] >= bm["accuracy"] - 0.02,
    }
    passed = all(gate.values())
    terminal = "PASS_R40D_SCORE_COMPRESSION_INCREMENT" if passed else "FAIL_R40D_SCORE_COMPRESSION_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"], "terminal": terminal, "passed": passed,
        "source_rows": len(rows), "eligible_rows": len(eligible),
        "score_audit": {**score_audit, "score_map_rows": len(scores), "score_state_update_rows": score_updates, "score_state_feature_rows": len(feature_map)},
        "consumed_sample_identities": {name: u.sample_sha(keys) for name, keys in consumed.items()},
        "r40d_fixed100_identity_sha256": u.sample_sha(sample_keys), "overlap": overlaps,
        "sample_keys": sample_keys,
        "challenger_metrics": cm, "baseline_metrics": bm,
        "challenger_draw_auc": cauc, "baseline_draw_auc": bauc,
        "gate": gate, "audit": audit,
        "challenger_predictions": challenger, "baseline_predictions": baseline,
        "hard_boundaries": hard,
        "interpretation_boundary": "Retrospective score-history challenger only. Even PASS authorizes only rolling time-ordered evaluation; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r40d_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r40d_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal, "passed": passed, "sample_sha": out["r40d_fixed100_identity_sha256"],
        "overlap": overlaps, "score_audit": out["score_audit"],
        "challenger_metrics": cm, "baseline_metrics": bm,
        "challenger_draw_auc": cauc, "baseline_draw_auc": bauc, "gate": gate,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
