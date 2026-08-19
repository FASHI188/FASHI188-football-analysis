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

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SOURCE_REVISION = "9fe7fb127cd05316dbd438fe0e5be82c5c3ed536"
DEV_SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025")
SEALED_SEASON = "2025-2026"
EXPECTED_SHA256 = {
    "2020-2021": "e794f97ce8d95676a0cf14a78057aba8837973459eda2a1e04194402c4bfaa37",
    "2021-2022": "5411a5a311a2f2e379967b585a2e54646168ca434923e4ca5389cb614d27de78",
    "2022-2023": "916724fe4cad4af6d350805f4962c94456a14edc7afcc6184b01f3fcb77fc06d",
    "2023-2024": "2e4f761f484891bec3457b4c52a3d1ee20e5379fe5efa69e6564133edcbec1b7",
    "2024-2025": "a62ce23b14c112ae02be470bf8e29f3568f2a40a311b2b0034be6cd8c1b53cb3",
}
EXPECTED_ALL5 = {
    "2020-2021": 125,
    "2021-2022": 156,
    "2022-2023": 159,
    "2023-2024": 168,
    "2024-2025": 174,
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
K = 8
B0_NUM = ["L25_T1"]
B1_NUM = ["L25_T60", "L25_T30", "L25_T1"]
C_NUM = [f"L{int(line * 10):02d}_T{snap}" for line in PREFERRED for snap in SNAPSHOTS]
TEST_SEASONS = ("2021-2022", "2022-2023", "2023-2024", "2024-2025")
SUMMARY = Path("football-data/research/c072n13_aleague_dynamic_multiline_pt_summary.json")
PREDICTIONS = Path("football-data/research/c072n13_aleague_dynamic_multiline_pt_predictions.csv")

NON_TARGET_FIELDS = (
    "EVENT_DATE", "EVENT_ID", "MARKET_TYPE", "MARKET_ID", "MARKET_NAME",
    "SELECTION_ID", "RUNNER_NAME", "HOME_TEAM", "AWAY_TEAM",
    "BEST_BACK_PRICE_60_MIN_PRIOR", "BEST_LAY_PRICE_60_MIN_PRIOR",
    "BEST_BACK_PRICE_30_MIN_PRIOR", "BEST_LAY_PRICE_30_MIN_PRIOR",
    "BEST_BACK_PRICE_1_MIN_PRIOR", "BEST_LAY_PRICE_1_MIN_PRIOR",
)
FORBIDDEN_MODEL_FIELDS = {"IS_WINNER", "HOME_SCORE", "AWAY_SCORE", "RUNNER_STATUS"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_text(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())


def finite_gt_one(value: str) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def parse_nonneg_int(value: str) -> int | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x < 0 or not float(x).is_integer():
        return None
    return int(x)


def recognized_line(market_type: str, market_name: str) -> float | None:
    mt = str(market_type).strip().upper()
    by_type = MARKET_TYPES.get(mt)
    m = MARKET_NAME_RE.fullmatch(str(market_name).strip())
    by_name = float(m.group(1)) if m else None
    if by_type is not None and by_name is not None and by_type != by_name:
        return None
    line = by_type if by_type is not None else by_name
    return line if line in PREFERRED else None


def runner_side(name: str, line: float) -> str | None:
    m = RUNNER_RE.fullmatch(str(name).strip())
    if not m or float(m.group(2)) != line:
        return None
    return m.group(1).casefold()


def market_logit(over_back: float, over_lay: float, under_back: float, under_lay: float) -> float:
    over_mid = 0.5 * (1.0 / over_back + 1.0 / over_lay)
    under_mid = 0.5 * (1.0 / under_back + 1.0 / under_lay)
    q = over_mid / (over_mid + under_mid)
    q = min(max(q, 1e-6), 1.0 - 1e-6)
    return float(math.log(q / (1.0 - q)))


def zero_label_pass(source_dir: Path) -> tuple[dict[tuple[str, str], dict], dict]:
    """Reproduce the exact fail-closed all-five/all-three development event set without target access."""
    features: dict[tuple[str, str], dict] = {}
    source_meta = {}
    zero_label_counts = {}
    diagnostics = Counter()

    for season in DEV_SEASONS:
        path = source_dir / f"A-League_{season}_All_Markets.csv"
        if not path.exists():
            raise RuntimeError(f"missing development source file: {path}")
        digest = sha256_file(path)
        if digest != EXPECTED_SHA256[season]:
            raise RuntimeError(f"source SHA mismatch {season}: {digest}")

        event_identity: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        markets: dict[tuple[str, float, str], dict] = {}

        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            idx = {name.strip(): i for i, name in enumerate(header)}
            missing = [name for name in NON_TARGET_FIELDS if name not in idx]
            if missing:
                raise RuntimeError(f"missing non-target fields {season}: {missing}")
            if "TOTAL_GOALS" not in idx:
                raise RuntimeError(f"TOTAL_GOALS header absent {season}")
            non_target_idx = {name: idx[name] for name in NON_TARGET_FIELDS}
            rows = 0
            eligible_runner_rows = 0
            for row in reader:
                rows += 1
                if len(row) < len(header):
                    diagnostics["short_rows"] += 1
                    continue
                # Binding first pass: target/outcome field positions are never indexed here.
                event_date = row[non_target_idx["EVENT_DATE"]].strip()
                event_id = row[non_target_idx["EVENT_ID"]].strip()
                market_type = row[non_target_idx["MARKET_TYPE"]].strip()
                market_id = row[non_target_idx["MARKET_ID"]].strip()
                market_name = row[non_target_idx["MARKET_NAME"]].strip()
                selection_id = row[non_target_idx["SELECTION_ID"]].strip()
                runner_name = row[non_target_idx["RUNNER_NAME"]].strip()
                home_team = row[non_target_idx["HOME_TEAM"]].strip()
                away_team = row[non_target_idx["AWAY_TEAM"]].strip()
                line = recognized_line(market_type, market_name)
                if line is None:
                    continue
                side = runner_side(runner_name, line)
                if side not in {"over", "under"} or not event_id or not market_id or not selection_id:
                    diagnostics["bad_eligible_runner_identity"] += 1
                    continue
                eligible_runner_rows += 1
                event_identity[event_id].add((norm_text(event_date), norm_text(home_team), norm_text(away_team)))
                key = (event_id, line, market_id)
                market = markets.setdefault(key, {
                    "market_types": set(), "market_names": set(), "runners": defaultdict(list)
                })
                market["market_types"].add(market_type)
                market["market_names"].add(market_name)
                snapshots = {}
                for snap in SNAPSHOTS:
                    back = finite_gt_one(row[non_target_idx[f"BEST_BACK_PRICE_{snap}_MIN_PRIOR"]])
                    lay = finite_gt_one(row[non_target_idx[f"BEST_LAY_PRICE_{snap}_MIN_PRIOR"]])
                    crossed = back is not None and lay is not None and back > lay
                    if crossed:
                        diagnostics[f"crossed_runner_snapshot_T{snap}"] += 1
                    snapshots[snap] = {"back": back, "lay": lay, "crossed": crossed}
                market["runners"][side].append({"selection_id": selection_id, "snapshots": snapshots})

        identity_conflicts = {event_id for event_id, vals in event_identity.items() if len(vals) != 1}
        diagnostics["identity_conflicts"] += len(identity_conflicts)
        event_line_keys: dict[tuple[str, float], list[tuple[str, float, str]]] = defaultdict(list)
        normalized_market: dict[tuple[str, float, str], dict] = {}

        for key, market in markets.items():
            event_id, line, _market_id = key
            event_line_keys[(event_id, line)].append(key)
            if event_id in identity_conflicts:
                continue
            if len(market["market_types"]) != 1 or len(market["market_names"]) != 1:
                diagnostics["market_metadata_conflicts"] += 1
                continue
            if set(market["runners"].keys()) != {"over", "under"}:
                diagnostics["market_missing_side"] += 1
                continue
            sides = {}
            ok = True
            for side in ("over", "under"):
                observations = market["runners"][side]
                selection_ids = {obs["selection_id"] for obs in observations}
                if len(selection_ids) != 1:
                    diagnostics["multiple_selection_ids_per_side"] += 1
                    ok = False
                    break
                first = observations[0]
                for obs in observations[1:]:
                    if obs["snapshots"] != first["snapshots"]:
                        diagnostics["duplicate_runner_quote_conflict"] += 1
                        ok = False
                        break
                if not ok:
                    break
                sides[side] = first
            if ok:
                normalized_market[key] = sides

        event_line_features: dict[tuple[str, float], dict[int, float]] = {}
        for event_line, keys in event_line_keys.items():
            if len(keys) != 1:
                diagnostics["event_lines_with_multiple_market_ids"] += 1
                continue
            key = keys[0]
            sides = normalized_market.get(key)
            if sides is None:
                continue
            snap_logits = {}
            complete = True
            for snap in SNAPSHOTS:
                over = sides["over"]["snapshots"][snap]
                under = sides["under"]["snapshots"][snap]
                if (
                    over["back"] is None or over["lay"] is None or over["crossed"] or
                    under["back"] is None or under["lay"] is None or under["crossed"]
                ):
                    complete = False
                    break
                snap_logits[snap] = market_logit(over["back"], over["lay"], under["back"], under["lay"])
            if complete:
                event_line_features[event_line] = snap_logits

        eligible = []
        for event_id in sorted({eid for eid, _line in event_line_features}):
            if all((event_id, line) in event_line_features for line in PREFERRED):
                eligible.append(event_id)
                row = {"season": season, "event_id": event_id}
                for line in PREFERRED:
                    code = int(line * 10)
                    for snap in SNAPSHOTS:
                        row[f"L{code:02d}_T{snap}"] = event_line_features[(event_id, line)][snap]
                identity = next(iter(event_identity[event_id]))
                row["event_date_norm"], row["home_norm"], row["away_norm"] = identity
                features[(season, event_id)] = row

        zero_label_counts[season] = len(eligible)
        source_meta[season] = {
            "path": path.name,
            "sha256": digest,
            "bytes": path.stat().st_size,
            "rows_first_pass_non_target_only": rows,
            "eligible_ou_runner_rows": eligible_runner_rows,
            "all_five_all_three_events": len(eligible),
            "target_values_read_first_pass": 0,
        }

    if zero_label_counts != EXPECTED_ALL5:
        summary = {
            "schema": "C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_V1",
            "project_line": "football3",
            "classification": "DEVELOPMENT_EVIDENCE_NOT_CONFIRMATION",
            "terminal": "C072N13_ZERO_LABEL_ELIGIBILITY_REPRODUCTION_MISMATCH",
            "expected_all5_counts": EXPECTED_ALL5,
            "observed_all5_counts": zero_label_counts,
            "source_meta": source_meta,
            "diagnostics": dict(diagnostics),
            "development_target_values_materialized": 0,
            "target_values_2025_26_read": 0,
            "model_fit": 0,
            "C073_C077_quarantined": True,
            "C070F_confirmation1597_opened": False,
            "formal_weight": 0,
        }
        SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0)

    return features, {
        "source_revision": SOURCE_REVISION,
        "source_meta": source_meta,
        "zero_label_all5_counts": zero_label_counts,
        "zero_label_all5_total": sum(zero_label_counts.values()),
        "diagnostics": dict(diagnostics),
        "target_values_read_before_eligibility_freeze": 0,
    }


def decode_targets(source_dir: Path, eligible_keys: set[tuple[str, str]]) -> tuple[dict[tuple[str, str], int], dict]:
    target_by_key: dict[tuple[str, str], int] = {}
    conflicts = 0
    invalid = 0
    eligible_target_cells_read = 0
    noneligible_target_cells_read = 0
    per_season = {}

    by_season: dict[str, set[str]] = defaultdict(set)
    for season, event_id in eligible_keys:
        by_season[season].add(event_id)

    for season in DEV_SEASONS:
        path = source_dir / f"A-League_{season}_All_Markets.csv"
        values: dict[str, set[int]] = defaultdict(set)
        bad_event = set()
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            idx = {name.strip(): i for i, name in enumerate(header)}
            event_i = idx["EVENT_ID"]
            target_i = idx["TOTAL_GOALS"]
            for row in reader:
                if len(row) < len(header):
                    continue
                event_id = row[event_i].strip()
                if event_id not in by_season[season]:
                    # Binding: do not index/decode TOTAL_GOALS for non-eligible events.
                    continue
                eligible_target_cells_read += 1
                v = parse_nonneg_int(row[target_i])
                if v is None:
                    bad_event.add(event_id)
                else:
                    values[event_id].add(v)
        valid_targets = 0
        for event_id in sorted(by_season[season]):
            vals = values.get(event_id, set())
            if event_id in bad_event or len(vals) != 1:
                if len(vals) > 1:
                    conflicts += 1
                else:
                    invalid += 1
                continue
            target_by_key[(season, event_id)] = min(next(iter(vals)), 7)
            valid_targets += 1
        per_season[season] = {
            "eligible_events": len(by_season[season]),
            "valid_target_events": valid_targets,
            "excluded_target_conflict_or_invalid": len(by_season[season]) - valid_targets,
        }

    return target_by_key, {
        "eligible_target_cells_read_for_consistency": eligible_target_cells_read,
        "noneligible_target_cells_read": noneligible_target_cells_read,
        "target_conflict_events": conflicts,
        "target_invalid_or_missing_events": invalid,
        "valid_target_events": len(target_by_key),
        "per_season": per_season,
        "target_values_2025_26_read": 0,
    }


def build_model(num_cols: list[str]) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            C=0.1,
            max_iter=3000,
            class_weight=None,
            random_state=0,
            solver="lbfgs",
        )),
    ])


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
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.sum((cp - cy) ** 2, axis=1) / (K - 1)
    top1 = np.argmax(p, axis=1)
    top3 = np.argsort(p, axis=1)[:, -3:]
    return {
        "n": int(len(y)),
        "log_loss": float(ll.mean()),
        "brier": float(br.mean()),
        "rps": float(rps.mean()),
        "top1_accuracy": float(np.mean(top1 == y)),
        "top3_accuracy": float(np.mean([y[i] in top3[i] for i in range(len(y))])),
        "top1_total_2_fraction": float(np.mean(top1 == 2)),
        "mean_entropy": float(np.mean(-np.sum(p * np.log(np.clip(p, 1e-15, 1.0)), axis=1))),
        "mean_top1_margin": float(np.mean(np.sort(p, axis=1)[:, -1] - np.sort(p, axis=1)[:, -2])),
        "max_probability_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))) if len(p) else 0.0,
    }


def delta(cand: dict, ref: dict) -> dict:
    keys = [
        "log_loss", "brier", "rps", "top1_accuracy", "top3_accuracy",
        "top1_total_2_fraction", "mean_entropy", "mean_top1_margin",
    ]
    return {k: float(cand[k] - ref[k]) for k in keys}


def bootstrap(y: np.ndarray, pref: np.ndarray, pcand: np.ndarray, seed: int, reps: int = 3000) -> dict:
    y = np.asarray(y, dtype=int)
    ix = np.arange(len(y))
    d = -np.log(np.clip(pcand[ix, y], 1e-15, 1.0)) + np.log(np.clip(pref[ix, y], 1e-15, 1.0))
    rng = np.random.default_rng(seed)
    sims = np.empty(reps, dtype=float)
    n = len(d)
    for i in range(reps):
        draw = rng.integers(0, n, n)
        sims[i] = float(d[draw].mean())
    return {
        "n": int(n),
        "reps": reps,
        "seed": seed,
        "mean_delta_log_loss": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "p_delta_lt_0": float(np.mean(sims < 0.0)),
    }


def comparison_pass(delta_metrics: dict, boot: dict, fold_wins: int, max_resid: float, boundary_ok: bool) -> tuple[bool, dict]:
    gates = {
        "four_folds_executed": True,
        "pooled_dlogloss_lt_0": delta_metrics["log_loss"] < 0,
        "bootstrap90_upper_dlogloss_lt_0": boot["ci90_high"] < 0,
        "fold_logloss_wins_ge_3_of_4": fold_wins >= 3,
        "pooled_dbrier_le_0": delta_metrics["brier"] <= 0,
        "pooled_drps_le_0": delta_metrics["rps"] <= 0,
        "pooled_top1_delta_ge_0": delta_metrics["top1_accuracy"] >= 0,
        "pooled_top3_delta_ge_0": delta_metrics["top3_accuracy"] >= 0,
        "probability_residual_le_1e_12": max_resid <= 1e-12,
        "2025_26_target_values_read_zero": boundary_ok,
        "C070F_protected_sealed": boundary_ok,
    }
    return all(gates.values()), gates


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    args = ap.parse_args()
    source_dir = Path(args.source_dir)

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    features, zero_meta = zero_label_pass(source_dir)
    eligible_keys = set(features.keys())
    targets, target_meta = decode_targets(source_dir, eligible_keys)

    rows = []
    for key in sorted(eligible_keys):
        if key not in targets:
            continue
        r = dict(features[key])
        r["target"] = targets[key]
        rows.append(r)
    data = pd.DataFrame(rows)
    if data.empty:
        raise RuntimeError("no valid development target rows")

    target_class_counts = {
        season: {str(k): int(((data["season"] == season) & (data["target"] == k)).sum()) for k in range(K)}
        for season in DEV_SEASONS
    }

    pred_parts = []
    folds = []
    fold_wins = {"C_vs_B1": 0, "C_vs_B0": 0, "B1_vs_B0": 0}
    all_classes_each_train = True
    max_resid = 0.0

    season_order = {s: i for i, s in enumerate(DEV_SEASONS)}
    for test_season in TEST_SEASONS:
        ti = season_order[test_season]
        train = data[data["season"].map(season_order) < ti].copy()
        test = data[data["season"] == test_season].copy()
        classes = sorted(train["target"].astype(int).unique().tolist())
        if classes != list(range(K)):
            all_classes_each_train = False
            break
        if train.empty or test.empty:
            raise RuntimeError(f"empty fold {test_season}")

        models = {
            "B0": (build_model(B0_NUM), B0_NUM),
            "B1": (build_model(B1_NUM), B1_NUM),
            "C": (build_model(C_NUM), C_NUM),
        }
        probs = {}
        for name, (model, cols) in models.items():
            model.fit(train[cols], train["target"].astype(int))
            probs[name] = aligned_predict(model, test[cols])

        y = test["target"].to_numpy(dtype=int)
        met = {name: metrics(y, p) for name, p in probs.items()}
        d_c_b1 = delta(met["C"], met["B1"])
        d_c_b0 = delta(met["C"], met["B0"])
        d_b1_b0 = delta(met["B1"], met["B0"])
        if d_c_b1["log_loss"] < 0: fold_wins["C_vs_B1"] += 1
        if d_c_b0["log_loss"] < 0: fold_wins["C_vs_B0"] += 1
        if d_b1_b0["log_loss"] < 0: fold_wins["B1_vs_B0"] += 1
        max_resid = max(max_resid, *(met[name]["max_probability_residual"] for name in met))
        folds.append({
            "test_season": test_season,
            "train_seasons": DEV_SEASONS[:ti],
            "train_n": int(len(train)),
            "test_n": int(len(test)),
            "train_classes": classes,
            "metrics": met,
            "delta_C_minus_B1": d_c_b1,
            "delta_C_minus_B0": d_c_b0,
            "delta_B1_minus_B0": d_b1_b0,
        })

        pp = test[["season", "event_id", "target"]].copy()
        pp["fold"] = test_season
        for name, p in probs.items():
            for k in range(K):
                pp[f"{name}_p{k}"] = p[:, k]
        pred_parts.append(pp)

    if not all_classes_each_train:
        summary = {
            "schema": "C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_V1",
            "project_line": "football3",
            "classification": "DEVELOPMENT_EVIDENCE_NOT_CONFIRMATION",
            "terminal": "C072N13_ALEAGUE_DATA_INSUFFICIENT_ALL_CLASSES",
            "zero_label_meta": zero_meta,
            "target_meta": target_meta,
            "target_class_counts": target_class_counts,
            "all_classes_each_train": False,
            "development_target_events_materialized": len(targets),
            "target_values_2025_26_read": 0,
            "model_fit": 0,
            "C073_C077_quarantined": True,
            "C070F_confirmation1597_opened": False,
            "protected_opened": False,
            "formal_weight": 0,
        }
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    pred = pd.concat(pred_parts, ignore_index=True)
    y = pred["target"].to_numpy(dtype=int)
    probs = {name: pred[[f"{name}_p{k}" for k in range(K)]].to_numpy(dtype=float) for name in ("B0", "B1", "C")}
    pooled = {name: metrics(y, p) for name, p in probs.items()}
    deltas = {
        "C_vs_B1": delta(pooled["C"], pooled["B1"]),
        "C_vs_B0": delta(pooled["C"], pooled["B0"]),
        "B1_vs_B0": delta(pooled["B1"], pooled["B0"]),
    }
    boots = {
        "C_vs_B1": bootstrap(y, probs["B1"], probs["C"], 72015),
        "C_vs_B0": bootstrap(y, probs["B0"], probs["C"], 72016),
        "B1_vs_B0": bootstrap(y, probs["B0"], probs["B1"], 72017),
    }

    boundary_ok = (
        target_meta["noneligible_target_cells_read"] == 0 and
        target_meta["target_values_2025_26_read"] == 0
    )
    primary_pass, primary_gates = comparison_pass(deltas["C_vs_B1"], boots["C_vs_B1"], fold_wins["C_vs_B1"], max_resid, boundary_ok)
    broad_pass, broad_gates = comparison_pass(deltas["C_vs_B0"], boots["C_vs_B0"], fold_wins["C_vs_B0"], max_resid, boundary_ok)
    endpoint_pass, endpoint_gates = comparison_pass(deltas["B1_vs_B0"], boots["B1_vs_B0"], fold_wins["B1_vs_B0"], max_resid, boundary_ok)

    breakthrough = (
        primary_pass and deltas["C_vs_B1"]["log_loss"] <= -0.003 and
        broad_pass and deltas["C_vs_B0"]["log_loss"] <= -0.008
    )
    if breakthrough:
        terminal = "C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_BREAKTHROUGH_SCREEN_PASS"
    elif primary_pass:
        terminal = "C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_DEVELOPMENT_PASS"
    elif endpoint_pass:
        terminal = "C072N13_ALEAGUE_SINGLELINE_DYNAMIC_PASS_MULTILINE_NOT_ESTABLISHED"
    else:
        terminal = "C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_PARK"

    PREDICTIONS.parent.mkdir(parents=True, exist_ok=True)
    pred.to_csv(PREDICTIONS, index=False)
    summary = {
        "schema": "C072N13_ALEAGUE_DYNAMIC_MULTILINE_PT_V1",
        "project_line": "football3",
        "classification": "DEVELOPMENT_EVIDENCE_NOT_CONFIRMATION",
        "terminal": terminal,
        "breakthrough_screen_pass": breakthrough,
        "primary_dynamic_multiline_vs_dynamic_ou25_pass": primary_pass,
        "broad_dynamic_multiline_vs_static_ou25_pass": broad_pass,
        "singleline_dynamic_vs_static_pass": endpoint_pass,
        "source_revision": SOURCE_REVISION,
        "zero_label_meta": zero_meta,
        "eligible_development_events": len(eligible_keys),
        "development_target_events_materialized": len(targets),
        "paired_model_rows_all_development_seasons": int(len(data)),
        "development_oos_n": int(len(pred)),
        "target_meta": target_meta,
        "target_class_counts": target_class_counts,
        "folds": folds,
        "fold_logloss_wins": fold_wins,
        "pooled": pooled,
        "deltas": deltas,
        "bootstraps": boots,
        "primary_gates": primary_gates,
        "broad_vs_static_gates": broad_gates,
        "singleline_dynamic_vs_static_gates": endpoint_gates,
        "breakthrough_magnitude_gates": {
            "C_vs_B1_dLL_le_minus_0_003": deltas["C_vs_B1"]["log_loss"] <= -0.003,
            "C_vs_B0_dLL_le_minus_0_008": deltas["C_vs_B0"]["log_loss"] <= -0.008,
        },
        "max_probability_residual": float(max_resid),
        "target_values_2025_26_read": 0,
        "reserve_2025_26_downloaded": False,
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
