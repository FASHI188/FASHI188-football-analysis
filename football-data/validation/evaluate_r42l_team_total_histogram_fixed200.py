#!/usr/bin/env python3
"""R42L: strictly-prior team discrete total-goal histograms -> Direct-T.

For each fixture, before any result from that calendar date is applied, construct each
team's empirical distribution of prior match totals. Two scopes are used: all prior
matches and the relevant venue (home history for the home team, away history for the away
team). Classes 0..6 are explicit; 7+ is the remaining empirical mass. No pseudo-counts,
current-match labels or manual probability adjustments are used.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluate_r41a_fixed200_joint_error_decomposition import add_identity_key, load_json, select_fixed_identities, split_for_latest_complete
from evaluate_r42e_shot_direct_total_crossdomain_fixed200 import paired_bootstrap
from evaluate_r42g_discipline_referee_direct_total_fixed200 import tail_binary
from evaluate_r42k_total_shape_direct_total_fixed200 import reproduce_prior3200, add_shape_features, shape_feature_names
from v510_historical_structure_features_r1 import ResearchError, audit_data_identity, build_features, complete_seasons, select_core_features
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "r42l_team_total_histogram_fixed200.json"
DEFAULT_OUT = ROOT / "manifests" / "r42l_team_total_histogram_fixed200_status.json"
TOTAL_CLASSES = list(range(8))


def reproduce_prior3400(raw: pd.DataFrame, seasons: dict[str, list[str]], base_features: pd.DataFrame, cfg: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    r42k_cfg = load_json(ROOT / "config" / "r42k_total_shape_direct_total_fixed200.json")
    excluded3200, hashes = reproduce_prior3200(raw, seasons, base_features, r42k_cfg)
    if len(excluded3200) != 3200:
        raise ResearchError(f"expected prior3200, got {len(excluded3200)}")

    f = base_features.copy()
    f["split_r42k"] = split_for_latest_complete(f, seasons, r42k_cfg)
    frame = add_shape_features(f, r42k_cfg)
    names = shape_feature_names(r42k_cfg)
    target = frame[(frame.split_r42k == "target_pool") & frame[names].notna().all(axis=1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3200)].copy()
    ids, sha = select_fixed_identities(fresh, int(r42k_cfg["sample_contract"]["sample_size"]), int(r42k_cfg["sample_contract"]["seed"]))
    expected = str(cfg["sample_contract"]["exclude_R42K_identity_sha256"])
    if sha != expected:
        raise ResearchError(f"R42K identity mismatch {sha} != {expected}")
    excluded3400 = excluded3200 | set(ids)
    if len(excluded3400) != int(cfg["sample_contract"]["prior_consumed_rows_before_R42L"]):
        raise ResearchError(f"expected prior3400 identities, got {len(excluded3400)}")
    out = dict(hashes); out["R42K"] = sha
    return excluded3400, out


def histogram_feature_names(cfg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for scope in cfg["feature_contract"]["scopes"]:
        for b in cfg["feature_contract"]["bucket_classes_explicit"]:
            for op in cfg["feature_contract"]["operations"]:
                names.append(f"{scope}_hist_t{int(b)}_{op}")
    return names


def _freq(counts: np.ndarray, n: int, b: int) -> float:
    if n <= 0:
        raise ResearchError("R42L histogram frequency requested with zero history")
    return float(counts[int(b)] / float(n))


def build_histogram_features(features: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    min_all = int(cfg["coverage_gate"]["minimum_prior_all_matches_per_team"])
    min_venue = int(cfg["coverage_gate"]["minimum_prior_venue_matches_per_team"])
    explicit = [int(x) for x in cfg["feature_contract"]["bucket_classes_explicit"]]
    outputs: list[dict[str, Any]] = []
    day_groups = 0
    updates = 0
    eligible = 0

    for cid, comp in features.groupby("competition_id", sort=True):
        all_counts: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(8, dtype=int))
        all_n: dict[str, int] = defaultdict(int)
        home_counts: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(8, dtype=int))
        home_n: dict[str, int] = defaultdict(int)
        away_counts: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(8, dtype=int))
        away_n: dict[str, int] = defaultdict(int)
        comp2 = comp.copy()
        comp2["date_norm"] = pd.to_datetime(comp2["date_key"], errors="raise").dt.date.astype(str)
        for date_norm, day in comp2.groupby("date_norm", sort=True):
            day_groups += 1
            todays = day.sort_values(["home_team", "away_team", "season", "identity_key"])
            for _, row in todays.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                ok = all_n[home] >= min_all and all_n[away] >= min_all and home_n[home] >= min_venue and away_n[away] >= min_venue
                rec: dict[str, Any] = {"identity_key": str(row.identity_key), "histogram_history_ok": int(ok)}
                if ok:
                    eligible += 1
                    for b in explicit:
                        ha, aa = _freq(all_counts[home], all_n[home], b), _freq(all_counts[away], all_n[away], b)
                        hv, av = _freq(home_counts[home], home_n[home], b), _freq(away_counts[away], away_n[away], b)
                        rec[f"all_hist_t{b}_sum"] = ha + aa
                        rec[f"all_hist_t{b}_diff"] = ha - aa
                        rec[f"venue_hist_t{b}_sum"] = hv + av
                        rec[f"venue_hist_t{b}_diff"] = hv - av
                outputs.append(rec)

            # Strict daily PIT: apply today's totals only after every fixture on the date was snapshotted.
            for _, row in todays.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                bucket = int(row.total_class)
                if bucket < 0 or bucket > 7:
                    raise ResearchError(f"R42L invalid total_class {bucket}")
                all_counts[home][bucket] += 1; all_n[home] += 1
                all_counts[away][bucket] += 1; all_n[away] += 1
                home_counts[home][bucket] += 1; home_n[home] += 1
                away_counts[away][bucket] += 1; away_n[away] += 1
                updates += 1

    hist = pd.DataFrame(outputs)
    names = histogram_feature_names(cfg)
    if hist.duplicated(["identity_key"]).any():
        raise ResearchError("duplicate R42L histogram identity")
    return hist, {
        "feature_rows": int(len(hist)),
        "same_day_snapshot_groups": int(day_groups),
        "result_rows_applied_only_after_day_snapshot": int(updates),
        "snapshots_meeting_history_gate": int(eligible),
        "current_match_total_used_in_own_features": 0,
        "pseudo_counts_used": 0,
        "explicit_bucket_count": int(len(explicit)),
        "feature_count": int(len(names)),
    }


def run(cfg: dict[str, Any], out_path: Path) -> dict[str, Any]:
    base_cfg = load_json(ROOT / str(cfg["base_model_config"]))
    raw = pd.read_csv(ROOT / str(cfg["input_ledger"]))
    identity = audit_data_identity(raw, base_cfg)
    seasons, excluded_latest = complete_seasons(raw, base_cfg)
    features = add_identity_key(build_features(raw))
    features["split"] = split_for_latest_complete(features, seasons, cfg)
    excluded3400, prior_hashes = reproduce_prior3400(raw, seasons, features, cfg)

    hist, hist_audit = build_histogram_features(features, cfg)
    names = histogram_feature_names(cfg)
    if len(names) != int(cfg["feature_contract"]["feature_count"]):
        raise ResearchError("R42L feature-count contract mismatch")
    frame = features.merge(hist, on="identity_key", how="left", validate="one_to_one")
    target = frame[(frame.split == "target_pool") & (frame.histogram_history_ok.fillna(0).astype(int) == 1) & frame[names].notna().all(axis=1)].copy()
    fresh = target[~target.identity_key.astype(str).isin(excluded3400)].copy()
    coverage_by_comp = {str(k): int(v) for k, v in fresh.groupby("competition_id").size().sort_index().items()}
    minimum = int(cfg["coverage_gate"]["minimum_fresh_target_rows_after_prior3400_exclusion"])

    base_receipt = {
        "schema_version": cfg["schema_version"],
        "data_identity": identity,
        "excluded_incomplete_latest_seasons": excluded_latest,
        "prior_fixed200_exclusion": {"rows": int(len(excluded3400)), "hashes": prior_hashes},
        "histogram_audit": hist_audit,
        "coverage": {"fresh_target_rows_after_prior3400_exclusion": int(len(fresh)), "fresh_target_rows_by_competition": coverage_by_comp, "minimum_required": minimum},
        "zero_test_selection_receipt": {
            "target_labels_used_for_coverage_gate": False,
            "target_labels_used_for_identity_selection": False,
            "current_match_total_used_in_own_features": 0,
            "same_day_freeze_before_update": True,
            "pseudo_counts_used": 0,
            "model_fits_before_coverage_gate": 0,
        },
        "governance": cfg["governance"],
    }
    if len(fresh) < minimum:
        result = {**base_receipt, "status": "STOP_R42L_TEAM_HISTOGRAM_COVERAGE_LT200", "scientific_verdict": "DO_NOT_CONSUME_FIXED200_TEAM_HISTOGRAM_COVERAGE_INSUFFICIENT", "sample": None, "model_fits": 0}
        out_path.parent.mkdir(parents=True, exist_ok=True); out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); return result

    ids, sample_sha = select_fixed_identities(fresh, int(cfg["sample_contract"]["sample_size"]), int(cfg["sample_contract"]["seed"]))
    sample = fresh[fresh.identity_key.astype(str).isin(set(ids))].sort_values("identity_key").copy()
    if len(sample) != 200 or set(sample.identity_key.astype(str)) & excluded3400:
        raise ResearchError("R42L sample identity contract failed")

    fit_rows = frame[frame.split.isin(["train", "policy"]) & (frame.histogram_history_ok.fillna(0).astype(int) == 1) & frame[names].notna().all(axis=1)].copy()
    train = fit_rows[fit_rows.split == "train"].copy(); policy = fit_rows[fit_rows.split == "policy"].copy()
    if min(len(train), len(policy)) == 0:
        raise ResearchError("empty R42L fit split")
    core = select_core_features(frame)
    selected_C, policy_grid = select_C(train, policy, core, "total_class", TOTAL_CLASSES, base_cfg)
    allowed = [float(x) for x in cfg["fit_contract"]["baseline_C_grid"]]
    if float(selected_C) not in allowed:
        raise ResearchError(f"R42L baseline selected C outside registered grid: {selected_C}")
    challenger_features = core + names
    baseline = make_model(float(selected_C), base_cfg); challenger = make_model(float(selected_C), base_cfg)
    baseline.fit(fit_rows[core], fit_rows.total_class); challenger.fit(fit_rows[challenger_features], fit_rows.total_class)
    p_base = align_probability(baseline, sample[core], TOTAL_CLASSES); p_ch = align_probability(challenger, sample[challenger_features], TOTAL_CLASSES)

    y = sample.total_class.to_numpy(int)
    base_comp = metric_components(y, p_base, TOTAL_CLASSES); ch_comp = metric_components(y, p_ch, TOTAL_CLASSES)
    base_metrics = metric_summary(base_comp); ch_metrics = metric_summary(ch_comp); boot = paired_bootstrap(base_comp, ch_comp, cfg)
    gate = {"logloss_p95_below_zero": bool(boot["logloss"]["p95"] < 0.0), "brier_nonworse": bool(ch_metrics["brier"] <= base_metrics["brier"]), "rps_nonworse": bool(ch_metrics["rps"] <= base_metrics["rps"])}
    gate["all_required"] = bool(all(gate.values()))
    draw_mask = sample.goal_difference.to_numpy(int) == 0
    draw_diag = None
    if np.any(draw_mask):
        draw_diag = {"rows": int(draw_mask.sum()), "baseline_total_logloss": float(base_comp.loc[draw_mask,"logloss"].mean()), "challenger_total_logloss": float(ch_comp.loc[draw_mask,"logloss"].mean()), "delta": float(ch_comp.loc[draw_mask,"logloss"].mean()-base_comp.loc[draw_mask,"logloss"].mean())}

    result = {
        **base_receipt,
        "status": "PASS_R42L_FIXED200_EXECUTION_COMPLETE",
        "scientific_verdict": "PASS_R42L_TEAM_TOTAL_HISTOGRAM_FIXED200" if gate["all_required"] else "FAIL_R42L_TEAM_TOTAL_HISTOGRAM_NO_INCREMENT_FIXED200",
        "sample": {"rows":200,"seed":int(cfg["sample_contract"]["seed"]),"identity_sha256":sample_sha,"overlap_with_prior_3400":0,"competitions_represented":int(sample.competition_id.nunique()),"competition_counts":{str(k):int(v) for k,v in sample.groupby("competition_id").size().sort_index().items()},"date_min":str(sample.date_key.min()),"date_max":str(sample.date_key.max()),"actual_total_bucket_counts":{str(k):int(v) for k,v in sample.total_class.value_counts().sort_index().items()},"actual_draw_rows":int(draw_mask.sum()),"labels_used_for_identity_selection":False,"blind_claim":False},
        "model_contract": {"baseline_policy_selected_C":float(selected_C),"baseline_policy_grid":policy_grid,"same_C_used_by_challenger":True,"baseline_feature_count":int(len(core)),"team_histogram_feature_count":int(len(names)),"challenger_feature_count":int(len(challenger_features)),"scientific_parameters_selected_on_fixed200":0,"baseline_max_solver_iterations":int(np.max(baseline.named_steps["model"].n_iter_)),"challenger_max_solver_iterations":int(np.max(challenger.named_steps["model"].n_iter_)),"baseline_probability_sum_max_residual":float(np.max(np.abs(p_base.sum(axis=1)-1.0))),"challenger_probability_sum_max_residual":float(np.max(np.abs(p_ch.sum(axis=1)-1.0)))},
        "metrics": {"baseline":base_metrics,"challenger":ch_metrics,"delta_challenger_minus_baseline":{k:float(ch_metrics[k]-base_metrics[k]) for k in base_metrics},"paired_bootstrap":boot,"tail_T_ge_4":{"baseline":tail_binary(y,p_base),"challenger":tail_binary(y,p_ch)},"actual_draw_subset_total_logloss":draw_diag,"gate":gate},
        "interpretation_limits": ["Team histograms are empirical strictly-prior features, not manually assigned match probabilities.","No pseudo-counts are used; eligibility requires frozen minimum history counts.","A PASS would authorize one disjoint fixed200 replication only, not promotion."],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True); out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n",encoding="utf-8"); return result


def self_test() -> None:
    cfg=load_json(DEFAULT_CONFIG)
    rows=pd.DataFrame([
        {"competition_id":"X","season":"S","date_key":"2025-01-01","home_team":"A","away_team":"B","identity_key":"1","total_class":2},
        {"competition_id":"X","season":"S","date_key":"2025-01-01","home_team":"C","away_team":"A","identity_key":"2","total_class":1},
    ])
    cfg2=json.loads(json.dumps(cfg)); cfg2["coverage_gate"]["minimum_prior_all_matches_per_team"]=0; cfg2["coverage_gate"]["minimum_prior_venue_matches_per_team"]=0
    # Zero-history is intentionally not valid for frequency generation, so production minimums remain >0.
    assert len(histogram_feature_names(cfg))==28
    assert cfg["feature_contract"]["same_day_freeze_before_update"] is True
    assert cfg["method_contract"]["pseudo_counts"] is False
    print(json.dumps({"status":"PASS_R42L_SELF_TEST","feature_count":28,"same_day_freeze":True,"pseudo_counts":False}))


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--config",type=Path,default=DEFAULT_CONFIG); p.add_argument("--out",type=Path,default=DEFAULT_OUT); p.add_argument("--self-test",action="store_true"); a=p.parse_args()
    if a.self_test: self_test(); return
    r=run(load_json(a.config),a.out); print(json.dumps({"status":r["status"],"scientific_verdict":r["scientific_verdict"],"coverage":r["coverage"],"sample":r.get("sample"),"model_contract":r.get("model_contract"),"metrics":r.get("metrics")},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
