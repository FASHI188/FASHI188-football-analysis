#!/usr/bin/env python3
"""E3g-0D API-Football forward PIT collector (research-only)."""
from __future__ import annotations

import argparse, hashlib, json, os, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

BASE = "https://v3.football.api-sports.io"
SCHEMA = "E3G0D-SNAPSHOT-1.0"
PLAN_SCHEMA = "E3G0D-PLAN-1.0"
TARGETS = {"T-90m": 90, "T-45m": 45, "T-15m": 15}
SAFE_HEADERS = {"x-ratelimit-requests-limit", "x-ratelimit-requests-remaining", "x-ratelimit-limit", "x-ratelimit-remaining", "retry-after", "content-type", "date"}

class CollectorError(RuntimeError): pass

def now() -> datetime: return datetime.now(timezone.utc)
def iso(dt: datetime) -> str: return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
def packed(obj: Any) -> bytes: return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
def digest(raw: bytes) -> str: return hashlib.sha256(raw).hexdigest()
def slug(endpoint: str) -> str: return endpoint.strip("/").replace("/", "__") or "root"

def xwrite(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as f: f.write(raw)
    except FileExistsError as exc: raise CollectorError(f"append-only collision: {path}") from exc

class Store:
    def __init__(self, root: Path): self.root = root
    def save(self, endpoint: str, params: Mapping[str, Any], raw: bytes, requested: datetime, observed: datetime,
             status: int, headers: Mapping[str, str], role: str, fixtures: Sequence[Mapping[str, Any]] = (),
             labels: Sequence[str] = (), final: bool = False) -> dict[str, Any]:
        try: payload = json.loads(raw)
        except json.JSONDecodeError as exc: raise CollectorError(f"non-JSON response for {endpoint}") from exc
        h, stamp, day, ep = digest(raw), observed.strftime("%Y%m%dT%H%M%SZ"), observed.strftime("%Y/%m/%d"), slug(endpoint)
        raw_rel = Path("raw")/day/f"{stamp}__{ep}__{h[:16]}.json"
        man_rel = Path("manifests")/day/f"{stamp}__{ep}__{h[:16]}.manifest.json"
        manifest = {
            "schema_version": SCHEMA, "provider": "API-Football", "endpoint": endpoint, "role": role,
            "request": {"params": dict(sorted((str(k), v) for k, v in params.items()))},
            "requested_at_utc": iso(requested), "observed_at_utc": iso(observed), "http_status": status,
            "safe_response_headers": {k.lower(): v for k, v in headers.items() if k.lower() in SAFE_HEADERS},
            "raw_payload_sha256": h, "raw_payload_path": raw_rel.as_posix(),
            "provider_results": payload.get("results") if isinstance(payload, dict) else None,
            "target_labels": sorted(set(labels)), "final_pre_kickoff": final,
            "related_fixtures": list(fixtures), "append_only": True, "formal_weight": 0,
        }
        xwrite(self.root/raw_rel, raw); xwrite(self.root/man_rel, packed(manifest)+b"\n")
        for fx in fixtures:
            kickoff = parse(str(fx["kickoff_utc"]))
            if observed >= kickoff: continue
            kind = "final" if final else "candidate"
            rel = Path("freeze_candidates")/str(fx["fixture_id"])/ep/f"{stamp}__{kind}__{h[:16]}.json"
            freeze = {"schema_version": SCHEMA, "provider": "API-Football", "fixture_id": int(fx["fixture_id"]),
                      "kickoff_utc": iso(kickoff), "observed_at_utc": iso(observed), "endpoint": endpoint,
                      "role": role, "target_labels": sorted(set(labels)), "freeze_kind": kind,
                      "raw_payload_sha256": h, "raw_payload_path": raw_rel.as_posix(),
                      "manifest_path": man_rel.as_posix(), "selection_rule": "latest observed_at_utc strictly before kickoff",
                      "append_only": True, "formal_weight": 0}
            xwrite(self.root/rel, packed(freeze)+b"\n")
        return {"endpoint": endpoint, "observed_at_utc": iso(observed), "sha256": h,
                "raw_path": raw_rel.as_posix(), "manifest_path": man_rel.as_posix(),
                "results": manifest["provider_results"]}

class Client:
    def __init__(self, key: str, timeout: float, retries: int):
        if not key.strip(): raise CollectorError("API_FOOTBALL_KEY is required for live collection")
        self.key, self.timeout, self.retries = key.strip(), timeout, retries
    def get(self, endpoint: str, params: Mapping[str, Any]) -> tuple[bytes, datetime, datetime, int, dict[str, str]]:
        q = urllib.parse.urlencode([(str(k), str(v)) for k,v in params.items() if v is not None])
        url = f"{BASE}/{endpoint.lstrip('/')}" + (f"?{q}" if q else "")
        for attempt in range(self.retries+1):
            requested = now()
            req = urllib.request.Request(url, headers={"x-apisports-key": self.key, "Accept":"application/json", "User-Agent":"FASHI188-e3g0d/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw, observed, headers = resp.read(), now(), dict(resp.headers.items())
                    obj = json.loads(raw)
                    if isinstance(obj, dict) and obj.get("errors"): raise CollectorError(f"provider errors for {endpoint}: {obj['errors']}")
                    return raw, requested, observed, int(resp.status), headers
            except urllib.error.HTTPError as exc:
                if attempt >= self.retries or exc.code not in {429,499,500,502,503,504}: raise CollectorError(f"HTTP {exc.code} for {endpoint}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt >= self.retries: raise CollectorError(f"network failure for {endpoint}") from exc
            time.sleep(min(2**attempt, 30))
        raise CollectorError(f"request failed for {endpoint}")

def fixtures_from(obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    out=[]
    for row in obj.get("response", []) if isinstance(obj.get("response", []), list) else []:
        fx, lg, teams = row.get("fixture",{}), row.get("league",{}), row.get("teams",{})
        if fx.get("id") is None or not fx.get("date"): continue
        home, away = teams.get("home",{}), teams.get("away",{})
        out.append({"fixture_id": int(fx["id"]), "kickoff_utc": iso(parse(str(fx["date"]))),
                    "status_short": (fx.get("status") or {}).get("short"), "league_id": lg.get("id"), "season": lg.get("season"),
                    "home_team_id": home.get("id"), "home_team_name": home.get("name"),
                    "away_team_id": away.get("id"), "away_team_name": away.get("name")})
    return sorted(out, key=lambda x:(x["kickoff_utc"],x["fixture_id"]))

def api_save(client: Client, store: Store, endpoint: str, params: Mapping[str,Any], role: str,
             fixtures: Sequence[Mapping[str,Any]]=(), labels: Sequence[str]=(), final: bool=False) -> tuple[dict[str,Any],dict[str,Any]]:
    raw, req, obs, status, headers = client.get(endpoint, params)
    return store.save(endpoint, params, raw, req, obs, status, headers, role, fixtures, labels, final), json.loads(raw)

def build_plan(client: Client, store: Store, league: int, season: int, day: str, tz: str) -> tuple[dict[str,Any],dict[str,Any]]:
    params={"league":league,"season":season,"date":day,"timezone":tz}
    snap,obj=api_save(client,store,"fixtures",params,"daily_fixture_plan_source")
    fixtures=fixtures_from(obj)
    plan={"schema_version":PLAN_SCHEMA,"provider":"API-Football","league_id":league,"season":season,"date_utc":day,"timezone":tz,
          "observed_at_utc":snap["observed_at_utc"],"fixtures":fixtures,"fixture_count":len(fixtures),
          "source_raw_payload_sha256":snap["sha256"],"source_manifest_path":snap["manifest_path"],"append_only":True,"formal_weight":0}
    xwrite(store.root/"plans"/f"{day}__league_{league}__season_{season}.json",packed(plan)+b"\n")
    return plan,snap

def load_plan(path: Path) -> dict[str,Any]:
    obj=json.loads(path.read_text())
    if obj.get("schema_version")!=PLAN_SCHEMA or not isinstance(obj.get("fixtures"),list): raise CollectorError("invalid plan")
    return obj

def due(fixtures: Sequence[Mapping[str,Any]], observed: datetime, tolerance: int) -> dict[str,list[dict[str,Any]]]:
    out={k:[] for k in TARGETS}
    for fx in fixtures:
        mins=(parse(str(fx["kickoff_utc"]))-observed).total_seconds()/60
        for label,target in TARGETS.items():
            if target-tolerance <= mins <= target+tolerance: out[label].append(dict(fx))
    return out

def collect_odds(client:Client,store:Store,league:int,season:int,day:str,tz:str,role:str,fixtures=(),labels=(),final=False,max_pages=2):
    out=[]
    for page in range(1,max_pages+1):
        snap,obj=api_save(client,store,"odds",{"league":league,"season":season,"date":day,"timezone":tz,"page":page},role,fixtures,labels,final)
        out.append(snap)
        total=int((obj.get("paging") or {}).get("total",1))
        if page>=total: break
    return out

def collect_injuries(client:Client,store:Store,fixtures:Sequence[Mapping[str,Any]],role:str,labels=(),final=False):
    ids=sorted({int(x["fixture_id"]) for x in fixtures}); out=[]
    for i in range(0,len(ids),20):
        chunk=ids[i:i+20]; chunk_set=set(chunk); fx=[x for x in fixtures if int(x["fixture_id"]) in chunk_set]
        snap,_=api_save(client,store,"injuries",{"ids":"-".join(map(str,chunk)),"timezone":"UTC"},role,fx,labels,final); out.append(snap)
    return out

def collect_lineups(client:Client,store:Store,fixtures:Sequence[Mapping[str,Any]],label:str):
    out=[]
    for fx in fixtures:
        snap,_=api_save(client,store,"fixtures/lineups",{"fixture":int(fx["fixture_id"])} ,"pre_kickoff_lineup_poll",[fx],[label],label=="T-15m"); out.append(snap)
    return out

def run_live(args: argparse.Namespace) -> dict[str,Any]:
    client=Client(os.getenv("API_FOOTBALL_KEY",""),args.timeout,args.retries); store=Store(Path(args.output_dir)); observed=now(); day=args.date or observed.date().isoformat(); snaps=[]; plan=None
    if args.mode=="build-plan": plan,s=build_plan(client,store,args.league,args.season,day,args.timezone); snaps.append(s)
    elif args.mode=="odds": snaps+=collect_odds(client,store,args.league,args.season,day,args.timezone,"scheduled_three_hour_odds")
    else:
        if not args.plan: raise CollectorError(f"--plan required for {args.mode}")
        plan=load_plan(Path(args.plan)); fixtures=plan["fixtures"]
        if args.mode=="injuries": snaps+=collect_injuries(client,store,fixtures,"scheduled_four_hour_injuries")
        elif args.mode=="lineup-window":
            for label,fxs in due(fixtures,observed,args.tolerance).items():
                if not fxs: continue
                snaps+=collect_lineups(client,store,fxs,label)
                if label in {"T-90m","T-15m"}: snaps+=collect_odds(client,store,args.league,args.season,day,args.timezone,"exact_pre_kickoff_odds",fxs,[label],label=="T-15m")
                if label=="T-15m": snaps+=collect_injuries(client,store,fxs,"final_pre_kickoff_injuries",[label],True)
        else: raise CollectorError(f"unsupported mode {args.mode}")
    receipt={"schema_version":SCHEMA,"provider":"API-Football","mode":args.mode,"observed_at_utc":iso(observed),"target_date_utc":day,
             "league_id":args.league,"season":args.season,"snapshot_count":len(snaps),"snapshots":snaps,
             "plan_fixture_count":plan.get("fixture_count") if plan else None,"candidate_probabilities":0,"model_fits":0,"formal_weight":0,"append_only":True}
    xwrite(store.root/"run_receipts"/f"{observed.strftime('%Y%m%dT%H%M%SZ')}__{args.mode}.json",packed(receipt)+b"\n"); return receipt

def self_test(root:Path) -> dict[str,Any]:
    store=Store(root); sentinel="SECRET_SHOULD_NEVER_APPEAR"; obs=datetime(2026,8,15,14,30,tzinfo=timezone.utc)
    payload={"get":"fixtures","errors":[],"results":2,"response":[
        {"fixture":{"id":1001,"date":"2026-08-15T16:00:00+00:00","status":{"short":"NS"}},"league":{"id":39,"season":2026},"teams":{"home":{"id":1,"name":"Alpha"},"away":{"id":2,"name":"Beta"}}},
        {"fixture":{"id":1002,"date":"2026-08-15T18:00:00+00:00","status":{"short":"NS"}},"league":{"id":39,"season":2026},"teams":{"home":{"id":3,"name":"Gamma"},"away":{"id":4,"name":"Delta"}}}]}
    raw=packed(payload); fxs=fixtures_from(payload)
    s1=store.save("fixtures",{"league":39,"season":2026,"date":"2026-08-15"},raw,obs,obs,200,{"x-ratelimit-requests-remaining":"99","authorization":sentinel},"self_test",fxs)
    windows=due(fxs,obs,7); assert [x["fixture_id"] for x in windows["T-90m"]]==[1001]
    store.save("fixtures/lineups",{"fixture":1001},packed({"errors":[],"results":0,"response":[]}),datetime(2026,8,15,15,45,tzinfo=timezone.utc),datetime(2026,8,15,15,45,tzinfo=timezone.utc),200,{},"self_test",[fxs[0]],["T-15m"],True)
    all_bytes=b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file()); assert sentinel.encode() not in all_bytes; assert s1["sha256"]==digest(raw); assert list((root/"freeze_candidates").rglob("*final*.json"))
    result={"self_test":"PASS","fixture_count":2,"due_T90":[1001],"secret_redaction":"PASS","freeze_candidate":"PASS","candidate_probabilities":0,"model_fits":0,"formal_weight":0}
    xwrite(root/"self_test_result.json",packed(result)+b"\n"); return result

def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(); p.add_argument("--mode",required=True,choices=["self-test","build-plan","odds","injuries","lineup-window"]); p.add_argument("--output-dir",required=True); p.add_argument("--plan"); p.add_argument("--date"); p.add_argument("--league",type=int,default=39); p.add_argument("--season",type=int,default=2026); p.add_argument("--timezone",default="UTC"); p.add_argument("--timeout",type=float,default=30); p.add_argument("--retries",type=int,default=2); p.add_argument("--tolerance",type=int,default=7); p.add_argument("--print-summary",action="store_true"); return p

def main() -> int:
    args=parser().parse_args(); root=Path(args.output_dir); root.mkdir(parents=True,exist_ok=True)
    try: result=self_test(root) if args.mode=="self-test" else run_live(args)
    except CollectorError as exc: print(f"E3g-0D collector error: {exc}",file=sys.stderr); return 2
    if args.print_summary: print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
    return 0
if __name__=="__main__": raise SystemExit(main())
