#!/usr/bin/env python3
"""V6.11.7 research-only PIT lineup increment validation.

Question: does player/lineup information add stable 1X2 Top-1 accuracy above the
market anchor?

Strict chronology:
- target features only use SAME-SEASON lineups with kickoff strictly earlier than
  target match;
- player residual-strength estimates are updated only AFTER each target match;
- model/hyper-parameter selection uses 2021/22-2023/24 train + 2024/25 validation;
- 2025/26 is untouched final test;
- actual target XI is evaluated only as ORACLE_UPPER_BOUND_NOT_OPERATIONAL.

Historical odds in the processed CSVs do not have original quote timestamps, so
all results are RETROSPECTIVE_REFERENCE_ONLY and cannot change formal CURRENT.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import canonical_team_name, load_aliases, parse_match_date

try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
except Exception as exc:  # pragma: no cover
    raise RuntimeError("numpy/scikit-learn required") from exc

OUT = ROOT / "manifests" / "v6_1x2_pit_lineup_increment_v6117_status.json"
COMPETITIONS = [
    "ENG_PremierLeague",
    "GER_Bundesliga",
    "ITA_SerieA",
    "FRA_Ligue1",
    "ESP_LaLiga",
]
TRAIN_SEASONS = {"2021/22", "2022/23", "2023/24"}
VALID_SEASON = "2024/25"
TEST_SEASON = "2025/26"
DIRECTIONS = ("home", "draw", "away")
LABEL = {"home": 0, "draw": 1, "away": 2}
INV_LABEL = {0: "home", 1: "draw", 2: "away"}
DECAY = 0.78
LOOKBACK = 8
LONG_LOOKBACK = 12
MIN_PRIOR_LINEUPS = 3
SHRINK = 10.0

OPENING_TRIPLETS = (
    ("PSH", "PSD", "PSA", "Pinnacle_opening"),
    ("B365H", "B365D", "B365A", "Bet365_opening"),
    ("AvgH", "AvgD", "AvgA", "Average_opening"),
)
CLOSING_TRIPLETS = (
    ("PSCH", "PSCD", "PSCA", "Pinnacle_closing"),
    ("B365CH", "B365CD", "B365CA", "Bet365_closing"),
    ("AvgCH", "AvgCD", "AvgCA", "Average_closing"),
)


def _f(v: Any) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if x > 1.0 and math.isfinite(x) else None


def _devig(h: float, d: float, a: float) -> tuple[float, float, float]:
    q = np.array([1.0 / h, 1.0 / d, 1.0 / a], dtype=float)
    q /= q.sum()
    return float(q[0]), float(q[1]), float(q[2])


def _odds(raw: dict[str, str], specs) -> tuple[tuple[float, float, float], str] | None:
    for hc, dc, ac, label in specs:
        h, d, a = _f(raw.get(hc)), _f(raw.get(dc)), _f(raw.get(ac))
        if h is not None and d is not None and a is not None:
            return _devig(h, d, a), label
    return None


def _actual(raw: dict[str, str]) -> str | None:
    ftr = str(raw.get("FTR") or raw.get("result") or "").strip().upper()
    if ftr == "H": return "home"
    if ftr == "D": return "draw"
    if ftr == "A": return "away"
    try:
        hg = int(float(raw.get("FTHG", "")))
        ag = int(float(raw.get("FTAG", "")))
    except Exception:
        return None
    return "home" if hg > ag else "away" if ag > hg else "draw"


def _load_matches() -> list[dict[str, Any]]:
    aliases = load_aliases()
    out: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for cid in COMPETITIONS:
        d = ROOT / "processed" / cid
        if not d.exists():
            continue
        for path in sorted(d.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                for raw0 in csv.DictReader(fh):
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    season = str(raw.get("season") or raw.get("Season") or "").strip()
                    if season not in TRAIN_SEASONS | {VALID_SEASON, TEST_SEASON}:
                        continue
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam") or not raw.get("Date"):
                        continue
                    act = _actual(raw)
                    op = _odds(raw, OPENING_TRIPLETS)
                    cl = _odds(raw, CLOSING_TRIPLETS)
                    if act is None or op is None or cl is None:
                        continue
                    try:
                        dt = parse_match_date(raw["Date"], season)
                    except Exception:
                        continue
                    home = canonical_team_name(cid, raw["HomeTeam"], aliases)
                    away = canonical_team_name(cid, raw["AwayTeam"], aliases)
                    key = (cid, season, dt.isoformat(), home, away)
                    out[key] = {
                        "competition_id": cid,
                        "season": season,
                        "date": dt.isoformat(),
                        "home": home,
                        "away": away,
                        "actual": act,
                        "opening": op[0],
                        "opening_provider": op[1],
                        "closing": cl[0],
                        "closing_provider": cl[1],
                    }
    return sorted(out.values(), key=lambda r: (r["date"], r["competition_id"], r["home"], r["away"]))


def _load_lineups(cid: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    aliases = load_aliases()
    path = ROOT / "lineups" / cid / "historical_lineups.jsonl"
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            season = str(r.get("season") or "").strip()
            if season not in TRAIN_SEASONS | {VALID_SEASON, TEST_SEASON}:
                continue
            ko = str(r.get("kickoff_utc") or "")
            try:
                date = datetime.fromisoformat(ko.replace("Z", "+00:00")).date().isoformat()
            except Exception:
                continue
            team_raw = str(r.get("team") or r.get("team_name") or "").strip()
            starters = r.get("starters") or []
            if not team_raw or not isinstance(starters, list) or len(starters) < 10:
                continue
            team = canonical_team_name(cid, team_raw, aliases)
            names = tuple(sorted({str(x).strip() for x in starters if str(x).strip()}))
            if len(names) < 10:
                continue
            key = (season, date, team)
            # Prefer a full 11-player record when duplicates exist.
            if key not in result or len(names) > len(result[key]["starters"]):
                result[key] = {"starters": names, "formation": r.get("formation")}
    return result


def _predicted_xi(hist: list[tuple[str, tuple[str, ...]]]) -> tuple[tuple[str, ...], dict[str, float]] | None:
    if len(hist) < MIN_PRIOR_LINEUPS:
        return None
    recent = hist[-LOOKBACK:]
    scores: dict[str, float] = defaultdict(float)
    denom = 0.0
    # newest lineup gets weight 1, then decay backward
    for lag, (_, xi) in enumerate(reversed(recent)):
        w = DECAY ** lag
        denom += w
        for p in xi:
            scores[p] += w
    probs = {p: s / denom for p, s in scores.items()}
    top = tuple(p for p, _ in sorted(probs.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:11])
    if len(top) < 10:
        return None
    return top, probs


def _team_features(hist: list[tuple[str, tuple[str, ...]]], player_sum, player_n, oracle_xi=None) -> dict[str, float] | None:
    pred = _predicted_xi(hist)
    if pred is None:
        return None
    xi, probs = pred
    recent = hist[-LONG_LOOKBACK:]
    last_xi = set(recent[-1][1])
    prev_xi = set(recent[-2][1]) if len(recent) >= 2 else last_xi
    long_counts = Counter(p for _, x in recent for p in x)
    core = {p for p, n in long_counts.items() if n / max(1, len(recent)) >= 0.60}
    pred_set = set(xi)
    certainty = sum(probs.get(p, 0.0) for p in xi) / max(1, len(xi))
    continuity = len(pred_set & last_xi) / 11.0
    prior_churn = 1.0 - len(last_xi & prev_xi) / 11.0
    missing_core_last = float(len(core - last_xi))
    newcomers = float(sum(1 for p in xi if long_counts.get(p, 0) <= 1))
    impacts = [player_sum.get(p, 0.0) / (player_n.get(p, 0) + SHRINK) for p in xi]
    pred_impact = float(sum(impacts) / len(impacts)) if impacts else 0.0
    out = {
        "certainty": certainty,
        "continuity": continuity,
        "prior_churn": prior_churn,
        "missing_core_last": missing_core_last,
        "newcomers": newcomers,
        "pred_impact": pred_impact,
        "core_count": float(len(core)),
    }
    if oracle_xi is not None:
        oracle = set(oracle_xi)
        oimp = [player_sum.get(p, 0.0) / (player_n.get(p, 0) + SHRINK) for p in oracle]
        out.update({
            "oracle_overlap_pred": len(oracle & pred_set) / 11.0,
            "oracle_overlap_last": len(oracle & last_xi) / 11.0,
            "oracle_core_missing": float(len(core - oracle)),
            "oracle_impact": float(sum(oimp) / len(oimp)) if oimp else 0.0,
        })
    return out


def _market_vec(p: tuple[float, float, float]) -> list[float]:
    order = sorted(p, reverse=True)
    return [p[0], p[1], p[2], order[0], order[0] - order[1]]


def _row_features(r, hf, af, market_kind: str, oracle: bool) -> list[float]:
    p = r[market_kind]
    feat = _market_vec(p)
    keys = ["certainty", "continuity", "prior_churn", "missing_core_last", "newcomers", "pred_impact", "core_count"]
    if oracle:
        keys += ["oracle_overlap_pred", "oracle_overlap_last", "oracle_core_missing", "oracle_impact"]
    for k in keys:
        feat += [hf[k], af[k], hf[k] - af[k]]
    # one-hot competition
    feat += [1.0 if r["competition_id"] == cid else 0.0 for cid in COMPETITIONS]
    return feat


def _market_pick(p):
    return DIRECTIONS[int(np.argmax(np.asarray(p, dtype=float)))]


def _acc(rows, picker):
    hits = sum(1 for r in rows if picker(r) == r["actual"])
    return {"count": len(rows), "hits": hits, "accuracy": hits / len(rows) if rows else None}


def _build_dataset(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lineups = {cid: _load_lineups(cid) for cid in COMPETITIONS}
    team_hist: dict[tuple[str, str, str], list[tuple[str, tuple[str, ...]]]] = defaultdict(list)
    psum: dict[tuple[str, str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    pn: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    out = []
    for r in matches:
        cid, season, date = r["competition_id"], r["season"], r["date"]
        hk = (season, date, r["home"])
        ak = (season, date, r["away"])
        hactual = lineups[cid].get(hk)
        aactual = lineups[cid].get(ak)
        th = (cid, season, r["home"])
        ta = (cid, season, r["away"])
        if hactual and aactual:
            hf = _team_features(team_hist[th], psum[th], pn[th], hactual["starters"])
            af = _team_features(team_hist[ta], psum[ta], pn[ta], aactual["starters"])
            if hf is not None and af is not None:
                item = dict(r)
                item["estimated_opening_features"] = _row_features(r, hf, af, "opening", False)
                item["estimated_closing_features"] = _row_features(r, hf, af, "closing", False)
                item["oracle_opening_features"] = _row_features(r, hf, af, "opening", True)
                item["home_lineup_overlap"] = hf["oracle_overlap_pred"]
                item["away_lineup_overlap"] = af["oracle_overlap_pred"]
                out.append(item)

            # Update player impact only after target prediction features are frozen.
            y_home = 1.0 if r["actual"] == "home" else 0.5 if r["actual"] == "draw" else 0.0
            y_away = 1.0 - y_home
            op = r["opening"]
            e_home = op[0] + 0.5 * op[1]
            e_away = op[2] + 0.5 * op[1]
            hres, ares = y_home - e_home, y_away - e_away
            for p in hactual["starters"]:
                psum[th][p] += hres; pn[th][p] += 1
            for p in aactual["starters"]:
                psum[ta][p] += ares; pn[ta][p] += 1
            team_hist[th].append((date, hactual["starters"]))
            team_hist[ta].append((date, aactual["starters"]))
    return out


def _fit_eval(rows, feature_key: str):
    tr = [r for r in rows if r["season"] in TRAIN_SEASONS]
    va = [r for r in rows if r["season"] == VALID_SEASON]
    te = [r for r in rows if r["season"] == TEST_SEASON]
    Xtr = np.asarray([r[feature_key] for r in tr], dtype=float)
    ytr = np.asarray([LABEL[r["actual"]] for r in tr], dtype=int)
    Xva = np.asarray([r[feature_key] for r in va], dtype=float)
    yva = np.asarray([LABEL[r["actual"]] for r in va], dtype=int)
    Xte = np.asarray([r[feature_key] for r in te], dtype=float)
    yte = np.asarray([LABEL[r["actual"]] for r in te], dtype=int)
    if min(len(tr), len(va), len(te)) < 100:
        raise RuntimeError(f"insufficient split for {feature_key}: {len(tr)}/{len(va)}/{len(te)}")
    board = []
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0):
        pipe = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=3000))
        pipe.fit(Xtr, ytr)
        p = pipe.predict_proba(Xva)
        pred = p.argmax(axis=1)
        acc = float((pred == yva).mean())
        board.append((acc, -C, C))
    board.sort(reverse=True)
    C = board[0][2]
    # Refit on train+validation after C selection; test remains untouched.
    Xtv = np.vstack([Xtr, Xva]); ytv = np.concatenate([ytr, yva])
    model = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=3000))
    model.fit(Xtv, ytv)
    pte = model.predict_proba(Xte)
    pred = pte.argmax(axis=1)
    acc = float((pred == yte).mean())
    # Conservative override rule: otherwise retain market pick. Threshold chosen on validation.
    selector = make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=3000))
    selector.fit(Xtr, ytr)
    pva = selector.predict_proba(Xva)
    market_kind = "opening" if "opening" in feature_key else "closing"
    best_gate = None
    for advantage in (0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12):
        hits = 0; overrides = 0
        for i, r in enumerate(va):
            mp = r[market_kind]
            m_idx = int(np.argmax(mp)); q_idx = int(np.argmax(pva[i]))
            pick = m_idx
            if q_idx != m_idx and pva[i][q_idx] - mp[m_idx] >= advantage:
                pick = q_idx; overrides += 1
            hits += int(pick == yva[i])
        a = hits / len(va)
        cand = (a, -overrides, advantage)
        if best_gate is None or cand > best_gate:
            best_gate = cand
    gate = best_gate[2]
    hits = 0; overrides = 0
    for i, r in enumerate(te):
        mp = r[market_kind]
        m_idx = int(np.argmax(mp)); q_idx = int(np.argmax(pte[i]))
        pick = m_idx
        if q_idx != m_idx and pte[i][q_idx] - mp[m_idx] >= gate:
            pick = q_idx; overrides += 1
        hits += int(pick == yte[i])
    return {
        "selected_C": C,
        "validation_best_accuracy": board[0][0],
        "test_direct_model": {"count": len(te), "hits": int((pred == yte).sum()), "accuracy": acc},
        "override_gate": {"advantage": gate, "test_count": len(te), "test_hits": hits, "test_accuracy": hits / len(te), "overrides": overrides},
    }


def _selective(rows, market_kind: str):
    out = {}
    for t in (0.56, 0.58, 0.60):
        sel = [r for r in rows if max(r[market_kind]) >= t]
        hits = sum(_market_pick(r[market_kind]) == r["actual"] for r in sel)
        out[f"p_ge_{t:.2f}"] = {"count": len(sel), "coverage": len(sel)/len(rows) if rows else 0.0, "hits": hits, "accuracy": hits/len(sel) if sel else None}
    return out


def main() -> int:
    matches = _load_matches()
    rows = _build_dataset(matches)
    test = [r for r in rows if r["season"] == TEST_SEASON]
    if len(test) < 500:
        raise RuntimeError(f"insufficient lineup-matched 2025/26 rows: {len(test)}")
    opening_market = _acc(test, lambda r: _market_pick(r["opening"]))
    closing_market = _acc(test, lambda r: _market_pick(r["closing"]))
    estimated_open = _fit_eval(rows, "estimated_opening_features")
    estimated_close = _fit_eval(rows, "estimated_closing_features")
    oracle_open = _fit_eval(rows, "oracle_opening_features")
    payload = {
        "schema_version": "V6.11.7-pit-lineup-increment-1x2-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "market_data_classification": "RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "governance": {
            "research_only": True,
            "same_season_prior_lineups_only": True,
            "target_actual_lineup_excluded_from_operational_features": True,
            "oracle_actual_xi_is_upper_bound_not_operational": True,
            "test_season_never_used_for_model_or_gate_selection": True,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "current_rule_change": False,
        },
        "sample": {
            "all_market_matches": len(matches),
            "lineup_feature_rows": len(rows),
            "train_rows": sum(r["season"] in TRAIN_SEASONS for r in rows),
            "validation_rows": sum(r["season"] == VALID_SEASON for r in rows),
            "test_rows": len(test),
            "competitions": COMPETITIONS,
            "mean_test_predicted_xi_overlap": float(np.mean([(r["home_lineup_overlap"] + r["away_lineup_overlap"])/2 for r in test])),
        },
        "newer_season_test": {
            "opening_market": opening_market,
            "closing_market": closing_market,
            "opening_plus_pit_estimated_lineup": estimated_open,
            "closing_plus_pit_estimated_lineup": estimated_close,
            "opening_plus_oracle_actual_xi_upper_bound": oracle_open,
            "opening_selective_market": _selective(test, "opening"),
            "closing_selective_market": _selective(test, "closing"),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["newer_season_test"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
