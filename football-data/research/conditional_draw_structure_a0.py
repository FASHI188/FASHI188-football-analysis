#!/usr/bin/env python3
"""Research-only A0 audit: separate P(T=t) error from P(D=0|T=t,X) error."""
from __future__ import annotations

import argparse, json, subprocess, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for p in (FD / "engine", FD / "validation", HERE):
    if str(p) not in sys.path: sys.path.insert(0, str(p))

from football_v460_engine import load_config, predict_from_history  # noqa:E402
from nested_backtest_v460 import _objective, evaluate_season  # noqa:E402
from platform_core import MatchRow, PlatformError, ROOT, load_registry, read_processed_matches  # noqa:E402

OUT = ROOT.parent / "artifacts/research/conditional_draw_structure_a0"
EXCLUDED = {"USA_MLS": "INACTIVE_STALE_BOUND_ARTIFACT"}
BUCKETS = ("0", "1", "2", "3", "4", "5", "6", "7+")
PRIMARY = (0, 2, 4, 6)
EPS = 1e-15


def head() -> str | None:
    try: return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True).strip()
    except Exception: return None


def counts(history: list[MatchRow]) -> Counter[str]:
    out: Counter[str] = Counter()
    for m in history: out[m.home_team] += 1; out[m.away_team] += 1
    return out


def parts(prediction: dict[str, Any]) -> tuple[dict[int, float], dict[int, float]]:
    totals: Counter[int] = Counter(); diagonal: Counter[int] = Counter()
    for c in prediction["probabilities"]["score_matrix"]:
        h, a, p = int(c["home_goals"]), int(c["away_goals"]), float(c["probability"])
        totals[h + a] += p
        if h == a: diagonal[h + a] += p
    return dict(totals), dict(diagonal)


def record(m: MatchRow, prediction: dict[str, Any], index: int) -> dict[str, Any]:
    totals, diagonal = parts(prediction); probs = prediction["probabilities"]; one = probs["one_x_two"]
    q = {str(t): diagonal.get(t, 0.0) / max(EPS, p) for t, p in totals.items() if t % 2 == 0}
    share = float(prediction.get("team_sample", {}).get("allocation_home_share", 0.5))
    ph, pa = float(one["home"]), float(one["away"])
    return {
        "match_key": f"{m.season}|{m.date.date().isoformat()}|{m.home_team}|{m.away_team}",
        "season": m.season, "date": m.date.date().isoformat(), "sequence_index": index,
        "actual_score": f"{m.home_goals}-{m.away_goals}", "actual_total": m.home_goals + m.away_goals,
        "actual_draw": m.home_goals == m.away_goals, "p_draw": float(one["draw"]),
        "strength_gap": abs(ph - pa), "allocation_gap": abs(share - 0.5),
        "exact_total": {str(k): v for k, v in sorted(totals.items())},
        "diagonal": {str(k): v for k, v in sorted(diagonal.items())}, "q": q,
        "buckets": {k: float(probs["total_goals"][k]) for k in BUCKETS},
    }


def evaluate_structure(cid: str, matches: list[MatchRow], params: dict[str, Any]) -> list[dict[str, Any]]:
    v = load_config()["validation"]; wc, wt = int(v["warmup_competition_matches"]), int(v["warmup_team_matches"])
    by_date: dict[datetime, list[MatchRow]] = defaultdict(list)
    for m in matches: by_date[m.date].append(m)
    history: list[MatchRow] = []; output = []; index = 0
    for date in sorted(by_date):
        team_n = counts(history)
        for m in sorted(by_date[date], key=lambda x: (x.home_team, x.away_team)):
            if len(history) >= wc and team_n[m.home_team] >= wt and team_n[m.away_team] >= wt:
                try: prediction = predict_from_history(history, cid, m.season, m.home_team, m.away_team, m.date, params, use_team_effects=True)
                except PlatformError: continue
                output.append(record(m, prediction, index)); index += 1
        history.extend(by_date[date]); history.sort(key=lambda x: (x.date, x.home_team, x.away_team))
    return output


def competition(cid: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config = load_config(); candidates = config["candidate_parameters"]
    grouped: dict[str, list[MatchRow]] = defaultdict(list)
    for m in read_processed_matches(cid): grouped[m.season].append(m)
    seasons = sorted(grouped, key=lambda s: min(m.date for m in grouped[s]))
    cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for i, candidate in enumerate(candidates):
        for season in seasons:
            ordered = sorted(grouped[season], key=lambda x: (x.date, x.home_team, x.away_team))
            cache[i][season] = evaluate_season(cid, ordered, candidate, use_team_effects=True)
    outer = []; folds = []
    for oi in range(1, len(seasons)):
        season, prior = seasons[oi], seasons[:oi]; scores = []
        for i, candidate in enumerate(candidates):
            prior_records = [r for s in prior for r in cache[i][s]]
            scores.append((_objective(prior_records), i, candidate, len(prior_records)))
        objective, selected_i, selected, selection_n = sorted(scores, key=lambda x: (x[0], x[1]))[0]
        ordered = sorted(grouped[season], key=lambda x: (x.date, x.home_team, x.away_team))
        records = evaluate_structure(cid, ordered, selected)
        if {r["match_key"] for r in records} != {r["match_key"] for r in cache[selected_i][season]}:
            raise RuntimeError(f"OOS identity mismatch: {cid} {season}")
        outer.extend(records); folds.append({"outer_season": season, "prior_seasons": prior, "selected_candidate_index": selected_i,
            "selected_parameters": selected, "selection_objective": objective, "selection_prediction_count": selection_n,
            "oos_records": len(records), "record_identity_check": "PASS"})
    return outer, folds


def bucket(total: int) -> str: return str(total) if total <= 6 else "7+"


def totals_diag(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {}
    for b in BUCKETS:
        p = mean(float(r["buckets"][b]) for r in records); o = mean(float(bucket(int(r["actual_total"])) == b) for r in records)
        rows[b] = {"predicted": p, "observed": o, "residual": p - o, "absolute_error": abs(p - o)}
    return {"by_bucket": rows, "mean_absolute_error": mean(x["absolute_error"] for x in rows.values()),
            "maximum_absolute_error": max(x["absolute_error"] for x in rows.values())}


def central_diag(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals = sorted({int(t) for r in records for t in r["q"]}); rows = {}; weighted = 0.0; n = 0
    for t in totals:
        key = str(t); realised = [r for r in records if int(r["actual_total"]) == t]
        up = mean(float(r["diagonal"].get(key, 0.0)) for r in records)
        uo = mean(float(r["actual_score"] == f"{t//2}-{t//2}") for r in records)
        q_all = mean(float(r["q"].get(key, 0.0)) for r in records)
        if realised:
            qr = mean(float(r["q"].get(key, 0.0)) for r in realised); oq = mean(float(r["actual_draw"]) for r in realised)
            residual = qr - oq; weighted += len(realised) * abs(residual); n += len(realised)
        else: qr = oq = residual = None
        rows[key] = {"score": f"{t//2}-{t//2}", "realised_total_count": len(realised),
            "unconditional_predicted": up, "unconditional_observed": uo, "unconditional_residual": up - uo,
            "mean_q_all_forecasts": q_all, "mean_q_when_total_realised": qr,
            "observed_draw_rate_when_total_realised": oq, "conditional_residual_when_total_realised": residual}
    return {"primary_even_totals": {str(t): rows.get(str(t)) for t in PRIMARY}, "all_even_totals": rows,
            "weighted_mean_absolute_conditional_residual": weighted / n if n else None}


def band(v: float, cuts: tuple[float, ...]) -> str:
    low = 0.0
    for high in cuts:
        if v < high: return f"[{low:.3f},{high:.3f})"
        low = high
    return f"[{low:.3f},inf)"


def gaps(records: list[dict[str, Any]], field: str, cuts: tuple[float, ...]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records: grouped[band(float(r[field]), cuts)].append(r)
    out = {}
    for label, subset in sorted(grouped.items()):
        pd = mean(float(r["p_draw"]) for r in subset); od = mean(float(r["actual_draw"]) for r in subset)
        pe = mean(sum(float(p) for t, p in r["exact_total"].items() if int(t) % 2 == 0) for r in subset)
        oe = mean(float(int(r["actual_total"]) % 2 == 0) for r in subset)
        out[label] = {"count": len(subset), "actual_draw_rate": od, "mean_predicted_draw_probability": pd,
            "draw_residual": pd - od, "actual_even_total_rate": oe,
            "mean_predicted_even_total_probability": pe, "even_total_residual": pe - oe}
    return out


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records: return {"count": 0}
    td, cd = totals_diag(records), central_diag(records)
    od = mean(float(r["actual_draw"]) for r in records); pd = mean(float(r["p_draw"]) for r in records)
    md = mean(sum(float(v) for v in r["diagonal"].values()) for r in records)
    tmae, cmae = td["mean_absolute_error"], cd["weighted_mean_absolute_conditional_residual"]
    if cmae is None: signal = "INSUFFICIENT_CONDITIONAL_OBSERVATIONS"
    elif cmae > tmae + 0.01: signal = "CONDITIONAL_CENTRAL_ALLOCATION_PRIMARY_SUSPECT"
    elif tmae > cmae + 0.01: signal = "TOTAL_GOALS_MARGINAL_PRIMARY_SUSPECT"
    else: signal = "MIXED_OR_INCONCLUSIVE"
    return {"count": len(records), "actual_draw_rate": od, "mean_predicted_draw_probability": pd,
        "draw_probability_residual": pd - od, "mean_score_matrix_diagonal_probability": md,
        "one_x_two_draw_vs_matrix_diagonal_residual": pd - md,
        "total_goals_marginal_calibration": td, "conditional_central_draw_calibration": cd,
        "strength_gap_diagnostics": gaps(records, "strength_gap", (0.05, 0.10, 0.20, 0.35)),
        "allocation_gap_diagnostics": gaps(records, "allocation_gap", (0.025, 0.05, 0.10, 0.20)),
        "architecture_signal": {"classification": signal, "total_goals_bucket_mae": tmae,
            "conditional_central_weighted_mae": cmae,
            "heuristic": "A >1 percentage-point MAE gap identifies the larger structural error source; research-only."}}


def markdown(report: dict[str, Any]) -> str:
    a = report["aggregate"]
    if not a.get("count"): return "# A0 Conditional Draw-Structure Diagnostic\n\nNo usable OOS records.\n"
    s = a["architecture_signal"]
    lines = ["# A0 Conditional Draw-Structure Diagnostic", "", "Research-only; no formal mutation.", "",
        f"- Repository HEAD: `{report['repository_head']}`", f"- 90-minute OOS records: {a['count']}",
        f"- Actual draw rate: {a['actual_draw_rate']:.6f}", f"- Mean predicted draw: {a['mean_predicted_draw_probability']:.6f}",
        f"- Total bucket MAE: {s['total_goals_bucket_mae']:.6f}",
        f"- Conditional central MAE: {s['conditional_central_weighted_mae']:.6f}",
        f"- Architecture signal: `{s['classification']}`", "", "## Primary even totals", ""]
    for t, row in a["conditional_central_draw_calibration"]["primary_even_totals"].items():
        if row: lines.append(f"- T={t}, {row['score']}: n={row['realised_total_count']}, predicted={row['mean_q_when_total_realised']}, observed={row['observed_draw_rate_when_total_realised']}, residual={row['conditional_residual_when_total_realised']}")
    lines += ["", "This audit locates the error source; it does not train or promote a replacement model.", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--competition"); ap.add_argument("--output-dir", default=str(OUT)); ap.add_argument("--print-summary", action="store_true"); args = ap.parse_args()
    registered = [x["competition_id"] for x in load_registry()["competitions"]]
    if args.competition:
        if args.competition not in registered: raise SystemExit(f"unknown competition: {args.competition}")
        if args.competition in EXCLUDED: raise SystemExit(f"competition excluded: {args.competition}")
        cids = [args.competition]
    else: cids = [x for x in registered if x not in EXCLUDED]
    all_records = []; competitions = {}; failures = []
    for cid in cids:
        try:
            records, folds = competition(cid); competitions[cid] = {"oos_records": len(records), "diagnostics": aggregate(records), "folds": folds}
            all_records.extend({**r, "competition_id": cid} for r in records)
        except Exception as exc: failures.append({"competition_id": cid, "error": f"{type(exc).__name__}: {exc}"})
    overall = aggregate(all_records)
    reconciliation = bool(overall.get("count")) and abs(float(overall["one_x_two_draw_vs_matrix_diagonal_residual"])) <= 1e-10
    status = "PASS" if not failures and reconciliation else "PARTIAL" if all_records else "FAIL"
    report = {"schema_version": "A0-conditional-draw-structure-v1", "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repository_head": head(), "status": status,
        "scope": {"research_only": True, "outcome_scope": "90_minutes_including_stoppage", "included_competitions": cids,
            "excluded_competitions": EXCLUDED, "formal_model_mutation": False, "formal_config_mutation": False,
            "formal_weight_mutation": False, "data_mutation": False, "current_rule_mutation": False},
        "method": {"parameter_selection": "existing nested time-ordered OOS objective; earlier seasons only",
            "same_day_leakage_policy": "same-day outcomes withheld until all same-day predictions finish",
            "decomposition": "P(score=t/2-t/2)=P(T=t)*P(D=0|T=t,X)", "primary_even_totals": PRIMARY, "model_training": False},
        "aggregate": overall, "competitions": competitions,
        "audits": {"one_x_two_draw_equals_matrix_diagonal": {"passed": reconciliation,
            "residual": overall.get("one_x_two_draw_vs_matrix_diagonal_residual"), "tolerance": 1e-10}, "competition_failures": failures},
        "governance": {"candidate_status": "RESEARCH_DIAGNOSTIC_ONLY", "formal_promotion": False,
            "codex_review_required": True, "user_approval_required": True}}
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / "conditional_draw_structure_a0.json", out / "conditional_draw_structure_a0.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); mp.write_text(markdown(report), encoding="utf-8")
    if args.print_summary: print(json.dumps({"status": status, "repository_head": report["repository_head"], "included_competitions": len(competitions), "failures": failures, "aggregate": overall, "json_report": str(jp), "markdown_report": str(mp)}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__": raise SystemExit(main())
