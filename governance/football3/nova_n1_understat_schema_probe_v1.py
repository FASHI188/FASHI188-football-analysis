#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from typing import Any

LEAGUES = ["EPL", "La_Liga", "Bundesliga", "Serie_A", "Ligue_1"]
SEASON = "2024"
BASE_URL = "https://understat.com/getLeagueData/{league}/{season}"
UA = {
    "User-Agent": "Football3-Nova-N1-schema-only-probe/1.0",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
}
REQUIRED_FEATURE_KEYS = {"deep", "ppda", "date"}
FORBIDDEN_RESULT_KEYS = {"result", "scored", "missed", "goals", "h_goals", "a_goals", "xG", "xGA"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode_json_string_token(token: bytes) -> str:
    return json.loads(token.decode("utf-8"))


def scan_object_keys_only(raw: bytes) -> Counter[str]:
    """Return JSON object-key counts without decoding any JSON value.

    Every quoted token is skipped structurally. Only strings immediately followed by ':'
    are decoded, because those are object keys. String/number/bool/null values are never
    decoded, persisted, or emitted.
    """
    counts: Counter[str] = Counter()
    n = len(raw)
    i = 0
    while i < n:
        if raw[i] != 0x22:  # '"'
            i += 1
            continue
        start = i
        i += 1
        escaped = False
        while i < n:
            b = raw[i]
            if escaped:
                escaped = False
            elif b == 0x5C:  # backslash
                escaped = True
            elif b == 0x22:
                break
            i += 1
        if i >= n:
            raise ValueError("unterminated JSON string")
        end = i + 1
        j = end
        while j < n and raw[j] in b" \t\r\n":
            j += 1
        if j < n and raw[j] == 0x3A:  # ':'
            key = _decode_json_string_token(raw[start:end])
            counts[str(key)] += 1
        i = end
    return counts


def fetch(league: str) -> tuple[bytes, dict[str, Any]]:
    url = BASE_URL.format(league=league, season=SEASON)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        meta = {
            "league": league,
            "url": url,
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type"),
            "content_encoding": resp.headers.get("Content-Encoding"),
            "bytes": len(raw),
            "sha256": sha256(raw),
            "retrieved_at": now(),
        }
    if len(raw) < 1000:
        raise RuntimeError(f"{league}: response too small ({len(raw)})")
    return raw, meta


def probe(league: str) -> dict[str, Any]:
    raw, meta = fetch(league)
    counts = scan_object_keys_only(raw)
    keys = sorted(counts)
    required_present = sorted(REQUIRED_FEATURE_KEYS & set(keys))
    result_keys_present = sorted(FORBIDDEN_RESULT_KEYS & set(keys))
    feature_schema_possible = REQUIRED_FEATURE_KEYS <= set(keys)
    return {
        "league": league,
        "season": SEASON,
        "fetch": meta,
        "status": "SCHEMA_KEYS_SUPPORT_FEATURE_ROUTE" if feature_schema_possible else "SCHEMA_KEYS_INSUFFICIENT",
        "object_keys": keys,
        "key_counts": {k: counts[k] for k in keys},
        "required_feature_keys": sorted(REQUIRED_FEATURE_KEYS),
        "required_feature_keys_present": required_present,
        "result_like_keys_present_but_values_not_decoded": result_keys_present,
        "json_values_decoded": 0,
        "result_values_decoded": 0,
        "goal_or_xg_values_decoded": 0,
        "raw_response_persisted": False,
        "test_identity_opened": False,
        "test_result_vault_opened": False,
        "test_labels_read": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    for league in LEAGUES:
        try:
            reports.append(probe(league))
        except Exception as exc:
            reports.append({
                "league": league,
                "season": SEASON,
                "status": "FETCH_OR_SCHEMA_ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "json_values_decoded": 0,
                "result_values_decoded": 0,
                "goal_or_xg_values_decoded": 0,
                "raw_response_persisted": False,
                "test_identity_opened": False,
                "test_result_vault_opened": False,
                "test_labels_read": False,
            })
    payload = {
        "schema_version": "football3-nova-n1-understat-schema-only-probe-v1",
        "season": SEASON,
        "reports": reports,
        "usage_status": "TECHNICAL_FEASIBILITY_ONLY_NOT_A_LICENSE_GRANT",
        "usage_note": "Public endpoint feasibility only. Source permission/terms must be independently qualified before any feature-value extraction or promotion evidence.",
        "json_values_decoded": 0,
        "test_result_vault_opened": False,
        "test_labels_read": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "probed_at": now(),
    }
    out = args.out / "understat_schema_only_probe.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({r['league']: r['status'] for r in reports}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
