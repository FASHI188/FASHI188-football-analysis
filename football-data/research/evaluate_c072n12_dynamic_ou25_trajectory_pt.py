#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DYNAMIC_SHA = "c0e8854302159e1a8c529463f33280b728909c5e0ba95262515a7a144a43aa2a"
MANIFEST_SHA = "96eb5dd412883d903cdf2ff0ebbffb8244f1a6f552fbeea1d4a1153f3a7b7faf"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
FILES = {
    "PREMIER LEAGUE": "data/england/premier-league.csv",
    "LIGA": "data/spain/laliga.csv",
    "BUNDESLIGA": "data/germany/bundesliga.csv",
    "SERIE A": "data/italy/serie-a.csv",
    "LIGUE 1": "data/france/ligue-1.csv",
}
K = 8
B0_NUM = ["L1"]
B1_NUM = ["L24", "L1"]
C_NUM = ["L24", "L6", "L1", "path_total_variation", "path_range", "path_log1p_updates", "path_max_abs_step"]
SUMMARY = Path("football-data/research/c072n12_dynamic_ou25_trajectory_pt_summary.json")
PRED = Path("football-data/research/c072n12_dynamic_ou25_trajectory_pt_predictions.csv")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_dt(x: str) -> datetime:
    s = str(x).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    z = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return z.replace(tzinfo=None) if z.tzinfo is not None else z


def parse_price(x: str) -> float | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def market_logit(under: float, over: float) -> float:
    q = (1.0 / over) / ((1.0 / over) + (1.0 / under))
    q = min(max(q, 1e-6), 1.0 - 1e-6)
    return float(math.log(q / (1.0 - q)))


def parse_nonneg_int(x: str) -> int | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < 0 or not float(v).is_integer():
        return None
    return int(v)


def raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/{path}"


def fetch(path: str) -> bytes:
    req = urllib.request.Request(raw_url(path), headers={"User-Agent": "football3-c072n12"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("complete_24h_6h_1h") == "1"]


def decode_authorized_targets(manifest: list[dict[str, str]]) -> tuple[dict[tuple[str, int], int], dict]:
    selected: dict[str, dict[int, dict[str, str]]] = {c: {} for c in FILES}
    for r in manifest:
        comp = r["competition"]
        idx = int(r["kickoff_source_row_index"])
        if idx in selected[comp]:
            raise RuntimeError(f"duplicate authorized row {comp}:{idx}")
        selected[comp][idx] = r

    target = {}
    meta = {}
    invalid = 0
    decoded = 0
    for comp, source_path in FILES.items():
        raw = fetch(source_path)
        text = raw.decode("utf-8-sig")
        lines = text.splitlines()
        header = next(csv.reader([lines[0]]))
        hi = {h: i for i, h in enumerate(header)}
        required = {"Date", "Season", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
        if not required.issubset(hi):
            raise RuntimeError(f"missing target fields in {source_path}")
        max_needed = max(hi[x] for x in required)
        for idx in sorted(selected[comp]):
            physical = idx + 1
            if physical >= len(lines):
                raise RuntimeError(f"authorized row out of range {comp}:{idx}")
            # Binding boundary: only authorized physical rows are CSV-decoded.
            row = next(csv.reader([lines[physical]]))
            if len(row) <= max_needed:
                raise RuntimeError(f"short authorized row {comp}:{idx}")
            m = selected[comp][idx]
            if row[hi["Date"]] != m["kickoff_source_date"] or row[hi["HomeTeam"]] != m["kickoff_source_home_team"] or row[hi["AwayTeam"]] != m["kickoff_source_away_team"]:
                raise RuntimeError(f"authorized identity mismatch {comp}:{idx}")
            hg, ag = parse_nonneg_int(row[hi["FTHG"]]), parse_nonneg_int(row[hi["FTAG"]])
            decoded += 1
            if hg is None or ag is None:
                invalid += 1
                continue
            target[(comp, idx)] = min(hg + ag, 7)
        meta[comp] = {
            "path": source_path, "revision": REV,
            "authorized_rows": len(selected[comp]),
            "authorized_rows_csv_decoded": len(selected[comp]),
            "unselected_rows_csv_decoded": 0,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return target, {"files": meta, "authorized_rows_decoded": decoded, "invalid_authorized_labels": invalid}


def build_market_features(manifest: list[dict[str, str]], dynamic_path: Path) -> tuple[pd.DataFrame, dict]:
    base = {}
    for r in manifest:
        comp = r["competition"]
        idx = int(r["kickoff_source_row_index"])
        key = (comp, r["dynamic_home_team"], r["dynamic_away_team"])
        kickoff = parse_dt(r["kickoff_source_date"])
        u24, o24 = parse_price(r["under_24h"]), parse_price(r["over_24h"])
        u6, o6 = parse_price(r["under_6h"]), parse_price(r["over_6h"])
        u1, o1 = parse_price(r["under_1h"]), parse_price(r["over_1h"])
        if None in (u24, o24, u6, o6, u1, o1):
            raise RuntimeError("manifest contains invalid frozen price")
        base[key] = {
            "competition": comp, "source_row_index": idx, "kickoff": kickoff,
            "home": r["dynamic_home_team"], "away": r["dynamic_away_team"],
            "L24": market_logit(u24, o24), "L6": market_logit(u6, o6), "L1": market_logit(u1, o1),
        }

    path_states: dict[tuple[str, str, str], dict[datetime, tuple[float, float]]] = defaultdict(dict)
    conflicting = set()
    with dynamic_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            key = (str(r.get("competition", "")).strip(), str(r.get("home_team", "")).strip(), str(r.get("away_team", "")).strip())
            b = base.get(key)
            if b is None:
                continue
            try:
                t = parse_dt(r.get("U/O 2.5 timestamp", ""))
            except Exception:
                continue
            start = b["kickoff"] - timedelta(hours=24)
            end = b["kickoff"] - timedelta(hours=1)
            if not (t > start and t <= end and t < b["kickoff"]):
                continue
            u, o = parse_price(r.get("odds_under_2.5", "")), parse_price(r.get("odds_over_2.5", ""))
            if u is None or o is None:
                continue
            old = path_states[key].get(t)
            pair = (u, o)
            if old is None:
                path_states[key][t] = pair
            elif old != pair:
                conflicting.add(key)

    rows = []
    for key, b in base.items():
        if key in conflicting:
            continue
        states = [(b["kickoff"] - timedelta(hours=24), b["L24"])]
        for t, (u, o) in sorted(path_states.get(key, {}).items()):
            states.append((t, market_logit(u, o)))
        vals = [z[1] for z in states]
        diffs = np.diff(np.asarray(vals, dtype=float)) if len(vals) > 1 else np.asarray([], dtype=float)
        rows.append({
            **b,
            "path_total_variation": float(np.abs(diffs).sum()) if len(diffs) else 0.0,
            "path_range": float(max(vals) - min(vals)),
            "path_log1p_updates": float(math.log1p(max(0, len(states) - 1))),
            "path_max_abs_step": float(np.abs(diffs).max()) if len(diffs) else 0.0,
            "path_state_count": int(len(states)),
        })
    return pd.DataFrame(rows), {"conflicting_window_identity_count": len(conflicting), "feature_rows": len(rows)}


def build_model(num_cols: list[str]) -> Pipeline:
    numeric = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())])
    pre = ColumnTransformer([
        ("num", numeric, num_cols),
        ("competition", OneHotEncoder(handle_unknown="ignore"), ["competition"]),
    ])
    clf = LogisticRegression(C=0.1, max_iter=3000, class_weight=None, random_state=0, solver="lbfgs")
    return Pipeline([("pre", pre), ("clf", clf)])


def aligned_predict(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    classes = np.asarray(model.named_steps["clf"].classes_, dtype=int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, classes] = raw
    out = np.clip(out, 1e-15, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y, dtype=int)
    one = np.eye(K)[y]
    ll = -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))
    br = np.sum((p - one) ** 2, axis=1)
    cp, cy = np.cumsum(p, axis=1)[:, :-1], np.cumsum(one, axis=1)[:, :-1]
    rps = np.sum((cp - cy) ** 2, axis=1) / (K - 1)
    top1 = np.argmax(p, axis=1)
    top3 = np.argsort(p, axis=1)[:, -3:]
    return {
        "n": int(len(y)), "log_loss": float(ll.mean()), "brier": float(br.mean()), "rps": float(rps.mean()),
        "top1_accuracy": float(np.mean(top1 == y)),
        "top3_accuracy": float(np.mean([y[i] in top3[i] for i in range(len(y))])),
        "top1_total_2_fraction": float(np.mean(top1 == 2)),
        "mean_entropy": float(np.mean(-np.sum(p * np.log(np.clip(p, 1e-15, 1.0)), axis=1))),
        "mean_top1_margin": float(np.mean(np.sort(p, axis=1)[:, -1] - np.sort(p, axis=1)[:, -2])),
        "max_probability_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))) if len(p) else 0.0,
    }


def delta(a: dict, b: dict) -> dict:
    keys = ["log_loss", "brier", "rps", "top1_accuracy", "top3_accuracy", "top1_total_2_fraction", "mean_entropy", "mean_top1_margin"]
    return {k: float(a[k] - b[k]) for k in keys}


def bootstrap(y: np.ndarray, pref: np.ndarray, pcand: np.ndarray, seed: int, reps: int = 3000) -> dict:
    y = np.asarray(y, dtype=int)
    ix = np.arange(len(y))
    d = -np.log(np.clip(pcand[ix, y], 1e-15, 1.0)) + np.log(np.clip(pref[ix, y], 1e-15, 1.0))
    rng = np.random.default_rng(seed)
    sims = np.empty(reps)
    n = len(d)
    for i in range(reps):
        draw = rng.integers(0, n, n)
        sims[i] = d[draw].mean()
    return {
        "n": n, "reps": reps, "seed": seed, "mean_delta_log_loss": float(d.mean()),
        "ci90_low": float(np.quantile(sims, .05)), "ci90_high": float(np.quantile(sims, .95)),
        "p_delta_lt_0": float(np.mean(sims < 0)),
    }


def comparison_pass(name: str, pooled_delta: dict, boot: dict, fold_wins: int, comp_wins: int, max_resid: float, boundary_ok: bool) -> tuple[bool, dict]:
    gates = {
        "pooled_dlogloss_lt_0": pooled_delta["log_loss"] < 0,
        "bootstrap90_upper_dlogloss_lt_0": boot["ci90_high"] < 0,
        "fold_logloss_wins_ge_4_of_5": fold_wins >= 4,
        "pooled_dbrier_le_0": pooled_delta["brier"] <= 0,
        "pooled_drps_le_0": pooled_delta["rps"] <= 0,
        "pooled_top1_delta_ge_0": pooled_delta["top1_accuracy"] >= 0,
        "pooled_top3_delta_ge_0": pooled_delta["top3_accuracy"] >= 0,
        "competition_logloss_wins_ge_4_of_5": comp_wins >= 4,
        "probability_residual_le_1e_12": max_resid <= 1e-12,
        "boundary_ok": boundary_ok,
    }
    return all(gates.values()), gates


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dynamic-csv", required=True)
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()
    dynamic = Path(args.dynamic_csv)
    manifest_path = Path(args.manifest)
    if sha256(dynamic) != DYNAMIC_SHA:
        raise RuntimeError("dynamic SHA mismatch")
    if sha256(manifest_path) != MANIFEST_SHA:
        raise RuntimeError("manifest SHA mismatch")

    manifest = read_manifest(manifest_path)
    target_by_source, target_meta = decode_authorized_targets(manifest)
    feat, path_meta = build_market_features(manifest, dynamic)
    if path_meta["conflicting_window_identity_count"] != 0:
        raise RuntimeError("conflicting path states in authorized identities")

    feat["target"] = [target_by_source.get((r.competition, int(r.source_row_index))) for r in feat.itertuples()]
    invalid_or_missing = int(feat["target"].isna().sum())
    data = feat.dropna(subset=["target"]).copy()
    data["target"] = data["target"].astype(int)
    data = data.sort_values(["kickoff", "competition", "home", "away"]).reset_index(drop=True)

    unique_times = sorted(data["kickoff"].unique())
    m = len(unique_times)
    boundaries = [math.floor(x * m) for x in (0.35, 0.48, 0.61, 0.74, 0.87)] + [m]
    if any(boundaries[i] >= boundaries[i+1] for i in range(5)):
        raise RuntimeError("chronological boundaries collapse")

    pred_parts = []
    folds = []
    all_classes_each_train = True
    max_resid = 0.0
    fold_wins = {"C_vs_B1": 0, "C_vs_B0": 0, "B1_vs_B0": 0}

    for j in range(5):
        start, end = boundaries[j], boundaries[j+1]
        cutoff_start = unique_times[start]
        test_times = set(unique_times[start:end])
        train = data[data["kickoff"] < cutoff_start].copy()
        test = data[data["kickoff"].isin(test_times)].copy()
        classes = sorted(train["target"].unique().tolist())
        if classes != list(range(K)):
            all_classes_each_train = False
            break
        if train.empty or test.empty:
            raise RuntimeError(f"empty fold {j+1}")

        models = {"B0": build_model(B0_NUM), "B1": build_model(B1_NUM), "C": build_model(C_NUM)}
        probs = {}
        for name, model in models.items():
            cols = {"B0": B0_NUM, "B1": B1_NUM, "C": C_NUM}[name] + ["competition"]
            model.fit(train[cols], train["target"])
            probs[name] = aligned_predict(model, test[cols])
        y = test["target"].to_numpy(dtype=int)
        met = {name: metrics(y, p) for name, p in probs.items()}
        d_c_b1, d_c_b0, d_b1_b0 = delta(met["C"], met["B1"]), delta(met["C"], met["B0"]), delta(met["B1"], met["B0"])
        if d_c_b1["log_loss"] < 0: fold_wins["C_vs_B1"] += 1
        if d_c_b0["log_loss"] < 0: fold_wins["C_vs_B0"] += 1
        if d_b1_b0["log_loss"] < 0: fold_wins["B1_vs_B0"] += 1
        max_resid = max(max_resid, *(met[n]["max_probability_residual"] for n in met))
        folds.append({
            "fold": j+1, "train_n": int(len(train)), "test_n": int(len(test)),
            "test_start": str(min(test_times)), "test_end": str(max(test_times)),
            "train_classes": classes, "metrics": met,
            "delta_C_minus_B1": d_c_b1, "delta_C_minus_B0": d_c_b0, "delta_B1_minus_B0": d_b1_b0,
        })
        pp = test[["competition", "source_row_index", "kickoff", "home", "away", "target"]].copy()
        pp["fold"] = j + 1
        for name, p in probs.items():
            for k in range(K):
                pp[f"{name}_p{k}"] = p[:, k]
        pred_parts.append(pp)

    if not all_classes_each_train:
        summary = {
            "schema": "C072N12_DYNAMIC_OU25_TRAJECTORY_PT_V1", "project_line": "football3",
            "classification": "REPLICATION / REPRODUCTION", "terminal": "C072N12_DATA_INSUFFICIENT_ALL_CLASSES",
            "all_classes_each_train": False, "manifest_complete_rows": len(manifest), "feature_rows": len(feat),
            "target_meta": target_meta, "target_rows_missing_or_invalid": invalid_or_missing,
            "C073_C077_quarantined": True, "C070F_confirmation1597_opened": False, "formal_weight": 0,
        }
        SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    pred = pd.concat(pred_parts, ignore_index=True)
    y = pred["target"].to_numpy(dtype=int)
    probs = {name: pred[[f"{name}_p{k}" for k in range(K)]].to_numpy(float) for name in ("B0", "B1", "C")}
    pooled = {name: metrics(y, p) for name, p in probs.items()}
    deltas = {
        "C_vs_B1": delta(pooled["C"], pooled["B1"]),
        "C_vs_B0": delta(pooled["C"], pooled["B0"]),
        "B1_vs_B0": delta(pooled["B1"], pooled["B0"]),
    }
    boots = {
        "C_vs_B1": bootstrap(y, probs["B1"], probs["C"], 72012),
        "C_vs_B0": bootstrap(y, probs["B0"], probs["C"], 72013),
        "B1_vs_B0": bootstrap(y, probs["B0"], probs["B1"], 72014),
    }

    comp_results = {}
    comp_wins = {"C_vs_B1": 0, "C_vs_B0": 0, "B1_vs_B0": 0}
    for comp in FILES:
        mask = pred["competition"].to_numpy() == comp
        yy = y[mask]
        if len(yy) == 0:
            continue
        mm = {name: metrics(yy, p[mask]) for name, p in probs.items()}
        dd = {
            "C_vs_B1": mm["C"]["log_loss"] - mm["B1"]["log_loss"],
            "C_vs_B0": mm["C"]["log_loss"] - mm["B0"]["log_loss"],
            "B1_vs_B0": mm["B1"]["log_loss"] - mm["B0"]["log_loss"],
        }
        for k, v in dd.items():
            if v < 0: comp_wins[k] += 1
        comp_results[comp] = {"n": int(len(yy)), "metrics": mm, "delta_log_loss": {k: float(v) for k, v in dd.items()}}

    boundary_ok = target_meta["authorized_rows_decoded"] == len(manifest) and invalid_or_missing == target_meta["invalid_authorized_labels"]
    primary_pass, primary_gates = comparison_pass("C_vs_B1", deltas["C_vs_B1"], boots["C_vs_B1"], fold_wins["C_vs_B1"], comp_wins["C_vs_B1"], max_resid, boundary_ok)
    broad_pass, broad_gates = comparison_pass("C_vs_B0", deltas["C_vs_B0"], boots["C_vs_B0"], fold_wins["C_vs_B0"], comp_wins["C_vs_B0"], max_resid, boundary_ok)
    endpoint_pass, endpoint_gates = comparison_pass("B1_vs_B0", deltas["B1_vs_B0"], boots["B1_vs_B0"], fold_wins["B1_vs_B0"], comp_wins["B1_vs_B0"], max_resid, boundary_ok)
    breakthrough = primary_pass and deltas["C_vs_B1"]["log_loss"] <= -0.003 and broad_pass and deltas["C_vs_B0"]["log_loss"] <= -0.005

    if breakthrough:
        terminal = "C072N12_DYNAMIC_OU25_TRAJECTORY_BREAKTHROUGH_SCREEN_PASS"
    elif primary_pass:
        terminal = "C072N12_DYNAMIC_OU25_TRAJECTORY_DEVELOPMENT_PASS"
    elif endpoint_pass:
        terminal = "C072N12_ENDPOINT_MOVEMENT_PASS_PATH_SHAPE_NOT_ESTABLISHED"
    else:
        terminal = "C072N12_DYNAMIC_OU25_TRAJECTORY_PARK"

    pred.to_csv(PRED, index=False)
    summary = {
        "schema": "C072N12_DYNAMIC_OU25_TRAJECTORY_PT_V1",
        "project_line": "football3", "classification": "REPLICATION / REPRODUCTION",
        "terminal": terminal, "breakthrough_screen_pass": breakthrough,
        "primary_full_trajectory_vs_endpoint_pass": primary_pass,
        "dynamic_component_vs_static_pass": broad_pass,
        "endpoint_movement_vs_static_pass": endpoint_pass,
        "dynamic_sha256": sha256(dynamic), "manifest_sha256": sha256(manifest_path),
        "manifest_complete_rows": len(manifest), "feature_rows": len(feat), "paired_model_rows": len(data),
        "target_rows_missing_or_invalid": invalid_or_missing, "target_meta": target_meta, "path_meta": path_meta,
        "unique_kickoff_timestamps": m, "fold_boundaries_unique_time_indices": boundaries,
        "all_classes_each_train": all_classes_each_train, "folds": folds, "fold_logloss_wins": fold_wins,
        "development_oos_n": int(len(pred)), "pooled": pooled, "deltas": deltas, "bootstraps": boots,
        "competition_results": comp_results, "competition_logloss_wins": comp_wins,
        "primary_gates": primary_gates, "broad_vs_static_gates": broad_gates, "endpoint_vs_static_gates": endpoint_gates,
        "max_probability_residual": max_resid,
        "breakthrough_magnitude_gates": {"C_vs_B1_dLL_le_minus_0_003": deltas["C_vs_B1"]["log_loss"] <= -0.003, "C_vs_B0_dLL_le_minus_0_005": deltas["C_vs_B0"]["log_loss"] <= -0.005},
        "C073_C077_quarantined": True, "C070F_confirmation1597_opened": False, "protected_opened": False,
        "formal_weight": 0,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
