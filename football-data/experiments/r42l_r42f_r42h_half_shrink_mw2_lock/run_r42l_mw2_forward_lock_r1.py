#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
sys.path.insert(0, str(HERE))
import run_r42l_mw2_forward_lock as base  # noqa: E402

r42k = base.r42k
r42h = base.r42h
r42g = base.r42g
r40c = base.r40c

TECH_ALPHA = 0.50


def key_of(target: dict) -> tuple[str, str, str]:
    return base.key_of(target)


def simple_vec(p: dict) -> np.ndarray:
    return np.array([float(p["home"]), float(p["draw"]), float(p["away"])], dtype=float)


def local_vec(p: dict) -> np.ndarray:
    return np.array([float(p["p_home"]), float(p["p_draw"]), float(p["p_away"])], dtype=float)


def simple_prob(v: np.ndarray) -> dict:
    v = np.asarray(v, dtype=float)
    if v.shape != (3,) or not np.all(np.isfinite(v)) or np.any(v <= 0):
        raise RuntimeError(f"invalid probability vector: {v}")
    v = v / float(v.sum())
    i = int(np.argmax(v))
    return {
        "home": float(v[0]),
        "draw": float(v[1]),
        "away": float(v[2]),
        "top1": ["home", "draw", "away"][i],
    }


def transport_fixed_log_ratio(anchor: dict, local_baseline: dict, local_full: dict, alpha: float) -> tuple[dict, dict]:
    """Apply the frozen R42H technical likelihood-ratio correction to the already locked R42E anchor.

    R42K's geometric half-shrink is equivalent to adding alpha times the log-probability
    ratio log(p_full/p_baseline), followed by normalization. R42E is an independently
    locked current-season bridge baseline and is not numerically identical to R42H's
    historical bridge baseline. Therefore the pre-label integration rule is to transport
    only the technical log-ratio onto R42E, rather than replace or refit the R42E anchor.
    """
    a = simple_vec(anchor)
    b = local_vec(local_baseline)
    f = local_vec(local_full)
    eps = 1e-15
    if np.any(a <= eps) or np.any(b <= eps) or np.any(f <= eps):
        raise RuntimeError(f"non-positive probability in transport anchor={a} baseline={b} full={f}")
    log_ratio = np.log(f) - np.log(b)
    z = np.log(a) + float(alpha) * log_ratio
    z = z - float(np.max(z))
    v = np.exp(z)
    v = v / float(v.sum())
    result = simple_prob(v)
    diagnostics = {
        "technical_log_ratio_full_vs_local_baseline": {
            "home": float(log_ratio[0]),
            "draw": float(log_ratio[1]),
            "away": float(log_ratio[2]),
        },
        "alpha_scaled_log_ratio": {
            "home": float(alpha * log_ratio[0]),
            "draw": float(alpha * log_ratio[1]),
            "away": float(alpha * log_ratio[2]),
        },
    }
    return result, diagnostics


def run():
    if abs(float(r42k.TECH_DELTA_ALPHA) - TECH_ALPHA) > 1e-12:
        raise RuntimeError(f"R42K alpha drift: {r42k.TECH_DELTA_ALPHA}")

    r42f = json.loads(base.R42F_SUMMARY.read_text(encoding="utf-8"))
    r42k_summary = json.loads(base.R42K_SUMMARY.read_text(encoding="utf-8"))
    if not r42f["gate"]["passed"]:
        raise RuntimeError("R42F lineup bridge gate is not passed")
    if not r42k_summary["gate"]["passed"]:
        raise RuntimeError("R42K half-shrink confirmation gate is not passed")
    if abs(float(r42k_summary["governance"]["technical_delta_alpha"]) - TECH_ALPHA) > 1e-12:
        raise RuntimeError("R42K summary alpha mismatch")

    r42d = base.fetch_json(base.R42D_URL, base.R42D_BLOB_SHA, "football3-r42l-r1-r42d-freeze")
    r42e = base.fetch_json(base.R42E_URL, base.R42E_BLOB_SHA, "football3-r42l-r1-r42e-lock")
    if r42d["status"] != "COMPLETE" or r42d["target_count"] != 3:
        raise RuntimeError("R42D frozen aggregate is not the expected three-target prematch snapshot")
    if r42e["status"] != "LOCKED_PREMATCH":
        raise RuntimeError("R42E source is not a prematch lock")
    if r42e["source"]["r42d_generated_head"] != base.R42D_COMMIT:
        raise RuntimeError("R42E does not point to the frozen R42D head")

    engine = base.replay_and_fit()
    r42e_by_key = {key_of(x["target"]): x for x in r42e["targets"]}
    lock_time = datetime.now(timezone.utc)
    outputs = []

    for target_summary in r42d["targets"]:
        target = target_summary["target"]
        k = key_of(target)
        if k not in r42e_by_key:
            raise RuntimeError(f"R42D target missing from R42E lock: {k}")
        e_lock = r42e_by_key[k]
        kickoff = datetime.fromisoformat(str(target["kickoff_at_utc"]).replace("Z", "+00:00"))
        if not lock_time < kickoff:
            raise RuntimeError(f"cannot create prospective lock after kickoff: target={k} lock={lock_time.isoformat()}")

        home_ids, home_roles = base.starter_ids_and_roles(target_summary, "home")
        away_ids, away_roles = base.starter_ids_and_roles(target_summary, "away")
        home_final, home_changes = base.apply_confirmed_availability(target_summary, "home", home_ids)
        away_final, away_changes = base.apply_confirmed_availability(target_summary, "away", away_ids)

        hs = base.side_spec(home_final, home_roles)
        a_s = base.side_spec(away_final, away_roles)
        hbase = r42g.snapshot_values(hs, engine["base_ledger"])
        abase = r42g.snapshot_values(a_s, engine["base_ledger"])
        htech = r42h.freeze_tech_snapshot(hs, engine["tech_ledger"])
        atech = r42h.freeze_tech_snapshot(a_s, engine["tech_ledger"])
        bridge_cf = r42g.context_from_pids(home_final, away_final, hbase, abase)
        tech_cf = r42h.technical_context(home_final, away_final, htech, atech)

        home_tid = str(target_summary["current_season_bridge"]["resolution"]["home"]["team_id"])
        away_tid = str(target_summary["current_season_bridge"]["resolution"]["away"]["team_id"])
        target_row = {
            "date": str(target["kickoff_at_utc"])[:10],
            "game_id": f"R42L-R1::{target['home_team']}::{target['away_team']}::{target['kickoff_at_utc']}",
            "competition_id": "1",
            "home_team": home_tid,
            "away_team": away_tid,
        }
        raw = engine["base"].pred(target_row)
        local_baseline = r42h.model_prob(
            engine["baseline_model"], raw, bridge_cf, r42h.BASE_NAMES
        )
        local_full = r42h.model_prob(
            engine["technical_model"], raw, {**bridge_cf, **tech_cf}, r42h.BASE_NAMES + r42h.TECH_NAMES
        )
        local_half = r42k.blend_prob(local_baseline, local_full, TECH_ALPHA)

        locked_anchor = e_lock["R42E_primary_current_season_bridge_plus_confirmed_availability"]
        transported, transport_diag = transport_fixed_log_ratio(
            locked_anchor, local_baseline, local_full, TECH_ALPHA
        )
        local_baseline_simple = base.to_simple(local_baseline)
        local_full_simple = base.to_simple(local_full)
        local_half_simple = base.to_simple(local_half)
        compatibility_diff = base.max_prob_diff(local_baseline_simple, locked_anchor)

        outputs.append(
            {
                "target": target,
                "team_ids": {"home": home_tid, "away": away_tid},
                "frozen_current_season_bridge_xi": {
                    "home_before_confirmed_availability": home_ids,
                    "away_before_confirmed_availability": away_ids,
                    "home_after_confirmed_availability": home_final,
                    "away_after_confirmed_availability": away_final,
                    "home_changes": home_changes,
                    "away_changes": away_changes,
                },
                "technical_coverage": {
                    "tech_known_share_min": float(tech_cf["tech_known_share_min"]),
                },
                "probabilities": {
                    "R42E_locked_primary_anchor": locked_anchor,
                    "R42H_local_bridge_baseline_diagnostic": local_baseline_simple,
                    "R42H_local_full_technical_diagnostic": local_full_simple,
                    "R42K_local_half_shrink_diagnostic": local_half_simple,
                    "R42L_R1_primary_locked_anchor_plus_transported_half_technical": transported,
                },
                "integration": {
                    "rule": "normalize(R42E_anchor * exp(alpha * (log(R42H_full) - log(R42H_local_baseline))))",
                    "alpha": TECH_ALPHA,
                    "local_baseline_vs_R42E_max_abs_diff": float(compatibility_diff),
                    **transport_diag,
                    "primary_probability_delta_vs_R42E_anchor": {
                        q: float(transported[q] - float(locked_anchor[q])) for q in ("home", "draw", "away")
                    },
                },
                "actual_result": None,
                "actual_score": None,
                "result_label_accessed": False,
            }
        )

    result = {
        "schema_version": "football3-r42l-r1-r42f-r42h-half-shrink-mw2-prospective-lock-v1",
        "status": "LOCKED_PREMATCH",
        "classification": "TRUE_FORWARD_THREE_MATCH_LOCK_R42E_ANCHOR_PLUS_R42H_TECH_FIXED_HALF_LOGRATIO",
        "formal_weight": 0,
        "locked_at_utc": lock_time.isoformat(),
        "question": "Does the already locked R42E current-season lineup/availability anchor improve when the frozen R42H V1 technical log-probability ratio is transported at the pre-fixed R42K alpha=0.5 on the same three true-forward MW2 targets?",
        "governance": {
            "target_count": 3,
            "target_results_accessed": False,
            "target_confirmed_xi_accessed": False,
            "target_postmatch_data_accessed": False,
            "r42d_prematch_snapshot_reused_exact": True,
            "r42e_existing_lock_reused_exact": True,
            "r42f_lineup_bridge_gate_passed": True,
            "r42h_v1_features_and_model_unchanged": True,
            "r42k_half_shrink_gate_passed": True,
            "technical_delta_alpha": TECH_ALPHA,
            "alpha_parameter_search_after_r42k": False,
            "probability_retuning_on_mw2_targets": False,
            "integration_parameter_search": False,
            "integration_rule_frozen_after_prelabel_compatibility_failure": True,
            "prelabel_compatibility_failure_run_id": 33096024886,
            "prelabel_compatibility_failure_reason": "R42H local bridge baseline is a different probability layer from independently locked R42E current-season bridge anchor; first target max abs difference was 0.00784603245628021.",
            "transport_interpretation": "R42K geometric shrink transports half of the R42H technical log-probability ratio onto the untouched R42E locked anchor.",
            "doubtful_primary_treatment": "DO_NOT_EXCLUDE",
            "confirmed_availability_statuses_applied": ["out", "suspended"],
            "reveal_policy": "append_only_after_results; never rewrite this lock",
        },
        "frozen_sources": {
            "r42d_commit": base.R42D_COMMIT,
            "r42d_summary_git_blob_sha": base.R42D_BLOB_SHA,
            "r42e_commit": base.R42E_COMMIT,
            "r42e_lock_git_blob_sha": base.R42E_BLOB_SHA,
            "r42f_summary_git_blob_sha": "0afd979daf3fe3f1596fede6dda3a5f096ab6a8f",
            "r42k_summary_git_blob_sha": "36de17feb7d3693810d770fd749802af3f5990de",
        },
        "engine": {
            "history_rows": len(engine["rows"]),
            "history_end_date": engine["history_end_date"],
            "model_train_rows": engine["train_rows"],
            "model_train_end_date": engine["train_end_date"],
            "fixture_players_sha256": engine["player_sha"],
            "matched_starter_rows": engine["matched_starters"],
            "technical_source": engine["tech_source"],
        },
        "targets": outputs,
        "next_gate": {
            "action": "WAIT_FOR_RESULTS_THEN_APPEND_ONLY_SCORE_R42E_ANCHOR_VS_R42L_R1_TRANSPORTED_HALF_TECH",
            "primary_comparison": "R42L_R1_primary_locked_anchor_plus_transported_half_technical versus R42E_locked_primary_anchor",
            "metrics": ["Top1 accuracy", "Log Loss", "Brier", "RPS"],
            "no_tuning_from_three_matches": True,
        },
        "limitations": [
            "This lock contains only three true-forward targets, so it can falsify obvious failures but cannot establish statistical significance.",
            "The R42H local baseline and R42E current-season bridge baseline are not the same probability layer; only the technical log-ratio is transported.",
            "Player technical values are frozen from the historical source ending before the target fixtures; current-season prior-match results/xG are not used to update player strength.",
            "Current-season opener XI membership and confirmed prematch availability come from the already frozen R42D/R42E evidence line.",
            "The fixed alpha=0.5 rule was selected before these targets and is unchanged from R42K.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "r42l_r1_mw2_half_technical_transport_lock.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def verify():
    p = OUT / "r42l_r1_mw2_half_technical_transport_lock.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "LOCKED_PREMATCH"
    assert d["classification"] == "TRUE_FORWARD_THREE_MATCH_LOCK_R42E_ANCHOR_PLUS_R42H_TECH_FIXED_HALF_LOGRATIO"
    assert d["formal_weight"] == 0
    g = d["governance"]
    assert g["target_count"] == 3
    assert g["target_results_accessed"] is False
    assert g["target_confirmed_xi_accessed"] is False
    assert g["target_postmatch_data_accessed"] is False
    assert g["r42f_lineup_bridge_gate_passed"] is True
    assert g["r42k_half_shrink_gate_passed"] is True
    assert g["technical_delta_alpha"] == 0.5
    assert g["alpha_parameter_search_after_r42k"] is False
    assert g["probability_retuning_on_mw2_targets"] is False
    assert g["integration_parameter_search"] is False
    assert len(d["targets"]) == 3
    for x in d["targets"]:
        assert x["actual_result"] is None and x["actual_score"] is None
        assert x["result_label_accessed"] is False
        assert math.isfinite(float(x["integration"]["local_baseline_vs_R42E_max_abs_diff"]))
        for name in (
            "R42E_locked_primary_anchor",
            "R42H_local_bridge_baseline_diagnostic",
            "R42H_local_full_technical_diagnostic",
            "R42K_local_half_shrink_diagnostic",
            "R42L_R1_primary_locked_anchor_plus_transported_half_technical",
        ):
            p = x["probabilities"][name]
            assert abs(float(p["home"]) + float(p["draw"]) + float(p["away"]) - 1.0) < 1e-10
            assert min(float(p["home"]), float(p["draw"]), float(p["away"])) > 0.0
    print("R42L_R1_MW2_HALF_TECH_TRANSPORT_LOCK_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42l_mw2_forward_lock_r1.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
