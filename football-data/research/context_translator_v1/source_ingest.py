from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ALLOWED_TIERS = {"TIER_1_OFFICIAL", "TIER_2_OPEN_STRUCTURED", "TIER_3_APPROVED_ARCHIVE"}
ALLOWED_PREDICATES = {
    "schedule", "prior_result", "prior_event", "prior_lineup", "prior_minutes",
    "injury", "suspension", "expected_return", "coach_change", "expected_lineup",
    "confirmed_lineup", "venue", "weather", "competition_rule", "referee",
    "geospatial", "tracking", "process_event",
}
PROHIBITED_KEYS = {
    "home_goals", "away_goals", "final_score", "result", "target_result",
    "actual_substitution", "actual_red_card", "actual_var", "actual_stoppage",
}
REQUIRED_PROVENANCE = {
    "source_url", "raw_sha256", "published_at", "observed_at", "retrieved_at",
    "known_at", "source_tier", "extraction_confidence", "provider_license",
    "immutable_source_ref",
}

SB_REPO = "hudl/open-data"
SB_COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
SB_COMP, SB_SEASON, RELEASE_HOURS, COMMON_N = 9, 281, 6, 306
LAYERS = (
    "0_v2_team_core", "1_player_capability", "2_expected_lineup", "3_confirmed_lineup",
    "4_bench_substitution", "5_coach_regime", "6_tactical_matchup",
    "7_fitness_schedule_travel", "8_referee_competition_environment", "9_pre_match_process_hazard",
)
ALIASES = {
    "Werder Bremen":"Werder Bremen","Bayern Munich":"Bayern München","FC Bayern Munich":"Bayern München",
    "Augsburg":"FC Augsburg","FC Augsburg":"FC Augsburg","Borussia Mönchengladbach":"M'gladbach",
    "TSG Hoffenheim":"Hoffenheim","Hoffenheim":"Hoffenheim","SC Freiburg":"Freiburg","Freiburg":"Freiburg",
    "Bayer Leverkusen":"Leverkusen","RB Leipzig":"RB Leipzig","Wolfsburg":"Wolfsburg","VfL Wolfsburg":"Wolfsburg",
    "FC Heidenheim":"Heidenheim","1. FC Heidenheim":"Heidenheim","Borussia Dortmund":"Dortmund",
    "FC Köln":"Köln","1. FC Köln":"Köln","Union Berlin":"Union Berlin","1. FC Union Berlin":"Union Berlin",
    "Mainz 05":"Mainz","FSV Mainz 05":"Mainz","1. FSV Mainz 05":"Mainz","Bochum":"Bochum",
    "VfL Bochum 1848":"Bochum","VfL Bochum":"Bochum","VfB Stuttgart":"Stuttgart",
    "Eintracht Frankfurt":"Frankfurt","Darmstadt 98":"Darmstadt","SV Darmstadt 98":"Darmstadt",
}


class PITViolation(RuntimeError):
    pass


def _dt(value: str | None, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PITViolation(f"{field} must be timezone-aware ISO datetime")
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PITViolation(f"invalid {field}: {value!r}") from exc
    if out.tzinfo is None or out.utcoffset() is None:
        raise PITViolation(f"{field} missing timezone")
    return out.astimezone(timezone.utc)


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Provenance:
    source_url: str
    raw_sha256: str
    published_at: str | None
    observed_at: str | None
    retrieved_at: str
    known_at: str
    source_tier: str
    extraction_confidence: float
    provider_license: str
    immutable_source_ref: str

    def validate(self, cutoff: str) -> None:
        if self.source_tier not in ALLOWED_TIERS:
            raise PITViolation(f"source tier denied: {self.source_tier}")
        if len(self.raw_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.raw_sha256):
            raise PITViolation("raw_sha256 must be lowercase 64-hex")
        if not self.source_url or not self.provider_license or not self.immutable_source_ref:
            raise PITViolation("source URL, license and immutable source ref are required")
        if not isinstance(self.extraction_confidence, (int, float)) or isinstance(self.extraction_confidence, bool):
            raise PITViolation("extraction_confidence must be numeric")
        if not 0.0 <= float(self.extraction_confidence) <= 1.0:
            raise PITViolation("extraction_confidence outside [0,1]")
        known, co = _dt(self.known_at, "known_at"), _dt(cutoff, "cutoff")
        _dt(self.retrieved_at, "retrieved_at")
        _dt(self.published_at, "published_at", nullable=True)
        _dt(self.observed_at, "observed_at", nullable=True)
        if known >= co:
            raise PITViolation(f"known_at must be strictly before cutoff: {known} >= {co}")


@dataclass(frozen=True)
class RawFact:
    predicate: str
    entity_type: str
    entity_id: str
    value: Any
    provenance: Provenance

    def validate(self, cutoff: str) -> None:
        if self.predicate not in ALLOWED_PREDICATES:
            raise PITViolation(f"predicate default-denied: {self.predicate}")
        if not self.entity_type or not self.entity_id:
            raise PITViolation("entity identity missing")
        if isinstance(self.value, dict) and PROHIBITED_KEYS.intersection(self.value):
            raise PITViolation(f"target/post-match field denied: {sorted(PROHIBITED_KEYS.intersection(self.value))}")
        self.provenance.validate(cutoff)

    def sha256(self) -> str:
        return canonical_sha({"predicate":self.predicate,"entity_type":self.entity_type,"entity_id":self.entity_id,
                              "value":self.value,"provenance":asdict(self.provenance)})


def fact_from_mapping(row: dict[str, Any], cutoff: str) -> RawFact:
    allowed = {"predicate", "entity_type", "entity_id", "value", "provenance"}
    if set(row) != allowed:
        raise PITViolation(f"raw fact schema mismatch extra/missing={sorted(set(row) ^ allowed)}")
    prov = row["provenance"]
    if not isinstance(prov, dict) or set(prov) != REQUIRED_PROVENANCE:
        raise PITViolation("provenance schema mismatch")
    fact = RawFact(str(row["predicate"]), str(row["entity_type"]), str(row["entity_id"]), row["value"], Provenance(**prov))
    fact.validate(cutoff)
    return fact


def ingest(rows: Iterable[dict[str, Any]], cutoff: str) -> list[RawFact]:
    out = [fact_from_mapping(r, cutoff) for r in rows]
    hashes = [f.sha256() for f in out]
    if len(hashes) != len(set(hashes)):
        raise PITViolation("duplicate raw fact payload")
    return out


# Round-2 historical PIT harness. Raw StatsBomb is never uploaded; only hashes/derived output are sealed.
def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _dump(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _url(rel: str) -> str:
    return f"https://raw.githubusercontent.com/{SB_REPO}/{SB_COMMIT}/{rel}"


def _get(rel: str) -> bytes:
    last = None
    for i in range(5):
        try:
            req = urllib.request.Request(_url(rel), headers={"User-Agent":"football3-translator-v1-research"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as exc:
            last = exc
            time.sleep(min(2 ** i, 12))
    raise PITViolation(f"download failed {rel}: {last}")


def _fetch_one(root: Path, rel: str) -> tuple[str, dict[str, Any]]:
    raw = _get(rel)
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return rel, {"url":_url(rel),"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)}


def download_statsbomb(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    meta = {}
    for rel in ("README.md","data/competitions.json",f"data/matches/{SB_COMP}/{SB_SEASON}.json"):
        k, v = _fetch_one(root, rel); meta[k] = v
    comps = json.loads((root/"data/competitions.json").read_text())
    if sum(int(x.get("competition_id",-1))==SB_COMP and int(x.get("season_id",-1))==SB_SEASON for x in comps) != 1:
        raise PITViolation("StatsBomb exact competition/season missing")
    mp = root/f"data/matches/{SB_COMP}/{SB_SEASON}.json"
    matches = json.loads(mp.read_text())
    if len(matches) != COMMON_N:
        raise PITViolation(f"StatsBomb match cardinality {len(matches)} != {COMMON_N}")
    safe = []
    for m in matches:
        safe.append({"match_id":int(m["match_id"]),"match_date":m["match_date"],
                     "home_team_id":int(m["home_team"]["home_team_id"]),"home_team_name":m["home_team"]["home_team_name"],
                     "away_team_id":int(m["away_team"]["away_team_id"]),"away_team_name":m["away_team"]["away_team_name"]})
    if len({x["match_id"] for x in safe}) != COMMON_N:
        raise PITViolation("duplicate StatsBomb match id")
    _dump(root/"safe_match_index.json", sorted(safe,key=lambda x:(x["match_date"],x["match_id"])))
    rels = [f"data/{kind}/{mid}.json" for mid in sorted(x["match_id"] for x in safe) for kind in ("events","lineups")]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_fetch_one, root, rel) for rel in rels]
        for fut in as_completed(futs):
            k, v = fut.result(); meta[k] = v
    # Remove post-match match objects and metadata before the prediction process starts.
    mp.unlink(); (root/"README.md").unlink(); (root/"data/competitions.json").unlink()
    out = {"schema_version":"football3-statsbomb-source-v1","repository":SB_REPO,"exact_commit":SB_COMMIT,
           "competition_id":SB_COMP,"season_id":SB_SEASON,"n":COMMON_N,
           "safe_index_sha256":_sha_file(root/"safe_match_index.json"),"retrieved_at":_iso(datetime.now(timezone.utc)),
           "source_tier":"TIER_2_OPEN_STRUCTURED",
           "provider_license":"StatsBomb Open Data; research/public-interest use with attribution per exact-commit README",
           "strict_prospective":False,"availability_basis":"SIMULATED_POST_MATCH_RELEASE_PLUS_6H",
           "raw_source_redistributed_in_artifact":False,"files":{k:meta[k] for k in sorted(meta)}}
    out["manifest_sha256"] = canonical_sha(out)
    _dump(root/"source_manifest.json", out)
    return out


def _verified(root: Path, sm: dict[str, Any], rel: str) -> Any:
    p = root/rel
    if rel not in sm["files"] or not p.exists() or _sha_file(p) != sm["files"][rel]["sha256"]:
        raise PITViolation(f"source hash/file mismatch {rel}")
    return json.loads(p.read_text())


def _alias(name: str) -> str:
    if name not in ALIASES:
        raise PITViolation(f"unregistered exact team alias {name!r}")
    return ALIASES[name]


def _common(feats: list[dict[str, Any]], safe: list[dict[str, Any]]) -> list[tuple[dict[str, Any],dict[str, Any]]]:
    vf = [r for r in feats if str(r["competition_id"])=="GER1" and str(r["season"])=="2023/24"]
    if len(vf) != COMMON_N:
        raise PITViolation(f"sealed V2 common cohort {len(vf)} != {COMMON_N}")
    idx = {}
    for s in safe:
        k=(s["match_date"],_alias(s["home_team_name"]),_alias(s["away_team_name"]))
        if k in idx: raise PITViolation(f"ambiguous identity {k}")
        idx[k]=s
    out=[]
    for r in sorted(vf,key=lambda x:(x["cutoff"],x["fixture_id"])):
        k=(str(r.get("date") or r["cutoff"][:10]),str(r["home_team"]),str(r["away_team"]))
        if k not in idx: raise PITViolation(f"identity miss {k}")
        out.append((r,idx[k]))
    if len({s["match_id"] for _,s in out}) != COMMON_N: raise PITViolation("identity reuse")
    return out


def _mins(text: str | None, default: float) -> float:
    if not text: return default
    try:
        a,b=text.split(":",1); return float(a)+float(b)/60
    except Exception: return default


def _history(events: list[dict[str,Any]], lineups: list[dict[str,Any]], known_at: str,
             home_tid: str, away_tid: str) -> tuple[list[dict[str,Any]],dict[str,list[dict[str,Any]]],dict[str,Any]]:
    duration=max(90.0,max((float(e.get("minute",0))+float(e.get("second",0))/60 for e in events),default=90.0))
    vals=defaultdict(lambda:defaultdict(float)); xg=defaultdict(float)
    for e in events:
        pid=str((e.get("player") or {}).get("id","")); tid=str((e.get("team") or {}).get("id",""))
        if not pid: continue
        typ=str((e.get("type") or {}).get("name","")); v=vals[pid]
        if typ=="Shot":
            sh=e.get("shot") or {}; q=float(sh.get("statsbomb_xg",0) or 0); goal=str((sh.get("outcome") or {}).get("name",""))=="Goal"
            v["shot_generation"]+=.15+.35*q; v["finishing"]+=.35*(float(goal)-q); v["current_form"]+=.2*q+.2*float(goal); xg[tid]+=q
        elif typ=="Pass":
            pa=e.get("pass") or {}; v["passing_progression"]+=.025 if not pa.get("outcome") else -.01
            v["chance_creation"]+=.18*bool(pa.get("shot_assist"))+.32*bool(pa.get("goal_assist"))
        elif typ=="Carry": v["carrying_progression"]+=.04
        elif typ=="Pressure": v["pressing"]+=.025; v["off_ball_contribution"]+=.018
        elif typ in {"Interception","Ball Recovery"}: v["tackling_interception"]+=.08; v["off_ball_contribution"]+=.05
        elif typ in {"Block","Clearance"}: v["defensive_position_protection"]+=.06
        elif typ in {"Miscontrol","Dispossessed"}: v["possession_retention_risk"]-=.08
        elif typ=="Goal Keeper": v["goalkeeper_shot_stopping"]+=.05
        if typ in {"Shot","Pass","Carry"}: v["on_ball_contribution"]+=.02
    rows=[]; usage=defaultdict(list); starters=defaultdict(list)
    for team in lineups:
        tid=str(team["team_id"])
        for p in team.get("lineup") or []:
            pid=str(p["player_id"]); pos=p.get("positions") or []; total=0.0; start=False; role="UNK"
            for z in pos:
                a,b=_mins(z.get("from"),0),_mins(z.get("to"),duration); total+=max(0,min(duration,b)-min(duration,a))
                start |= z.get("start_reason")=="Starting XI" and a<.01
                name=str(z.get("position","")).lower()
                if "goalkeeper" in name: role="GK"
                elif "back" in name or "defensive" in name: role="DEF"
                elif "midfield" in name: role="MID"
                elif "forward" in name or "wing" in name: role="FWD"
            total=min(total,duration)
            usage[tid].append({"player_id":pid,"started":start,"appeared":total>0,"minutes":total,"role":role,"known_at":known_at})
            if start: starters[tid].append(pid)
            if total>0:
                rows.append({"player_id":pid,"team_id":tid,"league_id":"statsbomb:9","role":role,"known_at":known_at,
                             "minutes_exposure":total,"possession_opportunity":1.0,"values":dict(vals[pid])})
    seg={"known_at":known_at,"minutes":duration,"impact":float(xg[home_tid]-xg[away_tid]),
         "home_player_ids":starters[home_tid],"away_player_ids":starters[away_tid]}
    return rows,usage,seg


def _expected(tid: str, usage: dict[str,list[dict[str,Any]]], cutoff: str) -> list[dict[str,Any]]:
    matches=usage.get(tid,[])[-8:]
    if not matches: return []
    q=defaultdict(lambda:{"s":0.0,"a":0.0,"m":0.0,"w":0.0,"n":0,"role":"UNK","known_at":""}); denom=0.0
    for age,rec in enumerate(reversed(matches)):
        w=.82**age; denom+=w
        for p in rec["players"]:
            x=q[p["player_id"]]; x["s"]+=w*p["started"]; x["a"]+=w*p["appeared"]; x["m"]+=w*p["minutes"]; x["w"]+=w; x["n"]+=1
            x["known_at"]=max(x["known_at"],p["known_at"]); x["role"]=p["role"] if p["role"]!="UNK" else x["role"]
    out=[]
    for pid,x in q.items():
        if _dt(x["known_at"],"known_at")>=_dt(cutoff,"cutoff"): raise PITViolation("future expected-lineup row")
        sp=max(0,min(1,x["s"]/denom)); ap=max(0,min(1,x["a"]/denom))
        out.append({"player_id":pid,"starting_probability":sp,"availability_probability":ap,
                    "expected_minutes_distribution":{"mean":x["m"]/max(x["w"],1e-9)},"injury_status":"UNKNOWN",
                    "suspension_status":"UNKNOWN","return_status":"UNKNOWN","rotation_probability":1-sp,
                    "role_distribution":{x["role"]:1.0},"replacement_quality":0.0,
                    "uncertainty":min(1,1/math.sqrt(x["n"])),"known_at":x["known_at"]})
    return sorted(out,key=lambda x:(x["starting_probability"]*x["availability_probability"],x["player_id"]),reverse=True)


def _fit_dev(artifact: Path, repo: Path):
    from match_context import ScheduleTracker, fit_schedule_coefficients
    from test_translator import tune_draw_threshold
    from v2_translator_integration import fit_independent_head
    import importlib
    dev=_read_jsonl(artifact/"dataset/development.jsonl"); lock=json.loads((artifact/"locks/v2_lock.json").read_text())
    p=repo/"football-data/new_engine_v2_joint_score"; sys.path.insert(0,str(p)); eng=importlib.import_module("engine"); sys.path.pop(0)
    state=eng.EngineState(eng.Parameters(**lock["parameters"])); tr=ScheduleTracker(); pending=[]; sf=[]; hf=[]
    for r in sorted(dev,key=lambda x:(x["cutoff"],x["fixture_id"])):
        co=eng._dt(r["cutoff"]); ready=[x for x in pending if eng._dt(x["available_at"])<=co]; pending=[x for x in pending if eng._dt(x["available_at"])>co]
        for x in sorted(ready,key=lambda z:(z["cutoff"],z["fixture_id"])):
            f=eng.Fixture(x["fixture_id"],x["competition_id"],x["season"],eng._dt(x["cutoff"]),x["home_team_id"],x["away_team_id"],x["round_index"])
            state.apply_batch([f],{f.fixture_id:(x["home_goals"],x["away_goals"])})
        f=eng.Fixture(r["fixture_id"],r["competition_id"],r["season"],co,r["home_team_id"],r["away_team_id"],r["round_index"])
        b=state.predict_features(f); z=tr.features(r["home_team_id"],r["away_team_id"],r["cutoff"],r.get("round_index"))
        sf.append({"x":z.vector(),"base_mu_home":b["mu_home"],"base_mu_away":b["mu_away"],"home_goals":r["home_goals"],"away_goals":r["away_goals"]})
        cls=0 if r["home_goals"]>r["away_goals"] else 1 if r["home_goals"]==r["away_goals"] else 2
        hf.append({"mu_home":b["mu_home"],"mu_away":b["mu_away"],"context_delta":0.0,"uncertainty":b["uncertainty"],"target_class":cls})
        tr.observe_fixture(r["home_team_id"],r["away_team_id"],r["cutoff"]); pending.append({**r,"available_at":r["result_available_at"]})
    weights=fit_independent_head(hf)
    return dev,lock,fit_schedule_coefficients(sf),weights,tune_draw_threshold(hf,weights)


def _pack(meta: dict[str,Any], layer: str, integrated: dict[str,Any]) -> dict[str,Any]:
    m=integrated["final_matrix"]; p=integrated["final_1x2"]
    return {**meta,"layer":layer,"matrix":m,"one_x_two":p,"prediction_sha256":canonical_sha({"matrix":m,"one_x_two":p})}


def _clone(x: dict[str,Any], layer: str) -> dict[str,Any]:
    y={**x,"layer":layer}; y["prediction_sha256"]=canonical_sha({"matrix":y["matrix"],"one_x_two":y["one_x_two"]}); return y


def predict(artifact: Path, source: Path, repo: Path, out: Path) -> dict[str,Any]:
    from football_context_translator import LayerAdjustment, build_plan, team_state
    from lineup_scenarios import LineupScenario, build_lineup_scenarios
    from match_context import ScheduleTracker, schedule_adjustment
    from player_strength import estimate_player_vectors
    from test_translator import matrix_mean
    from v2_translator_integration import integrate_plan
    out.mkdir(parents=True,exist_ok=True)
    am=json.loads((artifact/"artifact_manifest.json").read_text())
    if am["run_id"]!=33348991436 or am["prediction_sha256"]!="92dc38866e6e46b167ed6bf0bcfc6f6e0e8b85e57e68cb3a571d3c44fc9461a7": raise PITViolation("sealed V2 identity mismatch")
    sm=json.loads((source/"source_manifest.json").read_text())
    if sm["exact_commit"]!=SB_COMMIT or sm["n"]!=COMMON_N or _sha_file(source/"safe_match_index.json")!=sm["safe_index_sha256"]: raise PITViolation("StatsBomb manifest mismatch")
    feats=_read_jsonl(artifact/"dataset/evaluation_features.jsonl"); pp=_read_jsonl(artifact/"replay/predictions.jsonl"); by={x["fixture_id"]:x for x in pp}
    common=_common(feats,json.loads((source/"safe_match_index.json").read_text()))
    dev,lock,beta,weights,threshold=_fit_dev(artifact,repo)
    tr=ScheduleTracker()
    for r in sorted(dev,key=lambda x:(x["cutoff"],x["fixture_id"])): tr.observe_fixture(r["home_team_id"],r["away_team_id"],r["cutoff"])
    groups=defaultdict(list)
    for pair in common: groups[pair[0]["cutoff"]].append(pair)
    events=[]; segments=[]; usage=defaultdict(list); pending=[]; state=hashlib.sha256(b"football3-pit-v1").hexdigest()
    ledger=[]; preds=[]; cov={"1_player_capability":0,"2_expected_lineup":0}; prov=sm["manifest_sha256"]
    for cutoff in sorted(groups,key=lambda x:_dt(x,"cutoff")):
        co=_dt(cutoff,"cutoff"); ready=[x for x in pending if _dt(x["release_at"],"release_at")<co]; pending=[x for x in pending if _dt(x["release_at"],"release_at")>=co]
        for x in sorted(ready,key=lambda z:(z["release_at"],z["match_id"])):
            er=f"data/events/{x['match_id']}.json"; lr=f"data/lineups/{x['match_id']}.json"
            a,u,s=_history(_verified(source,sm,er),_verified(source,sm,lr),x["release_at"],x["home_tid"],x["away_tid"])
            events+=a; segments.append(s)
            for tid,players in u.items(): usage[tid].append({"players":players,"known_at":x["release_at"],"match_id":x["match_id"]})
            state=hashlib.sha256((state+str(x["match_id"])+sm["files"][er]["sha256"]+sm["files"][lr]["sha256"]).encode()).hexdigest()
            ledger.append({"event":"UPDATE_RELEASE","match_id":x["match_id"],"release_at":x["release_at"],"before_cutoff":cutoff,"state_sha256":state})
        batch=sorted(groups[cutoff],key=lambda x:x[0]["fixture_id"]); rows=[]; batch_ids=[s["match_id"] for _,s in batch]
        for r,sb in batch:
            pr=by[r["fixture_id"]]; raw={"home":pr["v2_joint"]["p_home"],"draw":pr["v2_joint"]["p_draw"],"away":pr["v2_joint"]["p_away"]}
            meta={"fixture_id":r["fixture_id"],"cutoff":r["cutoff"],"competition_id":r["competition_id"],"season":r["season"],
                  "cold_start_bucket":pr["shared_cold_start_bucket"],"coverage_grade":"TEAM_ONLY","weak_side":"home" if raw["home"]<raw["away"] else "away"}
            bh,ba=matrix_mean(pr["v2_joint_off"]["score_matrix"]); blocked=LayerAdjustment("BLOCKED_DATA",0,0,.4,None); neutral=LayerAdjustment("CONTRACT_ONLY",0,0,.15,None)
            hs=team_state(r["home_team_id"],0,0,float(pr.get("shared_home_prior_appearances",0)),.5); ats=team_state(r["away_team_id"],0,0,float(pr.get("shared_away_prior_appearances",0)),.5)
            unknown=build_lineup_scenarios(None,None,cutoff=r["cutoff"])
            def run(sc,vectors,context,grade,status):
                plan=build_plan(match_id=r["fixture_id"],cutoff=r["cutoff"],base_mu_home=bh,base_mu_away=ba,home_team_state=hs,away_team_state=ats,
                    scenarios=sc,player_vectors=vectors,coach_tactical=blocked,match_context=context,process_hazard=blocked,
                    provenance_manifest_sha256=prov,player_status=status,coverage_grade=grade)
                return integrate_plan(plan,lock,repo_root=repo,head_weights=weights)
            rows.append(_pack(meta,LAYERS[0],run(unknown,None,neutral,"TEAM_ONLY","BLOCKED_DATA")))
            ht,at=str(sb["home_team_id"]),str(sb["away_team_id"]); pe=[e for e in events if e["team_id"] in {ht,at}]
            vectors=estimate_player_vectors(pe,segments,as_of=r["cutoff"]) if pe else {}
            he,ae=_expected(ht,usage,r["cutoff"]),_expected(at,usage,r["cutoff"]); hr=[x["player_id"] for x in he[:18]]; ar=[x["player_id"] for x in ae[:18]]
            if vectors and hr and ar:
                known=max([x["known_at"] for x in he[:18]+ae[:18]]); payload={"home":hr,"away":ar,"known_at":known}
                sc1=[LineupScenario("cap_"+canonical_sha(payload)[:16],"EXPECTED_LINEUP",1.0,hr,ar,known,1.0,canonical_sha(payload))]
                cov[LAYERS[1]]+=1; grade,status="FULL_EVENT","IMPLEMENTED"
            else: sc1,grade,status=unknown,"TEAM_ONLY","BLOCKED_DATA"
            rows.append(_pack({**meta,"coverage_grade":grade},LAYERS[1],run(sc1,vectors or None,neutral,grade,status)))
            if vectors and len(he)>=11 and len(ae)>=11:
                sc2=build_lineup_scenarios(he,ae,cutoff=r["cutoff"]); cov[LAYERS[2]]+=1
            else: sc2=sc1
            rows.append(_pack({**meta,"coverage_grade":grade},LAYERS[2],run(sc2,vectors or None,neutral,grade,status)))
            for layer in LAYERS[3:7]: rows.append(_clone(rows[-1],layer))
            sf=tr.features(r["home_team_id"],r["away_team_id"],r["cutoff"],r.get("round_index")); dh,da,sha=schedule_adjustment(sf,beta)
            rows.append(_pack({**meta,"coverage_grade":grade},LAYERS[7],run(sc2,vectors or None,LayerAdjustment("IMPLEMENTED",dh,da,.15,sha),grade,status)))
            rows.append(_clone(rows[-1],LAYERS[8])); rows.append(_clone(rows[-1],LAYERS[9]))
        bsha=canonical_sha([{"fixture_id":x["fixture_id"],"layer":x["layer"],"sha":x["prediction_sha256"]} for x in rows])
        ledger.append({"event":"PREDICT_BATCH_FREEZE","cutoff":cutoff,"match_ids":batch_ids,"state_sha256":state,"prediction_batch_sha256":bsha})
        preds+=rows
        for r,sb in batch:
            release=_iso(_dt(r["cutoff"],"cutoff")+timedelta(hours=RELEASE_HOURS))
            pending.append({"match_id":sb["match_id"],"release_at":release,"home_tid":str(sb["home_team_id"]),"away_tid":str(sb["away_team_id"])})
            ledger.append({"event":"ENQUEUE_AFTER_FREEZE","cutoff":cutoff,"match_id":sb["match_id"],"release_at":release,"prediction_batch_sha256":bsha})
            tr.observe_fixture(r["home_team_id"],r["away_team_id"],r["cutoff"])
    if len(preds)!=COMMON_N*len(LAYERS): raise PITViolation("prediction cardinality mismatch")
    pf=out/"statsbomb_round2_predictions.jsonl"; lf=out/"statsbomb_pit_ledger.jsonl"
    pf.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in preds))
    lf.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n" for x in ledger))
    pm={"schema_version":"football3-translator-round2-predict-v1","research_only":True,"strict_prospective":False,"formal_weight":0,
        "statsbomb_exact_commit":SB_COMMIT,"statsbomb_manifest_sha256":prov,"n":COMMON_N,"common_cohort":"GER1 2023/24",
        "source_v2_run_id":am["run_id"],"source_v2_head":am["head"],"source_prediction_sha256":am["prediction_sha256"],
        "prediction_sha256":_sha_file(pf),"pit_ledger_sha256":_sha_file(lf),"label_access_in_predict_process":"NONE",
        "availability_basis":"SIMULATED_POST_MATCH_RELEASE_PLUS_6H","draw_threshold_dev_only":threshold,"coverage_counts":cov}
    _dump(out/"statsbomb_round2_predictor_manifest.json",pm); _dump(out/"statsbomb_source_manifest.json",sm); return pm


def _ledger_gate(rows: list[dict[str,Any]]) -> dict[str,Any]:
    frozen=set(); enqueued=set(); batches=0
    for r in rows:
        if r["event"]=="PREDICT_BATCH_FREEZE":
            s=set(map(int,r["match_ids"]))
            if s&frozen: raise PITViolation("duplicate freeze")
            frozen|=s; batches+=1
        elif r["event"]=="ENQUEUE_AFTER_FREEZE":
            mid=int(r["match_id"])
            if mid not in frozen or _dt(r["release_at"],"release_at")<=_dt(r["cutoff"],"cutoff"): raise PITViolation("enqueue-before-freeze")
            enqueued.add(mid)
        elif r["event"]=="UPDATE_RELEASE":
            mid=int(r["match_id"])
            if mid not in enqueued or _dt(r["release_at"],"release_at")>=_dt(r["before_cutoff"],"cutoff"): raise PITViolation("release PIT violation")
        else: raise PITViolation("unknown ledger event")
    if len(frozen)!=COMMON_N or len(enqueued)!=COMMON_N: raise PITViolation("ledger cardinality mismatch")
    return {"passed":True,"frozen_matches":len(frozen),"freeze_batches":batches}


def _gate(prev,cand,labels,threshold):
    from test_translator import bootstrap_delta,group_gate,metrics,per_match_ll
    a,b=metrics(prev,labels,threshold),metrics(cand,labels,threshold); boot=bootstrap_delta(per_match_ll(cand,labels),per_match_ll(prev,labels)); gain=a["logloss"]-b["logloss"]; gg=group_gate(prev,cand,labels,gain)
    gates={"logloss_gain_ge_0_001":gain>=.001,"paired_bootstrap_hi_lt_0":boot["hi"]<0,"brier_nonharm":b["brier"]<=a["brier"]+.001,
           "rps_nonharm":b["rps"]<=a["rps"]+.001,"draw_logloss_nonharm":b["draw"]["logloss"]<=a["draw"]["logloss"]+.002,
           "draw_ece_nonharm":b["draw"]["ece"]<=a["draw"]["ece"]+.010,"score_matrix_nonharm":b["exact_score_logloss"]<=a["exact_score_logloss"]+.005,
           "worst_group_nonharm":gg["passed"],"coverage_nonharm":True,
           "uncertainty_nonharm":b["uncertainty_calibration"]["top1_error_ece"]<=a["uncertainty_calibration"]["top1_error_ece"]+.010}
    return {"accepted":all(gates.values()),"gates":gates,"bootstrap":boot,"global_logloss_gain":gain,"previous_metrics":a,"candidate_metrics":b,"group_gate":gg}


def score(artifact: Path, out: Path) -> dict[str,Any]:
    from test_translator import metrics
    pm=json.loads((out/"statsbomb_round2_predictor_manifest.json").read_text()); pf=out/"statsbomb_round2_predictions.jsonl"; lf=out/"statsbomb_pit_ledger.jsonl"
    if _sha_file(pf)!=pm["prediction_sha256"] or _sha_file(lf)!=pm["pit_ledger_sha256"] or pm["label_access_in_predict_process"]!="NONE": raise PITViolation("predictor/scorer separation SHA gate")
    pit=_ledger_gate(_read_jsonl(lf)); all_labels={x["fixture_id"]:(int(x["home_goals"]),int(x["away_goals"])) for x in _read_jsonl(artifact/"dataset/evaluation_label_vault.jsonl")}
    by={k:[] for k in LAYERS}
    for r in _read_jsonl(pf): by[r["layer"]].append(r)
    for k in LAYERS:
        by[k].sort(key=lambda x:(x["cutoff"],x["fixture_id"]))
        if len(by[k])!=COMMON_N: raise PITViolation(f"layer {k} cardinality")
    ids=[x["fixture_id"] for x in by[LAYERS[0]]]
    if any([x["fixture_id"] for x in by[k]]!=ids for k in LAYERS): raise PITViolation("layer pairing")
    labels={x:all_labels[x] for x in ids}; threshold=pm["draw_threshold_dev_only"]
    mets={k:metrics(by[k],labels,threshold) for k in LAYERS}; ab={LAYERS[i]:_gate(by[LAYERS[i-1]],by[LAYERS[i]],labels,threshold) for i in range(1,len(LAYERS))}
    c1,c2=pm["coverage_counts"][LAYERS[1]],pm["coverage_counts"][LAYERS[2]]
    st={LAYERS[0]:"REFERENCE",LAYERS[1]:"BLOCKED_DATA" if not c1 else ("ACCEPTED" if ab[LAYERS[1]]["accepted"] else "REJECTED_ABLATION"),
        LAYERS[2]:"BLOCKED_DATA" if not c2 else ("ACCEPTED" if ab[LAYERS[2]]["accepted"] else "REJECTED_ABLATION"),
        LAYERS[3]:"BLOCKED_DATA",LAYERS[4]:"BLOCKED_DATA",LAYERS[5]:"BLOCKED_DATA",LAYERS[6]:"BLOCKED_DATA",
        LAYERS[7]:"REJECTED_ABLATION",LAYERS[8]:"BLOCKED_DATA",LAYERS[9]:"BLOCKED_DATA"}
    report={"schema_version":"football3-translator-round2-validation-v1","research_only":True,"strict_prospective":False,"formal_promotion_eligible":False,"formal_weight":0,
        "statsbomb_exact_commit":SB_COMMIT,"statsbomb_manifest_sha256":pm["statsbomb_manifest_sha256"],"availability_basis":pm["availability_basis"],
        "n":COMMON_N,"common_cohort":"GER1 2023/24","source_v2_run_id":pm["source_v2_run_id"],"source_v2_head":pm["source_v2_head"],
        "prediction_sha256":pm["prediction_sha256"],"pit_ledger_sha256":pm["pit_ledger_sha256"],"pit_ledger_gate":pit,
        "predictor_scorer_separation":{"passed":True,"predict_process_label_access":"NONE","scorer_after_prediction_sha_freeze":True},
        "layer_status":st,"layer_metrics":mets,"ablation":ab,"coverage_counts":pm["coverage_counts"],
        "layer7_lock":{"status":"REJECTED_ABLATION","formal_revival_allowed":False,"round2_gate_diagnostic_only":ab[LAYERS[7]]},
        "blocked_data_reason":{LAYERS[3]:"no reliable exact pre-match target-XI publication timestamp",LAYERS[4]:"no frozen bench-to-score-matrix effect; do not fit post-view",
            LAYERS[5]:"no approved exact pre-match coach-change timeline",LAYERS[6]:"no frozen pre-evaluation tactical coefficient source",
            LAYERS[8]:"target referee/environment publication timestamps unavailable; target metadata sanitized",
            LAYERS[9]:"process events exist but frozen hazard interface has no validated non-zero score-matrix effect"},
        "protected_v2_core_modified":False,"frozen_contracts_modified":False,"current_modified":False,"main_modified":False,"source_raw_in_artifact":False}
    _dump(out/"statsbomb_round2_validation_report.json",report); return report


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--statsbomb-download"); ap.add_argument("--statsbomb-predict",action="store_true"); ap.add_argument("--statsbomb-score",action="store_true")
    ap.add_argument("--artifact-dir"); ap.add_argument("--source-dir"); ap.add_argument("--repo-root",default="."); ap.add_argument("--out-dir",default="translator_out"); a=ap.parse_args()
    modes=sum(bool(x) for x in (a.statsbomb_download,a.statsbomb_predict,a.statsbomb_score))
    if modes==0: return 0
    if modes!=1: raise SystemExit("choose one mode")
    if a.statsbomb_download:
        m=download_statsbomb(Path(a.statsbomb_download)); print(json.dumps({"commit":m["exact_commit"],"n":m["n"],"manifest":m["manifest_sha256"]})); return 0
    if not a.artifact_dir: raise SystemExit("--artifact-dir required")
    if a.statsbomb_predict:
        if not a.source_dir: raise SystemExit("--source-dir required")
        m=predict(Path(a.artifact_dir),Path(a.source_dir),Path(a.repo_root).resolve(),Path(a.out_dir)); print(json.dumps({"n":m["n"],"prediction_sha256":m["prediction_sha256"],"coverage":m["coverage_counts"]})); return 0
    r=score(Path(a.artifact_dir),Path(a.out_dir)); print(json.dumps({"n":r["n"],"layer_status":r["layer_status"],"pit":r["pit_ledger_gate"]})); return 0


if __name__ == "__main__":
    raise SystemExit(main())
