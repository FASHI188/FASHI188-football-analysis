#!/usr/bin/env python3
"""V6.40.0 Gold500 market-error meta decision challenge.

Core idea
---------
Keep the closing-market probability vector untouched. Do NOT train another model
that replaces the 1X2 probabilities. Instead train two decision-only models:

1) error detector: P(market Top-1 is wrong | strict prematch features)
2) alternate selector: conditional on market Top-1 being wrong, choose between
   the market's second-ranked and third-ranked outcomes.

The candidate pick changes only when both models clear validation-selected
thresholds. Because probabilities remain exactly the market probabilities,
Brier/log-loss/RPS are identical to market by construction; only the Top-1
decision boundary is challenged.

Information families
--------------------
- frozen V6.36 53 base features: market/formal/xG/npxG/xPTS/PPDA/deep,
  prior shots/SOT, same-day-safe Elo/form/rest;
- 18 strict-PIT player-core features from prior lineups, transfers, valuations;
- optional earlier->closing 1X2 path + O/U 2.5 + Asian-handicap movement;
- optional cross-bookmaker closing disagreement/consensus features.

Governance
----------
- model/policy selection only on rolling historical folds:
  2022/23 -> 2023/24 and 2022/23+2023/24 -> 2024/25;
- Gold500 A_FAST100 is read once only if the historical gate passes;
- B_CONFIRM300 labels are never read; C_SEALED100 remains sealed;
- all A100 matches count; no confidence filtering, league dropping, seed
  replacement, or A100 threshold tuning;
- retrospective closing market is research-only; CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6360 as gold0  # noqa: E402
import build_gold500_feature_library_v6361 as gold1  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402
import v6_market_residual_fusion_v620 as marketmod  # noqa: E402

OUT = ROOT / "manifests" / "v6_market_error_meta_gold500_v6400_status.json"
GOLD_FEATURES = ROOT / "manifests" / "gold500_v6360" / "gold500_features_v6360.jsonl"
GOLD_LABELS = ROOT / "manifests" / "gold500_v6360" / "gold500_development_labels_v6360.jsonl"

TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
FOLDS = (
    (("2022/23",), "2023/24"),
    (("2022/23", "2023/24"), "2024/25"),
)
TEST_SEASON = "2025/26"
PART = "A_FAST100"
SEED = 640100
EPS = 1e-12

FEATURE_SPECS = ("core71", "core71_path", "core71_all_market")
DEPTHS = (3, 5)
L2S = (20.0, 50.0)
WRONG_THRESHOLDS = (0.55, 0.60, 0.65, 0.70)
ALT_THRESHOLDS = (0.52, 0.57, 0.62)
ITERATIONS = 320
LEARNING_RATE = 0.035

HIST_MEAN_UPLIFT_MIN_PP = 0.50
HIST_MIN_FOLD_UPLIFT_PP = 0.00
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_PP = 3.0

DOMAINS = tuple(v632.DOMAINS)
PLAYER_NAMES = tuple(gold0.PLAYER_FEATURE_NAMES)
EXCLUDE_BOOK_PREFIXES = {"Avg", "Max", "BbAv", "BbMx"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_fast100_labels_only(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for _ in range(100):
            line = handle.readline()
            if not line:
                raise RuntimeError("Gold labels ended before A_FAST100 completed")
            row = json.loads(line)
            if str(row.get("partition")) != PART:
                raise RuntimeError(f"non-A label encountered inside first 100 rows: {row.get('partition')}")
            out[int(row["gold_index"])] = row
    if len(out) != 100 or set(out) != set(range(100)):
        raise RuntimeError(f"A_FAST100 label contract changed: count={len(out)}")
    return out


def fnum(row: dict[str, str], key: str) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def odd(row: dict[str, str], key: str) -> float | None:
    x = fnum(row, key)
    return x if x is not None and x > 1.0 else None


def devig2(a: float, b: float) -> list[float]:
    inv = np.asarray([1.0 / a, 1.0 / b], dtype=float)
    inv /= inv.sum()
    return [float(x) for x in inv]


def devig3(a: float, b: float, c: float) -> list[float]:
    inv = np.asarray([1.0 / a, 1.0 / b, 1.0 / c], dtype=float)
    inv /= inv.sum()
    return [float(x) for x in inv]


def entropy(p: list[float] | np.ndarray) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def margin(p: list[float] | np.ndarray) -> float:
    s = sorted((float(x) for x in p), reverse=True)
    return s[0] - s[1]


def early_1x2(raw: dict[str, str]) -> list[float] | None:
    for hk, dk, ak in (("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A")):
        h, d, a = odd(raw, hk), odd(raw, dk), odd(raw, ak)
        if h is not None and d is not None and a is not None:
            return devig3(h, d, a)
    return None


def ou_features(raw: dict[str, str]) -> list[float]:
    early = close = None
    for ok, uk in (("Avg>2.5", "Avg<2.5"), ("B365>2.5", "B365<2.5")):
        o, u = odd(raw, ok), odd(raw, uk)
        if o is not None and u is not None:
            early = devig2(o, u)
            break
    for ok, uk in (("AvgC>2.5", "AvgC<2.5"), ("B365C>2.5", "B365C<2.5")):
        o, u = odd(raw, ok), odd(raw, uk)
        if o is not None and u is not None:
            close = devig2(o, u)
            break
    if early is None or close is None:
        return [0.0, 0.0, 0.0]
    return [float(close[1]), float(close[1] - early[1]), 1.0]


def ah_features(raw: dict[str, str]) -> list[float]:
    eline = fnum(raw, "AHh")
    cline = fnum(raw, "AHCh")
    eh, ea = odd(raw, "AvgAHH"), odd(raw, "AvgAHA")
    ch, ca = odd(raw, "AvgCAHH"), odd(raw, "AvgCAHA")
    if eh is None or ea is None:
        eh, ea = odd(raw, "B365AHH"), odd(raw, "B365AHA")
    if ch is None or ca is None:
        ch, ca = odd(raw, "B365CAHH"), odd(raw, "B365CAHA")
    if eline is None or cline is None or eh is None or ea is None or ch is None or ca is None:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    ep = devig2(eh, ea)
    cp = devig2(ch, ca)
    return [float(cline), float(cline - eline), float(cp[0]), float(cp[0] - ep[0]), 1.0]


def individual_book_features(raw: dict[str, str], close_market: list[float]) -> list[float]:
    probs: list[list[float]] = []
    for key in raw:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_BOOK_PREFIXES:
            continue
        dk, ak = prefix + "CD", prefix + "CA"
        if dk not in raw or ak not in raw:
            continue
        h, d, a = odd(raw, key), odd(raw, dk), odd(raw, ak)
        if h is None or d is None or a is None:
            continue
        probs.append(devig3(h, d, a))
    if len(probs) < 2:
        return [0.0] * 13
    arr = np.asarray(probs, dtype=float)
    mean = arr.mean(axis=0); mean /= mean.sum()
    std = arr.std(axis=0)
    rng = arr.max(axis=0) - arr.min(axis=0)
    top = np.argmax(arr, axis=1)
    votes = np.asarray([(top == k).mean() for k in range(3)], dtype=float)
    base = np.asarray(close_market, dtype=float)
    return [
        *[float(x) for x in mean - base],
        *[float(x) for x in std],
        *[float(x) for x in rng],
        *[float(x) for x in votes],
        float(math.log1p(len(probs))),
    ]


def market_extra_lookup(cid: str) -> tuple[dict[tuple[str, str, str, str], dict[str, list[float]]], dict[str, Any]]:
    out: dict[tuple[str, str, str, str], dict[str, list[float]]] = {}
    stats = Counter()
    for path in sorted((ROOT / "processed" / cid).glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                stats["rows"] += 1
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season not in TRAIN_SEASONS and season != TEST_SEASON:
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                    market_dict, _family = marketmod._closing_market(raw)
                except Exception:
                    stats["parse_rejected"] += 1
                    continue
                if market_dict is None:
                    stats["missing_close"] += 1
                    continue
                close = [float(market_dict[k]) for k in ("home", "draw", "away")]
                early = early_1x2(raw)
                if early is None:
                    stats["missing_early"] += 1
                    continue
                delta = np.asarray(close) - np.asarray(early)
                logmove = np.log(np.clip(close, EPS, 1.0)) - np.log(np.clip(early, EPS, 1.0))
                path_x = [
                    *[float(x) for x in early],
                    *[float(x) for x in delta],
                    *[float(x) for x in logmove],
                    float(entropy(close) - entropy(early)),
                    float(margin(close) - margin(early)),
                ]
                ou_x = ou_features(raw)
                ah_x = ah_features(raw)
                book_x = individual_book_features(raw, close)
                home = v632._token(cid, str(raw.get("HomeTeam") or ""))
                away = v632._token(cid, str(raw.get("AwayTeam") or ""))
                if not home or not away:
                    stats["identity_rejected"] += 1
                    continue
                key = (season, date, home, away)
                if key in out:
                    stats["duplicate_key"] += 1
                    continue
                out[key] = {"path": path_x, "ou": ou_x, "ah": ah_x, "book": book_x}
                stats["attached"] += 1
                stats["ou_available"] += int(ou_x[-1] > 0.5)
                stats["ah_available"] += int(ah_x[-1] > 0.5)
                stats["book_available"] += int(book_x[-1] > 0.0)
    return out, dict(stats)


def build_player_rows_all() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = v633.load_config()
    data_by_comp: dict[str, dict[str, Any]] = {}
    indexes_by_comp: dict[str, dict[str, Any]] = {}
    params_by_comp_season: dict[str, dict[str, dict[str, float]]] = {}
    dynamic_selection: dict[str, dict[str, Any]] = {}
    names_by_comp: dict[str, dict[int, tuple[str, str]]] = {}

    for cid in DOMAINS:
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
    counts = Counter(); missing_names = 0
    for cid in DOMAINS:
        for season in TRAIN_SEASONS:
            params = params_by_comp_season[cid].get(season)
            if params is None:
                continue
            rs = v633._season_rows(
                cid, season, dynamic_selection[cid], data_by_comp[cid], indexes_by_comp[cid], params, valuations
            )
            counts[f"{cid}:{season}"] = len(rs)
            for row in rs:
                try:
                    gid = int(str(row["match_key"]).rsplit(":", 1)[-1])
                except (TypeError, ValueError):
                    continue
                names = names_by_comp[cid].get(gid)
                if not names:
                    missing_names += 1
                    continue
                item = dict(row)
                item["home_name"], item["away_name"] = names
                item["player_features"] = v633._pair_features(item["home_player_context"], item["away_player_context"])
                rows.append(item)
    return rows, {
        "rows": len(rows), "missing_transfermarkt_game_names": missing_names,
        "valuation_audit": valuation_audit, "rows_by_domain_season": dict(counts),
    }


def build_historical_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, _base_audit, base_names = v632._build_rows()
    base_rows = [dict(r) for r in base_rows if str(r["season"]) in TRAIN_SEASONS]
    player_rows, player_audit = build_player_rows_all()
    extras = {}; extra_audit = {}
    for cid in DOMAINS:
        extras[cid], extra_audit[cid] = market_extra_lookup(cid)

    joined: list[dict[str, Any]] = []
    misses = Counter(); crosswalk = {}
    for season in TRAIN_SEASONS:
        bseason = [r for r in base_rows if str(r["season"]) == season]
        pseason = [r for r in player_rows if str(r["season"]) == season]
        mapping, audit = gold1.build_crosswalk(bseason, pseason)
        crosswalk[season] = {
            cid: {
                "mapped_teams": int(meta["mapped_teams"]),
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
            key = (cid, str(row["date"]), v632._token(cid, mh), v632._token(cid, ma))
            if key in pmap:
                misses["duplicate_player_key"] += 1
                continue
            pmap[key] = row

        seen = set()
        for row in bseason:
            cid = str(row["competition_id"])
            htok = v632._token(cid, str(row["home_team"])); atok = v632._token(cid, str(row["away_team"]))
            key = (cid, str(row["date"]), htok, atok)
            if key in seen:
                misses["duplicate_base_key"] += 1
                continue
            seen.add(key)
            player = pmap.get(key)
            extra = extras[cid].get((season, str(row["date"]), htok, atok))
            if player is None:
                misses["player_join_miss"] += 1
                continue
            if extra is None:
                misses["market_extra_miss"] += 1
                continue
            if int(row["y"]) != int(player["y"]):
                raise RuntimeError(f"historical label mismatch for {key}")
            pf = [float(x) for x in player["player_features"]]
            if len(row["x"]) != len(base_names) or len(pf) != len(PLAYER_NAMES):
                raise RuntimeError(f"feature length mismatch for {key}")
            joined.append({
                "competition_id": cid, "season": season, "date": str(row["date"]),
                "home_team": str(row["home_team"]), "away_team": str(row["away_team"]),
                "base_features": [float(x) for x in row["x"]], "player_features": pf,
                "market": [float(x) for x in row["market"]], "formal": [float(x) for x in row["formal"]],
                "path_features": extra["path"], "ou_features": extra["ou"],
                "ah_features": extra["ah"], "book_features": extra["book"],
                "y": int(row["y"]), "actual_score": [int(x) for x in row["actual_score"]],
            })

    by_season = Counter(r["season"] for r in joined)
    if any(by_season.get(s, 0) < 900 for s in TRAIN_SEASONS):
        raise RuntimeError(f"historical joined coverage too small: {dict(by_season)}")
    return joined, {
        "base_feature_count": len(base_names), "player_feature_count": len(PLAYER_NAMES),
        "joined_by_season": dict(by_season),
        "joined_by_competition": dict(Counter(r["competition_id"] for r in joined)),
        "misses": dict(misses), "player_audit": player_audit,
        "market_extra_audit": extra_audit, "crosswalk_summary": crosswalk,
    }


def order_market(p: list[float]) -> list[int]:
    return sorted(range(3), key=lambda i: (float(p[i]), -i), reverse=True)


def decision_context(row: dict[str, Any]) -> list[float]:
    m = [float(x) for x in row["market"]]
    f = [float(x) for x in row["formal"]]
    order = order_market(m)
    top, second, third = order
    return [
        *[1.0 if top == i else 0.0 for i in range(3)],
        float(m[top]), float(m[second]), float(m[third]),
        float(m[top] - m[second]), float(m[second] - m[third]),
        1.0 if max(range(3), key=lambda i: f[i]) == top else 0.0,
        float(f[top] - f[second]),
        1.0 if second == 1 else 0.0,
        1.0 if third == 1 else 0.0,
    ]


def feature_vector(row: dict[str, Any], spec: str) -> list[float]:
    x = [*row["base_features"], *row["player_features"], *decision_context(row)]
    if spec in {"core71_path", "core71_all_market"}:
        x.extend(row["path_features"])
        x.extend(row["ou_features"])
        x.extend(row["ah_features"])
    if spec == "core71_all_market":
        x.extend(row["book_features"])
    return [float(v) for v in x]


def market_pick(row: dict[str, Any]) -> int:
    return order_market(row["market"])[0]


def alt_target(row: dict[str, Any]) -> int:
    order = order_market(row["market"])
    if int(row["y"]) == order[0]:
        raise RuntimeError("alt_target called for market-correct row")
    return 1 if int(row["y"]) == order[1] else 0


def fit_binary(rows: list[dict[str, Any]], spec: str, depth: int, l2: float, target: str) -> CatBoostClassifier:
    if target == "wrong":
        y = [int(market_pick(r) != int(r["y"])) for r in rows]
    elif target == "alt_second":
        rows = [r for r in rows if market_pick(r) != int(r["y"])]
        y = [alt_target(r) for r in rows]
    else:
        raise RuntimeError(target)
    if len(set(y)) != 2:
        raise RuntimeError(f"binary target collapsed for {target}: {Counter(y)}")
    model = CatBoostClassifier(
        loss_function="Logloss", iterations=ITERATIONS, depth=int(depth), learning_rate=LEARNING_RATE,
        l2_leaf_reg=float(l2), random_seed=SEED, random_strength=0.4,
        bootstrap_type="Bayesian", bagging_temperature=0.4,
        allow_writing_files=False, verbose=False, thread_count=2,
    )
    model.fit([feature_vector(r, spec) for r in rows], y)
    return model


def predict_meta(
    rows: list[dict[str, Any]], spec: str, wrong_model: CatBoostClassifier, alt_model: CatBoostClassifier
) -> list[dict[str, float | int]]:
    x = [feature_vector(r, spec) for r in rows]
    pw = wrong_model.predict_proba(x)[:, 1]
    pa = alt_model.predict_proba(x)[:, 1]
    out = []
    for r, a, b in zip(rows, pw, pa):
        order = order_market(r["market"])
        alt = order[1] if float(b) >= 0.5 else order[2]
        out.append({
            "market_pick": order[0], "second": order[1], "third": order[2],
            "p_wrong": float(a), "p_second": float(b), "alt_pick": int(alt),
            "alt_conf": float(max(float(b), 1.0 - float(b))),
        })
    return out


def score_policy(
    rows: list[dict[str, Any]], meta: list[dict[str, float | int]], wrong_threshold: float, alt_threshold: float
) -> dict[str, Any]:
    market_hits = candidate_hits = overrides = wins = losses = neutral = 0
    predicted = Counter(); actual = Counter(); override_to = Counter()
    for r, m in zip(rows, meta):
        y = int(r["y"]); mp = int(m["market_pick"]); ap = int(m["alt_pick"])
        override = float(m["p_wrong"]) >= wrong_threshold and float(m["alt_conf"]) >= alt_threshold
        cp = ap if override else mp
        market_ok = mp == y; candidate_ok = cp == y
        market_hits += int(market_ok); candidate_hits += int(candidate_ok)
        predicted[str(cp)] += 1; actual[str(y)] += 1
        if override:
            overrides += 1; override_to[str(cp)] += 1
            if (not market_ok) and candidate_ok:
                wins += 1
            elif market_ok:
                losses += 1
            else:
                neutral += 1
    n = len(rows)
    return {
        "count": n, "market_hits": market_hits, "candidate_hits": candidate_hits,
        "market_top1": market_hits / n, "candidate_top1": candidate_hits / n,
        "uplift_pp": (candidate_hits - market_hits) * 100.0 / n,
        "override_count": overrides, "override_rate": overrides / n,
        "override_wins": wins, "override_losses": losses, "override_neutral": neutral,
        "override_net": wins - losses,
        "predicted_counts": dict(predicted), "actual_counts": dict(actual),
        "override_to_counts": dict(override_to),
    }


def evaluate_configuration(
    train: list[dict[str, Any]], val: list[dict[str, Any]], spec: str, depth: int, l2: float
) -> list[dict[str, Any]]:
    wrong_model = fit_binary(train, spec, depth, l2, "wrong")
    alt_model = fit_binary(train, spec, depth, l2, "alt_second")
    meta = predict_meta(val, spec, wrong_model, alt_model)
    out = []
    for wt in WRONG_THRESHOLDS:
        for at in ALT_THRESHOLDS:
            score = score_policy(val, meta, wt, at)
            out.append({
                "spec": spec, "depth": depth, "l2": l2,
                "wrong_threshold": wt, "alt_threshold": at, "score": score,
            })
    return out


def select_candidate(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    configs = [(spec, d, l2) for spec in FEATURE_SPECS for d in DEPTHS for l2 in L2S]
    aggregate: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fold_id, (train_seasons, val_season) in enumerate(FOLDS):
        tr = [r for r in rows if r["season"] in train_seasons]
        va = [r for r in rows if r["season"] == val_season]
        if len(tr) < 900 or len(va) < 900:
            raise RuntimeError(f"fold coverage too small train={len(tr)} val={len(va)}")
        for spec, depth, l2 in configs:
            for item in evaluate_configuration(tr, va, spec, depth, l2):
                key = (spec, depth, l2, item["wrong_threshold"], item["alt_threshold"])
                record = aggregate.setdefault(key, {
                    "spec": spec, "depth": depth, "l2": l2,
                    "wrong_threshold": item["wrong_threshold"], "alt_threshold": item["alt_threshold"],
                    "folds": [],
                })
                record["folds"].append({"fold_id": fold_id, "train": list(train_seasons), "validate": val_season, **item["score"]})

    leaderboard = []
    for record in aggregate.values():
        folds = record["folds"]
        if len(folds) != len(FOLDS):
            continue
        mean_uplift = sum(float(f["uplift_pp"]) for f in folds) / len(folds)
        min_uplift = min(float(f["uplift_pp"]) for f in folds)
        mean_net = sum(int(f["override_net"]) for f in folds) / len(folds)
        mean_overrides = sum(int(f["override_count"]) for f in folds) / len(folds)
        leaderboard.append({
            **record,
            "mean_uplift_pp": mean_uplift, "min_fold_uplift_pp": min_uplift,
            "mean_override_net": mean_net, "mean_override_count": mean_overrides,
            "historical_gate": mean_uplift >= HIST_MEAN_UPLIFT_MIN_PP and min_uplift >= HIST_MIN_FOLD_UPLIFT_PP,
        })
    if not leaderboard:
        raise RuntimeError("empty V6.40 leaderboard")
    leaderboard.sort(key=lambda x: (
        0 if x["historical_gate"] else 1,
        -float(x["mean_uplift_pp"]), -float(x["min_fold_uplift_pp"]),
        -float(x["mean_override_net"]), float(x["mean_override_count"]),
        FEATURE_SPECS.index(x["spec"]), int(x["depth"]), float(x["l2"]),
        float(x["wrong_threshold"]), float(x["alt_threshold"]),
    ))
    return leaderboard[0], leaderboard


def build_fast_rows() -> list[dict[str, Any]]:
    features = load_jsonl(GOLD_FEATURES)
    fast_features = [r for r in features if str(r["partition"]) == PART]
    if len(features) != 500 or len(fast_features) != 100:
        raise RuntimeError(f"Gold500 feature contract changed total={len(features)} fast={len(fast_features)}")
    extras = {}
    for cid in DOMAINS:
        extras[cid], _ = market_extra_lookup(cid)
    rows = []
    for r in fast_features:
        cid = str(r["competition_id"])
        key = (
            TEST_SEASON, str(r["date"]),
            v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])),
        )
        extra = extras[cid].get(key)
        if extra is None:
            raise RuntimeError(f"A_FAST100 market extra missing for {key}")
        rows.append({
            "gold_index": int(r["gold_index"]), "competition_id": cid, "season": TEST_SEASON,
            "date": str(r["date"]), "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "base_features": [float(x) for x in r["base_features"]],
            "player_features": [float(x) for x in r["player_features"]],
            "market": [float(x) for x in r["market"]], "formal": [float(x) for x in r["formal"]],
            "path_features": extra["path"], "ou_features": extra["ou"],
            "ah_features": extra["ah"], "book_features": extra["book"],
        })
    return rows


def attach_fast_labels(rows: list[dict[str, Any]]) -> None:
    labels = load_fast100_labels_only(GOLD_LABELS)
    for r in rows:
        lab = labels[int(r["gold_index"])]
        r["y"] = int(lab["label"])
        r["actual_score"] = [int(x) for x in lab["actual_score"]]


def main() -> int:
    historical, build_audit = build_historical_rows()
    selected, leaderboard = select_candidate(historical)

    payload: dict[str, Any] = {
        "schema_version": "V6.40.0-market-error-meta-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_DECISION_ONLY_MARKET_ERROR_META",
        "governance_contract": {
            "probability_vector_changed": False,
            "proper_scores_equal_market_by_construction": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "confidence_filtering": False, "league_dropping": False,
            "seed_replacement": False, "A100_parameter_tuning": False,
            "CURRENT_unchanged": True,
        },
        "architecture": {
            "stage1": "binary CatBoost: market Top-1 wrong vs correct",
            "stage2": "binary CatBoost on market-wrong train rows: actual is market second-ranked vs third-ranked",
            "decision": "override market Top-1 only when P(wrong)>=threshold and alternate confidence>=threshold",
            "probabilities": "unchanged closing-market 1X2",
            "feature_specs": list(FEATURE_SPECS),
            "depths": list(DEPTHS), "l2_grid": list(L2S),
            "wrong_thresholds": list(WRONG_THRESHOLDS), "alt_thresholds": list(ALT_THRESHOLDS),
            "rolling_folds": [{"train": list(a), "validate": b} for a, b in FOLDS],
            "historical_gate": {
                "mean_uplift_min_pp": HIST_MEAN_UPLIFT_MIN_PP,
                "min_fold_uplift_pp": HIST_MIN_FOLD_UPLIFT_PP,
            },
        },
        "build_audit": build_audit,
        "historical_selection": {"selected": selected, "leaderboard": leaderboard},
    }

    if not bool(selected["historical_gate"]):
        payload["fast100"] = {"opened": False, "reason": "historical gate failed; A_FAST100 labels not read"}
        payload["decision"] = "HISTORICAL_GATE_FAILED_A100_NOT_OPENED"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": payload["status"], "decision": payload["decision"],
            "historical_rows": build_audit["joined_by_season"],
            "selected": {k: selected[k] for k in ("spec", "depth", "l2", "wrong_threshold", "alt_threshold", "mean_uplift_pp", "min_fold_uplift_pp", "historical_gate")},
        }, ensure_ascii=False, indent=2))
        return 0

    final_train = [r for r in historical if r["season"] in TRAIN_SEASONS]
    wrong_model = fit_binary(final_train, str(selected["spec"]), int(selected["depth"]), float(selected["l2"]), "wrong")
    alt_model = fit_binary(final_train, str(selected["spec"]), int(selected["depth"]), float(selected["l2"]), "alt_second")
    fast = build_fast_rows()
    attach_fast_labels(fast)
    meta = predict_meta(fast, str(selected["spec"]), wrong_model, alt_model)
    score = score_policy(fast, meta, float(selected["wrong_threshold"]), float(selected["alt_threshold"]))

    changed = []
    for r, m in zip(fast, meta):
        override = float(m["p_wrong"]) >= float(selected["wrong_threshold"]) and float(m["alt_conf"]) >= float(selected["alt_threshold"])
        mp = int(m["market_pick"]); cp = int(m["alt_pick"]) if override else mp
        if cp != mp:
            changed.append({
                "gold_index": int(r["gold_index"]), "competition_id": r["competition_id"],
                "date": r["date"], "home_team": r["home_team"], "away_team": r["away_team"],
                "actual_result": int(r["y"]), "market_pick": mp, "candidate_pick": cp,
                "market_correct": mp == int(r["y"]), "candidate_correct": cp == int(r["y"]),
                "p_market_wrong": float(m["p_wrong"]), "alternate_confidence": float(m["alt_conf"]),
                "market": r["market"],
            })

    fast_gate = score["candidate_hits"] >= FAST_REQUIRED_HITS and float(score["uplift_pp"]) >= FAST_REQUIRED_UPLIFT_PP
    payload["fast100"] = {
        "opened": True,
        "market": {
            "count": 100, "hits": int(score["market_hits"]), "top1": float(score["market_top1"]),
            "brier_logloss_rps": "unchanged market probabilities",
        },
        "candidate": {
            "count": 100, "hits": int(score["candidate_hits"]), "top1": float(score["candidate_top1"]),
            "brier_logloss_rps": "identical to market by construction",
        },
        "candidate_vs_market_top1_pp": float(score["uplift_pp"]),
        "override_count": int(score["override_count"]),
        "override_wins": int(score["override_wins"]), "override_losses": int(score["override_losses"]),
        "override_neutral": int(score["override_neutral"]), "override_net": int(score["override_net"]),
        "predicted_counts": score["predicted_counts"], "actual_counts": score["actual_counts"],
        "override_to_counts": score["override_to_counts"],
        "required_hits": FAST_REQUIRED_HITS, "required_uplift_pp": FAST_REQUIRED_UPLIFT_PP,
        "gate_passed": bool(fast_gate), "changed_pick_audit": changed,
    }
    payload["decision"] = "OPEN_CONFIRM300" if fast_gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "historical_rows": build_audit["joined_by_season"],
        "selected": {k: selected[k] for k in ("spec", "depth", "l2", "wrong_threshold", "alt_threshold", "mean_uplift_pp", "min_fold_uplift_pp", "historical_gate")},
        "fast100": {k: payload["fast100"][k] for k in ("market", "candidate", "candidate_vs_market_top1_pp", "override_count", "override_wins", "override_losses", "override_neutral", "override_net", "predicted_counts", "actual_counts", "gate_passed")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
