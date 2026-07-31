"""Shared fail-closed primitives for E3g-0D."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

BASE_URL = "https://v3.football.api-sports.io"
HOST = "v3.football.api-sports.io"
ENDPOINTS = {"fixtures", "odds", "injuries", "fixtures/lineups"}
KEY_ENV = "API_FOOTBALL_KEY"
ENABLE_ENV = "API_FOOTBALL_COLLECTOR_ENABLED"
SCHEDULE_ENV = "API_FOOTBALL_SCHEDULE_ENABLED"
STATUS = "IMPLEMENTED_NOT_LIVE"
SCHEMA = "E3G0D-SNAPSHOT-1.2"
PLAN_SCHEMA = "E3G0D-PLAN-1.2"
RECEIPT_SCHEMA = "E3G0D-RUN-RECEIPT-1.2"
DAILY_LIMIT = 100
DAILY_CAP = 90
MAX_RUN = 20
MAX_TIMEOUT = 30.0
MAX_RETRIES = 2
MAX_BACKOFF = 30.0
ARTIFACT_RETENTION_DAYS = 30
MAX_BODY = 10 * 1024 * 1024
SAFE_HEADERS = {
    "x-ratelimit-requests-limit",
    "x-ratelimit-requests-remaining",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "retry-after",
    "content-type",
    "date",
}
TARGETS = {"T-90m": 90, "T-45m": 45, "T-15m": 15}


class E3Error(RuntimeError):
    """Sanitized operational failure with a stable public failure class."""

    def __init__(self, failure_class: str, message: str | None = None):
        self.failure_class = str(failure_class or "VALIDATION_FAILED")
        super().__init__(message or self.failure_class)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise E3Error("VALIDATION_FAILED", "invalid UTC timestamp") from exc
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def request_day_utc(clock: Callable[[], datetime] = utc_now) -> str:
    return clock().astimezone(timezone.utc).date().isoformat()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def slug(value: Any) -> str:
    return str(value).strip("/").replace("/", "__") or "root"


def boolv(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise E3Error("VALIDATION_FAILED", "invalid boolean value")


def clean_params(params: Mapping[str, Any]) -> dict[str, Any]:
    blocked = {"key", "token", "secret", "authorization", "x-apisports-key"}
    output: dict[str, Any] = {}
    for key, value in params.items():
        if str(key).lower() in blocked:
            raise E3Error("VALIDATION_FAILED", "credential-like request parameter is forbidden")
        output[str(key)] = value
    return dict(sorted(output.items()))


def xwrite(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError as exc:
        raise E3Error("APPEND_ONLY_WRITE_FAILED", "append-only collision") from exc
    except OSError as exc:
        raise E3Error("APPEND_ONLY_WRITE_FAILED", "append-only write failed") from exc


def raw_write(path: Path, raw: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
        return True
    except FileExistsError:
        try:
            if path.read_bytes() != raw:
                raise E3Error("APPEND_ONLY_WRITE_FAILED", "content-addressed raw collision")
        except OSError as exc:
            raise E3Error("APPEND_ONLY_WRITE_FAILED", "raw evidence verification failed") from exc
        return False
    except OSError as exc:
        raise E3Error("APPEND_ONLY_WRITE_FAILED", "raw evidence write failed") from exc


def api_url(endpoint: str, params: Mapping[str, Any]) -> str:
    normalized = str(endpoint).strip("/")
    if normalized not in ENDPOINTS:
        raise E3Error("VALIDATION_FAILED", "endpoint is not allowed")
    query = urllib.parse.urlencode(
        [(key, str(value)) for key, value in clean_params(params).items() if value is not None]
    )
    url = f"{BASE_URL}/{normalized}" + (f"?{query}" if query else "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.path.strip("/") not in ENDPOINTS:
        raise E3Error("VALIDATION_FAILED", "API URL is outside allowlist")
    return url


def expiry(days: int, clock: Callable[[], datetime] = utc_now) -> str:
    if days != ARTIFACT_RETENTION_DAYS:
        raise E3Error("VALIDATION_FAILED", "all E3g-0D Artifacts must retain for 30 days")
    return iso(clock() + timedelta(days=days))


def kickoff_id(fixture: Mapping[str, Any]) -> str:
    required = ("fixture_id", "scheduled_kickoff_utc", "home_team_id", "away_team_id")
    try:
        identity = {key: fixture[key] for key in required}
    except KeyError as exc:
        raise E3Error("IDENTITY_MAPPING_FAILED", "kickoff identity is incomplete") from exc
    return sha(packed(identity))


def classify_failure(exc: BaseException) -> str:
    if isinstance(exc, E3Error):
        return exc.failure_class
    return "INTERNAL_FAILURE"


@dataclass
class Budget:
    max_requests: int
    used_today: int
    request_day: str
    clock: Callable[[], datetime] = field(default=utc_now, repr=False)
    attempts: int = 0

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_requests) <= MAX_RUN:
            raise E3Error("VALIDATION_FAILED", "invalid per-run request limit")
        if not 0 <= int(self.used_today) <= DAILY_LIMIT:
            raise E3Error("QUOTA_STATE_UNTRUSTED", "invalid daily usage")
        if self.request_day != request_day_utc(self.clock):
            raise E3Error("QUOTA_STATE_UNTRUSTED", "request-day ledger is not the actual UTC day")
        if self.used_today + self.max_requests > DAILY_CAP:
            raise E3Error("PROVIDER_QUOTA_RESERVE_REACHED", "daily request reserve would be breached")

    def take(self) -> None:
        if request_day_utc(self.clock) != self.request_day:
            raise E3Error("UTC_DAY_ROLLOVER", "UTC request day changed before the next request")
        if self.attempts >= self.max_requests:
            raise E3Error("PROVIDER_QUOTA_RESERVE_REACHED", "request budget exhausted")
        if self.used_today + self.attempts + 1 > DAILY_CAP:
            raise E3Error("PROVIDER_QUOTA_RESERVE_REACHED", "daily safety cap reached")
        self.attempts += 1


def guard(args: Any) -> dict[str, Any]:
    event_name = os.getenv("GITHUB_EVENT_NAME", "local")
    ref = os.getenv("GITHUB_REF", "")
    enabled = boolv(os.getenv(ENABLE_ENV), False)
    schedule_enabled = boolv(os.getenv(SCHEDULE_ENV), False)
    live = not args.no_network and not args.dry_run and args.mode not in {"self-test", "preflight"}
    if live:
        if not enabled:
            raise E3Error("VALIDATION_FAILED", f"{ENABLE_ENV} is not true")
        if event_name in {"pull_request", "pull_request_target"}:
            raise E3Error("VALIDATION_FAILED", "PR trigger may not collect")
        if event_name in {"workflow_dispatch", "schedule"} and ref != "refs/heads/main":
            raise E3Error("VALIDATION_FAILED", "live GitHub collection is main-only")
        if event_name == "schedule" and (not schedule_enabled or not args.allow_schedule):
            raise E3Error("VALIDATION_FAILED", "schedule is not enabled")
        if event_name not in {"local", "workflow_dispatch", "schedule"}:
            raise E3Error("VALIDATION_FAILED", "unsupported live context")
        if not os.getenv(KEY_ENV, "").strip():
            raise E3Error("VALIDATION_FAILED", f"{KEY_ENV} is missing")
    return {
        "deployment_status": STATUS,
        "collector_enabled": enabled,
        "schedule_enabled": schedule_enabled,
        "event_name": event_name,
        "github_ref": ref or None,
        "network_requested": live,
        "dry_run": args.dry_run,
        "no_network": args.no_network,
    }
