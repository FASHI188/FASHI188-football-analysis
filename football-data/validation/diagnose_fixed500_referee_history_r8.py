#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value
from diagnose_fixed500_existing_market_pack_t_r1 import (
    MARKET_OU,
    add_identity_key,
    fit_parity,
    fit_total,
    materialize_market,
    parity_metrics,
)
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import (
    attach_exact_total,
    load_experiment,
    paired_bootstrap,
    sample_fixed_n,
)
from v510_historical_structure_features_r1 import (
    ResearchError,
    assign_fold,
    audit_data_identity,
    build_features,
    complete_seasons,
    select_core_features,
)
from v510_historical_structure_model_r1 import metric_components, metric_summary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_referee_history_r8.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_referee_history_r8_rows.csv"
TOTAL_CLASSES = list(range(8))

AUX_ALIASES = {
    "referee": ["Referee", "referee"],
    "home_fouls": ["HF"], "away_fouls": ["AF"],
    "home_yellow": ["HY"], "away_yellow": ["AY"],
    "home_red": ["HR"], "away_red": ["AR"],
}


def _finite_or_nan(value: float | None) -> float:
    return np.nan if value is None else float(value)


def normalize_referee(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


@dataclass
class RefState:
    n: int = 0
    sum_total: float = 0.0
    sum_total_sq: float = 0.0
    draws: int = 0
    zero_zero: int = 0
    even: int = 0
    high_total: int = 0
    home_wins: int = 0
    fouls: float = 0.0
    fouls_n: int = 0
    yellows: float = 0.0
    yellows_n: int = 0
    reds: float = 0.0
    reds_n: int = 0
    recent: deque[tuple[int, int, float, float, float]] = field(default_factory=lambda: deque(maxlen=10))

    def update(self, hg: int, ag: int, fouls: float, yellows: float, reds: float) -> None:
        total = hg + ag
        self.n += 1
        self.sum_total += total
        self.sum_total_sq += total * total
        self.draws += int(hg == ag)
        self.zero_zero += int(total == 0)
        self.even += int(total % 2 == 0)
        self.high_total += int(total >= 4)
        self.home_wins += int(hg > ag)
        if np.isfinite(fouls):
            self.fouls += fouls; self.fouls_n += 1
        if np.isfinite(yellows):
            self.yellows += yellows; self.yellows_n += 1
        if np.isfinite(reds):
            self.reds += reds; self.reds_n += 1
        self.recent.append((hg, ag, fouls, yellows, reds))

    def features(self) -> dict[str, float]:
        f: dict[str, float] = {"ref_n_log": math.log1p(self.n)}
        if self.n:
            mean = self.sum_total / self.n
            var = max(0.0, self.sum_total_sq / self.n - mean * mean)
            f.update({
                "ref_mean_total": mean,
                "ref_std_total": math.sqrt(var),
                "ref_draw_rate": self.draws / self.n,
                "ref_zero_zero_rate": self.zero_zero / self.n,
                "ref_even_rate": self.even / self.n,
                "ref_high_total_rate": self.high_total / self.n,
                "ref_home_win_rate": self.home_wins / self.n,
            })
        else:
            for name in ("mean_total","std_total","draw_rate","zero_zero_rate","even_rate","high_total_rate","home_win_rate"):
                f[f"ref_{name}"] = np.nan
        f["ref_mean_fouls"] = self.fouls / self.fouls_n if self.fouls_n else np.nan
        f["ref_mean_yellows"] = self.yellows / self.yellows_n if self.yellows_n else np.nan
        f["ref_mean_reds"] = self.reds / self.reds_n if self.reds_n else np.nan
        rec = list(self.recent)
        if rec:
            hg = np.asarray([x[0] for x in rec], int); ag = np.asarray([x[1] for x in rec], int)
            totals = hg + ag
            f["ref_recent_mean_total"] = float(np.mean(totals))
            f["ref_recent_draw_rate"] = float(np.mean(hg == ag))
            f["ref_recent_zero_zero_rate"] = float(np.mean(totals == 0))
            f["ref_recent_even_rate"] = float(np.mean(totals % 2 == 0))
            f["ref_recent_high_total_rate"] = float(np.mean(totals >= 4))
            f["ref_recent_home_win_rate"] = float(np.mean(hg > ag))
            for j, name in ((2,"fouls"),(3,"yellows"),(4,"reds")):
                vals = np.asarray([x[j] for x in rec], float)
                f[f"ref_recent_mean_{name}"] = float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan
        else:
            for name in ("mean_total","draw_rate","zero_zero_rate","even_rate","high_total_rate","home_win_rate","mean_fouls","mean_yellows","mean_reds"):
                f[f"ref_recent_{name}"] = np.nan
        return f


def materialize_referee_aux(ledger: pd.DataFrame) -> pd.DataFrame:
    wanted: dict[str, dict[int, int]] = {}
    for idx, row in ledger[["source_file","row_number"]].iterrows():
        wanted.setdefault(str(row.source_file), {})[int(row.row_number)] = int(idx)
    records: list[dict[str, Any]] = []
    for source_file in sorted(wanted):
        path = ROOT / source_file
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle); headers = list(reader.fieldnames or [])
            resolved = {name: field_name(headers, aliases) for name, aliases in AUX_ALIASES.items()}
            for row_number, row in enumerate(reader, start=2):
                ledger_index = wanted[source_file].get(row_number)
                if ledger_index is None:
                    continue
                referee = str(row.get(resolved["referee"]) or "").strip() if resolved["referee"] else ""
                rec: dict[str, Any] = {"ledger_index": ledger_index, "referee_key": normalize_referee(referee)}
                for name in ("home_fouls","away_fouls","home_yellow","away_yellow","home_red","away_red"):
                    rec[name] = _finite_or_nan(float_value(row, resolved[name])) if resolved[name] else np.nan
                records.append(rec)
    if not records:
        raise ResearchError("R8 no referee auxiliary rows")
    return pd.DataFrame(records).sort_values("ledger_index").drop_duplicates("ledger_index").reset_index(drop=True)


def build_referee_features(ledger: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    aux = materialize_referee_aux(ledger)
    work = ledger.copy(); work["ledger_index"] = np.arange(len(work), dtype=int)
    work = work.merge(aux, on="ledger_index", how="left", validate="one_to_one")
    work["referee_key"] = work.referee_key.fillna("")
    work["date"] = pd.to_datetime(work.date_key, errors="raise")
    work = work.sort_values(["competition_id","date","home_team","away_team","source_file","row_number"]).reset_index(drop=True)
    work["row_id"] = np.arange(len(work), dtype=int)
    states: dict[tuple[str,str], RefState] = defaultdict(RefState)
    out: list[dict[str, Any]] = []
    updates = 0
    for date, day in work.groupby("date", sort=True):
        frozen: list[tuple[int, dict[str,float], bool]] = []
        for _, row in day.iterrows():
            ref = str(row.referee_key)
            key = (str(row.competition_id), ref)
            available = bool(ref)
            f = states[key].features() if available else RefState().features()
            f["ref_identity_available"] = float(available)
            f["ref_history_ready"] = float(available and states[key].n >= 5)
            frozen.append((int(row.row_id), f, available))
        for row_id, f, _ in frozen:
            out.append({"row_id": row_id, **f})
        for _, row in day.iterrows():
            ref = str(row.referee_key)
            if not ref:
                continue
            key = (str(row.competition_id), ref)
            hg, ag = int(row.home_goals_90), int(row.away_goals_90)
            fouls = float(row.home_fouls + row.away_fouls) if np.isfinite(row.home_fouls) and np.isfinite(row.away_fouls) else np.nan
            yellows = float(row.home_yellow + row.away_yellow) if np.isfinite(row.home_yellow) and np.isfinite(row.away_yellow) else np.nan
            reds = float(row.home_red + row.away_red) if np.isfinite(row.home_red) and np.isfinite(row.away_red) else np.nan
            states[key].update(hg, ag, fouls, yellows, reds); updates += 1
    frame = pd.DataFrame(out).sort_values("row_id").reset_index(drop=True)
    if len(frame) != len(work) or frame.row_id.nunique() != len(work):
        raise ResearchError("R8 referee feature row identity failure")
    coverage = {
        "ledger_rows": int(len(work)),
        "rows_with_referee_identity": int(frame.ref_identity_available.eq(1.0).sum()),
        "rows_referee_history_ready_ge5": int(frame.ref_history_ready.eq(1.0).sum()),
        "referee_updates_applied": int(updates),
    }
    return frame, coverage


def parity_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y, int); p = np.asarray(p, float)
    p_even = np.clip(p[:,0], 1e-15, 1-1e-15); y_even = (y == 0).astype(int)
    return {
        "logloss": -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0)),
        "brier": (p_even-y_even)**2 + ((1-p_even)-(1-y_even))**2,
    }


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = build_features(raw)
    referee, coverage = build_referee_features(raw)
    base = base.merge(referee, on="row_id", how="left", validate="one_to_one")
    base = add_identity_key(base); base = attach_exact_total(base, raw)
    base = base.merge(materialize_market(raw), on="identity_key", how="left", validate="one_to_one")
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    pos = int(exp["test_position_zero_based"]); latest = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos >= latest:
        raise ResearchError("R8 must reuse PR197 non-latest fixed500")
    base["split"] = assign_fold(base, seasons, pos)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    sample = base.merge(sample_base[KEYS + ["match_identity","identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("R8 fixed500 reconstruction mismatch")

    compact = [
        "ref_n_log","ref_mean_total","ref_std_total","ref_draw_rate","ref_zero_zero_rate",
        "ref_even_rate","ref_high_total_rate","ref_home_win_rate",
        "ref_recent_mean_total","ref_recent_draw_rate","ref_recent_zero_zero_rate",
        "ref_recent_even_rate","ref_recent_high_total_rate","ref_recent_home_win_rate",
    ]
    full = compact + [
        "ref_mean_fouls","ref_mean_yellows","ref_mean_reds",
        "ref_recent_mean_fouls","ref_recent_mean_yellows","ref_recent_mean_reds",
    ]
    ready = base.ref_history_ready.eq(1.0)
    sample_ready = sample.ref_history_ready.eq(1.0)
    ou = base[MARKET_OU].notna().all(axis=1)
    sample_ou = sample[MARKET_OU].notna().all(axis=1)
    cohorts = {
        "ref_ready": (base[ready].copy(), sample[sample_ready].copy()),
        "ref_ready_ou": (base[ready & ou].copy(), sample[sample_ready & sample_ou].copy()),
    }
    outputs: dict[str, Any] = {}
    row_export = sample[KEYS + ["match_identity","identity_hash","exact_total","exact_parity","ref_identity_available","ref_history_ready"] + MARKET_OU + full].copy()

    for ci, (cohort_name, (fold_c, sample_c)) in enumerate(cohorts.items()):
        if len(sample_c) < 60:
            raise ResearchError(f"R8 cohort too small {cohort_name}: {len(sample_c)}")
        train = fold_c[fold_c.split == "train"].copy(); policy = fold_c[fold_c.split == "policy"].copy(); fit = fold_c[fold_c.split.isin(["train","policy"])].copy()
        if min(len(train), len(policy)) < 100:
            raise ResearchError(f"R8 fit cohort too small {cohort_name}")
        packs: dict[str, list[str]] = {"core":core, "core_plus_ref_compact":core+compact, "core_plus_ref_full":core+full}
        if cohort_name == "ref_ready_ou":
            packs = {
                "core":core,
                "core_plus_single_ou":core+MARKET_OU,
                "core_plus_ref_compact":core+compact,
                "core_plus_single_ou_ref_compact":core+MARKET_OU+compact,
                "core_plus_ref_full":core+full,
                "core_plus_single_ou_ref_full":core+MARKET_OU+full,
            }
        yT = sample_c.total_class.to_numpy(int); yP = sample_c.exact_parity.to_numpy(int)
        tprob: dict[str,np.ndarray] = {}; pprob: dict[str,np.ndarray] = {}; tm: dict[str,Any] = {}; pm: dict[str,Any] = {}; tc: dict[str,Any] = {}; pc: dict[str,Any] = {}; receipts: dict[str,Any] = {}
        for name, feats in packs.items():
            pt, tr = fit_total(fit, train, policy, sample_c, feats, config)
            pp, pr = fit_parity(fit, train, policy, sample_c, feats, config)
            tprob[name] = pt; pprob[name] = pp; receipts[name] = {"total":tr,"parity":pr}
            c = metric_components(yT, pt, TOTAL_CLASSES); tc[name] = c; tm[name] = metric_summary(c)
            pc[name] = parity_components(yP, pp); pm[name] = parity_metrics(yP, pp)
        boot: dict[str,Any] = {"direct_t_vs_core":{},"parity_vs_core":{}}
        for k, challenger in enumerate(list(packs)[1:]):
            boot["direct_t_vs_core"][challenger] = {m:paired_bootstrap(tc[challenger][m].to_numpy(float)-tc["core"][m].to_numpy(float),5000,920100+ci*1000+k*20+i) for i,m in enumerate(("logloss","brier","rps"))}
            boot["parity_vs_core"][challenger] = {m:paired_bootstrap(pc[challenger][m]-pc["core"][m],5000,920500+ci*1000+k*20+i) for i,m in enumerate(("logloss","brier"))}
        if cohort_name == "ref_ready_ou":
            boot["direct_t_ref_plus_ou_vs_ou"] = {}; boot["parity_ref_plus_ou_vs_ou"] = {}
            for k, challenger in enumerate(("core_plus_single_ou_ref_compact","core_plus_single_ou_ref_full")):
                boot["direct_t_ref_plus_ou_vs_ou"][challenger] = {m:paired_bootstrap(tc[challenger][m].to_numpy(float)-tc["core_plus_single_ou"][m].to_numpy(float),5000,923100+k*20+i) for i,m in enumerate(("logloss","brier","rps"))}
                boot["parity_ref_plus_ou_vs_ou"][challenger] = {m:paired_bootstrap(pc[challenger][m]-pc["core_plus_single_ou"][m],5000,923500+k*20+i) for i,m in enumerate(("logloss","brier"))}
        outputs[cohort_name] = {"n":int(len(sample_c)),"actual_draws":int(np.sum(sample_c.goal_difference.to_numpy(int)==0)),"actual_even":int(np.sum(yP==0)),"actual_odd":int(np.sum(yP==1)),"direct_t":tm,"parity":pm,"bootstrap":boot,"receipts":receipts}
        idx = sample_c.index.to_list()
        for name, p in tprob.items():
            mp = dict(zip(idx,np.argmax(p,axis=1))); row_export[f"{cohort_name}_{name}_pred_T"] = [mp.get(i,np.nan) for i in row_export.index]
        for name, p in pprob.items():
            mp = dict(zip(idx,p[:,0])); row_export[f"{cohort_name}_{name}_p_even"] = [mp.get(i,np.nan) for i in row_export.index]

    ouc = outputs["ref_ready_ou"]
    candidates = ("core_plus_single_ou_ref_compact","core_plus_single_ou_ref_full")
    best_t = min(candidates,key=lambda n:ouc["direct_t"][n]["logloss"])
    best_p = min(candidates,key=lambda n:ouc["parity"][n]["log_loss"])
    base_t = ouc["direct_t"]["core_plus_single_ou"]["logloss"]; base_p = ouc["parity"]["core_plus_single_ou"]["log_loss"]
    bt = ouc["bootstrap"]["direct_t_ref_plus_ou_vs_ou"][best_t]["logloss"]
    bp = ouc["bootstrap"]["parity_ref_plus_ou_vs_ou"][best_p]["logloss"]
    t_stable = ouc["direct_t"][best_t]["logloss"] < base_t and bt["p95"] <= 0.0
    p_stable = ouc["parity"][best_p]["log_loss"] < base_p and bp["p95"] <= 0.0
    if t_stable and p_stable:
        verdict = "REFEREE_HISTORY_ADDS_STABLE_T_AND_PARITY_SIGNAL_OVER_OU"
    elif t_stable:
        verdict = "REFEREE_HISTORY_ADDS_STABLE_T_SIGNAL_ONLY"
    elif p_stable:
        verdict = "REFEREE_HISTORY_ADDS_STABLE_PARITY_SIGNAL_ONLY"
    elif ouc["direct_t"][best_t]["logloss"] < base_t or ouc["parity"][best_p]["log_loss"] < base_p:
        verdict = "REFEREE_HISTORY_DIRECTIONAL_ONLY"
    else:
        verdict = "REFEREE_HISTORY_DOES_NOT_IMPROVE_OVER_OU"

    result = {
        "schema_version":"FIXED500_REFEREE_HISTORY_R8",
        "status":"COMPLETED_RESEARCH_ONLY",
        "scientific_verdict":verdict,
        "question":"Does the assigned referee's prior match history explain T or T parity beyond core+OU on the same fixed500?",
        "sample":{"parent_fixed500_n":500,"parent_fixed500_identity_sha256":sample_hash,"new_sample_consumed":False,"latest_position4_confirmation_opened":False},
        "coverage":coverage,
        "feature_contract":{"compact_features":compact,"full_features":full,"minimum_prior_referee_matches":5,"state_key":"competition_id + normalized referee identity","same_day_freeze_before_updates":True,"current_match_result_used_as_current_feature":False,"only_prior_referee_matches_update_state":True,"model_family_unchanged":True,"manual_threshold":False},
        "cohorts":outputs,
        "best_on_ref_ready_ou":{"direct_t":best_t,"parity":best_p},
        "data_identity":data_identity,"excluded_incomplete_latest_seasons":excluded,
        "interpretation_guard":{"retrospective_same_fixed500_already_viewed":True,"referee_assignment_archive_has_no_original_observation_timestamp":True,"formal_PIT_claim":False,"can_authorize_promotion":False,"no_current_match_result_leakage":True},
        "governance":{"formal_weight":0,"provider_requests":0,"new_data_collection":False,"new_sample_consumed":False,"latest_position4_confirmation_opened":False,"post_result_threshold_search":False,"formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,"current_mutation":False,"main_mutation":False},
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); row_export.to_csv(ROWS_OUT,index=False)
    return result


def main() -> None:
    x=run(); print(json.dumps({"verdict":x["scientific_verdict"],"sample":x["sample"],"coverage":x["coverage"],"best":x["best_on_ref_ready_ou"],"cohorts":x["cohorts"]},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
