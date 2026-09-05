from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "stage6_pre_b_deep_ppda"))
sys.path.insert(0, str(ROOT / "research" / "historical_direction_screen_v1"))
import common
import run_stage6_pre_b as bmod
import screen_pit_directions as ds

EPS = 1e-12
BINS = ((0, 14), (15, 29), (30, 44), (45, 59), (60, 74), (75, 89), (90, 120))
FEATURES = ("early", "late", "concentration")


def logit(p: float) -> float:
    p = min(max(float(p), 1e-8), 1.0 - 1e-8)
    return math.log(p / (1.0 - p))


def event_profile(events: list[dict]) -> dict[str, float]:
    masses = [0.0] * len(BINS)
    total = 0.0
    early = 0.0
    late = 0.0
    for e in events:
        xg = max(0.0, float(e["xG"]))
        minute = int(e["minute"])
        total += xg
        if minute <= 29:
            early += xg
        if minute >= 75:
            late += xg
        for idx, (lo, hi) in enumerate(BINS):
            if lo <= minute <= hi:
                masses[idx] += xg
                break
    if total <= EPS:
        return {"early": 0.0, "late": 0.0, "concentration": 0.0}
    shares = [m / total for m in masses]
    return {
        "early": early / total,
        "late": late / total,
        "concentration": sum(v * v for v in shares),
    }


def temporal_feature_map(db: pathlib.Path) -> tuple[dict[str, dict[str, float]], dict]:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    qs = ",".join("?" for _ in common.LEAGUES)
    games = [
        dict(r)
        for r in con.execute(
            f"""
            select id,fid,h_id,a_id,date,league,season
            from general_game_stats
            where league in ({qs}) and season between 2014 and 2022
            order by date,id
            """,
            common.LEAGUES,
        )
    ]
    events: dict[int, dict[str, list[dict]]] = collections.defaultdict(lambda: {"h": [], "a": []})
    minute_min = None
    minute_max = None
    event_n = 0
    for r in con.execute(
        f"""
        select e.match_id,e.h_a,e.minute,e.xG
        from game_events e
        join general_game_stats g on g.id=e.match_id
        where g.league in ({qs}) and g.season between 2014 and 2022
          and e.situation!='Penalty'
        order by e.match_id,e.minute,e.id
        """,
        common.LEAGUES,
    ):
        d = dict(r)
        minute = int(d["minute"])
        minute_min = minute if minute_min is None else min(minute_min, minute)
        minute_max = minute if minute_max is None else max(minute_max, minute)
        event_n += 1
        events[int(d["match_id"])][str(d["h_a"])].append(d)
    con.close()

    assert len(games) == 16332
    assert event_n == 403517
    assert minute_min == 0 and minute_max == 105
    assert len(events) == 16332

    states: dict[tuple[str, int], collections.deque] = collections.defaultdict(
        lambda: collections.deque(maxlen=8)
    )
    out: dict[str, dict[str, float]] = {}
    bytime: collections.OrderedDict[str, list[dict]] = collections.OrderedDict()
    for g in games:
        bytime.setdefault(str(g["date"]), []).append(g)

    def prior_profile(key: tuple[str, int]) -> dict[str, float] | None:
        q = states.get(key)
        if not q or len(q) < 3:
            return None
        keys = tuple(f"atk_{z}" for z in FEATURES) + tuple(f"def_{z}" for z in FEATURES)
        return {k: sum(float(row[k]) for row in q) / len(q) for k in keys}

    for _, batch in bytime.items():
        batch = sorted(batch, key=lambda x: int(x["id"]))
        for g in batch:
            if int(g["season"]) < 2020:
                continue
            hk = (str(g["league"]), int(g["h_id"]))
            ak = (str(g["league"]), int(g["a_id"]))
            hp = prior_profile(hk)
            ap = prior_profile(ak)
            rec: dict[str, float] = {}
            if hp is not None and ap is not None:
                for z in FEATURES:
                    signed = hp[f"atk_{z}"] * ap[f"def_{z}"] - ap[f"atk_{z}"] * hp[f"def_{z}"]
                    rec[f"{z}_fit_diff"] = signed
                    rec[f"{z}_fit_abs"] = abs(signed)
            out[f"understat:{int(g['fid'])}"] = rec

        # Same-kickoff isolation: only after every feature snapshot in the batch is frozen
        # may events from those completed target matches enter future state.
        for g in batch:
            mid = int(g["id"])
            hp = event_profile(events[mid]["h"])
            ap = event_profile(events[mid]["a"])
            hk = (str(g["league"]), int(g["h_id"]))
            ak = (str(g["league"]), int(g["a_id"]))
            states[hk].append(
                {**{f"atk_{k}": v for k, v in hp.items()}, **{f"def_{k}": v for k, v in ap.items()}}
            )
            states[ak].append(
                {**{f"atk_{k}": v for k, v in ap.items()}, **{f"def_{k}": v for k, v in hp.items()}}
            )

    audit = {
        "state_game_n": len(games),
        "nonpenalty_event_n": event_n,
        "minute_min": minute_min,
        "minute_max": minute_max,
        "target_feature_map_n": len(out),
    }
    return out, audit


def standardize(train: list[dict], test: list[dict], keys: list[str]):
    means = {}
    sds = {}
    for key in keys:
        vals = [
            float(r[key])
            for r in train
            if r.get(key) is not None and math.isfinite(float(r[key]))
        ]
        assert vals
        means[key] = sum(vals) / len(vals)
        sds[key] = math.sqrt(sum((x - means[key]) ** 2 for x in vals) / len(vals)) or 1.0

    def make(rows: list[dict]) -> list[list[float]]:
        X = []
        for r in rows:
            x = [1.0]
            for key in keys:
                value = r.get(key)
                x.append(0.0 if value is None else (float(value) - means[key]) / sds[key])
            X.append(x)
        return X

    return make(train), make(test)


def fit_offset(X, y, offsets, ridge: float):
    p = len(X[0])
    beta = [0.0] * p
    for _ in range(30):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        for x, yy, off in zip(X, y, offsets):
            pred = ds.sigmoid(float(off) + sum(beta[j] * x[j] for j in range(p)))
            w = max(pred * (1.0 - pred), 1e-6)
            residual = yy - pred
            for j in range(p):
                grad[j] += x[j] * residual
                for k in range(p):
                    hess[j][k] += w * x[j] * x[k]
        for j in range(1, p):
            grad[j] -= ridge * beta[j]
            hess[j][j] += ridge
        delta = ds.solve(hess, grad)
        beta = [beta[j] + delta[j] for j in range(p)]
        if max(abs(v) for v in delta) < 1e-7:
            break
    return beta


def rolling_axis(rows, prob_key, target_fn, groups, ridge: float, drop_draw: bool = False):
    result = []
    for season in (2021, 2022):
        train = [r for r in rows if r["season"] < season and (not drop_draw or r["y"] != 1)]
        test = [r for r in rows if r["season"] == season and (not drop_draw or r["y"] != 1)]
        y_train = [target_fn(r) for r in train]
        y_test = [target_fn(r) for r in test]
        p_train = [float(r[prob_key]) for r in train]
        p_test = [float(r[prob_key]) for r in test]
        base_ll = ds.binary_logloss(y_test, p_test)
        rec = {"season": season, "train_n": len(train), "test_n": len(test), "base_logloss": base_ll}
        for name, keys in groups.items():
            X_train, X_test = standardize(train, test, keys)
            beta = fit_offset(X_train, y_train, [logit(p) for p in p_train], ridge)
            pred = [
                ds.sigmoid(logit(p) + sum(beta[j] * x[j] for j in range(len(beta))))
                for p, x in zip(p_test, X_test)
            ]
            ll = ds.binary_logloss(y_test, pred)
            rec[name] = {"logloss": ll, "delta": ll - base_ll, "auc": ds.auc(y_test, pred)}
        result.append(rec)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    for arg in ("contract", "v311", "v31", "usr1", "v2", "xg", "v1", "v1_result", "db", "xg_identity", "out"):
        ap.add_argument("--" + arg.replace("_", "-"), type=pathlib.Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    contract = json.loads(a.contract.read_text())
    assert contract["status"] == "FROZEN_BEFORE_RESIDUAL_SCREEN"

    frozen = common.build_frozen_baseline(a, "event_temporal_residual")
    dev = [
        r
        for r in frozen["rows"]
        if r["season"] in (2020, 2021, 2022) and r["fixture_id"] in frozen["bmap"]
    ]
    assert len(dev) == 5478

    fmap, event_audit = temporal_feature_map(a.db)
    assert all(r["fixture_id"] in fmap for r in dev)

    wanted = {r["fixture_id"] for r in dev}
    snapshots, snapshot_receipt = bmod.make_snapshots(
        a.db, wanted, float(contract["frozen_bases"]["stage6_b_half_life"])
    )
    bprob = {}
    for r in dev:
        fid = r["fixture_id"]
        p, _ = bmod.predict(
            frozen["bmap"][fid],
            snapshots.get(fid),
            float(contract["frozen_bases"]["stage6_b_coefficient"]),
        )
        bprob[fid] = list(map(float, p))

    rows = []
    for r in dev:
        fid = r["fixture_id"]
        base = list(map(float, frozen["bmap"][fid]))
        bp = bprob[fid]
        feat = dict(fmap[fid])
        hg = int(r["home_goals"])
        ag = int(r["away_goals"])
        y = 0 if hg > ag else 1 if hg == ag else 2
        feat.update(
            {
                "fixture_id": fid,
                "season": int(r["season"]),
                "hg": hg,
                "ag": ag,
                "y": y,
                "base_draw": base[1],
                "b_draw": bp[1],
                "base_side": base[0] / max(EPS, base[0] + base[2]),
                "b_side": bp[0] / max(EPS, bp[0] + bp[2]),
            }
        )
        rows.append(feat)

    required = ["early_fit_diff", "late_fit_diff", "concentration_fit_diff"]
    coverage = sum(all(r.get(k) is not None for k in required) for r in rows) / len(rows)

    draw_groups = {
        "early_access": ["early_fit_abs"],
        "late_pressure": ["late_fit_abs"],
        "chance_burstiness": ["concentration_fit_abs"],
        "compact_all": ["early_fit_abs", "late_fit_abs", "concentration_fit_abs"],
    }
    side_groups = {
        "early_access": ["early_fit_diff"],
        "late_pressure": ["late_fit_diff"],
        "chance_burstiness": ["concentration_fit_diff"],
        "compact_all": ["early_fit_diff", "late_fit_diff", "concentration_fit_diff"],
    }
    ridge = float(contract["method"]["fixed_ridge"])
    residual_tests = {
        "draw": rolling_axis(rows, "b_draw", lambda r: int(r["y"] == 1), draw_groups, ridge),
        "side": rolling_axis(rows, "b_side", lambda r: int(r["y"] == 0), side_groups, ridge, True),
    }

    classification = {}
    for family in draw_groups:
        draw_wins = sum(1 for fold in residual_tests["draw"] if fold[family]["delta"] < 0.0)
        side_wins = sum(1 for fold in residual_tests["side"] if fold[family]["delta"] < 0.0)
        if draw_wins == 2 and side_wins == 2:
            classification[family] = "ROBUST_BOTH_AXES_POST_B_RESIDUAL"
        elif draw_wins == 2:
            classification[family] = "ROBUST_DRAW_POST_B_RESIDUAL"
        elif side_wins == 2:
            classification[family] = "ROBUST_SIDE_POST_B_RESIDUAL"
        elif max(draw_wins, side_wins) == 1:
            classification[family] = "MIXED_POST_B_RESIDUAL"
        else:
            classification[family] = "NO_POST_B_RESIDUAL"

    status = (
        contract["terminal"]["pass"]
        if coverage >= float(contract["gates"]["minimum_coverage"])
        else contract["terminal"]["stop_coverage"]
    )
    out = {
        "schema_version": "football3-event-temporal-process-residual-result-v1",
        "status": status,
        "research_only": True,
        "development_n": len(rows),
        "source_max_season_loaded": 2022,
        "event_audit": event_audit,
        "historical_confirmation_2023_labels_opened": False,
        "prospective_1335_data_touched": False,
        "stage6_b_active_n": snapshot_receipt["active"],
        "coverage": coverage,
        "residual_tests": residual_tests,
        "classification": classification,
        "candidate_eligible_families": [
            k for k, v in classification.items() if v.startswith("ROBUST_")
        ],
        "next_step": "FREEZE_ONE_MINIMAL_CANDIDATE_ONLY_IF_A_FAMILY_IS_ROBUST_ON_THE_SAME_AXIS_IN_BOTH_ROLLING_FOLDS",
    }
    assert out["stage6_b_active_n"] == 5463
    assert out["historical_confirmation_2023_labels_opened"] is False
    assert out["prospective_1335_data_touched"] is False
    common.write_json(a.out / "event_temporal_process_residual.json", out)
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
