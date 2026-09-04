#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
from collections import defaultdict, deque
from pathlib import Path

DECAY = 0.78
SMOOTH = 0.50
LOOKBACK_POOL = 20
FEATURE_LOOKBACK = 8
MAX_CANDIDATES = 32
TARGET_SUM = 11.0
EPS = 1e-8
LEAGUES = ("Bundesliga", "EPL", "La liga", "Ligue 1", "Serie A")
TARGET_SEASONS = (2020, 2021, 2022)
EXPECTED_PER_SEASON = 1826
EXPECTED_TARGET_N = 5478
EXPECTED_DB_SHA256 = "f102eae39b4036a4c24e5b75b9cee551064cf1e7d4fd028966cd62a5784d8681"
PASS = "HISTORICAL_PIT_XI_DIAGNOSTIC_PASS_1X2_BRIDGE_ALLOWED"
REJECT = "HISTORICAL_PIT_XI_DIAGNOSTIC_REJECT_NO_1X2_BRIDGE"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def project_sum(probs: list[float], target_sum: float = TARGET_SUM) -> list[float]:
    n = len(probs)
    if n < int(target_sum):
        raise ValueError(f"insufficient candidates: {n}")
    if n == int(target_sum):
        return [1.0] * n
    p = [min(1.0 - EPS, max(EPS, float(x))) for x in probs]
    logits = [math.log(x / (1.0 - x)) for x in p]
    lo, hi = -30.0, 30.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if sum(sigmoid(z + mid) for z in logits) > target_sum:
            hi = mid
        else:
            lo = mid
    shift = (lo + hi) / 2.0
    q = [sigmoid(z + shift) for z in logits]
    if abs(sum(q) - target_sum) > 1e-7:
        raise ValueError("projection drift")
    return q


def is_starter(position: object) -> bool:
    if position is None:
        return False
    return str(position).strip().lower() != "sub"


def raw_probability(hist: deque, player_id: int) -> float:
    recent = list(hist)[-FEATURE_LOOKBACK:]
    ws = 0.0
    wsum = 0.0
    for lag, entry in enumerate(reversed(recent)):
        w = DECAY ** lag
        rec = entry["players"].get(player_id)
        ws += w * (1.0 if rec and rec["start"] else 0.0)
        wsum += w
    return float((ws + SMOOTH) / (wsum + 2.0 * SMOOTH)) if wsum else 0.5


def candidate_stats(hist: deque) -> dict[int, tuple[str, float]]:
    out: dict[int, tuple[str, float]] = {}
    for entry in hist:
        day = entry["day"]
        for pid, rec in entry["players"].items():
            old = out.get(pid)
            mins = float(rec.get("minutes", 0.0))
            if old is None:
                out[pid] = (day, mins)
            else:
                out[pid] = (max(old[0], day), old[1] + mins)
    return out


def predict_side(hist: deque) -> dict | None:
    if not hist:
        return None
    stats = candidate_stats(hist)
    if len(stats) < 11:
        return None
    ranked_candidates = sorted(stats, key=lambda pid: (-int(stats[pid][0].replace("-", "")), -stats[pid][1], pid))[:MAX_CANDIDATES]
    raw = [raw_probability(hist, pid) for pid in ranked_candidates]
    projected = project_sum(raw)
    rows = [
        {"player_id": int(pid), "p_start": float(p), "prior_minutes": float(stats[pid][1]), "last_seen_day": stats[pid][0]}
        for pid, p in zip(ranked_candidates, projected)
    ]
    rows.sort(key=lambda r: (-r["p_start"], -r["prior_minutes"], r["player_id"]))
    expected = [r["player_id"] for r in rows[:11]]
    last_xi = sorted(int(pid) for pid, rec in hist[-1]["players"].items() if rec["start"])
    return {
        "expected_xi": expected,
        "p_start": {int(r["player_id"]): float(r["p_start"]) for r in rows},
        "candidate_n": len(rows),
        "last_completed_xi": last_xi if len(last_xi) == 11 else None,
    }


def load_fixtures(con: sqlite3.Connection) -> list[dict]:
    marks = ",".join("?" for _ in LEAGUES)
    rows = con.execute(
        f"select id,fid,h_id,a_id,date,league,season from general_game_stats where league in ({marks}) order by date,id",
        LEAGUES,
    ).fetchall()
    return [
        {
            "id": int(r[0]), "fid": int(r[1]), "h_id": int(r[2]), "a_id": int(r[3]),
            "date": str(r[4]), "day": str(r[4])[:10], "league": str(r[5]), "season": int(r[6]),
        }
        for r in rows
    ]


def read_day_lineups(con: sqlite3.Connection, fixture_ids: list[int]) -> dict[tuple[int, int], dict]:
    if not fixture_ids:
        return {}
    out: dict[tuple[int, int], dict] = {}
    step = 500
    for i in range(0, len(fixture_ids), step):
        ids = fixture_ids[i:i + step]
        marks = ",".join("?" for _ in ids)
        q = f"select match_id,team_id,player_id,position,time from lineup_stats where match_id in ({marks}) order by match_id,team_id,player_id"
        for match_id, team_id, player_id, position, minutes in con.execute(q, ids):
            key = (int(match_id), int(team_id))
            side = out.setdefault(key, {"players": {}})
            side["players"][int(player_id)] = {
                "start": is_starter(position),
                "minutes": float(minutes or 0.0),
                "position": None if position is None else str(position),
            }
    for side in out.values():
        side["starters"] = sorted(pid for pid, rec in side["players"].items() if rec["start"])
    return out


def overlap(a: list[int], b: list[int]) -> int:
    return len(set(a) & set(b))


def binary_scores(pmap: dict[int, float], actual: list[int]) -> tuple[float, float, int]:
    ids = sorted(set(pmap) | set(actual))
    yset = set(actual)
    ll = 0.0
    br = 0.0
    for pid in ids:
        p = min(1.0 - EPS, max(EPS, float(pmap.get(pid, EPS))))
        y = 1.0 if pid in yset else 0.0
        ll += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
        br += (p - y) ** 2
    return ll / len(ids), br / len(ids), len(ids)


def chronological_blocks(rows: list[dict], k: int = 4) -> list[list[dict]]:
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: (r["day"], r["fid"], r["team_id"]))
    unique_days = sorted({r["day"] for r in rows})
    buckets: list[list[str]] = [[] for _ in range(k)]
    for i, day in enumerate(unique_days):
        buckets[min(k - 1, (i * k) // len(unique_days))].append(day)
    return [[r for r in rows if r["day"] in set(days)] for days in buckets if days]


def aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    gains = [r["overlap"] - r["last_xi_overlap"] for r in rows if r["last_xi_overlap"] is not None]
    return {
        "n": len(rows),
        "mean_xi_overlap": float(statistics.fmean(r["overlap"] for r in rows)),
        "median_xi_overlap": float(statistics.median(r["overlap"] for r in rows)),
        "exact_11_rate": float(sum(r["overlap"] == 11 for r in rows) / len(rows)),
        "mean_unseen_actual_starters": float(statistics.fmean(r["unseen_actual_starters"] for r in rows)),
        "last_xi_n": len(gains),
        "last_xi_mean_overlap": float(statistics.fmean(r["last_xi_overlap"] for r in rows if r["last_xi_overlap"] is not None)) if gains else None,
        "mean_overlap_gain_vs_last_xi": float(statistics.fmean(gains)) if gains else None,
        "starter_probability_logloss": float(statistics.fmean(r["prob_logloss"] for r in rows)),
        "starter_probability_brier": float(statistics.fmean(r["prob_brier"] for r in rows)),
    }


def run(db_path: Path, out_dir: Path) -> dict:
    if sha256_file(db_path) != EXPECTED_DB_SHA256:
        raise RuntimeError("database sha256 drift")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fixtures = load_fixtures(con)
    target = [r for r in fixtures if r["season"] in TARGET_SEASONS]
    counts = {s: sum(r["season"] == s for r in target) for s in TARGET_SEASONS}
    if len(target) != EXPECTED_TARGET_N or any(counts[s] != EXPECTED_PER_SEASON for s in TARGET_SEASONS):
        raise RuntimeError(f"target cohort drift n={len(target)} counts={counts}")

    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in fixtures:
        by_day[r["day"]].append(r)
    target_ids = {r["id"] for r in target}
    histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=LOOKBACK_POOL))
    predictions: dict[tuple[int, int], dict] = {}
    evaluated: list[dict] = []
    structural = {"malformed_target_sides": 0, "target_prediction_missing": 0, "team_identity_errors": 0}

    out_dir.mkdir(parents=True, exist_ok=True)
    prelabel_path = out_dir / "prelabel_predictions.jsonl"
    prelabel_file = prelabel_path.open("w", encoding="utf-8")

    for day in sorted(by_day):
        games = sorted(by_day[day], key=lambda r: r["id"])
        # Phase A: fix every target prediction for this calendar day using strictly earlier-day history.
        for g in games:
            if g["id"] not in target_ids:
                continue
            for ha, tid in (("h", g["h_id"]), ("a", g["a_id"])):
                pred = predict_side(histories[tid])
                if pred is None:
                    structural["target_prediction_missing"] += 1
                    continue
                receipt = {
                    "day": day, "date": g["date"], "fid": g["fid"], "internal_match_id": g["id"],
                    "league": g["league"], "season": g["season"], "h_a": ha, "team_id": tid,
                    "expected_xi": pred["expected_xi"], "candidate_n": pred["candidate_n"],
                    "p_start": {str(k): v for k, v in pred["p_start"].items()},
                    "last_completed_xi": pred["last_completed_xi"],
                    "target_confirmed_xi_access": False,
                    "target_result_access": False,
                }
                predictions[(g["id"], tid)] = pred | {"meta": receipt}
                prelabel_file.write(json.dumps(receipt, sort_keys=True) + "\n")
        prelabel_file.flush()

        # Phase B: only after today's target predictions are fixed, reveal today's lineup rows.
        day_lineups = read_day_lineups(con, [g["id"] for g in games])
        for g in games:
            for ha, tid in (("h", g["h_id"]), ("a", g["a_id"])):
                side = day_lineups.get((g["id"], tid))
                if side is None:
                    continue
                starters = side["starters"]
                if len(starters) != 11:
                    if g["id"] in target_ids:
                        structural["malformed_target_sides"] += 1
                    continue
                if g["id"] in target_ids:
                    pred = predictions.get((g["id"], tid))
                    if pred is not None:
                        ov = overlap(pred["expected_xi"], starters)
                        last = pred["last_completed_xi"]
                        last_ov = overlap(last, starters) if last is not None else None
                        pll, pbr, pn = binary_scores(pred["p_start"], starters)
                        evaluated.append({
                            "day": day, "fid": g["fid"], "internal_match_id": g["id"], "league": g["league"],
                            "season": g["season"], "h_a": ha, "team_id": tid, "overlap": ov,
                            "last_xi_overlap": last_ov,
                            "unseen_actual_starters": sum(pid not in pred["p_start"] for pid in starters),
                            "prob_logloss": pll, "prob_brier": pbr, "prob_universe_n": pn,
                        })
                # Phase C: after label reveal, today's lineup may become history for later calendar days.
                histories[tid].append({"day": day, "players": side["players"]})
    prelabel_file.close()
    con.close()

    overall = aggregate(evaluated)
    seasons = {str(s): aggregate([r for r in evaluated if r["season"] == s]) for s in TARGET_SEASONS}
    blocks = [aggregate(b) | {"first_day": min(r["day"] for r in b), "last_day": max(r["day"] for r in b)} for b in chronological_blocks(evaluated, 4)]
    season_nonnegative = sum((v.get("mean_overlap_gain_vs_last_xi") or -999) >= 0 for v in seasons.values())
    block_nonnegative = sum((v.get("mean_overlap_gain_vs_last_xi") or -999) >= 0 for v in blocks)
    gate = {
        "eligible_team_sides": overall.get("n", 0) >= 9000,
        "overall_overlap_gain_nonnegative": (overall.get("mean_overlap_gain_vs_last_xi") is not None and overall["mean_overlap_gain_vs_last_xi"] >= 0),
        "season_blocks_nonnegative_n": season_nonnegative,
        "season_gate": season_nonnegative >= 2,
        "chronological_blocks_nonnegative_n": block_nonnegative,
        "chronological_gate": block_nonnegative >= 3,
        "probability_logloss_finite": math.isfinite(overall.get("starter_probability_logloss", float("nan"))),
    }
    gate["all_pass"] = all(v for k, v in gate.items() if k not in {"season_blocks_nonnegative_n", "chronological_blocks_nonnegative_n"})
    status = PASS if gate["all_pass"] else REJECT
    final = {
        "schema_version": "football3-historical-pit-xi-replay-result-v1",
        "status": status,
        "classification": "DIAGNOSTIC_CONSUMED_HISTORY_ONLY",
        "research_only": True,
        "promotion_allowed": False,
        "source_db_sha256": EXPECTED_DB_SHA256,
        "target_fixture_n": len(target),
        "target_fixture_counts": counts,
        "target_result_access": False,
        "target_score_access": False,
        "market_access": False,
        "injury_backfill": False,
        "2023_opened_by_this_replay": False,
        "3504_opened": False,
        "formal_v2_changed": False,
        "frozen_v3_1_1_changed": False,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_weights_changed": False,
        "structural": structural,
        "overall": overall,
        "seasons": seasons,
        "chronological_blocks": blocks,
        "gate": gate,
        "next_action": "RUN_FROZEN_1X2_TECHNICAL_BRIDGE_DIAGNOSTIC" if status == PASS else "STOP_1X2_BRIDGE_KEEP_PROSPECTIVE_ONLY",
    }
    (out_dir / "final_status.json").write_text(json.dumps(final, indent=2, sort_keys=True), encoding="utf-8")
    with (out_dir / "evaluated_sides.jsonl").open("w", encoding="utf-8") as f:
        for r in evaluated:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return final


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    final = run(Path(args.db), Path(args.out))
    print(json.dumps(final, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
