#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
TRIAL_DIR = ROOT / "football-data" / "experiments" / "trial7_r42k_pseudoprematch_20260828"
sys.path.insert(0, str(TRIAL_DIR))
import run_trial7_r42k as t7  # noqa: E402

RAW_NAMES = [
    "log_p_home_over_away", "log_p_draw_over_away", "mu_total", "mu_total_sq", "mu_gap", "mu_gap_sq",
    "log1p_home_history", "log1p_away_history", "log1p_comp_history", "p_draw_x_total", "p_draw_x_gap",
    "xg_mu_home", "xg_mu_away", "xg_mu_total", "xg_mu_diff", "xg_total_minus_mu_total",
    "xg_diff_minus_mu_diff", "xg_for_diff", "xg_against_diff", "log1p_xg_weight_min",
]


def model_vector(h, raw, cf, names):
    return np.asarray(list(h.r9.feat_k1(raw)) + [float(cf[n]) for n in names], dtype=float)


def probs(model, x):
    p = model.predict_proba([x])[0]
    out = np.zeros(3, dtype=float)
    for cls, v in zip(model[-1].classes_, p):
        out[int(cls)] = float(v)
    return out / out.sum()


def gap_terms(model, x, names, hi_cls=2, lo_cls=0, topn=12):
    scaler = model[0]
    lr = model[-1]
    z = scaler.transform([x])[0]
    classes = list(lr.classes_)
    ih = classes.index(hi_cls)
    il = classes.index(lo_cls)
    coef = lr.coef_[ih] - lr.coef_[il]
    vals = coef * z
    rows = []
    for n, raw_z, c, v in zip(names, z, coef, vals):
        rows.append({"feature": n, "z": float(raw_z), "coef_gap": float(c), "away_minus_home_logit_term": float(v)})
    rows.sort(key=lambda r: abs(r["away_minus_home_logit_term"]), reverse=True)
    intercept = float(lr.intercept_[ih] - lr.intercept_[il])
    return intercept, rows[:topn]


def raw_prob(raw):
    return np.asarray([raw["p_home"], raw["p_draw"], raw["p_away"]], dtype=float)


def decorate(v):
    labs = ["home", "draw", "away"]
    v = np.asarray(v, dtype=float); v = v / v.sum()
    return {"home": float(v[0]), "draw": float(v[1]), "away": float(v[2]), "top1": labs[int(np.argmax(v))]}


def main():
    h, runner_sha = t7.import_r42h()
    base, states, base_ledger, tech_ledger, baseline_model, technical_model, meta = t7.replay_and_fit(h)
    out = {
        "schema_version": "football3-trial7-posthoc-feature-attribution-v1",
        "status": "COMPLETE",
        "classification": "POSTHOC_DIAGNOSTIC_ONLY_NO_MODEL_CHANGE",
        "governance": {
            "locked_prediction_file_unchanged": True,
            "target_results_used_in_feature_replay": False,
            "target_confirmed_xi_used": False,
            "target_postmatch_stats_used": False,
            "parameter_tuning": False,
            "purpose": "attribute the already-locked prematch logits; do not promote or retune from these seven matches",
        },
        "runner_sha": runner_sha,
        "source": meta,
        "targets": [],
    }

    for i, m in enumerate(t7.TARGETS, 1):
        row = {
            "date": m["date"], "game_id": f"trial7_diag_{i}", "competition_id": m["competition_id"],
            "home_team": m["home_team"], "away_team": m["away_team"],
        }
        raw = base.pred(row)
        bcf = h.r40c.context_features(row, states, base_ledger)
        tcf = h.live_technical_context(row, states, tech_ledger, base_ledger)
        fullcf = {**bcf, **tcf}

        xbase = model_vector(h, raw, bcf, h.BASE_NAMES)
        xfull = model_vector(h, raw, fullcf, h.BASE_NAMES + h.TECH_NAMES)
        p0 = raw_prob(raw)
        pb = probs(baseline_model, xbase)
        pf = probs(technical_model, xfull)
        vh = np.exp(0.5 * np.log(np.clip(pb, 1e-15, 1.0)) + 0.5 * np.log(np.clip(pf, 1e-15, 1.0)))
        vh /= vh.sum()

        int_b, terms_b = gap_terms(baseline_model, xbase, RAW_NAMES + list(h.BASE_NAMES))
        int_f, terms_f = gap_terms(technical_model, xfull, RAW_NAMES + list(h.BASE_NAMES) + list(h.TECH_NAMES))
        out["targets"].append({
            "ticket_code": m["ticket_code"], "home": m["home"], "away": m["away"],
            "raw_poisson": decorate(p0), "baseline_r40c": decorate(pb), "full_r42h": decorate(pf), "r42k_half": decorate(vh),
            "raw_state": {
                "mu_home": float(raw["mu_home"]), "mu_away": float(raw["mu_away"]), "mu_total": float(raw["mu_total"]),
                "xg_mu_home": float(raw["xg_mu_home"]), "xg_mu_away": float(raw["xg_mu_away"]), "xg_mu_total": float(raw["xg_mu_total"]),
                "home_history": int(raw["home_history"]), "away_history": int(raw["away_history"]), "comp_history": int(raw["comp_history"]),
            },
            "baseline_away_minus_home_intercept": int_b,
            "baseline_top_away_vs_home_terms": terms_b,
            "full_away_minus_home_intercept": int_f,
            "full_top_away_vs_home_terms": terms_f,
            "lineup_context": {
                "home_xi_certainty": float(bcf.get("home_xi_certainty", 0.0)),
                "away_xi_certainty": float(bcf.get("away_xi_certainty", 0.0)),
                "home_role_known_share": float(bcf.get("home_role_known_share", 0.0)),
                "away_role_known_share": float(bcf.get("away_role_known_share", 0.0)),
                "tech_known_share_min": float(tcf.get("tech_known_share_min", 0.0)),
            },
        })

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
