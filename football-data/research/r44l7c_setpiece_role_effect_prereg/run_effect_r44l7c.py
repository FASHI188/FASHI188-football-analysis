#!/usr/bin/env python3
import hashlib
import json
import math
import os
import statistics
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

EXPECTED_CONTRACT_SHA256 = "74fc844324569dff8d4d6c836ded9872498fa378fd57f88f19b188b64a9979c2"
EXPECTED_SKLEARN_VERSION = "1.9.0"
SOURCE_REPO = "hudl/open-data"
SOURCE_PIN = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
RAW_ROOT = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_PIN}"
DOMAINS = [
    {"competition_id": 2, "season_id": 27, "name": "Premier League"},
    {"competition_id": 12, "season_id": 27, "name": "Serie A"},
    {"competition_id": 7, "season_id": 27, "name": "Ligue 1"},
]
PERMANENT_EXCLUDED_COMPETITION = 11
WARMUP = 100
PRIOR10 = 10
PRIOR5 = 5
C = 1.0
MAX_ITER = 2000
BOOTSTRAP_REPS = 10000
BOOTSTRAP_SEED = 20260813
EPS = 1e-12

BASELINE_NAMES = [
    "home_goals_for_per_match", "home_goals_against_per_match", "home_points_per_match",
    "home_goal_difference_per_match", "home_draw_rate",
    "away_goals_for_per_match", "away_goals_against_per_match", "away_points_per_match",
    "away_goal_difference_per_match", "away_draw_rate",
    "home_side_indicator",
    "competition_is_premier_league", "competition_is_serie_a", "competition_is_ligue_1",
]
CANDIDATE_ADD_NAMES = [
    "home_prior5_setpiece_passes", "home_prior5_unique_takers", "home_prior5_top1_share",
    "home_prior5_top1_taker_in_current_xi", "home_prior5_xi_role_retention",
    "away_prior5_setpiece_passes", "away_prior5_unique_takers", "away_prior5_top1_share",
    "away_prior5_top1_taker_in_current_xi", "away_prior5_xi_role_retention",
]


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch_bytes(path, attempts=4):
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                f"{RAW_ROOT}/{path}", headers={"User-Agent": "r44l7c-effect/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise last


def fetch_json(path):
    raw = fetch_bytes(path)
    return json.loads(raw), sha256_bytes(raw)


def verify_contract(root):
    p = root / "football-data/research/r44l7c_setpiece_role_effect_prereg/contract_r44l7c.json"
    raw = p.read_bytes()
    got = sha256_bytes(raw)
    if got != EXPECTED_CONTRACT_SHA256:
        raise SystemExit(f"BLOCKED_PREREG_HASH_MISMATCH got={got}")
    c = json.loads(raw)
    assert c["schema_version"] == "V520-R44L7C-SETPIECE-EFFECT-PREREG-1.0"
    assert c["parent_evidence"]["source_pin"] == SOURCE_PIN
    assert [d["competition_id"] for d in c["domains"]] == [2, 12, 7]
    assert c["permanent_exclusions"][0]["competition_id"] == PERMANENT_EXCLUDED_COMPETITION
    assert c["estimator"]["regularization_C"] == C
    assert c["estimator"]["solver"] == "lbfgs"
    assert c["estimator"]["max_iter"] == MAX_ITER
    assert c["chronological_evaluation"]["warmup_match_count_per_domain"] == WARMUP
    assert c["chronological_evaluation"]["formal_post_warmup_match_capacity"] == 837
    assert c["metrics"]["bootstrap"]["replicates"] == BOOTSTRAP_REPS
    assert c["metrics"]["bootstrap"]["seed"] == BOOTSTRAP_SEED
    return c, got


def load_matches(domain, manifest):
    path = f"data/matches/{domain['competition_id']}/{domain['season_id']}.json"
    obj, sha = fetch_json(path)
    manifest.append({"path": path, "sha256": sha})
    rows = []
    for m in obj:
        if int(m["competition"]["competition_id"]) != domain["competition_id"]:
            raise RuntimeError("competition_identity_mismatch")
        if int(m["season"]["season_id"]) != domain["season_id"]:
            raise RuntimeError("season_identity_mismatch")
        # Label access is explicit and authorized for this one frozen execution.
        rows.append({
            "domain": domain["name"],
            "competition_id": domain["competition_id"],
            "match_id": int(m["match_id"]),
            "match_date": str(m["match_date"]),
            "kick_off": str(m.get("kick_off") or ""),
            "home_team_id": int(m["home_team"]["home_team_id"]),
            "away_team_id": int(m["away_team"]["away_team_id"]),
            "home_score": int(m["home_score"]),
            "away_score": int(m["away_score"]),
        })
    rows.sort(key=lambda x: (x["match_date"], x["kick_off"], x["match_id"]))
    return rows


def lineup_starters(mid, manifest):
    path = f"data/lineups/{mid}.json"
    obj, sha = fetch_json(path)
    manifest.append({"path": path, "sha256": sha})
    teams = {}
    for t in obj:
        starters = []
        for p in t.get("lineup", []):
            positions = p.get("positions") or []
            if any(pos.get("from") == "00:00" and pos.get("start_reason") == "Starting XI" for pos in positions):
                starters.append(int(p["player_id"]))
        teams[int(t["team_id"])] = starters
    return teams


def current_setpiece_counts(mid, manifest):
    path = f"data/events/{mid}.json"
    obj, sha = fetch_json(path)
    manifest.append({"path": path, "sha256": sha})
    out = defaultdict(Counter)
    for e in obj:
        if e.get("type", {}).get("name") != "Pass":
            continue
        ptype = (e.get("pass") or {}).get("type", {}).get("name")
        if ptype not in {"Corner", "Free Kick"}:
            continue
        tid = (e.get("team") or {}).get("id")
        pid = (e.get("player") or {}).get("id")
        if tid is not None and pid is not None:
            out[int(tid)][int(pid)] += 1
    return out


def prior10_summary(hist):
    if len(hist) != PRIOR10:
        return None
    gf = sum(x["gf"] for x in hist) / PRIOR10
    ga = sum(x["ga"] for x in hist) / PRIOR10
    pts = sum(x["pts"] for x in hist) / PRIOR10
    gd = sum(x["gf"] - x["ga"] for x in hist) / PRIOR10
    dr = sum(1 for x in hist if x["gf"] == x["ga"]) / PRIOR10
    return [gf, ga, pts, gd, dr]


def prior5_role(hist, current_xi):
    if len(hist) != PRIOR5 or len(current_xi) == 0:
        return None
    total = Counter()
    for c in hist:
        total.update(c)
    nsp = sum(total.values())
    if nsp <= 0:
        return None
    top_pid, top_n = total.most_common(1)[0]
    xi = set(current_xi)
    return [
        float(nsp),
        float(len(total)),
        float(top_n / nsp),
        float(top_pid in xi),
        float(sum(v for pid, v in total.items() if pid in xi) / nsp),
    ]


def point(team_gf, team_ga):
    if team_gf > team_ga:
        return 3
    if team_gf == team_ga:
        return 1
    return 0


def label_for(hs, aas):
    if hs == aas:
        return 1
    if abs(hs - aas) == 1:
        return 0
    return None


def comp_dummies(name):
    return [float(name == "Premier League"), float(name == "Serie A"), float(name == "Ligue 1")]


def build_rows(all_domain_rows, manifest):
    feature_rows = []
    formal_identity_rows = []
    same_match_event_used_before_feature = 0
    target_event_files_accessed_after_feature = 0

    for domain_name, rows in all_domain_rows.items():
        result_hist = defaultdict(lambda: deque(maxlen=PRIOR10))
        sp_hist = defaultdict(lambda: deque(maxlen=PRIOR5))
        for idx, m in enumerate(rows):
            ht, at = m["home_team_id"], m["away_team_id"]
            timestamp = (m["match_date"], m["kick_off"])
            if idx >= WARMUP:
                formal_identity_rows.append({
                    "domain": domain_name,
                    "match_id": m["match_id"],
                    "match_date": m["match_date"],
                    "kick_off": m["kick_off"],
                    "timestamp": timestamp,
                })

            hbase = prior10_summary(result_hist[ht])
            abase = prior10_summary(result_hist[at])
            can_attempt_features = hbase is not None and abase is not None and len(sp_hist[ht]) == PRIOR5 and len(sp_hist[at]) == PRIOR5
            row = None
            if can_attempt_features:
                try:
                    lineups = lineup_starters(m["match_id"], manifest)
                except Exception:
                    lineups = {}
                hxi = lineups.get(ht, [])
                axi = lineups.get(at, [])
                hrole = prior5_role(sp_hist[ht], hxi)
                arole = prior5_role(sp_hist[at], axi)
                if hrole is not None and arole is not None:
                    baseline = hbase + abase + [1.0] + comp_dummies(domain_name)
                    candidate = baseline + hrole + arole
                    row = {
                        "domain": domain_name,
                        "competition_id": m["competition_id"],
                        "match_id": m["match_id"],
                        "match_date": m["match_date"],
                        "kick_off": m["kick_off"],
                        "timestamp": timestamp,
                        "domain_index": idx,
                        "is_formal": idx >= WARMUP,
                        "home_xi_n": len(hxi),
                        "away_xi_n": len(axi),
                        "baseline": baseline,
                        "candidate": candidate,
                        "home_score": m["home_score"],
                        "away_score": m["away_score"],
                        "y": label_for(m["home_score"], m["away_score"]),
                    }
                    feature_rows.append(row)

            # Strict chronology: only after the current feature row is sealed do current events enter future prior5.
            sp_now = current_setpiece_counts(m["match_id"], manifest)
            if idx >= WARMUP:
                target_event_files_accessed_after_feature += 1
            sp_hist[ht].append(Counter(sp_now.get(ht, {})))
            sp_hist[at].append(Counter(sp_now.get(at, {})))

            # Only after feature sealing do current result labels enter future prior10.
            hs, aas = m["home_score"], m["away_score"]
            result_hist[ht].append({"gf": hs, "ga": aas, "pts": point(hs, aas)})
            result_hist[at].append({"gf": aas, "ga": hs, "pts": point(aas, hs)})

    return feature_rows, formal_identity_rows, same_match_event_used_before_feature, target_event_files_accessed_after_feature


def build_folds(formal_identity_rows):
    rows = sorted(formal_identity_rows, key=lambda x: (x["match_date"], x["kick_off"], x["match_id"], x["domain"]))
    groups = []
    for r in rows:
        key = (r["match_date"], r["kick_off"])
        if not groups or groups[-1][0] != key:
            groups.append([key, []])
        groups[-1][1].append(r)
    n = len(rows)
    targets = [n / 3.0, 2.0 * n / 3.0]
    cuts = []
    cumulative = 0
    for key, g in groups:
        cumulative += len(g)
        if len(cuts) < 2 and cumulative >= targets[len(cuts)]:
            cuts.append(key)
    if len(cuts) != 2:
        raise RuntimeError("fold_cut_failure")
    # cuts are first timestamps in the tail after reaching thresholds; use group order indices instead.
    fold_map = {}
    cumulative = 0
    cut_group_indices = []
    next_target = 0
    for gi, (key, g) in enumerate(groups):
        cumulative += len(g)
        if next_target < 2 and cumulative >= targets[next_target]:
            cut_group_indices.append(gi + 1)
            next_target += 1
    if len(cut_group_indices) != 2 or cut_group_indices[0] <= 0 or cut_group_indices[1] <= cut_group_indices[0]:
        raise RuntimeError("invalid_fold_boundaries")
    for gi, (key, g) in enumerate(groups):
        fold = 0 if gi < cut_group_indices[0] else (1 if gi < cut_group_indices[1] else 2)
        for r in g:
            fold_map[(r["domain"], r["match_id"])] = fold
    starts = []
    for fold in range(3):
        fr = [r for r in rows if fold_map[(r["domain"], r["match_id"])] == fold]
        starts.append(min((r["match_date"], r["kick_off"]) for r in fr))
    return fold_map, starts, [sum(1 for r in rows if fold_map[(r["domain"], r["match_id"])] == f) for f in range(3)]


def sample_gate(formal_rows, fold_map, contract):
    eligible = [r for r in formal_rows if r["y"] is not None]
    draw_count = sum(r["y"] for r in eligible)
    domain_counts = {}
    for d in [x["name"] for x in DOMAINS]:
        dr = [r for r in eligible if r["domain"] == d]
        domain_counts[d] = {"binary": len(dr), "draw": sum(r["y"] for r in dr)}
    fold_counts = {}
    for f in range(3):
        fr = [r for r in eligible if fold_map[(r["domain"], r["match_id"])] == f]
        fold_counts[str(f + 1)] = {"binary": len(fr), "draw": sum(r["y"] for r in fr)}
    g = contract["pre_fit_sample_gate_after_label_open"]
    gates = {
        "total_primary_binary_ge_450": len(eligible) >= g["total_primary_binary_min"],
        "draw_count_ge_120": draw_count >= g["draw_count_min"],
        "each_domain_primary_binary_ge_120": all(v["binary"] >= g["each_domain_primary_binary_min"] for v in domain_counts.values()),
        "each_domain_draw_ge_30": all(v["draw"] >= g["each_domain_draw_min"] for v in domain_counts.values()),
        "each_oos_fold_primary_binary_ge_120": all(v["binary"] >= g["each_oos_fold_primary_binary_min"] for v in fold_counts.values()),
    }
    return eligible, draw_count, domain_counts, fold_counts, gates


def make_model():
    return Pipeline([
        ("scale", StandardScaler()),
        ("logit", LogisticRegression(
            penalty="l2", C=C, solver="lbfgs", max_iter=MAX_ITER,
            class_weight=None, random_state=None
        )),
    ])


def obs_logloss(y, p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1.0 - EPS)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))


def calibration(y, p):
    p = np.clip(np.asarray(p, dtype=float), 1e-8, 1.0 - 1e-8)
    x = np.log(p / (1.0 - p)).reshape(-1, 1)
    m = LogisticRegression(penalty=None, solver="lbfgs", max_iter=MAX_ITER)
    m.fit(x, np.asarray(y, dtype=int))
    return {"intercept": float(m.intercept_[0]), "slope": float(m.coef_[0][0])}


def metric_block(y, pb, pc):
    y = np.asarray(y, dtype=int)
    pb = np.clip(np.asarray(pb, dtype=float), EPS, 1 - EPS)
    pc = np.clip(np.asarray(pc, dtype=float), EPS, 1 - EPS)
    b = {
        "n": int(len(y)),
        "draw": int(y.sum()),
        "baseline_logloss": float(log_loss(y, pb, labels=[0, 1])),
        "candidate_logloss": float(log_loss(y, pc, labels=[0, 1])),
        "baseline_brier": float(brier_score_loss(y, pb)),
        "candidate_brier": float(brier_score_loss(y, pc)),
        "baseline_auc": float(roc_auc_score(y, pb)),
        "candidate_auc": float(roc_auc_score(y, pc)),
    }
    b["delta_logloss_candidate_minus_baseline"] = b["candidate_logloss"] - b["baseline_logloss"]
    b["delta_brier_candidate_minus_baseline"] = b["candidate_brier"] - b["baseline_brier"]
    b["delta_auc_candidate_minus_baseline"] = b["candidate_auc"] - b["baseline_auc"]
    return b


def run_oos(feature_rows, fold_map, fold_starts):
    predictions = []
    fit_audit = []
    predictive_model_fits = 0
    for f in range(3):
        fold_start = fold_starts[f]
        train = [
            r for r in feature_rows
            if r["y"] is not None and (r["match_date"], r["kick_off"]) < fold_start
        ]
        test = [
            r for r in feature_rows
            if r["is_formal"] and r["y"] is not None and fold_map.get((r["domain"], r["match_id"])) == f
        ]
        if not train or not test:
            raise RuntimeError(f"empty_train_or_test_fold_{f+1}")
        train_ids = {(r["domain"], r["match_id"]) for r in train}
        test_ids = {(r["domain"], r["match_id"]) for r in test}
        if train_ids & test_ids:
            raise RuntimeError("train_test_overlap")
        if max((r["match_date"], r["kick_off"]) for r in train) >= fold_start:
            raise RuntimeError("train_time_not_strictly_before_fold")
        ytr = np.asarray([r["y"] for r in train], dtype=int)
        yte = np.asarray([r["y"] for r in test], dtype=int)
        xbtr = np.asarray([r["baseline"] for r in train], dtype=float)
        xctr = np.asarray([r["candidate"] for r in train], dtype=float)
        xbte = np.asarray([r["baseline"] for r in test], dtype=float)
        xcte = np.asarray([r["candidate"] for r in test], dtype=float)
        bm = make_model(); cm = make_model()
        bm.fit(xbtr, ytr); predictive_model_fits += 1
        cm.fit(xctr, ytr); predictive_model_fits += 1
        pb = bm.predict_proba(xbte)[:, 1]
        pc = cm.predict_proba(xcte)[:, 1]
        fit_audit.append({
            "fold": f + 1, "fold_start": list(fold_start), "train_n": len(train), "train_draw": int(ytr.sum()),
            "test_n": len(test), "test_draw": int(yte.sum()),
            "train_max_timestamp": list(max((r["match_date"], r["kick_off"]) for r in train)),
            "baseline_n_features": int(xbtr.shape[1]), "candidate_n_features": int(xctr.shape[1]),
        })
        for r, y, b, c in zip(test, yte, pb, pc):
            predictions.append({
                "fold": f + 1, "domain": r["domain"], "match_id": r["match_id"],
                "match_date": r["match_date"], "kick_off": r["kick_off"],
                "y": int(y), "baseline_p_draw": float(b), "candidate_p_draw": float(c),
            })
    return predictions, fit_audit, predictive_model_fits


def bootstrap_delta(predictions):
    y = np.asarray([r["y"] for r in predictions], dtype=int)
    pb = np.asarray([r["baseline_p_draw"] for r in predictions], dtype=float)
    pc = np.asarray([r["candidate_p_draw"] for r in predictions], dtype=float)
    diff = obs_logloss(y, pc) - obs_logloss(y, pb)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(diff)
    vals = np.empty(BOOTSTRAP_REPS, dtype=float)
    for i in range(BOOTSTRAP_REPS):
        idx = rng.integers(0, n, size=n)
        vals[i] = float(diff[idx].mean())
    lo, hi = np.percentile(vals, [5, 95])
    return {"replicates": BOOTSTRAP_REPS, "seed": BOOTSTRAP_SEED, "lower_5pct": float(lo), "upper_95pct": float(hi), "bootstrap_mean": float(vals.mean())}


def main():
    repo_root = Path(__file__).resolve().parents[4]
    contract, contract_sha = verify_contract(repo_root)
    if sklearn.__version__ != EXPECTED_SKLEARN_VERSION:
        raise SystemExit(f"BLOCKED_SKLEARN_VERSION got={sklearn.__version__} expected={EXPECTED_SKLEARN_VERSION}")
    out = Path(os.environ.get("R44L7C_OUTPUT", "r44l7c_output"))
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    all_domain_rows = {}
    for d in DOMAINS:
        all_domain_rows[d["name"]] = load_matches(d, manifest)
    if [len(all_domain_rows[d["name"]]) for d in DOMAINS] != [380, 380, 377]:
        raise SystemExit("BLOCKED_IDENTITY_CAPACITY_DRIFT")

    feature_rows, formal_identity, same_match_event_used_before_feature, target_event_after_feature = build_rows(all_domain_rows, manifest)
    if len(formal_identity) != 837:
        raise SystemExit(f"BLOCKED_FORMAL_CAPACITY_DRIFT n={len(formal_identity)}")
    fold_map, fold_starts, fold_identity_counts = build_folds(formal_identity)
    formal_feature_rows = [r for r in feature_rows if r["is_formal"]]
    formal_feature_ids = {(r["domain"], r["match_id"]) for r in formal_feature_rows}
    formal_identity_ids = {(r["domain"], r["match_id"]) for r in formal_identity}
    coverage = {
        "formal_identity": len(formal_identity_ids),
        "formal_features_complete": len(formal_feature_ids),
        "formal_features_missing": len(formal_identity_ids - formal_feature_ids),
        "formal_exact_11v11": sum(1 for r in formal_feature_rows if r["home_xi_n"] == 11 and r["away_xi_n"] == 11),
        "same_match_event_used_before_feature": same_match_event_used_before_feature,
        "target_event_files_accessed_after_feature": target_event_after_feature,
        "fold_identity_counts": fold_identity_counts,
    }
    if same_match_event_used_before_feature != 0:
        raise SystemExit("BLOCKED_SAME_MATCH_EVENT_LEAKAGE")
    if len(formal_feature_rows) < 830:
        raise SystemExit("BLOCKED_FORMAL_FEATURE_COVERAGE_BELOW_FROZEN_UPSTREAM_GATE")

    eligible, draw_count, domain_counts, fold_counts, sample_gates = sample_gate(formal_feature_rows, fold_map, contract)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    base_receipt = {
        "schema_version": "V520-R44L7C-EFFECT-EXECUTION-1.0",
        "research_only": True,
        "formal_weight": 0,
        "contract_sha256": contract_sha,
        "source_repo": SOURCE_REPO,
        "source_pin": SOURCE_PIN,
        "sklearn_version": sklearn.__version__,
        "label_access_receipt": {
            "formal_target_match_labels_opened": 837,
            "all_three_domain_match_labels_opened_including_warmup": 1137,
            "la_liga_2015_16_labels_opened": 0,
            "sample_selection_from_labels_before_prereg": 0,
        },
        "coverage": coverage,
        "sample_gate": {
            "primary_binary_total": len(eligible),
            "draw_count": draw_count,
            "domain_counts": domain_counts,
            "fold_counts": fold_counts,
            "gates": sample_gates,
        },
        "run_identity": {
            "checked_out_head_sha": head,
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        },
        "formal_changes": {"model": 0, "data": 0, "config": 0, "CURRENT": 0},
    }

    if not all(sample_gates.values()):
        base_receipt.update({
            "terminal": contract["pre_fit_sample_gate_after_label_open"]["failure_terminal"],
            "predictive_model_fits": 0,
            "candidate_probabilities": 0,
            "scientific_effect": "UNKNOWN_UNDERPOWERED_NO_FIT",
        })
        predictions = []
    else:
        predictions, fit_audit, predictive_model_fits = run_oos(feature_rows, fold_map, fold_starts)
        pooled_y = [r["y"] for r in predictions]
        pooled_b = [r["baseline_p_draw"] for r in predictions]
        pooled_c = [r["candidate_p_draw"] for r in predictions]
        pooled = metric_block(pooled_y, pooled_b, pooled_c)
        pooled["baseline_calibration"] = calibration(pooled_y, pooled_b)
        pooled["candidate_calibration"] = calibration(pooled_y, pooled_c)
        fold_metrics = {}
        for f in range(1, 4):
            rr = [r for r in predictions if r["fold"] == f]
            fold_metrics[str(f)] = metric_block([r["y"] for r in rr], [r["baseline_p_draw"] for r in rr], [r["candidate_p_draw"] for r in rr])
        domain_metrics = {}
        for d in [x["name"] for x in DOMAINS]:
            rr = [r for r in predictions if r["domain"] == d]
            domain_metrics[d] = metric_block([r["y"] for r in rr], [r["baseline_p_draw"] for r in rr], [r["candidate_p_draw"] for r in rr])
        boot = bootstrap_delta(predictions)
        science_gates = {
            "pooled_delta_logloss_lt_0": pooled["delta_logloss_candidate_minus_baseline"] < 0,
            "bootstrap_90pct_upper_delta_logloss_lt_0": boot["upper_95pct"] < 0,
            "pooled_delta_brier_lt_0": pooled["delta_brier_candidate_minus_baseline"] < 0,
            "chronological_folds_improved_ge_2": sum(v["delta_logloss_candidate_minus_baseline"] < 0 for v in fold_metrics.values()) >= 2,
            "domains_improved_ge_2": sum(v["delta_logloss_candidate_minus_baseline"] < 0 for v in domain_metrics.values()) >= 2,
        }
        terminal = contract["scientific_pass_gate"]["pass_terminal"] if all(science_gates.values()) else contract["scientific_pass_gate"]["fail_terminal"]
        base_receipt.update({
            "terminal": terminal,
            "predictive_model_fits": predictive_model_fits,
            "calibration_auxiliary_fits": 2,
            "candidate_probabilities": len(predictions),
            "fit_audit": fit_audit,
            "pooled": pooled,
            "fold_metrics": fold_metrics,
            "domain_metrics": domain_metrics,
            "bootstrap_delta_logloss": boot,
            "scientific_gates": science_gates,
        })

    # Compact audit files; labels are research artifacts only and never enter formal data.
    source_dedup = {(x["path"], x["sha256"]): x for x in manifest}
    (out / "source_manifest.json").write_text(json.dumps(list(source_dedup.values()), indent=2), encoding="utf-8")
    (out / "predictions.json").write_text(json.dumps(predictions, indent=2), encoding="utf-8")
    raw = json.dumps(base_receipt, indent=2, sort_keys=True).encode("utf-8")
    (out / "receipt.json").write_bytes(raw)
    (out / "receipt.sha256").write_text(sha256_bytes(raw) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": base_receipt["terminal"],
        "sample_gate": base_receipt["sample_gate"],
        "pooled": base_receipt.get("pooled"),
        "bootstrap_delta_logloss": base_receipt.get("bootstrap_delta_logloss"),
        "scientific_gates": base_receipt.get("scientific_gates"),
        "predictive_model_fits": base_receipt.get("predictive_model_fits"),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
