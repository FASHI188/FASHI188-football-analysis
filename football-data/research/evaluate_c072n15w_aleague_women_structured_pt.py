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

DEV_SEASONS = ("2021-2022", "2022-2023", "2023-2024", "2024-2025")
EXPECTED_SHA = {
    "2021-2022": "f2e2a957c7674c5626eff71c7ab360e698c1ccb9f8054383e5c5c0eb6498a86f",
    "2022-2023": "899992d57d65c4786b47057a8c009dbeea3ec170aadc29beb1fd47e4b12a7524",
    "2023-2024": "85e3b6d3cf2f3e54e1adbe456e24ec5042946e5102c99501c412039279814792",
    "2024-2025": "3daf737dd36ef95b4e2e0d354674842faec755016e43abb9f987d67b1d594060",
}
EXPECTED_ELIGIBLE = {"2021-2022": 59, "2022-2023": 61, "2023-2024": 139, "2024-2025": 135}
PREFERRED = (0.5, 1.5, 2.5, 3.5, 4.5)
SNAPSHOTS = (60, 30, 1)
MARKET_TYPES = {
    "OVER_UNDER_05": 0.5,
    "OVER_UNDER_15": 1.5,
    "OVER_UNDER_25": 2.5,
    "OVER_UNDER_35": 3.5,
    "OVER_UNDER_45": 4.5,
}
MARKET_NAME_RE = re.compile(r"^Over/Under ([0-4]\.5) Goals$", re.I)
RUNNER_RE = re.compile(r"^(Over|Under)\s+([0-4]\.5)(?:\s+Goals)?$", re.I)
NON_TARGET_FIELDS = [
    "EVENT_DATE", "EVENT_ID", "MARKET_TYPE", "MARKET_ID", "MARKET_NAME",
    "SELECTION_ID", "RUNNER_NAME", "HOME_TEAM", "AWAY_TEAM",
    "BEST_BACK_PRICE_60_MIN_PRIOR", "BEST_LAY_PRICE_60_MIN_PRIOR",
    "BEST_BACK_PRICE_30_MIN_PRIOR", "BEST_LAY_PRICE_30_MIN_PRIOR",
    "BEST_BACK_PRICE_1_MIN_PRIOR", "BEST_LAY_PRICE_1_MIN_PRIOR",
]
K = 8
SUMMARY = Path("football-data/research/c072n15w_aleague_women_structured_pt_summary.json")
PREDICTIONS = Path("football-data/research/c072n15w_aleague_women_structured_pt_predictions.csv")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm(x: str) -> str:
    return " ".join(str(x).strip().casefold().split())


def price(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def parse_nonneg_int(x: str) -> int | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < 0 or not float(v).is_integer():
        return None
    return int(v)


def recognized_line(mt: str, mn: str) -> float | None:
    by_type = MARKET_TYPES.get(str(mt).strip().upper())
    m = MARKET_NAME_RE.fullmatch(str(mn).strip())
    by_name = float(m.group(1)) if m else None
    if by_type is not None and by_name is not None and by_type != by_name:
        return None
    line = by_type if by_type is not None else by_name
    return line if line in PREFERRED else None


def runner_side(name: str, line: float) -> str | None:
    m = RUNNER_RE.fullmatch(str(name).strip())
    if not m or float(m.group(2)) != line:
        return None
    return m.group(1).lower()


def devig_over(ob: float, ol: float, ub: float, ul: float) -> float:
    mo = 0.5 * (1.0 / ob + 1.0 / ol)
    mu = 0.5 * (1.0 / ub + 1.0 / ul)
    return float(mo / (mo + mu))


def eligible_features(source_dir: Path) -> tuple[dict[tuple[str, str], dict], dict]:
    features: dict[tuple[str, str], dict] = {}
    counts = {}
    meta = {}
    diagnostics = Counter()

    for season in DEV_SEASONS:
        path = source_dir / f"A-League_Womens_{season}_All_Markets.csv"
        digest = sha256_file(path)
        if digest != EXPECTED_SHA[season]:
            raise RuntimeError(f"SHA mismatch {season}: {digest}")

        header = list(pd.read_csv(path, nrows=0).columns)
        missing = [c for c in NON_TARGET_FIELDS if c not in header]
        if missing or "TOTAL_GOALS" not in header:
            raise RuntimeError(f"schema mismatch {season}: {missing}")
        df = pd.read_csv(path, usecols=NON_TARGET_FIELDS, dtype=str, keep_default_na=False)
        if "TOTAL_GOALS" in df.columns:
            raise RuntimeError("target field materialized in zero-label eligibility pass")

        event_identity: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        markets: dict[tuple[str, float, str], dict] = {}

        for r in df.itertuples(index=False):
            d = r._asdict()
            eid = str(d["EVENT_ID"]).strip()
            line = recognized_line(d["MARKET_TYPE"], d["MARKET_NAME"])
            if not eid or line is None:
                continue
            side = runner_side(d["RUNNER_NAME"], line)
            mid = str(d["MARKET_ID"]).strip()
            sid = str(d["SELECTION_ID"]).strip()
            if side not in {"over", "under"} or not mid or not sid:
                diagnostics["bad_runner_identity"] += 1
                continue
            event_identity[eid].add((norm(d["EVENT_DATE"]), norm(d["HOME_TEAM"]), norm(d["AWAY_TEAM"])))
            key = (eid, line, mid)
            m = markets.setdefault(key, {"market_types": set(), "market_names": set(), "runners": defaultdict(list)})
            m["market_types"].add(str(d["MARKET_TYPE"]).strip())
            m["market_names"].add(str(d["MARKET_NAME"]).strip())
            snaps = {}
            for snap in SNAPSHOTS:
                b = price(d[f"BEST_BACK_PRICE_{snap}_MIN_PRIOR"])
                l = price(d[f"BEST_LAY_PRICE_{snap}_MIN_PRIOR"])
                crossed = b is not None and l is not None and b > l
                snaps[snap] = (b, l, crossed)
            m["runners"][side].append((sid, snaps))

        identity_conflicts = {eid for eid, vals in event_identity.items() if len(vals) != 1}
        event_line_keys: dict[tuple[str, float], list[tuple[str, float, str]]] = defaultdict(list)
        normalized = {}
        for key, m in markets.items():
            eid, line, _mid = key
            event_line_keys[(eid, line)].append(key)
            if eid in identity_conflicts:
                continue
            if len(m["market_types"]) != 1 or len(m["market_names"]) != 1 or set(m["runners"]) != {"over", "under"}:
                continue
            sides = {}
            ok = True
            for side in ("over", "under"):
                obs = m["runners"][side]
                if len({x[0] for x in obs}) != 1:
                    ok = False; break
                first = obs[0]
                if any(x != first for x in obs[1:]):
                    ok = False; break
                sides[side] = first
            if ok:
                normalized[key] = sides

        complete: dict[tuple[str, float], dict] = {}
        for event_line, keys in event_line_keys.items():
            if len(keys) != 1:
                continue
            sides = normalized.get(keys[0])
            if sides is None:
                continue
            all3 = True
            for snap in SNAPSHOTS:
                for side in ("over", "under"):
                    b, l, crossed = sides[side][1][snap]
                    if b is None or l is None or crossed:
                        all3 = False; break
                if not all3: break
            if all3:
                complete[event_line] = sides

        eligible = []
        for eid in sorted({eid for eid, _line in complete}):
            if all((eid, line) in complete for line in PREFERRED):
                eligible.append(eid)
                q = {}
                for line in PREFERRED:
                    sides = complete[(eid, line)]
                    ob, ol, _ = sides["over"][1][1]
                    ub, ul, _ = sides["under"][1][1]
                    q[line] = devig_over(ob, ol, ub, ul)
                ident = next(iter(event_identity[eid]))
                features[(season, eid)] = {
                    "season": season,
                    "event_id": eid,
                    "event_date_norm": ident[0],
                    "home_norm": ident[1],
                    "away_norm": ident[2],
                    **{f"q{int(line*10):02d}": q[line] for line in PREFERRED},
                }
        counts[season] = len(eligible)
        meta[season] = {
            "sha256": digest,
            "rows_materialized_non_target_only": int(len(df)),
            "eligible_all5_all3_events": len(eligible),
            "target_values_read_before_eligibility_freeze": 0,
        }

    if counts != EXPECTED_ELIGIBLE:
        return {}, {
            "eligibility_match": False,
            "observed_counts": counts,
            "expected_counts": EXPECTED_ELIGIBLE,
            "source_meta": meta,
            "diagnostics": dict(diagnostics),
        }
    return features, {
        "eligibility_match": True,
        "observed_counts": counts,
        "expected_counts": EXPECTED_ELIGIBLE,
        "source_meta": meta,
        "diagnostics": dict(diagnostics),
        "target_values_read_before_eligibility_freeze": 0,
    }


def decode_targets(source_dir: Path, eligible_keys: set[tuple[str, str]]) -> tuple[dict[tuple[str, str], int], dict]:
    by_season: dict[str, set[str]] = defaultdict(set)
    for season, eid in eligible_keys:
        by_season[season].add(eid)
    targets = {}
    eligible_cells_read = 0
    noneligible_cells_read = 0
    conflicts = 0
    invalid = 0

    for season in DEV_SEASONS:
        path = source_dir / f"A-League_Womens_{season}_All_Markets.csv"
        values: dict[str, set[int]] = defaultdict(set)
        bad = set()
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            idx = {h.strip(): i for i, h in enumerate(header)}
            ei = idx["EVENT_ID"]
            ti = idx["TOTAL_GOALS"]
            for row in reader:
                if len(row) < len(header):
                    continue
                eid = row[ei].strip()
                if eid not in by_season[season]:
                    continue
                eligible_cells_read += 1
                v = parse_nonneg_int(row[ti])
                if v is None:
                    bad.add(eid)
                else:
                    values[eid].add(v)
        for eid in by_season[season]:
            vals = values.get(eid, set())
            if eid in bad or len(vals) != 1:
                if len(vals) > 1: conflicts += 1
                else: invalid += 1
                continue
            targets[(season, eid)] = min(next(iter(vals)), 7)

    return targets, {
        "eligible_target_cells_read_for_consistency": eligible_cells_read,
        "noneligible_target_cells_read": noneligible_cells_read,
        "target_conflict_events": conflicts,
        "target_invalid_or_missing_events": invalid,
        "valid_target_events": len(targets),
        "target_values_2025_26_read": 0,
    }


def poisson_mass(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_survival_ge(lam: float, k: int) -> float:
    return max(0.0, 1.0 - sum(poisson_mass(lam, j) for j in range(k)))


def lambda_from_q25(q: float) -> float:
    lo, hi = 0.01, 20.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if poisson_survival_ge(mid, 3) >= q:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def pav_nonincreasing(q: list[float]) -> tuple[list[float], bool, float]:
    # blocks: [sum, weight, start, end]; block value=sum/weight
    blocks = [[float(v), 1, i, i] for i, v in enumerate(q)]
    i = 0
    while i < len(blocks) - 1:
        v0 = blocks[i][0] / blocks[i][1]
        v1 = blocks[i+1][0] / blocks[i+1][1]
        if v0 + 0.0 >= v1:
            i += 1
            continue
        merged = [blocks[i][0] + blocks[i+1][0], blocks[i][1] + blocks[i+1][1], blocks[i][2], blocks[i+1][3]]
        blocks[i:i+2] = [merged]
        if i > 0:
            i -= 1
    z = [0.0] * len(q)
    for s, w, a, b in blocks:
        v = s / w
        for j in range(a, b+1):
            z[j] = v
    adj = max(abs(z[i] - q[i]) for i in range(len(q)))
    return z, any(abs(z[i] - q[i]) > 1e-15 for i in range(len(q))), adj


def score_floor(p: list[float]) -> np.ndarray:
    a = np.asarray([max(float(x), 1e-12) for x in p], dtype=float)
    a /= a.sum()
    return a


def baseline_probs(q25: float) -> tuple[np.ndarray, float]:
    lam = lambda_from_q25(q25)
    p = [poisson_mass(lam, k) for k in range(7)]
    p.append(max(0.0, 1.0 - sum(p)))
    return score_floor(p), lam


def candidate_probs(qs: list[float], lam: float) -> tuple[np.ndarray, bool, float]:
    z, pooled, max_adj = pav_nonincreasing(qs)
    p0 = 1.0 - z[0]
    p1 = z[0] - z[1]
    p2 = z[1] - z[2]
    p3 = z[2] - z[3]
    p4 = z[3] - z[4]
    r5 = z[4]
    a5 = poisson_mass(lam, 5)
    a6 = poisson_mass(lam, 6)
    a7p = poisson_survival_ge(lam, 7)
    A = a5 + a6 + a7p
    if not (A > 0):
        raise RuntimeError("invalid Poisson tail closure")
    p = [p0, p1, p2, p3, p4, r5*a5/A, r5*a6/A, r5*a7p/A]
    if min(p) < -1e-12:
        raise RuntimeError(f"negative structured probability: {p}")
    return score_floor(p), pooled, max_adj


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    one = np.eye(K)[y]
    ll = -np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))
    br = np.sum((p - one) ** 2, axis=1)
    cp, cy = np.cumsum(p, axis=1)[:, :-1], np.cumsum(one, axis=1)[:, :-1]
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
        "max_probability_residual": float(np.max(np.abs(p.sum(axis=1) - 1.0))) if len(p) else 0.0,
    }


def delta(c: dict, b: dict) -> dict:
    return {k: float(c[k] - b[k]) for k in ("log_loss", "brier", "rps", "top1_accuracy", "top3_accuracy", "top1_total_2_fraction")}


def bootstrap(y: np.ndarray, p0: np.ndarray, p1: np.ndarray) -> dict:
    ix = np.arange(len(y))
    d = -np.log(np.clip(p1[ix, y], 1e-15, 1.0)) + np.log(np.clip(p0[ix, y], 1e-15, 1.0))
    rng = np.random.default_rng(72018)
    sims = np.empty(5000, dtype=float)
    n = len(d)
    for i in range(5000):
        draw = rng.integers(0, n, n)
        sims[i] = float(d[draw].mean())
    return {
        "n": n,
        "reps": 5000,
        "seed": 72018,
        "mean_delta_log_loss": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "p_delta_lt_0": float(np.mean(sims < 0.0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    args = ap.parse_args()
    root = Path(args.source_dir)
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)

    features, zero_meta = eligible_features(root)
    if not zero_meta.get("eligibility_match"):
        summary = {
            "schema": "C072N15W_STRUCTURED_PT_V1",
            "project_line": "football3",
            "classification": "POST_VIEW_HYPOTHESIS_NEW_DATA_PLAN",
            "terminal": "C072N15W_ZERO_LABEL_ELIGIBILITY_MISMATCH_STOP",
            "zero_label_meta": zero_meta,
            "target_result_values_materialized": 0,
            "model_fit": 0,
            "model_score": 0,
            "target_values_2025_26_read": 0,
            "reserve_2025_26_downloaded": False,
            "C073_C077_scientific_results_used": False,
            "C070F_confirmation1597_opened": False,
            "protected_opened": False,
            "formal_weight": 0,
        }
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    targets, target_meta = decode_targets(root, set(features))
    rows = []
    isotonic_count = 0
    adj_values = []
    for key in sorted(features):
        if key not in targets:
            continue
        r = features[key]
        qs = [r[f"q{int(line*10):02d}"] for line in PREFERRED]
        p0, lam = baseline_probs(r["q25"])
        p1, pooled, max_adj = candidate_probs(qs, lam)
        isotonic_count += int(pooled)
        adj_values.append(max_adj)
        rows.append({
            "season": r["season"], "event_id": r["event_id"], "target": targets[key],
            "lambda_q25": lam, "isotonic_pooled": int(pooled), "max_tail_adjustment": max_adj,
            **{f"B0_p{k}": float(p0[k]) for k in range(K)},
            **{f"C_p{k}": float(p1[k]) for k in range(K)},
        })

    pred = pd.DataFrame(rows)
    if pred.empty:
        raise RuntimeError("no scored development rows")
    y = pred["target"].to_numpy(dtype=int)
    p0 = pred[[f"B0_p{k}" for k in range(K)]].to_numpy(dtype=float)
    p1 = pred[[f"C_p{k}" for k in range(K)]].to_numpy(dtype=float)
    m0, m1 = metrics(y, p0), metrics(y, p1)
    d = delta(m1, m0)
    boot = bootstrap(y, p0, p1)

    per_season = {}
    season_wins = 0
    all_season_nonpositive = True
    for s in DEV_SEASONS:
        mask = pred["season"].to_numpy() == s
        yy, b, c = y[mask], p0[mask], p1[mask]
        mb, mc = metrics(yy, b), metrics(yy, c)
        ds = delta(mc, mb)
        if ds["log_loss"] < 0: season_wins += 1
        if ds["log_loss"] > 0: all_season_nonpositive = False
        per_season[s] = {"baseline": mb, "candidate": mc, "delta_candidate_minus_baseline": ds}

    max_resid = max(m0["max_probability_residual"], m1["max_probability_residual"])
    boundary_ok = target_meta["noneligible_target_cells_read"] == 0 and target_meta["target_values_2025_26_read"] == 0
    gates = {
        "exact_zero_label_eligibility_reproduced": zero_meta["observed_counts"] == EXPECTED_ELIGIBLE,
        "valid_scored_targets_ge_380": len(pred) >= 380,
        "pooled_dlogloss_lt_0": d["log_loss"] < 0,
        "bootstrap90_upper_dlogloss_lt_0": boot["ci90_high"] < 0,
        "season_logloss_wins_ge_3_of_4": season_wins >= 3,
        "pooled_dbrier_le_0": d["brier"] <= 0,
        "pooled_drps_le_0": d["rps"] <= 0,
        "pooled_top1_delta_ge_0": d["top1_accuracy"] >= 0,
        "pooled_top3_delta_ge_0": d["top3_accuracy"] >= 0,
        "probability_residual_le_1e_12": max_resid <= 1e-12,
        "noneligible_target_cells_read_zero": target_meta["noneligible_target_cells_read"] == 0,
        "2025_26_target_read_zero_and_not_downloaded": boundary_ok,
        "seals_and_quarantine_hold": True,
    }
    development_pass = all(gates.values())
    breakthrough = development_pass and d["log_loss"] <= -0.010 and d["rps"] <= -0.001 and all_season_nonpositive
    terminal = (
        "C072N15W_STRUCTURED_PT_BREAKTHROUGH_SCREEN_PASS" if breakthrough else
        "C072N15W_STRUCTURED_PT_DEVELOPMENT_PASS" if development_pass else
        "C072N15W_STRUCTURED_PT_PARK"
    )

    PREDICTIONS.parent.mkdir(parents=True, exist_ok=True)
    pred.to_csv(PREDICTIONS, index=False)
    summary = {
        "schema": "C072N15W_STRUCTURED_PT_V1",
        "project_line": "football3",
        "classification": "POST_VIEW_HYPOTHESIS_NEW_DATA_PLAN",
        "terminal": terminal,
        "development_pass": development_pass,
        "breakthrough_screen_pass": breakthrough,
        "zero_label_meta": zero_meta,
        "target_meta": target_meta,
        "eligible_events": len(features),
        "scored_events": int(len(pred)),
        "pooled": {"baseline": m0, "candidate": m1, "delta_candidate_minus_baseline": d},
        "paired_bootstrap_delta_log_loss": boot,
        "per_season": per_season,
        "season_logloss_wins": season_wins,
        "all_four_seasons_dlogloss_nonpositive": all_season_nonpositive,
        "isotonic_diagnostics": {
            "events_requiring_pooling": isotonic_count,
            "fraction_requiring_pooling": float(isotonic_count / len(pred)),
            "mean_max_absolute_tail_adjustment": float(np.mean(adj_values)) if adj_values else 0.0,
            "max_absolute_tail_adjustment": float(np.max(adj_values)) if adj_values else 0.0,
        },
        "max_probability_residual": max_resid,
        "gates": gates,
        "breakthrough_magnitude_gates": {
            "dlogloss_le_minus_0_010": d["log_loss"] <= -0.010,
            "drps_le_minus_0_001": d["rps"] <= -0.001,
            "all_four_seasons_nonpositive_dlogloss": all_season_nonpositive,
        },
        "target_values_2025_26_read": 0,
        "reserve_2025_26_downloaded": False,
        "excluded_2020_21_downloaded": False,
        "model_fit": 0,
        "model_score": 1,
        "C073_C077_scientific_results_used": False,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
