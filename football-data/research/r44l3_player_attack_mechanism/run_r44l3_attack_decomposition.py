#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline

SEASONS = ["2021/22", "2022/23", "2023/24", "2024/25", "2025/26"]
FOLD_TARGETS = ["2023/24", "2024/25", "2025/26"]
FPL_FOLDERS = {
    "2022/23": "2022-23",
    "2023/24": "2023-24",
    "2024/25": "2024-25",
    "2025/26": "2025-26",
}
EXPECTED_RAW_HASHES = {
    "2022/23": "b78a6d0456141f9033c32fc4122931baae780ac4a4938683451b1fce7a4fdd15",
    "2023/24": "e9c09c8856f1c86b4f920f46ddd5033af83409439dfda53be925df2a3e7c8a9e",
    "2024/25": "5bbbcba6353b4c72ad273adcc8e3aa451946a826564679788f45b1cb3325b84e",
    "2025/26": "0d09f1f1cb1b5520ec8e2f25238aa652efe2a263d8ca7cb2b6538b27bf86727d",
}
MARKET = ["fair_home", "fair_draw", "fair_away", "home_away_balance", "draw_vs_side_margin", "market_entropy"]
MODEL = dict(learning_rate=0.05, max_iter=250, max_leaf_nodes=7, l2_regularization=4.0, random_state=260803)
PERM_REPS = 100
PERM_SEED = 20260812


def num(x) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def truth(x) -> bool:
    return str(x).strip().lower() in {"true", "1", "yes"}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "r44l3-player-attack-mechanism/1.0"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return resp.read()


def player_rates(hist: deque[dict[str, float]]) -> dict[str, float]:
    vals = list(hist)[-10:]
    minutes = sum(r["minutes"] for r in vals)
    scale = 90.0 / max(minutes, 90.0)
    return {
        "xg": sum(r["xg"] for r in vals) * scale,
        "xa": sum(r["xa"] for r in vals) * scale,
        "xgi": sum(r["xgi"] for r in vals) * scale,
    }


def side_attack(starters: list[dict[str, str]], histories: dict[int, deque[dict[str, float]]]) -> dict[str, float]:
    rates = []
    for row in starters:
        pid = int(num(row.get("element")))
        if pid > 0:
            rates.append(player_rates(histories[pid]))
    if len(rates) != 11:
        raise RuntimeError(f"starter identity count !=11: {len(rates)}")
    xg = np.asarray([r["xg"] for r in rates], dtype=float)
    xa = np.asarray([r["xa"] for r in rates], dtype=float)
    xgi = np.asarray([r["xgi"] for r in rates], dtype=float)
    total = float(xgi.sum())
    shares = xgi / total if total > 1e-12 else np.zeros_like(xgi)
    return {
        "starter_xg_per90_sum": float(xg.sum()),
        "starter_xa_per90_sum": float(xa.sum()),
        "starter_xgi_per90_sum_rebuilt": total,
        "starter_xgi_top1_share": float(np.sort(shares)[-1:].sum()),
        "starter_xgi_top3_share": float(np.sort(shares)[-3:].sum()),
        "starter_xgi_hhi": float(np.square(shares).sum()),
        "starter_attack_active_ge_0p10": float(np.sum(xgi >= 0.10)),
    }


def rebuild_micro_features(r2: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    wanted = {(str(r.season), int(r.fixture)) for r in r2[["season", "fixture"]].itertuples(index=False)}
    rows = []
    ledger = []
    for season, folder in FPL_FOLDERS.items():
        url = f"https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{folder}/gws/merged_gw.csv"
        raw = get(url)
        digest = sha256(raw)
        if digest != EXPECTED_RAW_HASHES[season]:
            raise RuntimeError(f"SOURCE_HASH_DRIFT {season}: {digest} != {EXPECTED_RAW_HASHES[season]}")
        data = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
        ledger.append({"season": season, "url": url, "sha256": digest, "bytes": len(raw), "rows": len(data)})
        groups = defaultdict(list)
        for row in data:
            fid = int(num(row.get("fixture")))
            if fid > 0:
                groups[fid].append(row)
        fixtures = []
        for fid, g in groups.items():
            kickoff = next((r.get("kickoff_time", "") for r in g if r.get("kickoff_time")), "")
            if kickoff:
                fixtures.append((kickoff, fid, g))
        fixtures.sort(key=lambda x: (x[0], x[1]))
        histories: dict[int, deque[dict[str, float]]] = defaultdict(lambda: deque(maxlen=10))
        for kickoff, fid, g in fixtures:
            home = [r for r in g if truth(r.get("was_home"))]
            away = [r for r in g if not truth(r.get("was_home"))]
            if not home or not away:
                continue
            hs = [r for r in home if num(r.get("starts")) > 0]
            aas = [r for r in away if num(r.get("starts")) > 0]
            if (season, fid) in wanted:
                if len(hs) != 11 or len(aas) != 11:
                    raise RuntimeError(f"R2 target fixture no longer exact XI season={season} fixture={fid} h={len(hs)} a={len(aas)}")
                h = side_attack(hs, histories)
                a = side_attack(aas, histories)
                out = {"season": season, "fixture": fid}
                for name in h:
                    out[f"home_{name}"] = h[name]
                    out[f"away_{name}"] = a[name]
                    out[f"diff_{name}"] = h[name] - a[name]
                    out[f"absdiff_{name}"] = abs(h[name] - a[name])
                    out[f"sum_{name}"] = h[name] + a[name]
                rows.append(out)
            for row in g:
                pid = int(num(row.get("element")))
                if pid <= 0:
                    continue
                xg = num(row.get("expected_goals"))
                xa = num(row.get("expected_assists"))
                xgi_field = num(row.get("expected_goal_involvements"))
                histories[pid].append({
                    "minutes": num(row.get("minutes")),
                    "xg": xg,
                    "xa": xa,
                    "xgi": xgi_field if xgi_field != 0 else xg + xa,
                })
    micro = pd.DataFrame(rows)
    merged = r2.merge(micro, on=["season", "fixture"], how="left", validate="one_to_one")
    if merged.filter(regex="starter_xg|starter_xa|starter_xgi").isna().any().any():
        miss = merged[merged["home_starter_xg_per90_sum"].isna()][["season", "fixture"]].to_dict("records")
        raise RuntimeError(f"micro coverage missing: {miss[:20]}")
    # Hard integrity gate: rebuilt legacy xGI must reproduce the frozen R2 feature.
    h_err = np.max(np.abs(merged["home_starter_xgi_per90_sum_rebuilt"] - merged["home_lineup_prior_xgi_per90_sum"]))
    a_err = np.max(np.abs(merged["away_starter_xgi_per90_sum_rebuilt"] - merged["away_lineup_prior_xgi_per90_sum"]))
    d_err = np.max(np.abs(merged["diff_starter_xgi_per90_sum_rebuilt"] - merged["diff_lineup_prior_xgi_per90_sum"]))
    integrity = {"home_xgi_max_abs_error": float(h_err), "away_xgi_max_abs_error": float(a_err), "diff_xgi_max_abs_error": float(d_err)}
    if max(h_err, a_err, d_err) > 1e-10:
        raise RuntimeError(f"LEGACY_XGI_REBUILD_MISMATCH {integrity}")
    return merged, {"source_ledger": ledger, "legacy_xgi_integrity": integrity}


def model():
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("model", HistGradientBoostingClassifier(**MODEL)),
    ])


def metrics(y, p):
    return {
        "pr_auc": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-9, 1 - 1e-9))),
        "brier": float(brier_score_loss(y, p)),
    }


def fit_score(train: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    m = model()
    ytr = train["label_draw"].astype(int).to_numpy()
    m.fit(train[features].astype(float).to_numpy(), ytr)
    return m.predict_proba(test[features].astype(float).to_numpy())[:, 1]


def candidate_map() -> dict[str, list[str]]:
    xg = "starter_xg_per90_sum"
    xa = "starter_xa_per90_sum"
    xgi = "starter_xgi_per90_sum_rebuilt"
    top1 = "starter_xgi_top1_share"
    top3 = "starter_xgi_top3_share"
    hhi = "starter_xgi_hhi"
    active = "starter_attack_active_ge_0p10"
    starts = "lineup_prior_starts_10_sum"
    return {
        "xgi_diff_only_rebuild": [f"diff_{xgi}"],
        "xg_diff_only": [f"diff_{xg}"],
        "xa_diff_only": [f"diff_{xa}"],
        "xg_xa_diff": [f"diff_{xg}", f"diff_{xa}"],
        "xgi_balance": [f"diff_{xgi}", f"absdiff_{xgi}", f"sum_{xgi}"],
        "xg_balance": [f"diff_{xg}", f"absdiff_{xg}", f"sum_{xg}"],
        "xa_balance": [f"diff_{xa}", f"absdiff_{xa}", f"sum_{xa}"],
        "xg_xa_balance": [f"diff_{xg}", f"absdiff_{xg}", f"sum_{xg}", f"diff_{xa}", f"absdiff_{xa}", f"sum_{xa}"],
        "xgi_diff_plus_starts_diff": [f"diff_{xgi}", f"diff_{starts}"],
        "xg_xa_plus_starts_diff": [f"diff_{xg}", f"diff_{xa}", f"diff_{starts}"],
        "attack_concentration": [f"diff_{top1}", f"absdiff_{top1}", f"diff_{top3}", f"absdiff_{top3}", f"diff_{hhi}", f"absdiff_{hhi}"],
        "attack_breadth": [f"diff_{active}", f"absdiff_{active}", f"sum_{active}"],
        "micro_core": [f"diff_{xg}", f"absdiff_{xg}", f"sum_{xg}", f"diff_{xa}", f"absdiff_{xa}", f"sum_{xa}", f"diff_{starts}", f"diff_{top3}", f"absdiff_{top3}", f"diff_{hhi}", f"absdiff_{hhi}", f"diff_{active}", f"absdiff_{active}"],
    }


def rolling_eval(df: pd.DataFrame, candidates: dict[str, list[str]]):
    folds = []
    scores = []
    for target in FOLD_TARGETS:
        ti = SEASONS.index(target)
        train = df[df["season"].isin(SEASONS[:ti])].copy()
        test = df[df["season"] == target].copy()
        y = test["label_draw"].astype(int).to_numpy()
        base = fit_score(train, test, MARKET)
        bm = metrics(y, base)
        for name, extra in candidates.items():
            p = fit_score(train, test, MARKET + extra)
            cm = metrics(y, p)
            folds.append({
                "target_season": target, "candidate": name, "rows": len(test), "draws": int(y.sum()),
                **{f"market_{k}": v for k, v in bm.items()}, **{f"candidate_{k}": v for k, v in cm.items()},
                "delta_pr_auc": cm["pr_auc"] - bm["pr_auc"], "delta_roc_auc": cm["roc_auc"] - bm["roc_auc"],
                "delta_log_loss": cm["log_loss"] - bm["log_loss"], "delta_brier": cm["brier"] - bm["brier"],
            })
            for j, row in enumerate(test.itertuples(index=False)):
                scores.append({"target_season": target, "candidate": name, "fixture": int(row.fixture), "label_draw": int(row.label_draw), "market_score": float(base[j]), "candidate_score": float(p[j]), "fair_home": float(row.fair_home), "fair_draw": float(row.fair_draw), "fair_away": float(row.fair_away)})
    return pd.DataFrame(folds), pd.DataFrame(scores)


def summarize(folds: pd.DataFrame, candidates: dict[str, list[str]]):
    out = []
    for name in candidates:
        g = folds[folds["candidate"] == name]
        pr = g["delta_pr_auc"].to_numpy(float); roc = g["delta_roc_auc"].to_numpy(float)
        ll = g["delta_log_loss"].to_numpy(float); br = g["delta_brier"].to_numpy(float)
        out.append({
            "candidate": name, "features": candidates[name], "positive_pr_folds": int((pr > 0).sum()),
            "median_delta_pr_auc": float(np.median(pr)), "worst_delta_pr_auc": float(np.min(pr)),
            "median_delta_roc_auc": float(np.median(roc)), "median_delta_log_loss": float(np.median(ll)),
            "median_delta_brier": float(np.median(br)),
            "diagnostic_stable": bool((pr > 0).all() and np.median(ll) <= 0 and np.median(br) <= 0),
        })
    out.sort(key=lambda r: (r["diagnostic_stable"], r["worst_delta_pr_auc"], r["median_delta_pr_auc"], r["median_delta_roc_auc"]), reverse=True)
    return out


def action_diag(score_df: pd.DataFrame, winner: str):
    d = score_df[score_df["candidate"] == winner].copy()
    q = float(np.quantile(d["candidate_score"], 0.90))
    sel = d[d["candidate_score"] >= q]
    hits = int(sel["label_draw"].sum()); n = len(sel); total_draws = int(d["label_draw"].sum())
    precision = hits / n if n else 0.0; recall = hits / total_draws if total_draws else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    market_pred = np.argmax(d[["fair_home", "fair_draw", "fair_away"]].to_numpy(), axis=1)
    actual = []
    for r in d.itertuples(index=False):
        # label_draw alone cannot distinguish H/A; use only draw-forcing accuracy delta from known market correctness on non-selected is not identifiable here.
        actual.append(int(r.label_draw))
    market_selected_draw_rate = float(sel["fair_draw"].mean()) if n else 0.0
    return {"coverage_target": 0.10, "threshold_same_consumed_oof": q, "selected": n, "draw_hits": hits, "precision": precision, "recall": recall, "f1": f1, "mean_market_draw_probability_selected": market_selected_draw_rate, "complete_1x2_accuracy": "NOT_RECOMPUTED_IN_THIS_DIAGNOSTIC"}


def permutation_test(df: pd.DataFrame, candidate: str, extras: list[str], actual_median: float):
    rng = np.random.default_rng(PERM_SEED)
    medians = []
    for rep in range(PERM_REPS):
        work = df.copy()
        signed = work["fair_home"].astype(float) - work["fair_away"].astype(float)
        # preserve season and coarse market-strength stratum while breaking match-specific lineup assignment
        bins = pd.qcut(signed.rank(method="first"), 10, labels=False, duplicates="drop")
        work["_bin"] = bins
        for (_, _), idx in work.groupby(["season", "_bin"], sort=False).groups.items():
            idx = np.asarray(list(idx), dtype=int)
            if len(idx) > 1:
                perm = rng.permutation(idx)
                vals = work.loc[perm, extras].to_numpy(copy=True)
                work.loc[idx, extras] = vals
        f, _ = rolling_eval(work.drop(columns=["_bin"]), {candidate: extras})
        medians.append(float(np.median(f["delta_pr_auc"].to_numpy(float))))
    arr = np.asarray(medians)
    p = (1 + int(np.sum(arr >= actual_median))) / (1 + len(arr))
    return {"reps": PERM_REPS, "seed": PERM_SEED, "market_strata": 10, "actual_median_delta_pr_auc": actual_median, "permuted_median_mean": float(arr.mean()), "permuted_p95": float(np.quantile(arr, 0.95)), "empirical_p_one_sided": float(p)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--r2-dataset", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    r2 = pd.read_csv(args.r2_dataset)
    if len(r2) != 1231:
        raise RuntimeError(f"frozen R2 row count drift: {len(r2)} != 1231")
    df, source_audit = rebuild_micro_features(r2)
    candidates = candidate_map()
    folds, scores = rolling_eval(df, candidates)
    summary = summarize(folds, candidates)
    winner = summary[0]["candidate"] if summary else None
    winner_row = summary[0] if summary else None
    perm = permutation_test(df, winner, candidates[winner], float(winner_row["median_delta_pr_auc"])) if winner else None
    action = action_diag(scores, winner) if winner else None
    # Correlation audit against signed market strength.
    market_signed = df["fair_home"].astype(float) - df["fair_away"].astype(float)
    corr = {}
    for col in ["diff_starter_xg_per90_sum", "diff_starter_xa_per90_sum", "diff_starter_xgi_per90_sum_rebuilt"]:
        corr[col] = {"pearson_vs_fair_home_minus_away": float(np.corrcoef(df[col].astype(float), market_signed)[0,1]), "spearman_vs_fair_home_minus_away": float(pd.Series(df[col]).corr(pd.Series(market_signed), method="spearman"))}
    result = {
        "study_id": "r44l3_player_attack_mechanism_diagnostic",
        "status": "POST_HOC_MECHANISM_DIAGNOSTIC_COMPLETE",
        "formal_weight": 0,
        "promotion_allowed": False,
        "independent_validation": False,
        "consumed_historical_sample_reused": True,
        "new_protected_sample_consumed": False,
        "dataset_rows": int(len(df)),
        "targets": FOLD_TARGETS,
        "source_audit": source_audit,
        "candidate_summary": summary,
        "diagnostic_winner": winner_row,
        "market_correlation": corr,
        "winner_market_stratified_permutation": perm,
        "winner_same_sample_top10_action_diagnostic": action,
        "interpretation_boundary": "Mechanism discovery only. Results cannot promote or claim solved draw prediction; any candidate requires untouched forward confirmation.",
    }
    df.to_csv(args.out / "r44l3_micro_feature_rows.csv", index=False)
    folds.to_csv(args.out / "r44l3_fold_metrics.csv", index=False)
    scores.to_csv(args.out / "r44l3_oof_scores.csv", index=False)
    (args.out / "r44l3_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {}
    for p in args.out.iterdir():
        if p.is_file() and p.name != "artifact_manifest.json":
            b = p.read_bytes(); manifest[p.name] = {"sha256": sha256(b), "bytes": len(b)}
    (args.out / "artifact_manifest.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "winner": winner, "winner_summary": winner_row, "permutation": perm}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
