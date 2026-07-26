#!/usr/bin/env python3
"""V6.39.0 Gold500 market-path + cross-market 1X2 challenger.

New information source
----------------------
Football-Data has two pre-match snapshots since 2019/20: an earlier snapshot
collected after market opening and a closing snapshot (C-suffixed fields).
This challenger asks whether the *path* from earlier -> closing prices, together
with closing/pre-closing O/U 2.5 and Asian-handicap structure, can identify the
small subset of matches where the closing 1X2 Top-1 is wrong.

This is deliberately different from:
- V6.37 player residuals;
- V6.38 cross-bookmaker closing dispersion;
- old direct 1X2 model reweighting.

Discipline
----------
- rolling historical selection only: 2022/23 -> 2023/24 and
  2022/23+2023/24 -> 2024/25;
- fixed Gold500 A_FAST100 is touched once after selection;
- B_CONFIRM300 labels are never read; C_SEALED100 remains sealed;
- no confidence filtering, league dropping, seed replacement or A100 tuning;
- retrospective odds are research-only; CURRENT V5.0.1 remains unchanged.
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
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_market_residual_fusion_v620 as marketmod  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402

OUT = ROOT / "manifests" / "v6_market_path_crossmarket_gold500_v6390_status.json"
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
RIDGES = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0)
SPECS = ("path_only", "path_plus_ou", "path_plus_ou_ah")
PROPER_LOG_TOL = 0.01
PROPER_RPS_TOL = 0.01
REQUIRED_UPLIFT_PP = 3.0


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_fast100_labels_only(path: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for _ in range(100):
            line = handle.readline()
            if not line:
                raise RuntimeError("Gold development label file ended inside A_FAST100")
            row = json.loads(line)
            if str(row.get("partition")) != PART:
                raise RuntimeError(f"non-A label encountered in first 100: {row.get('partition')}")
            out[int(row["gold_index"])] = row
    if len(out) != 100 or set(out) != set(range(100)):
        raise RuntimeError("A_FAST100 label contract changed")
    return out


def fnum(row: dict[str, str], key: str) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def odds(row: dict[str, str], key: str) -> float | None:
    x = fnum(row, key)
    return x if x is not None and x > 1.0 else None


def devig3(values: tuple[float, float, float]) -> list[float]:
    inv = np.asarray([1.0 / float(x) for x in values], dtype=float)
    inv /= inv.sum()
    return [float(x) for x in inv]


def devig2(a: float, b: float) -> list[float]:
    inv = np.asarray([1.0 / float(a), 1.0 / float(b)], dtype=float)
    inv /= inv.sum()
    return [float(x) for x in inv]


def entropy(p: list[float] | np.ndarray) -> float:
    return -sum(float(x) * math.log(max(EPS, float(x))) for x in p)


def margin3(p: list[float] | np.ndarray) -> float:
    s = sorted((float(x) for x in p), reverse=True)
    return s[0] - s[1]


def pre_1x2(row: dict[str, str]) -> tuple[list[float] | None, str | None]:
    families = (
        ("AvgH", "AvgD", "AvgA", "average_early"),
        ("B365H", "B365D", "B365A", "bet365_early"),
        ("PSH", "PSD", "PSA", "pinnacle_early_fallback"),
    )
    for hk, dk, ak, label in families:
        h, d, a = odds(row, hk), odds(row, dk), odds(row, ak)
        if h is not None and d is not None and a is not None:
            return devig3((h, d, a)), label
    return None, None


def ou25(row: dict[str, str], closing: bool) -> tuple[list[float] | None, str | None]:
    # Return [over, under]. Prefer market average; bookmaker fallbacks are explicit.
    families = (
        (("AvgC>2.5", "AvgC<2.5"), "average_closing") if closing else (("Avg>2.5", "Avg<2.5"), "average_early"),
        (("B365C>2.5", "B365C<2.5"), "bet365_closing") if closing else (("B365>2.5", "B365<2.5"), "bet365_early"),
        (("PC>2.5", "PC<2.5"), "pinnacle_closing_fallback") if closing else (("P>2.5", "P<2.5"), "pinnacle_early_fallback"),
    )
    for (ok, uk), label in families:
        o, u = odds(row, ok), odds(row, uk)
        if o is not None and u is not None:
            return devig2(o, u), label
    return None, None


def asian(row: dict[str, str], closing: bool) -> tuple[dict[str, float] | None, str | None]:
    # Probability is normalized quote-side probability, not a structural win probability.
    families = (
        (("AHCh", "AvgCAHH", "AvgCAHA"), "average_closing") if closing else (("AHh", "AvgAHH", "AvgAHA"), "average_early"),
        (("AHCh", "B365CAHH", "B365CAHA"), "bet365_closing") if closing else (("AHh", "B365AHH", "B365AHA"), "bet365_early"),
        (("AHCh", "PCAHH", "PCAHA"), "pinnacle_closing_fallback") if closing else (("AHh", "PAHH", "PAHA"), "pinnacle_early_fallback"),
    )
    for (lk, hk, ak), label in families:
        line = fnum(row, lk)
        h, a = odds(row, hk), odds(row, ak)
        if line is not None and h is not None and a is not None:
            q = devig2(h, a)
            return {"line": float(line), "home_quote_p": q[0], "away_quote_p": q[1]}, label
    return None, None


def make_payload(raw: dict[str, str], close_market: list[float]) -> dict[str, Any] | None:
    early, early_family = pre_1x2(raw)
    if early is None:
        return None
    close = [float(x) for x in close_market]
    delta = np.asarray(close) - np.asarray(early)
    logmove = np.log(np.clip(close, EPS, 1.0)) - np.log(np.clip(early, EPS, 1.0))
    side_early = math.log(max(EPS, early[0]) / max(EPS, early[2]))
    side_close = math.log(max(EPS, close[0]) / max(EPS, close[2]))
    draw_early = math.log(max(EPS, early[1]) / max(EPS, 1.0 - early[1]))
    draw_close = math.log(max(EPS, close[1]) / max(EPS, 1.0 - close[1]))
    base = [
        *delta.tolist(),
        *logmove.tolist(),
        float(entropy(close) - entropy(early)),
        float(margin3(close) - margin3(early)),
        float(side_close - side_early),
        float(draw_close - draw_early),
        float(np.max(np.abs(delta))),
        float(np.linalg.norm(delta)),
    ]

    ou_e, ou_e_family = ou25(raw, False)
    ou_c, ou_c_family = ou25(raw, True)
    if ou_e is not None and ou_c is not None:
        ou = [
            float(ou_c[1]),
            float(ou_c[1] - ou_e[1]),
            float(math.log(max(EPS, ou_c[1]) / max(EPS, ou_e[1]))),
            1.0,
        ]
    else:
        ou = [0.0, 0.0, 0.0, 0.0]

    ah_e, ah_e_family = asian(raw, False)
    ah_c, ah_c_family = asian(raw, True)
    if ah_e is not None and ah_c is not None:
        ah = [
            float(ah_c["line"]),
            float(ah_c["line"] - ah_e["line"]),
            float(ah_c["home_quote_p"]),
            float(ah_c["home_quote_p"] - ah_e["home_quote_p"]),
            float(abs(ah_c["line"])),
            1.0,
        ]
    else:
        ah = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    return {
        "early_1x2": early,
        "path_features": base,
        "ou_features": ou,
        "ah_features": ah,
        "families": {
            "early_1x2": early_family,
            "early_ou": ou_e_family,
            "closing_ou": ou_c_family,
            "early_ah": ah_e_family,
            "closing_ah": ah_c_family,
        },
    }


def raw_lookup(cid: str) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    directory = ROOT / "processed" / cid
    lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    stats = Counter()
    family_counts = Counter()
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                stats["rows"] += 1
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season not in TRAIN_SEASONS and season != TEST_SEASON:
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                    market, family = marketmod._closing_market(raw)
                except Exception:
                    stats["parse_rejected"] += 1
                    continue
                if market is None:
                    stats["missing_closing_1x2"] += 1
                    continue
                close = [float(market[k]) for k in ("home", "draw", "away")]
                payload = make_payload(raw, close)
                if payload is None:
                    stats["missing_early_1x2"] += 1
                    continue
                cid_home = v632._token(cid, str(raw.get("HomeTeam") or ""))
                cid_away = v632._token(cid, str(raw.get("AwayTeam") or ""))
                if not cid_home or not cid_away:
                    stats["identity_rejected"] += 1
                    continue
                key = (season, date, cid_home, cid_away)
                if key in lookup:
                    stats["duplicate_key"] += 1
                    continue
                payload["close_market"] = close
                payload["closing_1x2_family"] = family
                lookup[key] = payload
                stats["attached"] += 1
                if payload["ou_features"][-1] > 0.5:
                    stats["with_ou"] += 1
                if payload["ah_features"][-1] > 0.5:
                    stats["with_ah"] += 1
                for k, v in payload["families"].items():
                    if v:
                        family_counts[f"{k}:{v}"] += 1
    return lookup, {"stats": dict(stats), "family_counts": dict(family_counts)}


def feature_vector(row: dict[str, Any], spec: str, leagues: tuple[str, ...]) -> list[float]:
    x = list(row["path_features"])
    if spec in {"path_plus_ou", "path_plus_ou_ah"}:
        x.extend(row["ou_features"])
    if spec == "path_plus_ou_ah":
        x.extend(row["ah_features"])
    x.extend(1.0 if str(row["competition_id"]) == league else 0.0 for league in leagues[:-1])
    return [float(v) for v in x]


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter(); actual = Counter()
    for r in rows:
        p = [float(x) for x in r[key]]
        y = int(r["y"])
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        predicted[str(pick)] += 1; actual[str(y)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    return {
        "count": n, "hits": hits, "top1": hits / n if n else None,
        "brier": brier / n if n else None,
        "logloss": logloss / n if n else None,
        "rps": rps / n if n else None,
        "predicted_counts": dict(predicted), "actual_counts": dict(actual),
    }


class Scaler:
    def __init__(self, spec: str, leagues: tuple[str, ...]):
        self.spec = spec; self.leagues = leagues
        self.mean: np.ndarray | None = None; self.scale: np.ndarray | None = None

    def fit(self, rows: list[dict[str, Any]]) -> "Scaler":
        x = np.asarray([feature_vector(r, self.spec, self.leagues) for r in rows], dtype=float)
        self.mean = x.mean(axis=0); self.scale = x.std(axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        return self

    def apply(self, rows: list[dict[str, Any]]) -> np.ndarray:
        if self.mean is None or self.scale is None:
            raise RuntimeError("scaler not fitted")
        x = np.asarray([feature_vector(r, self.spec, self.leagues) for r in rows], dtype=float)
        z = (x - self.mean) / self.scale
        return np.column_stack([np.ones(len(z)), z])


def softmax(logits: np.ndarray) -> np.ndarray:
    x = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(x); return e / e.sum(axis=1, keepdims=True)


def fit_offset(rows: list[dict[str, Any]], z: np.ndarray, ridge: float) -> np.ndarray:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    y = np.asarray([int(r["y"]) for r in rows], dtype=int)
    offset = np.log(np.clip(market, EPS, 1.0)); d = z.shape[1]

    def fun(flat: np.ndarray) -> tuple[float, np.ndarray]:
        w = flat.reshape(3, d)
        probs = softmax(offset + z @ w.T)
        n = len(y)
        loss = -np.log(np.clip(probs[np.arange(n), y], EPS, 1.0)).mean()
        penalty = float(ridge) * float(np.sum(w[:, 1:] ** 2))
        diff = probs.copy(); diff[np.arange(n), y] -= 1.0
        grad = (diff.T @ z) / n
        grad[:, 1:] += 2.0 * float(ridge) * w[:, 1:]
        return float(loss + penalty), grad.ravel()

    res = minimize(fun, np.zeros(3 * d), jac=True, method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-11})
    if not res.success:
        raise RuntimeError(f"optimizer failed: {res.message}")
    return np.asarray(res.x, dtype=float).reshape(3, d)


def apply_offset(rows: list[dict[str, Any]], z: np.ndarray, w: np.ndarray, key: str = "candidate") -> None:
    market = np.asarray([r["market"] for r in rows], dtype=float)
    probs = softmax(np.log(np.clip(market, EPS, 1.0)) + z @ w.T)
    for r, p in zip(rows, probs):
        r[key] = [float(x) for x in p]


def proper(c: dict[str, Any], m: dict[str, Any]) -> bool:
    return c["logloss"] <= m["logloss"] + PROPER_LOG_TOL and c["rps"] <= m["rps"] + PROPER_RPS_TOL


def build_rows() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[tuple[str, str, str, str], dict[str, Any]]]]:
    base_rows, _audit, _names = v632._build_rows()
    domains = tuple(sorted({str(r["competition_id"]) for r in base_rows}))
    lookups = {}; raw_audit = {}
    for cid in domains:
        lookups[cid], raw_audit[cid] = raw_lookup(cid)

    rows = []; misses = Counter()
    for r in base_rows:
        season = str(r["season"])
        if season not in TRAIN_SEASONS:
            continue
        cid = str(r["competition_id"])
        key = (season, str(r["date"]), v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])))
        payload = lookups[cid].get(key)
        if payload is None:
            misses["path_join_miss"] += 1; continue
        rows.append({
            "competition_id": cid, "season": season, "date": str(r["date"]),
            "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "market": [float(x) for x in r["market"]],
            "early_1x2": payload["early_1x2"],
            "path_features": payload["path_features"],
            "ou_features": payload["ou_features"],
            "ah_features": payload["ah_features"],
            "y": int(r["y"]), "actual_score": [int(x) for x in r["actual_score"]],
        })
    by_season = Counter(r["season"] for r in rows)
    if any(by_season.get(s, 0) < 1000 for s in TRAIN_SEASONS):
        raise RuntimeError(f"historical market-path coverage too small: {dict(by_season)}")
    return rows, {
        "joined_by_season": dict(by_season),
        "joined_by_competition": dict(Counter(r["competition_id"] for r in rows)),
        "misses": dict(misses), "raw_market_audit": raw_audit,
    }, lookups


def main() -> int:
    historical, build_audit, lookups = build_rows()
    leagues = tuple(sorted({str(r["competition_id"]) for r in historical}))
    leaderboard = []
    for spec in SPECS:
        for ridge in RIDGES:
            folds = []
            for train_seasons, val_season in FOLDS:
                tr = [r for r in historical if r["season"] in train_seasons]
                va = [dict(r) for r in historical if r["season"] == val_season]
                scaler = Scaler(spec, leagues).fit(tr)
                zt = scaler.apply(tr); zv = scaler.apply(va)
                w = fit_offset(tr, zt, ridge)
                apply_offset(va, zv, w)
                cm = metrics(va, "candidate"); mm = metrics(va, "market")
                folds.append({
                    "candidate": cm, "market": mm,
                    "uplift_pp": (cm["top1"] - mm["top1"]) * 100.0,
                    "proper_guard": proper(cm, mm),
                })
            ct = sum(x["candidate"]["top1"] for x in folds) / len(folds)
            mt = sum(x["market"]["top1"] for x in folds) / len(folds)
            leaderboard.append({
                "spec": spec, "ridge": ridge,
                "mean_candidate_top1": ct, "mean_market_top1": mt,
                "mean_uplift_pp": (ct - mt) * 100.0,
                "mean_candidate_logloss": sum(x["candidate"]["logloss"] for x in folds) / len(folds),
                "mean_market_logloss": sum(x["market"]["logloss"] for x in folds) / len(folds),
                "mean_candidate_rps": sum(x["candidate"]["rps"] for x in folds) / len(folds),
                "mean_market_rps": sum(x["market"]["rps"] for x in folds) / len(folds),
                "proper_guard": all(x["proper_guard"] for x in folds), "folds": folds,
            })
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(eligible, key=lambda x: (-x["mean_candidate_top1"], -x["mean_uplift_pp"], x["mean_candidate_logloss"], x["mean_candidate_rps"], SPECS.index(x["spec"]), RIDGES.index(x["ridge"])))
    leaderboard.sort(key=lambda x: (-x["mean_candidate_top1"], x["mean_candidate_logloss"], x["mean_candidate_rps"]))

    final_train = [r for r in historical if r["season"] in TRAIN_SEASONS]
    scaler = Scaler(str(selected["spec"]), leagues).fit(final_train)
    zt = scaler.apply(final_train); w = fit_offset(final_train, zt, float(selected["ridge"]))

    features = load_jsonl(GOLD_FEATURES)
    fast_features = [r for r in features if str(r["partition"]) == PART]
    if len(features) != 500 or len(fast_features) != 100:
        raise RuntimeError(f"Gold500 feature contract changed total={len(features)} fast={len(fast_features)}")
    labels = load_fast100_labels_only(GOLD_LABELS)
    fast = []
    for row in fast_features:
        idx = int(row["gold_index"]); cid = str(row["competition_id"])
        key = (TEST_SEASON, str(row["date"]), v632._token(cid, str(row["home_team"])), v632._token(cid, str(row["away_team"])))
        payload = lookups[cid].get(key)
        if payload is None:
            raise RuntimeError(f"Gold A path/crossmarket missing for {key}")
        lab = labels[idx]
        fast.append({
            "gold_index": idx, "competition_id": cid, "date": str(row["date"]),
            "home_team": str(row["home_team"]), "away_team": str(row["away_team"]),
            "market": [float(x) for x in row["market"]], "formal": [float(x) for x in row["formal"]],
            "early_1x2": payload["early_1x2"], "path_features": payload["path_features"],
            "ou_features": payload["ou_features"], "ah_features": payload["ah_features"],
            "y": int(lab["label"]), "actual_score": [int(x) for x in lab["actual_score"]],
        })
    zf = scaler.apply(fast); apply_offset(fast, zf, w)

    market_m = metrics(fast, "market"); formal_m = metrics(fast, "formal"); candidate_m = metrics(fast, "candidate")
    top1_pass = candidate_m["hits"] >= market_m["hits"] + 3 and candidate_m["hits"] >= 63
    proper_pass = proper(candidate_m, market_m); gate = bool(top1_pass and proper_pass)
    changed = []
    for r in fast:
        mp = max(range(3), key=lambda i: r["market"][i]); cp = max(range(3), key=lambda i: r["candidate"][i])
        if mp != cp:
            changed.append({
                "gold_index": r["gold_index"], "competition_id": r["competition_id"], "date": r["date"],
                "home_team": r["home_team"], "away_team": r["away_team"], "actual_result": r["y"],
                "market_pick": mp, "candidate_pick": cp,
                "market_correct": mp == r["y"], "candidate_correct": cp == r["y"],
                "market": r["market"], "candidate": r["candidate"],
                "early_1x2": r["early_1x2"],
                "ou_available": bool(r["ou_features"][-1] > 0.5),
                "ah_available": bool(r["ah_features"][-1] > 0.5),
            })

    payload = {
        "schema_version": "V6.39.0-market-path-crossmarket-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_GOLD500_FAST100_MARKET_PATH_CROSSMARKET",
        "research_hypothesis": "Opening-to-closing information flow plus O/U and Asian-handicap structure may identify closing-1X2 Top-1 errors without relearning the market baseline.",
        "governance_contract": {
            "A_FAST100_only": True, "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False, "confidence_filtering": False, "league_dropping": False,
            "seed_replacement": False, "A100_parameter_tuning": False,
            "retrospective_market_research_only": True, "CURRENT_unchanged": True,
        },
        "build_audit": build_audit,
        "architecture": {
            "offset": "softmax(log(p_closing_1x2)+W*z_market_path_crossmarket)",
            "specs": list(SPECS), "ridge_grid": list(RIDGES),
            "rolling_folds": [{"train": list(a), "validate": b} for a, b in FOLDS],
            "Pinnacle_2025_26_note": "Pinnacle-specific fields are fallback only; average-market fields are preferred because Football-Data flags Pinnacle delivery as stale after 2025-07-23.",
        },
        "historical_selection": {"selected": selected, "leaderboard": leaderboard},
        "fast100": {
            "market": market_m, "formal": formal_m, "candidate": candidate_m,
            "candidate_vs_market_top1_pp": (candidate_m["top1"] - market_m["top1"]) * 100.0,
            "required_market_top1_uplift_pp": REQUIRED_UPLIFT_PP,
            "top1_gate_pass": top1_pass, "proper_gate_pass": proper_pass, "gate_passed": gate,
            "ou_available_count": sum(int(r["ou_features"][-1] > 0.5) for r in fast),
            "ah_available_count": sum(int(r["ah_features"][-1] > 0.5) for r in fast),
            "changed_pick_count": len(changed), "changed_pick_audit": changed,
        },
        "decision": "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "historical_rows": build_audit["joined_by_season"],
        "selected": {k: selected[k] for k in ("spec", "ridge", "mean_candidate_top1", "mean_market_top1", "mean_uplift_pp", "proper_guard")},
        "fast100": {
            "market": market_m, "formal": formal_m, "candidate": candidate_m,
            "ou_available_count": payload["fast100"]["ou_available_count"],
            "ah_available_count": payload["fast100"]["ah_available_count"],
            "changed_pick_count": len(changed), "gate_passed": gate,
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
