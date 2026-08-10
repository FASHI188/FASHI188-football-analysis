#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"
LABELS = ("H", "D", "A")


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r40g", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def num(v: str) -> float:
    try:
        x = float(str(v).strip())
    except Exception:
        return math.nan
    return x if math.isfinite(x) else math.nan


def raw_key(raw: dict[str, str]) -> str | None:
    try:
        dt = date.fromisoformat(str(raw.get("date", "")).strip())
    except Exception:
        return None
    comp = str(raw.get("competition_id", "")).strip()
    season = str(raw.get("season", "")).strip()
    home = str(raw.get("home_team", "")).strip()
    away = str(raw.get("away_team", "")).strip()
    if not all((comp, season, home, away)):
        return None
    return f"{comp}|{season}|{dt.isoformat()}|{home}|{away}"


def load_venue_features(root: Path, alpha: float) -> tuple[dict[str, tuple[float, ...]], dict]:
    features: dict[str, tuple[float, ...]] = {}
    rows_seen = 0
    ineligible = 0
    duplicate = 0
    for p in sorted(root.glob("football-data/training_datasets/*/point_in_time.csv")):
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            for raw in rd:
                rows_seen += 1
                key = raw_key(raw)
                if key is None:
                    ineligible += 1
                    continue
                hm = num(raw.get("home_history_matches", ""))
                am = num(raw.get("away_history_matches", ""))
                hvm = num(raw.get("home_venue_matches", ""))
                avm = num(raw.get("away_venue_matches", ""))
                hgf = num(raw.get("home_history_gf", ""))
                hga = num(raw.get("home_history_ga", ""))
                agf = num(raw.get("away_history_gf", ""))
                aga = num(raw.get("away_history_ga", ""))
                hvgf_raw = num(raw.get("home_venue_gf", ""))
                hvga_raw = num(raw.get("home_venue_ga", ""))
                avgf_raw = num(raw.get("away_venue_gf", ""))
                avga_raw = num(raw.get("away_venue_ga", ""))
                vals = (hm, am, hvm, avm, hgf, hga, agf, aga, hvgf_raw, hvga_raw, avgf_raw, avga_raw)
                if not all(math.isfinite(v) for v in vals) or hm <= 0 or am <= 0 or hvm < 0 or avm < 0:
                    ineligible += 1
                    continue
                h_hist_gf = hgf / hm
                h_hist_ga = hga / hm
                a_hist_gf = agf / am
                a_hist_ga = aga / am
                hvgf = (hvgf_raw + alpha * h_hist_gf) / (hvm + alpha)
                hvga = (hvga_raw + alpha * h_hist_ga) / (hvm + alpha)
                avgf = (avgf_raw + alpha * a_hist_gf) / (avm + alpha)
                avga = (avga_raw + alpha * a_hist_ga) / (avm + alpha)
                feat = (
                    abs(hvgf - avgf),
                    abs(hvga - avga),
                    hvgf + avgf,
                    hvga + avga,
                    abs((hvgf + hvga) - (avgf + avga)),
                    abs((hvgf - hvga) - (avgf - avga)),
                )
                if not all(math.isfinite(v) for v in feat):
                    ineligible += 1
                    continue
                if key in features:
                    duplicate += 1
                    raise RuntimeError(f"DUPLICATE_VENUE_FEATURE_KEY:{key}")
                features[key] = feat
    return features, {
        "raw_rows_seen": rows_seen,
        "venue_feature_rows": len(features),
        "ineligible_rows": ineligible,
        "duplicate_keys": duplicate,
    }


def combine(p_draw: float, p_home_given_non_draw: float) -> dict[str, float]:
    p = {
        "D": p_draw,
        "H": (1.0 - p_draw) * p_home_given_non_draw,
        "A": (1.0 - p_draw) * (1.0 - p_home_given_non_draw),
    }
    total = sum(p.values())
    if abs(total - 1.0) > 1e-10:
        raise RuntimeError(f"PROBABILITY_CONSERVATION_FAIL:{total}")
    return p


def binary_draw_log_loss(records: list[dict]) -> float:
    if not records:
        raise RuntimeError("EMPTY_BINARY_LOG_LOSS")
    total = 0.0
    for r in records:
        p = min(max(float(r["probabilities"]["D"]), 1e-12), 1.0 - 1e-12)
        y = 1.0 if r["actual"] == "D" else 0.0
        total += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
    return total / len(records)


def draw_auc(w, records: list[dict]) -> float | None:
    labels = [r["actual"] == "D" for r in records]
    if not any(labels) or all(labels):
        return None
    return w.auc_binary([float(r["probabilities"]["D"]) for r in records], labels)


def safe_metric(v) -> float:
    return float(v) if v is not None else -1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R40G-ROLLING-VENUE-DRAW-1.0"
    fold_cfg = pre["folding"]
    venue_cfg = pre["venue_features"]
    model_cfg = pre["models"]
    hard = pre["hard_boundaries"]
    assert int(fold_cfg["minimum_train_rows"]) == 200
    assert int(fold_cfg["minimum_test_rows"]) == 80
    assert fold_cfg["same_season_training"] is False
    assert float(venue_cfg["empirical_bayes_prior_strength"]) == 5.0
    assert len(venue_cfg["draw_specific_features"]) == 6
    assert venue_cfg["no_post_result_feature_changes"] is True
    assert model_cfg["class_weighting"] is False
    assert model_cfg["candidate_grid"] is False
    assert model_cfg["manual_draw_boost"] is False
    assert model_cfg["threshold_tuning"] is False
    assert model_cfg["abstention"] is False
    assert hard["external_network_requests"] == 0 and hard["football_api_requests"] == 0
    assert hard["new_data_collection"] is False
    assert hard["random_fixed100_consumed"] == 0

    w = load_r39w_module()
    u = w.load_r39u_module()
    rows = [r for r in u.load_rows(args.root) if u.finite_vec(r.x)]
    venue_map, venue_audit = load_venue_features(args.root, float(venue_cfg["empirical_bayes_prior_strength"]))

    by_comp: dict[str, list] = defaultdict(list)
    for r in rows:
        by_comp[r.competition].append(r)
    for comp in by_comp:
        by_comp[comp].sort(key=lambda r: (r.dt, r.key))

    min_train = int(fold_cfg["minimum_train_rows"])
    min_test = int(fold_cfg["minimum_test_rows"])
    baseline_records: list[dict] = []
    challenger_records: list[dict] = []
    fold_metrics: list[dict] = []
    skipped_folds: list[dict] = []
    baseline_test_denominator = 0
    venue_test_numerator = 0

    for comp, comp_rows in sorted(by_comp.items()):
        seasons: dict[str, list] = defaultdict(list)
        for r in comp_rows:
            seasons[r.season].append(r)
        season_groups = sorted(seasons.items(), key=lambda kv: (min(r.dt for r in kv[1]), kv[0]))
        for season, test_all in season_groups:
            test_all = sorted(test_all, key=lambda r: (r.dt, r.key))
            test_start = min(r.dt for r in test_all)
            train_all = [r for r in comp_rows if r.dt < test_start]
            if len(train_all) < min_train or len(test_all) < min_test:
                skipped_folds.append({
                    "competition": comp, "season": season,
                    "train_all_n": len(train_all), "test_all_n": len(test_all),
                    "reason": "BASELINE_MINIMUM_ROWS",
                })
                continue

            baseline_test_denominator += len(test_all)
            train = [r for r in train_all if r.key in venue_map]
            test = [r for r in test_all if r.key in venue_map]
            venue_test_numerator += len(test)
            if len(train) < min_train or len(test) < min_test:
                skipped_folds.append({
                    "competition": comp, "season": season,
                    "train_all_n": len(train_all), "test_all_n": len(test_all),
                    "venue_train_n": len(train), "venue_test_n": len(test),
                    "reason": "VENUE_MINIMUM_ROWS",
                })
                continue

            Xb, _ = w.standardize([r.x for r in train], train[0].x, u.quantile)
            ydraw = [1 if r.label == "D" else 0 for r in train]
            bbase, base_draw_iters = w.fit_logistic(Xb, ydraw, 10.0)

            train_ch = [tuple(r.x) + tuple(venue_map[r.key]) for r in train]
            Xc, _ = w.standardize(train_ch, train_ch[0], u.quantile)
            bchall, chall_draw_iters = w.fit_logistic(Xc, ydraw, 10.0)

            nd = [r for r in train if r.label != "D"]
            Xs, _ = w.standardize([w.side_features(r.x) for r in nd], w.side_features(nd[0].x), u.quantile)
            yside = [1 if r.label == "H" else 0 for r in nd]
            bside, side_iters = w.fit_logistic(Xs, yside, 10.0)

            # Build train-only scaling transforms explicitly so every test row uses frozen fold statistics.
            def transform(train_raw, target_raw):
                d = len(target_raw)
                out = [1.0]
                for j in range(d):
                    vals = [x[j] for x in train_raw]
                    med = u.quantile(vals, 0.5)
                    scale = u.quantile(vals, 0.75) - u.quantile(vals, 0.25)
                    if not math.isfinite(scale) or scale < 1e-9:
                        scale = 1.0
                    out.append((target_raw[j] - med) / scale)
                return out

            base_train_raw = [r.x for r in train]
            chall_train_raw = train_ch
            side_train_raw = [w.side_features(r.x) for r in nd]
            fold_base = []
            fold_chall = []
            for r in test:
                xb = transform(base_train_raw, r.x)
                xc_raw = tuple(r.x) + tuple(venue_map[r.key])
                xc = transform(chall_train_raw, xc_raw)
                xs = transform(side_train_raw, w.side_features(r.x))
                pD_base = w.predict_prob(bbase, xb)
                pD_chall = w.predict_prob(bchall, xc)
                pHnd = w.predict_prob(bside, xs)
                pb = combine(pD_base, pHnd)
                pc = combine(pD_chall, pHnd)
                rb = {
                    "competition": comp, "season": season, "key": r.key,
                    "date": r.dt.isoformat(), "actual": r.label,
                    "prediction": u.choose(pb),
                    "probabilities": {lab: round(pb[lab], 12) for lab in LABELS},
                }
                rc = {
                    "competition": comp, "season": season, "key": r.key,
                    "date": r.dt.isoformat(), "actual": r.label,
                    "prediction": u.choose(pc),
                    "probabilities": {lab: round(pc[lab], 12) for lab in LABELS},
                    "venue_features": {name: value for name, value in zip(venue_cfg["draw_specific_features"], venue_map[r.key])},
                }
                fold_base.append(rb)
                fold_chall.append(rc)
                baseline_records.append(rb)
                challenger_records.append(rc)

            mb = u.metrics(fold_base)
            mc = u.metrics(fold_chall)
            bdrawll = binary_draw_log_loss(fold_base)
            cdrawll = binary_draw_log_loss(fold_chall)
            bauc = draw_auc(w, fold_base)
            cauc = draw_auc(w, fold_chall)
            fold_metrics.append({
                "competition": comp, "season": season,
                "test_start": test_start.isoformat(),
                "train_all_n": len(train_all), "test_all_n": len(test_all),
                "train_n": len(train), "test_n": len(test),
                "baseline_metrics": mb, "challenger_metrics": mc,
                "baseline_binary_draw_log_loss": bdrawll,
                "challenger_binary_draw_log_loss": cdrawll,
                "baseline_draw_auc": bauc, "challenger_draw_auc": cauc,
                "challenger_draw_ll_win": cdrawll < bdrawll,
                "solver_iterations": {
                    "baseline_draw": base_draw_iters,
                    "challenger_draw": chall_draw_iters,
                    "conditional_HA": side_iters,
                },
            })

    if not fold_metrics:
        raise RuntimeError("NO_ELIGIBLE_R40G_FOLDS")
    coverage = venue_test_numerator / baseline_test_denominator if baseline_test_denominator else 0.0
    bm = u.metrics(baseline_records)
    cm = u.metrics(challenger_records)
    bdrawll = binary_draw_log_loss(baseline_records)
    cdrawll = binary_draw_log_loss(challenger_records)
    bauc = draw_auc(w, baseline_records)
    cauc = draw_auc(w, challenger_records)
    if bauc is None or cauc is None:
        raise RuntimeError("POOLED_DRAW_AUC_CLASS_MISSING")

    fold_wins = sum(f["challenger_draw_ll_win"] for f in fold_metrics)
    fold_win_rate = fold_wins / len(fold_metrics)

    by_comp_base: dict[str, list[dict]] = defaultdict(list)
    by_comp_ch: dict[str, list[dict]] = defaultdict(list)
    comp_fold_count: dict[str, int] = defaultdict(int)
    for f in fold_metrics:
        comp_fold_count[f["competition"]] += 1
    for r in baseline_records:
        by_comp_base[r["competition"]].append(r)
    for r in challenger_records:
        by_comp_ch[r["competition"]].append(r)
    competition_metrics = []
    eligible_comp = []
    for comp in sorted(by_comp_base):
        bll = binary_draw_log_loss(by_comp_base[comp])
        cll = binary_draw_log_loss(by_comp_ch[comp])
        item = {
            "competition": comp,
            "eligible_folds": comp_fold_count[comp],
            "n": len(by_comp_base[comp]),
            "baseline_binary_draw_log_loss": bll,
            "challenger_binary_draw_log_loss": cll,
            "challenger_draw_ll_win": cll < bll,
        }
        competition_metrics.append(item)
        if comp_fold_count[comp] >= 2:
            eligible_comp.append(item)
    comp_wins = sum(x["challenger_draw_ll_win"] for x in eligible_comp)
    comp_win_rate = comp_wins / len(eligible_comp) if eligible_comp else 0.0

    bf1 = safe_metric(bm["draw_f1"])
    cf1 = safe_metric(cm["draw_f1"])
    brec = safe_metric(bm["draw_recall"])
    crec = safe_metric(cm["draw_recall"])
    gate = {
        "coverage_ge_0_95": coverage >= 0.95,
        "pooled_HDA_log_loss_better": cm["log_loss"] < bm["log_loss"],
        "pooled_binary_draw_log_loss_better": cdrawll < bdrawll,
        "pooled_draw_auc_plus_0_005": cauc >= bauc + 0.005,
        "draw_f1_plus_0_03": cf1 >= bf1 + 0.03,
        "draw_recall_nonworse": crec >= brec,
        "accuracy_noninferior_within_0_01": cm["accuracy"] >= bm["accuracy"] - 0.01,
        "fold_draw_ll_win_rate_ge_0_55": fold_win_rate >= 0.55,
        "competition_draw_ll_win_rate_ge_0_60": comp_win_rate >= 0.60,
    }
    passed = all(gate.values())
    terminal = "PASS_R40G_VENUE_DRAW_INCREMENT" if passed else "FAIL_R40G_VENUE_DRAW_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(rows),
        "venue_audit": venue_audit,
        "baseline_test_denominator": baseline_test_denominator,
        "venue_test_numerator": venue_test_numerator,
        "coverage": coverage,
        "eligible_fold_count": len(fold_metrics),
        "rows_scored": len(baseline_records),
        "competition_count": len(by_comp_base),
        "competitions_with_at_least_two_folds": len(eligible_comp),
        "baseline_metrics": bm,
        "challenger_metrics": cm,
        "baseline_binary_draw_log_loss": bdrawll,
        "challenger_binary_draw_log_loss": cdrawll,
        "baseline_draw_auc": bauc,
        "challenger_draw_auc": cauc,
        "fold_draw_ll_win_count": fold_wins,
        "fold_draw_ll_win_rate": fold_win_rate,
        "competition_draw_ll_win_count": comp_wins,
        "competition_draw_ll_win_rate": comp_win_rate,
        "fold_metrics": fold_metrics,
        "competition_metrics": competition_metrics,
        "skipped_folds": skipped_folds,
        "gate": gate,
        "hard_boundaries": hard,
        "interpretation_boundary": "Retrospective rolling venue-history challenger only. It consumes no random fixed100 sample. Even PASS authorizes only separately preregistered confirmation; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r40g_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r40g_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal, "passed": passed,
        "coverage": coverage,
        "eligible_fold_count": len(fold_metrics),
        "rows_scored": len(baseline_records),
        "competition_count": len(by_comp_base),
        "baseline_metrics": bm, "challenger_metrics": cm,
        "baseline_binary_draw_log_loss": bdrawll,
        "challenger_binary_draw_log_loss": cdrawll,
        "baseline_draw_auc": bauc, "challenger_draw_auc": cauc,
        "fold_draw_ll_win_rate": fold_win_rate,
        "competition_draw_ll_win_rate": comp_win_rate,
        "gate": gate,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
