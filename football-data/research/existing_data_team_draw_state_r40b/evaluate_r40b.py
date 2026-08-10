#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
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
}
EXPECTED_SHA = {
    "r39u": "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6",
    "r39v": "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b",
    "r39w": "df64b2999f447a1b42a69326ca5db3e4132af624d14d147deda06042b6d3f5c8",
    "r39x": "5de03fc4d0e52fda74c10dd8430cbe0a2f3db43fbd97529608222083d70270ba",
    "r39y": "de0c755f6e828a3778d8da9e830bba988ade7ebb347b208f20260872c4fdbc57",
    "r39z": "ffb26e35e1f98196cfb2842a559b0d22380a4b89165889351dfbf6f99aaf61a2",
    "r40a": "64d7c671987dac7bdd92c1eb76370e28c62f9157c9071861af33d59a0f23d7b2",
}
LABELS = ("H", "D", "A")


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r40b", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def eb_rate(history: list[bool], window: int, competition_prior: float, alpha: float) -> float:
    sub = history[-window:]
    return (sum(sub) + alpha * competition_prior) / (len(sub) + alpha)


def build_team_state_features(by_comp: dict[str, list], alpha: float) -> dict[str, tuple[float, ...]]:
    features: dict[str, tuple[float, ...]] = {}
    for comp, rs in sorted(by_comp.items()):
        overall: dict[str, list[bool]] = {}
        home_role: dict[str, list[bool]] = {}
        away_role: dict[str, list[bool]] = {}
        comp_n = 0
        comp_draws = 0
        i = 0
        while i < len(rs):
            d = rs[i].dt
            j = i
            while j < len(rs) and rs[j].dt == d:
                j += 1
            if comp_n > 0:
                comp_rate = comp_draws / comp_n
                for r in rs[i:j]:
                    h_hist = overall.get(r.home, [])
                    a_hist = overall.get(r.away, [])
                    hh_hist = home_role.get(r.home, [])
                    aa_hist = away_role.get(r.away, [])
                    h20 = eb_rate(h_hist, 20, comp_rate, alpha)
                    a20 = eb_rate(a_hist, 20, comp_rate, alpha)
                    h5 = eb_rate(h_hist, 5, comp_rate, alpha)
                    a5 = eb_rate(a_hist, 5, comp_rate, alpha)
                    hh10 = eb_rate(hh_hist, 10, comp_rate, alpha)
                    aa10 = eb_rate(aa_hist, 10, comp_rate, alpha)
                    features[r.key] = (
                        h20, a20, h5, a5, hh10, aa10,
                        h20 * a20,
                        hh10 * aa10,
                    )
            # Same-date outcomes become available only after every row on the date was featurized.
            for r in rs[i:j]:
                is_draw = r.label == "D"
                overall.setdefault(r.home, []).append(is_draw)
                overall.setdefault(r.away, []).append(is_draw)
                home_role.setdefault(r.home, []).append(is_draw)
                away_role.setdefault(r.away, []).append(is_draw)
                comp_n += 1
                comp_draws += int(is_draw)
            i = j
    return features


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
    assert pre["schema_version"] == "R40B-EXISTING-DATA-TEAM-DRAW-STATE-1.0"
    state = pre["team_state"]
    model = pre["model"]
    alpha = float(state["empirical_bayes_prior_strength"])
    assert alpha == 5.0
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
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = w.build_eligible(u, rows, min_prior)

    excluded: set[str] = set()
    consumed: dict[str, list[str]] = {}
    for name in ("r39u", "r39v", "r39w", "r39x", "r39y", "r39z", "r40a"):
        old = w.hashed_sample(eligible, SEEDS[name], 100, excluded)
        keys = verify_sample(u, old, name)
        consumed[name] = keys
        excluded.update(keys)

    target_sample = w.hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(target_sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R40B_SAMPLE")
    sample_keys = [t.key for t, _ in target_sample]
    overlaps = {name: len(set(sample_keys) & set(keys)) for name, keys in consumed.items()}
    if any(overlaps.values()):
        raise RuntimeError(f"R40B_OVERLAP_INVALID:{overlaps}")

    feature_map = build_team_state_features(by_comp, alpha)
    missing_targets = [t.key for t, _ in target_sample if t.key not in feature_map]
    if missing_targets:
        raise RuntimeError(f"R40B_TARGET_TEAM_STATE_MISSING:{missing_targets[:5]}")

    challenger = []
    baseline = []
    audit = []
    for t, _ in target_sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        train = [r for r in prior if r.key in feature_map]
        if len(train) < 100:
            raise RuntimeError(f"INSUFFICIENT_TEAM_STATE_TRAIN:{t.key}:{len(train)}")

        train_raw = [feature_map[r.key] for r in train]
        target_raw = feature_map[t.key]
        Xd, xd = w.standardize(train_raw, target_raw, u.quantile)
        yd = [1 if r.label == "D" else 0 for r in train]
        bdraw, draw_iters = w.fit_logistic(Xd, yd, float(model["draw_l2"]))
        pD_team = w.predict_prob(bdraw, xd)

        nd = [r for r in prior if r.label != "D"]
        Xs, xs = w.standardize([w.side_features(r.x) for r in nd], w.side_features(t.x), u.quantile)
        ys = [1 if r.label == "H" else 0 for r in nd]
        bside, side_iters = w.fit_logistic(Xs, ys, 10.0)
        pHnd = w.predict_prob(bside, xs)
        pc = combine(pD_team, pHnd)
        challenger.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pc),
            "probabilities": {lab: round(pc[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
            "team_state_train_n": len(train),
        })

        # Exact R39W baseline on the same target.
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
            "team_state_features": {name: value for name, value in zip(state["features"], target_raw)},
            "team_state_train_n": len(train),
            "prior_pool_n": len(prior),
            "pD_team_state": pD_team,
            "pD_baseline": pD_base,
            "pH_given_non_draw": pHnd,
            "draw_solver_iterations": draw_iters,
            "baseline_draw_solver_iterations": base_draw_iters,
            "side_solver_iterations": side_iters,
            "probability_sum_challenger": sum(pc.values()),
            "probability_sum_baseline": sum(pb.values()),
        })

    cm = u.metrics(challenger)
    bm = u.metrics(baseline)
    labels = [r["actual"] == "D" for r in challenger]
    cauc = w.auc_binary([float(r["probabilities"]["D"]) for r in challenger], labels)
    bauc = w.auc_binary([float(r["probabilities"]["D"]) for r in baseline], labels)
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
    terminal = "PASS_R40B_TEAM_DRAW_STATE_INCREMENT" if passed else "FAIL_R40B_TEAM_DRAW_STATE_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "team_state_feature_rows": len(feature_map),
        "consumed_sample_identities": {name: u.sample_sha(keys) for name, keys in consumed.items()},
        "r40b_fixed100_identity_sha256": u.sample_sha(sample_keys),
        "overlap": overlaps,
        "sample_keys": sample_keys,
        "challenger_metrics": cm,
        "baseline_metrics": bm,
        "challenger_draw_auc": cauc,
        "baseline_draw_auc": bauc,
        "gate": gate,
        "audit": audit,
        "challenger_predictions": challenger,
        "baseline_predictions": baseline,
        "hard_boundaries": hard,
        "interpretation_boundary": "Retrospective team-state challenger only. Even PASS authorizes only rolling time-ordered evaluation; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r40b_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r40b_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal, "passed": passed,
        "sample_sha": out["r40b_fixed100_identity_sha256"], "overlap": overlaps,
        "challenger_metrics": cm, "baseline_metrics": bm,
        "challenger_draw_auc": cauc, "baseline_draw_auc": bauc,
        "gate": gate,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
