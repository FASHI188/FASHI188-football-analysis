#!/usr/bin/env python3
"""V6.18.1 shot-informed direct total-goals challenger.

Research-only. Directly predicts P(T=0,1,...,6,7+) and compares three arms:
A formal: unchanged formal total-goals marginal from the calibrated score matrix.
B calibration: multinomial residual calibration using only log formal P(T).
C shot: B plus strictly lagged shot / SOT / corner process features.

The purpose is to isolate whether pre-match attack-process information adds total-goal
information beyond re-calibrating the existing formal P(T). No current-match event
statistic is used as a feature. Team rolling states are frozen for all matches on the
same date and updated only after the whole date is scored.

Chronology:
- candidate regularization is selected only on an earlier-season validation block;
- the next season is then evaluated untouched by that fold's model selection;
- because this project has already inspected 2025/26 in other research, these tests
  are RETROSPECTIVE chronological OOS, not promotion-grade prospective evidence.

No formal weight/runtime/CURRENT change.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "engine"
V = ROOT / "validation"
for p in (E, V):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config, predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import canonical_team_name, load_aliases, parse_match_date, read_processed_matches

OUT = ROOT / "manifests" / "v6_total_shot_residual_v6181_status.json"
COMPS = (
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
    "ESP_LaLiga", "POR_PrimeiraLiga", "NED_Eredivisie", "SCO_Premiership",
)
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")
STAT_COLS = ("HS", "AS", "HST", "AST", "HC", "AC")
CANDIDATE_C = (0.01, 0.03, 0.1, 0.3, 1.0)
TOTAL_CAP = 7
EPS = 1e-15
MIN_TRAIN = 800
MIN_VALID = 250
MIN_TEST = 250


def num(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def mean(items, key):
    vals = [float(x[key]) for x in items if x.get(key) is not None]
    return sum(vals) / len(vals) if vals else 0.0


def profile(history, n):
    xs = list(history)[-n:]
    return {
        "sf": mean(xs, "sf"), "sa": mean(xs, "sa"),
        "sotf": mean(xs, "sotf"), "sota": mean(xs, "sota"),
        "cf": mean(xs, "cf"), "ca": mean(xs, "ca"),
    }


def raw_stat_matches():
    aliases = load_aliases()
    rows = []
    seen = set()
    by_comp_season = Counter()
    for cid in COMPS:
        directory = ROOT / "processed" / cid
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw0 in csv.DictReader(handle):
                    raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in SEASONS or not raw.get("Date") or not raw.get("HomeTeam") or not raw.get("AwayTeam"):
                        continue
                    stats = {k: num(raw.get(k)) for k in STAT_COLS}
                    hg, ag = num(raw.get("FTHG")), num(raw.get("FTAG"))
                    if hg is None or ag is None or any(stats[k] is None for k in STAT_COLS):
                        continue
                    try:
                        dt = parse_match_date(raw["Date"], season)
                    except Exception:
                        continue
                    home = canonical_team_name(cid, raw["HomeTeam"], aliases)
                    away = canonical_team_name(cid, raw["AwayTeam"], aliases)
                    key = (cid, season, dt.date().isoformat(), home, away)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "competition_id": cid, "season": season, "date": dt,
                        "home_team": home, "away_team": away,
                        "home_goals": int(hg), "away_goals": int(ag), **stats,
                    })
                    by_comp_season[(cid, season)] += 1
    return rows, {f"{k[0]}::{k[1]}": v for k, v in sorted(by_comp_season.items())}


def lagged_shot_lookup(raw_rows):
    groups = defaultdict(list)
    for r in raw_rows:
        groups[(r["competition_id"], r["season"])].append(r)
    lookup = {}
    feature_names = None
    for (cid, season), matches in groups.items():
        bydate = defaultdict(list)
        for r in matches:
            bydate[r["date"].date()].append(r)
        hist = defaultdict(lambda: deque(maxlen=10))
        for d in sorted(bydate):
            todays = sorted(bydate[d], key=lambda r: (r["home_team"], r["away_team"]))
            pending = []
            for r in todays:
                hh, ah = hist[r["home_team"]], hist[r["away_team"]]
                if len(hh) >= 3 and len(ah) >= 3:
                    h5, a5 = profile(hh, 5), profile(ah, 5)
                    h10, a10 = profile(hh, 10), profile(ah, 10)
                    feat = {
                        "h_sf5": h5["sf"], "h_sa5": h5["sa"], "h_sotf5": h5["sotf"], "h_sota5": h5["sota"],
                        "a_sf5": a5["sf"], "a_sa5": a5["sa"], "a_sotf5": a5["sotf"], "a_sota5": a5["sota"],
                        "h_sf10": h10["sf"], "h_sa10": h10["sa"], "h_sotf10": h10["sotf"], "h_sota10": h10["sota"],
                        "a_sf10": a10["sf"], "a_sa10": a10["sa"], "a_sotf10": a10["sotf"], "a_sota10": a10["sota"],
                        "h_cf5": h5["cf"], "h_ca5": h5["ca"], "a_cf5": a5["cf"], "a_ca5": a5["ca"],
                        "h_sot_rate5": h5["sotf"] / max(h5["sf"], 1e-6),
                        "a_sot_rate5": a5["sotf"] / max(a5["sf"], 1e-6),
                        "expected_shots5": 0.5 * (h5["sf"] + a5["sa"] + a5["sf"] + h5["sa"]),
                        "expected_sot5": 0.5 * (h5["sotf"] + a5["sota"] + a5["sotf"] + h5["sota"]),
                        "expected_corners5": 0.5 * (h5["cf"] + a5["ca"] + a5["cf"] + h5["ca"]),
                        "shot_balance5": (h5["sf"] - h5["sa"]) - (a5["sf"] - a5["sa"]),
                        "sot_balance5": (h5["sotf"] - h5["sota"]) - (a5["sotf"] - a5["sota"]),
                    }
                    if feature_names is None:
                        feature_names = tuple(sorted(feat))
                    lookup[(cid, season, r["date"].date().isoformat(), r["home_team"], r["away_team"])] = feat
                pending.append(r)
            # Strict same-date PIT: no match on this date sees another match's final stats.
            for r in pending:
                hist[r["home_team"]].append({
                    "sf": r["HS"], "sa": r["AS"], "sotf": r["HST"], "sota": r["AST"], "cf": r["HC"], "ca": r["AC"],
                })
                hist[r["away_team"]].append({
                    "sf": r["AS"], "sa": r["HS"], "sotf": r["AST"], "sota": r["HST"], "cf": r["AC"], "ca": r["HC"],
                })
    return lookup, list(feature_names or [])


def total_vec(matrix):
    out = [0.0] * (TOTAL_CAP + 1)
    for c in matrix:
        h, a, p = int(c["home_goals"]), int(c["away_goals"]), float(c["probability"])
        out[min(TOTAL_CAP, h + a)] += p
    s = sum(out)
    return [x / s for x in out]


def formal_rows(shot_lookup):
    cfg = load_config()
    all_rows = []
    meta = {}
    for cid in COMPS:
        params_map = ou.params_by_season(cid)
        matches = [m for m in read_processed_matches(cid) if str(m.season) in SEASONS]
        byseason = defaultdict(list)
        for m in matches:
            byseason[str(m.season)].append(m)
        meta[cid] = {}
        for season in SEASONS:
            sm = byseason.get(season, [])
            params = params_map.get(season)
            if not sm or not params:
                meta[cid][season] = {"matches": len(sm), "rows": 0, "reason": "NO_MATCHES_OR_PARAMS"}
                continue
            temp = ou.calibrator(cid, season)
            bydate = defaultdict(list)
            for m in sm:
                bydate[m.date].append(m)
            hist = []
            hc = Counter(); ac = Counter(); count = 0; failures = 0
            warmc = int(cfg["validation"]["warmup_competition_matches"])
            warmt = int(cfg["validation"]["warmup_team_matches"])
            for dt in sorted(bydate):
                for m in sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team)):
                    key = (cid, season, m.date.isoformat(), m.home_team, m.away_team)
                    feat = shot_lookup.get(key)
                    if feat is not None and len(hist) >= warmc and hc[m.home_team] >= warmt and ac[m.away_team] >= warmt:
                        try:
                            pred = predict_from_history(
                                hist, cid, season, m.home_team, m.away_team, m.date,
                                selected_parameters=params, use_team_effects=True,
                            )
                            matrix = temperature_scale_matrix(pred["probabilities"]["score_matrix"], temp)
                            p = total_vec(matrix)
                            all_rows.append({
                                "competition_id": cid, "season": season, "date": m.date.isoformat(),
                                "home_team": m.home_team, "away_team": m.away_team,
                                "actual": min(TOTAL_CAP, int(m.home_goals) + int(m.away_goals)),
                                "formal": p, "shots": feat,
                            })
                            count += 1
                        except Exception:
                            failures += 1
                    hist.append(m); hc[m.home_team] += 1; ac[m.away_team] += 1
            meta[cid][season] = {"matches": len(sm), "rows": count, "prediction_failures": failures}
    all_rows.sort(key=lambda r: (r["season"], r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    return all_rows, meta


def rps(p, y):
    cp = 0.0; score = 0.0
    for k in range(TOTAL_CAP):
        cp += p[k]
        cy = 1.0 if y <= k else 0.0
        score += (cp - cy) ** 2
    return score / TOTAL_CAP


def metrics(rows, probs_getter):
    n = len(rows); top1 = 0; rps_sum = 0.0; ll = 0.0
    for r, p in ((r, probs_getter(r)) for r in rows):
        y = int(r["actual"])
        top1 += int(max(range(len(p)), key=lambda i: p[i]) == y)
        rps_sum += rps(p, y)
        ll += -math.log(max(EPS, p[y]))
    return {
        "count": n,
        "top1": top1,
        "top1_rate": top1 / n if n else None,
        "rps": rps_sum / n if n else None,
        "logloss": ll / n if n else None,
    }


def feature_names(rows):
    shot_names = sorted(rows[0]["shots"])
    comps = sorted({r["competition_id"] for r in rows})
    return shot_names, comps


def xvec(r, mode, shot_names, comps):
    # Log probabilities make multiplicative residual corrections approximately linear.
    x = [math.log(max(EPS, float(v))) for v in r["formal"]]
    if mode == "shot":
        x.extend(float(r["shots"][k]) for k in shot_names)
        x.extend(1.0 if r["competition_id"] == c else 0.0 for c in comps)
    return x


def fit_model(rows, mode, c_value, shot_names, comps):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    X = [xvec(r, mode, shot_names, comps) for r in rows]
    y = [int(r["actual"]) for r in rows]
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=float(c_value), max_iter=4000, solver="lbfgs"),
    )
    model.fit(X, y)
    return model


def model_probs(model, r, mode, shot_names, comps):
    arr = model.predict_proba([xvec(r, mode, shot_names, comps)])[0]
    classes = list(model.classes_)
    p = [0.0] * (TOTAL_CAP + 1)
    for cls, v in zip(classes, arr):
        p[int(cls)] = float(v)
    s = sum(p)
    return [v / s for v in p]


def select_candidate(train, valid, mode, shot_names, comps):
    baseline = metrics(valid, lambda r: r["formal"])
    board = []
    for c in CANDIDATE_C:
        model = fit_model(train, mode, c, shot_names, comps)
        m = metrics(valid, lambda r, model=model: model_probs(model, r, mode, shot_names, comps))
        m["C"] = c
        m["proper_noninferior"] = bool(
            m["rps"] <= baseline["rps"] and m["logloss"] <= baseline["logloss"]
        )
        board.append(m)
    eligible = [m for m in board if m["proper_noninferior"]]
    selected = max(eligible, key=lambda m: (m["top1_rate"], -m["rps"], -m["logloss"], -m["C"])) if eligible else None
    return baseline, board, selected


def season_rows(rows, season):
    return [r for r in rows if r["season"] == season]


def fold(train_seasons, valid_season, test_season, rows, mode, shot_names, comps):
    train = [r for r in rows if r["season"] in set(train_seasons)]
    valid = season_rows(rows, valid_season)
    test = season_rows(rows, test_season)
    if len(train) < MIN_TRAIN or len(valid) < MIN_VALID or len(test) < MIN_TEST:
        return {"status": "INSUFFICIENT_ROWS", "train": len(train), "valid": len(valid), "test": len(test)}
    val_base, board, selected = select_candidate(train, valid, mode, shot_names, comps)
    if selected is None:
        return {
            "status": "NO_PROPER_NONINFERIOR_CANDIDATE",
            "train": len(train), "valid": len(valid), "test": len(test),
            "validation_baseline": val_base, "leaderboard": board,
        }
    final_train = train + valid
    model = fit_model(final_train, mode, selected["C"], shot_names, comps)
    base_test = metrics(test, lambda r: r["formal"])
    cand_test = metrics(test, lambda r: model_probs(model, r, mode, shot_names, comps))
    return {
        "status": "PASS", "train": len(train), "valid": len(valid), "test": len(test),
        "validation_baseline": val_base, "leaderboard": board,
        "selected_C": selected["C"], "selected_validation": selected,
        "test_baseline": base_test, "test_candidate": cand_test,
        "delta": {
            "top1_rate": cand_test["top1_rate"] - base_test["top1_rate"],
            "rps": cand_test["rps"] - base_test["rps"],
            "logloss": cand_test["logloss"] - base_test["logloss"],
        },
    }


def main():
    raw, stat_counts = raw_stat_matches()
    lookup, shot_names = lagged_shot_lookup(raw)
    rows, formal_meta = formal_rows(lookup)
    if not rows or not shot_names:
        raise RuntimeError("no joined formal/shot rows")
    shot_names2, comps = feature_names(rows)
    if shot_names2 != sorted(shot_names):
        shot_names = shot_names2

    designs = [
        (("2022/23",), "2023/24", "2024/25"),
        (("2022/23", "2023/24"), "2024/25", "2025/26"),
    ]
    results = {"calibration": [], "shot": []}
    for train_seasons, valid_season, test_season in designs:
        for mode in ("calibration", "shot"):
            results[mode].append({
                "train_seasons": list(train_seasons), "valid_season": valid_season, "test_season": test_season,
                **fold(train_seasons, valid_season, test_season, rows, mode, shot_names, comps),
            })

    def pass_count(mode):
        return sum(1 for r in results[mode] if r.get("status") == "PASS")

    payload = {
        "schema_version": "V6.18.1-shot-informed-direct-total-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_CHRONOLOGICAL_OOS_RESEARCH_NOT_PROSPECTIVE",
        "design": {
            "target": "direct P(T=0..6,7+)",
            "arms": {
                "formal": "unchanged calibrated formal total marginal",
                "calibration": "multinomial residual model using only log formal P(T)",
                "shot": "same residual model plus strictly lagged shots/SOT/corners",
            },
            "candidate_C": list(CANDIDATE_C),
            "selection_gate": "validation RPS and LogLoss both <= formal baseline; then maximize exact-total Top-1",
            "same_date_event_stats_frozen": True,
            "current_match_stats_used_as_features": False,
            "test_used_for_parameter_selection": False,
        },
        "joined_rows": len(rows),
        "shot_feature_names": shot_names,
        "stat_rows_by_competition_season": stat_counts,
        "formal_join_meta": formal_meta,
        "results": results,
        "summary": {
            "calibration_pass_folds": pass_count("calibration"),
            "shot_pass_folds": pass_count("shot"),
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "2025_26_not_claimed_as_pristine_holdout": True,
            "promotion_requires_new_prospective_evidence_and_full_joint_matrix_audit": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "joined_rows": len(rows),
        "summary": payload["summary"],
        "results": {
            mode: [
                {"test_season": r["test_season"], "status": r["status"], "selected_C": r.get("selected_C"), "delta": r.get("delta")}
                for r in vals
            ] for mode, vals in results.items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
