#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as html_lib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def packed(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fail(message: str) -> None:
    raise RuntimeError(message)


def visible_text(dom: str) -> str:
    text = TAG_RE.sub(" ", dom)
    text = html_lib.unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def find_browser() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    fail("HEADLESS_BROWSER_NOT_FOUND")
    raise AssertionError


def validate_contract(c: dict[str, Any]) -> None:
    if c.get("schema_version") != "R39S-PL-RENDERED-LINEUP-TECH-1.0":
        fail("CONTRACT_SCHEMA_MISMATCH")
    if c.get("research_only") is not True or c.get("formal_weight") != 0:
        fail("RESEARCH_BOUNDARY_INVALID")
    if (c.get("source") or {}).get("allowed_host") != "www.premierleague.com":
        fail("SOURCE_HOST_INVALID")
    if int((c.get("render_rules") or {}).get("max_browser_navigations", -1)) != 2:
        fail("NAVIGATION_BUDGET_INVALID")
    b = c.get("hard_boundaries") or {}
    for key in ("blind100_labels_accessed", "research_target_labels_used", "model_fits", "candidate_probabilities"):
        if b.get(key) != 0:
            fail("HARD_BOUNDARY_INVALID")
    if any(b.get(k) is not False for k in (
        "ev", "current_match_prediction", "formal_model_mutation", "formal_data_mutation",
        "config_mutation", "current_rule_mutation", "main_mutation",
    )):
        fail("MUTATION_BOUNDARY_INVALID")


def render(browser: str, url: str, budget_ms: int, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
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
        tail = proc.stderr.decode("utf-8", errors="replace")[-2000:]
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
        "browser_stderr_tail": proc.stderr.decode("utf-8", errors="replace")[-1000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    contract_path = Path(args.contract)
    out = Path(args.output)
    c = json.loads(contract_path.read_text(encoding="utf-8"))
    validate_contract(c)
    out.mkdir(parents=True, exist_ok=False)

    browser = find_browser()
    version = subprocess.run([browser, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10, check=False).stdout.strip()
    rules = c["render_rules"]
    budget_ms = int(rules["virtual_time_budget_ms"])
    max_bytes = int(rules["max_dom_bytes"])

    reference = c["technical_reference"]
    forward = c["forward_target"]
    kickoff = parse_utc(forward["scheduled_kickoff_utc"])
    if utc_now() >= kickoff:
        fail("FORWARD_TARGET_ALREADY_KICKED_OFF")

    ref_raw, ref_meta = render(browser, reference["url"], budget_ms, max_bytes)
    ref_dom = ref_raw.decode("utf-8", errors="replace")
    ref_text = visible_text(ref_dom)
    marker_results = {m: (m.casefold() in ref_text.casefold()) for m in reference["expected_lineup_markers"]}
    if not all(marker_results.values()):
        missing = [m for m, ok in marker_results.items() if not ok]
        fail("REFERENCE_LINEUP_MARKERS_MISSING:" + "|".join(missing))
    (out / "technical_reference_rendered_dom.html").write_bytes(ref_raw)

    fwd_raw, fwd_meta = render(browser, forward["url"], budget_ms, max_bytes)
    fwd_dom = fwd_raw.decode("utf-8", errors="replace")
    fwd_text = visible_text(fwd_dom)
    observed = parse_utc(fwd_meta["observed_at_utc"])
    if observed >= kickoff:
        fail("FORWARD_RENDER_NOT_PRE_KICKOFF")
    identity = {
        "contains_match_id": str(forward["match_id"]) in fwd_dom,
        "contains_home": str(forward["home"]).casefold() in fwd_text.casefold(),
        "contains_away": str(forward["away"]).casefold() in fwd_text.casefold(),
        "contains_lineup_route_or_token": "lineup" in fwd_dom.casefold(),
    }
    if not all(identity.values()):
        fail("FORWARD_RENDER_IDENTITY_FAILED")
    (out / "forward_target_rendered_dom.html").write_bytes(fwd_raw)

    diagnostics = {
        "reference_visible_text_chars": len(ref_text),
        "forward_visible_text_chars": len(fwd_text),
        "reference_formation_occurrences": ref_text.casefold().count("formation"),
        "forward_formation_occurrences": fwd_text.casefold().count("formation"),
        "reference_manager_occurrences": ref_text.casefold().count("manager"),
        "forward_manager_occurrences": fwd_text.casefold().count("manager"),
        "forward_contains_squads_token": "squads" in fwd_text.casefold(),
        "forward_contains_substitutes_token": "substitutes" in fwd_text.casefold(),
    }

    receipt = {
        "schema_version": "R39S-RENDER-RECEIPT-1.0",
        "terminal": "PASS_R39S_RENDERED_LINEUP_TECHNICAL_AUDIT",
        "contract_sha256": sha256(contract_path.read_bytes()),
        "browser_binary": browser,
        "browser_version": version,
        "technical_reference": {
            "role": reference["role"],
            "postmatch_page_loaded": True,
            "outcome_fields_used_for_model_or_evaluation": False,
            "expected_marker_results": marker_results,
            **ref_meta,
        },
        "forward_target": {
            "match_id": forward["match_id"],
            "scheduled_kickoff_utc": forward["scheduled_kickoff_utc"],
            "identity": identity,
            **fwd_meta,
        },
        "diagnostics": diagnostics,
        "blind100_labels_accessed": 0,
        "research_target_labels_used": 0,
        "model_fits": 0,
        "candidate_probabilities": 0,
        "football_api_requests": 0,
        "api_keys_used": 0,
        "formal_weight": 0,
    }
    raw = packed(receipt) + b"\n"
    (out / "render_receipt_r39s.json").write_bytes(raw)
    (out / "render_receipt_r39s.sha256").write_text(sha256(raw) + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": receipt["terminal"],
        "browser_version": version,
        "reference_dom_sha256": ref_meta["dom_sha256"],
        "forward_dom_sha256": fwd_meta["dom_sha256"],
        "forward_observed_at_utc": fwd_meta["observed_at_utc"],
        "diagnostics": diagnostics,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"R39S_RENDER_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
