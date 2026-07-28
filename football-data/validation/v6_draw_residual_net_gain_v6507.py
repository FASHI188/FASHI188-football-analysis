#!/usr/bin/env python3
"""V6.50.7 decision-theoretic draw residual challenge.

Goal
----
Fix the recurring 1X2 failure mode where the frozen selector is accurate on strong
home/away picks but almost never executes DRAW.  This challenger does NOT add an
arbitrary draw bonus and does NOT mutate market probabilities.  It learns a separate
pre-match residual decision: for an already-selected H/A pick, override to DRAW only
when pre-target evidence estimates that doing so has positive expected hit gain.

Decision utility for an override:
  actual DRAW       -> +1 hit vs keeping H/A
  actual base pick  -> -1 hit vs keeping H/A
  opposite H/A      ->  0 (both decisions miss)
Hence the relevant Bayes comparison is P(DRAW|x) versus P(BASE_CORRECT|x).

Leakage controls
----------------
* Logistic residual coefficients are trained only on rows dated before 2024-01-01.
* Override threshold is chosen only on 2024 calendar-year validation rows.
* 2025-era target is exactly the V6.50.6 contract: calendar leagues 2025, cross-year
  leagues 2025/26.
* Match-result state features are frozen for an entire day, then all matches that day
  update together.
* V6.47.5 selector threshold/reliability model stays frozen at 0.55.
* No target result tunes coefficients, threshold, feature definitions, or selector.

Research only. formal_weight=0. CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
import sys
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_fullseason_2025_replay_v6506 as base
import evaluate_direct_total_margin_matrix_v6477 as core
from diagnose_1x2_market_anchor_v697 import _extract_odds

OUT = ROOT / "manifests" / "v6_draw_residual_net_gain_v6507_status.json"
SELECTOR_FREEZE = ROOT / "manifests" / "v6_hierarchical_selector_forward_v6475_freeze.json"
D = ("home", "draw", "away")
TRAIN_END = "2024-01-01"
VALID_END = "2025-01-01"
EPS = 1e-12
HOME_ELO_ADV = 60.0
ELO_K = 20.0


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def date_key(v: Any) -> str:
    return str(v)[:10]


def days_between(a: str | None, b: str) -> float:
    if not a:
        return 7.0
    try:
        da = datetime.fromisoformat(a[:10])
        db = datetime.fromisoformat(b[:10])
        return float(max(0, min(30, (db - da).days)))
    except Exception:
        return 7.0


@dataclass
class TeamState:
    recent: deque = field(default_factory=lambda: deque(maxlen=10))
    home_recent: deque = field(default_factory=lambda: deque(maxlen=8))
    away_recent: deque = field(default_factory=lambda: deque(maxlen=8))
    last_date: str | None = None

    @staticmethod
    def stats(q: deque) -> dict[str, float]:
        if not q:
            return {"ppg": 1.35, "gd": 0.0, "draw": 0.27, "under25": 0.5, "avg_total": 2.6, "btts": 0.5}
        n = float(len(q))
        return {
            "ppg": sum(x[0] for x in q) / n,
            "gd": sum(x[1] for x in q) / n,
            "draw": sum(x[2] for x in q) / n,
            "under25": sum(x[3] for x in q) / n,
            "avg_total": sum(x[4] for x in q) / n,
            "btts": sum(x[5] for x in q) / n,
        }


class ContextState:
    def __init__(self) -> None:
        self.team: dict[tuple[str, str], TeamState] = defaultdict(TeamState)
        self.elo: dict[tuple[str, str], float] = defaultdict(lambda: 1500.0)
        self.comp_games: Counter[str] = Counter()
        self.comp_draws: Counter[str] = Counter()

    def features(self, r: dict[str, Any], q: dict[str, float], pick: str) -> list[float]:
        cid = str(r["competition_id"]); home = str(r["home_team"]); away = str(r["away_team"]); day = date_key(r["date"])
        hs = self.team[(cid, home)]; aas = self.team[(cid, away)]
        hr = TeamState.stats(hs.recent); ar = TeamState.stats(aas.recent)
        hhome = TeamState.stats(hs.home_recent); aaway = TeamState.stats(aas.away_recent)
        eh = self.elo[(cid, home)] + HOME_ELO_ADV; ea = self.elo[(cid, away)]
        sign = 1.0 if pick == "home" else -1.0
        pick_p = float(q[pick]); opp = "away" if pick == "home" else "home"; opp_p = float(q[opp]); dp = float(q["draw"])
        comp_n = int(self.comp_games[cid]); comp_draw = (self.comp_draws[cid] + 12.0 * 0.27) / (comp_n + 12.0)
        rest_h = days_between(hs.last_date, day); rest_a = days_between(aas.last_date, day)
        pick_ppg_gap = sign * (hr["ppg"] - ar["ppg"])
        pick_gd_gap = sign * (hr["gd"] - ar["gd"])
        venue_ppg_gap = sign * (hhome["ppg"] - aaway["ppg"])
        elo_pick_gap = sign * (eh - ea) / 400.0
        draw_avg = (hr["draw"] + ar["draw"]) / 2.0
        under_avg = (hr["under25"] + ar["under25"]) / 2.0
        total_avg = (hr["avg_total"] + ar["avg_total"]) / 2.0
        btts_avg = (hr["btts"] + ar["btts"]) / 2.0
        market_gap = pick_p - dp
        market_balance = abs(float(q["home"]) - float(q["away"]))
        rest_gap = abs(rest_h - rest_a) / 14.0
        log_draw_vs_pick = math.log(max(EPS, dp) / max(EPS, pick_p))
        # Raw + nonlinear basis. All are pre-match and frozen before the day's results.
        raw = [
            log_draw_vs_pick, dp, pick_p, opp_p, market_gap, market_balance,
            elo_pick_gap, abs(elo_pick_gap), pick_ppg_gap, abs(pick_ppg_gap),
            pick_gd_gap, abs(pick_gd_gap), venue_ppg_gap, draw_avg, under_avg,
            total_avg, btts_avg, rest_gap, comp_draw,
            dp * draw_avg, dp * under_avg, draw_avg * under_avg,
            market_gap * abs(elo_pick_gap), market_gap * abs(pick_ppg_gap),
            (total_avg - 2.5) ** 2,
        ]
        return [1.0] + raw

    def update_day(self, day_rows: list[dict[str, Any]]) -> None:
        elo_delta: dict[tuple[str, str], float] = defaultdict(float)
        for r in day_rows:
            cid = str(r["competition_id"]); h = str(r["home_team"]); a = str(r["away_team"])
            hg = int(r["hg"]); ag = int(r["ag"]); total = hg + ag
            hp = 3.0 if hg > ag else 1.0 if hg == ag else 0.0
            ap = 3.0 if ag > hg else 1.0 if hg == ag else 0.0
            dr = 1.0 if hg == ag else 0.0; u25 = 1.0 if total <= 2 else 0.0; btts = 1.0 if hg > 0 and ag > 0 else 0.0
            self.team[(cid,h)].recent.append((hp, float(hg-ag), dr, u25, float(total), btts))
            self.team[(cid,a)].recent.append((ap, float(ag-hg), dr, u25, float(total), btts))
            self.team[(cid,h)].home_recent.append((hp, float(hg-ag), dr, u25, float(total), btts))
            self.team[(cid,a)].away_recent.append((ap, float(ag-hg), dr, u25, float(total), btts))
            self.team[(cid,h)].last_date = date_key(r["date"]); self.team[(cid,a)].last_date = date_key(r["date"])
            rh = self.elo[(cid,h)]; ra = self.elo[(cid,a)]
            exp_h = 1.0/(1.0 + 10.0 ** (-(rh + HOME_ELO_ADV - ra)/400.0))
            obs_h = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
            de = ELO_K * (obs_h-exp_h); elo_delta[(cid,h)] += de; elo_delta[(cid,a)] -= de
            self.comp_games[cid] += 1; self.comp_draws[cid] += int(hg == ag)
        for k,v in elo_delta.items(): self.elo[k] += v


def sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-min(50.0, z)); return 1.0/(1.0+e)
    e = math.exp(max(-50.0, z)); return e/(1.0+e)


def fit_logistic(rows: list[dict[str, Any]], epochs: int = 80, lr: float = 0.15, l2: float = 0.02) -> dict[str, Any]:
    if not rows: raise RuntimeError("empty logistic training")
    p = len(rows[0]["x"]); n = len(rows)
    means = [0.0]*p; stds = [1.0]*p
    for j in range(1,p): means[j] = sum(float(r["x"][j]) for r in rows)/n
    for j in range(1,p):
        var = sum((float(r["x"][j])-means[j])**2 for r in rows)/n
        stds[j] = max(1e-6, math.sqrt(var))
    X = [[1.0] + [(float(r["x"][j])-means[j])/stds[j] for j in range(1,p)] for r in rows]
    y = [float(r["y"]) for r in rows]
    w = [0.0]*p
    # Initialize intercept at empirical class log-odds.
    py = min(0.99,max(0.01,sum(y)/n)); w[0] = math.log(py/(1-py))
    for ep in range(epochs):
        g = [0.0]*p
        for xi, yi in zip(X,y):
            pr = sigmoid(sum(a*b for a,b in zip(w,xi))); err = pr-yi
            for j in range(p): g[j] += err*xi[j]
        step = lr / math.sqrt(1.0 + ep/10.0)
        for j in range(p):
            reg = 0.0 if j == 0 else l2*w[j]
            w[j] -= step*(g[j]/n + reg)
    return {"weights":w,"means":means,"stds":stds,"n":n,"positive_rate":sum(y)/n}


def predict(model: dict[str,Any], x: list[float]) -> float:
    xx = [1.0]+[(float(x[j])-model["means"][j])/model["stds"][j] for j in range(1,len(x))]
    return sigmoid(sum(a*b for a,b in zip(model["weights"],xx)))


def metrics(rows: list[dict[str,Any]], threshold: float | None = None) -> dict[str,Any]:
    n=len(rows); base_hits=sum(int(r["actual"]==r["pick"]) for r in rows)
    if threshold is None:
        return {"count":n,"base_hits":base_hits,"base_accuracy":base_hits/n if n else None}
    overrides=[r for r in rows if float(r["draw_vs_correct_score"])>=threshold]
    cand_hits=0; rescued=lost=opposite_unchanged=0
    for r in rows:
        ov=float(r["draw_vs_correct_score"])>=threshold
        if ov:
            cand_hits += int(r["actual"]=="draw")
            rescued += int(r["actual"]=="draw")
            lost += int(r["actual"]==r["pick"])
            opposite_unchanged += int(r["actual"] not in {"draw",r["pick"]})
        else:
            cand_hits += int(r["actual"]==r["pick"])
    return {
        "count":n,"base_hits":base_hits,"base_accuracy":base_hits/n if n else None,
        "candidate_hits":cand_hits,"candidate_accuracy":cand_hits/n if n else None,
        "net_hits":cand_hits-base_hits,"accuracy_uplift_pp":100.0*(cand_hits-base_hits)/n if n else None,
        "override_count":len(overrides),"override_rate":len(overrides)/n if n else None,
        "rescued_draws":rescued,"lost_correct_home_away":lost,"opposite_home_away_still_wrong":opposite_unchanged,
        "override_net_gain":rescued-lost,
        "override_draw_precision_vs_all_overrides":rescued/len(overrides) if overrides else None,
        "override_draw_vs_basecorrect_precision":rescued/(rescued+lost) if (rescued+lost) else None,
    }


def main() -> int:
    freeze=json.loads(SELECTOR_FREEZE.read_text(encoding="utf-8"))
    if freeze.get("status")!="FROZEN" or float(freeze.get("selector_threshold") or 0)!=0.55:
        raise RuntimeError("selector freeze drift")
    all_rows, source_meta=core.read_rows()
    raw_cache, raw_meta=base.load_raw_row_cache(all_rows)
    by_day: dict[str,list[dict[str,Any]]]=defaultdict(list)
    for r in all_rows: by_day[date_key(r["date"])].append(r)
    state=ContextState(); train=[]; valid=[]; target=[]; market_seen=selected_seen=0
    for day in sorted(by_day):
        day_rows=sorted(by_day[day], key=lambda r:(str(r["competition_id"]),str(r["home_team"]),str(r["away_team"])))
        frozen_records=[]
        for r in day_rows:
            raw=raw_cache.get((str(r["source_file"]),int(r["row_index"])))
            if raw is None: continue
            ex=_extract_odds(raw)
            if ex is None: continue
            q,_provider=ex; q={d:float(q[d]) for d in D}; s=sum(q.values())
            if s<=0 or not math.isfinite(s): continue
            q={d:q[d]/s for d in D}; market_seen+=1
            dec=base.selector_decision(q,str(r["competition_id"]),freeze)
            if not bool(dec["selected"]): continue
            pick=str(dec["pick"])
            if pick=="draw": continue
            selected_seen+=1
            actual=base.result_actual(int(r["hg"]),int(r["ag"]))
            x=state.features(r,q,pick)
            rec={"date":day,"competition_id":str(r["competition_id"]),"season":str(r["season"]),"pick":pick,"actual":actual,"x":x,"q":q}
            # Binary residual training ignores opposite H/A because override utility is zero there.
            if actual in {"draw",pick}: rec["y"]=1 if actual=="draw" else 0
            frozen_records.append(rec)
        for rec in frozen_records:
            day=rec["date"]
            if day < TRAIN_END and "y" in rec: train.append(rec)
            elif TRAIN_END <= day < VALID_END: valid.append(rec)
            # Exact V6.50.6 target-season contract; do not use arbitrary calendar filter.
            cid=rec["competition_id"]
            if rec["season"]==base.TARGET_SEASONS.get(cid): target.append(rec)
        state.update_day(day_rows)

    model=fit_logistic(train)
    for r in valid: r["draw_vs_correct_score"]=predict(model,r["x"])
    for r in target: r["draw_vs_correct_score"]=predict(model,r["x"])

    # Coarse threshold is selected ONLY on 2024 validation. Bayes-neutral 0.50 is included;
    # lower thresholds are allowed only if validation empirically demonstrates positive net utility.
    curve=[]
    for k in range(35,76):
        th=k/100.0; m=metrics(valid,th); m["threshold"]=th
        m["eligible"] = bool(m["override_count"]>=20 and m["override_net_gain"]>0 and (m["override_draw_vs_basecorrect_precision"] or 0)>0.5)
        curve.append(m)
    eligible=[x for x in curve if x["eligible"]]
    chosen=max(eligible,key=lambda x:(x["net_hits"],x["candidate_accuracy"],-x["override_count"],x["threshold"])) if eligible else None
    chosen_th=float(chosen["threshold"]) if chosen else None
    target_base=metrics(target)
    target_result=metrics(target,chosen_th) if chosen_th is not None else {**target_base,"decision":"NO_VALIDATION_POSITIVE_OVERRIDE_RULE"}

    # Error decomposition makes the recurring failure mode explicit.
    base_errors=[r for r in target if r["actual"]!=r["pick"]]
    err=Counter("draw" if r["actual"]=="draw" else "opposite_home_away" for r in base_errors)
    by_comp={}
    if chosen_th is not None:
        for cid in sorted(set(r["competition_id"] for r in target)):
            by_comp[cid]=metrics([r for r in target if r["competition_id"]==cid],chosen_th)

    payload={
        "schema_version":"V6.50.7-draw-residual-net-gain-r1",
        "generated_at_utc":now(),"formal_current_version":"V5.0.1",
        "status":"PASS_RESEARCH_CHALLENGE" if chosen_th is not None else "REJECT_NO_VALIDATION_POSITIVE_RULE",
        "classification":"STRICT_PRE2025_TRAIN_2024_VALIDATION_2025_TARGET_DRAW_RESIDUAL_FORMAL_WEIGHT_0",
        "design":{
            "base_selector":"V6.47.5 frozen reliability selector threshold 0.55",
            "train_end_exclusive":TRAIN_END,"validation_end_exclusive":VALID_END,
            "target_contract":"V6.50.6 exact 2025 / 2025-26 seasons",
            "objective":"override utility +1 actual draw, -1 base-pick-correct, 0 opposite H/A",
            "model":"L2 logistic draw-vs-base-correct residual with market baseline plus orthogonal pre-match team-state context",
            "feature_count_including_intercept":25+1,
            "same_day_policy":"freeze all features then update all matches",
            "target_results_used_for_training_or_threshold":False,
            "market_probabilities_mutated":False,
        },
        "data":{"source_meta":source_meta,"raw_resolution":raw_meta,"market_rows_seen":market_seen,"selector_h_a_rows_seen":selected_seen,"train_relevant_n":len(train),"validation_selected_n":len(valid),"target_selected_n":len(target)},
        "model":{"training_n":model["n"],"training_draw_rate_vs_basecorrect":model["positive_rate"],"weights":model["weights"]},
        "validation":{"threshold_curve":curve,"chosen":chosen},
        "target_2025":{"base":target_base,"candidate":target_result,"base_error_decomposition":dict(err),"by_competition":by_comp},
        "gates":{
            "must_improve_target_net_hits": bool(chosen_th is not None and int(target_result.get("net_hits") or 0)>0),
            "must_not_reduce_target_accuracy": bool(chosen_th is not None and float(target_result.get("candidate_accuracy") or 0)>=float(target_base.get("base_accuracy") or 0)),
            "target_70pct_reached": bool(chosen_th is not None and float(target_result.get("candidate_accuracy") or 0)>=0.70),
            "formal_promotion_allowed":False,
        },
        "governance":{"research_only":True,"formal_weight":0,"automatic_promotion":False,"current_rule_change":False,"formal_probability_change":False,"formal_selector_threshold_change":False,"historical_replay_cannot_promote":True},
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":payload["status"],"train_n":len(train),"valid_n":len(valid),"target_n":len(target),"chosen":chosen,"target":target_result,"errors":dict(err)},ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
