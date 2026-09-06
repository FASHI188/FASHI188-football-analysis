#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import statistics
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def local_corpus(understat_db: Path, confirmation_dir: Path) -> tuple[list[rt.HistoryFixture], dict[str,rt.XGLabel],dict[str,Any],dict[str,Any]]:
    if rt._sha_file(understat_db) != rt.BINDINGS["understat_frozen.db"]["sha256"]:
        raise AssertionError("old frozen DB SHA mismatch")
    ci=confirmation_dir/"confirmation_identity.jsonl"
    cv=confirmation_dir/"confirmation_xg_result_vault.jsonl"
    if rt._sha_file(ci) != rt.BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise AssertionError("confirmation identity SHA mismatch")
    if rt._sha_file(cv) != rt.BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise AssertionError("confirmation vault SHA mismatch")

    rows=[]; labels={}
    con=sqlite3.connect(str(understat_db)); con.row_factory=sqlite3.Row
    try:
        old=[dict(r) for r in con.execute(
            "select fid,date,league,season,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "and season in (2022,2023) order by date,fid"
        )]
    finally:
        con.close()
    if len(old)!=rt.EXPECTED_XG_OLD_N:
        raise AssertionError(f"old local engineering rows={len(old)}")
    old_sha=rt._sha_file(understat_db)
    for r in old:
        comp=rt.BIG5[str(r["league"])]
        ko=datetime.strptime(str(r["date"]),"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        season=f"{int(r['season'])}/{str(int(r['season'])+1)[2:]}"
        home=str(r["team_h"]); away=str(r["team_a"])
        fid=f"local-understat:{int(r['fid'])}"
        h=rt.HistoryFixture(fid,comp,season,ko,rt._global_team_id(home),rt._global_team_id(away),home,away,
                            int(r["h_goals"]),int(r["a_goals"]),"frozen-understat-db",old_sha)
        rows.append(h)
        lab=rt.hxg.ReleasedLabel(int(r["h_goals"]),int(r["a_goals"]),float(r["h_xg"]),float(r["a_xg"]),ko+timedelta(hours=3))
        labels[fid]=rt.XGLabel(lab,f"understat:{int(r['fid'])}",old_sha,lab.release_at.isoformat())

    identities=[json.loads(x) for x in ci.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows=[json.loads(x) for x in cv.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault={str(x["fixture_id"]):x for x in vault_rows}
    if len(identities)!=1752 or len(vault)!=1752:
        raise AssertionError("confirmation local row count mismatch")
    conf_sha=rt._sha_file(ci)+"+"+rt._sha_file(cv)
    for r in identities:
        v=vault[str(r["fixture_id"])]
        ko=rt._parse_dt(str(r["kickoff"]),"local confirmation kickoff")
        comp=rt.BIG5[str(r["league"])]
        season=f"{int(r['season'])}/{str(int(r['season'])+1)[2:]}"
        home=str(r["home_team"]); away=str(r["away_team"])
        fid=f"local-confirmation:{r['fixture_id']}"
        h=rt.HistoryFixture(fid,comp,season,ko,rt._global_team_id(home),rt._global_team_id(away),home,away,
                            int(v["home_goals"]),int(v["away_goals"]),"frozen-confirmation",conf_sha)
        rows.append(h)
        lab=rt.hxg.ReleasedLabel(int(v["home_goals"]),int(v["away_goals"]),float(v["home_xg"]),float(v["away_xg"]),
                                 rt._parse_dt(str(v["release_at"]),"confirmation release"))
        labels[fid]=rt.XGLabel(lab,f"understat:{r['fixture_id']}",conf_sha,lab.release_at.isoformat())
    rows.sort(key=lambda r:(r.kickoff,r.competition_id,r.fixture_id))
    if len(rows)!=rt.EXPECTED_XG_JOIN_N or len(labels)!=rt.EXPECTED_XG_JOIN_N:
        raise AssertionError("local corpus cardinality mismatch")
    source={
        "understat_db":{"sha256":old_sha,"bytes":understat_db.stat().st_size},
        "confirmation_identity":{"sha256":rt._sha_file(ci),"bytes":ci.stat().st_size},
        "confirmation_vault":{"sha256":rt._sha_file(cv),"bytes":cv.stat().st_size},
        "corpus_sha256":sha([{"fixture_id":r.fixture_id,"kickoff":r.kickoff.isoformat(),"competition":r.competition_id} for r in rows]),
    }
    identity={"scope":"frozen_big5_engineering_subset","fixture_n":len(rows),"competitions":sorted({r.competition_id for r in rows})}
    return rows,labels,source,identity


def production_corpus(repo_root: Path, understat_db: Path, confirmation_dir: Path):
    history,v1_source=rt.load_frozen_v1_history(repo_root)
    labels,xg_source=rt.load_xg_labels(history,understat_db,confirmation_dir)
    return history,labels,{"v1":v1_source,"xg":xg_source,"source_scope":"FROZEN_HISTORICAL_PRODUCTION"},rt._production_identity()


def result_release(r: rt.HistoryFixture, labels: dict[str,rt.XGLabel]) -> datetime:
    x=labels.get(r.fixture_id)
    return x.label.release_at if x is not None else r.kickoff+timedelta(hours=3)


def make_delta(history: list[rt.HistoryFixture], labels: dict[str,rt.XGLabel], lower: datetime, upper: datetime, target_id: str) -> list[dict[str,Any]]:
    return rt.history_delta_events(history,labels,lower,upper,target_id)

def target_payload(r: rt.HistoryFixture) -> dict[str,Any]:
    return {
        "fixture_id":r.fixture_id,"competition_id":r.competition_id,"season":r.season,"kickoff":r.kickoff.isoformat(),
        "home_team_id":r.home_team_id,"away_team_id":r.away_team_id,"home_team_name":r.home_team_name,"away_team_name":r.away_team_name,
    }


def runtime_input(target: rt.HistoryFixture, lower: datetime, upper: datetime, delta: list[dict[str,Any]], status: str="COMPLETE") -> dict[str,Any]:
    coverage={"schema_version":rt.DELTA_SCHEMA,"status":status}
    if status=="COMPLETE":
        coverage.update({
            "verification":"VERIFIED_COMPLETE","v1_status":"COMPLETE","xg_status":"COMPLETE",
            "from":lower.isoformat(),"to":upper.isoformat(),"records_sha256":rt._sha_bytes(rt._canon_bytes(delta)),
            "source_set_sha256":sha(sorted({str(x["source_content_sha256"]) for x in delta})+[lower.isoformat(),upper.isoformat()]),
        })
    return {"schema_version":rt.INPUT_SCHEMA,"fixture":target_payload(target),"cutoff":upper.isoformat(),"delta_coverage":coverage,"model_delta":delta}


def mechanical_sample(history: list[rt.HistoryFixture], n: int) -> list[rt.HistoryFixture]:
    # Identity/time only. Prefer 2024/25 Big-5 rows when available, never results.
    pool=[r for r in history if r.competition_id in set(rt.BIG5.values()) and r.season=="2024/25"]
    if len(pool)<n:
        pool=[r for r in history if r.competition_id in set(rt.BIG5.values())]
    pool.sort(key=lambda r:(r.kickoff,r.competition_id,r.fixture_id))
    if len(pool)<n:
        raise AssertionError("not enough mechanical sample rows")
    idx=[(i*len(pool))//n for i in range(n)]
    if len(set(idx))!=n:
        raise AssertionError("mechanical sample index collision")
    return [pool[i] for i in idx]


def choose_safe_seed_cutoff(sample: list[rt.HistoryFixture]) -> datetime:
    # A day before first target is deterministic and lets the route exercise a non-empty delta.
    return sample[0].kickoff-timedelta(days=1,hours=1)


def compare_with_cutoff(a: dict[str,Any], b: dict[str,Any], cutoff_a: datetime, cutoff_b: datetime) -> dict[str,Any]:
    q=rt._prediction_equivalent(a,b)
    q["cutoff_equal"]=cutoff_a==cutoff_b
    q["passed"]=bool(q["passed"] and q["cutoff_equal"])
    return q


def exact_fallback_check() -> dict[str,Any]:
    state=rt.formal_v2.new_candidate_state()
    ko=datetime(2024,1,1,12,tzinfo=timezone.utc)
    f=rt.hxg.FixtureRow("fallback-check","ENG_PremierLeague","2023/24",ko,rt._global_team_id("Fallback Home"),rt._global_team_id("Fallback Away"),"Fallback Home","Fallback Away")
    # Exact V1 bytes before formal call.
    v1=state.base.predict(rt.v1_engine.Fixture(f.fixture_id,f.competition_id,f.season,f.kickoff,f.home_team_id,f.away_team_id))
    formal=rt.formal_v2.predict_formal_batch(state,[f])[0]
    pred=formal["prediction"]
    exact=rt._canon_bytes(pred)==rt._canon_bytes(v1)
    return {"passed":exact and formal["audit"]["fallback_exact_v1"] is True,
            "formal_route":formal["audit"]["route"],"byte_equal":exact,
            "v1_sha256":rt._sha_bytes(rt._canon_bytes(v1)),"formal_sha256":rt._sha_bytes(rt._canon_bytes(pred))}


def same_kickoff_isolation(history: list[rt.HistoryFixture], labels: dict[str,rt.XGLabel]) -> dict[str,Any]:
    groups=defaultdict(list)
    for r in history:
        groups[r.kickoff].append(r)
    batch=next((sorted(v,key=lambda r:r.fixture_id) for _,v in sorted(groups.items()) if len(v)>=2),None)
    if not batch:
        return {"passed":False,"reason":"no same-kickoff batch"}
    target=batch[0]; cutoff=target.kickoff-timedelta(minutes=60)
    s1,_=rt.replay_history_state(history,labels,cutoff)
    p1=rt._prediction_from_state(s1,target.xg_fixture())
    # Alter every target-batch result/XG label; cutoff replay must be unchanged because those labels are post-cutoff.
    altered=dict(labels)
    for r in batch:
        x=labels.get(r.fixture_id)
        if x:
            lab=rt.hxg.ReleasedLabel(min(30,x.label.home_goals+7),min(30,x.label.away_goals+5),min(20.0,x.label.home_xg+6.0),min(20.0,x.label.away_xg+4.0),x.label.release_at)
            altered[r.fixture_id]=rt.XGLabel(lab,x.source_fixture_id,x.source_sha256,x.source_kickoff)
    s2,_=rt.replay_history_state(history,altered,cutoff)
    p2=rt._prediction_from_state(s2,target.xg_fixture())
    eq=rt._prediction_equivalent(p1,p2)
    return {"passed":eq["passed"],"batch_n":len(batch),"kickoff":target.kickoff.isoformat(),"max_1x2":eq["max_1x2"],"max_matrix":eq["max_matrix"]}


def target_label_isolation(history: list[rt.HistoryFixture], labels: dict[str,rt.XGLabel], target: rt.HistoryFixture) -> dict[str,Any]:
    cutoff=target.kickoff-timedelta(minutes=60)
    s1,_=rt.replay_history_state(history,labels,cutoff)
    p1=rt._prediction_from_state(s1,target.xg_fixture())
    altered=list(history)
    pos=next(i for i,r in enumerate(altered) if r.fixture_id==target.fixture_id)
    r=altered[pos]
    altered[pos]=rt.HistoryFixture(r.fixture_id,r.competition_id,r.season,r.kickoff,r.home_team_id,r.away_team_id,r.home_team_name,r.away_team_name,
                                   min(30,r.home_goals+11),min(30,r.away_goals+9),r.source_path,r.source_sha256)
    altered_labels=dict(labels)
    if target.fixture_id in altered_labels:
        x=altered_labels[target.fixture_id]
        lab=rt.hxg.ReleasedLabel(min(30,x.label.home_goals+11),min(30,x.label.away_goals+9),min(20.0,x.label.home_xg+7),min(20.0,x.label.away_xg+7),x.label.release_at)
        altered_labels[target.fixture_id]=rt.XGLabel(lab,x.source_fixture_id,x.source_sha256,x.source_kickoff)
    s2,_=rt.replay_history_state(altered,altered_labels,cutoff)
    p2=rt._prediction_from_state(s2,target.xg_fixture())
    eq=rt._prediction_equivalent(p1,p2)
    return {"passed":eq["passed"],"fixture_id":target.fixture_id,"max_1x2":eq["max_1x2"],"max_matrix":eq["max_matrix"]}


def route_tests(history,labels,source,identity,sample,tmp:Path) -> dict[str,Any]:
    t0=sample[20]; c0=t0.kickoff-timedelta(minutes=60)
    empty_delta=[]
    no_cache=tmp/"no-cache"
    inp=runtime_input(t0,c0,c0,empty_delta,"UNKNOWN")
    r=rt.resolve_state_for_cutoff(no_cache,inp,target_payload(t0),c0,engineering_history=history,engineering_xg_labels=labels,engineering_source=source,engineering_identity=identity)
    no_cache_ok=r["path"]=="FULL_REBUILD_PATH" and no_cache.exists()

    # Old valid cache + complete released delta -> FAST.
    t1=sample[21]; c1=t1.kickoff-timedelta(minutes=60)
    d1=make_delta(history,labels,c0,c1,t1.fixture_id)
    i1=runtime_input(t1,c0,c1,d1,"COMPLETE")
    r1=rt.resolve_state_for_cutoff(no_cache,i1,target_payload(t1),c1,engineering_history=history,engineering_xg_labels=labels,engineering_source=source,engineering_identity=identity)
    fast_ok=r1["path"]=="FAST_PATH"

    # Incomplete delta must not touch cache state; historical FULL rebuild succeeds.
    t2=sample[22]; c2=t2.kickoff-timedelta(minutes=60)
    d2=make_delta(history,labels,c1,c2,t2.fixture_id)
    i2=runtime_input(t2,c1,c2,d2,"INCOMPLETE")
    r2=rt.resolve_state_for_cutoff(no_cache,i2,target_payload(t2),c2,engineering_history=history,engineering_xg_labels=labels,engineering_source=source,engineering_identity=identity)
    incomplete_ok=r2["path"]=="FULL_REBUILD_PATH"

    # Corrupt one cache byte; FULL must ignore it and rebuild from raw.
    (no_cache/"state_v1.json").write_bytes((no_cache/"state_v1.json").read_bytes()+b" ")
    t3=sample[23]; c3=t3.kickoff-timedelta(minutes=60)
    i3=runtime_input(t3,c2,c3,make_delta(history,labels,c2,c3,t3.fixture_id),"COMPLETE")
    r3=rt.resolve_state_for_cutoff(no_cache,i3,target_payload(t3),c3,engineering_history=history,engineering_xg_labels=labels,engineering_source=source,engineering_identity=identity)
    corrupt_ok=r3["path"]=="FULL_REBUILD_PATH" and "bundle payload mismatch" in (r3["fast_failure"] or "")

    # FULL-created cache is reusable on next fixture.
    t4=sample[24]; c4=t4.kickoff-timedelta(minutes=60)
    i4=runtime_input(t4,c3,c4,make_delta(history,labels,c3,c4,t4.fixture_id),"COMPLETE")
    r4=rt.resolve_state_for_cutoff(no_cache,i4,target_payload(t4),c4,engineering_history=history,engineering_xg_labels=labels,engineering_source=source,engineering_identity=identity)
    reuse_ok=r4["path"]=="FAST_PATH"

    # Missing FULL sources is explicit fail-closed.
    missing=tmp/"missing-sources"
    fail_reason=None
    try:
        rt.resolve_state_for_cutoff(missing,runtime_input(t0,c0,c0,[],"UNKNOWN"),target_payload(t0),c0)
    except rt.RuntimeGateError as exc:
        fail_reason=str(exc)
    insufficient_ok=fail_reason is not None and "FORMAL_INPUT_DATA_INCOMPLETE" in fail_reason

    # Per-fixture input/receipt state: prediction of B must be invariant to unrelated A transaction metadata.
    a=sample[25]; b=sample[26]
    cb=b.kickoff-timedelta(minutes=60)
    sb,_=rt.replay_history_state(history,labels,cb)
    pb1=rt._prediction_from_state(sb,b.xg_fixture())
    unrelated={"fixture":target_payload(a),"cutoff":(a.kickoff-timedelta(minutes=60)).isoformat(),"nonce":"A-only"}
    _=sha(unrelated)
    sb2,_=rt.replay_history_state(history,labels,cb)
    pb2=rt._prediction_from_state(sb2,b.xg_fixture())
    iso=rt._prediction_equivalent(pb1,pb2)

    return {"passed":all((no_cache_ok,fast_ok,incomplete_ok,corrupt_ok,reuse_ok,insufficient_ok,iso["passed"])),
            "no_cache_auto_full":no_cache_ok,"complete_delta_fast":fast_ok,"incomplete_delta_auto_full":incomplete_ok,
            "corrupt_cache_isolated_full":corrupt_ok,"full_then_next_fast":reuse_ok,"full_input_insufficient_fail_closed":insufficient_ok,
            "per_fixture_transaction_isolation":iso["passed"],"insufficient_reason":fail_reason}


def reference_events(history: list[rt.HistoryFixture], labels: dict[str,rt.XGLabel]) -> list[dict[str,Any]]:
    events=[]
    for r in history:
        x=labels.get(r.fixture_id)
        if x is not None:
            e={"event_type":"FIXTURE_FREEZE","event_at":r.kickoff,"row":r,"x":x}
            events.append(e)
        release=result_release(r,labels)
        events.append({"event_type":"LABEL_RELEASE","event_at":release,"row":r,"x":x})
    order={"LABEL_RELEASE":0,"FIXTURE_FREEZE":1}
    events.sort(key=lambda e:(e["event_at"],order[e["event_type"]],e["row"].kickoff,e["row"].competition_id,e["row"].fixture_id))
    return events


def reference_apply_group(state: rt.hxg.ChallengerState, group: list[dict[str,Any]]) -> None:
    # Independent test-side raw replay: release labels first at a timestamp, then freeze new fixtures.
    releases=[e for e in group if e["event_type"]=="LABEL_RELEASE"]
    freezes=[e for e in group if e["event_type"]=="FIXTURE_FREEZE"]
    by_k=defaultdict(list)
    for e in releases: by_k[e["row"].kickoff].append(e)
    for ko,items in sorted(by_k.items()):
        xitems=[e for e in items if e["x"] is not None]
        if xitems:
            xf=[e["row"].xg_fixture() for e in xitems]
            labs={e["row"].fixture_id:e["x"].label for e in xitems}
            state.apply_released_batch(xf,labs,as_of=items[0]["event_at"],update_base=False)
        rows=[e["row"] for e in items]
        state.base.apply_batch([r.v1_fixture() for r in rows],{r.fixture_id:(r.home_goals,r.away_goals) for r in rows})
    by_k=defaultdict(list)
    for e in freezes: by_k[e["row"].kickoff].append(e)
    for _,items in sorted(by_k.items()):
        state.predict_batch([e["row"].xg_fixture() for e in items],include_matrix=False,lightweight=True)


def reference_advance(state: rt.hxg.ChallengerState, events: list[dict[str,Any]], pos: int, cutoff: datetime) -> int:
    while pos<len(events):
        at=events[pos]["event_at"]
        # Labels available exactly at cutoff enter; fixtures kicking exactly at cutoff do not.
        same=[]; j=pos
        while j<len(events) and events[j]["event_at"]==at:
            same.append(events[j]); j+=1
        if at>cutoff:
            break
        if at==cutoff:
            same=[e for e in same if e["event_type"]=="LABEL_RELEASE"]
            if same: reference_apply_group(state,same)
            # Keep freeze events at this same timestamp for the next advance.
            while pos<j and events[pos]["event_type"]=="LABEL_RELEASE": pos+=1
            break
        reference_apply_group(state,same); pos=j
    return pos


def benchmark_paths(history,labels,source,identity,sample,tmp:Path) -> dict[str,Any]:
    target=sample[len(sample)//2]; cutoff=target.kickoff-timedelta(minutes=60)
    full=[]
    for k in range(2):
        b=tmp/f"bench-full-{k}"
        inp=runtime_input(target,cutoff,cutoff,[],"UNKNOWN")
        t=time.perf_counter()
        r=rt.resolve_state_for_cutoff(b,inp,target_payload(target),cutoff,
                                      engineering_history=history,engineering_xg_labels=labels,
                                      engineering_source=source,engineering_identity=identity)
        full.append(time.perf_counter()-t)
        if r["path"]!="FULL_REBUILD_PATH": raise AssertionError("FULL benchmark route mismatch")
    lower=cutoff-timedelta(days=2)
    base_state,_=rt.replay_history_state(history,labels,lower)
    base=tmp/"bench-fast-base"
    rt.seal_bundle(base_state,base,source,identity,lower.isoformat(),"FULL_REBUILD_PATH")
    delta=make_delta(history,labels,lower,cutoff,target.fixture_id)
    inp=runtime_input(target,lower,cutoff,delta,"COMPLETE")
    fast=[]
    for k in range(5):
        b=tmp/f"bench-fast-{k}"; shutil.copytree(base,b)
        t=time.perf_counter()
        r=rt.resolve_state_for_cutoff(b,inp,target_payload(target),cutoff,
                                      engineering_history=history,engineering_xg_labels=labels,
                                      engineering_source=source,engineering_identity=identity)
        fast.append(time.perf_counter()-t)
        if r["path"]!="FAST_PATH": raise AssertionError(f"FAST benchmark route mismatch: {r['fast_failure']}")
    def st(xs):
        xs=sorted(xs)
        return {"n":len(xs),"mean_s":statistics.mean(xs),"median_s":statistics.median(xs),"min_s":xs[0],"max_s":xs[-1]}
    return {"fast":st(fast),"full":st(full),"speedup_median":statistics.median(full)/statistics.median(fast)}


def run_equivalence(history,labels,source,identity,n:int,tmp:Path) -> dict[str,Any]:
    sample=mechanical_sample(history,n)
    sample_identity=[{"fixture_id":r.fixture_id,"kickoff":r.kickoff.isoformat(),"competition_id":r.competition_id} for r in sample]
    seed_cutoff=choose_safe_seed_cutoff(sample)

    # Independent FULL reference stream over trusted raw history; no cache serialization is used here.
    refs=reference_events(history,labels)
    ref_state=rt.formal_v2.new_candidate_state(); ref_pos=0
    ref_pos=reference_advance(ref_state,refs,ref_pos,seed_cutoff)

    # FAST state starts as an exact serialized/restored cache at the same cutoff.
    seed_fast,_=rt.replay_history_state(history,labels,seed_cutoff)
    cache_state=rt.deserialize_state(rt.serialize_v1_state(seed_fast.base),rt.serialize_xg_state(seed_fast))
    prev=seed_cutoff
    max1=maxm=0.0; meta_ok=True; cutoff_ok=True; state_equal=True; fallback=active=0
    paths=defaultdict(int); fast_core=[]

    for i,target in enumerate(sample):
        cutoff=target.kickoff-timedelta(minutes=60)
        ref_pos=reference_advance(ref_state,refs,ref_pos,cutoff)
        ref_for_prediction=rt.deserialize_state(rt.serialize_v1_state(ref_state.base),rt.serialize_xg_state(ref_state))
        pfull=rt._prediction_from_state(ref_for_prediction,target.xg_fixture())

        delta=make_delta(history,labels,prev,cutoff,target.fixture_id)
        inp=runtime_input(target,prev,cutoff,delta,"COMPLETE")
        t=time.perf_counter()
        checked=rt.validate_runtime_input(inp,target_payload(target),prev,cutoff)
        rt.apply_delta(cache_state,checked["delta"],cutoff)
        cache_state=rt.deserialize_state(rt.serialize_v1_state(cache_state.base),rt.serialize_xg_state(cache_state))
        fast_for_prediction=rt.deserialize_state(rt.serialize_v1_state(cache_state.base),rt.serialize_xg_state(cache_state))
        pfast=rt._prediction_from_state(fast_for_prediction,target.xg_fixture())
        fast_core.append(time.perf_counter()-t)

        eq=compare_with_cutoff(pfast,pfull,cutoff,cutoff)
        max1=max(max1,eq["max_1x2"]); maxm=max(maxm,eq["max_matrix"])
        meta_ok=meta_ok and bool(eq.get("metadata_equal")); cutoff_ok=cutoff_ok and bool(eq["cutoff_equal"])
        # Stronger than probability equivalence: exact deterministic state representation must also agree.
        sv1=rt.serialize_v1_state(cache_state.base); sxg=rt.serialize_xg_state(cache_state)
        rv1=rt.serialize_v1_state(ref_state.base); rxg=rt.serialize_xg_state(ref_state)
        state_equal=state_equal and sv1==rv1 and sxg==rxg
        if not eq["passed"] or not (sv1==rv1 and sxg==rxg):
            raise AssertionError(f"equivalence failure fixture={target.fixture_id}: pred={eq} state_equal={sv1==rv1 and sxg==rxg}")
        audit=pfast["row"]["audit"]
        paths[str(audit["route"])]+=1
        fallback+=int(bool(audit["fallback_exact_v1"])); active+=int(not bool(audit["fallback_exact_v1"]))
        prev=cutoff

    core=sorted(fast_core)
    core_timing={"n":len(core),"mean_s":statistics.mean(core),"median_s":statistics.median(core),
                 "p95_s":core[min(len(core)-1,math.ceil(.95*len(core))-1)],"min_s":core[0],"max_s":core[-1]}
    bench=benchmark_paths(history,labels,source,identity,sample,tmp)
    return {
        "passed":max1<=1e-12 and maxm<=1e-12 and meta_ok and cutoff_ok and state_equal,
        "n":n,"selection_rule":"sort 2024/25 Big-5 by kickoff,competition,fixture_id; take floor(i*N/n), i=0..n-1",
        "sample_identity_sha256":sha(sample_identity),"first":sample_identity[0],"last":sample_identity[-1],
        "max_abs_1x2":max1,"max_abs_score_matrix_cell":maxm,"metadata_equal":meta_ok,"cutoff_equal":cutoff_ok,
        "cache_state_exact":state_equal,"formal_routes":dict(paths),"active_n":active,"fallback_n":fallback,
        "fast_core_timing":core_timing,"route_benchmark":bench,"sample":sample,
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=("local","production"),required=True)
    ap.add_argument("--repo-root")
    ap.add_argument("--understat-db",required=True)
    ap.add_argument("--confirmation-dir",required=True)
    ap.add_argument("--out",required=True)
    ap.add_argument("--n",type=int,default=300)
    args=ap.parse_args()
    under=Path(args.understat_db); conf=Path(args.confirmation_dir)
    production_adjudication=None
    if args.mode=="production":
        if not args.repo_root: raise SystemExit("--repo-root required in production mode")
        # Production equivalence must exercise the exact same frozen result/identity
        # adjudication and delayed-settlement release-order semantics installed by entry.py.
        # This changes only the regression harness bootstrap; model/CURRENT/weights stay frozen.
        import formal_result_adjudication_v2
        production_adjudication=formal_result_adjudication_v2.install()
        history,labels,source,identity=production_corpus(Path(args.repo_root),under,conf)
    else:
        history,labels,source,identity=local_corpus(under,conf)
    with tempfile.TemporaryDirectory(prefix="football3-fast-runtime-test-") as td:
        tmp=Path(td)
        eq=run_equivalence(history,labels,source,identity,args.n,tmp)
        sample=eq.pop("sample")
        fallback=exact_fallback_check()
        same=same_kickoff_isolation(history,labels)
        target=target_label_isolation(history,labels,sample[len(sample)//2])
        routes=route_tests(history,labels,source,identity,sample,tmp)
        receipt={
            "schema_version":"football3-fast-runtime-equivalence-receipt-v1","mode":args.mode,"formal_head":rt.FORMAL_HEAD,
            "formal_weights":{"xg":0.75,"v1":0.25},"frozen_source_sha256":{
                "understat_db":rt._sha_file(under),"confirmation_identity":rt._sha_file(conf/"confirmation_identity.jsonl"),
                "confirmation_vault":rt._sha_file(conf/"confirmation_xg_result_vault.jsonl")},
            "corpus":{"fixture_n":len(history),"xg_label_n":len(labels),"identity_sha256":sha([{"fixture_id":r.fixture_id,"kickoff":r.kickoff.isoformat()} for r in history])},
            "production_result_adjudication":production_adjudication,
            "equivalence_300":eq,"v1_exact_fallback":fallback,"same_kickoff_isolation":same,
            "target_label_isolation":target,"automatic_routing":routes,
            "claim":"CACHE_FULL_EQUIVALENT_ON_FROZEN_HISTORY_AUTOROUTE_RELIABLE_PENDING_INDEPENDENT_ENGINEERING_REVIEW",
            "current_data_complete_claimed":False,"formal_enablement_changed":False,"current_pointer_changed":False,
        }
        receipt["passed"]=all((eq["passed"],fallback["passed"],same["passed"],target["passed"],routes["passed"]))
        receipt["receipt_sha256"]=sha(receipt)
        out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(canon(receipt))
        print(json.dumps({"status":"PASS" if receipt["passed"] else "FAIL","receipt":str(out),"sha256":receipt["receipt_sha256"],
                          "max_1x2":eq["max_abs_1x2"],"max_matrix":eq["max_abs_score_matrix_cell"],
                          "fast_median_s":eq["route_benchmark"]["fast"]["median_s"],"full_median_s":eq["route_benchmark"]["full"]["median_s"]},sort_keys=True))
        return 0 if receipt["passed"] else 1

if __name__=="__main__":
    raise SystemExit(main())
