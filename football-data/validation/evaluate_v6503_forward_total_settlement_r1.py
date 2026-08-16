#!/usr/bin/env python3
"""Prospective settlement of frozen V6.50.3 O/U -> Direct-T events.

The prediction ledgers were frozen before the matches. This evaluator uses only result
records already present in the repository; it performs no provider/network request and
no outcome-driven parameter, threshold, blend, feature, or sample search.

Primary scientific cohort: fixtures that can be joined deterministically to the
market-first result inbox and whose prediction/market/result timestamps pass the strict
temporal ordering audit.

Supplementary cohort: the primary cohort plus deterministic exact date/team matches from
the rebuilt historical score ledger. Historical fallback labels do not carry an original
result-observed timestamp and therefore cannot strengthen the strict prospective timing
claim; they are reported separately.

formal_weight=0. This evaluator cannot authorize promotion or mutate formal assets.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EVENTS_T = ROOT / "forward" / "v6_ou_kl_direct_total_events_v6503.json"
EVENTS_JOINT = ROOT / "forward" / "v6_ou_result_joint_matrix_events_v6505.json"
RESULTS = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
HIST_LEDGER = ROOT / "manifests" / "v510_existing_score_market_pit_ledger_r1_rows.csv"
OUT = ROOT / "manifests" / "v6503_forward_total_settlement_r1.json"
ROWS_OUT = ROOT / "manifests" / "v6503_forward_total_settlement_r1_rows.csv"
TOTAL_KEYS = ["0", "1", "2", "3", "4", "5", "6", "7+"]
EPS = 1e-15


class SettlementError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SettlementError(f"missing input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SettlementError(f"JSON root not object: {path.relative_to(ROOT)}")
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_team(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum())


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def iso_second(value: Any) -> str:
    dt = parse_dt(value)
    if dt is None:
        return ""
    return dt.replace(microsecond=0).isoformat()


def date_key(value: Any) -> str:
    dt = parse_dt(value)
    return dt.date().isoformat() if dt is not None else ""


def fixture_key(comp: Any, kickoff: Any, home: Any, away: Any) -> tuple[str, str, str, str]:
    return (str(comp or ""), iso_second(kickoff), norm_team(home), norm_team(away))


def date_team_key(comp: Any, kickoff: Any, home: Any, away: Any) -> tuple[str, str, str, str]:
    return (str(comp or ""), date_key(kickoff), norm_team(home), norm_team(away))


def load_primary_results() -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], list[dict[str, Any]]]:
    root = load_json(RESULTS)
    mapping: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for row in root.get("results", []):
        key = fixture_key(row.get("competition_id"), row.get("kickoff_at"), row.get("home_team"), row.get("away_team"))
        if not all(key):
            continue
        if key in mapping:
            duplicates.append({"key": list(key), "match_ids": [mapping[key].get("match_id"), row.get("match_id")]})
            mapping.pop(key, None)
            continue
        if any(d.get("key") == list(key) for d in duplicates):
            continue
        mapping[key] = row
    return mapping, duplicates


def load_historical_results() -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], int]:
    if not HIST_LEDGER.is_file():
        raise SettlementError("historical audit ledger missing; run audit_v510_existing_score_market_pit_ledger_r1.py first")
    buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    with HIST_LEDGER.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (
                str(row.get("competition_id") or ""),
                str(row.get("date_key") or ""),
                norm_team(row.get("home_team")),
                norm_team(row.get("away_team")),
            )
            if all(key):
                buckets.setdefault(key, []).append(row)
    mapping: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    ambiguous = 0
    for key, rows in buckets.items():
        unique_scores = {(r.get("home_goals_90"), r.get("away_goals_90")) for r in rows}
        if len(unique_scores) != 1:
            ambiguous += 1
            continue
        mapping[key] = rows[0]
    return mapping, ambiguous


def probability_vector(d: dict[str, Any]) -> np.ndarray:
    p = np.asarray([float(d[k]) for k in TOTAL_KEYS], dtype=float)
    if not np.all(np.isfinite(p)) or np.any(p < 0):
        raise SettlementError("invalid total probability vector")
    mass = float(p.sum())
    if abs(mass - 1.0) > 1e-8:
        raise SettlementError(f"total probability mass error {mass}")
    return p / mass


def metric_components(y: np.ndarray, p: np.ndarray) -> dict[str, np.ndarray]:
    n = len(y)
    ll = -np.log(np.clip(p[np.arange(n), y], EPS, 1.0))
    onehot = np.zeros_like(p)
    onehot[np.arange(n), y] = 1.0
    brier = np.sum((p - onehot) ** 2, axis=1)
    cdf_p = np.cumsum(p[:, :-1], axis=1)
    cdf_y = np.cumsum(onehot[:, :-1], axis=1)
    rps = np.mean((cdf_p - cdf_y) ** 2, axis=1)
    order = np.argsort(-p, axis=1, kind="stable")
    top1 = (order[:, 0] == y).astype(float)
    top2 = np.asarray([int(y[i] in order[i, :2]) for i in range(n)], dtype=float)
    return {"logloss": ll, "brier": brier, "rps": rps, "top1": top1, "top2": top2}


def summary(c: dict[str, np.ndarray]) -> dict[str, float]:
    return {k: float(np.mean(v)) for k, v in c.items()}


def paired_bootstrap(delta: np.ndarray, seed: int, n_boot: int = 10000) -> dict[str, float]:
    x = np.asarray(delta, dtype=float)
    if len(x) == 0:
        return {"mean": math.nan, "p05": math.nan, "median": math.nan, "p95": math.nan, "p_delta_lt_0": math.nan}
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, len(x), size=len(x))
        out[i] = float(np.mean(x[idx]))
    return {
        "mean": float(np.mean(out)),
        "p05": float(np.quantile(out, 0.05)),
        "median": float(np.quantile(out, 0.50)),
        "p95": float(np.quantile(out, 0.95)),
        "p_delta_lt_0": float(np.mean(out < 0.0)),
    }


def eval_total(rows: list[dict[str, Any]], seed_base: int) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    y = np.asarray([int(r["actual_total_class"]) for r in rows], dtype=int)
    p0 = np.vstack([r["source_p"] for r in rows])
    p1 = np.vstack([r["candidate_p"] for r in rows])
    c0 = metric_components(y, p0)
    c1 = metric_components(y, p1)
    s0, s1 = summary(c0), summary(c1)
    delta = {k: float(s1[k] - s0[k]) for k in s0}
    boot = {
        metric: paired_bootstrap(c1[metric] - c0[metric], seed_base + i)
        for i, metric in enumerate(("logloss", "brier", "rps"))
    }
    return {"n": len(rows), "source_prior": s0, "ou_updated": s1, "delta_updated_minus_source": delta, "paired_bootstrap_delta": boot}


def line_group_mass(p: np.ndarray, line: float) -> float:
    total_values = np.asarray([0, 1, 2, 3, 4, 5, 6, 7], dtype=float)
    return float(np.sum(p[total_values > line]))


def binary_components(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in rows if abs(float(r["ou_line"]) * 2 - round(float(r["ou_line"]) * 2)) < 1e-10 and abs(float(r["ou_line"]) - round(float(r["ou_line"]))) > 1e-10]
    if not eligible:
        return {"n": 0}
    y = np.asarray([float(r["actual_total"] > r["ou_line"]) for r in eligible], dtype=float)
    q0 = np.asarray([line_group_mass(r["source_p"], float(r["ou_line"])) for r in eligible])
    q1 = np.asarray([line_group_mass(r["candidate_p"], float(r["ou_line"])) for r in eligible])
    qm = np.asarray([float(r["market_p_over"]) for r in eligible])
    def metrics(q: np.ndarray) -> dict[str, float]:
        ll = -(y * np.log(np.clip(q, EPS, 1.0)) + (1-y) * np.log(np.clip(1-q, EPS, 1.0)))
        br = (q-y)**2
        return {"log_loss": float(ll.mean()), "brier": float(br.mean()), "accuracy_at_0_5": float(np.mean((q >= .5) == y))}
    return {
        "n": len(eligible),
        "source_prior": metrics(q0),
        "ou_updated": metrics(q1),
        "market_devig": metrics(qm),
        "max_abs_candidate_vs_market_over_mass": float(np.max(np.abs(q1-qm))),
    }


def exact_score_eval(rows: list[dict[str, Any]], joint_by_match: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ll0: list[float] = []
    ll1: list[float] = []
    t1_0: list[float] = []
    t1_1: list[float] = []
    t3_0: list[float] = []
    t3_1: list[float] = []
    result_marginal_residuals: list[float] = []
    evaluated_match_ids: list[str] = []
    for r in rows:
        event = joint_by_match.get(str(r["match_id"]))
        if event is None:
            continue
        payload = event.get("payload") or {}
        ref = payload.get("coherent_reference") or {}
        proj = payload.get("projected") or {}
        m0 = ref.get("score_matrix") or {}
        m1 = proj.get("score_matrix") or {}
        key = f"{int(r['home_goals_90'])}-{int(r['away_goals_90'])}"
        if key not in m0 or key not in m1:
            continue
        p0 = max(float(m0[key]), EPS)
        p1 = max(float(m1[key]), EPS)
        ll0.append(-math.log(p0)); ll1.append(-math.log(p1))
        rank0 = [k for k,_ in sorted(m0.items(), key=lambda kv: (-float(kv[1]), kv[0]))]
        rank1 = [k for k,_ in sorted(m1.items(), key=lambda kv: (-float(kv[1]), kv[0]))]
        t1_0.append(float(key == rank0[0])); t1_1.append(float(key == rank1[0]))
        t3_0.append(float(key in rank0[:3])); t3_1.append(float(key in rank1[:3]))
        res0 = ref.get("result") or {}; res1 = proj.get("result") or {}
        if all(k in res0 and k in res1 for k in ("home","draw","away")):
            result_marginal_residuals.append(max(abs(float(res0[k])-float(res1[k])) for k in ("home","draw","away")))
        evaluated_match_ids.append(str(r["match_id"]))
    if not ll0:
        return {"n": 0}
    a0, a1 = np.asarray(ll0), np.asarray(ll1)
    return {
        "n": len(ll0),
        "source_coherent_reference": {
            "exact_score_log_loss": float(a0.mean()),
            "top1_accuracy": float(np.mean(t1_0)),
            "top3_accuracy": float(np.mean(t3_0)),
        },
        "ou_projected": {
            "exact_score_log_loss": float(a1.mean()),
            "top1_accuracy": float(np.mean(t1_1)),
            "top3_accuracy": float(np.mean(t3_1)),
        },
        "delta_projected_minus_reference": {
            "exact_score_log_loss": float((a1-a0).mean()),
            "top1_accuracy": float(np.mean(t1_1)-np.mean(t1_0)),
            "top3_accuracy": float(np.mean(t3_1)-np.mean(t3_0)),
        },
        "bootstrap_exact_score_log_loss_delta": paired_bootstrap(a1-a0, 65050351),
        "max_abs_result_marginal_change": float(max(result_marginal_residuals) if result_marginal_residuals else 0.0),
        "evaluated_match_ids_sha256": hashlib.sha256("\n".join(sorted(evaluated_match_ids)).encode()).hexdigest(),
    }


def lead_stats(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {}
    values = np.asarray([float(r["prediction_lead_hours"]) for r in rows], dtype=float)
    market_values = np.asarray([float(r["market_lead_hours"]) for r in rows], dtype=float)
    return {
        "prediction_min_hours": float(values.min()), "prediction_p10_hours": float(np.quantile(values,.10)),
        "prediction_median_hours": float(np.median(values)), "prediction_p90_hours": float(np.quantile(values,.90)),
        "prediction_max_hours": float(values.max()),
        "market_snapshot_min_hours": float(market_values.min()), "market_snapshot_median_hours": float(np.median(market_values)),
        "market_snapshot_max_hours": float(market_values.max()),
    }


def run() -> dict[str, Any]:
    t_root = load_json(EVENTS_T)
    joint_root = load_json(EVENTS_JOINT)
    primary_results, primary_duplicates = load_primary_results()
    historical_results, historical_ambiguous = load_historical_results()
    joint_by_match = {str(e.get("match_id")): e for e in joint_root.get("events", [])}

    rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for event in t_root.get("events", []):
        payload = event.get("payload") or {}
        fixture = payload.get("fixture_identity") or {}
        match_id = str(event.get("match_id") or "")
        comp = fixture.get("competition_id")
        kickoff_text = fixture.get("kickoff_at")
        home = fixture.get("home_team")
        away = fixture.get("away_team")
        exact_key = fixture_key(comp, kickoff_text, home, away)
        date_key_ = date_team_key(comp, kickoff_text, home, away)
        result = primary_results.get(exact_key)
        settlement_tier = "market_first_exact_fixture"
        result_observed = None
        result_source = None
        if result is not None:
            hg = int(result["home_goals_90"]); ag = int(result["away_goals_90"])
            result_observed = ((result.get("source") or {}).get("observed_at"))
            result_source = ((result.get("source") or {}).get("name"))
        else:
            hist = historical_results.get(date_key_)
            if hist is None:
                unmatched.append({"match_id": match_id, "competition_id": comp, "kickoff_at": kickoff_text, "home_team": home, "away_team": away})
                continue
            settlement_tier = "historical_exact_date_team_fallback"
            hg = int(hist["home_goals_90"]); ag = int(hist["away_goals_90"])
            result_source = str(hist.get("source_file") or "")

        kickoff = parse_dt(kickoff_text)
        freeze = parse_dt(payload.get("projection_freeze_at_utc"))
        market_obs = parse_dt(payload.get("market_observed_at_utc"))
        result_obs = parse_dt(result_observed)
        if kickoff is None or freeze is None or market_obs is None:
            raise SettlementError(f"missing frozen timestamps for {match_id}")
        prediction_temporal_pass = bool(market_obs <= freeze < kickoff)
        result_temporal_pass = bool(result_obs is not None and kickoff < result_obs)
        strict_temporal_pass = bool(settlement_tier == "market_first_exact_fixture" and prediction_temporal_pass and result_temporal_pass)
        source_p = probability_vector(payload.get("source_prior_total") or {})
        candidate_p = probability_vector(payload.get("candidate_total") or {})
        actual_total = hg + ag
        rows.append({
            "match_id": match_id,
            "competition_id": str(comp), "kickoff_at": kickoff.isoformat(), "home_team": str(home), "away_team": str(away),
            "home_goals_90": hg, "away_goals_90": ag, "actual_total": actual_total, "actual_total_class": min(actual_total,7),
            "settlement_tier": settlement_tier, "result_source": result_source, "result_observed_at_utc": result_obs.isoformat() if result_obs else None,
            "prediction_temporal_pass": prediction_temporal_pass, "result_temporal_pass": result_temporal_pass, "strict_temporal_pass": strict_temporal_pass,
            "projection_freeze_at_utc": freeze.isoformat(), "market_observed_at_utc": market_obs.isoformat(),
            "prediction_lead_hours": (kickoff-freeze).total_seconds()/3600.0,
            "market_lead_hours": (kickoff-market_obs).total_seconds()/3600.0,
            "ou_line": float((payload.get("over_under_raw") or {})["line"]),
            "market_p_over": float((payload.get("over_under_devig") or {})["over"]),
            "source_p": source_p, "candidate_p": candidate_p,
            "source_top1": int(np.argmax(source_p)), "candidate_top1": int(np.argmax(candidate_p)),
        })

    strict = [r for r in rows if r["strict_temporal_pass"]]
    all_settled = list(rows)
    if not strict:
        raise SettlementError("zero strict timestamped settlements; cannot run primary prospective endpoint")

    lines = sorted({float(r["ou_line"]) for r in strict})
    by_line: dict[str, Any] = {}
    for i, line in enumerate(lines):
        part = [r for r in strict if float(r["ou_line"]) == line]
        by_line[str(line)] = eval_total(part, 6505300 + i*10)

    primary_total = eval_total(strict, 6505001)
    support_total = eval_total(all_settled, 6505101)
    primary_binary = binary_components(strict)
    support_binary = binary_components(all_settled)
    primary_score = exact_score_eval(strict, joint_by_match)
    support_score = exact_score_eval(all_settled, joint_by_match)

    tier_counts = Counter(r["settlement_tier"] for r in rows)
    comp_counts = Counter(r["competition_id"] for r in strict)
    line_counts = Counter(str(r["ou_line"]) for r in strict)
    result = {
        "schema_version": "V6503_FORWARD_TOTAL_SETTLEMENT_R1",
        "classification": "PROSPECTIVE_FROZEN_PREDICTION_SETTLEMENT_RESEARCH_ONLY",
        "status": "COMPLETED_RESEARCH_ONLY",
        "prediction_ledgers": {
            "v6503_path": str(EVENTS_T.relative_to(ROOT)), "v6503_sha256": sha256_file(EVENTS_T), "v6503_events": int(len(t_root.get("events",[]))),
            "v6505_path": str(EVENTS_JOINT.relative_to(ROOT)), "v6505_sha256": sha256_file(EVENTS_JOINT), "v6505_events": int(len(joint_root.get("events",[]))),
        },
        "settlement": {
            "settled_total": int(len(all_settled)), "strict_timestamped_n": int(len(strict)),
            "unmatched_n": int(len(unmatched)), "unmatched_examples": unmatched[:20],
            "settlement_tier_counts": dict(sorted(tier_counts.items())),
            "primary_result_duplicate_keys_excluded": primary_duplicates,
            "historical_ambiguous_identity_keys_excluded": int(historical_ambiguous),
            "strict_competition_counts": dict(sorted(comp_counts.items())),
            "strict_ou_line_counts": dict(sorted(line_counts.items(), key=lambda kv: float(kv[0]))),
            "strict_actual_total_distribution": dict(sorted(Counter(str(r["actual_total_class"]) if r["actual_total"] < 7 else "7+" for r in strict).items())),
            "strict_actual_draws": int(sum(r["home_goals_90"] == r["away_goals_90"] for r in strict)),
        },
        "temporal_audit": {
            "primary_definition": "market_observed_at <= projection_freeze_at < kickoff_at < result_observed_at",
            "prediction_temporal_failures_all_settled": int(sum(not r["prediction_temporal_pass"] for r in all_settled)),
            "result_temporal_failures_primary_tier": int(sum(r["settlement_tier"]=="market_first_exact_fixture" and not r["result_temporal_pass"] for r in all_settled)),
            "strict_all_pass": bool(all(r["strict_temporal_pass"] for r in strict)),
            "lead_time": lead_stats(strict),
        },
        "primary_strict_timestamped": {
            "direct_total": primary_total,
            "binary_at_observed_ou_line": primary_binary,
            "direct_total_by_ou_line_diagnostic": by_line,
            "v6505_exact_score_projection": primary_score,
        },
        "supplementary_all_deterministically_settled": {
            "direct_total": support_total,
            "binary_at_observed_ou_line": support_binary,
            "v6505_exact_score_projection": support_score,
            "timing_claim": "supplementary_only_historical_fallback_has_no_result_observed_timestamp",
        },
        "frozen_decision_contract": {
            "outcome_driven_sample_selection": False,
            "post_result_parameter_search": False,
            "post_result_feature_search": False,
            "post_result_threshold_search": False,
            "post_result_blend_search": False,
            "prediction_probabilities_recomputed": False,
            "prediction_probabilities_mutated": False,
            "settled_subset_rule": "deterministic fixture identity availability only",
        },
        "interpretation_guard": {
            "strict_timestamped_subset_is_prospective_PIT_evidence": True,
            "historical_fallback_strengthens_PIT_claim": False,
            "single_provider_market_challenge": True,
            "can_authorize_promotion": False,
            "formal_weight": 0,
        },
        "governance": {
            "formal_weight": 0, "provider_requests": 0, "new_data_collection": False,
            "latest_position4_confirmation_opened": False,
            "formal_model_mutation": False, "formal_data_mutation": False, "formal_config_mutation": False,
            "current_mutation": False, "main_mutation": False,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = [
        "match_id","competition_id","kickoff_at","home_team","away_team","home_goals_90","away_goals_90","actual_total","actual_total_class",
        "settlement_tier","result_source","result_observed_at_utc","prediction_temporal_pass","result_temporal_pass","strict_temporal_pass",
        "projection_freeze_at_utc","market_observed_at_utc","prediction_lead_hours","market_lead_hours","ou_line","market_p_over","source_top1","candidate_top1",
    ] + [f"source_p_{k}" for k in TOTAL_KEYS] + [f"candidate_p_{k}" for k in TOTAL_KEYS]
    with ROWS_OUT.open("w", encoding="utf-8", newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for r in rows:
            flat={k:r.get(k) for k in fields if not k.startswith("source_p_") and not k.startswith("candidate_p_")}
            flat.update({f"source_p_{k}":float(r["source_p"][i]) for i,k in enumerate(TOTAL_KEYS)})
            flat.update({f"candidate_p_{k}":float(r["candidate_p"][i]) for i,k in enumerate(TOTAL_KEYS)})
            w.writerow(flat)
    return result


def main() -> None:
    x=run()
    print(json.dumps({
        "classification":x["classification"], "settlement":x["settlement"], "temporal_audit":x["temporal_audit"],
        "primary":x["primary_strict_timestamped"], "supplementary":x["supplementary_all_deterministically_settled"],
        "frozen_decision_contract":x["frozen_decision_contract"], "interpretation_guard":x["interpretation_guard"], "governance":x["governance"],
    },ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
