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
ROOT = HERE.parents[2]
R42L_DIR = ROOT / "football-data" / "experiments" / "r42l_r42f_r42h_half_shrink_mw2_lock"
sys.path.insert(0, str(R42L_DIR))
import run_r42l_mw2_forward_lock_r1 as l1  # noqa: E402

base = l1.base
r42h = l1.r42h
r42g = l1.r42g
R42L_LOCK = R42L_DIR / "results" / "r42l_r1_mw2_half_technical_transport_lock.json"
EXPECTED_R42L_LOCK_BLOB_SHA = "615277fa8728c2cb7d42c48190348d38eb0955c6"
SOURCE_R42L_EVIDENCE_HEAD = "aa05776c2a354ad6c85f53a7a6e39f7035b4d86a"
SOURCE_R43G1_RESULT_BLOB_SHA = "589983c9d14d763a94ee89ee2a029c249d667c7b"
SOURCE_R43G2_RESULT_BLOB_SHA = "a4ce570836eaa6b9273cc00b48db70b45f1241e9"
TECH_ALPHA = 0.50
REQUIRED_REL = (
    "home_xi_certainty", "away_xi_certainty",
    "home_role_known_share", "away_role_known_share",
)


def vec_simple(p: dict) -> np.ndarray:
    return np.asarray([float(p["home"]), float(p["draw"]), float(p["away"])], dtype=float)


def vec_local(p: dict) -> np.ndarray:
    return np.asarray([float(p["p_home"]), float(p["p_draw"]), float(p["p_away"])], dtype=float)


def simple(v: np.ndarray) -> dict:
    v = np.asarray(v, dtype=float)
    if v.shape != (3,) or not np.all(np.isfinite(v)) or np.any(v <= 0):
        raise RuntimeError(f"invalid probability vector {v}")
    v = v / float(v.sum())
    i = int(np.argmax(v))
    return {"home": float(v[0]), "draw": float(v[1]), "away": float(v[2]), "top1": ["home", "draw", "away"][i]}


def reliability(bridge_cf: dict, tech_cf: dict) -> tuple[float, float, dict]:
    missing = [k for k in REQUIRED_REL if k not in bridge_cf]
    if missing:
        raise RuntimeError(f"R43G3 cannot create lock: bridge context lacks frozen R43G1 reliability fields {missing}; keys={sorted(bridge_cf)}")
    vals = []
    for k in REQUIRED_REL:
        x = float(bridge_cf[k])
        if not np.isfinite(x):
            x = 0.0
        vals.append(min(1.0, max(0.0, x)))
    r_ctx = float(min(vals))
    tk = float(tech_cf.get("tech_known_share_min", 0.0))
    tk = min(1.0, max(0.0, tk)) if np.isfinite(tk) else 0.0
    r_tech = float(min(r_ctx, tk))
    diag = {k: float(bridge_cf[k]) for k in REQUIRED_REL}
    diag["tech_known_share_min"] = tk
    return r_ctx, r_tech, diag


def transport(raw: dict, anchor: dict, local_baseline: dict, local_full: dict, r_ctx: float, r_tech: float) -> tuple[dict, dict]:
    pr = vec_local(raw)
    pa = vec_simple(anchor)
    pb = vec_local(local_baseline)
    pf = vec_local(local_full)
    eps = 1e-15
    if any(np.any(v <= eps) for v in (pr, pa, pb, pf)):
        raise RuntimeError("non-positive probability in forward transport")
    ctx_lr = np.log(pa) - np.log(pr)
    tech_lr = np.log(pf) - np.log(pb)
    z = np.log(pr) + float(r_ctx) * ctx_lr + TECH_ALPHA * float(r_tech) * tech_lr
    z -= float(np.max(z))
    q = np.exp(z); q /= float(q.sum())
    return simple(q), {
        "context_log_ratio_R42E_anchor_vs_raw": {k: float(v) for k, v in zip(("home", "draw", "away"), ctx_lr)},
        "technical_log_ratio_R42H_full_vs_local_baseline": {k: float(v) for k, v in zip(("home", "draw", "away"), tech_lr)},
        "r_ctx_scaled_context_log_ratio": {k: float(r_ctx * v) for k, v in zip(("home", "draw", "away"), ctx_lr)},
        "alpha_rtech_scaled_technical_log_ratio": {k: float(TECH_ALPHA * r_tech * v) for k, v in zip(("home", "draw", "away"), tech_lr)},
    }


def run() -> dict:
    locked = json.loads(R42L_LOCK.read_text(encoding="utf-8"))
    if locked["status"] != "LOCKED_PREMATCH" or len(locked["targets"]) != 3:
        raise RuntimeError("unexpected R42L source lock")
    by_key = {base.key_of(x["target"]): x for x in locked["targets"]}

    r42d = base.fetch_json(base.R42D_URL, base.R42D_BLOB_SHA, "football3-r43g3-r42d-freeze")
    engine = base.replay_and_fit()
    lock_time = datetime.now(timezone.utc)
    outputs = []

    for ts in r42d["targets"]:
        target = ts["target"]
        key = base.key_of(target)
        src = by_key[key]
        kickoff = datetime.fromisoformat(str(target["kickoff_at_utc"]).replace("Z", "+00:00"))
        if not lock_time < kickoff:
            raise RuntimeError(f"cannot create prospective R43G3 lock after kickoff target={key} lock={lock_time.isoformat()}")

        home_ids, home_roles = base.starter_ids_and_roles(ts, "home")
        away_ids, away_roles = base.starter_ids_and_roles(ts, "away")
        home_final, home_changes = base.apply_confirmed_availability(ts, "home", home_ids)
        away_final, away_changes = base.apply_confirmed_availability(ts, "away", away_ids)
        hs, aus = base.side_spec(home_final, home_roles), base.side_spec(away_final, away_roles)
        hb = r42g.snapshot_values(hs, engine["base_ledger"])
        ab = r42g.snapshot_values(aus, engine["base_ledger"])
        ht = r42h.freeze_tech_snapshot(hs, engine["tech_ledger"])
        at = r42h.freeze_tech_snapshot(aus, engine["tech_ledger"])
        bridge_cf = r42g.context_from_pids(home_final, away_final, hb, ab)
        tech_cf = r42h.technical_context(home_final, away_final, ht, at)
        r_ctx, r_tech, rel_diag = reliability(bridge_cf, tech_cf)

        home_tid = str(ts["current_season_bridge"]["resolution"]["home"]["team_id"])
        away_tid = str(ts["current_season_bridge"]["resolution"]["away"]["team_id"])
        row = {
            "date": str(target["kickoff_at_utc"])[:10],
            "game_id": f"R43G3::{target['home_team']}::{target['away_team']}::{target['kickoff_at_utc']}",
            "competition_id": "1", "home_team": home_tid, "away_team": away_tid,
        }
        raw = engine["base"].pred(row)
        local_baseline = r42h.model_prob(engine["baseline_model"], raw, bridge_cf, r42h.BASE_NAMES)
        local_full = r42h.model_prob(engine["technical_model"], raw, {**bridge_cf, **tech_cf}, r42h.BASE_NAMES + r42h.TECH_NAMES)
        anchor = src["probabilities"]["R42E_locked_primary_anchor"]
        candidate, tdiag = transport(raw, anchor, local_baseline, local_full, r_ctx, r_tech)
        old_r42l = src["probabilities"]["R42L_R1_primary_locked_anchor_plus_transported_half_technical"]

        outputs.append({
            "target": target,
            "team_ids": {"home": home_tid, "away": away_tid},
            "frozen_current_season_bridge_xi": {
                "home_after_confirmed_availability": home_final,
                "away_after_confirmed_availability": away_final,
                "home_changes": home_changes, "away_changes": away_changes,
            },
            "reliability": {"r_ctx": r_ctx, "r_tech": r_tech, **rel_diag},
            "probabilities": {
                "raw_R9_generator": base.to_simple(raw),
                "R42E_locked_anchor": anchor,
                "R42H_local_bridge_baseline_diagnostic": base.to_simple(local_baseline),
                "R42H_local_full_technical_diagnostic": base.to_simple(local_full),
                "R42L_existing_immutable_reference": old_r42l,
                "R43G3_forward_reliability_transport_candidate": candidate,
            },
            "integration": {
                "rule": "normalize(exp(log(P_raw)+r_ctx*(log(P_R42E_anchor)-log(P_raw))+0.5*r_tech*(log(P_R42H_full_local)-log(P_R42H_baseline_local))))",
                "historical_R43G1_formula_changed": False,
                "forward_parent_transport_required": True,
                "transport_reason": "R42E is the independently locked current-season context parent while R42H technical residual is defined on its local historical bridge parent",
                "tech_alpha": TECH_ALPHA,
                **tdiag,
                "candidate_delta_vs_existing_R42L": {k: float(candidate[k] - float(old_r42l[k])) for k in ("home", "draw", "away")},
            },
            "actual_result": None, "actual_score": None, "result_label_accessed": False,
        })

    result = {
        "schema_version": "football3-r43g3-forward-coverage-reliability-transport-lock-v1",
        "status": "LOCKED_PREMATCH",
        "classification": "TRUE_FORWARD_R43G1_RELIABILITY_FORMULA_TRANSPORT_ON_EXISTING_R42L_MW2_TARGETS",
        "formal_weight": 0,
        "locked_at_utc": lock_time.isoformat(),
        "question": "Does the frozen R43G1 coverage-reliability residual shrink remain useful when transported without tuning onto the already locked R42L three-target current-season architecture?",
        "governance": {
            "target_count": 3,
            "target_results_accessed": False,
            "target_confirmed_xi_accessed": False,
            "target_postmatch_data_accessed": False,
            "source_r42l_evidence_head": SOURCE_R42L_EVIDENCE_HEAD,
            "source_r42l_lock_blob_sha": EXPECTED_R42L_LOCK_BLOB_SHA,
            "source_r43g1_result_blob_sha": SOURCE_R43G1_RESULT_BLOB_SHA,
            "source_r43g2_result_blob_sha": SOURCE_R43G2_RESULT_BLOB_SHA,
            "r43g1_reliability_formula_reused_exact": "r_ctx=min(home_xi_certainty,away_xi_certainty,home_role_known_share,away_role_known_share); r_tech=min(r_ctx,tech_known_share_min)",
            "parameter_search": False, "threshold_search": False, "feature_search": False,
            "probability_retuning_on_targets": False,
            "no_manual_outcome_override": True, "no_manual_draw_override": True,
            "no_draw_threshold": True, "no_draw_class_weight": True,
            "r42l_existing_lock_modified": False,
            "reveal_policy": "append_only_after_results; never rewrite this lock",
        },
        "engine": {"history_rows": len(engine["rows"]), "history_end_date": engine["history_end_date"], "model_train_rows": engine["train_rows"], "model_train_end_date": engine["train_end_date"]},
        "targets": outputs,
        "next_gate": {
            "action": "WAIT_FOR_RESULTS_AND_SCORE_R42L_REFERENCE_VS_R43G3_WITHOUT_TUNING",
            "metrics": ["Top1 accuracy", "Log Loss", "Brier", "RPS"],
            "no_tuning_from_three_matches": True,
        },
        "limitations": [
            "Only three true-forward targets; this is a falsification/forward-direction check, not statistical proof.",
            "R43G1 reliability magnitudes are reused exactly, but the context parent is transported from historical R40C to the independently locked R42E current-season anchor; this architectural parent transport is pre-registered here before target outcomes.",
            "R42L remains immutable and is retained as the reference prediction.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "r43g3_forward_coverage_reliability_transport_lock.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "locked_at_utc": result["locked_at_utc"], "targets": [{"target": x["target"], "reliability": x["reliability"], "candidate": x["probabilities"]["R43G3_forward_reliability_transport_candidate"]} for x in outputs]}, indent=2))
    return result


def verify() -> None:
    p = OUT / "r43g3_forward_coverage_reliability_transport_lock.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "LOCKED_PREMATCH" and d["formal_weight"] == 0
    assert d["classification"] == "TRUE_FORWARD_R43G1_RELIABILITY_FORMULA_TRANSPORT_ON_EXISTING_R42L_MW2_TARGETS"
    g = d["governance"]
    assert g["target_results_accessed"] is False and g["target_confirmed_xi_accessed"] is False and g["target_postmatch_data_accessed"] is False
    assert g["parameter_search"] is False and g["threshold_search"] is False and g["feature_search"] is False
    assert g["probability_retuning_on_targets"] is False and g["r42l_existing_lock_modified"] is False
    assert len(d["targets"]) == 3
    for x in d["targets"]:
        assert x["actual_result"] is None and x["actual_score"] is None and x["result_label_accessed"] is False
        assert 0 <= float(x["reliability"]["r_tech"]) <= float(x["reliability"]["r_ctx"]) <= 1
        p = x["probabilities"]["R43G3_forward_reliability_transport_candidate"]
        assert abs(sum(float(p[k]) for k in ("home", "draw", "away")) - 1.0) < 1e-10
        assert all(math.isfinite(float(p[k])) and float(p[k]) > 0 for k in ("home", "draw", "away"))
    print("R43G3 forward lock contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(f"unknown command {cmd}")
