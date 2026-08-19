#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

SOURCE_REVISION = "9fe7fb127cd05316dbd438fe0e5be82c5c3ed536"
SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025", "2025-2026")
DEV_SEASONS = SEASONS[:-1]
RESERVE_SEASON = "2025-2026"
EXPECTED_SHA256 = {
    "2020-2021": "e794f97ce8d95676a0cf14a78057aba8837973459eda2a1e04194402c4bfaa37",
    "2021-2022": "5411a5a311a2f2e379967b585a2e54646168ca434923e4ca5389cb614d27de78",
    "2022-2023": "916724fe4cad4af6d350805f4962c94456a14edc7afcc6184b01f3fcb77fc06d",
    "2023-2024": "2e4f761f484891bec3457b4c52a3d1ee20e5379fe5efa69e6564133edcbec1b7",
    "2024-2025": "a62ce23b14c112ae02be470bf8e29f3568f2a40a311b2b0034be6cd8c1b53cb3",
    "2025-2026": "f0980a3a37b79a5be947e4a7e3288c9f88e3cf03810b4b23cb8f8871064fc5ab",
}
EXPECTED_ALL5 = {
    "2020-2021": 125,
    "2021-2022": 156,
    "2022-2023": 159,
    "2023-2024": 168,
    "2024-2025": 174,
    "2025-2026": 162,
}
PREFERRED = (0.5, 1.5, 2.5, 3.5, 4.5)
SNAPSHOTS = (60, 30, 1)
MARKET_TYPES = {
    "OVER_UNDER_05": 0.5,
    "OVER_UNDER_15": 1.5,
    "OVER_UNDER_25": 2.5,
    "OVER_UNDER_35": 3.5,
    "OVER_UNDER_45": 4.5,
}
MARKET_NAME_RE = re.compile(r"^Over/Under ([0-4]\.5) Goals$", re.IGNORECASE)
RUNNER_RE = re.compile(r"^(Over|Under)\s+([0-4]\.5)(?:\s+Goals)?$", re.IGNORECASE)
EPS_LOGIT = 1e-6
EPS_CLASS = 1e-9
N_CLASSES = 8
BOOT_REPS = 5000
BOOT_SEED = 72012

MARKET_ALLOWED = (
    "EVENT_DATE", "EVENT_ID", "MARKET_TYPE", "MARKET_ID", "MARKET_NAME",
    "SELECTION_ID", "RUNNER_NAME", "HOME_TEAM", "AWAY_TEAM",
    "BEST_BACK_PRICE_60_MIN_PRIOR", "BEST_LAY_PRICE_60_MIN_PRIOR",
    "BEST_BACK_PRICE_30_MIN_PRIOR", "BEST_LAY_PRICE_30_MIN_PRIOR",
    "BEST_BACK_PRICE_1_MIN_PRIOR", "BEST_LAY_PRICE_1_MIN_PRIOR",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def finite_gt_one(value: str) -> float | None:
    try:
        x = float(value.strip())
    except (ValueError, AttributeError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def recognized_line(market_type: str, market_name: str) -> float | None:
    by_type = MARKET_TYPES.get(market_type.strip().upper())
    m = MARKET_NAME_RE.fullmatch(market_name.strip())
    by_name = float(m.group(1)) if m else None
    if by_type is not None and by_name is not None and by_type != by_name:
        return None
    line = by_type if by_type is not None else by_name
    return line if line in PREFERRED else None


def runner_side(name: str, line: float) -> str | None:
    m = RUNNER_RE.fullmatch(name.strip())
    if not m or float(m.group(2)) != line:
        return None
    side = m.group(1).casefold()
    return side if side in {"over", "under"} else None


def quote_logit(over: tuple[float, float], under: tuple[float, float]) -> float:
    ob, ol = over
    ub, ul = under
    a_over = 0.5 * (1.0 / ob + 1.0 / ol)
    a_under = 0.5 * (1.0 / ub + 1.0 / ul)
    q = a_over / (a_over + a_under)
    q = min(1.0 - EPS_LOGIT, max(EPS_LOGIT, q))
    return math.log(q / (1.0 - q))


def load_zero_label_features(root: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    file_meta = []
    stats = Counter()
    event_identity: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    markets: dict[tuple[str, str, float, str], dict[str, Any]] = {}

    for season in SEASONS:
        path = root / f"A-League_{season}_All_Markets.csv"
        if not path.exists():
            raise RuntimeError(f"missing source file {path}")
        digest = sha256_file(path)
        if digest != EXPECTED_SHA256[season]:
            raise RuntimeError(f"source hash mismatch {season}: {digest}")
        file_meta.append({"season": season, "bytes": path.stat().st_size, "sha256": digest})

        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            index = {name.strip(): i for i, name in enumerate(header)}
            missing = [name for name in MARKET_ALLOWED if name not in index]
            if missing:
                raise RuntimeError(f"missing market fields {season}: {missing}")
            idx = {name: index[name] for name in MARKET_ALLOWED}

            for row in reader:
                stats["market_rows_seen"] += 1
                if len(row) < len(header):
                    stats["short_rows"] += 1
                    continue
                event_date = row[idx["EVENT_DATE"]].strip()
                event_id = row[idx["EVENT_ID"]].strip()
                market_type = row[idx["MARKET_TYPE"]].strip()
                market_id = row[idx["MARKET_ID"]].strip()
                market_name = row[idx["MARKET_NAME"]].strip()
                selection_id = row[idx["SELECTION_ID"]].strip()
                runner_name = row[idx["RUNNER_NAME"]].strip()
                home_team = row[idx["HOME_TEAM"]].strip()
                away_team = row[idx["AWAY_TEAM"]].strip()

                line = recognized_line(market_type, market_name)
                if line is None:
                    continue
                side = runner_side(runner_name, line)
                if side is None or not event_id or not market_id or not selection_id:
                    continue
                stats["eligible_runner_rows"] += 1
                event_identity[(season, event_id)].add((norm_text(event_date), norm_text(home_team), norm_text(away_team)))

                key = (season, event_id, line, market_id)
                market = markets.setdefault(key, {
                    "event_date": event_date,
                    "market_types": set(),
                    "market_names": set(),
                    "runners": defaultdict(list),
                })
                market["market_types"].add(market_type)
                market["market_names"].add(market_name)
                snapshots = {}
                for minute in SNAPSHOTS:
                    back = finite_gt_one(row[idx[f"BEST_BACK_PRICE_{minute}_MIN_PRIOR"]])
                    lay = finite_gt_one(row[idx[f"BEST_LAY_PRICE_{minute}_MIN_PRIOR"]])
                    snapshots[minute] = (back, lay)
                market["runners"][side].append((selection_id, runner_name, snapshots))

    identity_conflicts = {key for key, vals in event_identity.items() if len(vals) != 1}
    stats["identity_conflicts"] = len(identity_conflicts)

    event_line_keys: dict[tuple[str, str, float], list[tuple[str, str, float, str]]] = defaultdict(list)
    valid_market_quotes: dict[tuple[str, str, float, str], dict[int, dict[str, tuple[float, float]]]] = {}

    for key, market in markets.items():
        season, event_id, line, _mid = key
        event_line_keys[(season, event_id, line)].append(key)
        if (season, event_id) in identity_conflicts:
            continue
        if len(market["market_types"]) != 1 or len(market["market_names"]) != 1:
            stats["market_metadata_conflict"] += 1
            continue
        if set(market["runners"]) != {"over", "under"}:
            stats["market_side_missing"] += 1
            continue

        normalized: dict[str, tuple[str, str, dict[int, tuple[float | None, float | None]]]] = {}
        ok = True
        for side in ("over", "under"):
            obs = market["runners"][side]
            selection_ids = {x[0] for x in obs}
            if len(selection_ids) != 1:
                ok = False
                stats["multiple_selection_ids_per_side"] += 1
                break
            first = obs[0]
            if any(x[2] != first[2] for x in obs[1:]):
                ok = False
                stats["duplicate_runner_quote_conflict"] += 1
                break
            normalized[side] = first
        if not ok:
            continue

        q: dict[int, dict[str, tuple[float, float]]] = {}
        for minute in SNAPSHOTS:
            q[minute] = {}
            for side in ("over", "under"):
                back, lay = normalized[side][2][minute]
                if back is None or lay is None or back > lay:
                    ok = False
                    break
                q[minute][side] = (back, lay)
            if not ok:
                break
        if ok:
            valid_market_quotes[key] = q

    duplicate_event_lines = {k for k, v in event_line_keys.items() if len(v) != 1}
    stats["duplicate_event_lines"] = len(duplicate_event_lines)

    line_quotes: dict[tuple[str, str, float], dict[int, dict[str, tuple[float, float]]]] = {}
    for event_line, keys in event_line_keys.items():
        if event_line in duplicate_event_lines:
            continue
        key = keys[0]
        if key in valid_market_quotes:
            line_quotes[event_line] = valid_market_quotes[key]

    feature_rows: dict[tuple[str, str], dict[str, Any]] = {}
    per_season_count = Counter()
    for season in SEASONS:
        event_ids = sorted({event_id for s, event_id, _line in line_quotes if s == season})
        for event_id in event_ids:
            if not all((season, event_id, line) in line_quotes for line in PREFERRED):
                continue
            z: dict[tuple[float, int], float] = {}
            for line in PREFERRED:
                snapshots = line_quotes[(season, event_id, line)]
                for minute in SNAPSHOTS:
                    z[(line, minute)] = quote_logit(snapshots[minute]["over"], snapshots[minute]["under"])
            identity = next(iter(event_identity[(season, event_id)]))
            event_date = identity[0]
            baseline = np.array([z[(2.5, minute)] for minute in SNAPSHOTS], dtype=float)
            candidate = np.array([z[(line, minute)] for line in PREFERRED for minute in SNAPSHOTS], dtype=float)
            feature_rows[(season, event_id)] = {
                "season": season,
                "event_id": event_id,
                "event_date": event_date,
                "baseline": baseline,
                "candidate": candidate,
            }
            per_season_count[season] += 1

    observed_counts = {s: per_season_count[s] for s in SEASONS}
    if observed_counts != EXPECTED_ALL5:
        raise RuntimeError(f"N11 all-five eligibility mismatch: {observed_counts} != {EXPECTED_ALL5}")

    meta = {
        "source_files": file_meta,
        "stats": dict(stats),
        "eligible_all5_per_season": observed_counts,
        "eligible_all5_total": len(feature_rows),
    }
    return feature_rows, meta


def read_development_targets(root: Path, authorized: set[tuple[str, str]]) -> tuple[dict[tuple[str, str], int], dict[str, Any]]:
    targets: dict[tuple[str, str], int] = {}
    target_values_read = 0
    invalid = 0
    for season in DEV_SEASONS:
        path = root / f"A-League_{season}_All_Markets.csv"
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            index = {name.strip(): i for i, name in enumerate(header)}
            event_idx = index["EVENT_ID"]
            target_idx = index["TOTAL_GOALS"]
            for row in reader:
                if len(row) < len(header):
                    continue
                event_id = row[event_idx].strip()
                key = (season, event_id)
                if key not in authorized or key in targets:
                    continue
                raw = row[target_idx].strip()
                target_values_read += 1
                try:
                    x = float(raw)
                    xi = int(x)
                    if not math.isfinite(x) or x < 0 or abs(x - xi) > 1e-9:
                        raise ValueError(raw)
                except (ValueError, OverflowError):
                    invalid += 1
                    continue
                targets[key] = min(xi, 7)
    missing = sorted(authorized - set(targets))
    return targets, {
        "authorized_development_events": len(authorized),
        "development_target_values_read": target_values_read,
        "valid_development_targets": len(targets),
        "invalid_development_targets": invalid,
        "missing_authorized_targets": len(missing),
        "target_values_2025_2026_read": 0,
        "forbidden_non_total_goals_outcome_values_read": 0,
    }


def fit_predict(x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray) -> np.ndarray:
    scaler = StandardScaler()
    xt = scaler.fit_transform(x_train)
    xv = scaler.transform(x_test)
    model = LogisticRegression(
        C=0.1,
        penalty="l2",
        solver="lbfgs",
        max_iter=3000,
        class_weight=None,
        random_state=0,
    )
    model.fit(xt, y_train)
    p_seen = model.predict_proba(xv)
    p = np.full((len(x_test), N_CLASSES), EPS_CLASS, dtype=float)
    for j, cls in enumerate(model.classes_):
        p[:, int(cls)] = p_seen[:, j]
    p = np.maximum(p, EPS_CLASS)
    p /= p.sum(axis=1, keepdims=True)
    return p


def score_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    n = len(y)
    idx = np.arange(n)
    ll = float(np.mean(-np.log(np.clip(p[idx, y], 1e-15, 1.0))))
    one = np.zeros_like(p)
    one[idx, y] = 1.0
    brier = float(np.mean(np.sum((p - one) ** 2, axis=1)))
    cp = np.cumsum(p, axis=1)[:, :-1]
    co = np.cumsum(one, axis=1)[:, :-1]
    rps = float(np.mean(np.sum((cp - co) ** 2, axis=1) / (N_CLASSES - 1)))
    top1 = np.argmax(p, axis=1)
    top1_acc = float(np.mean(top1 == y))
    top3_idx = np.argpartition(p, -3, axis=1)[:, -3:]
    top3_acc = float(np.mean(np.any(top3_idx == y[:, None], axis=1)))
    entropy = float(np.mean(-np.sum(p * np.log(np.clip(p, 1e-15, 1.0)), axis=1)))
    sorted_p = np.sort(p, axis=1)
    margin = float(np.mean(sorted_p[:, -1] - sorted_p[:, -2]))
    top1_t2_fraction = float(np.mean(top1 == 2))
    residual = float(np.max(np.abs(p.sum(axis=1) - 1.0))) if n else 0.0
    return {
        "log_loss": ll,
        "brier": brier,
        "rps": rps,
        "top1_accuracy": top1_acc,
        "top3_accuracy": top3_acc,
        "mean_entropy": entropy,
        "mean_top1_margin": margin,
        "top1_t2_fraction": top1_t2_fraction,
        "probability_residual_max": residual,
    }


def class_diagnostics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    out = {}
    for cls in range(N_CLASSES):
        mask = y == cls
        out[str(cls)] = {
            "n": int(mask.sum()),
            "true_class_log_loss": float(np.mean(-np.log(np.clip(p[mask, cls], 1e-15, 1.0)))) if mask.any() else None,
            "top1_recall": float(np.mean(np.argmax(p[mask], axis=1) == cls)) if mask.any() else None,
        }
    return out


def delta_metrics(base: dict[str, float], cand: dict[str, float]) -> dict[str, float]:
    return {k: float(cand[k] - base[k]) for k in ("log_loss", "brier", "rps", "top1_accuracy", "top3_accuracy", "mean_entropy", "mean_top1_margin", "top1_t2_fraction")}


def cluster_bootstrap_delta(y: np.ndarray, pb: np.ndarray, pc: np.ndarray, dates: list[str]) -> dict[str, Any]:
    per_row = -np.log(np.clip(pc[np.arange(len(y)), y], 1e-15, 1.0)) + np.log(np.clip(pb[np.arange(len(y)), y], 1e-15, 1.0))
    by_date: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(dates):
        by_date[d].append(i)
    keys = sorted(by_date)
    sums = np.array([float(np.sum(per_row[by_date[k]])) for k in keys], dtype=float)
    counts = np.array([len(by_date[k]) for k in keys], dtype=float)
    rng = np.random.default_rng(BOOT_SEED)
    vals = np.empty(BOOT_REPS, dtype=float)
    m = len(keys)
    for r in range(BOOT_REPS):
        sel = rng.integers(0, m, size=m)
        vals[r] = float(np.sum(sums[sel]) / np.sum(counts[sel]))
    return {
        "method": "paired_EVENT_DATE_cluster_bootstrap",
        "reps": BOOT_REPS,
        "seed": BOOT_SEED,
        "clusters": m,
        "mean_delta_log_loss": float(np.mean(vals)),
        "ci90_low": float(np.quantile(vals, 0.05)),
        "ci90_high": float(np.quantile(vals, 0.95)),
        "p_delta_lt_0": float(np.mean(vals < 0.0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--out-summary", required=True)
    ap.add_argument("--out-predictions", required=True)
    args = ap.parse_args()
    root = Path(args.source_dir)

    feature_rows, source_meta = load_zero_label_features(root)
    authorized_dev = {key for key in feature_rows if key[0] in DEV_SEASONS}
    targets, target_meta = read_development_targets(root, authorized_dev)
    if target_meta["missing_authorized_targets"] != 0 or target_meta["invalid_development_targets"] != 0:
        raise RuntimeError(f"development target completeness failure: {target_meta}")

    folds = [
        (("2020-2021",), "2021-2022"),
        (("2020-2021", "2021-2022"), "2022-2023"),
        (("2020-2021", "2021-2022", "2022-2023"), "2023-2024"),
        (("2020-2021", "2021-2022", "2022-2023", "2023-2024"), "2024-2025"),
    ]

    pooled_y: list[int] = []
    pooled_pb: list[np.ndarray] = []
    pooled_pc: list[np.ndarray] = []
    pooled_dates: list[str] = []
    prediction_rows: list[dict[str, Any]] = []
    fold_results = []

    for train_seasons, test_season in folds:
        train_keys = sorted(k for k in authorized_dev if k[0] in train_seasons)
        test_keys = sorted(k for k in authorized_dev if k[0] == test_season)
        xb_train = np.stack([feature_rows[k]["baseline"] for k in train_keys])
        xc_train = np.stack([feature_rows[k]["candidate"] for k in train_keys])
        y_train = np.array([targets[k] for k in train_keys], dtype=int)
        xb_test = np.stack([feature_rows[k]["baseline"] for k in test_keys])
        xc_test = np.stack([feature_rows[k]["candidate"] for k in test_keys])
        y_test = np.array([targets[k] for k in test_keys], dtype=int)

        pb = fit_predict(xb_train, y_train, xb_test)
        pc = fit_predict(xc_train, y_train, xc_test)
        mb = score_metrics(y_test, pb)
        mc = score_metrics(y_test, pc)
        dm = delta_metrics(mb, mc)
        fold_results.append({
            "train_seasons": list(train_seasons),
            "test_season": test_season,
            "train_n": len(train_keys),
            "test_n": len(test_keys),
            "train_class_counts": {str(k): int(v) for k, v in sorted(Counter(y_train.tolist()).items())},
            "baseline": mb,
            "candidate": mc,
            "delta_candidate_minus_baseline": dm,
        })

        pooled_y.extend(y_test.tolist())
        pooled_pb.extend(pb)
        pooled_pc.extend(pc)
        for i, key in enumerate(test_keys):
            d = feature_rows[key]["event_date"]
            pooled_dates.append(d)
            row = {
                "season": key[0], "event_id": key[1], "event_date": d, "target_T": int(y_test[i]),
                "baseline_top1": int(np.argmax(pb[i])), "candidate_top1": int(np.argmax(pc[i])),
            }
            for cls in range(N_CLASSES):
                row[f"baseline_p{cls}"] = float(pb[i, cls])
                row[f"candidate_p{cls}"] = float(pc[i, cls])
            prediction_rows.append(row)

    y = np.array(pooled_y, dtype=int)
    pb = np.stack(pooled_pb)
    pc = np.stack(pooled_pc)
    mb = score_metrics(y, pb)
    mc = score_metrics(y, pc)
    dm = delta_metrics(mb, mc)
    boot = cluster_bootstrap_delta(y, pb, pc, pooled_dates)
    fold_wins = sum(f["delta_candidate_minus_baseline"]["log_loss"] < 0 for f in fold_results)
    max_resid = max(mb["probability_residual_max"], mc["probability_residual_max"])

    gates = {
        "pooled_dlogloss_lt_0": dm["log_loss"] < 0.0,
        "bootstrap90_upper_lt_0": boot["ci90_high"] < 0.0,
        "fold_logloss_wins_ge_3_of_4": fold_wins >= 3,
        "brier_nonworse": dm["brier"] <= 0.0,
        "rps_nonworse": dm["rps"] <= 0.0,
        "probability_residual_le_1e12": max_resid <= 1e-12,
        "reserve_2025_26_target_values_read_zero": target_meta["target_values_2025_2026_read"] == 0,
        "c073_c077_scientific_results_unused": True,
        "c070f_confirmation_unopened": True,
    }
    development_pass = all(gates.values())
    practical_breakthrough = development_pass and dm["log_loss"] <= -0.003
    if practical_breakthrough:
        terminal = "C072N12_BREAKTHROUGH_CANDIDATE"
    elif development_pass:
        terminal = "C072N12_STABLE_SMALL_INCREMENT"
    else:
        terminal = "C072N12_DYNAMIC_SURFACE_PT_DEVELOPMENT_PARK"

    summary = {
        "schema_version": "C072N12_ALEAGUE_DYNAMIC_SURFACE_PT_DEVELOPMENT_V1",
        "project": "football3",
        "source_revision": SOURCE_REVISION,
        "classification": "NEW_DOMAIN_DEVELOPMENT",
        "target": "T=min(int(TOTAL_GOALS),7); classes 0..6,7+",
        "baseline_features": [f"z_2.5_T{m}" for m in SNAPSHOTS],
        "candidate_features": [f"z_{line}_T{m}" for line in PREFERRED for m in SNAPSHOTS],
        "estimator": {"scaler": "StandardScaler", "model": "LogisticRegression", "C": 0.1, "penalty": "l2", "solver": "lbfgs", "max_iter": 3000, "class_weight": None, "random_state": 0},
        "source_meta": source_meta,
        "target_access": target_meta,
        "development_oos_n": len(y),
        "folds": fold_results,
        "fold_logloss_wins": fold_wins,
        "pooled_baseline": mb,
        "pooled_candidate": mc,
        "pooled_delta_candidate_minus_baseline": dm,
        "paired_date_cluster_bootstrap_dlogloss": boot,
        "baseline_class_diagnostics": class_diagnostics(y, pb),
        "candidate_class_diagnostics": class_diagnostics(y, pc),
        "gates": gates,
        "development_pass": development_pass,
        "practical_breakthrough_threshold_dlogloss": -0.003,
        "practical_breakthrough_candidate": practical_breakthrough,
        "target_values_2025_26_read": 0,
        "c073_c077_scientific_results_used": False,
        "c070f_confirmation_opened": False,
        "max_probability_conservation_residual": max_resid,
        "terminal": terminal,
        "authorization": "N13_CONFIRMATION_ONLY_IF_BREAKTHROUGH_CANDIDATE; otherwise PARK and keep 2025-26 target unread",
    }

    out_summary = Path(args.out_summary)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out_predictions = Path(args.out_predictions)
    out_predictions.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(prediction_rows[0].keys()) if prediction_rows else []
    with out_predictions.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(prediction_rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
