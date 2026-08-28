#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import subprocess
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
B_DIR = ROOT / "football-data" / "research" / "r44l7b_setpiece_full_zero_label"
if str(B_DIR) not in sys.path:
    sys.path.insert(0, str(B_DIR))

import run_full_coverage_r44l7b as b  # noqa: E402

SOURCE_R44L7B_HEAD = "5dd07c66634779609b4691520bc6904a56cd155a"
SOURCE_R44L7C_FAILURE_HEAD = "1f51c695181da6ddfdc7d54b1768d545b0c6cacb"
SOURCE_PIN = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
DOMAINS = [
    {"competition_id": 2, "season_id": 27, "name": "Premier League"},
    {"competition_id": 12, "season_id": 27, "name": "Serie A"},
    {"competition_id": 7, "season_id": 27, "name": "Ligue 1"},
]
WARMUP_PER_DOMAIN = 100
SETPIECE_WINDOW = 5
RESULT_WINDOW = 10
TRAIN_SEED_TARGET = 180
N_FOLDS = 3
C_FIXED = 1.0
BOOTSTRAP_N = 5000
BOOTSTRAP_SEED = 20260828
BREAKTHROUGH_TOP1_PP = 1.0

BASE_NAMES = [
    "home_gf10", "home_ga10", "home_pts10", "home_gd10", "home_draw10",
    "away_gf10", "away_ga10", "away_pts10", "away_gd10", "away_draw10",
    "comp_premier", "comp_seriea",
]
SP_NAMES = [
    "home_sp_passes5", "home_sp_unique5", "home_sp_top1_share5", "home_sp_top1_in_xi", "home_sp_xi_retention5",
    "away_sp_passes5", "away_sp_unique5", "away_sp_top1_share5", "away_sp_top1_in_xi", "away_sp_xi_retention5",
]
CAND_NAMES = BASE_NAMES + SP_NAMES


def ts_key(r: dict):
    return (r["match_date"], r["kick_off"], int(r["match_id"]))


def group_key(r: dict):
    return (r["match_date"], r["kick_off"])


def boundary_at_least(rows: list[dict], target: int) -> int:
    if target >= len(rows):
        return len(rows)
    i = max(1, target)
    key = group_key(rows[i - 1])
    while i < len(rows) and group_key(rows[i]) == key:
        i += 1
    return i


def outcome(home_score: int, away_score: int) -> int:
    return 0 if home_score > away_score else 1 if home_score == away_score else 2


def result_state(hist: deque) -> list[float]:
    if not hist:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    n = float(len(hist))
    gf = sum(x[0] for x in hist) / n
    ga = sum(x[1] for x in hist) / n
    pts = sum(x[2] for x in hist) / n
    draw = sum(x[3] for x in hist) / n
    return [float(gf), float(ga), float(pts), float(gf - ga), float(draw)]


def sp_state(prior: deque, xi: set[int]) -> list[float] | None:
    if len(prior) != SETPIECE_WINDOW:
        return None
    total = Counter()
    for c in prior:
        total.update(c)
    nsp = int(sum(total.values()))
    if nsp <= 0:
        return None
    top_pid, top_n = total.most_common(1)[0]
    retention = sum(v for pid, v in total.items() if pid in xi) / nsp
    return [
        float(nsp),
        float(len(total)),
        float(top_n / nsp),
        float(top_pid in xi),
        float(retention),
    ]


def load_rows():
    by_domain = {}
    source_manifest = []
    all_ids = []
    target_ids = []
    for d in DOMAINS:
        obj, sha = b.fetch_json(f"data/matches/{d['competition_id']}/{d['season_id']}.json")
        source_manifest.append({"path": f"data/matches/{d['competition_id']}/{d['season_id']}.json", "sha256": sha})
        rows = []
        for m in obj:
            if int(m["competition"]["competition_id"]) != d["competition_id"] or int(m["season"]["season_id"]) != d["season_id"]:
                raise RuntimeError("domain identity mismatch")
            rows.append({
                "domain": d["name"],
                "competition_id": d["competition_id"],
                "match_id": int(m["match_id"]),
                "match_date": str(m["match_date"]),
                "kick_off": str(m.get("kick_off") or ""),
                "home_team_id": int(m["home_team"]["home_team_id"]),
                "away_team_id": int(m["away_team"]["away_team_id"]),
                "home_score": int(m["home_score"]),
                "away_score": int(m["away_score"]),
            })
        rows.sort(key=ts_key)
        by_domain[d["name"]] = rows
        all_ids.extend(r["match_id"] for r in rows)
        target_ids.extend(r["match_id"] for r in rows[WARMUP_PER_DOMAIN:])

    event_map, event_errors = b.parallel_map(b.extract_event_summary, all_ids)
    lineup_map, lineup_errors = b.parallel_map(b.extract_lineup, target_ids)
    if event_errors or lineup_errors:
        raise RuntimeError(f"source download errors event={len(event_errors)} lineup={len(lineup_errors)}")

    records = []
    same_match_result_before_feature = 0
    same_match_event_before_feature = 0
    domain_stats = {}
    for d in DOMAINS:
        name = d["name"]
        result_hist = defaultdict(lambda: deque(maxlen=RESULT_WINDOW))
        sp_hist = defaultdict(lambda: deque(maxlen=SETPIECE_WINDOW))
        rows = by_domain[name]
        created = 0
        exact = 0
        for idx, r in enumerate(rows):
            mid = r["match_id"]
            ht, at = r["home_team_id"], r["away_team_id"]
            if idx >= WARMUP_PER_DOMAIN:
                lineup_tuple = lineup_map.get(mid)
                lineups = lineup_tuple[1] if lineup_tuple else {}
                hxi = set(lineups.get(ht, [])); axi = set(lineups.get(at, []))
                exact_11 = len(hxi) == 11 and len(axi) == 11
                if exact_11:
                    exact += 1
                hs = sp_state(sp_hist[ht], hxi) if exact_11 else None
                a_s = sp_state(sp_hist[at], axi) if exact_11 else None
                if exact_11 and hs is not None and a_s is not None and len(result_hist[ht]) >= 5 and len(result_hist[at]) >= 5:
                    hb = result_state(result_hist[ht]); ab = result_state(result_hist[at])
                    comp = [float(name == "Premier League"), float(name == "Serie A")]
                    base = hb + ab + comp
                    cand = base + hs + a_s
                    if len(base) != len(BASE_NAMES) or len(cand) != len(CAND_NAMES):
                        raise RuntimeError("feature length drift")
                    records.append({
                        **r,
                        "y": outcome(r["home_score"], r["away_score"]),
                        "base": base,
                        "candidate": cand,
                    })
                    created += 1

            # Strict prior-only: update result and set-piece histories only after current feature sealing.
            hs_now, as_now = r["home_score"], r["away_score"]
            if hs_now > as_now:
                hp, ap, dr = 3.0, 0.0, 0.0
            elif hs_now == as_now:
                hp, ap, dr = 1.0, 1.0, 1.0
            else:
                hp, ap, dr = 0.0, 3.0, 0.0
            result_hist[ht].append((float(hs_now), float(as_now), hp, dr))
            result_hist[at].append((float(as_now), float(hs_now), ap, dr))
            ev = event_map.get(mid)
            if ev:
                sp_hist[ht].append(Counter(ev[1].get(ht, {})))
                sp_hist[at].append(Counter(ev[1].get(at, {})))
        domain_stats[name] = {"identity_matches": len(rows), "target_capacity": max(0, len(rows) - WARMUP_PER_DOMAIN), "exact_11v11": exact, "eligible_records": created}

    records.sort(key=ts_key)
    return records, {
        "source_manifest_count": len(source_manifest),
        "event_files": len(event_map),
        "lineup_files": len(lineup_map),
        "event_errors": len(event_errors),
        "lineup_errors": len(lineup_errors),
        "domain_stats": domain_stats,
        "same_match_result_before_feature": same_match_result_before_feature,
        "same_match_event_before_feature": same_match_event_before_feature,
    }


def fit_predict(train: list[dict], test: list[dict], key: str):
    xtr = np.asarray([r[key] for r in train], dtype=float)
    ytr = np.asarray([r["y"] for r in train], dtype=int)
    xte = np.asarray([r[key] for r in test], dtype=float)
    if set(ytr.tolist()) != {0, 1, 2}:
        raise RuntimeError("training set missing 1X2 class")
    model = make_pipeline(StandardScaler(), LogisticRegression(C=C_FIXED, solver="lbfgs", max_iter=2000))
    model.fit(xtr, ytr)
    p0 = model.predict_proba(xte)
    classes = model[-1].classes_.tolist()
    p = np.zeros((len(test), 3), dtype=float)
    for j, cls in enumerate(classes):
        p[:, int(cls)] = p0[:, j]
    return p


def metrics(rows: list[dict], p: np.ndarray) -> dict:
    y = np.asarray([r["y"] for r in rows], dtype=int)
    eps = 1e-15
    pp = np.clip(p, eps, 1.0)
    pp /= pp.sum(axis=1, keepdims=True)
    picks = np.argmax(pp, axis=1)
    hits = int(np.sum(picks == y))
    one = np.eye(3)[y]
    ll = float(-np.mean(np.log(pp[np.arange(len(y)), y])))
    br = float(np.mean(np.sum((pp - one) ** 2, axis=1)))
    cp = np.cumsum(pp[:, :2], axis=1); cy = np.cumsum(one[:, :2], axis=1)
    rps = float(np.mean(np.sum((cp - cy) ** 2, axis=1) / 2.0))
    yd = (y == 1).astype(float); pd = pp[:, 1]
    dll = float(-np.mean(yd * np.log(np.clip(pd, eps, 1.0)) + (1 - yd) * np.log(np.clip(1 - pd, eps, 1.0))))
    dbr = float(np.mean((pd - yd) ** 2))
    return {
        "count": len(rows), "hits": hits, "top1_accuracy": float(hits / len(rows)),
        "logloss": ll, "brier": br, "rps": rps,
        "draw_binary": {"logloss": dll, "brier": dbr},
        "top1_picks": {"home": int(np.sum(picks == 0)), "draw": int(np.sum(picks == 1)), "away": int(np.sum(picks == 2))},
        "top1_hits": {"home": int(np.sum((picks == 0) & (y == 0))), "draw": int(np.sum((picks == 1) & (y == 1))), "away": int(np.sum((picks == 2) & (y == 2)))},
        "actuals": {"home": int(np.sum(y == 0)), "draw": int(np.sum(y == 1)), "away": int(np.sum(y == 2))},
    }


def delta(base: dict, cand: dict) -> dict:
    return {
        "hits": int(cand["hits"] - base["hits"]),
        "accuracy_pp": float(100.0 * (cand["top1_accuracy"] - base["top1_accuracy"])),
        "logloss": float(cand["logloss"] - base["logloss"]),
        "brier": float(cand["brier"] - base["brier"]),
        "rps": float(cand["rps"] - base["rps"]),
        "draw_binary_logloss": float(cand["draw_binary"]["logloss"] - base["draw_binary"]["logloss"]),
        "draw_binary_brier": float(cand["draw_binary"]["brier"] - base["draw_binary"]["brier"]),
    }


def bootstrap_ll(rows: list[dict], bp: np.ndarray, cp: np.ndarray) -> dict:
    y = np.asarray([r["y"] for r in rows], dtype=int)
    eps = 1e-15
    bl = -np.log(np.clip(bp[np.arange(len(y)), y], eps, 1.0))
    cl = -np.log(np.clip(cp[np.arange(len(y)), y], eps, 1.0))
    d = cl - bl
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    vals = np.empty(BOOTSTRAP_N, dtype=float)
    n = len(d)
    for i in range(BOOTSTRAP_N):
        ix = rng.integers(0, n, size=n)
        vals[i] = float(np.mean(d[ix]))
    lo, hi = np.quantile(vals, [0.05, 0.95])
    return {"replicates": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED, "mean_delta_logloss": float(np.mean(d)), "p05": float(lo), "p95": float(hi)}


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, source = load_rows()
    if len(rows) < 700:
        raise RuntimeError(f"eligible cohort unexpectedly small: {len(rows)}")

    seed_end = boundary_at_least(rows, TRAIN_SEED_TARGET)
    if seed_end >= len(rows) - 300:
        raise RuntimeError("training seed consumed too much cohort")
    remaining = len(rows) - seed_end
    b1 = boundary_at_least(rows, seed_end + math.ceil(remaining / 3))
    rem2 = len(rows) - b1
    b2 = boundary_at_least(rows, b1 + math.ceil(rem2 / 2))
    bounds = [(seed_end, b1), (b1, b2), (b2, len(rows))]

    seed_y = {r["y"] for r in rows[:seed_end]}
    if seed_y != {0, 1, 2}:
        raise RuntimeError("fixed training seed missing a 1X2 class")

    scored_rows = []
    base_parts = []
    cand_parts = []
    fold_results = []
    for i, (start, end) in enumerate(bounds, 1):
        train = rows[:start]
        test = rows[start:end]
        if not test:
            raise RuntimeError(f"empty test fold {i}")
        if group_key(train[-1]) >= group_key(test[0]):
            raise RuntimeError(f"timestamp leakage at fold {i}")
        bp = fit_predict(train, test, "base")
        cp = fit_predict(train, test, "candidate")
        bm, cm = metrics(test, bp), metrics(test, cp)
        fold_results.append({
            "fold": i,
            "train_n": len(train), "test_n": len(test),
            "train_dates": [train[0]["match_date"], train[-1]["match_date"]],
            "test_dates": [test[0]["match_date"], test[-1]["match_date"]],
            "baseline": bm, "candidate": cm, "delta": delta(bm, cm),
        })
        scored_rows.extend(test); base_parts.append(bp); cand_parts.append(cp)

    base_p = np.vstack(base_parts); cand_p = np.vstack(cand_parts)
    bm, cm = metrics(scored_rows, base_p), metrics(scored_rows, cand_p)
    agg_delta = delta(bm, cm)

    domain_results = {}
    domain_positive_ll = 0
    offset = 0
    indexes = defaultdict(list)
    for idx, r in enumerate(scored_rows):
        indexes[r["domain"]].append(idx)
    for name, ix in indexes.items():
        subrows = [scored_rows[j] for j in ix]
        bpm = metrics(subrows, base_p[ix]); cpm = metrics(subrows, cand_p[ix])
        dd = delta(bpm, cpm)
        if dd["logloss"] < 0:
            domain_positive_ll += 1
        domain_results[name] = {"baseline": bpm, "candidate": cpm, "delta": dd}

    fold_positive_ll = sum(fr["delta"]["logloss"] < 0 for fr in fold_results)
    boot = bootstrap_ll(scored_rows, base_p, cand_p)
    gate = bool(
        agg_delta["hits"] >= 0
        and agg_delta["logloss"] < 0
        and agg_delta["brier"] < 0
        and agg_delta["rps"] < 0
        and agg_delta["draw_binary_logloss"] <= 0
        and agg_delta["draw_binary_brier"] <= 0
        and fold_positive_ll >= 2
        and domain_positive_ll >= 2
        and boot["p95"] < 0
    )
    breakthrough = bool(gate and agg_delta["accuracy_pp"] >= BREAKTHROUGH_TOP1_PP)
    if breakthrough:
        action = "FREEZE_SETPIECE_1X2_ARCHITECTURE_FOR_FORWARD_CONFIRMATION"
    elif gate:
        action = "KEEP_SETPIECE_1X2_AS_DEVELOPMENT_ONLY"
    else:
        action = "DO_NOT_PROMOTE_AND_CLOSE_SETPIECE_ROLE_FAMILY"

    result = {
        "schema_version": "football3-r43p0-setpiece-role-1x2-postview-v1",
        "status": "COMPLETE",
        "classification": "POSTVIEW_ARCHITECTURE_DEVELOPMENT_ONLY",
        "formal_weight": 0,
        "question": "Does strictly prior set-piece taker/retention state add direct multiclass 1X2 information beyond prior-result team state on the already-consumed R44L7B domains?",
        "governance": {
            "source_r44l7b_head": SOURCE_R44L7B_HEAD,
            "source_r44l7c_failure_head": SOURCE_R44L7C_FAILURE_HEAD,
            "source_pin": SOURCE_PIN,
            "labels_already_consumed_by_r44l7c": True,
            "postview_design_repair": True,
            "formal_or_independent_claim": False,
            "parameter_search": False,
            "feature_search": False,
            "hyperparameter_search": False,
            "threshold_search": False,
            "class_weight": None,
            "unified_three_class_argmax": True,
            "same_match_result_used_before_feature": False,
            "same_match_event_used_before_feature": False,
            "odds_used": False,
            "current_formal_model_modified": False,
        },
        "design": {
            "domains": DOMAINS,
            "warmup_per_domain": WARMUP_PER_DOMAIN,
            "result_window": RESULT_WINDOW,
            "setpiece_window": SETPIECE_WINDOW,
            "training_seed_target": TRAIN_SEED_TARGET,
            "training_seed_actual": seed_end,
            "oos_folds": N_FOLDS,
            "fold_rule": "global chronological grouped by match_date+kickoff; fixed seed then expanding prior-only fit",
            "baseline_features": BASE_NAMES,
            "candidate_additions": SP_NAMES,
            "estimator": {"family": "standardized multinomial logistic regression", "C": C_FIXED, "solver": "lbfgs", "max_iter": 2000},
            "breakthrough_threshold_top1_pp": BREAKTHROUGH_TOP1_PP,
        },
        "source": source,
        "cohort": {
            "eligible_n": len(rows),
            "seed_n": seed_end,
            "scored_n": len(scored_rows),
            "first_date": rows[0]["match_date"], "last_date": rows[-1]["match_date"],
            "seed_last_date": rows[seed_end - 1]["match_date"],
        },
        "folds": fold_results,
        "domains": domain_results,
        "aggregate": {"baseline": bm, "candidate": cm, "delta": agg_delta, "folds_positive_logloss": fold_positive_ll, "domains_positive_logloss": domain_positive_ll, "bootstrap_logloss_delta": boot},
        "gate": {"passed": gate, "breakthrough_candidate": breakthrough, "action": action},
        "limitations": [
            "All three scored domains had labels consumed by the failed R44L7C attempt before this design repair, so this evidence is explicitly post-view and formal_weight=0.",
            "The baseline is a compact prior-result state model, not R42L and not a market baseline; a positive result would only justify forward confirmation, never direct promotion.",
            "The set-piece event definition is limited to StatsBomb Pass events typed Corner or Free Kick and measures taker-role continuity rather than set-piece xG quality.",
        ],
        "run_identity": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()},
    }
    path = OUT / "summary_r43p0_setpiece_role_1x2_postview.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    d = json.loads((OUT / "summary_r43p0_setpiece_role_1x2_postview.json").read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0
    assert d["governance"]["labels_already_consumed_by_r44l7c"] is True
    assert d["governance"]["parameter_search"] is False and d["governance"]["feature_search"] is False
    assert d["governance"]["unified_three_class_argmax"] is True
    assert d["governance"]["same_match_result_used_before_feature"] is False
    assert d["governance"]["same_match_event_used_before_feature"] is False
    assert d["design"]["training_seed_target"] == TRAIN_SEED_TARGET
    assert len(d["folds"]) == N_FOLDS
    assert d["cohort"]["scored_n"] >= 500
    print("R43P0 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
