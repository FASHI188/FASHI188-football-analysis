from __future__ import annotations

import argparse
import json
import math
import random
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Any

from coach_tactical_regime import TeamStyle,tactical_matchup
from football_context_translator import LayerAdjustment,build_plan,team_state
from identity_registry import IdentityError,IdentityRegistry
from lineup_scenarios import bench_substitution_profile,build_lineup_scenarios
from match_context import opponent_adjusted_mu,uncertainty_with_missing,venue_core_audit
from pit_feature_store import PITFeatureStore
from player_strength import DIMS,PlayerVector,lineup_components
from process_hazard import ProcessHazardError,process_feature_vector
from source_ingest import PITViolation,fact_from_mapping
from translator_adversarial_probe import run_probes
from translator_schema import canonical_sha
from v2_translator_integration import draw_structure


def read_jsonl(path:Path)->list[dict[str,Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def matrix_mean(m:list[list[float]])->tuple[float,float]:
    s=sum(map(sum,m))
    if s<=0:raise ValueError("matrix has zero mass")
    return sum(i*p for i,row in enumerate(m) for p in row)/s,sum(j*p for row in m for j,p in enumerate(row))/s


def ece(probs:list[float],outcomes:list[int],bins:int=10)->float:
    if not probs:return 0.0
    total=0.0;n=len(probs)
    for b in range(bins):
        lo,hi=b/bins,(b+1)/bins;idx=[i for i,p in enumerate(probs) if lo<=p<hi or (b==bins-1 and p==1.0)]
        if idx:
            mp=sum(probs[i] for i in idx)/len(idx);ar=sum(outcomes[i] for i in idx)/len(idx);total+=len(idx)/n*abs(mp-ar)
    return total


def binary_metrics(probs:list[float],outcomes:list[int],threshold:float|None=None)->dict[str,float]:
    n=max(len(probs),1);ll=-sum(y*math.log(max(p,1e-15))+(1-y)*math.log(max(1-p,1e-15)) for p,y in zip(probs,outcomes))/n;brier=sum((p-y)**2 for p,y in zip(probs,outcomes))/n
    out={"logloss":ll,"brier":brier,"ece":ece(probs,outcomes),"actual_rate":sum(outcomes)/n,"mean_probability":sum(probs)/n}
    if threshold is not None:
        tp=sum(1 for p,y in zip(probs,outcomes) if p>=threshold and y==1);fp=sum(1 for p,y in zip(probs,outcomes) if p>=threshold and y==0);fn=sum(1 for p,y in zip(probs,outcomes) if p<threshold and y==1)
        precision=tp/max(tp+fp,1);recall=tp/max(tp+fn,1);out.update({"threshold":threshold,"precision":precision,"recall":recall,"f1":2*precision*recall/max(precision+recall,1e-15),"tp":tp,"fp":fp,"fn":fn})
    return out


def tune_draw_threshold(dev_rows:list[dict[str,Any]],weights:list[list[float]])->float:
    from v2_translator_integration import head_predict
    probs=[];ys=[]
    for r in dev_rows:
        p=head_predict(float(r["mu_home"]),float(r["mu_away"]),0.0,float(r.get("uncertainty",0.0)),weights)["draw"];probs.append(p);ys.append(1 if int(r["target_class"])==1 else 0)
    best=(0.25,-1.0)
    for k in range(20,81):
        t=k/200.0;f1=binary_metrics(probs,ys,t)["f1"]
        if f1>best[1]+1e-15:best=(t,f1)
    return best[0]


def metrics(pred:list[dict[str,Any]],labels:dict[str,tuple[int,int]],draw_threshold:float)->dict[str,Any]:
    if not pred:return {"n":0}
    ll=br=rps=es=0.0;top=0;draw_p=[];draw_y=[];error_p=[];error_y=[];weak_p=[];weak_y=[];exact={"0-0":([],[]),"1-1":([],[]),"2-2":([],[])}
    for r in pred:
        hg,ag=labels[r["fixture_id"]];y="home" if hg>ag else "draw" if hg==ag else "away";p=r["one_x_two"]
        ll-=math.log(max(p[y],1e-15));br+=sum((p[k]-(1.0 if k==y else 0.0))**2 for k in ("home","draw","away"));c1=p["home"]-(1.0 if y=="home" else 0.0);c2=p["home"]+p["draw"]-(1.0 if y in {"home","draw"} else 0.0);rps+=(c1*c1+c2*c2)/2
        pred_cls=max(p,key=p.get);top+=pred_cls==y;error_p.append(1.0-max(p.values()));error_y.append(0 if pred_cls==y else 1);m=r["matrix"];prob=m[hg][ag] if hg<len(m) and ag<len(m[0]) else 1e-15;es-=math.log(max(prob,1e-15));draw_p.append(p["draw"]);draw_y.append(1 if y=="draw" else 0)
        for score in exact:
            a,b=map(int,score.split("-"));pp=m[a][b] if a<len(m) and b<len(m[0]) else 0.0;exact[score][0].append(pp);exact[score][1].append(1 if (hg,ag)==(a,b) else 0)
        weak=str(r.get("weak_side","home"));weak_p.append(p[weak]);weak_y.append(1 if y==weak else 0)
    n=len(pred)
    return {"n":n,"logloss":ll/n,"brier":br/n,"rps":rps/n,"top1":top/n,"exact_score_logloss":es/n,"draw":binary_metrics(draw_p,draw_y,draw_threshold),"exact_scores":{k:binary_metrics(v[0],v[1]) for k,v in exact.items()},"weak_team_win":binary_metrics(weak_p,weak_y),"uncertainty_calibration":{"top1_error_ece":ece(error_p,error_y),"mean_predicted_error":sum(error_p)/n,"actual_error_rate":sum(error_y)/n}}


def per_match_ll(pred:list[dict[str,Any]],labels:dict[str,tuple[int,int]])->list[float]:
    out=[]
    for r in pred:
        hg,ag=labels[r["fixture_id"]];y="home" if hg>ag else "draw" if hg==ag else "away";out.append(-math.log(max(r["one_x_two"][y],1e-15)))
    return out


def bootstrap_delta(candidate:list[float],baseline:list[float],seed:int=20260831,reps:int|None=None)->dict[str,float]:
    if len(candidate)!=len(baseline) or not candidate:raise ValueError("paired bootstrap requires nonempty equal vectors")
    rng=random.Random(seed);n=len(candidate);reps=reps or (5000 if n>=1000 else 2000);d=[x-y for x,y in zip(candidate,baseline)];point=sum(d)/n;vals=[]
    for _ in range(reps):vals.append(sum(d[rng.randrange(n)] for _ in range(n))/n)
    vals.sort();return {"delta":point,"lo":vals[int(.025*reps)],"hi":vals[min(reps-1,int(.975*reps))],"reps":reps}


def _groups(pred:list[dict[str,Any]])->dict[str,list[dict[str,Any]]]:
    out=defaultdict(list)
    for r in pred:
        for k in (f"league:{r.get('competition_id','?')}",f"season:{r.get('season','?')}",f"cold:{r.get('cold_start_bucket','?')}",f"grade:{r.get('coverage_grade','?')}"):out[k].append(r)
    return out


def group_gate(base:list[dict[str,Any]],cand:list[dict[str,Any]],labels:dict[str,tuple[int,int]],global_gain:float)->dict[str,Any]:
    bg=_groups(base);cg=_groups(cand);checks={};passed=True
    for k in sorted(set(bg)&set(cg)):
        if len(bg[k])<100 or len(cg[k])!=len(bg[k]):continue
        b=per_match_ll(bg[k],labels);c=per_match_ll(cg[k],labels);delta=sum(x-y for x,y in zip(c,b))/len(b);rec={"n":len(b),"delta_logloss":delta,"passes":True}
        if delta>0.0100:
            ci=bootstrap_delta(c,b);rec["bootstrap"]=ci;exception=ci["lo"]<=0<=ci["hi"] and global_gain>0.0030;rec["passes"]=exception;passed&=exception
        checks[k]=rec
    worst=max(checks.items(),key=lambda kv:kv[1]["delta_logloss"]) if checks else (None,None)
    return {"passed":passed,"groups":checks,"worst_delta_group":None if worst[0] is None else {"group":worst[0],**worst[1]}}


def acceptance_gate(base:list[dict[str,Any]],cand:list[dict[str,Any]],labels:dict[str,tuple[int,int]],draw_threshold:float,*,min_n:int=100)->dict[str,Any]:
    if [x["fixture_id"] for x in base]!=[x["fixture_id"] for x in cand]:raise ValueError("ablation cohort mismatch")
    n=len(base)
    if n<min_n:return {"accepted":False,"status":"INSUFFICIENT_SAMPLE","n":n,"minimum_n":min_n}
    bm=metrics(base,labels,draw_threshold);cm=metrics(cand,labels,draw_threshold);gain=bm["logloss"]-cm["logloss"];boot=bootstrap_delta(per_match_ll(cand,labels),per_match_ll(base,labels));gg=group_gate(base,cand,labels,gain)
    bcov=sum(r.get("coverage_grade")!="HARD_FAIL" for r in base)/n;ccov=sum(r.get("coverage_grade")!="HARD_FAIL" for r in cand)/n
    gates={"logloss_gain_ge_0_001":gain>=0.0010,"paired_bootstrap_hi_lt_0":boot["hi"]<0.0,"brier_nonharm":cm["brier"]<=bm["brier"]+0.0010,"rps_nonharm":cm["rps"]<=bm["rps"]+0.0010,"draw_logloss_nonharm":cm["draw"]["logloss"]<=bm["draw"]["logloss"]+0.0020,"draw_ece_nonharm":cm["draw"]["ece"]<=bm["draw"]["ece"]+0.010,"score_matrix_nonharm":cm["exact_score_logloss"]<=bm["exact_score_logloss"]+0.0050,"worst_group_nonharm":gg["passed"],"coverage_nonharm":ccov>=bcov-0.02,"uncertainty_nonharm":cm["uncertainty_calibration"]["top1_error_ece"]<=bm["uncertainty_calibration"]["top1_error_ece"]+0.010}
    ok=all(gates.values());return {"accepted":ok,"status":"ACCEPTED" if ok else "REJECTED_ABLATION","n":n,"logloss_gain":gain,"bootstrap":boot,"baseline_metrics":bm,"candidate_metrics":cm,"group_gate":gg,"coverage":{"baseline":bcov,"candidate":ccov},"gates":gates}


def _player(pid:str,team:str,role:str,value:float,exposure:float=10.0)->PlayerVector:
    vals={d:0.0 for d in DIMS};vals["on_ball_contribution"]=value;vals["off_ball_contribution"]=value;vals["goalkeeper_shot_stopping"]=value if role=="GK" else 0.0
    return PlayerVector(pid,team,"L",role,{role:1.0},vals,exposure,0.2,"2026-01-01T00:00:00Z","FULL_EVENT",[],canonical_sha({"v":1}),0)


def _lineup_rows(prefix:str,n:int=14)->list[dict[str,Any]]:
    out=[]
    for i in range(n):
        out.append({"player_id":f"{prefix}{i}","starting_probability":max(0.05,0.95-i*0.05),"availability_probability":1.0,"expected_minutes_distribution":{"mean":90.0 if i<11 else 25.0},"injury_status":"UNKNOWN","suspension_status":"UNKNOWN","return_status":"UNKNOWN","rotation_probability":min(0.95,0.05+i*0.05),"role_distribution":{"MID":1.0},"replacement_quality":0.0,"uncertainty":0.2,"known_at":"2026-08-30T12:00:00Z"})
    return out


class TranslatorUnitTests(unittest.TestCase):
    def test_identity_ambiguity(self):
        r=IdentityRegistry();r.register("player","1","Same Name");r.register("player","2","Same Name")
        with self.assertRaises(IdentityError):r.resolve("player","Same Name")
    def test_transfer_membership_time_boundary(self):
        r=IdentityRegistry();r.register("player","p","P");r.register("team","a","A");r.register("team","b","B");r.record_membership("p","a","2025-01-01T00:00:00Z","2025-07-01T00:00:00Z","permanent");r.record_membership("p","b","2025-07-01T00:00:00Z",None,"permanent");self.assertEqual(r.team_at("p","2025-06-01T00:00:00Z"),"a");self.assertEqual(r.team_at("p","2025-08-01T00:00:00Z"),"b")
    def test_lineup_probability_conservation(self):
        sc=build_lineup_scenarios(_lineup_rows("h"),_lineup_rows("a"),cutoff="2026-08-31T12:00:00Z");self.assertAlmostEqual(sum(x.probability for x in sc),1.0,places=12)
    def test_lineup_unknown(self):self.assertEqual(build_lineup_scenarios(None,None,cutoff="2026-08-31T12:00:00Z")[0].route,"LINEUP_UNKNOWN")
    def test_player_effect_is_replacement_relative(self):
        vec={"s":_player("s","T","MID",2.0),"b":_player("b","T","MID",1.0)};a,_,_,_=lineup_components(vec,["s"]);self.assertGreater(a,0);a2,_,_,_=lineup_components(vec,["b"]);self.assertLess(a2,0)
    def test_bench_profile_is_bounded(self):
        hp=_lineup_rows("h");ap=_lineup_rows("a");vec={r["player_id"]:_player(r["player_id"],"H" if r["player_id"].startswith("h") else "A","MID",float(i%5)) for i,r in enumerate(hp+ap)};b=bench_substitution_profile(hp,ap,vec,cutoff="2026-08-31T12:00:00Z");self.assertTrue(math.isfinite(b.log_mu_home_delta));self.assertLessEqual(abs(b.log_mu_home_delta),0.18)
    def test_tactical_swap_symmetry(self):
        v={k:0.0 for k in ("tempo","high_press","defensive_line_height","passing_directness","attacking_width","transition_attack","set_piece_attack","set_piece_defence","leading_contraction","trailing_risk","substitution_timing")};v1={**v,"high_press":1.0};v2={**v,"passing_directness":0.5};h=TeamStyle("H",v1,{}, {"other":1.0},10,0.2,None,"a"*64);a=TeamStyle("A",v2,{}, {"other":1.0},10,0.2,None,"b"*64);coef={"press_vs_buildup":0.05};d1=tactical_matchup(h,a,coef);d2=tactical_matchup(a,h,coef);self.assertAlmostEqual(d1[0],-d2[0],places=12);self.assertAlmostEqual(d1[1],-d2[1],places=12)
    def test_opponent_strength_direction(self):self.assertGreater(opponent_adjusted_mu(1.3,1.3,1.7),opponent_adjusted_mu(1.3,1.3,1.0))
    def test_venue_core_not_reapplied(self):self.assertEqual(venue_core_audit(1.5,1.1)["translator_extra_venue_multiplier"],1.0)
    def test_missing_uncertainty_only_increases(self):self.assertGreater(uncertainty_with_missing(0.2,3),uncertainty_with_missing(0.2,0))
    def test_process_future_rejected(self):
        rows=[{"team_id":"T","known_at":"2026-09-01T00:00:00Z","event_type":"red_card","minute":20,"match_id":"m"}]
        with self.assertRaises(ProcessHazardError):process_feature_vector(rows,"T",cutoff="2026-08-31T12:00:00Z")
    def test_duplicate_fact_rejected(self):
        row={"predicate":"injury","entity_type":"player","entity_id":"p","value":{"status":"out"},"provenance":{"source_url":"https://example.invalid/x","raw_sha256":"a"*64,"published_at":"2026-08-30T00:00:00Z","observed_at":"2026-08-30T00:00:00Z","retrieved_at":"2026-08-31T00:00:00Z","known_at":"2026-08-30T00:00:00Z","source_tier":"TIER_1_OFFICIAL","extraction_confidence":1.0,"provider_license":"public","immutable_source_ref":"x"}};f=fact_from_mapping(row,"2026-08-31T12:00:00Z");s=PITFeatureStore();s.append(f,"2026-08-31T12:00:00Z")
        with self.assertRaises(PITViolation):s.append(f,"2026-08-31T12:00:00Z")
    def test_draw_structure_probability_identity(self):
        m=[[0.2,0.1,0.0],[0.1,0.3,0.05],[0.0,0.05,0.2]];d=draw_structure(m);self.assertAlmostEqual(d["natural_draw"],0.7,places=12);self.assertAlmostEqual(d["p_0_0"]+d["p_1_1"]+d["p_2_2"]+d["other_draw"],d["natural_draw"],places=12)
    def test_adversarial(self):self.assertTrue(run_probes()["passed"])


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--unit",action="store_true");a=ap.parse_args()
    if a.unit:
        res=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TranslatorUnitTests));return 0 if res.wasSuccessful() else 1
    return 0


if __name__=="__main__":raise SystemExit(main())
