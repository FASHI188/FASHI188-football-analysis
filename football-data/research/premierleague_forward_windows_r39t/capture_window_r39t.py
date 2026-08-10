#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def fail(msg: str) -> None:
    raise RuntimeError(msg)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(v: dt.datetime) -> str:
    return v.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def find_browser() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    fail("HEADLESS_BROWSER_NOT_FOUND")
    raise AssertionError


def git_blob(path: str) -> str:
    p = subprocess.run(["git", "rev-parse", f"HEAD:{path}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if p.returncode != 0:
        fail("GIT_BLOB_LOOKUP_FAILED:" + path)
    return p.stdout.strip()


def load_detector(implementation_path: str):
    spec = importlib.util.spec_from_file_location("r39s_detector", implementation_path)
    if spec is None or spec.loader is None:
        fail("DETECTOR_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_one(browser: str, url: str, budget_ms: int, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    requested = utc_now()
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--window-size=1440,1200",
        f"--virtual-time-budget={budget_ms}",
        "--dump-dom",
        url,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=70, check=False)
    observed = utc_now()
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-1600:]
        fail("BROWSER_RENDER_FAILED:" + tail.replace("\n", " "))
    raw = proc.stdout
    if not raw or len(raw) > max_bytes:
        fail("RENDERED_DOM_SIZE_INVALID")
    digest = sha256(raw)
    if not HEX64.fullmatch(digest):
        fail("RENDERED_DOM_SHA_INVALID")
    return raw, {
        "requested_at_utc": iso(requested),
        "observed_at_utc": iso(observed),
        "dom_bytes": len(raw),
        "dom_sha256": digest,
        "browser_stderr_tail": proc.stderr.decode("utf-8", errors="replace")[-800:],
    }


def validate_contract(c: dict[str, Any]) -> None:
    if c.get("schema_version") != "R39T-PL-FORWARD-WINDOWS-1.0":
        fail("CONTRACT_SCHEMA_MISMATCH")
    if c.get("research_only") is not True or c.get("formal_weight") != 0:
        fail("RESEARCH_BOUNDARY_INVALID")
    if (c.get("source") or {}).get("allowed_host") != "www.premierleague.com":
        fail("SOURCE_HOST_INVALID")
    b = c.get("hard_boundaries") or {}
    for k in ("blind100_labels_accessed", "research_target_labels_used", "model_fits", "candidate_probabilities", "football_api_requests", "api_keys_used"):
        if b.get(k) != 0:
            fail("HARD_BOUNDARY_INVALID")
    if any(b.get(k) is not False for k in ("ev", "current_match_prediction", "formal_model_mutation", "formal_data_mutation", "config_mutation", "current_rule_mutation", "main_mutation")):
        fail("MUTATION_BOUNDARY_INVALID")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--trigger", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    contract_path = Path(args.contract)
    trigger_path = Path(args.trigger)
    out = Path(args.output)
    c = json.loads(contract_path.read_text(encoding="utf-8"))
    trigger = json.loads(trigger_path.read_text(encoding="utf-8"))
    validate_contract(c)

    mode = str(trigger.get("mode", ""))
    if mode not in c["trigger_contract"]["allowed_modes"]:
        fail("TRIGGER_MODE_INVALID")

    frozen = c["frozen_detector"]
    if git_blob(frozen["contract_path"]) != frozen["contract_git_blob_sha"]:
        fail("R39S_DETECTOR_CONTRACT_BLOB_DRIFT")
    if git_blob(frozen["implementation_path"]) != frozen["implementation_git_blob_sha"]:
        fail("R39S_DETECTOR_IMPLEMENTATION_BLOB_DRIFT")

    out.mkdir(parents=True, exist_ok=False)
    now = utc_now()
    kickoff = parse_utc(c["fixture"]["scheduled_kickoff_utc"])
    if now >= kickoff:
        fail("TARGET_MATCH_ALREADY_KICKED_OFF")

    if mode == "PRECHECK":
        first = min(parse_utc(v["earliest_start_utc"]) for v in c["windows"].values())
        receipt = {
            "schema_version": "R39T-PRECHECK-RECEIPT-1.0",
            "terminal": "PASS_R39T_ZERO_NETWORK_PRECHECK",
            "checked_at_utc": iso(now),
            "first_capture_earliest_start_utc": iso(first),
            "fixture": c["fixture"],
            "frozen_detector": frozen,
            "network_requests": 0,
            "football_api_requests": 0,
            "api_keys_used": 0,
            "blind100_labels_accessed": 0,
            "research_target_labels_used": 0,
            "model_fits": 0,
            "candidate_probabilities": 0,
            "formal_weight": 0,
        }
        raw = packed(receipt) + b"\n"
        (out / "precheck_receipt_r39t.json").write_bytes(raw)
        (out / "precheck_receipt_r39t.sha256").write_text(sha256(raw) + "\n", encoding="ascii")
        print(json.dumps({"terminal": receipt["terminal"], "checked_at_utc": receipt["checked_at_utc"], "network_requests": 0}, sort_keys=True))
        return 0

    window = c["windows"][mode]
    earliest = parse_utc(window["earliest_start_utc"])
    latest = parse_utc(window["latest_observed_utc"])
    started = utc_now()
    if started < earliest:
        fail(f"WINDOW_TOO_EARLY:{mode}:{iso(started)}:{window['earliest_start_utc']}")
    if started > latest:
        fail(f"WINDOW_TOO_LATE_BEFORE_NETWORK:{mode}:{iso(started)}:{window['latest_observed_utc']}")

    browser = find_browser()
    browser_version = subprocess.run([browser, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10, check=False).stdout.strip()
    rules = c["render_rules"]
    dom_raw, meta = render_one(browser, c["fixture"]["lineups_url"], int(rules["virtual_time_budget_ms"]), int(rules["max_dom_bytes"]))
    observed = parse_utc(meta["observed_at_utc"])
    if observed > latest:
        fail(f"WINDOW_OBSERVED_TOO_LATE:{mode}:{meta['observed_at_utc']}:{window['latest_observed_utc']}")
    if observed >= kickoff:
        fail("OBSERVED_NOT_PRE_KICKOFF")

    dom_path = out / f"{mode.lower()}_rendered_dom__sha256_{meta['dom_sha256']}.html"
    dom_path.write_bytes(dom_raw)

    detector_contract = json.loads(Path(frozen["contract_path"]).read_text(encoding="utf-8"))
    detector = load_detector(frozen["implementation_path"])
    result = detector.detect(dom_raw, detector_contract)
    state = result["state"]
    usable = state == frozen["usable_state"]

    receipt = {
        "schema_version": "R39T-WINDOW-CAPTURE-RECEIPT-1.0",
        "terminal": "PASS_R39T_WINDOW_CAPTURE",
        "window": mode,
        "nominal_utc": window["nominal_utc"],
        "earliest_start_utc": window["earliest_start_utc"],
        "latest_observed_utc": window["latest_observed_utc"],
        "fixture": c["fixture"],
        "browser_version": browser_version,
        "requested_at_utc": meta["requested_at_utc"],
        "observed_at_utc": meta["observed_at_utc"],
        "lateness_seconds_from_nominal": (observed - parse_utc(window["nominal_utc"])).total_seconds(),
        "rendered_dom_sha256": meta["dom_sha256"],
        "rendered_dom_bytes": meta["dom_bytes"],
        "detector_state": state,
        "detector_counts": result["source_native_counts"],
        "starter_count": result["starter_count"],
        "home_starters": result["home_starters"] if usable else [],
        "away_starters": result["away_starters"] if usable else [],
        "usable_as_confirmed_xi_feature": usable,
        "availability_time": meta["observed_at_utc"] if usable else None,
        "backdated_publication_time": None,
        "football_api_requests": 0,
        "api_keys_used": 0,
        "blind100_labels_accessed": 0,
        "research_target_labels_used": 0,
        "model_fits": 0,
        "candidate_probabilities": 0,
        "formal_weight": 0,
    }
    raw = packed(receipt) + b"\n"
    (out / f"capture_receipt_{mode.lower()}_r39t.json").write_bytes(raw)
    (out / f"capture_receipt_{mode.lower()}_r39t.sha256").write_text(sha256(raw) + "\n", encoding="ascii")
    print(json.dumps({"terminal": receipt["terminal"], "window": mode, "state": state, "starter_count": receipt["starter_count"], "usable": usable, "observed_at_utc": receipt["observed_at_utc"], "dom_sha256": receipt["rendered_dom_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R39T_CAPTURE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
