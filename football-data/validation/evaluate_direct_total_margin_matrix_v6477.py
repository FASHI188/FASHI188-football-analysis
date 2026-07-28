#!/usr/bin/env python3
"""V6.47.7 direct-total + conditional-margin joint-matrix challenge.

This is a clean research restart for total-goals / exact-score modelling after the
V6.17 and V6.22.4 candidates failed the proper-score gate.

Architecture (CURRENT-compatible research form)
-----------------------------------------------
1. Predict the total-goal distribution DIRECTLY. The internal track predicts exact
   T=0..14 plus T=15+, then reports the required public margin T=0..6,7+.
2. Predict conditional goal difference P(D=d | T=t, X) for exact T=0..14.
   For the negligible T=15+ tail only the 1X2 sign is retained; no exact tail score is
   invented.
3. Map exact (T,D) to H=(T+D)/2, A=(T-D)/2 only when parity/integer/nonnegative
   constraints hold. T=15+ stays an explicit unresolved tail bucket.
4. 1X2 and exact-score metrics are aggregated from this same reconstructed matrix.

No Poisson means, no home/away expected-goal sum, no artificial score shares, no
manual draw adjustment, and no fixed1000 outcome is used for training or parameter
selection. The model is online: all same-day matches are predicted before any result
from that day updates ratings, recent-total context, or categorical counts.

Research only, formal_weight=0. Historical fixed1000 can reject a candidate but cannot
promote it to CURRENT; a passing candidate still requires a fresh post-freeze forward
challenge.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_1x2_fixed1000_benchmark_v6130 as base
from platform_core import parse_match_date

BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
OUT = ROOT / "manifests" / "v6_direct_total_margin_matrix_v6477_status.json"

ELO_INITIAL = 1500.0
ELO_K = 20.0
HOME_ADV = 100.0
RECENT_N = 10
RECENT_MIN = 5
TAU = 50.0
ALPHA = 0.05
TOTAL_EXACT_MAX = 14
TOTAL_TAIL = "15+"
TOTAL_STATES: tuple[Any, ...] = tuple(range(TOTAL_EXACT_MAX + 1)) + (TOTAL_TAIL,)
REPORT_TOTAL_STATES = ("0", "1", "2", "3", "4", "5", "6", "7+")
DIRECTIONS = ("home", "draw", "away")
ELO_EDGES = (-math.inf, -200.0, -100.0, 0.0, 100.0, 200.0, math.inf)
RECENT_EDGES = (-math.inf, 2.0, 2.5, 3.0, 3.5, math.inf)
EPS = 1e-15


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def actual_score(raw: dict[str, str]) -> tuple[int, int] | None:
    try:
        hg = int(float(str(raw.get("FTHG") or raw.get("HG") or "")))
        ag = int(float(str(raw.get("FTAG") or raw.get("AG") or "")))
    except (TypeError, ValueError):
        return None
    if hg < 0 or ag < 0:
        return None
    return hg, ag


def team(raw: dict[str, str], side: str) -> str:
    keys = ("HomeTeam", "Home", "home_team", "home") if side == "home" else ("AwayTeam", "Away", "away_team", "away")
    for key in keys:
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    return ""


def bucket(value: float, edges: tuple[float, ...]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return len(edges) - 2


def recent_bucket(values: deque[int]) -> str:
    if len(values) < RECENT_MIN:
        return "NA"
    return str(bucket(sum(values) / len(values), RECENT_EDGES))


def total_state(hg: int, ag: int) -> Any:
    t = hg + ag
    return t if t <= TOTAL_EXACT_MAX else TOTAL_TAIL


def result_direction(hg: int, ag: int) -> str:
    return "home" if hg > ag else "away" if ag > hg else "draw"


def identity_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(row["competition_id"]), str(row["date"]), str(row["home_team"]), str(row["away_team"]))


def read_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    chosen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    duplicate_same = conflicting = parse_fail = score_fail = 0
    source_rows = 0
    for cid in base.TARGET_COMPETITIONS:
        comp_dir = ROOT / "processed" / cid
        if not comp_dir.exists():
            continue
        for path in sorted(comp_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row_index, raw0 in enumerate(csv.DictReader(handle)):
                    source_rows += 1
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    score = actual_score(raw)
                    if score is None:
                        score_fail += 1
                        continue
                    date_raw = str(raw.get("Date") or raw.get("date") or "").strip()
                    season = base._season_label(raw, path)
                    if not date_raw:
                        parse_fail += 1
                        continue
                    try:
                        date_iso = parse_match_date(date_raw, season).isoformat()
                    except Exception:
                        parse_fail += 1
                        continue
                    home, away = team(raw, "home"), team(raw, "away")
                    if not home or not away:
                        parse_fail += 1
                        continue
                    row = {
                        "competition_id": cid,
                        "season": season,
                        "date": date_iso,
                        "home_team": home,
                        "away_team": away,
                        "hg": score[0],
                        "ag": score[1],
                        "source_file": str(path.relative_to(ROOT)),
                        "row_index": row_index,
                    }
                    key = identity_key(row)
                    old = chosen.get(key)
                    if old is None:
                        chosen[key] = row
                        continue
                    if (old["hg"], old["ag"]) != (row["hg"], row["ag"]):
                        conflicting += 1
                        # Fail closed: remove a conflicting identity entirely.
                        chosen.pop(key, None)
                        continue
                    duplicate_same += 1
                    # Prefer consolidated recent_seasons, otherwise lexicographically stable.
                    old_pref = (0 if str(old["source_file"]).endswith("recent_seasons.csv") else 1, str(old["source_file"]))
                    new_pref = (0 if str(row["source_file"]).endswith("recent_seasons.csv") else 1, str(row["source_file"]))
                    if new_pref < old_pref:
                        chosen[key] = row
    rows = sorted(chosen.values(), key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    return rows, {
        "raw_source_rows": source_rows,
        "deduplicated_rows": len(rows),
        "duplicate_same_score_rows": duplicate_same,
        "conflicting_identity_rows_removed": conflicting,
        "parse_or_identity_failures": parse_fail,
        "score_failures": score_fail,
    }


def smooth(counter: Counter[Any], states: Iterable[Any], prior: dict[Any, float] | None = None, tau: float = TAU) -> dict[Any, float]:
    states = tuple(states)
    n = sum(counter.get(s, 0) for s in states)
    if prior is None:
        den = n + ALPHA * len(states)
        return {s: (counter.get(s, 0) + ALPHA) / den for s in states}
    den = n + tau
    return {s: (counter.get(s, 0) + tau * prior[s]) / den for s in states}


def valid_d_states(t: int) -> tuple[int, ...]:
    return tuple(range(-t, t + 1, 2))


def report_total_probs(internal: dict[Any, float]) -> dict[str, float]:
    out = {str(i): float(internal.get(i, 0.0)) for i in range(7)}
    out["7+"] = sum(float(internal.get(i, 0.0)) for i in range(7, TOTAL_EXACT_MAX + 1)) + float(internal.get(TOTAL_TAIL, 0.0))
    return out


def rps_total(p: dict[str, float], actual_cat: str) -> float:
    ordered = list(REPORT_TOTAL_STATES)
    score = 0.0
    for i in range(len(ordered) - 1):
        cp = sum(p[s] for s in ordered[: i + 1])
        cy = 1.0 if ordered.index(actual_cat) <= i else 0.0
        score += (cp - cy) ** 2
    return score / (len(ordered) - 1)


def total_cat(hg: int, ag: int) -> str:
    t = hg + ag
    return str(t) if t <= 6 else "7+"


def topk(probs: dict[Any, float], k: int) -> list[Any]:
    return [s for s, _ in sorted(probs.items(), key=lambda kv: (-kv[1], str(kv[0])))[:k]]


class OnlineModel:
    def __init__(self) -> None:
        self.ratings: dict[tuple[str, str], float] = defaultdict(lambda: ELO_INITIAL)
        self.recent: dict[tuple[str, str], deque[int]] = defaultdict(lambda: deque(maxlen=RECENT_N))
        self.total_comp: dict[str, Counter[Any]] = defaultdict(Counter)
        self.total_strength: dict[tuple[str, int], Counter[Any]] = defaultdict(Counter)
        self.total_full: dict[tuple[str, int, str, str], Counter[Any]] = defaultdict(Counter)
        self.margin_comp: dict[tuple[str, Any], Counter[Any]] = defaultdict(Counter)
        self.margin_strength: dict[tuple[str, int, Any], Counter[Any]] = defaultdict(Counter)
        self.margin_full: dict[tuple[str, int, str, str, Any], Counter[Any]] = defaultdict(Counter)

    def features(self, r: dict[str, Any]) -> tuple[int, str, str]:
        cid = str(r["competition_id"])
        hr = self.ratings[(cid, str(r["home_team"]))]
        ar = self.ratings[(cid, str(r["away_team"]))]
        sbin = bucket(hr + HOME_ADV - ar, ELO_EDGES)
        hb = recent_bucket(self.recent[(cid, str(r["home_team"]))])
        ab = recent_bucket(self.recent[(cid, str(r["away_team"]))])
        return sbin, hb, ab

    def total_probs(self, cid: str, feat: tuple[int, str, str], level: str) -> dict[Any, float]:
        sbin, hb, ab = feat
        comp = smooth(self.total_comp[cid], TOTAL_STATES, None)
        if level == "comp":
            return comp
        strength = smooth(self.total_strength[(cid, sbin)], TOTAL_STATES, comp)
        if level == "strength":
            return strength
        return smooth(self.total_full[(cid, sbin, hb, ab)], TOTAL_STATES, strength)

    def margin_probs(self, cid: str, feat: tuple[int, str, str], tstate: Any, level: str) -> dict[Any, float]:
        sbin, hb, ab = feat
        states: tuple[Any, ...] = DIRECTIONS if tstate == TOTAL_TAIL else valid_d_states(int(tstate))
        comp = smooth(self.margin_comp[(cid, tstate)], states, None)
        if level == "comp":
            return comp
        strength = smooth(self.margin_strength[(cid, sbin, tstate)], states, comp)
        if level == "strength":
            return strength
        return smooth(self.margin_full[(cid, sbin, hb, ab, tstate)], states, strength)

    def predict(self, r: dict[str, Any], level: str = "full") -> dict[str, Any]:
        cid = str(r["competition_id"])
        feat = self.features(r)
        pt = self.total_probs(cid, feat, level)
        matrix: dict[tuple[int, int], float] = {}
        result = {d: 0.0 for d in DIRECTIONS}
        parity_errors = 0
        for t in range(TOTAL_EXACT_MAX + 1):
            pd = self.margin_probs(cid, feat, t, level)
            for d, q in pd.items():
                if (t + int(d)) % 2 != 0:
                    parity_errors += 1
                    continue
                h = (t + int(d)) // 2
                a = (t - int(d)) // 2
                if h < 0 or a < 0:
                    parity_errors += 1
                    continue
                p = pt[t] * q
                matrix[(h, a)] = matrix.get((h, a), 0.0) + p
                result[result_direction(h, a)] += p
        tail_result = self.margin_probs(cid, feat, TOTAL_TAIL, level)
        for d in DIRECTIONS:
            result[d] += pt[TOTAL_TAIL] * tail_result[d]
        total_report = report_total_probs(pt)
        matrix_mass = sum(matrix.values())
        prob_sum = matrix_mass + pt[TOTAL_TAIL]
        # Reconstruct the exact internal total margin from the mapped matrix.
        rec_t = Counter()
        for (h, a), p in matrix.items():
            rec_t[h + a] += p
        rec_resid = max(abs(rec_t[t] - pt[t]) for t in range(TOTAL_EXACT_MAX + 1))
        return {
            "features": {"strength_bin": feat[0], "home_recent_total_bin": feat[1], "away_recent_total_bin": feat[2]},
            "internal_total": pt,
            "total": total_report,
            "matrix": matrix,
            "tail15plus": pt[TOTAL_TAIL],
            "tail15plus_result": tail_result,
            "result": result,
            "audit": {
                "probability_sum": prob_sum,
                "probability_sum_residual": abs(prob_sum - 1.0),
                "internal_total_reconstruction_residual": rec_resid,
                "parity_mapping_errors": parity_errors,
                "result_sum_residual": abs(sum(result.values()) - 1.0),
            },
        }

    def update_batch(self, batch: list[tuple[dict[str, Any], tuple[int, str, str]]]) -> None:
        # Counts and recent form use the frozen pre-day features for every same-day row.
        elo_delta: dict[tuple[str, str], float] = defaultdict(float)
        for r, feat in batch:
            cid = str(r["competition_id"]); home = str(r["home_team"]); away = str(r["away_team"])
            hg = int(r["hg"]); ag = int(r["ag"]); tstate = total_state(hg, ag)
            sbin, hb, ab = feat
            self.total_comp[cid][tstate] += 1
            self.total_strength[(cid, sbin)][tstate] += 1
            self.total_full[(cid, sbin, hb, ab)][tstate] += 1
            mstate: Any = result_direction(hg, ag) if tstate == TOTAL_TAIL else hg - ag
            self.margin_comp[(cid, tstate)][mstate] += 1
            self.margin_strength[(cid, sbin, tstate)][mstate] += 1
            self.margin_full[(cid, sbin, hb, ab, tstate)][mstate] += 1

            hr = self.ratings[(cid, home)]; ar = self.ratings[(cid, away)]
            exp_h = 1.0 / (1.0 + 10.0 ** (-(hr + HOME_ADV - ar) / 400.0))
            obs_h = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
            delta = ELO_K * (obs_h - exp_h)
            elo_delta[(cid, home)] += delta
            elo_delta[(cid, away)] -= delta

        for key, delta in elo_delta.items():
            self.ratings[key] += delta
        for r, _ in batch:
            cid = str(r["competition_id"]); t = int(r["hg"]) + int(r["ag"])
            self.recent[(cid, str(r["home_team"]))].append(t)
            self.recent[(cid, str(r["away_team"]))].append(t)


def evaluate_predictions(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"label": label, "count": 0}
    total1 = total2 = score1 = score3 = result1 = 0
    total_ll = exact_ll = result_brier = total_rps_sum = 0.0
    exact_ll_n = 0; tail_actual_n = 0
    max_prob_resid = max_reconstruct_resid = max_result_resid = 0.0
    parity_errors = 0
    predicted_total_avg = Counter(); actual_total = Counter()
    tail_probs = []
    for x in rows:
        p = x["prediction"]; hg = int(x["hg"]); ag = int(x["ag"]); tc = total_cat(hg, ag); actual_total[tc] += 1
        tr = p["total"]; predicted_total_avg.update(tr)
        tk = topk(tr, 2)
        total1 += int(tk[0] == tc); total2 += int(tc in tk)
        total_ll -= math.log(max(EPS, tr[tc])); total_rps_sum += rps_total(tr, tc)

        actual_res = result_direction(hg, ag); rp = p["result"]; result1 += int(max(DIRECTIONS, key=lambda d: rp[d]) == actual_res)
        y = {d: 1.0 if d == actual_res else 0.0 for d in DIRECTIONS}
        result_brier += sum((rp[d] - y[d]) ** 2 for d in DIRECTIONS)

        t = hg + ag
        if t <= TOTAL_EXACT_MAX:
            matrix = p["matrix"]
            sk = topk(matrix, 3)
            score1 += int(sk and sk[0] == (hg, ag)); score3 += int((hg, ag) in sk)
            exact_ll -= math.log(max(EPS, matrix.get((hg, ag), 0.0))); exact_ll_n += 1
        else:
            tail_actual_n += 1

        a = p["audit"]
        max_prob_resid = max(max_prob_resid, float(a["probability_sum_residual"]))
        max_reconstruct_resid = max(max_reconstruct_resid, float(a["internal_total_reconstruction_residual"]))
        max_result_resid = max(max_result_resid, float(a["result_sum_residual"]))
        parity_errors += int(a["parity_mapping_errors"])
        tail_probs.append(float(p["tail15plus"]))

    return {
        "label": label,
        "count": n,
        "total_top1_accuracy": total1 / n,
        "total_top2_accuracy": total2 / n,
        "total_log_loss": total_ll / n,
        "total_rps": total_rps_sum / n,
        "exact_score_top1_accuracy": score1 / n,
        "exact_score_top3_accuracy": score3 / n,
        "exact_score_log_loss_non_tail": exact_ll / exact_ll_n if exact_ll_n else None,
        "exact_score_log_loss_n": exact_ll_n,
        "actual_15plus_count": tail_actual_n,
        "result_top1_accuracy": result1 / n,
        "result_brier": result_brier / n,
        "average_predicted_total_distribution": {s: predicted_total_avg[s] / n for s in REPORT_TOTAL_STATES},
        "actual_total_distribution": {s: actual_total[s] / n for s in REPORT_TOTAL_STATES},
        "mean_tail15plus_probability": sum(tail_probs) / n,
        "matrix_audit": {
            "max_probability_sum_residual": max_prob_resid,
            "max_internal_total_reconstruction_residual": max_reconstruct_resid,
            "max_result_sum_residual": max_result_resid,
            "parity_mapping_error_count": parity_errors,
        },
    }


def main() -> int:
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    bench_keys = {
        (str(r["competition_id"]), str(r["date"]), str(r["home_team"]), str(r["away_team"]))
        for r in benchmark.get("rows", [])
    }
    rows, source_meta = read_rows()
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_comp[str(r["competition_id"])].append(r)

    selected_seasons = benchmark.get("source_meta", {}).get("selected_seasons", {})
    model = OnlineModel()
    candidate_rows: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []
    benchmark_missing = []
    training_rows = walkforward_rows = 0

    for cid in base.TARGET_COMPETITIONS:
        comp_rows = sorted(by_comp.get(cid, []), key=lambda r: (r["date"], r["home_team"], r["away_team"]))
        seasons = set(str(x) for x in selected_seasons.get(cid, []))
        cutoff_dates = [str(r["date"]) for r in comp_rows if str(r["season"]) in seasons]
        cutoff = min(cutoff_dates) if cutoff_dates else None
        day_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in comp_rows:
            day_groups[str(r["date"])[:10]].append(r)
        for day in sorted(day_groups):
            day_rows = day_groups[day]
            frozen = [(r, model.features(r)) for r in day_rows]
            for r, _feat in frozen:
                if cutoff and str(r["date"]) < cutoff:
                    training_rows += 1
                    continue
                walkforward_rows += 1
                key = identity_key(r)
                if key not in bench_keys:
                    continue
                cand = model.predict(r, "full")
                ref = model.predict(r, "comp")
                common = {"competition_id": cid, "date": r["date"], "home_team": r["home_team"], "away_team": r["away_team"], "hg": r["hg"], "ag": r["ag"]}
                candidate_rows.append({**common, "prediction": cand})
                reference_rows.append({**common, "prediction": ref})
            model.update_batch(frozen)

    seen = {identity_key(r) for r in rows}
    for key in sorted(bench_keys):
        if key not in seen:
            benchmark_missing.append("|".join(key))

    candidate = evaluate_predictions(candidate_rows, "candidate_full_context_bins")
    reference = evaluate_predictions(reference_rows, "reference_competition_only")
    gate_results = {
        "benchmark_coverage_1000": candidate.get("count") == 1000 and not benchmark_missing,
        "probability_conservation": candidate.get("matrix_audit", {}).get("max_probability_sum_residual", 1.0) <= 1e-12,
        "total_reconstruction": candidate.get("matrix_audit", {}).get("max_internal_total_reconstruction_residual", 1.0) <= 1e-12,
        "parity_mapping": candidate.get("matrix_audit", {}).get("parity_mapping_error_count", 1) == 0,
        "result_conservation": candidate.get("matrix_audit", {}).get("max_result_sum_residual", 1.0) <= 1e-12,
        "no_unresolved_actual_15plus": candidate.get("actual_15plus_count", 1) == 0,
        "total_rps_nonworse": candidate.get("total_rps", math.inf) <= reference.get("total_rps", -math.inf),
        "total_log_loss_nonworse": candidate.get("total_log_loss", math.inf) <= reference.get("total_log_loss", -math.inf),
        "exact_score_log_loss_nonworse": candidate.get("exact_score_log_loss_non_tail", math.inf) <= reference.get("exact_score_log_loss_non_tail", -math.inf),
        "total_top1_nonworse": candidate.get("total_top1_accuracy", -1.0) >= reference.get("total_top1_accuracy", 2.0),
        "exact_score_top1_nonworse": candidate.get("exact_score_top1_accuracy", -1.0) >= reference.get("exact_score_top1_accuracy", 2.0),
    }
    decision = "CHALLENGE_FORWARD_REQUIRED" if all(gate_results.values()) else "HOLD_FIXED1000_QUALITY_GATE_NOT_PASSED"

    payload = {
        "schema_version": "V6.47.7-direct-total-conditional-margin-matrix-r1",
        "generated_at_utc": now(),
        "formal_current_version": "V5.0.1",
        "status": "PASS_RESEARCH_AUDIT",
        "classification": "TIME_ORDERED_DIRECT_TOTAL_CONDITIONAL_MARGIN_RESEARCH_FORMAL_WEIGHT_0",
        "design": {
            "direct_total_internal_states": [str(x) for x in TOTAL_STATES],
            "reported_total_states": list(REPORT_TOTAL_STATES),
            "conditional_margin": "P(D=d | exact T=t, X) for T=0..14; T15+ keeps only 1X2 sign and remains an unresolved exact-score tail",
            "mapping": "H=(T+D)/2; A=(T-D)/2; parity/integer/nonnegative enforced",
            "context": "competition + fixed Elo-difference bin + each team's prior 10-match observed-total bin",
            "elo": {"initial": ELO_INITIAL, "k": ELO_K, "home_advantage": HOME_ADV},
            "recent_total_window": RECENT_N,
            "recent_minimum": RECENT_MIN,
            "hierarchical_dirichlet_tau": TAU,
            "symmetric_base_alpha": ALPHA,
            "same_day_predict_before_update": True,
            "poisson_used": False,
            "expected_goals_used": False,
            "home_away_goal_means_used": False,
            "manual_draw_adjustment_used": False,
            "manual_score_share_used": False,
            "benchmark_outcomes_used_for_training_or_parameter_selection": False,
        },
        "data": {
            **source_meta,
            "training_rows_before_recent_two_season_cutoffs": training_rows,
            "walkforward_rows_on_or_after_cutoff": walkforward_rows,
            "benchmark_target_n": len(bench_keys),
            "benchmark_predictions": len(candidate_rows),
            "benchmark_missing_identity_count": len(benchmark_missing),
            "benchmark_missing_identities": benchmark_missing[:20],
        },
        "reference": reference,
        "candidate": candidate,
        "acceptance_gate": {
            "principle": "accuracy alone cannot pass; probability conservation plus total/exact proper-score nonworsening are mandatory",
            "results": gate_results,
        },
        "decision": decision,
        "governance": {
            "historical_fixed1000_can_reject_but_not_promote": True,
            "fresh_postfreeze_forward_required_if_passed": True,
            "formal_weight": 0,
            "automatic_promotion": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"reference": reference, "candidate": candidate, "gate": gate_results, "decision": decision}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
