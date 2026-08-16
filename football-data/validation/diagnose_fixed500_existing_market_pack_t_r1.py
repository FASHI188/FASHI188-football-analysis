#!/usr/bin/env python3
"""Same-fixed500 exploratory test of repository-local historical market information for T.

Question: on the exact PR197 fixed500 identities, does an existing synchronized historical
market pack (1X2 + Asian handicap + O/U) add T information beyond the 47 strict-prior
score-history features, and beyond O/U alone?

All market columns already exist in repository-local processed historical files. No provider,
network, new collection, sample search, threshold search, or formal mutation is performed.
Historical closing/reference odds lack original quote timestamps, so this is retrospective
information-ceiling evidence only and cannot establish live PIT validity.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, assemble_joint, conditional_probabilities, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total,
    hda_metrics,
    load_experiment,
    paired_bootstrap,
    sample_fixed_n,
    score_metrics,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, metric_components, metric_summary, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_existing_market_pack_t_r1.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_existing_market_pack_t_r1_rows.csv"
TOTAL_CLASSES = list(range(8))

ONE_X_TWO = (
    ("PSCH", "PSCD", "PSCA", "PS_CLOSE"),
    ("AvgCH", "AvgCD", "AvgCA", "AVG_CLOSE"),
    ("MaxCH", "MaxCD", "MaxCA", "MAX_CLOSE"),
    ("B365CH", "B365CD", "B365CA", "B365_CLOSE"),
    ("PSH", "PSD", "PSA", "PS_OPEN"),
    ("AvgH", "AvgD", "AvgA", "AVG_OPEN"),
    ("MaxH", "MaxD", "MaxA", "MAX_OPEN"),
    ("B365H", "B365D", "B365A", "B365_OPEN"),
)
AH_PAIRS = (
    ("PCAHH", "PCAHA", "PS_CLOSE"),
    ("AvgCAHH", "AvgCAHA", "AVG_CLOSE"),
    ("MaxCAHH", "MaxCAHA", "MAX_CLOSE"),
    ("B365CAHH", "B365CAHA", "B365_CLOSE"),
    ("PAHH", "PAHA", "PS_OPEN"),
    ("AvgAHH", "AvgAHA", "AVG_OPEN"),
)
OU_PAIRS = (
    ("PC>2.5", "PC<2.5", 2.5, "PS_CLOSE"),
    ("AvgC>2.5", "AvgC<2.5", 2.5, "AVG_CLOSE"),
    ("MaxC>2.5", "MaxC<2.5", 2.5, "MAX_CLOSE"),
    ("B365C>2.5", "B365C<2.5", 2.5, "B365_CLOSE"),
    ("P>2.5", "P<2.5", 2.5, "PS_OPEN"),
    ("Avg>2.5", "Avg<2.5", 2.5, "AVG_OPEN"),
)
MARKET_FULL = [
    "mkt_ou_over_logit",
    "mkt_draw_logit",
    "mkt_home_minus_away",
    "mkt_ah_line",
    "mkt_ah_home_logit",
]
MARKET_OU = ["mkt_ou_over_logit"]


def add_identity_key(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["identity_key"] = out[KEYS].astype(str).agg("|".join, axis=1)
    return out


def devig(values: list[float]) -> np.ndarray:
    inv = 1.0 / np.asarray(values, dtype=float)
    return inv / inv.sum()


def logit(p: float, eps: float = 1e-6) -> float:
    q = min(1.0 - eps, max(eps, float(p)))
    return float(math.log(q / (1.0 - q)))


def first_triplet(row: dict[str, Any], headers: list[str]) -> tuple[np.ndarray, str] | None:
    for h_alias, d_alias, a_alias, source in ONE_X_TWO:
        fields = [field_name(headers, [x]) for x in (h_alias, d_alias, a_alias)]
        if not all(fields):
            continue
        vals = [float_value(row, x) for x in fields]
        if all(valid_price(v) for v in vals):
            return devig([float(v) for v in vals]), source
    return None


def first_ah(row: dict[str, Any], headers: list[str]) -> tuple[float, float, str] | None:
    line_field = field_name(headers, ["AHCh", "AHh", "asian_handicap_line", "handicap_line", "spread_line"])
    line = float_value(row, line_field)
    if line is None:
        return None
    for h_alias, a_alias, source in AH_PAIRS:
        hf = field_name(headers, [h_alias]); af = field_name(headers, [a_alias])
        if not hf or not af:
            continue
        hv = float_value(row, hf); av = float_value(row, af)
        if valid_price(hv) and valid_price(av):
            p = devig([float(hv), float(av)])
            return float(line), float(p[0]), source
    return None


def first_ou(row: dict[str, Any], headers: list[str]) -> tuple[float, float, str] | None:
    for o_alias, u_alias, line, source in OU_PAIRS:
        of = field_name(headers, [o_alias]); uf = field_name(headers, [u_alias])
        if not of or not uf:
            continue
        ov = float_value(row, of); uv = float_value(row, uf)
        if valid_price(ov) and valid_price(uv):
            p = devig([float(ov), float(uv)])
            return float(line), float(p[0]), source
    return None


def materialize_market(ledger: pd.DataFrame) -> pd.DataFrame:
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
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            for row_number, row in enumerate(reader, start=2):
                identity = row_map.get(row_number)
                if identity is None:
                    continue
                x12 = first_triplet(row, headers)
                ah = first_ah(row, headers)
                ou = first_ou(row, headers)
                if x12 is None and ah is None and ou is None:
                    continue
                rec: dict[str, Any] = {"identity_key": identity}
                if x12 is not None:
                    p, source = x12
                    rec.update({
                        "mkt_p_home": float(p[0]), "mkt_p_draw": float(p[1]), "mkt_p_away": float(p[2]),
                        "mkt_draw_logit": logit(float(p[1])),
                        "mkt_home_minus_away": float(p[0] - p[2]),
                        "mkt_1x2_source": source,
                    })
                if ah is not None:
                    line, ph, source = ah
                    rec.update({"mkt_ah_line": line, "mkt_ah_home_prob": ph, "mkt_ah_home_logit": logit(ph), "mkt_ah_source": source})
                if ou is not None:
                    line, po, source = ou
                    rec.update({"mkt_ou_line": line, "mkt_ou_over_prob": po, "mkt_ou_over_logit": logit(po), "mkt_ou_source": source})
                records.append(rec)
    if not records:
        raise ResearchError("no existing historical market rows materialized")
    frame = pd.DataFrame(records)
    if frame.identity_key.duplicated().any():
        frame = frame.sort_values("identity_key").drop_duplicates("identity_key")
    return frame.reset_index(drop=True)


def parity_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    truth = np.asarray(y, dtype=int)
    pred = np.argmax(p, axis=1)
    ll = -np.log(np.clip(p[np.arange(len(p)), truth], 1e-15, 1.0))
    onehot = np.zeros_like(p); onehot[np.arange(len(p)), truth] = 1.0
    auc = float(roc_auc_score(truth, p[:, 1])) if len(np.unique(truth)) == 2 else float("nan")
    return {
        "accuracy": float(np.mean(pred == truth)),
        "log_loss": float(ll.mean()),
        "brier": float(np.mean(np.sum((p - onehot) ** 2, axis=1))),
        "odd_auc": auc,
    }


def fit_total(fit: pd.DataFrame, train: pd.DataFrame, policy: pd.DataFrame, test: pd.DataFrame, features: list[str], config: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    c, grid = select_C(train, policy, features, "total_class", TOTAL_CLASSES, config)
    model = make_model(c, config); model.fit(fit[features], fit.total_class)
    return align_probability(model, test[features], TOTAL_CLASSES), {"selected_C": c, "policy_grid": grid, "fit_rows": int(len(fit)), "feature_count": len(features)}


def fit_parity(fit: pd.DataFrame, train: pd.DataFrame, policy: pd.DataFrame, test: pd.DataFrame, features: list[str], config: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    classes = [0, 1]
    c, grid = select_C(train, policy, features, "exact_parity", classes, config)
    model = make_model(c, config); model.fit(fit[features], fit.exact_parity)
    return align_probability(model, test[features], classes), {"selected_C": c, "policy_grid": grid, "fit_rows": int(len(fit)), "feature_count": len(features)}


def clean_hda(x: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in x.items() if not str(k).startswith("_")}


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = add_identity_key(build_features(raw))
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    test_position = int(exp["test_position_zero_based"])
    latest_position = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if test_position >= latest_position:
        raise ResearchError("must reuse PR197 non-latest fixed500")

    base["split"] = assign_fold(base, seasons, test_position)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    fold = attach_exact_total(base, raw)
    market = materialize_market(raw)
    fold = fold.merge(market, on="identity_key", how="left", validate="one_to_one")

    sample = fold.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    raw_scores = raw[KEYS + ["home_goals_90", "away_goals_90", "total_goals"]].copy()
    raw_scores["season"] = raw_scores["season"].astype(str); sample["season"] = sample["season"].astype(str)
    sample = sample.merge(raw_scores, on=KEYS, how="left", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("fixed500 reconstruction mismatch")

    synchronized = fold[MARKET_FULL].notna().all(axis=1)
    ou_valid = fold[MARKET_OU].notna().all(axis=1)
    sample_sync = sample[sample[MARKET_FULL].notna().all(axis=1)].copy()
    sample_ou = sample[sample[MARKET_OU].notna().all(axis=1)].copy()
    if len(sample_sync) < 80:
        raise ResearchError(f"fixed500 synchronized market coverage too small: {len(sample_sync)}")

    # Main comparison uses one identical synchronized cohort and identical historical rows.
    fit_sync = fold[fold.split.isin(["train", "policy"]) & synchronized].copy()
    train_sync = fit_sync[fit_sync.split == "train"].copy(); policy_sync = fit_sync[fit_sync.split == "policy"].copy()
    if min(len(train_sync), len(policy_sync)) < 100:
        raise ResearchError("synchronized historical market train/policy coverage too small")

    packs = {
        "core": core,
        "core_plus_ou": core + MARKET_OU,
        "core_plus_full_market": core + MARKET_FULL,
    }
    total_probs: dict[str, np.ndarray] = {}
    parity_probs: dict[str, np.ndarray] = {}
    receipts: dict[str, Any] = {}
    for name, feats in packs.items():
        pT, t_receipt = fit_total(fit_sync, train_sync, policy_sync, sample_sync, feats, config)
        pp, p_receipt = fit_parity(fit_sync, train_sync, policy_sync, sample_sync, feats, config)
        total_probs[name] = pT; parity_probs[name] = pp
        receipts[name] = {"total": t_receipt, "parity": p_receipt}

    yT = sample_sync.total_class.to_numpy(int); yP = sample_sync.exact_parity.to_numpy(int)
    total_metrics: dict[str, Any] = {}; parity_out: dict[str, Any] = {}
    components: dict[str, Any] = {}
    for name in packs:
        c = metric_components(yT, total_probs[name], TOTAL_CLASSES); components[name] = c
        total_metrics[name] = metric_summary(c); parity_out[name] = parity_metrics(yP, parity_probs[name])

    boot_full_vs_core = {m: paired_bootstrap(components["core_plus_full_market"][m].to_numpy(float)-components["core"][m].to_numpy(float), 5000, 850100+i) for i,m in enumerate(("logloss","brier","rps"))}
    boot_full_vs_ou = {m: paired_bootstrap(components["core_plus_full_market"][m].to_numpy(float)-components["core_plus_ou"][m].to_numpy(float), 5000, 850110+i) for i,m in enumerate(("logloss","brier","rps"))}

    cond, _, cond_receipt = conditional_probabilities(fold, sample_sync, core, config)
    rows = sample_sync[KEYS + ["match_identity","identity_hash","home_goals_90","away_goals_90","total_goals","goal_difference"] + MARKET_FULL + ["mkt_1x2_source","mkt_ah_source","mkt_ou_source"]].copy()
    rows = rows.rename(columns={"total_goals":"actual_total","goal_difference":"actual_gd"})
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
        "schema_version":"FIXED500_EXISTING_MARKET_PACK_T_R1",
        "classification":"POST_RESULT_SAME_FIXED500_RETROSPECTIVE_EXISTING_MARKET_INFORMATION_TEST",
        "scientific_question":"Can existing repository-local 1X2+AH+OU information improve T beyond the 47 historical core and beyond OU alone?",
        "sample":{
            "parent_fixed500_n":500,
            "parent_fixed500_identity_sha256":sample_hash,
            "synchronized_market_n":int(len(sample_sync)),
            "ou_any_n":int(len(sample_ou)),
            "synchronized_market_rate":float(len(sample_sync)/500),
            "actual_draws_synchronized":int(draws.sum()),
            "new_sample_consumed":False,
            "latest_position4_confirmation_opened":False,
        },
        "market_features":{"ou_only":MARKET_OU,"full_market":MARKET_FULL,"semantics":"de-vigged existing historical closing/reference prices; preferred closing columns then opening fallback; no original quote timestamps"},
        "historical_fit":{"synchronized_fit_rows":int(len(fit_sync)),"train_rows":int(len(train_sync)),"policy_rows":int(len(policy_sync)),"feature_receipts":receipts},
        "metrics":{
            "direct_t":total_metrics,
            "direct_t_full_minus_core":{k:float(total_metrics["core_plus_full_market"][k]-total_metrics["core"][k]) for k in total_metrics["core"]},
            "direct_t_full_minus_ou":{k:float(total_metrics["core_plus_full_market"][k]-total_metrics["core_plus_ou"][k]) for k in total_metrics["core_plus_ou"]},
            "bootstrap_full_minus_core":boot_full_vs_core,
            "bootstrap_full_minus_ou":boot_full_vs_ou,
            "parity":parity_out,
            "hda":hda,
            "exact_score":scores,
        },
        "draw_diagnostics":{
            "actual_draws":int(draws.sum()),
            **{f"{name}_draw_calls":int(rows[f"{name}_pred_result"].eq("D").sum()) for name in packs},
            **{f"{name}_draw_hits":int((rows[f"{name}_pred_result"].eq("D") & draws).sum()) for name in packs},
        },
        "conditional_gd_receipt":cond_receipt,
        "data_identity":data_identity,
        "excluded_incomplete_latest_seasons":excluded,
        "interpretation_guard":{
            "retrospective_information_ceiling_only":True,
            "formal_PIT_claim":False,
            "can_authorize_promotion":False,
            "same_fixed500_already_viewed":True,
        },
        "governance":{
            "formal_weight":0,"provider_requests":0,"new_data_collection":False,
            "new_sample_consumed":False,"latest_position4_confirmation_opened":False,
            "formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,
            "current_mutation":False,"main_mutation":False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    rows.to_csv(ROWS_OUT,index=False)
    return result


def main() -> None:
    x=run()
    print(json.dumps({"sample":x["sample"],"direct_t":x["metrics"]["direct_t"],"delta_full_core":x["metrics"]["direct_t_full_minus_core"],"delta_full_ou":x["metrics"]["direct_t_full_minus_ou"],"bootstrap_full_core":x["metrics"]["bootstrap_full_minus_core"],"parity":x["metrics"]["parity"],"hda":x["metrics"]["hda"],"score":x["metrics"]["exact_score"],"draw":x["draw_diagnostics"]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
