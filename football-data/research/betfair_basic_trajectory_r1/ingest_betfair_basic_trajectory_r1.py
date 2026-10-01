#!/usr/bin/env python3
"""Fail-closed Betfair BASIC historical MATCH_ODDS trajectory parser.

BASIC supplies timestamped last traded price changes, not an executable back/lay book.
This parser freezes cached H/D/A LTP state at fixed pre-match cutoffs while preserving
the original timestamp of the last LTP observation for each runner.
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

class IngestionError(RuntimeError):
    pass

def load_json(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise IngestionError("config root must be object")
    return value

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):
            h.update(block)
    return h.hexdigest()

def parse_iso(value: str) -> datetime:
    text=str(value).strip().replace("Z","+00:00")
    parsed=datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise IngestionError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)

def epoch_ms(value: Any) -> datetime:
    try:
        number=int(value)
    except (TypeError,ValueError) as exc:
        raise IngestionError(f"invalid publish time: {value!r}") from exc
    return datetime.fromtimestamp(number/1000.0,tz=timezone.utc)

def norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())

def lines(path: Path, accepted: set[str]) -> Iterable[str]:
    suffix=path.suffix.casefold()
    if suffix not in accepted:
        raise IngestionError(f"unsupported extension: {suffix}")
    if suffix==".bz2":
        handle=bz2.open(path,"rt",encoding="utf-8",errors="strict")
    elif suffix==".gz":
        handle=gzip.open(path,"rt",encoding="utf-8",errors="strict")
    else:
        handle=path.open("rt",encoding="utf-8-sig",errors="strict")
    with handle:
        for number,line in enumerate(handle,start=1):
            text=line.strip()
            if text:
                yield text

@dataclass
class MarketState:
    market_id: str
    definition: dict[str, Any] | None = None
    inplay: bool | None = None
    ltp: dict[int,float] = field(default_factory=dict)
    ltp_pt: dict[int,datetime] = field(default_factory=dict)
    candidates: dict[int,dict[str,Any]] = field(default_factory=dict)
    messages: int = 0

def runner_map(definition: dict[str,Any], cfg: dict[str,Any]) -> dict[str,Any]:
    if str(definition.get("eventTypeId")) != str(cfg["market_contract"]["event_type_id"]):
        raise IngestionError("event type is not soccer")
    if str(definition.get("marketType")) != str(cfg["market_contract"]["market_type"]):
        raise IngestionError("market type is not MATCH_ODDS")
    runners=definition.get("runners")
    if not isinstance(runners,list) or len(runners)!=int(cfg["market_contract"]["required_runner_count"]):
        raise IngestionError("runner count is not three")
    event_name=str(definition.get("eventName") or "").strip()
    if " v " not in event_name:
        raise IngestionError("eventName cannot identify home and away")
    home_name,away_name=[part.strip() for part in event_name.split(" v ",1)]
    by_name={norm(row.get("name")):int(row["id"]) for row in runners if isinstance(row,dict) and row.get("id") is not None}
    draw_names={norm(x) for x in cfg["market_contract"]["draw_runner_names"]}
    draw_ids=[rid for name,rid in by_name.items() if name in draw_names]
    if len(draw_ids)!=1:
        raise IngestionError("draw runner not uniquely identified")
    if norm(home_name) not in by_name or norm(away_name) not in by_name:
        raise IngestionError("home/away runners do not match eventName")
    return {
        "home_name":home_name,"away_name":away_name,
        "home_id":by_name[norm(home_name)],"away_id":by_name[norm(away_name)],"draw_id":draw_ids[0],
    }

def maybe_record(state: MarketState, pt: datetime, cfg: dict[str,Any]) -> None:
    definition=state.definition
    if not definition:
        return
    if cfg["market_contract"]["require_inplay_false"] and state.inplay is not False:
        return
    mapping=runner_map(definition,cfg)
    ids=[mapping["home_id"],mapping["draw_id"],mapping["away_id"]]
    if any(rid not in state.ltp or rid not in state.ltp_pt for rid in ids):
        return
    market_time=parse_iso(str(definition["marketTime"]))
    if pt>=market_time:
        return
    for minutes in cfg["market_contract"]["cutoffs_minutes_before_kickoff"]:
        target=market_time-timedelta(minutes=int(minutes))
        if pt<=target:
            state.candidates[int(minutes)]={
                "market_id":state.market_id,
                "event_id":str(definition.get("eventId") or ""),
                "event_name":str(definition.get("eventName") or ""),
                "market_time_utc":market_time.isoformat(),
                "target_cutoff_utc":target.isoformat(),
                "snapshot_publish_time_utc":pt.isoformat(),
                "home_team":mapping["home_name"],
                "away_team":mapping["away_name"],
                "home_ltp":state.ltp[mapping["home_id"]],
                "draw_ltp":state.ltp[mapping["draw_id"]],
                "away_ltp":state.ltp[mapping["away_id"]],
                "home_ltp_observed_at_utc":state.ltp_pt[mapping["home_id"]].isoformat(),
                "draw_ltp_observed_at_utc":state.ltp_pt[mapping["draw_id"]].isoformat(),
                "away_ltp_observed_at_utc":state.ltp_pt[mapping["away_id"]].isoformat(),
                "minutes_before_kickoff":int(minutes),
            }

def parse_file(path: Path, cfg: dict[str,Any]) -> dict[str,Any]:
    accepted={str(x).casefold() for x in cfg["input_contract"]["accepted_extensions"]}
    states: dict[str,MarketState]={}
    messages=0
    parse_errors=[]
    for line_number,text in enumerate(lines(path,accepted),start=1):
        try:
            message=json.loads(text)
            if not isinstance(message,dict):
                raise IngestionError("message root is not object")
            pt=epoch_ms(message.get("pt"))
            changes=message.get("mc")
            if not isinstance(changes,list):
                continue
            messages+=1
            for change in changes:
                if not isinstance(change,dict) or not change.get("id"):
                    continue
                mid=str(change["id"])
                state=states.setdefault(mid,MarketState(mid))
                state.messages+=1
                definition=change.get("marketDefinition")
                if isinstance(definition,dict):
                    state.definition=definition
                    state.inplay=bool(definition.get("inPlay"))
                for row in change.get("rc") or []:
                    if not isinstance(row,dict) or row.get("id") is None or "ltp" not in row:
                        continue
                    value=float(row["ltp"])
                    if not math.isfinite(value) or value<float(cfg["market_contract"]["ltp_minimum"]):
                        continue
                    rid=int(row["id"])
                    state.ltp[rid]=value
                    state.ltp_pt[rid]=pt
                maybe_record(state,pt,cfg)
        except Exception as exc:
            parse_errors.append({"line":line_number,"reason":f"{type(exc).__name__}: {exc}"})
    snapshots=[]
    market_receipts=[]
    for mid,state in sorted(states.items()):
        valid_definition=False
        definition_error=None
        try:
            if state.definition is None:
                raise IngestionError("market definition missing")
            runner_map(state.definition,cfg)
            valid_definition=True
        except Exception as exc:
            definition_error=f"{type(exc).__name__}: {exc}"
        for minutes in cfg["market_contract"]["cutoffs_minutes_before_kickoff"]:
            row=state.candidates.get(int(minutes))
            if row:
                snapshots.append(row)
        market_receipts.append({
            "market_id":mid,
            "messages":state.messages,
            "valid_match_odds_definition":valid_definition,
            "definition_error":definition_error,
            "available_cutoffs":sorted(state.candidates,reverse=True),
            "missing_cutoffs":[int(x) for x in cfg["market_contract"]["cutoffs_minutes_before_kickoff"] if int(x) not in state.candidates],
        })
    valid_markets=sum(row["valid_match_odds_definition"] for row in market_receipts)
    complete_markets=sum(not row["missing_cutoffs"] for row in market_receipts if row["valid_match_odds_definition"])
    status=(
        "PASS_BETFAIR_BASIC_TRAJECTORY_INGESTED"
        if valid_markets and snapshots and not parse_errors
        else "FAIL_BETFAIR_BASIC_TRAJECTORY_INGESTION"
    )
    return {
        "schema_version":"BETFAIR-BASIC-TRAJECTORY-INGESTION-R1-STATUS",
        "status":status,
        "classification":cfg["classification"],
        "input":{"path":str(path),"sha256":sha256_file(path),"messages":messages,"parse_errors":parse_errors},
        "counts":{"markets_seen":len(states),"valid_match_odds_markets":valid_markets,"complete_all_cutoff_markets":complete_markets,"snapshots":len(snapshots)},
        "market_receipts":market_receipts,
        "snapshots":snapshots,
        "ruling":{
            "trajectory_features_allowed":bool(valid_markets and snapshots and not parse_errors),
            "last_traded_price_is_executable_price":False,
            "formal_ev_allowed":False,
            "current_match_use_allowed":False,
            "model_fit_allowed":False,
            "formal_weight":0
        },
        "hard_limits":cfg["hard_limits"]
    }

def self_test(cfg: dict[str,Any]) -> None:
    kickoff=datetime(2026,8,10,18,0,tzinfo=timezone.utc)
    market_definition={
        "eventId":"EV1","eventTypeId":"1","marketType":"MATCH_ODDS",
        "marketTime":kickoff.isoformat().replace("+00:00","Z"),
        "inPlay":False,"numberOfActiveRunners":3,
        "eventName":"Alpha v Beta",
        "runners":[
            {"id":101,"name":"Alpha","sortPriority":1},
            {"id":102,"name":"Beta","sortPriority":2},
            {"id":103,"name":"The Draw","sortPriority":3},
        ],
    }
    updates=[]
    points=[
        (5000,[2.2,3.4,3.6]),(4300,[2.18,3.45,3.65]),(1400,[2.12,3.5,3.8]),
        (350,[2.08,3.55,3.95]),(80,[2.04,3.6,4.1]),(10,[2.0,3.65,4.2]),
    ]
    for i,(minutes,prices) in enumerate(points):
        pt=int((kickoff-timedelta(minutes=minutes)).timestamp()*1000)
        mc={"id":"1.TEST","rc":[{"id":101,"ltp":prices[0]},{"id":103,"ltp":prices[1]},{"id":102,"ltp":prices[2]}]}
        if i==0:
            mc["marketDefinition"]=market_definition
        updates.append(json.dumps({"op":"mcm","pt":pt,"mc":[mc]},separators=(",",":")))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        raw=Path(tmp)/"sample.bz2"
        with bz2.open(raw,"wt",encoding="utf-8") as handle:
            handle.write("\n".join(updates)+"\n")
        result=parse_file(raw,cfg)
        assert result["status"]=="PASS_BETFAIR_BASIC_TRAJECTORY_INGESTED",result
        assert result["counts"]["valid_match_odds_markets"]==1
        assert result["counts"]["snapshots"]==5
        by={row["minutes_before_kickoff"]:row for row in result["snapshots"]}
        assert by[4320]["home_ltp"]==2.2
        assert by[1440]["home_ltp"]==2.18
        assert by[360]["home_ltp"]==2.12
        assert by[90]["home_ltp"]==2.08
        assert by[15]["home_ltp"]==2.04
        assert result["ruling"]["last_traded_price_is_executable_price"] is False
    print(json.dumps({"status":"PASS","self_test":True}))

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--input",type=Path)
    parser.add_argument("--out",type=Path)
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    cfg=load_json(args.config)
    if args.self_test:
        self_test(cfg)
        return
    if args.input is None or args.out is None:
        raise SystemExit("--input and --out are required unless --self-test")
    result=parse_file(args.input,cfg)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"counts":result["counts"]},ensure_ascii=False))
    if not result["status"].startswith("PASS"):
        raise SystemExit(2)

if __name__=="__main__":
    main()
