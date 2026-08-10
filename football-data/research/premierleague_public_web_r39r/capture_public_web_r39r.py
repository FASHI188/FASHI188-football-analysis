#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "FASHI188-R39R-Public-Web-Forward/1.0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fail(message: str) -> None:
    raise RuntimeError(message)


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != "R39R-PL-PUBLIC-WEB-FORWARD-1.0":
        fail("CONTRACT_SCHEMA_MISMATCH")
    if contract.get("formal_weight") != 0 or contract.get("research_only") is not True:
        fail("RESEARCH_BOUNDARY_INVALID")
    source = contract.get("source") or {}
    if source.get("allowed_host") != "www.premierleague.com":
        fail("HOST_CONTRACT_INVALID")
    rules = contract.get("capture_rules") or {}
    targets = contract.get("targets") or []
    if not isinstance(targets, list) or len(targets) != 4:
        fail("TARGET_COUNT_INVALID")
    if int(rules.get("max_requests", -1)) != 4:
        fail("REQUEST_BUDGET_INVALID")
    boundaries = contract.get("hard_boundaries") or {}
    for key in ("football_api_requests", "api_keys", "target_labels", "model_fits", "candidate_probabilities"):
        if boundaries.get(key) != 0:
            fail("HARD_BOUNDARY_INVALID")
    if any(boundaries.get(key) is not False for key in (
        "ev", "current_match_prediction", "formal_model_mutation", "formal_data_mutation",
        "config_mutation", "current_rule_mutation", "main_mutation",
    )):
        fail("MUTATION_BOUNDARY_INVALID")


def validate_url(url: str, host: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password or parsed.port:
        fail("URL_OUTSIDE_ALLOWLIST")
    if parsed.query or parsed.fragment:
        fail("QUERY_OR_FRAGMENT_FORBIDDEN")
    return parsed


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str) -> None:
        super().__init__()
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        validate_url(newurl, self.host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_exact(url: str, host: str, timeout: int, max_body: int) -> tuple[bytes, dict[str, Any]]:
    validate_url(url, host)
    requested = utc_now()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        SameHostRedirect(host),
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            final_url = response.geturl()
            validate_url(final_url, host)
            raw = response.read(max_body + 1)
            content_type = str(response.headers.get("Content-Type") or "")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP_{exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError("NETWORK_READ_FAILED") from exc
    observed = utc_now()
    if status != 200:
        fail(f"HTTP_STATUS_{status}")
    if not raw or len(raw) > max_body:
        fail("BODY_SIZE_INVALID")
    digest = sha256(raw)
    if not HEX64.fullmatch(digest):
        fail("SHA256_INVALID")
    return raw, {
        "requested_at_utc": iso(requested),
        "observed_at_utc": iso(observed),
        "http_status": status,
        "final_url": final_url,
        "content_type": content_type,
        "content_length_bytes": len(raw),
        "sha256": digest,
    }


def detect_markers(name: str, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    lowered = text.lower()
    return {
        "contains_premier_league": "premier league" in lowered,
        "contains_arsenal": "arsenal" in lowered,
        "contains_coventry": "coventry" in lowered,
        "contains_lineup_token": any(token in lowered for token in ("lineup", "line-up", "starting xi", "starting 11")),
        "contains_fixture_release_token": "380" in lowered and "2026/27" in text,
        "target_name": name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    contract_path = Path(args.contract)
    output = Path(args.output)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    validate_contract(contract)
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"
    raw_dir.mkdir()

    host = contract["source"]["allowed_host"]
    rules = contract["capture_rules"]
    timeout = int(rules["timeout_seconds"])
    max_body = int(rules["max_body_bytes"])
    kickoff = parse_utc(contract["fixture"]["scheduled_kickoff_utc"])
    run_started = utc_now()
    if run_started >= kickoff:
        fail("TARGET_MATCH_ALREADY_KICKED_OFF")

    rows: list[dict[str, Any]] = []
    for target in contract["targets"]:
        name = str(target["name"])
        url = str(target["url"])
        raw, meta = fetch_exact(url, host, timeout, max_body)
        observed = parse_utc(meta["observed_at_utc"])
        if target.get("pre_kickoff_required") is True and observed >= kickoff:
            fail("PRE_KICKOFF_GATE_FAILED")
        suffix = ".txt" if target.get("content_kind") == "text" else ".html"
        raw_name = f"{name}__sha256_{meta['sha256']}{suffix}"
        raw_path = raw_dir / raw_name
        raw_path.write_bytes(raw)
        markers = detect_markers(name, raw)
        rows.append({
            "name": name,
            "requested_url": url,
            "raw_path": raw_path.relative_to(output).as_posix(),
            "pre_kickoff_required": bool(target.get("pre_kickoff_required")),
            **meta,
            "markers": markers,
        })

    if len(rows) != int(rules["max_requests"]):
        fail("REQUEST_COUNT_MISMATCH")
    if any(row["http_status"] != 200 or not HEX64.fullmatch(row["sha256"]) for row in rows):
        fail("CAPTURE_AUDIT_FAILED")
    if not rows[0]["markers"]["contains_premier_league"]:
        fail("ROBOTS_CONTENT_IDENTITY_FAILED")
    if not rows[1]["markers"]["contains_fixture_release_token"]:
        fail("FIXTURE_LIST_IDENTITY_FAILED")
    for row in rows[2:]:
        if not row["markers"]["contains_arsenal"] or not row["markers"]["contains_coventry"]:
            fail("MATCH_PAGE_IDENTITY_FAILED")

    receipt = {
        "schema_version": "R39R-CAPTURE-RECEIPT-1.0",
        "contract_sha256": sha256(contract_path.read_bytes()),
        "run_started_at_utc": iso(run_started),
        "run_finished_at_utc": iso(utc_now()),
        "provider": "PremierLeague.com official public website",
        "network_class": "PUBLIC_WEB_NO_API_NO_CREDENTIAL",
        "fixture": contract["fixture"],
        "request_count": len(rows),
        "captures": rows,
        "target_labels_accessed": 0,
        "model_fits": 0,
        "candidate_probabilities": 0,
        "football_api_requests": 0,
        "api_keys_used": 0,
        "append_only_evidence": True,
        "formal_weight": 0,
        "terminal": "PASS_R39R_FIRST_PUBLIC_WEB_FORWARD_CAPTURE" 
    }
    receipt_bytes = packed(receipt) + b"\n"
    (output / "capture_receipt_r39r.json").write_bytes(receipt_bytes)
    (output / "capture_receipt_r39r.sha256").write_text(sha256(receipt_bytes) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": receipt["terminal"],
        "request_count": receipt["request_count"],
        "receipt_sha256": sha256(receipt_bytes),
        "observed_at": [row["observed_at_utc"] for row in rows],
        "raw_sha256": [row["sha256"] for row in rows],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R39R_CAPTURE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
