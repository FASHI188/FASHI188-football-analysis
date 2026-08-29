#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "summary_r43ac1.json"
AB1_DIR = HERE.parent / "r43ab1_referee_prior_style_screen"
sys.path.insert(0, str(AB1_DIR))
import run_r43ab1 as ab1  # noqa: E402

r9 = ab1.r9
r34 = ab1.r34
CAT_URL = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main/league_catalogue.parquet?download=true"
CYCLE_GAP_DAYS = 75
MODEL_C = 0.5
PPG_PRIOR = 1.33
PPG_PRIOR_STRENGTH = 2.0

FEATURES = [
    "is_league",
    "both_table_known",
    "both_table_mature3",
    "log_min_cycle_games",
    "cycle_progress_20",
    "home_ppg",
    "away_ppg",
    "ppg_diff",
    "ppg_abs_diff",
    "home_gdpg",
    "away_gdpg",
    "gdpg_diff",
    "home_rank_pct",
    "away_rank_pct",
    "rank_pct_diff",
    "rank_pct_abs_diff",
]


@dataclass
class TeamTable:
    games: int = 0
    points: int = 0
    gf: int = 0
    ga: int = 0


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r43ac1/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def load_league_types() -> dict[str, str]:
    tmp = HERE / "league_catalogue.parquet"
    download(CAT_URL, tmp)
    df = pd.read_parquet(tmp, columns=["dataset_league_id", "af_type"])
    df = df[df["dataset_league_id"].notna()].copy()
    df["competition_id"] = df["dataset_league_id"].astype("int64").astype(str)
    df = df.drop_duplicates("competition_id")
    out = {str(r.competition_id): str(r.af_type) for r in df.itertuples(index=False)}
    tmp.unlink(missing_ok=True)
    return out


class TableState:
    def __init__(self, league_types: dict[str, str]):
        self.league_types = league_types
        self.tables: dict[str, dict[str, TeamTable]] = defaultdict(dict)
        self.last_comp_date: dict[str, date] = {}
        self.cycle_id = defaultdict(int)
        self.reset_events = []

    def is_league(self, comp: str) -> bool:
        return self.league_types.get(comp) == "League"

    def maybe_reset(self, comp: str, d: date) -> None:
        if not self.is_league(comp):
            return
        prev = self.last_comp_date.get(comp)
        if prev is not None:
            gap = (d - prev).days
            if gap >= CYCLE_GAP_DAYS:
                self.tables[comp] = {}
                self.cycle_id[comp] += 1
                self.reset_events.append({"competition_id": comp, "date": d.isoformat(), "prior_date": prev.isoformat(), "gap_days": gap, "new_cycle": self.cycle_id[comp]})

    def _rank_pct(self, comp: str, team: str) -> float:
        table = self.tables[comp]
        if team not in table or not table:
            return 0.5
        order = sorted(table.items(), key=lambda kv: (-kv[1].points, -(kv[1].gf-kv[1].ga), -kv[1].gf, kv[0]))
        idx = next(i for i, (t, _) in enumerate(order) if t == team)
        return 0.5 if len(order) <= 1 else idx / (len(order) - 1)

    @staticmethod
    def _ppg(s: TeamTable) -> float:
        return (float(s.points) + PPG_PRIOR_STRENGTH * PPG_PRIOR) / (float(s.games) + PPG_PRIOR_STRENGTH)

    @staticmethod
    def _gdpg(s: TeamTable) -> float:
        return float(s.gf - s.ga) / (float(s.games) + PPG_PRIOR_STRENGTH)

    def features(self, comp: str, home: str, away: str) -> dict[str, float]:
        if not self.is_league(comp):
            return {k: 0.0 for k in FEATURES}
        table = self.tables[comp]
        hs = table.get(home, TeamTable())
        as_ = table.get(away, TeamTable())
        hppg, appg = self._ppg(hs), self._ppg(as_)
        hgd, agd = self._gdpg(hs), self._gdpg(as_)
        hr, ar = self._rank_pct(comp, home), self._rank_pct(comp, away)
        ming = min(hs.games, as_.games)
        return {
            "is_league": 1.0,
            "both_table_known": float(hs.games >= 1 and as_.games >= 1),
            "both_table_mature3": float(hs.games >= 3 and as_.games >= 3),
            "log_min_cycle_games": math.log1p(float(ming)),
            "cycle_progress_20": min(float(ming), 20.0) / 20.0,
            "home_ppg": hppg,
            "away_ppg": appg,
            "ppg_diff": hppg - appg,
            "ppg_abs_diff": abs(hppg - appg),
            "home_gdpg": hgd,
            "away_gdpg": agd,
            "gdpg_diff": hgd - agd,
            "home_rank_pct": hr,
            "away_rank_pct": ar,
            "rank_pct_diff": hr - ar,
            "rank_pct_abs_diff": abs(hr - ar),
        }

    def update(self, comp: str, home: str, away: str, hg: int, ag: int) -> None:
        if not self.is_league(comp):
            return
        table = self.tables[comp]
        hs = table.setdefault(home, TeamTable())
        as_ = table.setdefault(away, TeamTable())
        hs.games += 1; as_.games += 1
        hs.gf += hg; hs.ga += ag; as_.gf += ag; as_.ga += hg
        if hg > ag:
            hs.points += 3
        elif hg < ag:
            as_.points += 3
        else:
            hs.points += 1; as_.points += 1


def build_history():
    r34.r12.freeze_gate()
    rows = r9.load()
    league_types = load_league_types()
    if any(str(r["competition_id"]) not in league_types for r in rows):
        missing = sorted({str(r["competition_id"]) for r in rows if str(r["competition_id"]) not in league_types})
        raise RuntimeError(f"R43AC1 unmapped competition ids: {missing}")
    base = r9.S()
    ts = TableState(league_types)
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        d = date.fromisoformat(ds)
        comps_today = sorted({str(r["competition_id"]) for r in by[ds]})
        for comp in comps_today:
            ts.maybe_reset(comp, d)
        pending = []
        for row in sorted(by[ds], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            cf = ts.features(str(row["competition_id"]), str(row["home_team"]), str(row["away_team"]))
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))
        for row, raw in pending:
            base.update(row, raw)
            ts.update(str(row["competition_id"]), str(row["home_team"]), str(row["away_team"]), int(row["home_goals"]), int(row["away_goals"]))
        for comp in comps_today:
            if ts.is_league(comp):
                ts.last_comp_date[comp] = d
    return pred, ts.reset_events


def x_for(rec):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in FEATURES]


def fit_candidate(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0))
    m.fit([x_for(r) for r in train], [r["y"] for r in train])
    return m


def decorate_candidate(model, rows):
    pr = model.predict_proba([x_for(r) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, p in zip(rows, pr):
        v = np.zeros(3, dtype=float)
        for cls, q in zip(classes, p):
            v[int(cls)] = float(q)
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def run():
    pred, reset_events = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    base_model = ab1.baseline_model(train)
    val_base_rows = ab1.decorate_baseline(base_model, val0)
    vb = ab1.metrics(val_base_rows)
    if vb["hits"] != 2064:
        raise RuntimeError(f"R43AC1 K1 validation reproduction failed: {vb['hits']}")

    candidate_model = fit_candidate(train)
    val_candidate_rows = decorate_candidate(candidate_model, val0)
    vc = ab1.metrics(val_candidate_rows)
    vd = ab1.delta(vb, vc)
    vpb = ab1.paired_blocks(val_base_rows, val_candidate_rows)
    validation_gate = ab1.dev_gate(vd, vpb)

    historical_test = None
    if validation_gate:
        test_base_rows = ab1.decorate_baseline(base_model, test0)
        tb = ab1.metrics(test_base_rows)
        if tb["hits"] != 1877:
            raise RuntimeError(f"R43AC1 K1 test reproduction failed: {tb['hits']}")
        test_candidate_rows = decorate_candidate(candidate_model, test0)
        tc = ab1.metrics(test_candidate_rows)
        td = ab1.delta(tb, tc)
        tpb = ab1.paired_blocks(test_base_rows, test_candidate_rows)
        strong_test = bool(
            td["accuracy_pp"] >= 1.0
            and td["logloss"] < 0 and td["brier"] < 0 and td["rps"] < 0
            and td["draw_logloss"] <= 0 and td["draw_brier"] <= 0
            and tpb["nonnegative_blocks"] >= 3
        )
        historical_test = {"baseline": tb, "candidate": tc, "candidate_minus_baseline": td, "paired_time_blocks": tpb, "strong_test_gate": strong_test}
        action = "FREEZE_CAUSAL_TABLE_CYCLE_ARCHITECTURE_FOR_GENUINELY_FRESH_CONFIRMATION" if strong_test else "DO_NOT_PROMOTE_OR_RETUNE_TABLE_STATE_ON_CONSUMED_HISTORY"
    else:
        action = "CLOSE_DYNAMIC_TABLE_STATE_AXIS_NO_STRONG_VALIDATION_SIGNAL"

    league_val = sum(int(r["context_features"]["is_league"] > 0) for r in val0)
    mature_val = sum(int(r["context_features"]["both_table_mature3"] > 0) for r in val0)
    out = {
        "schema_version": "football3-r43ac1-dynamic-table-state-screen-v1",
        "status": "COMPLETE",
        "classification": "POSTVIEW_HISTORICAL_DEVELOPMENT_CAUSAL_INFERRED_CYCLE_FORMAL_WEIGHT_ZERO",
        "formal_weight": 0,
        "governance": {
            "source_r43ac0_head": "38a11dd8c47d6b79eb377a41eada476dbc64ac66",
            "same_r9_consumed_20k_history": True,
            "official_season_id_available": False,
            "official_standings_claimed": False,
            "cycle_reset_uses_future_schedule": False,
            "cycle_reset_gap_days_fixed": CYCLE_GAP_DAYS,
            "same_date_results_withheld_until_all_predictions": True,
            "current_match_result_or_xg_used_in_table_features": False,
            "odds_used": False,
            "feature_set_predeclared": True,
            "hyperparameter_search": False,
            "test_opened_only_after_strong_validation_gate": True,
            "promotion_allowed_from_this_run": False,
        },
        "design": {
            "feature_set": FEATURES,
            "model": f"StandardScaler + multinomial LogisticRegression C={MODEL_C}",
            "ppg_prior": PPG_PRIOR,
            "ppg_prior_strength": PPG_PRIOR_STRENGTH,
            "cycle_rule": f"af_type=League only; reset table state when current date - previously observed competition match date >= {CYCLE_GAP_DAYS} days",
            "validation_gate": ">=+1.0pp Top1; LL/Brier/RPS all improve; draw LL/Brier nonworse; >=3/4 time blocks nonnegative and >=2 positive",
            "test_gate": ">=+1.0pp Top1; LL/Brier/RPS all improve; draw LL/Brier nonworse; >=3/4 time blocks nonnegative",
        },
        "split": {"train_n": len(train), "validation_n": len(val0), "historical_test_n": len(test0)},
        "coverage": {"validation_league_rows": league_val, "validation_league_rate": league_val / len(val0), "validation_mature3_rows": mature_val, "validation_mature3_rate": mature_val / len(val0), "reset_event_count_full20k": len(reset_events)},
        "validation": {"baseline": vb, "candidate": vc, "candidate_minus_baseline": vd, "paired_time_blocks": vpb, "strong_validation_gate": validation_gate},
        "historical_test": historical_test,
        "action": action,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": out["status"], "coverage": out["coverage"], "validation_delta": vd, "validation_blocks": vpb, "validation_gate": validation_gate, "historical_test": historical_test, "action": action}, ensure_ascii=False, indent=2))
    return out


def verify():
    s = json.loads(OUT.read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE" and s["formal_weight"] == 0
    assert s["governance"]["cycle_reset_gap_days_fixed"] == 75
    assert s["governance"]["cycle_reset_uses_future_schedule"] is False
    assert s["governance"]["same_date_results_withheld_until_all_predictions"] is True
    assert s["validation"]["baseline"]["hits"] == 2064
    if s["historical_test"] is not None:
        assert s["historical_test"]["baseline"]["hits"] == 1877
    print("R43AC1 dynamic table-state development contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run": run()
    elif cmd == "verify": verify()
    else: raise SystemExit(cmd)
