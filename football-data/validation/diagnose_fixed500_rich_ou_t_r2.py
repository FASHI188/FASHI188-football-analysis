#!/usr/bin/env python3
from __future__ import annotations

import csv, json, math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from diagnose_fixed500_existing_market_pack_t_r1 import (
    MARKET_FULL, MARKET_OU, add_identity_key, clean_hda, devig, fit_parity, fit_total,
    logit, materialize_market, parity_metrics,
)
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, assemble_joint, conditional_probabilities, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total, hda_metrics, load_experiment, paired_bootstrap, sample_fixed_n, score_metrics,
)
from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, audit_data_identity, build_features, complete_seasons, select_core_features,
)
from v510_historical_structure_model_r1 import metric_components, metric_summary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_rich_ou_t_r2.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_rich_ou_t_r2_rows.csv"
TOTAL_CLASSES = list(range(8))

OU_SOURCES = {
    "ps": (("P>2.5", "P<2.5"), ("PC>2.5", "PC<2.5")),
    "b365": (("B365>2.5", "B365<2.5"), ("B365C>2.5", "B365C<2.5")),
    "avg": (("Avg>2.5", "Avg<2.5"), ("AvgC>2.5", "AvgC<2.5")),
    "max": (("Max>2.5", "Max<2.5"), ("MaxC>2.5", "MaxC<2.5")),
    "bfe": (("BFE>2.5", "BFE<2.5"), ("BFEC>2.5", "BFEC<2.5")),
}


def pair(row: dict[str, Any], headers: list[str], aliases: tuple[str, str]) -> tuple[float, float] | None:
    of = field_name(headers, [aliases[0]]); uf = field_name(headers, [aliases[1]])
    if not of or not uf:
        return None
    ov = float_value(row, of); uv = float_value(row, uf)
    if not valid_price(ov) or not valid_price(uv):
        return None
    inv_o, inv_u = 1.0 / float(ov), 1.0 / float(uv)
    p = devig([float(ov), float(uv)])
    return float(p[0]), float(inv_o + inv_u - 1.0)


def poisson_lambda_from_over25(p_over: float) -> float:
    p = min(0.999999, max(0.000001, float(p_over)))
    def f(lam: float) -> float:
        under_eq_2 = math.exp(-lam) * (1.0 + lam + lam * lam / 2.0)
        return 1.0 - under_eq_2 - p
    return float(brentq(f, 0.02, 12.0))


def add_consensus(rec: dict[str, Any], stage: str, probs: list[float], margins: list[float]) -> None:
    if not probs:
        rec[f"ou_{stage}_source_count"] = 0.0
        return
    x = np.asarray(probs, dtype=float)
    rec[f"ou_{stage}_source_count"] = float(len(x))
    rec[f"ou_{stage}_mean_prob"] = float(x.mean())
    rec[f"ou_{stage}_median_prob"] = float(np.median(x))
    rec[f"ou_{stage}_std_prob"] = float(x.std(ddof=0))
    rec[f"ou_{stage}_range_prob"] = float(x.max() - x.min())
    rec[f"ou_{stage}_mean_logit"] = logit(float(x.mean()))
    rec[f"ou_{stage}_poisson_lambda"] = poisson_lambda_from_over25(float(x.mean()))
    if margins:
        m = np.asarray(margins, dtype=float)
        rec[f"ou_{stage}_mean_margin"] = float(m.mean())
        rec[f"ou_{stage}_std_margin"] = float(m.std(ddof=0))


def materialize_rich_ou(ledger: pd.DataFrame) -> pd.DataFrame:
    keyed = add_identity_key(ledger)
    wanted: dict[str, dict[int, str]] = {}
    for row in keyed[["source_file", "row_number", "identity_key"]].itertuples(index=False):
        wanted.setdefault(str(row.source_file), {})[int(row.row_number)] = str(row.identity_key)
    records: list[dict[str, Any]] = []
    for source_file in sorted(wanted):
        path = ROOT / source_file
        if not path.is_file():
            continue
        row_map = wanted[source_file]
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle); headers = list(reader.fieldnames or [])
            for row_number, row in enumerate(reader, start=2):
                identity = row_map.get(row_number)
                if identity is None:
                    continue
                rec: dict[str, Any] = {"identity_key": identity}
                open_probs: list[float] = []; close_probs: list[float] = []
                open_margins: list[float] = []; close_margins: list[float] = []
                for source, (open_alias, close_alias) in OU_SOURCES.items():
                    op = pair(row, headers, open_alias); cp = pair(row, headers, close_alias)
                    if op is not None:
                        p, margin = op; open_probs.append(p); open_margins.append(margin)
                        rec[f"ou_{source}_open_prob"] = p
                        rec[f"ou_{source}_open_logit"] = logit(p)
                        rec[f"ou_{source}_open_margin"] = margin
                    if cp is not None:
                        p, margin = cp; close_probs.append(p); close_margins.append(margin)
                        rec[f"ou_{source}_close_prob"] = p
                        rec[f"ou_{source}_close_logit"] = logit(p)
                        rec[f"ou_{source}_close_margin"] = margin
                    if op is not None and cp is not None:
                        rec[f"ou_{source}_shift_prob"] = float(cp[0] - op[0])
                        rec[f"ou_{source}_shift_logit"] = float(logit(cp[0]) - logit(op[0]))
                add_consensus(rec, "open", open_probs, open_margins)
                add_consensus(rec, "close", close_probs, close_margins)
                if open_probs and close_probs:
                    rec["ou_consensus_shift_prob"] = float(np.mean(close_probs) - np.mean(open_probs))
                    rec["ou_consensus_shift_logit"] = float(logit(float(np.mean(close_probs))) - logit(float(np.mean(open_probs))))
                    rec["ou_consensus_lambda_shift"] = float(poisson_lambda_from_over25(float(np.mean(close_probs))) - poisson_lambda_from_over25(float(np.mean(open_probs))))
                if len(rec) > 1:
                    records.append(rec)
    if not records:
        raise ResearchError("no rich OU rows materialized")
    frame = pd.DataFrame(records).sort_values("identity_key").drop_duplicates("identity_key")
    return frame.reset_index(drop=True)


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw)); core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    test_position = int(exp["test_position_zero_based"])
    latest_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if test_position >= latest_position:
        raise ResearchError("must reuse PR197 non-latest fixed500")
    base["split"] = assign_fold(base, seasons, test_position)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    fold = attach_exact_total(base, raw)
    fold = fold.merge(materialize_market(raw), on="identity_key", how="left", validate="one_to_one")
    rich = materialize_rich_ou(raw)
    fold = fold.merge(rich, on="identity_key", how="left", validate="one_to_one")
    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str); sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("fixed500 reconstruction mismatch")

    sync_mask = fold[MARKET_FULL].notna().all(axis=1)
    sample_sync = sample[sample[MARKET_FULL].notna().all(axis=1)].copy()
    fit_sync = fold[fold.split.isin(["train", "policy"]) & sync_mask].copy()
    train_sync = fit_sync[fit_sync.split == "train"].copy(); policy_sync = fit_sync[fit_sync.split == "policy"].copy()
    if len(sample_sync) < 80 or min(len(train_sync), len(policy_sync)) < 100:
        raise ResearchError("synchronized cohort too small")

    close_consensus = [
        "ou_close_mean_logit", "ou_close_std_prob", "ou_close_range_prob",
        "ou_close_source_count", "ou_close_poisson_lambda", "ou_close_mean_margin", "ou_close_std_margin",
    ]
    open_close = [
        "ou_open_mean_logit", "ou_open_std_prob", "ou_open_range_prob", "ou_open_source_count",
        "ou_open_poisson_lambda", "ou_open_mean_margin", "ou_open_std_margin",
        "ou_consensus_shift_prob", "ou_consensus_shift_logit", "ou_consensus_lambda_shift",
    ]
    source_detail = []
    for source in OU_SOURCES:
        source_detail += [
            f"ou_{source}_open_logit", f"ou_{source}_close_logit",
            f"ou_{source}_shift_prob", f"ou_{source}_shift_logit",
            f"ou_{source}_open_margin", f"ou_{source}_close_margin",
        ]
    rich_ou = close_consensus + open_close + source_detail
    packs = {
        "core": core,
        "core_plus_single_ou": core + MARKET_OU,
        "core_plus_close_consensus": core + close_consensus,
        "core_plus_rich_ou": core + rich_ou,
        "core_plus_rich_ou_full_market": core + rich_ou + [x for x in MARKET_FULL if x not in MARKET_OU],
    }

    total_probs: dict[str, np.ndarray] = {}; parity_probs: dict[str, np.ndarray] = {}; receipts = {}
    for name, feats in packs.items():
        pT, tr = fit_total(fit_sync, train_sync, policy_sync, sample_sync, feats, config)
        pp, pr = fit_parity(fit_sync, train_sync, policy_sync, sample_sync, feats, config)
        total_probs[name] = pT; parity_probs[name] = pp; receipts[name] = {"total": tr, "parity": pr}

    yT = sample_sync.total_class.to_numpy(int); yP = sample_sync.exact_parity.to_numpy(int)
    components = {}; total_metrics = {}; parity_out = {}
    for name in packs:
        c = metric_components(yT, total_probs[name], TOTAL_CLASSES); components[name] = c
        total_metrics[name] = metric_summary(c); parity_out[name] = parity_metrics(yP, parity_probs[name])
    boot_rich_vs_single = {m: paired_bootstrap(components["core_plus_rich_ou"][m].to_numpy(float)-components["core_plus_single_ou"][m].to_numpy(float), 5000, 860100+i) for i,m in enumerate(("logloss","brier","rps"))}
    boot_rich_vs_core = {m: paired_bootstrap(components["core_plus_rich_ou"][m].to_numpy(float)-components["core"][m].to_numpy(float), 5000, 860110+i) for i,m in enumerate(("logloss","brier","rps"))}
    boot_richfull_vs_rich = {m: paired_bootstrap(components["core_plus_rich_ou_full_market"][m].to_numpy(float)-components["core_plus_rich_ou"][m].to_numpy(float), 5000, 860120+i) for i,m in enumerate(("logloss","brier","rps"))}

    cond, _, cond_receipt = conditional_probabilities(fold, sample_sync, core, config)
    row_cols = KEYS + ["match_identity","identity_hash","home_goals_90","away_goals_90","total_goals","goal_difference"] + MARKET_FULL + rich_ou
    rows = sample_sync[row_cols].copy().rename(columns={"total_goals":"actual_total","goal_difference":"actual_gd"})
    rows["actual_total_class"] = np.minimum(rows.actual_total.astype(int),7)
    rows["actual_score"] = rows.home_goals_90.astype(int).astype(str)+":"+rows.away_goals_90.astype(int).astype(str)
    rows["actual_result"] = np.where(rows.actual_gd>0,"H",np.where(rows.actual_gd==0,"D","A"))
    for name in packs:
        rows[f"{name}_pred_total_class"] = np.argmax(total_probs[name],axis=1)
        joint = pd.DataFrame(assemble_joint(total_probs[name], cond))
        for col in joint.columns:
            rows[f"{name}_{col}"] = joint[col].to_numpy()
    hda = {name: clean_hda(hda_metrics(rows,name)) for name in packs}
    scores = {name: score_metrics(rows,name) for name in packs}
    draws = rows.actual_result.eq("D")

    result = {
        "schema_version":"FIXED500_RICH_OU_T_R2",
        "classification":"POST_RESULT_SAME_FIXED500_RETROSPECTIVE_RICH_OU_INFORMATION_CEILING",
        "sample":{"parent_fixed500_n":500,"parent_fixed500_identity_sha256":sample_hash,"comparison_n":int(len(sample_sync)),"actual_draws":int(draws.sum()),"new_sample_consumed":False,"latest_position4_confirmation_opened":False},
        "feature_contract":{"close_consensus":close_consensus,"rich_ou":rich_ou,"ou_sources":list(OU_SOURCES),"same_cohort_for_all_packs":True},
        "historical_fit":{"fit_rows":int(len(fit_sync)),"train_rows":int(len(train_sync)),"policy_rows":int(len(policy_sync)),"receipts":receipts},
        "metrics":{"direct_t":total_metrics,"bootstrap_rich_minus_single":boot_rich_vs_single,"bootstrap_rich_minus_core":boot_rich_vs_core,"bootstrap_richfull_minus_rich":boot_richfull_vs_rich,"parity":parity_out,"hda":hda,"exact_score":scores},
        "draw_diagnostics":{"actual_draws":int(draws.sum()),**{f"{n}_draw_calls":int(rows[f"{n}_pred_result"].eq("D").sum()) for n in packs},**{f"{n}_draw_hits":int((rows[f"{n}_pred_result"].eq("D") & draws).sum()) for n in packs}},
        "conditional_gd_receipt":cond_receipt,"data_identity":data_identity,"excluded_incomplete_latest_seasons":excluded,
        "interpretation_guard":{"retrospective_information_ceiling_only":True,"formal_PIT_claim":False,"can_authorize_promotion":False,"same_fixed500_already_viewed":True},
        "governance":{"formal_weight":0,"provider_requests":0,"new_data_collection":False,"new_sample_consumed":False,"latest_position4_confirmation_opened":False,"formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,"current_mutation":False,"main_mutation":False},
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); rows.to_csv(ROWS_OUT,index=False)
    return result


def main() -> None:
    x=run(); print(json.dumps({"sample":x["sample"],"direct_t":x["metrics"]["direct_t"],"boot_rich_single":x["metrics"]["bootstrap_rich_minus_single"],"boot_rich_core":x["metrics"]["bootstrap_rich_minus_core"],"boot_full_rich":x["metrics"]["bootstrap_richfull_minus_rich"],"parity":x["metrics"]["parity"],"hda":x["metrics"]["hda"],"score":x["metrics"]["exact_score"],"draw":x["draw_diagnostics"]},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
