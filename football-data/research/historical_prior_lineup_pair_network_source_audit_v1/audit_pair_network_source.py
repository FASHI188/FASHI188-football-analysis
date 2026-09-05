from __future__ import annotations

import argparse
import collections
import itertools
import json
import math
import pathlib
import sqlite3
import statistics

LEAGUES = ("Bundesliga", "EPL", "La liga", "Ligue 1", "Serie A")
TARGET_SEASONS = (2020, 2021, 2022)


def starter(position: object) -> bool:
    return position is not None and str(position).strip().lower() != "sub"


def read_lineups(con: sqlite3.Connection, ids: list[int]) -> dict[tuple[int, int], frozenset[int]]:
    if not ids:
        return {}
    out: dict[tuple[int, int], dict[int, bool]] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        marks = ",".join("?" for _ in chunk)
        sql = f"select match_id,team_id,player_id,position from lineup_stats where match_id in ({marks}) order by match_id,team_id,player_id"
        for mid, tid, pid, pos in con.execute(sql, chunk):
            out.setdefault((int(mid), int(tid)), {})[int(pid)] = starter(pos)
    return {
        k: frozenset(pid for pid, is_start in vals.items() if is_start)
        for k, vals in out.items()
    }


def pair_hhi(xis: list[frozenset[int]]) -> float:
    counts: collections.Counter[tuple[int, int]] = collections.Counter()
    for xi in xis:
        for a, b in itertools.combinations(sorted(xi), 2):
            counts[(a, b)] += 1
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("empty pair network")
    return sum((v / total) ** 2 for v in counts.values())


def jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def mean_jaccard_last3(xis: list[frozenset[int]]) -> float:
    recent = xis[-4:]
    vals = [jaccard(a, b) for a, b in zip(recent[:-1], recent[1:])]
    if len(vals) != 3:
        raise RuntimeError("jaccard comparator requires 4 prior XIs")
    return statistics.fmean(vals)


def core_3of5(xis: list[frozenset[int]]) -> float:
    recent = xis[-5:]
    counts = collections.Counter(pid for xi in recent for pid in xi)
    return float(sum(n >= 3 for n in counts.values()))


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 3:
        raise RuntimeError("invalid correlation sample")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sx = math.sqrt(sum(v * v for v in dx))
    sy = math.sqrt(sum(v * v for v in dy))
    if sx == 0.0 or sy == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / (sx * sy)


def quantiles(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {}
    s = sorted(vals)
    def q(p: float) -> float:
        x = (len(s) - 1) * p
        lo, hi = int(math.floor(x)), int(math.ceil(x))
        if lo == hi:
            return float(s[lo])
        w = x - lo
        return float(s[lo] * (1 - w) + s[hi] * w)
    return {"p01": q(.01), "p10": q(.10), "p25": q(.25), "p50": q(.50), "p75": q(.75), "p90": q(.90), "p99": q(.99)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", type=pathlib.Path, required=True)
    ap.add_argument("--db", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args()
    c = json.loads(a.contract.read_text())
    assert c["status"] == "FROZEN_ZERO_MODEL_SOURCE_AUDIT"
    assert c["boundaries"]["model_fit"] == 0
    assert c["boundaries"]["outcome_label_access"] == 0
    lookback = int(c["feature"]["lookback_valid_completed_lineups"])
    minimum = int(c["feature"]["minimum_valid_completed_lineups"])
    if lookback != 8 or minimum != 8:
        raise RuntimeError("frozen lookback drift")

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    marks = ",".join("?" for _ in LEAGUES)
    # Deliberately omit goals/results/xG columns: this audit is zero-label by construction.
    fixtures = [
        {
            "id": int(r[0]), "fid": int(r[1]), "date": str(r[2]), "day": str(r[2])[:10],
            "league": str(r[3]), "season": int(r[4]), "h_id": int(r[5]), "a_id": int(r[6]),
        }
        for r in con.execute(
            f"select id,fid,date,league,season,h_id,a_id from general_game_stats where league in ({marks}) and season between 2014 and 2022 order by date,id",
            LEAGUES,
        )
    ]
    targets = [g for g in fixtures if g["season"] in TARGET_SEASONS]
    counts = {str(s): sum(g["season"] == s for g in targets) for s in TARGET_SEASONS}
    expected = int(c["cohort"]["expected_target_n"])
    if len(targets) != expected:
        raise RuntimeError(f"target_n drift {len(targets)}")

    by_day: dict[str, list[dict]] = collections.defaultdict(list)
    for g in fixtures:
        by_day[g["day"]].append(g)
    target_ids = {g["id"] for g in targets}
    histories: dict[int, collections.deque[frozenset[int]]] = collections.defaultdict(lambda: collections.deque(maxlen=lookback))
    receipts: list[dict] = []
    valid_target_sides = 0
    malformed_lineups = 0

    for day in sorted(by_day):
        games = sorted(by_day[day], key=lambda z: z["id"])
        # Phase A: freeze every target-date feature using only earlier calendar dates.
        for g in games:
            if g["id"] not in target_ids:
                continue
            side_vals: list[dict | None] = []
            for tid in (g["h_id"], g["a_id"]):
                hist = list(histories[tid])
                rec = None
                if len(hist) >= minimum:
                    rec = {
                        "pair_hhi": pair_hhi(hist[-lookback:]),
                        "mean_jaccard_last3": mean_jaccard_last3(hist[-lookback:]),
                        "core_3of5": core_3of5(hist[-lookback:]),
                    }
                    valid_target_sides += 1
                side_vals.append(rec)
            h, v = side_vals
            feature = None if h is None or v is None else 0.5 * (h["pair_hhi"] + v["pair_hhi"])
            comp_j = None if h is None or v is None else 0.5 * (h["mean_jaccard_last3"] + v["mean_jaccard_last3"])
            comp_c = None if h is None or v is None else 0.5 * (h["core_3of5"] + v["core_3of5"])
            receipts.append({
                "fixture_id": f"understat:{g['fid']}", "internal_match_id": g["id"], "day": day,
                "season": g["season"], "league": g["league"],
                "pair_mean_prior_starter_pair_hhi": feature,
                "pair_mean_prior_mean_jaccard_last3_transitions": comp_j,
                "pair_mean_prior_core_starters_3of5": comp_c,
                "target_current_lineup_access": False,
                "target_result_access": False,
                "target_score_access": False,
                "target_xg_access": False,
            })

        # Phase B: only after all snapshots for this calendar date are frozen, reveal lineup rows for future dates.
        lineups = read_lineups(con, [g["id"] for g in games])
        for g in games:
            for tid in (g["h_id"], g["a_id"]):
                xi = lineups.get((g["id"], tid))
                if xi is None:
                    continue
                if len(xi) != int(c["feature"]["valid_lineup_exact_starters"]):
                    malformed_lineups += 1
                    continue
                histories[tid].append(xi)
    con.close()

    covered = [r for r in receipts if r["pair_mean_prior_starter_pair_hhi"] is not None]
    hhi = [float(r["pair_mean_prior_starter_pair_hhi"]) for r in covered]
    jacc = [float(r["pair_mean_prior_mean_jaccard_last3_transitions"]) for r in covered]
    core = [float(r["pair_mean_prior_core_starters_3of5"]) for r in covered]
    correlations = {
        "vs_pair_mean_prior_mean_jaccard_last3_transitions": pearson(hhi, jacc),
        "vs_pair_mean_prior_core_starters_3of5": pearson(hhi, core),
    }
    max_abs_corr = max(abs(v) for v in correlations.values())
    coverage = len(covered) / len(receipts)
    side_fraction = valid_target_sides / (2 * len(receipts))
    g = c["gates"]
    checks = {
        "target_n_exact": len(receipts) == int(g["target_n_exact"]),
        "per_season_n_exact": all(counts[str(s)] == int(g["per_season_n_exact"]) for s in TARGET_SEASONS),
        "minimum_match_feature_coverage": coverage >= float(g["minimum_match_feature_coverage"]),
        "minimum_valid_target_side_fraction": side_fraction >= float(g["minimum_valid_target_side_fraction"]),
        "maximum_absolute_redundancy_correlation": max_abs_corr <= float(g["maximum_absolute_redundancy_correlation"]),
    }
    passed = all(checks.values())
    out = {
        "schema_version": "football3-prior-lineup-pair-network-source-audit-result-v1",
        "status": c["terminal"]["pass"] if passed else c["terminal"]["fail"],
        "source_only": True,
        "model_fit": 0,
        "candidate_probability": 0,
        "outcome_label_access": 0,
        "target_n": len(receipts),
        "target_counts": counts,
        "match_feature_covered_n": len(covered),
        "match_feature_coverage": coverage,
        "valid_target_sides": valid_target_sides,
        "valid_target_side_fraction": side_fraction,
        "malformed_historical_lineup_rowsides": malformed_lineups,
        "feature_key": c["feature"]["key"],
        "feature_quantiles": quantiles(hhi),
        "comparator_quantiles": {
            "mean_jaccard_last3": quantiles(jacc),
            "core_3of5": quantiles(core),
        },
        "redundancy_correlations": correlations,
        "maximum_absolute_redundancy_correlation": max_abs_corr,
        "checks": checks,
        "historical_confirmation_2023_labels_opened": False,
        "prospective_1335_data_touched": False,
        "formal_weight": 0,
        "next_step": "FREEZE_ONE_PAIR_HHI_CANDIDATE_NO_SEARCH" if passed else "CLOSE_PAIR_NETWORK_AS_DUPLICATE_OR_INSUFFICIENT_SOURCE",
    }
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "pair_network_source_audit.json").write_text(json.dumps(out, sort_keys=True, indent=2) + "\n")
    with (a.out / "pair_network_prematch_receipts.jsonl").open("w", encoding="utf-8") as f:
        for r in receipts:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
