"""Shared fail-closed primitives for E3g-0D."""
from __future__ import annotations
import hashlib,json,os,urllib.parse
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Mapping

BASE_URL="https://v3.football.api-sports.io"
HOST="v3.football.api-sports.io"
ENDPOINTS={"fixtures","odds","injuries","fixtures/lineups"}
KEY_ENV="API_FOOTBALL_KEY"; ENABLE_ENV="API_FOOTBALL_COLLECTOR_ENABLED"; SCHEDULE_ENV="API_FOOTBALL_SCHEDULE_ENABLED"
STATUS="IMPLEMENTED_NOT_LIVE"; SCHEMA="E3G0D-SNAPSHOT-1.1"; PLAN_SCHEMA="E3G0D-PLAN-1.1"
DAILY_LIMIT=100; DAILY_CAP=90; MAX_RUN=20; MAX_TIMEOUT=30.; MAX_RETRIES=2; MAX_BACKOFF=30.
SAFE_HEADERS={"x-ratelimit-requests-limit","x-ratelimit-requests-remaining","x-ratelimit-limit","x-ratelimit-remaining","retry-after","content-type","date"}
TARGETS={"T-90m":90,"T-45m":45,"T-15m":15}

class E3Error(RuntimeError): pass

def now(): return datetime.now(timezone.utc)
def iso(dt): return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
def parse_utc(value):
    try: dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except ValueError as exc: raise E3Error("invalid UTC timestamp") from exc
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
def packed(obj): return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
def sha(raw): return hashlib.sha256(raw).hexdigest()
def slug(s): return str(s).strip("/").replace("/","__") or "root"
def boolv(value,default=False):
    if value is None:return default
    if isinstance(value,bool):return value
    value=str(value).strip().lower()
    if value in {"1","true","yes","on"}:return True
    if value in {"0","false","no","off",""}:return False
    raise E3Error("invalid boolean value")
def clean_params(params:Mapping[str,Any]):
    blocked={"key","token","secret","authorization","x-apisports-key"}; out={}
    for k,v in params.items():
        if str(k).lower() in blocked: raise E3Error("credential-like request parameter is forbidden")
        out[str(k)]=v
    return dict(sorted(out.items()))
def xwrite(path:Path,raw:bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open("xb") as f:f.write(raw)
    except FileExistsError as exc: raise E3Error(f"append-only collision: {path.as_posix()}") from exc
def raw_write(path:Path,raw:bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open("xb") as f:f.write(raw)
        return True
    except FileExistsError:
        if path.read_bytes()!=raw: raise E3Error("content-addressed raw collision")
        return False
def api_url(endpoint,params):
    endpoint=str(endpoint).strip("/")
    if endpoint not in ENDPOINTS: raise E3Error("endpoint is not allowed")
    q=urllib.parse.urlencode([(k,str(v)) for k,v in clean_params(params).items() if v is not None])
    url=f"{BASE_URL}/{endpoint}"+(f"?{q}" if q else "")
    p=urllib.parse.urlsplit(url)
    if p.scheme!="https" or p.hostname!=HOST or p.path.strip("/") not in ENDPOINTS: raise E3Error("API URL is outside allowlist")
    return url

def expiry(days):
    if not 1<=days<=30: raise E3Error("retention days must be between 1 and 30")
    return iso(now()+timedelta(days=days))

def kickoff_id(fx):
    return sha(packed({k:fx[k] for k in ("fixture_id","scheduled_kickoff_utc","home_team_id","away_team_id")}))

@dataclass
class Budget:
    max_requests:int; used_today:int; attempts:int=0
    def __post_init__(self):
        if not 1<=self.max_requests<=MAX_RUN: raise E3Error("invalid per-run request limit")
        if not 0<=self.used_today<=DAILY_LIMIT: raise E3Error("invalid daily usage")
        if self.used_today+self.max_requests>DAILY_CAP: raise E3Error("daily request reserve would be breached")
    def take(self):
        if self.attempts>=self.max_requests or self.used_today+self.attempts+1>DAILY_CAP: raise E3Error("request budget exhausted")
        self.attempts+=1

def guard(args):
    event=os.getenv("GITHUB_EVENT_NAME","local"); ref=os.getenv("GITHUB_REF","")
    enabled=boolv(os.getenv(ENABLE_ENV),False); scheduled=boolv(os.getenv(SCHEDULE_ENV),False)
    live=not args.no_network and not args.dry_run and args.mode not in {"self-test","preflight"}
    if live:
        if not enabled: raise E3Error(f"{ENABLE_ENV} is not true")
        if event in {"pull_request","pull_request_target"}: raise E3Error("PR trigger may not collect")
        if event in {"workflow_dispatch","schedule"} and ref!="refs/heads/main": raise E3Error("live GitHub collection is main-only")
        if event=="schedule" and (not scheduled or not args.allow_schedule): raise E3Error("schedule is not enabled")
        if event not in {"local","workflow_dispatch","schedule"}: raise E3Error("unsupported live context")
        if not os.getenv(KEY_ENV,"").strip(): raise E3Error(f"{KEY_ENV} is missing")
    return {"deployment_status":STATUS,"collector_enabled":enabled,"schedule_enabled":scheduled,"event_name":event,"github_ref":ref or None,"network_requested":live,"dry_run":args.dry_run,"no_network":args.no_network}
