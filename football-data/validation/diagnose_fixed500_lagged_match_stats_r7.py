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
OUT = ROOT / "manifests" / "fixed500_lagged_match_stats_r7.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_lagged_match_stats_r7_rows.csv"
TOTAL_CLASSES = list(range(8))

STAT_ALIASES = {
    "home_shots": ["HS"], "away_shots": ["AS"],
    "home_sot": ["HST"], "away_sot": ["AST"],
    "home_corners": ["HC"], "away_corners": ["AC"],
    "home_fouls": ["HF"], "away_fouls": ["AF"],
    "home_yellow": ["HY"], "away_yellow": ["AY"],
    "home_red": ["HR"], "away_red": ["AR"],
}
CORE_REQUIRED = ("home_shots", "away_shots", "home_sot", "away_sot", "home_corners", "away_corners")


@dataclass
class StatState:
    n: int = 0
    shots_for: float = 0.0
    shots_against: float = 0.0
    sot_for: float = 0.0
    sot_against: float = 0.0
    corners_for: float = 0.0
    corners_against: float = 0.0
    fouls_for: float = 0.0
    fouls_against: float = 0.0
    yellow_for: float = 0.0
    yellow_against: float = 0.0
    red_for: float = 0.0
    red_against: float = 0.0
    recent: deque[tuple[float, ...]] = field(default_factory=lambda: deque(maxlen=10))

    def update(self, values: dict[str, float]) -> None:
        self.n += 1
        names = (
            "shots_for", "shots_against", "sot_for", "sot_against",
            "corners_for", "corners_against", "fouls_for", "fouls_against",
            "yellow_for", "yellow_against", "red_for", "red_against",
        )
        for name in names:
            val = float(values.get(name, np.nan))
            if np.isfinite(val):
                setattr(self, name, getattr(self, name) + val)
        self.recent.append(tuple(float(values.get(name, np.nan)) for name in names))

    def features(self, prefix: str) -> dict[str, float]:
        out: dict[str, float] = {f"{prefix}_n_log": math.log1p(self.n)}
        names = (
            "shots_for", "shots_against", "sot_for", "sot_against",
            "corners_for", "corners_against", "fouls_for", "fouls_against",
            "yellow_for", "yellow_against", "red_for", "red_against",
        )
        if self.n:
            for name in names:
                out[f"{prefix}_{name}"] = getattr(self, name) / self.n
            out[f"{prefix}_shot_accuracy_for"] = self.sot_for / self.shots_for if self.shots_for > 0 else np.nan
            out[f"{prefix}_shot_accuracy_against"] = self.sot_against / self.shots_against if self.shots_against > 0 else np.nan
        else:
            for name in names:
                out[f"{prefix}_{name}"] = np.nan
            out[f"{prefix}_shot_accuracy_for"] = np.nan
            out[f"{prefix}_shot_accuracy_against"] = np.nan

        rec = list(self.recent)
        if rec:
            a = np.asarray(rec, dtype=float)
            for j, name in enumerate(names):
                col = a[:, j]
                out[f"{prefix}_recent_{name}"] = float(np.nanmean(col)) if np.isfinite(col).any() else np.nan
            shots_for = np.nansum(a[:, 0]); sot_for = np.nansum(a[:, 2])
            shots_against = np.nansum(a[:, 1]); sot_against = np.nansum(a[:, 3])
            out[f"{prefix}_recent_shot_accuracy_for"] = float(sot_for / shots_for) if shots_for > 0 else np.nan
            out[f"{prefix}_recent_shot_accuracy_against"] = float(sot_against / shots_against) if shots_against > 0 else np.nan
        else:
            for name in names:
                out[f"{prefix}_recent_{name}"] = np.nan
            out[f"{prefix}_recent_shot_accuracy_for"] = np.nan
            out[f"{prefix}_recent_shot_accuracy_against"] = np.nan
        return out


def materialize_current_match_stats(ledger: pd.DataFrame) -> pd.DataFrame:
    wanted: dict[str, dict[int, int]] = {}
    for idx, row in ledger[["source_file", "row_number"]].iterrows():
        wanted.setdefault(str(row.source_file), {})[int(row.row_number)] = int(idx)
    records: list[dict[str, Any]] = []
    for source_file in sorted(wanted):
        path = ROOT / source_file
        if not path.is_file():
            continue
        row_map = wanted[source_file]
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            resolved = {name: field_name(headers, aliases) for name, aliases in STAT_ALIASES.items()}
            for row_number, row in enumerate(reader, start=2):
                ledger_index = row_map.get(row_number)
                if ledger_index is None:
                    continue
                rec: dict[str, Any] = {"ledger_index": ledger_index}
                for name, field_name_resolved in resolved.items():
                    rec[name] = float_value(row, field_name_resolved) if field_name_resolved else np.nan
                rec["core_stats_complete"] = bool(all(np.isfinite(rec[name]) for name in CORE_REQUIRED))
                records.append(rec)
    if not records:
        raise ResearchError("R7 could not materialize any match statistics")
    result = pd.DataFrame(records).sort_values("ledger_index").drop_duplicates("ledger_index")
    return result.reset_index(drop=True)


def perspective_values(row: pd.Series, home_perspective: bool) -> dict[str, float]:
    if home_perspective:
        return {
            "shots_for": row.home_shots, "shots_against": row.away_shots,
            "sot_for": row.home_sot, "sot_against": row.away_sot,
            "corners_for": row.home_corners, "corners_against": row.away_corners,
            "fouls_for": row.home_fouls, "fouls_against": row.away_fouls,
            "yellow_for": row.home_yellow, "yellow_against": row.away_yellow,
            "red_for": row.home_red, "red_against": row.away_red,
        }
    return {
        "shots_for": row.away_shots, "shots_against": row.home_shots,
        "sot_for": row.away_sot, "sot_against": row.home_sot,
        "corners_for": row.away_corners, "corners_against": row.home_corners,
        "fouls_for": row.away_fouls, "fouls_against": row.home_fouls,
        "yellow_for": row.away_yellow, "yellow_against": row.home_yellow,
        "red_for": row.away_red, "red_against": row.home_red,
    }


def avg2(a: float, b: float) -> float:
    if np.isfinite(a) and np.isfinite(b):
        return float((a + b) / 2.0)
    return np.nan


def add_match_proxies(f: dict[str, float]) -> None:
    for scope, hp, ap in (
        ("all", "s_home_all", "s_away_all"),
        ("venue", "s_home_venue", "s_away_venue"),
    ):
        for recent in (False, True):
            r = "recent_" if recent else ""
            tag = "recent_" if recent else ""
            home_shots = avg2(f.get(f"{hp}_{r}shots_for", np.nan), f.get(f"{ap}_{r}shots_against", np.nan))
            away_shots = avg2(f.get(f"{ap}_{r}shots_for", np.nan), f.get(f"{hp}_{r}shots_against", np.nan))
            home_sot = avg2(f.get(f"{hp}_{r}sot_for", np.nan), f.get(f"{ap}_{r}sot_against", np.nan))
            away_sot = avg2(f.get(f"{ap}_{r}sot_for", np.nan), f.get(f"{hp}_{r}sot_against", np.nan))
            home_corners = avg2(f.get(f"{hp}_{r}corners_for", np.nan), f.get(f"{ap}_{r}corners_against", np.nan))
            away_corners = avg2(f.get(f"{ap}_{r}corners_for", np.nan), f.get(f"{hp}_{r}corners_against", np.nan))
            f[f"s_proxy_{scope}_{tag}home_shots"] = home_shots
            f[f"s_proxy_{scope}_{tag}away_shots"] = away_shots
            f[f"s_proxy_{scope}_{tag}total_shots"] = home_shots + away_shots if np.isfinite(home_shots) and np.isfinite(away_shots) else np.nan
            f[f"s_proxy_{scope}_{tag}home_sot"] = home_sot
            f[f"s_proxy_{scope}_{tag}away_sot"] = away_sot
            f[f"s_proxy_{scope}_{tag}total_sot"] = home_sot + away_sot if np.isfinite(home_sot) and np.isfinite(away_sot) else np.nan
            f[f"s_proxy_{scope}_{tag}home_corners"] = home_corners
            f[f"s_proxy_{scope}_{tag}away_corners"] = away_corners
            f[f"s_proxy_{scope}_{tag}total_corners"] = home_corners + away_corners if np.isfinite(home_corners) and np.isfinite(away_corners) else np.nan


def build_lagged_stat_features(ledger: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    current = materialize_current_match_stats(ledger)
    work = ledger.copy()
    work["ledger_index"] = np.arange(len(work), dtype=int)
    work = work.merge(current, on="ledger_index", how="left", validate="one_to_one")
    work["core_stats_complete"] = work["core_stats_complete"].fillna(False)
    work["date"] = pd.to_datetime(work.date_key, errors="raise")
    work = work.sort_values(["competition_id", "date", "home_team", "away_team", "source_file", "row_number"]).reset_index(drop=True)
    work["row_id"] = np.arange(len(work), dtype=int)

    output: list[dict[str, Any]] = []
    updates = 0
    for competition, matches in work.groupby("competition_id", sort=True):
        all_state: dict[str, StatState] = defaultdict(StatState)
        home_state: dict[str, StatState] = defaultdict(StatState)
        away_state: dict[str, StatState] = defaultdict(StatState)
        for date, day in matches.groupby("date", sort=True):
            frozen: list[tuple[int, dict[str, float]]] = []
            for _, row in day.iterrows():
                home, away = str(row.home_team), str(row.away_team)
                f: dict[str, float] = {}
                f.update(all_state[home].features("s_home_all"))
                f.update(all_state[away].features("s_away_all"))
                f.update(home_state[home].features("s_home_venue"))
                f.update(away_state[away].features("s_away_venue"))
                add_match_proxies(f)
                f["s_history_ready"] = float(all_state[home].n >= 5 and all_state[away].n >= 5)
                frozen.append((int(row.row_id), f))
            for row_id, f in frozen:
                output.append({"row_id": row_id, **f})
            for _, row in day.iterrows():
                if not bool(row.core_stats_complete):
                    continue
                home, away = str(row.home_team), str(row.away_team)
                hv = perspective_values(row, True); av = perspective_values(row, False)
                all_state[home].update(hv); all_state[away].update(av)
                home_state[home].update(hv); away_state[away].update(av)
                updates += 1

    frame = pd.DataFrame(output).sort_values("row_id").reset_index(drop=True)
    if len(frame) != len(work) or frame.row_id.nunique() != len(work):
        raise ResearchError("R7 lagged-stat row identity failure")
    coverage = {
        "ledger_rows": int(len(work)),
        "rows_with_current_core_stats": int(work.core_stats_complete.sum()),
        "historical_updates_applied": int(updates),
        "rows_history_ready": int(frame.s_history_ready.eq(1.0).sum()),
    }
    return frame, coverage


def parity_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    y = np.asarray(y, int); p = np.asarray(p, float)
    p_even = np.clip(p[:, 0], 1e-15, 1-1e-15); y_even = (y == 0).astype(int)
    return {
        "logloss": -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0)),
        "brier": (p_even-y_even)**2 + ((1-p_even)-(1-y_even))**2,
    }


def run() -> dict[str, Any]:
    exp = load_experiment(); config = load_config()
    raw = pd.read_csv(ROOT / str(config["input_ledger"]))
    data_identity = audit_data_identity(raw, config)
    base = build_features(raw)
    lagged, stat_coverage = build_lagged_stat_features(raw)
    base = base.merge(lagged, on="row_id", how="left", validate="one_to_one")
    base = add_identity_key(base)
    base = attach_exact_total(base, raw)
    base = base.merge(materialize_market(raw), on="identity_key", how="left", validate="one_to_one")
    core = select_core_features(base)
    seasons, excluded = complete_seasons(raw, config)
    pos = int(exp["test_position_zero_based"])
    latest = max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos >= latest:
        raise ResearchError("R7 must reuse PR197 non-latest fixed500")
    base["split"] = assign_fold(base, seasons, pos)
    sample_base, sample_hash = sample_fixed_n(base[base.split == "test"].copy(), int(exp["sample_n"]))
    sample = base.merge(sample_base[KEYS + ["match_identity", "identity_hash"]], on=KEYS, how="inner", validate="one_to_one")
    if len(sample) != 500:
        raise ResearchError("R7 fixed500 reconstruction mismatch")

    compact = [
        "s_proxy_all_total_shots", "s_proxy_all_total_sot", "s_proxy_all_total_corners",
        "s_proxy_all_home_sot", "s_proxy_all_away_sot",
        "s_proxy_all_recent_total_shots", "s_proxy_all_recent_total_sot", "s_proxy_all_recent_total_corners",
        "s_proxy_all_recent_home_sot", "s_proxy_all_recent_away_sot",
        "s_proxy_venue_total_shots", "s_proxy_venue_total_sot", "s_proxy_venue_total_corners",
        "s_proxy_venue_recent_total_shots", "s_proxy_venue_recent_total_sot", "s_proxy_venue_recent_total_corners",
        "s_home_all_shot_accuracy_for", "s_home_all_shot_accuracy_against",
        "s_away_all_shot_accuracy_for", "s_away_all_shot_accuracy_against",
        "s_home_all_recent_shot_accuracy_for", "s_home_all_recent_shot_accuracy_against",
        "s_away_all_recent_shot_accuracy_for", "s_away_all_recent_shot_accuracy_against",
    ]
    full = sorted([
        c for c in lagged.columns
        if c != "row_id" and c != "s_history_ready" and (
            c.startswith("s_proxy_")
            or any(token in c for token in ("shots_", "sot_", "corners_", "shot_accuracy_", "fouls_", "yellow_", "red_"))
        )
    ])
    missing = sorted(set(compact) - set(base.columns))
    if missing:
        raise ResearchError(f"R7 compact feature fields missing: {missing}")

    ready = base.s_history_ready.eq(1.0)
    sample_ready = sample.s_history_ready.eq(1.0)
    ou = base[MARKET_OU].notna().all(axis=1)
    sample_ou = sample[MARKET_OU].notna().all(axis=1)
    fit_mask = base.split.isin(["train", "policy"])

    cohorts = {
        "stats_ready": (base[ready].copy(), sample[sample_ready].copy()),
        "stats_ready_ou": (base[ready & ou].copy(), sample[sample_ready & sample_ou].copy()),
    }
    result_cohorts: dict[str, Any] = {}
    row_export = sample[KEYS + ["match_identity", "identity_hash", "exact_total", "exact_parity", "s_history_ready"] + MARKET_OU + compact].copy()

    for cohort_index, (cohort_name, (fold_c, sample_c)) in enumerate(cohorts.items()):
        if len(sample_c) < 80:
            raise ResearchError(f"R7 cohort too small {cohort_name}: {len(sample_c)}")
        train_c = fold_c[fold_c.split == "train"].copy(); policy_c = fold_c[fold_c.split == "policy"].copy(); fit_c = fold_c[fold_c.split.isin(["train", "policy"])].copy()
        if min(len(train_c), len(policy_c)) < 100:
            raise ResearchError(f"R7 fit cohort too small {cohort_name}")
        packs: dict[str, list[str]] = {
            "core": core,
            "core_plus_compact_stats": core + compact,
            "core_plus_full_stats": core + full,
        }
        if cohort_name == "stats_ready_ou":
            packs = {
                "core": core,
                "core_plus_single_ou": core + MARKET_OU,
                "core_plus_compact_stats": core + compact,
                "core_plus_single_ou_compact_stats": core + MARKET_OU + compact,
                "core_plus_full_stats": core + full,
                "core_plus_single_ou_full_stats": core + MARKET_OU + full,
            }

        total_probs: dict[str, np.ndarray] = {}; parity_probs: dict[str, np.ndarray] = {}; receipts: dict[str, Any] = {}
        total_metrics: dict[str, Any] = {}; parity_out: dict[str, Any] = {}; tcomp: dict[str, Any] = {}; pcomp: dict[str, Any] = {}
        yT = sample_c.total_class.to_numpy(int); yP = sample_c.exact_parity.to_numpy(int)
        for name, feats in packs.items():
            pT, tr = fit_total(fit_c, train_c, policy_c, sample_c, feats, config)
            pP, pr = fit_parity(fit_c, train_c, policy_c, sample_c, feats, config)
            total_probs[name] = pT; parity_probs[name] = pP; receipts[name] = {"total": tr, "parity": pr}
            tc = metric_components(yT, pT, TOTAL_CLASSES); tcomp[name] = tc; total_metrics[name] = metric_summary(tc)
            pcomp[name] = parity_components(yP, pP); parity_out[name] = parity_metrics(yP, pP)

        boot: dict[str, Any] = {"direct_t_vs_core": {}, "parity_vs_core": {}}
        for k, challenger in enumerate(list(packs)[1:]):
            boot["direct_t_vs_core"][challenger] = {
                m: paired_bootstrap(tcomp[challenger][m].to_numpy(float) - tcomp["core"][m].to_numpy(float), 5000, 910100 + cohort_index*1000 + k*20 + i)
                for i, m in enumerate(("logloss", "brier", "rps"))
            }
            boot["parity_vs_core"][challenger] = {
                m: paired_bootstrap(pcomp[challenger][m] - pcomp["core"][m], 5000, 910500 + cohort_index*1000 + k*20 + i)
                for i, m in enumerate(("logloss", "brier"))
            }
        if cohort_name == "stats_ready_ou":
            boot["direct_t_stats_plus_ou_vs_ou"] = {}
            boot["parity_stats_plus_ou_vs_ou"] = {}
            for k, challenger in enumerate(("core_plus_single_ou_compact_stats", "core_plus_single_ou_full_stats")):
                boot["direct_t_stats_plus_ou_vs_ou"][challenger] = {
                    m: paired_bootstrap(tcomp[challenger][m].to_numpy(float)-tcomp["core_plus_single_ou"][m].to_numpy(float),5000,913100+k*20+i)
                    for i,m in enumerate(("logloss","brier","rps"))
                }
                boot["parity_stats_plus_ou_vs_ou"][challenger] = {
                    m: paired_bootstrap(pcomp[challenger][m]-pcomp["core_plus_single_ou"][m],5000,913500+k*20+i)
                    for i,m in enumerate(("logloss","brier"))
                }

        result_cohorts[cohort_name] = {
            "n": int(len(sample_c)), "actual_draws": int(np.sum(sample_c.goal_difference.to_numpy(int)==0)),
            "actual_even": int(np.sum(yP==0)), "actual_odd": int(np.sum(yP==1)),
            "direct_t": total_metrics, "parity": parity_out, "bootstrap": boot, "receipts": receipts,
        }
        idx = sample_c.index.to_list()
        for name, p in total_probs.items():
            mp = dict(zip(idx, np.argmax(p, axis=1)))
            row_export[f"{cohort_name}_{name}_pred_T"] = [mp.get(i, np.nan) for i in row_export.index]
        for name, p in parity_probs.items():
            mp = dict(zip(idx, p[:,0]))
            row_export[f"{cohort_name}_{name}_p_even"] = [mp.get(i, np.nan) for i in row_export.index]

    ouc = result_cohorts["stats_ready_ou"]
    ou_base_t = ouc["direct_t"]["core_plus_single_ou"]["logloss"]
    candidate_names = ("core_plus_single_ou_compact_stats", "core_plus_single_ou_full_stats")
    best_t = min(candidate_names, key=lambda n: ouc["direct_t"][n]["logloss"])
    best_p = min(candidate_names, key=lambda n: ouc["parity"][n]["log_loss"])
    t_boot_key = ouc["bootstrap"]["direct_t_stats_plus_ou_vs_ou"][best_t]["logloss"]
    p_boot_key = ouc["bootstrap"]["parity_stats_plus_ou_vs_ou"][best_p]["logloss"]
    t_stable = ouc["direct_t"][best_t]["logloss"] < ou_base_t and t_boot_key["p95"] <= 0.0
    p_base_ll = ouc["parity"]["core_plus_single_ou"]["log_loss"]
    p_stable = ouc["parity"][best_p]["log_loss"] < p_base_ll and p_boot_key["p95"] <= 0.0
    if t_stable and p_stable:
        verdict = "LAGGED_MATCH_STATS_ADD_STABLE_T_AND_PARITY_SIGNAL_OVER_OU"
    elif t_stable:
        verdict = "LAGGED_MATCH_STATS_ADD_STABLE_T_SIGNAL_ONLY"
    elif p_stable:
        verdict = "LAGGED_MATCH_STATS_ADD_STABLE_PARITY_SIGNAL_ONLY"
    elif ouc["direct_t"][best_t]["logloss"] < ou_base_t or ouc["parity"][best_p]["log_loss"] < p_base_ll:
        verdict = "LAGGED_MATCH_STATS_DIRECTIONAL_ONLY"
    else:
        verdict = "LAGGED_MATCH_STATS_DO_NOT_IMPROVE_OVER_OU"

    result = {
        "schema_version": "FIXED500_LAGGED_MATCH_STATS_R7",
        "status": "COMPLETED_RESEARCH_ONLY",
        "scientific_verdict": verdict,
        "question": "Do pre-match lagged shots/SOT/corners and secondary match-stat histories explain T or T parity beyond existing core and OU on the same fixed500?",
        "sample": {
            "parent_fixed500_n": 500, "parent_fixed500_identity_sha256": sample_hash,
            "new_sample_consumed": False, "latest_position4_confirmation_opened": False,
        },
        "stat_coverage": stat_coverage,
        "feature_contract": {
            "compact_feature_count": len(compact), "full_feature_count": len(full),
            "compact_features": compact,
            "same_day_freeze_before_updates": True,
            "current_match_stats_used_as_current_match_features": False,
            "only_prior_complete_match_stats_update_state": True,
            "minimum_prior_stats_matches_per_team": 5,
            "model_family_unchanged": True,
            "manual_threshold": False,
        },
        "cohorts": result_cohorts,
        "best_on_stats_ready_ou": {"direct_t": best_t, "parity": best_p},
        "data_identity": data_identity,
        "excluded_incomplete_latest_seasons": excluded,
        "interpretation_guard": {
            "retrospective_same_fixed500_already_viewed": True,
            "formal_PIT_claim": False,
            "can_authorize_promotion": False,
            "no_current_match_stat_leakage": True,
        },
        "governance": {
            "formal_weight": 0, "provider_requests": 0, "new_data_collection": False,
            "new_sample_consumed": False, "latest_position4_confirmation_opened": False,
            "post_result_threshold_search": False, "formal_model_mutation": False,
            "formal_data_mutation": False, "formal_config_mutation": False,
            "current_mutation": False, "main_mutation": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    row_export.to_csv(ROWS_OUT, index=False)
    return result


def main() -> None:
    x = run()
    print(json.dumps({
        "verdict": x["scientific_verdict"], "sample": x["sample"], "coverage": x["stat_coverage"],
        "best": x["best_on_stats_ready_ou"], "cohorts": x["cohorts"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
