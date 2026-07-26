#!/usr/bin/env python3
"""V6.37.0 Gold500 market-offset player-residual 1X2 challenge.

Research question
-----------------
Can genuinely player-level pre-match information improve the strong market 1X2
baseline, if we stop asking another classifier to relearn the whole match and
instead learn only a regularized residual correction around market probabilities?

Discipline
----------
- historical model/regularization selection uses 2022/23->2023/24 and
  2022/23+2023/24->2024/25 only;
- the fixed Gold500 A_FAST100 is touched once after selection;
- B_CONFIRM300 is not read or scored by this script;
- C_SEALED100 remains sealed;
- no confidence filtering, league dropping, seed replacement, or A100 tuning;
- retrospective closing odds are development research only; formal_weight=0.

Architecture
------------
1. Build the same 53-feature base rows and strict-PIT V6.33 player-core rows.
2. Cross-provider team identities are matched result-blind by schedule fingerprints.
3. Regress the 18 player-core features on market geometry + league on TRAIN only.
   Only residual player information is fed to the correction layer.
4. Fit a multinomial ridge layer with market log-probabilities as fixed offsets:
       softmax(log p_market + W z_residual)
   This means the model must earn every deviation from the market.
5. Three small, predeclared residual feature specifications and a ridge grid are
   selected only on historical rolling validation.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6360 as gold0  # noqa: E402
import build_gold500_feature_library_v6361 as gold1  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402

OUT = ROOT / "manifests" / "v6_market_offset_player_residual_gold500_v6370_status.json"
GOLD_FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
GOLD_LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"

TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
TEST_SEASON = "2025/26"
PART = "A_FAST100"
EPS = 1e-12
RIDGES = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3)
RESID_DIFF_COUNT = 12
FEATURE_SPECS = ("core_signed", "core_balance", "core_interaction")
PROPER_LOG_TOL = 0.01
PROPER_RPS_TOL = 0.01
REQUIRED_UPLIFT_PP = 3.0

PLAYER_NAMES = tuple(gold0.PLAYER_FEATURE_NAMES)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_fast100_labels_only(path: Path) -> dict[int, dict[str, Any]]:
    """Read exactly the first 100 A labels; never parse B labels."""
    out: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for _ in range(100):
            line = handle.readline()
            if not line:
                raise RuntimeError("Gold development label file ended before A_FAST100 completed")
            row = json.loads(line)
            if str(row.get("partition")) != PART:
                raise RuntimeError(f"non-A label encountered inside first 100 rows: {row.get('partition')}")
            idx = int(row["gold_index"])
            out[idx] = row
    if len(out) != 100 or set(out) != set(range(100)):
        raise RuntimeError(f"A_FAST100 label contract changed: count={len(out)} indexes={sorted(out)[:5]}..")
    return out


def entropy(p: list[float] | np.ndarray) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def market_geometry(p: list[float] | np.ndarray, cid: str, leagues: tuple[str, ...]) -> list[float]:
    p = [float(x) for x in p]
    side = math.log(max(EPS, p[0]) / max(EPS, p[2]))
    draw = math.log(max(EPS, p[1]) / max(EPS, 1.0 - p[1]))
    s = sorted(p, reverse=True)
    margin = s[0] - s[1]
    dummies = [1.0 if cid == league else 0.0 for league in leagues[:-1]]
    return [1.0, p[0], p[1], p[2], side, draw, margin, entropy(p), *dummies]


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter()
    actual = Counter()
    for row in rows:
        p = [float(x) for x in row[key]]
        y = int(row["y"])
        if len(p) != 3 or abs(sum(p) - 1.0) > 1e-7 or min(p) < 0.0:
            raise RuntimeError(f"invalid probability vector: {p}")
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        predicted[str(pick)] += 1
        actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    return {
        "count": n,
        "hits": hits,
        "top1": hits / n,
        "brier": brier / n,
        "logloss": logloss / n,
        "rps": rps / n,
        "predicted_counts": dict(predicted),
        "actual_counts": dict(actual),
    }


def build_player_rows_all() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = v633.load_config()
    data_by_comp: dict[str, dict[str, Any]] = {}
    indexes_by_comp: dict[str, dict[str, Any]] = {}
    params_by_comp_season: dict[str, dict[str, dict[str, float]]] = {}
    dynamic_selection: dict[str, dict[str, Any]] = {}
    names_by_comp: dict[str, dict[int, tuple[str, str]]] = {}

    for cid in v633.v6280.COMPS:
        data = v633.load_domain_data(cid, v633.CACHE)
        indexes = v633.build_season_indexes(data)
        artifact = v633.load_json(v633.MODEL_ROOT / cid / "model.json")
        raw_test = artifact["point_in_time_parameters"].get(TEST_SEASON)
        if not raw_test:
            raise v633.PlatformError(f"{cid}: missing {TEST_SEASON} parameters")
        test_params = v633._merge_parameters(config, raw_test)
        selection = v633.v6280.choose_candidate(cid, test_params, data, indexes)
        data_by_comp[cid] = data
        indexes_by_comp[cid] = indexes
        dynamic_selection[cid] = selection["selected"]
        params_by_comp_season[cid] = {}
        for season in TRAIN_SEASONS:
            raw = artifact["point_in_time_parameters"].get(season)
            if raw:
                params_by_comp_season[cid][season] = v633._merge_parameters(config, raw)
        names_by_comp[cid] = gold0.game_name_lookup(cid, v633.CACHE)

    wanted_players = v633._all_player_ids(data_by_comp)
    valuations, valuation_audit = v633._load_valuations(wanted_players)

    rows: list[dict[str, Any]] = []
    by_comp: dict[str, Any] = {}
    missing_names = 0
    for cid in v633.v6280.COMPS:
        counts: dict[str, int] = {}
        for season in TRAIN_SEASONS:
            params = params_by_comp_season[cid].get(season)
            if params is None:
                continue
            rs = v633._season_rows(
                cid,
                season,
                dynamic_selection[cid],
                data_by_comp[cid],
                indexes_by_comp[cid],
                params,
                valuations,
            )
            counts[season] = len(rs)
            for row in rs:
                try:
                    gid = int(str(row["match_key"]).rsplit(":", 1)[-1])
                except (TypeError, ValueError):
                    continue
                names = names_by_comp[cid].get(gid)
                if not names:
                    missing_names += 1
                    continue
                enriched = dict(row)
                enriched["home_name"], enriched["away_name"] = names
                enriched["player_features"] = v633._pair_features(
                    enriched["home_player_context"], enriched["away_player_context"]
                )
                rows.append(enriched)
        by_comp[cid] = {"rows_by_season": counts, "dynamic_candidate": dynamic_selection[cid]["id"]}
    return rows, {
        "rows": len(rows),
        "missing_transfermarkt_game_names": missing_names,
        "valuation_audit": valuation_audit,
        "by_competition": by_comp,
    }


def build_historical_join() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, base_audit, base_feature_names = v632._build_rows()
    base_rows = [dict(r) for r in base_rows if str(r["season"]) in TRAIN_SEASONS]
    player_rows, player_audit = build_player_rows_all()

    joined: list[dict[str, Any]] = []
    crosswalk_summary: dict[str, Any] = {}
    misses = Counter()

    for season in TRAIN_SEASONS:
        bseason = [r for r in base_rows if str(r["season"]) == season]
        pseason = [r for r in player_rows if str(r["season"]) == season]
        mapping, audit = gold1.build_crosswalk(bseason, pseason)
        crosswalk_summary[season] = {
            cid: {
                "mapped_teams": int(meta["mapped_teams"]),
                "tm_teams": int(meta["tm_teams"]),
                "fd_teams": int(meta["fd_teams"]),
                "min_role_overlap": meta["min_role_overlap"],
                "min_schedule_margin": meta["min_schedule_margin"],
            }
            for cid, meta in audit.items()
        }

        pmap: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in pseason:
            cid = str(row["competition_id"])
            mh = mapping.get(cid, {}).get(str(row["home_name"]))
            ma = mapping.get(cid, {}).get(str(row["away_name"]))
            if not mh or not ma:
                misses["unmapped_player_identity"] += 1
                continue
            key = (
                cid,
                str(row["date"]),
                v632._token(cid, mh),
                v632._token(cid, ma),
            )
            if key in pmap:
                misses["duplicate_player_key"] += 1
                continue
            pmap[key] = row

        seen = set()
        for row in bseason:
            cid = str(row["competition_id"])
            key = (
                cid,
                str(row["date"]),
                v632._token(cid, str(row["home_team"])),
                v632._token(cid, str(row["away_team"])),
            )
            if key in seen:
                misses["duplicate_base_key"] += 1
                continue
            seen.add(key)
            player = pmap.get(key)
            if player is None:
                misses["player_join_miss"] += 1
                continue
            if int(row["y"]) != int(player["y"]) or list(row["actual_score"]) != list(player["actual_score"]):
                raise RuntimeError(f"historical label mismatch for {key}")
            pf = [float(x) for x in player["player_features"]]
            if len(pf) != len(PLAYER_NAMES):
                raise RuntimeError(f"player feature length changed for {key}: {len(pf)}")
            joined.append({
                "competition_id": cid,
                "season": season,
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "market": [float(x) for x in row["market"]],
                "player_features": pf,
                "y": int(row["y"]),
                "actual_score": [int(x) for x in row["actual_score"]],
            })

    by_season = Counter(str(r["season"]) for r in joined)
    by_comp = Counter(str(r["competition_id"]) for r in joined)
    if any(by_season.get(s, 0) < 900 for s in TRAIN_SEASONS):
        raise RuntimeError(f"historical joined coverage too small: {dict(by_season)}")
    return joined, {
        "base_feature_count": len(base_feature_names),
        "base_audit_status": base_audit.get("status") if isinstance(base_audit, dict) else None,
        "player_audit": player_audit,
        "joined_by_season": dict(by_season),
        "joined_by_competition": dict(by_comp),
        "misses": dict(misses),
        "crosswalk_summary": crosswalk_summary,
    }


class Transform:
    def __init__(self, leagues: tuple[str, ...], spec: str):
        self.leagues = leagues
        self.spec = spec
        self.market_coef: np.ndarray | None = None
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None

    def _raw_z(self, residual: np.ndarray, market: np.ndarray, cid: str) -> np.ndarray:
        side = math.log(max(EPS, float(market[0])) / max(EPS, float(market[2])))
        s = sorted((float(x) for x in market), reverse=True)
        margin = s[0] - s[1]
        ent = entropy(market)
        league = np.asarray([1.0 if cid == x else 0.0 for x in self.leagues], dtype=float)
        if self.spec == "core_signed":
            parts = [residual, np.asarray([margin, ent]), league]
        elif self.spec == "core_balance":
            parts = [residual, np.abs(residual[:RESID_DIFF_COUNT]), np.asarray([margin, ent]), league]
        elif self.spec == "core_interaction":
            exp11 = float(residual[3])
            top11 = float(residual[0])
            parts = [
                residual,
                np.abs(residual[:RESID_DIFF_COUNT]),
                np.asarray([
                    margin,
                    ent,
                    exp11 * side,
                    abs(exp11) * margin,
                    abs(top11) * margin,
                ]),
                league,
            ]
        else:
            raise RuntimeError(f"unknown feature spec {self.spec}")
        return np.concatenate(parts)

    def fit(self, rows: list[dict[str, Any]]) -> "Transform":
        m = np.asarray([market_geometry(r["market"], str(r["competition_id"]), self.leagues) for r in rows], dtype=float)
        y = np.asarray([r["player_features"] for r in rows], dtype=float)
        self.market_coef = np.linalg.lstsq(m, y, rcond=None)[0]
        raw = []
        for row, mi, yi in zip(rows, m, y):
            residual = yi - mi @ self.market_coef
            raw.append(self._raw_z(residual, np.asarray(row["market"], dtype=float), str(row["competition_id"])))
        raw_a = np.asarray(raw, dtype=float)
        self.mean = raw_a.mean(axis=0)
        self.scale = raw_a.std(axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def apply(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if self.market_coef is None or self.mean is None or self.scale is None:
            raise RuntimeError("transform not fitted")
        out = []
        for row in rows:
            mi = np.asarray(market_geometry(row["market"], str(row["competition_id"]), self.leagues), dtype=float)
            yi = np.asarray(row["player_features"], dtype=float)
            residual = yi - mi @ self.market_coef
            raw = self._raw_z(residual, np.asarray(row["market"], dtype=float), str(row["competition_id"]))
            out.append((raw - self.mean) / self.scale)
        z = np.asarray(out, dtype=float)
        return np.column_stack([np.ones(len(z)), z])


def softmax(logits: np.ndarray) -> np.ndarray:
    x = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=1, keepdims=True)


def fit_offset(rows: list[dict[str, Any]], z: np.ndarray, ridge: float) -> np.ndarray:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    y = np.asarray([int(r["y"]) for r in rows], dtype=int)
    offset = np.log(np.clip(market, EPS, 1.0))
    d = z.shape[1]

    def fun(flat: np.ndarray) -> tuple[float, np.ndarray]:
        w = flat.reshape(3, d)
        probs = softmax(offset + z @ w.T)
        n = len(y)
        loss = -np.log(np.clip(probs[np.arange(n), y], EPS, 1.0)).mean()
        penalty = float(ridge) * float(np.sum(w[:, 1:] ** 2))
        diff = probs.copy()
        diff[np.arange(n), y] -= 1.0
        grad = (diff.T @ z) / n
        grad[:, 1:] += 2.0 * float(ridge) * w[:, 1:]
        return float(loss + penalty), grad.ravel()

    init = np.zeros((3, d), dtype=float).ravel()
    res = minimize(lambda q: fun(q), init, jac=True, method="L-BFGS-B", options={"maxiter": 600, "ftol": 1e-11})
    if not res.success:
        raise RuntimeError(f"offset optimizer failed: {res.message}")
    return np.asarray(res.x, dtype=float).reshape(3, d)


def apply_offset(rows: list[dict[str, Any]], z: np.ndarray, w: np.ndarray, key: str = "candidate") -> None:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    probs = softmax(np.log(np.clip(market, EPS, 1.0)) + z @ w.T)
    for row, p in zip(rows, probs):
        row[key] = [float(x) for x in p]


def evaluate_fold(train: list[dict[str, Any]], val: list[dict[str, Any]], spec: str, ridge: float, leagues: tuple[str, ...]) -> dict[str, Any]:
    transform = Transform(leagues, spec).fit(train)
    zt = transform.apply(train)
    zv = transform.apply(val)
    w = fit_offset(train, zt, ridge)
    trial = [dict(r) for r in val]
    apply_offset(trial, zv, w)
    cm = metrics(trial, "candidate")
    mm = metrics(trial, "market")
    return {
        "candidate": cm,
        "market": mm,
        "top1_uplift_pp": (cm["top1"] - mm["top1"]) * 100.0,
        "proper_guard": (
            cm["logloss"] <= mm["logloss"] + PROPER_LOG_TOL
            and cm["rps"] <= mm["rps"] + PROPER_RPS_TOL
        ),
    }


def main() -> int:
    historical, join_audit = build_historical_join()
    leagues = tuple(sorted({str(r["competition_id"]) for r in historical}))
    if len(leagues) != 5:
        raise RuntimeError(f"expected five Gold500 leagues, found {leagues}")

    leaderboard = []
    for spec in FEATURE_SPECS:
        for ridge in RIDGES:
            fold_results = []
            for train_seasons, val_season in FOLDS:
                tr = [r for r in historical if str(r["season"]) in train_seasons]
                va = [r for r in historical if str(r["season"]) == val_season]
                if len(tr) < 900 or len(va) < 900:
                    raise RuntimeError(f"fold coverage too small train={len(tr)} val={len(va)}")
                fold_results.append(evaluate_fold(tr, va, spec, ridge, leagues))
            candidate_top1 = sum(x["candidate"]["top1"] for x in fold_results) / len(fold_results)
            market_top1 = sum(x["market"]["top1"] for x in fold_results) / len(fold_results)
            candidate_log = sum(x["candidate"]["logloss"] for x in fold_results) / len(fold_results)
            market_log = sum(x["market"]["logloss"] for x in fold_results) / len(fold_results)
            candidate_rps = sum(x["candidate"]["rps"] for x in fold_results) / len(fold_results)
            market_rps = sum(x["market"]["rps"] for x in fold_results) / len(fold_results)
            leaderboard.append({
                "spec": spec,
                "ridge": ridge,
                "mean_candidate_top1": candidate_top1,
                "mean_market_top1": market_top1,
                "mean_uplift_pp": (candidate_top1 - market_top1) * 100.0,
                "mean_candidate_logloss": candidate_log,
                "mean_market_logloss": market_log,
                "mean_candidate_rps": candidate_rps,
                "mean_market_rps": market_rps,
                "proper_guard": all(x["proper_guard"] for x in fold_results),
                "folds": fold_results,
            })

    eligible = [x for x in leaderboard if x["proper_guard"]]
    if not eligible:
        eligible = leaderboard
    selected = min(
        eligible,
        key=lambda x: (
            -x["mean_candidate_top1"],
            -x["mean_uplift_pp"],
            x["mean_candidate_logloss"],
            x["mean_candidate_rps"],
            FEATURE_SPECS.index(x["spec"]),
            RIDGES.index(x["ridge"]),
        ),
    )
    leaderboard.sort(key=lambda x: (-x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"]))

    final_train = [r for r in historical if str(r["season"]) in TRAIN_SEASONS]
    transform = Transform(leagues, str(selected["spec"])).fit(final_train)
    ztrain = transform.apply(final_train)
    w = fit_offset(final_train, ztrain, float(selected["ridge"]))

    features = load_jsonl(GOLD_FEATURES)
    fast_features = [r for r in features if str(r["partition"]) == PART]
    if len(features) != 500 or len(fast_features) != 100:
        raise RuntimeError(f"Gold500 feature contract changed total={len(features)} fast={len(fast_features)}")
    labels = load_fast100_labels_only(GOLD_LABELS)

    fast = []
    for row in fast_features:
        idx = int(row["gold_index"])
        lab = labels[idx]
        fast.append({
            "gold_index": idx,
            "competition_id": str(row["competition_id"]),
            "date": str(row["date"]),
            "home_team": str(row["home_team"]),
            "away_team": str(row["away_team"]),
            "market": [float(x) for x in row["market"]],
            "formal": [float(x) for x in row["formal"]],
            "player_features": [float(x) for x in row["player_features"]],
            "y": int(lab["label"]),
            "actual_score": [int(x) for x in lab["actual_score"]],
        })
    zfast = transform.apply(fast)
    apply_offset(fast, zfast, w)

    market_m = metrics(fast, "market")
    formal_m = metrics(fast, "formal")
    candidate_m = metrics(fast, "candidate")
    uplift_pp = (candidate_m["top1"] - market_m["top1"]) * 100.0
    top1_pass = candidate_m["hits"] >= market_m["hits"] + 3 and candidate_m["hits"] >= 63
    proper_pass = (
        candidate_m["logloss"] <= market_m["logloss"] + PROPER_LOG_TOL
        and candidate_m["rps"] <= market_m["rps"] + PROPER_RPS_TOL
    )
    gate = bool(top1_pass and proper_pass)

    changed = []
    for r in fast:
        mp = max(range(3), key=lambda i: r["market"][i])
        cp = max(range(3), key=lambda i: r["candidate"][i])
        if mp != cp:
            changed.append({
                "gold_index": r["gold_index"],
                "competition_id": r["competition_id"],
                "date": r["date"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "actual_result": r["y"],
                "market_pick": mp,
                "candidate_pick": cp,
                "market_correct": mp == r["y"],
                "candidate_correct": cp == r["y"],
                "market": r["market"],
                "candidate": r["candidate"],
            })

    payload = {
        "schema_version": "V6.37.0-market-offset-player-residual-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_GOLD500_FAST100_MARKET_OFFSET_PLAYER_RESIDUAL",
        "research_hypothesis": (
            "Preserve the market as the base probability and learn only shrinkage-controlled "
            "outcome-specific corrections from player-core information orthogonalized against market geometry."
        ),
        "governance_contract": {
            "A_FAST100_only": True,
            "B_CONFIRM300_labels_read": False,
            "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "confidence_filtering": False,
            "league_dropping": False,
            "seed_replacement": False,
            "A100_parameter_tuning": False,
            "retrospective_market_research_only": True,
            "CURRENT_unchanged": True,
        },
        "historical_join_audit": join_audit,
        "architecture": {
            "market_offset": "softmax(log(p_market) + W*z)",
            "player_feature_count": len(PLAYER_NAMES),
            "player_residualization": "OLS on market geometry + league using TRAIN fold only",
            "feature_specs": list(FEATURE_SPECS),
            "ridge_grid": list(RIDGES),
            "rolling_folds": [{"train": list(a), "validate": b} for a, b in FOLDS],
            "proper_tolerances": {"logloss": PROPER_LOG_TOL, "rps": PROPER_RPS_TOL},
        },
        "historical_selection": {
            "selected": selected,
            "leaderboard": leaderboard,
        },
        "fast100": {
            "market": market_m,
            "formal": formal_m,
            "candidate": candidate_m,
            "candidate_vs_market_top1_pp": uplift_pp,
            "required_market_top1_uplift_pp": REQUIRED_UPLIFT_PP,
            "top1_gate_pass": top1_pass,
            "proper_gate_pass": proper_pass,
            "gate_passed": gate,
            "changed_pick_count": len(changed),
            "changed_pick_audit": changed,
        },
        "decision": "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "decision": payload["decision"],
        "historical_rows": join_audit["joined_by_season"],
        "selected": {
            "spec": selected["spec"],
            "ridge": selected["ridge"],
            "mean_candidate_top1": selected["mean_candidate_top1"],
            "mean_market_top1": selected["mean_market_top1"],
            "mean_uplift_pp": selected["mean_uplift_pp"],
            "proper_guard": selected["proper_guard"],
        },
        "fast100": {
            "market": market_m,
            "formal": formal_m,
            "candidate": candidate_m,
            "candidate_vs_market_top1_pp": uplift_pp,
            "changed_pick_count": len(changed),
            "gate_passed": gate,
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
