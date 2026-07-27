#!/usr/bin/env python3
"""V6.55.0 online bookmaker-expert aggregation for Full500 1X2 research.

Each individual closing bookmaker is treated as a sleeping expert. Weights evolve
strictly from prior completed match dates via specialist Hedge on de-vigged 1X2
log-loss. All matches on a calendar date are predicted before any result from that date
updates expert weights.

Research design:
- individual C-suffixed bookmaker triplets only; Avg/Max/BbAv/BbMx excluded;
- candidate pool uses rows with >=2 complete individual books;
- modes: one global expert state or independent league expert states;
- eta and market-blend alpha selected on 2023/24 only after warming on 2022/23;
- fixed mode/eta/alpha validated on untouched 2024/25, after warming on
  2022/23+2023/24 in chronological order;
- A_FAST100 opens only if selection uplift >0, holdout uplift >=+0.5pp, and proper
  log-loss/RPS guard passes;
- B300/C100 never read; CURRENT V5.0.1 unchanged; formal_weight=0.

The weighted expert pool uses geometric pooling. Specialist Hedge updates an available
expert's log-weight by -eta * (expert_logloss - aggregate_logloss), so experts are not
penalized merely for having broader historical coverage.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import v6_market_residual_fusion_v620 as marketmod  # noqa: E402

OUT = ROOT / "manifests" / "v6_online_bookmaker_experts_full500_v6550_status.json"
FEATURES = ROOT / "manifests" / "full500_v6493" / "full500_features_v6493.jsonl"
LABELS = ROOT / "manifests" / "full500_v6493" / "full500_development_labels_v6493.jsonl"
PART = "A_FAST100"
EPS = 1e-12
SEASONS = ("2022/23", "2023/24", "2024/25")
MODES = ("global", "league")
ETAS = (0.01, 0.03, 0.10, 0.30, 0.70)
ALPHAS = (0.25, 0.50, 0.75, 1.00)
PROPER_TOL = 0.01
HOLDOUT_REQUIRED_UPLIFT_PP = 0.5
EXCLUDE_PREFIXES = {"Avg", "Max", "BbAv", "BbMx"}


def fnum(row: dict[str, str], key: str) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 1.0 else None


def devig(h: float, d: float, a: float) -> np.ndarray:
    inv = np.asarray([1.0/h, 1.0/d, 1.0/a], dtype=float)
    return inv / inv.sum()


def books(raw: dict[str, str]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for key in raw:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_PREFIXES:
            continue
        dk, ak = prefix + "CD", prefix + "CA"
        if dk not in raw or ak not in raw:
            continue
        h, d, a = fnum(raw, key), fnum(raw, dk), fnum(raw, ak)
        if h is None or d is None or a is None:
            continue
        out[prefix] = devig(h, d, a)
    return out


def raw_lookup(cid: str) -> dict[tuple[str, str, str, str], dict[str, str]]:
    out = {}
    for path in sorted((ROOT / "processed" / cid).glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as h:
            for raw in csv.DictReader(h):
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season not in SEASONS and season != "2025/26":
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                except Exception:
                    continue
                home = v632._token(cid, str(raw.get("HomeTeam") or ""))
                away = v632._token(cid, str(raw.get("AwayTeam") or ""))
                if home and away:
                    out.setdefault((season, date, home, away), raw)
    return out


def build_historical() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base, base_audit, _ = v632._build_rows()
    cids = sorted({str(r["competition_id"]) for r in base})
    lookups = {cid: raw_lookup(cid) for cid in cids}
    rows = []
    misses = Counter()
    prefix_counts = Counter()
    for r in base:
        season = str(r["season"])
        if season not in SEASONS:
            continue
        cid = str(r["competition_id"])
        key = (season, str(r["date"]), v632._token(cid, str(r["home_team"])), v632._token(cid, str(r["away_team"])))
        raw = lookups[cid].get(key)
        if raw is None:
            misses["raw_join"] += 1
            continue
        panel = books(raw)
        if len(panel) < 2:
            misses["lt2_books"] += 1
            continue
        prefix_counts.update(panel)
        rows.append({
            "competition_id": cid,
            "season": season,
            "date": str(r["date"]),
            "home_team": str(r["home_team"]),
            "away_team": str(r["away_team"]),
            "market": np.asarray(r["market"], dtype=float),
            "books": panel,
            "y": int(r["y"]),
        })
    rows.sort(key=lambda z: (z["date"], z["competition_id"], z["home_team"], z["away_team"]))
    return rows, {
        "base_audit": base_audit,
        "misses": dict(misses),
        "by_season": dict(Counter(r["season"] for r in rows)),
        "book_prefix_counts": dict(prefix_counts),
    }


def state_key(row: dict[str, Any], mode: str) -> str:
    return "GLOBAL" if mode == "global" else str(row["competition_id"])


def weighted_geo(panel: dict[str, np.ndarray], logw: dict[str, float]) -> np.ndarray:
    names = sorted(panel)
    lw = np.asarray([float(logw.get(n, 0.0)) for n in names], dtype=float)
    lw -= lw.max()
    w = np.exp(lw)
    w /= w.sum()
    mat = np.asarray([panel[n] for n in names], dtype=float)
    g = np.exp(np.sum(w[:, None] * np.log(np.clip(mat, EPS, 1.0)), axis=0))
    return g / g.sum()


def blend(market: np.ndarray, expert: np.ndarray, alpha: float) -> np.ndarray:
    z = (1.0-alpha)*np.log(np.clip(market, EPS, 1.0)) + alpha*np.log(np.clip(expert, EPS, 1.0))
    z -= z.max()
    p = np.exp(z)
    return p / p.sum()


def run_online(rows: list[dict[str, Any]], mode: str, eta: float, alpha: float, score_season: str) -> dict[str, Any]:
    states: dict[str, dict[str, float]] = defaultdict(dict)
    scored: list[tuple[int, np.ndarray, np.ndarray]] = []
    date_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["season"] <= score_season:  # season strings are only used after explicit filter below
            pass
        date_groups[r["date"]].append(r)

    # Explicitly process only seasons up through score_season in chronological date order.
    allowed = set()
    for s in SEASONS:
        allowed.add(s)
        if s == score_season:
            break

    for date in sorted(date_groups):
        day = [r for r in date_groups[date] if r["season"] in allowed]
        if not day:
            continue
        pending = []
        for r in day:
            sk = state_key(r, mode)
            expert = weighted_geo(r["books"], states[sk])
            candidate = blend(r["market"], expert, alpha)
            if r["season"] == score_season:
                scored.append((int(r["y"]), np.asarray(r["market"], float), candidate))
            pending.append((r, sk, expert))
        # Same-day safe: update only after all predictions for the date are frozen.
        for r, sk, expert in pending:
            y = int(r["y"])
            agg_loss = -math.log(max(EPS, float(expert[y])))
            st = states[sk]
            for name, p in r["books"].items():
                loss = -math.log(max(EPS, float(p[y])))
                st[name] = float(st.get(name, 0.0) - eta * (loss - agg_loss))
            if st:
                mean_lw = sum(st.values()) / len(st)
                for name in list(st):
                    st[name] -= mean_lw

    if not scored:
        raise RuntimeError(f"no scored rows for {score_season}")
    y = np.asarray([z[0] for z in scored], dtype=int)
    market = np.asarray([z[1] for z in scored], dtype=float)
    cand = np.asarray([z[2] for z in scored], dtype=float)
    return {"market": metrics(y, market), "candidate": metrics(y, cand)}


def metrics(y: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    n = len(y)
    picks = probs.argmax(axis=1)
    hits = int(np.sum(picks == y))
    brier = float(np.mean(np.sum((probs - np.eye(3)[y])**2, axis=1)))
    logloss = float(-np.mean(np.log(np.clip(probs[np.arange(n), y], EPS, 1.0))))
    c1 = probs[:,0] - (y == 0)
    c2 = probs[:,0] + probs[:,1] - (y <= 1)
    rps = float(np.mean((c1*c1 + c2*c2)/2.0))
    return {
        "count": n, "hits": hits, "top1": hits/n, "brier": brier,
        "logloss": logloss, "rps": rps,
        "predicted_counts": dict(Counter(str(int(x)) for x in picks)),
        "actual_counts": dict(Counter(str(int(x)) for x in y)),
    }


def historical_select(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    board = []
    for mode in MODES:
        for eta in ETAS:
            for alpha in ALPHAS:
                res = run_online(rows, mode, eta, alpha, "2023/24")
                m, c = res["market"], res["candidate"]
                board.append({
                    "mode": mode, "eta": eta, "alpha": alpha,
                    "selection": {"market": m, "candidate": c,
                                  "uplift_pp": 100.0*(c["top1"]-m["top1"]),
                                  "logloss_delta": c["logloss"]-m["logloss"],
                                  "rps_delta": c["rps"]-m["rps"]},
                })
    board.sort(key=lambda z: (z["selection"]["uplift_pp"], -z["selection"]["logloss_delta"], -z["selection"]["rps_delta"]), reverse=True)
    return board[0], board


def load_a100() -> tuple[list[dict[str, Any]], np.ndarray]:
    feats = [json.loads(x) for x in FEATURES.read_text(encoding="utf-8").splitlines() if x.strip()]
    feats = [r for r in feats if r.get("partition") == PART]
    if len(feats) != 100:
        raise RuntimeError(f"expected 100 A features, got {len(feats)}")
    labels = []
    with LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            r = json.loads(h.readline())
            if r.get("partition") != PART or int(r["full_index"]) != len(labels):
                raise RuntimeError("A100 label contract changed")
            labels.append(int(r["label"]))
    cids = sorted({str(r["competition_id"]) for r in feats})
    lookups = {cid: raw_lookup(cid) for cid in cids}
    rows = []
    for f in sorted(feats, key=lambda r: int(r["full_index"])):
        cid = str(f["competition_id"])
        key = (str(f["season"]), str(f["date"]), v632._token(cid, str(f["home_team"])), v632._token(cid, str(f["away_team"])))
        raw = lookups[cid].get(key)
        panel = books(raw) if raw is not None else {}
        if len(panel) < 2:
            raise RuntimeError(f"A100 bookmaker panel missing at {f['full_index']}")
        rows.append({
            "competition_id": cid, "season": str(f["season"]), "date": str(f["date"]),
            "home_team": str(f["home_team"]), "away_team": str(f["away_team"]),
            "market": np.asarray(f["market"], dtype=float), "books": panel,
            "y": int(labels[int(f["full_index"])]),
        })
    return rows, np.asarray(labels, dtype=int)


def score_a100_with_warm_history(hist: list[dict[str, Any]], arows: list[dict[str, Any]], mode: str, eta: float, alpha: float) -> dict[str, Any]:
    # Re-run all historical dates through 2024/25 to build the frozen state, then score
    # A100 chronologically during 2025/26 with same-day-safe updates inside the A set.
    states: dict[str, dict[str, float]] = defaultdict(dict)
    combined = sorted(hist + arows, key=lambda z: (z["date"], z["competition_id"], z["home_team"], z["away_team"]))
    a_ids = {id(r) for r in arows}
    date_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in combined:
        date_groups[r["date"]].append(r)
    scored = []
    for date in sorted(date_groups):
        pending = []
        for r in date_groups[date]:
            sk = state_key(r, mode)
            expert = weighted_geo(r["books"], states[sk])
            candidate = blend(r["market"], expert, alpha)
            if id(r) in a_ids:
                scored.append((int(r["y"]), np.asarray(r["market"], float), candidate))
            pending.append((r, sk, expert))
        for r, sk, expert in pending:
            y = int(r["y"])
            agg_loss = -math.log(max(EPS, float(expert[y])))
            st = states[sk]
            for name, p in r["books"].items():
                loss = -math.log(max(EPS, float(p[y])))
                st[name] = float(st.get(name, 0.0) - eta*(loss-agg_loss))
            if st:
                mean_lw = sum(st.values())/len(st)
                for name in list(st):
                    st[name] -= mean_lw
    y = np.asarray([x[0] for x in scored], dtype=int)
    market = np.asarray([x[1] for x in scored], dtype=float)
    cand = np.asarray([x[2] for x in scored], dtype=float)
    if len(y) != 100:
        raise RuntimeError(f"A100 scoring count {len(y)}")
    return {"market": metrics(y, market), "candidate": metrics(y, cand)}


def main() -> int:
    hist, audit = build_historical()
    chosen, board = historical_select(hist)
    hold = run_online(hist, str(chosen["mode"]), float(chosen["eta"]), float(chosen["alpha"]), "2024/25")
    hm, hc = hold["market"], hold["candidate"]
    hold_uplift = 100.0*(hc["top1"]-hm["top1"])
    hold_log_delta = hc["logloss"]-hm["logloss"]
    hold_rps_delta = hc["rps"]-hm["rps"]
    selection = chosen["selection"]
    proper = (selection["logloss_delta"] <= PROPER_TOL and selection["rps_delta"] <= PROPER_TOL and hold_log_delta <= PROPER_TOL and hold_rps_delta <= PROPER_TOL)
    hist_gate = bool(selection["uplift_pp"] > 0.0 and hold_uplift >= HOLDOUT_REQUIRED_UPLIFT_PP - 1e-12 and proper)

    payload: dict[str, Any] = {
        "schema_version": "V6.55.0-online-bookmaker-experts-full500-r1",
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "governance": {
            "same_date_predictions_before_updates": True,
            "weights_use_prior_completed_results_only": True,
            "selection_season": "2023/24", "holdout_season": "2024/25",
            "A100_values_used_for_selection": False,
            "B_CONFIRM300_labels_read": False, "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": audit,
        "grid": {"modes": MODES, "etas": ETAS, "alphas": ALPHAS},
        "selected": chosen,
        "holdout_2024_25": {
            "market": hm, "candidate": hc, "uplift_pp": hold_uplift,
            "logloss_delta": hold_log_delta, "rps_delta": hold_rps_delta,
        },
        "proper_guard": proper,
        "historical_gate": hist_gate,
        "selection_leaderboard_top10": board[:10],
    }
    if not hist_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_HOLDOUT_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    arows, _ = load_a100()
    ares = score_a100_with_warm_history(hist, arows, str(chosen["mode"]), float(chosen["eta"]), float(chosen["alpha"]))
    am, ac = ares["market"], ares["candidate"]
    au = 100.0*(ac["top1"]-am["top1"])
    gate = {
        "required_candidate_hits": 63, "required_uplift_vs_market_pp": 3.0,
        "market_hits": am["hits"], "candidate_hits": ac["hits"], "uplift_vs_market_pp": au,
        "top1_gate": ac["hits"] >= 63, "uplift_gate": au >= 3.0-1e-12,
        "proper_score_guard": ac["logloss"] <= am["logloss"]+PROPER_TOL and ac["rps"] <= am["rps"]+PROPER_TOL,
    }
    gate["A_FAST100_passed"] = bool(gate["top1_gate"] and gate["uplift_gate"] and gate["proper_score_guard"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_GATE", "market": am, "candidate": ac, "gate": gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
