from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sqlite3
import statistics

LEAGUES = ("Bundesliga", "EPL", "La liga", "Ligue 1", "Serie A")
TARGET_SEASONS = (2020, 2021, 2022)
SETPIECE = {"SetPiece", "FromCorner", "DirectFreekick"}
BINS = ((0, 14), (15, 29), (30, 44), (45, 59), (60, 74), (75, 89), (90, 120))
EPS = 1e-12


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
        return float(s[lo] * (1.0 - w) + s[hi] * w)
    return {"p01": q(.01), "p10": q(.10), "p25": q(.25), "p50": q(.50), "p75": q(.75), "p90": q(.90), "p99": q(.99)}


def clean_category(x: object) -> str | None:
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


def side_profile(events: list[dict]) -> dict[str, float] | None:
    if not events:
        return None
    cats = collections.Counter()
    xgs: list[float] = []
    bin_mass = [0.0] * len(BINS)
    setpiece_xg = 0.0
    total_xg = 0.0
    for e in events:
        cat = clean_category(e.get("lastAction"))
        if cat is not None:
            cats[cat] += 1
        xg = max(0.0, float(e["xG"]))
        xgs.append(xg)
        total_xg += xg
        if str(e.get("situation")) in SETPIECE:
            setpiece_xg += xg
        minute = int(e["minute"])
        for i, (lo, hi) in enumerate(BINS):
            if lo <= minute <= hi:
                bin_mass[i] += xg
                break
    known = sum(cats.values())
    if known <= 0:
        return None
    route_hhi = sum((n / known) ** 2 for n in cats.values())
    if total_xg > EPS:
        shot_xg_hhi = sum((v / total_xg) ** 2 for v in xgs)
        setpiece_share = setpiece_xg / total_xg
        temporal_hhi = sum((v / total_xg) ** 2 for v in bin_mass)
    else:
        shot_xg_hhi = 0.0
        setpiece_share = 0.0
        temporal_hhi = 0.0
    return {
        "route_hhi": float(route_hhi),
        "shot_xg_hhi": float(shot_xg_hhi),
        "setpiece_xg_share": float(setpiece_share),
        "temporal_xg_hhi": float(temporal_hhi),
        "known_fraction": float(known / len(events)),
    }


def avg_profile(hist: collections.deque[dict], minimum: int) -> dict[str, float] | None:
    if len(hist) < minimum:
        return None
    keys = (
        "atk_route_hhi", "def_route_hhi",
        "atk_shot_xg_hhi", "def_shot_xg_hhi",
        "atk_setpiece_xg_share", "def_setpiece_xg_share",
        "atk_temporal_xg_hhi", "def_temporal_xg_hhi",
    )
    return {k: statistics.fmean(float(x[k]) for x in hist) for k in keys}


def matchup_abs(h: dict[str, float], a: dict[str, float], stem: str) -> float:
    signed = h[f"atk_{stem}"] * a[f"def_{stem}"] - a[f"atk_{stem}"] * h[f"def_{stem}"]
    return abs(float(signed))


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
    lookback = int(c["feature"]["lookback_completed_matches"])
    minimum = int(c["feature"]["minimum_prior_matches"])
    if lookback != 8 or minimum != 3:
        raise RuntimeError("frozen lookback/minimum drift")

    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables = {str(r[0]) for r in con.execute("select name from sqlite_master where type='table'")}
    if "game_events" not in tables or "general_game_stats" not in tables:
        raise RuntimeError("required source tables missing")
    event_cols = {str(r[1]) for r in con.execute("pragma table_info(game_events)")}
    required = set(c["source"]["required_event_columns"])
    missing = sorted(required - event_cols)
    if missing:
        raise RuntimeError(f"required event columns missing: {missing}")

    qs = ",".join("?" for _ in LEAGUES)
    fixtures = [
        {
            "id": int(r["id"]), "fid": int(r["fid"]), "date": str(r["date"]), "day": str(r["date"])[:10],
            "league": str(r["league"]), "season": int(r["season"]), "h_id": int(r["h_id"]), "a_id": int(r["a_id"]),
        }
        for r in con.execute(
            f"select id,fid,date,league,season,h_id,a_id from general_game_stats where league in ({qs}) and season between 2014 and 2022 order by date,id",
            LEAGUES,
        )
    ]
    targets = [g for g in fixtures if g["season"] in TARGET_SEASONS]
    target_counts = {str(s): sum(g["season"] == s for g in targets) for s in TARGET_SEASONS}
    if len(targets) != int(c["cohort"]["expected_target_n"]):
        raise RuntimeError(f"target_n drift: {len(targets)}")

    raw: dict[int, dict[str, list[dict]]] = collections.defaultdict(lambda: {"h": [], "a": []})
    category_counts: collections.Counter[str] = collections.Counter()
    nonpenalty_n = 0
    last_action_nonblank_n = 0
    for r in con.execute(
        f"""
        select e.match_id,e.h_a,e.situation,e.lastAction,e.xG,e.minute
        from game_events e
        join general_game_stats g on g.id=e.match_id
        where g.league in ({qs}) and g.season between 2014 and 2022
          and e.situation!='Penalty'
        order by e.match_id,e.minute,e.id
        """,
        LEAGUES,
    ):
        rec = dict(r)
        side = str(rec["h_a"])
        if side not in ("h", "a"):
            raise RuntimeError(f"unexpected h_a: {side!r}")
        raw[int(rec["match_id"])][side].append(rec)
        nonpenalty_n += 1
        cat = clean_category(rec.get("lastAction"))
        if cat is not None:
            category_counts[cat] += 1
            last_action_nonblank_n += 1
    con.close()

    by_day: dict[str, list[dict]] = collections.defaultdict(list)
    for g in fixtures:
        by_day[g["day"]].append(g)
    target_ids = {g["id"] for g in targets}
    histories: dict[tuple[str, int], collections.deque[dict]] = collections.defaultdict(
        lambda: collections.deque(maxlen=lookback)
    )
    receipts: list[dict] = []
    valid_history_rows = 0
    skipped_history_rows = 0

    for day in sorted(by_day):
        games = sorted(by_day[day], key=lambda z: z["id"])
        # Phase A: every target-date feature is frozen before any same-date event is released.
        for g in games:
            if g["id"] not in target_ids:
                continue
            hk = (g["league"], g["h_id"])
            ak = (g["league"], g["a_id"])
            hp = avg_profile(histories[hk], minimum)
            ap = avg_profile(histories[ak], minimum)
            rec = {
                "fixture_id": f"understat:{g['fid']}", "internal_match_id": g["id"], "day": day,
                "season": g["season"], "league": g["league"],
                "creation_route_hhi_fit_abs": None,
                "shot_xg_hhi_fit_abs": None,
                "setpiece_xg_share_fit_abs": None,
                "temporal_xg_hhi_fit_abs": None,
                "target_current_event_access": False,
                "target_result_access": False,
                "target_score_access": False,
                "target_match_xg_access": False,
            }
            if hp is not None and ap is not None:
                rec["creation_route_hhi_fit_abs"] = matchup_abs(hp, ap, "route_hhi")
                rec["shot_xg_hhi_fit_abs"] = matchup_abs(hp, ap, "shot_xg_hhi")
                rec["setpiece_xg_share_fit_abs"] = matchup_abs(hp, ap, "setpiece_xg_share")
                rec["temporal_xg_hhi_fit_abs"] = matchup_abs(hp, ap, "temporal_xg_hhi")
            receipts.append(rec)

        # Phase B: only completed same-date event rows become available to later calendar dates.
        for g in games:
            hp = side_profile(raw[g["id"]]["h"])
            ap = side_profile(raw[g["id"]]["a"])
            if hp is None or ap is None:
                skipped_history_rows += 2
                continue
            hrow = {
                "atk_route_hhi": hp["route_hhi"], "def_route_hhi": ap["route_hhi"],
                "atk_shot_xg_hhi": hp["shot_xg_hhi"], "def_shot_xg_hhi": ap["shot_xg_hhi"],
                "atk_setpiece_xg_share": hp["setpiece_xg_share"], "def_setpiece_xg_share": ap["setpiece_xg_share"],
                "atk_temporal_xg_hhi": hp["temporal_xg_hhi"], "def_temporal_xg_hhi": ap["temporal_xg_hhi"],
            }
            arow = {
                "atk_route_hhi": ap["route_hhi"], "def_route_hhi": hp["route_hhi"],
                "atk_shot_xg_hhi": ap["shot_xg_hhi"], "def_shot_xg_hhi": hp["shot_xg_hhi"],
                "atk_setpiece_xg_share": ap["setpiece_xg_share"], "def_setpiece_xg_share": hp["setpiece_xg_share"],
                "atk_temporal_xg_hhi": ap["temporal_xg_hhi"], "def_temporal_xg_hhi": hp["temporal_xg_hhi"],
            }
            histories[(g["league"], g["h_id"])].append(hrow)
            histories[(g["league"], g["a_id"])].append(arow)
            valid_history_rows += 2

    covered = [r for r in receipts if r["creation_route_hhi_fit_abs"] is not None]
    route = [float(r["creation_route_hhi_fit_abs"]) for r in covered]
    comparators = {
        "shot_xg_hhi_fit_abs": [float(r["shot_xg_hhi_fit_abs"]) for r in covered],
        "setpiece_xg_share_fit_abs": [float(r["setpiece_xg_share_fit_abs"]) for r in covered],
        "temporal_xg_hhi_fit_abs": [float(r["temporal_xg_hhi_fit_abs"]) for r in covered],
    }
    correlations = {k: pearson(route, vals) for k, vals in comparators.items()} if len(route) >= 3 else {}
    max_abs_corr = max((abs(v) for v in correlations.values()), default=1.0)
    coverage = len(covered) / len(receipts) if receipts else 0.0
    last_action_fraction = last_action_nonblank_n / nonpenalty_n if nonpenalty_n else 0.0
    gates = c["gates"]
    checks = {
        "target_n_exact": len(receipts) == int(gates["target_n_exact"]),
        "per_season_n_exact": all(target_counts[str(s)] == int(gates["per_season_n_exact"]) for s in TARGET_SEASONS),
        "minimum_match_feature_coverage": coverage >= float(gates["minimum_match_feature_coverage"]),
        "minimum_last_action_nonblank_fraction": last_action_fraction >= float(gates["minimum_last_action_nonblank_fraction"]),
        "minimum_distinct_last_action_categories": len(category_counts) >= int(gates["minimum_distinct_last_action_categories"]),
        "maximum_absolute_redundancy_correlation": max_abs_corr <= float(gates["maximum_absolute_redundancy_correlation"]),
    }
    passed = all(checks.values())
    out = {
        "schema_version": "football3-prior-chance-creation-route-source-audit-result-v1",
        "status": c["terminal"]["pass"] if passed else c["terminal"]["fail"],
        "source_only": True,
        "model_fit": 0,
        "candidate_probability": 0,
        "outcome_label_access": 0,
        "target_n": len(receipts),
        "target_counts": target_counts,
        "match_feature_covered_n": len(covered),
        "match_feature_coverage": coverage,
        "nonpenalty_event_n": nonpenalty_n,
        "last_action_nonblank_n": last_action_nonblank_n,
        "last_action_nonblank_fraction": last_action_fraction,
        "distinct_last_action_categories": len(category_counts),
        "last_action_category_counts": dict(sorted(category_counts.items(), key=lambda z: (-z[1], z[0]))),
        "valid_history_rowsides": valid_history_rows,
        "skipped_history_rowsides": skipped_history_rows,
        "feature_key": c["feature"]["key"],
        "feature_quantiles": quantiles(route),
        "comparator_quantiles": {k: quantiles(v) for k, v in comparators.items()},
        "redundancy_correlations": correlations,
        "maximum_absolute_redundancy_correlation": max_abs_corr,
        "checks": checks,
        "historical_confirmation_2023_labels_opened": False,
        "prospective_1335_data_touched": False,
        "formal_weight": 0,
        "next_step": "FREEZE_ONE_CREATION_ROUTE_HHI_CANDIDATE_NO_SEARCH" if passed else "CLOSE_CREATION_ROUTE_AS_DUPLICATE_OR_INSUFFICIENT_SOURCE",
    }
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "creation_route_source_audit.json").write_text(json.dumps(out, sort_keys=True, indent=2) + "\n")
    with (a.out / "creation_route_prematch_receipts.jsonl").open("w", encoding="utf-8") as f:
        for r in receipts:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
