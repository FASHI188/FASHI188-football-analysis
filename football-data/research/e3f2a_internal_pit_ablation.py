#!/usr/bin/env python3
"""E3f-2A: pure H/D/A ablation of internally derived PIT features."""
from __future__ import annotations
import argparse, hashlib, json, math, subprocess, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for p in (FD / "engine", FD / "validation", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import big5_high_completeness_b100 as b100
import e3e0_draw_identifiability as e3e0
import e3f0_pit_feature_coverage_entry as e3f0_entry
import e3f1a_internal_pit_feature_build as e3f1a
from platform_core import ROOT

OUT = ROOT.parent / "artifacts/research/e3f2a_internal_pit_ablation"
EXPECTED = 6251
SEED = 3700
BOOTSTRAP = 250
MODEL_TYPES = ("LOGISTIC", "TREE")
GROUPS = (
    "A_MARKET",
    "B_LEGACY_TEAM",
    "C_LEGACY_COMBINED",
    "D_TASK_REST",
    "E_STYLE_STATE",
    "F_INTERNAL_ALL",
    "G_MARKET_INTERNAL",
)
TASK_REST = {
    "home_played", "away_played", "home_points", "away_points", "points_gap",
    "home_gd", "away_gd", "gd_gap", "home_ppg", "away_ppg",
    "home_ppg_available", "away_ppg_available",
    "home_rest", "away_rest", "home_rest_available", "away_rest_available", "rest_gap",
    "home_7d", "away_7d", "gap_7d", "home_14d", "away_14d", "gap_14d",
}
MARKET_GROUPS = {"A_MARKET", "C_LEGACY_COMBINED", "G_MARKET_INTERNAL"}
EPS = 1e-12


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def attach_internal(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    raw_by_comp, audit = {}, {}
    for cid in b100.BIG5:
        raw_by_comp[cid], audit[cid] = e3f0_entry.audit.load_raw_observations(cid)
    built = {}
    for cid in b100.BIG5:
        keys = {r["match_key"] for r in rows if r["competition_id"] == cid}
        built.update(e3f1a.build(cid, keys, raw_by_comp[cid], False))
    expected_keys = {r["match_key"] for r in rows}
    if set(built) != expected_keys:
        raise RuntimeError("E3f-1A identity mismatch")
    names = sorted(next(iter(built.values()))["features"])
    if len(names) != 93:
        raise RuntimeError(f"E3f-1A feature schema count={len(names)} != 93")
    output = []
    for row in rows:
        item = dict(row)
        features = dict(built[row["match_key"]]["features"])
        if sorted(features) != names:
            raise RuntimeError("internal feature schema drift")
        if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in features.values()):
            raise RuntimeError("non-finite internal feature")
        item["internal_features"] = features
        output.append(item)
    return output, names, audit


def internal_vector(record: dict[str, Any], names: list[str]) -> tuple[list[float], list[str]]:
    f = record["internal_features"]
    return [float(f[n]) for n in names], [f"internal_{n}" for n in names]


def vector(record: dict[str, Any], group: str, internal_names: list[str]) -> tuple[list[float], list[str]]:
    market_v, market_n = e3e0.market_feature(record)
    team_v, team_n = e3e0.team_feature(record)
    all_v, all_n = internal_vector(record, internal_names)
    task_names = [n for n in internal_names if n in TASK_REST]
    style_names = [n for n in internal_names if n not in TASK_REST]
    task_v = [float(record["internal_features"][n]) for n in task_names]
    style_v = [float(record["internal_features"][n]) for n in style_names]
    if group == "A_MARKET":
        return market_v, market_n
    if group == "B_LEGACY_TEAM":
        return team_v, team_n
    if group == "C_LEGACY_COMBINED":
        return market_v + team_v, market_n + team_n
    if group == "D_TASK_REST":
        return task_v, [f"task_{n}" for n in task_names]
    if group == "E_STYLE_STATE":
        return style_v, [f"style_{n}" for n in style_names]
    if group == "F_INTERNAL_ALL":
        return all_v, all_n
    if group == "G_MARKET_INTERNAL":
        return market_v + all_v, market_n + all_n
    raise ValueError(group)


def matrix(rows: list[dict[str, Any]], group: str, internal_names: list[str]) -> tuple[np.ndarray, list[str]]:
    values, schema = [], None
    for row in rows:
        v, n = vector(row, group, internal_names)
        if schema is None:
            schema = n
        elif schema != n:
            raise RuntimeError("feature schema drift")
        values.append(v)
    x = np.asarray(values, dtype=float)
    if not np.isfinite(x).all():
        raise RuntimeError("non-finite design matrix")
    return x, list(schema or [])


def fallback(record: dict[str, Any], group: str) -> tuple[float, float]:
    source = record["market_probs"] if group in MARKET_GROUPS else record["champion_probs"]
    q = e3e0.finite(source["draw"])
    denom = e3e0.finite(source["home"]) + e3e0.finite(source["away"])
    r = e3e0.finite(source["home"]) / max(EPS, denom)
    return min(1.0, max(0.0, q)), min(1.0, max(0.0, r))


def rolling_oof(
    rows: list[dict[str, Any]], group: str, kind: str, internal_names: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["season_start_year"])].append(row)
    output, folds = [], []
    for target_year in sorted(by_year):
        prior = [r for y in sorted(by_year) if y < target_year for r in by_year[y]]
        current = sorted(by_year[target_year], key=lambda r: (r["date"], r["competition_id"], r["match_key"]))
        train_draws = sum(r["actual_outcome"] == "draw" for r in prior)
        home_n = sum(r["actual_outcome"] == "home" for r in prior)
        away_n = sum(r["actual_outcome"] == "away" for r in prior)
        trainable = (
            len(prior) >= e3e0.MIN_TRAIN and 0 < train_draws < len(prior)
            and home_n > 0 and away_n > 0
        )
        if trainable:
            x_train, names = matrix(prior, group, internal_names)
            x_current, names2 = matrix(current, group, internal_names)
            if names2 != names:
                raise RuntimeError("OOF schema mismatch")
            y_draw = np.asarray([int(r["actual_outcome"] == "draw") for r in prior], dtype=int)
            idx = np.asarray([i for i, r in enumerate(prior) if r["actual_outcome"] != "draw"], dtype=int)
            y_home = np.asarray([int(prior[i]["actual_outcome"] == "home") for i in idx], dtype=int)
            q_model = e3e0.make_estimator(kind, SEED + target_year)
            r_model = e3e0.make_estimator(kind, SEED + 100 + target_year)
            q_model.fit(x_train, y_draw)
            r_model.fit(x_train[idx], y_home)
            q_values = e3e0.positive_probability(q_model, x_current)
            r_values = e3e0.positive_probability(r_model, x_current)
            status = "MODELED"
        else:
            names = vector(current[0], group, internal_names)[1] if current else []
            q_values = np.asarray([fallback(r, group)[0] for r in current], dtype=float)
            r_values = np.asarray([fallback(r, group)[1] for r in current], dtype=float)
            status = "BASELINE_FALLBACK"
        for row, q0, r0 in zip(current, q_values, r_values):
            q = min(1.0, max(0.0, float(q0)))
            r = min(1.0, max(0.0, float(r0)))
            probs = {"draw": q, "home": (1-q)*r, "away": (1-q)*(1-r)}
            total = sum(probs.values())
            probs = {k: v / max(EPS, total) for k, v in probs.items()}
            item = dict(row)
            item["e3f2a_draw_probability"] = q
            item["e3f2a_probs"] = probs
            item["e3f2a_status"] = status
            output.append(item)
        folds.append({
            "target_year": target_year, "prior_rows": len(prior), "target_rows": len(current),
            "prior_draws": train_draws, "prior_home": home_n, "prior_away": away_n,
            "status": status, "feature_count": len(names), "class_weight": None,
            "posthoc_threshold": None,
        })
    return output, folds


def subset_metrics(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    return {
        "draw": e3e0.draw_diagnostics(rows, q_field="e3f2a_draw_probability", seed=seed),
        "hda": e3e0.hda_metrics(rows, "e3f2a_probs"),
        "modeled": sum(r["e3f2a_status"] == "MODELED" for r in rows),
        "fallback": sum(r["e3f2a_status"] != "MODELED" for r in rows),
    }


def paired_pr_delta(a_rows: list[dict[str, Any]], b_rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    a_map = {r["match_key"]: r for r in a_rows}
    b_map = {r["match_key"]: r for r in b_rows}
    keys = sorted(set(a_map) & set(b_map))
    if len(keys) != len(a_rows) or len(keys) != len(b_rows):
        raise RuntimeError("paired identity mismatch")
    y = np.asarray([int(a_map[k]["actual_outcome"] == "draw") for k in keys], dtype=int)
    pa = np.asarray([a_map[k]["e3f2a_draw_probability"] for k in keys], dtype=float)
    pb = np.asarray([b_map[k]["e3f2a_draw_probability"] for k in keys], dtype=float)
    point = float(average_precision_score(y, pa) - average_precision_score(y, pb))
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(BOOTSTRAP):
        idx = rng.integers(0, len(keys), len(keys))
        if len(np.unique(y[idx])) < 2:
            continue
        values.append(float(average_precision_score(y[idx], pa[idx]) - average_precision_score(y[idx], pb[idx])))
    values.sort()
    return {
        "count": len(keys), "point": point, "resamples": len(values),
        "lower_95": values[int(.025*(len(values)-1))] if values else None,
        "upper_95": values[int(.975*(len(values)-1))] if values else None,
    }


def protected(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    checks = {}
    for key in ("logloss", "brier", "rps"):
        checks[key] = candidate[key] <= baseline[key] * 1.005 + 1e-15
    return {"tolerance_relative": 0.005, "checks": checks, "all": all(checks.values())}


def audit_model(rows: list[dict[str, Any]], b100_keys: set[str], seed: int) -> dict[str, Any]:
    full = subset_metrics(rows, seed)
    per_league = {
        cid: subset_metrics([r for r in rows if r["competition_id"] == cid], seed + i + 10)
        for i, cid in enumerate(b100.BIG5)
    }
    years = sorted({int(r["season_start_year"]) for r in rows})
    per_season = {
        str(y): subset_metrics([r for r in rows if int(r["season_start_year"]) == y], seed + y)
        for y in years
    }
    bset = [r for r in rows if r["match_key"] in b100_keys]
    return {"full": full, "per_league": per_league, "per_season": per_season, "b100": subset_metrics(bset, seed+900)}


def baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "market": e3e0.hda_metrics(rows, "market_probs"),
        "champion": e3e0.hda_metrics(rows, "champion_probs"),
        "e3b1": e3e0.hda_metrics(rows, "e3b1_probs"),
        "e3d1": e3e0.hda_metrics(rows, "e3d1_probs"),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# E3f-2A Internal PIT H/D/A Ablation", "",
        "Research-only; formal_weight=0; no external data, class weights or post-hoc threshold.", "",
        f"- HEAD: `{report['repository_head']}`",
        f"- Sample/B100: {report['sample_count']}/{report['b100_count']}",
        f"- Internal schema: {report['internal_feature_count']} features, SHA-256 `{report['internal_schema_sha256']}`", "",
        "## Full OOF", "",
        "| Group | Model | PR-AUC | Top10 P/R | Accuracy | Macro-F1 | Draw F1 | LogLoss | Brier | RPS |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["models"].values():
        d, h = item["audit"]["full"]["draw"], item["audit"]["full"]["hda"]
        top = d["top_candidates"]["top_10pct"]
        lines.append(
            f"| {item['group']} | {item['model_type']} | {d['pr_auc']:.4%} | "
            f"{top['precision']:.2%}/{top['recall']:.2%} | {h['accuracy']:.4%} | "
            f"{h['macro_f1']:.4%} | {h['draw_f1']:.4%} | {h['logloss']:.6f} | "
            f"{h['brier']:.6f} | {h['rps']:.6f} |"
        )
    lines += ["", "## Incremental gates", ""]
    for key, gate in report["incremental_gates"].items():
        lines.append(
            f"- {key}: PR-AUC delta={gate['full_pr_auc_delta']['point']:+.4%}, "
            f"95%=[{gate['full_pr_auc_delta']['lower_95']:+.4%}, {gate['full_pr_auc_delta']['upper_95']:+.4%}], "
            f"proper-score protected={gate['proper_score_protection']['all']}, "
            f"leagues positive={gate['positive_leagues']}/5, seasons positive={gate['positive_modeled_seasons']}/3, "
            f"B100 delta={gate['b100_pr_auc_delta']:+.4%}, advance={gate['advance_candidate']}"
        )
    lines += ["", "No formal model, data, config, CURRENT or formal weight is changed.", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default=str(OUT))
    ap.add_argument("--print-summary", action="store_true")
    args = ap.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows, recon = e3f0_entry.audit.reconstruct_fixed_sample()
    if len(rows) != EXPECTED:
        raise RuntimeError(f"sample={len(rows)} != {EXPECTED}")
    rows, internal_names, source_audit = attach_internal(rows)
    bkeys = e3f1a.b100_keys(rows)
    schema_sha = hashlib.sha256("\n".join(internal_names).encode()).hexdigest()

    predictions, models = {}, {}
    for gi, group in enumerate(GROUPS):
        for ki, kind in enumerate(MODEL_TYPES):
            key = f"{group}__{kind}"
            pred, folds = rolling_oof(rows, group, kind, internal_names)
            predictions[key] = pred
            models[key] = {
                "group": group, "model_type": kind, "folds": folds,
                "feature_count": folds[-1]["feature_count"] if folds else 0,
                "audit": audit_model(pred, bkeys, SEED + gi*100 + ki*10),
            }

    gates = {}
    comparisons = (
        ("G_MARKET_INTERNAL", "A_MARKET"),
        ("F_INTERNAL_ALL", "B_LEGACY_TEAM"),
    )
    for candidate_group, baseline_group in comparisons:
        for kind in MODEL_TYPES:
            ckey, bkey = f"{candidate_group}__{kind}", f"{baseline_group}__{kind}"
            c, base = models[ckey]["audit"], models[bkey]["audit"]
            delta = paired_pr_delta(predictions[ckey], predictions[bkey], SEED + len(gates)*100)
            league_deltas = {}
            for cid in b100.BIG5:
                league_deltas[cid] = c["per_league"][cid]["draw"]["pr_auc"] - base["per_league"][cid]["draw"]["pr_auc"]
            modeled_years = ("2023", "2024", "2025")
            season_deltas = {
                y: c["per_season"][y]["draw"]["pr_auc"] - base["per_season"][y]["draw"]["pr_auc"]
                for y in modeled_years
            }
            b100_delta = c["b100"]["draw"]["pr_auc"] - base["b100"]["draw"]["pr_auc"]
            proper = protected(c["full"]["hda"], base["full"]["hda"])
            top_c = c["full"]["draw"]["top_candidates"]["top_10pct"]["precision"]
            top_b = base["full"]["draw"]["top_candidates"]["top_10pct"]["precision"]
            gate = {
                "candidate": ckey, "baseline": bkey,
                "full_pr_auc_delta": delta,
                "top10_precision_not_lower": top_c >= top_b,
                "proper_score_protection": proper,
                "league_pr_auc_deltas": league_deltas,
                "positive_leagues": sum(v > 0 for v in league_deltas.values()),
                "season_pr_auc_deltas": season_deltas,
                "positive_modeled_seasons": sum(v > 0 for v in season_deltas.values()),
                "b100_pr_auc_delta": b100_delta,
            }
            gate["advance_candidate"] = bool(
                delta["lower_95"] is not None and delta["lower_95"] > 0
                and gate["top10_precision_not_lower"] and proper["all"]
                and gate["positive_leagues"] >= 3
                and gate["positive_modeled_seasons"] >= 2
                and b100_delta >= 0
            )
            gates[f"{candidate_group}_vs_{baseline_group}__{kind}"] = gate

    def best(metric_path, reverse=False):
        def get(item):
            x = item["audit"]["full"]
            for k in metric_path:
                x = x[k]
            return x
        return min(models, key=lambda k: get(models[k])) if not reverse else max(models, key=lambda k: get(models[k]))

    report = {
        "schema_version": "1.0",
        "research_id": "E3f-2A",
        "research_status": "PASS",
        "repository_head": repository_head(),
        "scope": "pure_90_minute_HDA_internal_PIT_ablation",
        "formal_weight": 0,
        "external_records_ingested": 0,
        "class_weight": None,
        "posthoc_threshold": None,
        "candidate_model_spec_count": len(models),
        "candidate_binary_fit_count": sum(2 for m in models.values() for f in m["folds"] if f["status"] == "MODELED"),
        "sample_count": len(rows),
        "b100_count": len(bkeys),
        "internal_feature_count": len(internal_names),
        "internal_feature_names": internal_names,
        "internal_schema_sha256": schema_sha,
        "reconstruction": recon,
        "source_audit": source_audit,
        "baselines": baseline_metrics(rows),
        "models": models,
        "incremental_gates": gates,
        "best_full": {
            "draw_pr_auc": best(("draw", "pr_auc"), True),
            "accuracy": best(("hda", "accuracy"), True),
            "macro_f1": best(("hda", "macro_f1"), True),
            "logloss": best(("hda", "logloss"), False),
        },
        "verdict": {
            "any_internal_incremental_candidate": any(g["advance_candidate"] for g in gates.values()),
            "training_is_research_only": True,
            "promotion_candidate": False,
            "formal_assets_changed": 0,
            "next_step": "stop for review; no automatic threshold, external source join, or formalization",
        },
    }
    (out / "e3f2a_internal_pit_ablation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "e3f2a_internal_pit_ablation.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["research_status"],
            "sample": report["sample_count"],
            "models": len(models),
            "best": report["best_full"],
            "incremental_candidate": report["verdict"]["any_internal_incremental_candidate"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
