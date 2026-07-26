#!/usr/bin/env python3
"""V6.27.2 fixed-seed random100: Asian-handicap as the dedicated net-goal layer.

Architecture under test
-----------------------
1. Keep the existing formal 1X2 marginal EXACTLY unchanged.
2. Keep the V6.26 market-updated direct 0-7+ total-goals marginal EXACTLY unchanged.
3. Use the historical Asian-handicap closing line + two-way side prices only as a goal-difference
   constraint. It is not allowed to mutate 1X2 or total goals.
4. Exact score remains downstream of the three accepted heads.

AH transformation
-----------------
Legacy football-data.co.uk AH rows do not carry original quote timestamps and the normalized two-way
side price is not claimed to be an exact win probability when pushes/quarter-lines exist. Therefore
this is retrospective research only. We convert the de-vigged home-side share p_h into a signed
settlement moment target m=2*p_h-1. For every score cell, g(score,line)=home_win_fraction-
home_loss_fraction in [-1,1] using the repository's quarter-line settlement routine. The candidate
matrix is the alternating KL/I-projection onto:
  - formal 1X2 partition marginal,
  - V6.26 total-goal partition marginal,
  - E[g]=m.
The moment projection itself is the exact exponential tilt q_i proportional p_i*exp(lambda*g_i).
No blend weight or lambda is tuned on results; lambda is solved numerically from the market moment.

Sampling
--------
Enumerate legal pre-match rows with 1X2+O/U2.5+AH availability without looking at the target outcome,
shuffle once with seed 6260100, then take the first 100 successfully reconciled predictions. Same-day
match results are withheld until every prediction on that date is complete.

Governance
----------
Random100 is a diagnostic only. Historical market quotes lack original timestamps. Formal CURRENT
V5.0.1 and all formal weights remain unchanged regardless of result.
"""
from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import three_stage_core_v6260 as core  # noqa: E402
import validate_architecture_order_v6190 as arch  # noqa: E402
import validate_decoupled_1x2_total_fusion_v6191 as dec  # noqa: E402
import validate_joint_market_ipf_crossseason_v6164 as base  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
from football_v460_engine import load_config, predict_from_history  # noqa: E402
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import (  # noqa: E402
    canonical_team_name,
    derive_score_marginals,
    load_aliases,
    parse_match_date,
    read_processed_matches,
    settle_home_handicap,
)

OUT = ROOT / "manifests" / "v6_three_stage_ah_netgoal_random100_v6272_status.json"
SEED = 6260100
TARGET = 100
EPS = 1e-15
TOL = 2e-9
MAX_ITER = 2500

AH_SPECS = (
    ("AHCh", ("PCAHH", "PCAHA"), "Pinnacle_closing"),
    ("AHCh", ("B365CAHH", "B365CAHA"), "Bet365_closing"),
    ("AHCh", ("AvgCAHH", "AvgCAHA"), "Average_closing"),
    ("AHCh", ("MaxCAHH", "MaxCAHA"), "Maximum_closing"),
    ("AHh", ("PAHH", "PAHA"), "Pinnacle"),
    ("AHh", ("B365AHH", "B365AHA"), "Bet365"),
    ("AHh", ("AvgAHH", "AvgAHA"), "Average"),
    ("AHh", ("MaxAHH", "MaxAHA"), "Maximum"),
    ("BbAHh", ("BbAvAHH", "BbAvAHA"), "Betbrain_average"),
    ("BbAHh", ("BbMxAHH", "BbMxAHA"), "Betbrain_maximum"),
)


def fv(x: Any) -> float | None:
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def odds(x: Any) -> float | None:
    v = fv(x)
    return v if v is not None and v > 1.0 else None


def ah_lookup(cid: str, season: str) -> dict[tuple[str, str, str], dict[str, Any]]:
    aliases = load_aliases()
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    d = ROOT / "processed" / cid
    if not d.exists():
        return out
    for path in sorted(d.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for raw0 in csv.DictReader(fh):
                raw = {str(k): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                s = str(raw.get("season") or raw.get("Season") or "").strip()
                if s != season or not raw.get("Date") or not raw.get("HomeTeam") or not raw.get("AwayTeam"):
                    continue
                chosen = None
                for line_col, (hc, ac), source in AH_SPECS:
                    line = fv(raw.get(line_col)); oh = odds(raw.get(hc)); oa = odds(raw.get(ac))
                    if line is None or oh is None or oa is None or abs(line) > 6.0:
                        continue
                    rh, ra = 1.0 / oh, 1.0 / oa
                    z = rh + ra
                    chosen = {"line": float(line), "home_side_share": rh / z, "away_side_share": ra / z, "source": source}
                    break
                if chosen is None:
                    continue
                try:
                    di = parse_match_date(raw["Date"], s).isoformat()
                except Exception:
                    continue
                h = canonical_team_name(cid, raw["HomeTeam"], aliases)
                a = canonical_team_name(cid, raw["AwayTeam"], aliases)
                out[(di, h, a)] = chosen
    return out


def result_idx(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def total_idx(h: int, a: int) -> int:
    return min(7, h + a)


def copy_matrix(matrix: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    out = [{"home_goals": int(c["home_goals"]), "away_goals": int(c["away_goals"]), "probability": float(c["probability"])} for c in matrix]
    z = sum(float(c["probability"]) for c in out)
    if z <= 0 or not math.isfinite(z):
        raise RuntimeError("invalid matrix mass")
    for c in out:
        c["probability"] = float(c["probability"]) / z
    return out


def scale_partition(matrix: list[dict[str, float | int]], fn, targets: list[float]) -> None:
    cur = [0.0] * len(targets)
    for c in matrix:
        cur[fn(int(c["home_goals"]), int(c["away_goals"]))] += float(c["probability"])
    fac = []
    for have, want in zip(cur, targets):
        if have <= EPS:
            if want > TOL:
                raise RuntimeError("zero support for required marginal")
            fac.append(1.0)
        else:
            fac.append(float(want) / have)
    for c in matrix:
        c["probability"] = float(c["probability"]) * fac[fn(int(c["home_goals"]), int(c["away_goals"]))]


def settlement_score(h: int, a: int, line: float) -> float:
    s = settle_home_handicap(h, a, line)
    return float(s["win"] - s["loss"])


def settlement_vector(matrix: list[dict[str, Any]], line: float) -> list[float]:
    out = [0.0, 0.0, 0.0]  # win, push, loss
    for c in matrix:
        s = settle_home_handicap(int(c["home_goals"]), int(c["away_goals"]), line)
        p = float(c["probability"])
        out[0] += p * float(s["win"]); out[1] += p * float(s["push"]); out[2] += p * float(s["loss"])
    return out


def settlement_moment(matrix: list[dict[str, Any]], line: float) -> float:
    v = settlement_vector(matrix, line)
    return v[0] - v[2]


def tilt_moment(matrix: list[dict[str, float | int]], line: float, target: float) -> None:
    gs = [settlement_score(int(c["home_goals"]), int(c["away_goals"]), line) for c in matrix]
    ps = [float(c["probability"]) for c in matrix]
    support = [g for g, p in zip(gs, ps) if p > EPS]
    if not support or target < min(support) - 1e-12 or target > max(support) + 1e-12:
        raise RuntimeError("AH moment outside prior support")

    def moment(lam: float) -> tuple[float, list[float]]:
        logs = [math.log(max(EPS, p)) + lam * g for p, g in zip(ps, gs)]
        m = max(logs)
        ws = [math.exp(x - m) for x in logs]
        z = sum(ws)
        probs = [w / z for w in ws]
        return sum(q * g for q, g in zip(probs, gs)), probs

    m0, q0 = moment(0.0)
    if abs(m0 - target) <= TOL:
        for c, q in zip(matrix, q0): c["probability"] = q
        return
    lo, hi = -1.0, 1.0
    mlo, _ = moment(lo); mhi, _ = moment(hi)
    for _ in range(30):
        if mlo <= target <= mhi:
            break
        if target < mlo:
            hi = lo; mhi = mlo; lo *= 2.0; mlo, _ = moment(lo)
        else:
            lo = hi; mlo = mhi; hi *= 2.0; mhi, _ = moment(hi)
    if not (mlo <= target <= mhi):
        raise RuntimeError("failed to bracket AH moment")
    probs = q0
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        mm, probs = moment(mid)
        if abs(mm - target) <= TOL:
            break
        if mm < target: lo = mid
        else: hi = mid
    for c, q in zip(matrix, probs):
        c["probability"] = q


def reconcile_ah(prior: list[dict[str, Any]], target_one: list[float], target_total: list[float], line: float, home_share: float):
    one = [float(x) / sum(target_one) for x in target_one]
    total = [float(x) / sum(target_total) for x in target_total]
    target_m = 2.0 * float(home_share) - 1.0
    q = copy_matrix(prior)
    residual = math.inf
    for it in range(1, MAX_ITER + 1):
        scale_partition(q, result_idx, one)
        scale_partition(q, total_idx, total)
        tilt_moment(q, line, target_m)
        qo = core.one_x_two_vector(q); qt = core.total_goals_vector(q); qm = settlement_moment(q, line)
        residual = max(max(abs(a-b) for a,b in zip(qo,one)), max(abs(a-b) for a,b in zip(qt,total)), abs(qm-target_m), abs(sum(float(c["probability"]) for c in q)-1.0))
        if residual <= TOL:
            z = sum(float(c["probability"]) for c in q)
            for c in q: c["probability"] = float(c["probability"]) / z
            return q, {"converged": True, "iterations": it, "max_residual": residual, "target_signed_settlement": target_m, "final_signed_settlement": settlement_moment(q,line)}
    return q, {"converged": False, "iterations": MAX_ITER, "max_residual": residual, "target_signed_settlement": target_m, "final_signed_settlement": settlement_moment(q,line)}


def gd_vector(matrix: list[dict[str, Any]], lo: int = -8, hi: int = 8) -> list[float]:
    out = [0.0] * (hi - lo + 1)
    for c in matrix:
        d = int(c["home_goals"]) - int(c["away_goals"])
        d = min(hi, max(lo, d))
        out[d - lo] += float(c["probability"])
    return out


def rps(prob: list[float], actual_idx: int) -> float:
    cp=co=score=0.0
    for i in range(len(prob)-1):
        cp += prob[i]; co += 1.0 if actual_idx == i else 0.0; score += (cp-co)**2
    return score / max(1,len(prob)-1)


def joint_log(matrix: list[dict[str, Any]], hg: int, ag: int) -> float:
    p = sum(float(c["probability"]) for c in matrix if int(c["home_goals"])==hg and int(c["away_goals"])==ag)
    return -math.log(max(EPS,p))


def brier_frac(pred: list[float], obs: list[float]) -> float:
    return sum((float(a)-float(b))**2 for a,b in zip(pred,obs))


def direction_hit(pred_signed: float, actual_signed: float) -> int | None:
    if abs(actual_signed) <= 1e-12 or abs(pred_signed) <= 1e-12:
        return None
    return int((pred_signed > 0) == (actual_signed > 0))


def avg(rows: list[dict[str,Any]], key: str) -> float | None:
    vals=[float(r[key]) for r in rows if r.get(key) is not None]
    return sum(vals)/len(vals) if vals else None


def main() -> int:
    cfg=load_config(); warmc=int(cfg["validation"]["warmup_competition_matches"]); warmt=int(cfg["validation"]["warmup_team_matches"])
    candidates=[]; packs={}; ah_sources=Counter()
    for season in dec.SEASONS:
        for cid in dec.COMPS:
            mk=base.market_lookup(cid,season); ah=ah_lookup(cid,season); params=ou.params_by_season(cid).get(season)
            if not params: continue
            matches=[m for m in read_processed_matches(cid) if str(m.season)==season]; bydate=defaultdict(list)
            for m in matches: bydate[m.date].append(m)
            hist=[]; hc=Counter(); ac=Counter(); ids=[]
            for dt in sorted(bydate):
                day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
                for m in day:
                    key3=(m.date.isoformat(),m.home_team,m.away_team)
                    if len(hist)>=warmc and hc[m.home_team]>=warmt and ac[m.away_team]>=warmt and key3 in mk and key3 in ah:
                        key=(season,cid,*key3); candidates.append(key); ids.append(key); ah_sources[ah[key3]["source"]]+=1
                for m in day: hist.append(m); hc[m.home_team]+=1; ac[m.away_team]+=1
            packs[(season,cid)]={"market":mk,"ah":ah,"params":params,"matches":matches,"candidate_ids":set(ids),"temperature":ou.calibrator(cid,season)}

    order=list(candidates); random.Random(SEED).shuffle(order); rank={k:i for i,k in enumerate(order)}; wanted=set(order)
    produced={}; failures=Counter(); max1=maxT=maxAH=maxMass=0.0
    for (season,cid),pack in packs.items():
        if not wanted.intersection(pack["candidate_ids"]): continue
        bydate=defaultdict(list)
        for m in pack["matches"]: bydate[m.date].append(m)
        hist=[]
        for dt in sorted(bydate):
            day=sorted(bydate[dt],key=lambda x:(x.home_team,x.away_team))
            for m in day:
                key=(season,cid,m.date.isoformat(),m.home_team,m.away_team)
                if key not in wanted: continue
                key3=(m.date.isoformat(),m.home_team,m.away_team); mk=pack["market"].get(key3); ah=pack["ah"].get(key3)
                try: pred=predict_from_history(hist,cid,season,m.home_team,m.away_team,m.date,selected_parameters=pack["params"],use_team_effects=True)
                except Exception: pred=None
                if not pred: failures["formal_prior"]+=1; continue
                prior=temperature_scale_matrix(pred["probabilities"]["score_matrix"],pack["temperature"]); formal_one=arch.one_vec(prior); marg=derive_score_marginals(prior); td=ou.project(marg["total_goals"],float(mk["p_over25"]))
                if td is None: failures["total_projection"]+=1; continue
                target_total=[float(td[k]) for k in ou.TOTAL_KEYS]
                try: baseline,ba=core.reconcile(prior,formal_one,target_total)
                except Exception: baseline,ba=None,{"converged":False}
                if baseline is None or not ba.get("converged"): failures["baseline_reconciliation"]+=1; continue
                try: candidate,ca=reconcile_ah(baseline,formal_one,target_total,float(ah["line"]),float(ah["home_side_share"]))
                except Exception: candidate,ca=None,{"converged":False}
                if candidate is None or not ca.get("converged"): failures["ah_reconciliation"]+=1; continue

                bo=core.one_x_two_vector(baseline); co=core.one_x_two_vector(candidate); bt=core.total_goals_vector(baseline); ct=core.total_goals_vector(candidate)
                line=float(ah["line"]); bset=settlement_vector(baseline,line); cset=settlement_vector(candidate,line); aset=settle_home_handicap(m.home_goals,m.away_goals,line); obs=[float(aset[k]) for k in ("win","push","loss")]
                bs=bset[0]-bset[2]; cs=cset[0]-cset[2]; actual_signed=obs[0]-obs[2]
                bgd=gd_vector(baseline); cgd=gd_vector(candidate); actual_d=max(-8,min(8,m.home_goals-m.away_goals))+8
                max1=max(max1,max(abs(a-b) for a,b in zip(co,formal_one))); maxT=max(maxT,max(abs(a-b) for a,b in zip(ct,target_total))); maxAH=max(maxAH,abs(cs-(2*float(ah["home_side_share"])-1))); maxMass=max(maxMass,abs(sum(float(c["probability"]) for c in candidate)-1.0))
                produced[key]={
                  "date":m.date.isoformat(),"competition_id":cid,"season":season,"home":m.home_team,"away":m.away_team,"actual_score":[m.home_goals,m.away_goals],"ah_line_home":line,"ah_home_side_share":float(ah["home_side_share"]),"ah_source":ah["source"],
                  "baseline_ah_brier":brier_frac(bset,obs),"candidate_ah_brier":brier_frac(cset,obs),"baseline_ah_signed_mse":(bs-actual_signed)**2,"candidate_ah_signed_mse":(cs-actual_signed)**2,
                  "baseline_ah_direction_hit":direction_hit(bs,actual_signed),"candidate_ah_direction_hit":direction_hit(cs,actual_signed),
                  "baseline_gd_rps":rps(bgd,actual_d),"candidate_gd_rps":rps(cgd,actual_d),"baseline_gd_logloss":-math.log(max(EPS,bgd[actual_d])),"candidate_gd_logloss":-math.log(max(EPS,cgd[actual_d])),
                  "baseline_score_top1":arch.score_topk(baseline,1,m.home_goals,m.away_goals),"candidate_score_top1":arch.score_topk(candidate,1,m.home_goals,m.away_goals),"baseline_score_top3":arch.score_topk(baseline,3,m.home_goals,m.away_goals),"candidate_score_top3":arch.score_topk(candidate,3,m.home_goals,m.away_goals),"baseline_joint_log":joint_log(baseline,m.home_goals,m.away_goals),"candidate_joint_log":joint_log(candidate,m.home_goals,m.away_goals),
                  "iterations":int(ca.get("iterations") or 0),"max_residual":float(ca.get("max_residual") or 0.0)}
            for m in day: hist.append(m)

    rows=sorted(produced.values(),key=lambda r:rank[(r["season"],r["competition_id"],r["date"],r["home"],r["away"])])[:TARGET]
    summary={"count":len(rows)}
    for prefix in ("baseline","candidate"):
        for metric in ("ah_brier","ah_signed_mse","ah_direction_hit","gd_rps","gd_logloss","score_top1","score_top3","joint_log"):
            summary[f"{prefix}_{metric}"]=avg(rows,f"{prefix}_{metric}")
    summary["delta_ah_direction_pp"] = ((summary["candidate_ah_direction_hit"] or 0)-(summary["baseline_ah_direction_hit"] or 0))*100
    summary["delta_score_top1_pp"] = ((summary["candidate_score_top1"] or 0)-(summary["baseline_score_top1"] or 0))*100
    summary["delta_score_top3_pp"] = ((summary["candidate_score_top3"] or 0)-(summary["baseline_score_top3"] or 0))*100
    checks={
      "sample_100":len(rows)==TARGET,
      "one_x_two_invariant":max1<=TOL,
      "total_invariant":maxT<=TOL,
      "ah_moment_fitted":maxAH<=TOL,
      "ah_brier_improves":summary.get("candidate_ah_brier") is not None and summary["candidate_ah_brier"]<summary["baseline_ah_brier"],
      "ah_signed_mse_improves":summary.get("candidate_ah_signed_mse") is not None and summary["candidate_ah_signed_mse"]<summary["baseline_ah_signed_mse"],
      "goal_difference_rps_improves":summary.get("candidate_gd_rps") is not None and summary["candidate_gd_rps"]<summary["baseline_gd_rps"],
      "ah_direction_noninferior":summary.get("candidate_ah_direction_hit") is not None and summary["candidate_ah_direction_hit"]>=summary["baseline_ah_direction_hit"]-1e-12,
      "joint_log_nonworse":summary.get("candidate_joint_log") is not None and summary["candidate_joint_log"]<=summary["baseline_joint_log"]+1e-12,
    }
    report={
      "schema_version":"V6.27.2-ah-netgoal-fixed-seed-random100-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS" if len(rows)==TARGET else "PARTIAL","formal_current_version":"V5.0.1","classification":"RETROSPECTIVE_FIXED_SEED_RANDOM100_AH_NETGOAL_NO_ORIGINAL_MARKET_TIMESTAMP","seed":SEED,"target":TARGET,"candidate_population":len(candidates),"failures":dict(failures),"ah_source_candidate_counts":dict(ah_sources),
      "audit":{"max_1x2_invariant_residual":max1,"max_total_invariant_residual":maxT,"max_ah_moment_residual":maxAH,"max_probability_mass_residual":maxMass,"same_day_history_frozen":True,"ah_role":"DEDICATED_NET_GOAL_LAYER_RESEARCH_ONLY","normalized_two_way_ah_share_is_not_claimed_exact_push_adjusted_probability":True},
      "summary":summary,"continuation_gate":{"checks":checks,"passed":all(checks.values()),"on_failure":"DO_NOT_PROMOTE_AH_AS_HARD_NETGOAL_CONSTRAINT"},"sample":rows,
      "governance":{"research_only":True,"formal_weight":0,"current_rule_change":False,"historical_market_quotes_lack_original_timestamp":True,"random100_is_diagnostic_only":True,"automatic_promotion":False,"one_x_two_locked":True,"total_goals_locked":True}
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"candidate_population":len(candidates),"failures":dict(failures),"audit":report["audit"],"summary":summary,"continuation_gate":report["continuation_gate"]},ensure_ascii=False,indent=2))
    return 0 if len(rows)==TARGET else 2

if __name__=="__main__": raise SystemExit(main())
