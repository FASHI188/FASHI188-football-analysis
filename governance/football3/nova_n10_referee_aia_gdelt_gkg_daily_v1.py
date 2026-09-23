#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import ssl
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

class GDELTDailyError(RuntimeError):
    pass

URL_RE = re.compile(rb"https?://[^\s\t;"'<>]+", re.I)

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise GDELTDailyError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def normalize_identity(url: str) -> tuple[str, str] | None:
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return None
    if p.scheme.lower() not in {"http", "https"}:
        return None
    h = (p.hostname or "").lower()
    if h.startswith("www."):
        h = h[4:]
    path = urllib.parse.unquote(p.path or "")
    if len(path) > 1:
        path = path.rstrip("/")
    return h, path

def exact_target_url(url: str, target: dict[str, Any]) -> bool:
    ident = normalize_identity(url)
    if ident is None:
        return False
    return ident == (target["normalized_host"], target["normalized_path"])

def build_daily_url(template: str, iso_date: str) -> str:
    yyyymmdd = iso_date.replace("-", "")
    req(len(yyyymmdd) == 8 and yyyymmdd.isdigit(), "DATE_FORMAT")
    return template.replace("{yyyymmdd}", yyyymmdd)

def fetch(url: str, timeout: int, limit: int, allowed_host: str) -> tuple[bytes, str, dict[str, str]]:
    req(host(url) == allowed_host, "REQUEST_HOST")
    rq = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Football3-Nova-N10-GDELTDailyGKG/1.0",
            "Accept": "application/zip,application/octet-stream,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(rq, timeout=timeout, context=ssl.create_default_context()) as r:
        final = r.geturl()
        req(host(final) == allowed_host, "REDIRECT_OUTSIDE_GDELT")
        data = r.read(limit + 1)
        req(len(data) <= limit, "ZIP_TOO_LARGE")
        return data, final, {k.lower(): v for k, v in r.headers.items()}

def urls_from_line(line: bytes) -> list[str]:
    out = []
    for m in URL_RE.finditer(line):
        raw = m.group(0).rstrip(b".,)]}")
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(raw.decode("latin1", "ignore"))
    return out

def scan_zip(zip_bytes: bytes, target: dict[str, Any], expected_suffix: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    member_reports = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        req(names, "EMPTY_ZIP")
        members = [n for n in names if n.endswith(expected_suffix)]
        req(members, "EXPECTED_GKG_MEMBER_MISSING")
        for name in members:
            line_n = 0
            byte_n = 0
            target_line_n = 0
            with zf.open(name, "r") as fh:
                for line in fh:
                    line_n += 1
                    byte_n += len(line)
                    line_matches = []
                    for u in urls_from_line(line):
                        if exact_target_url(u, target):
                            line_matches.append(u)
                    if line_matches:
                        target_line_n += 1
                        matches.append({
                            "member": name,
                            "line_sha256": sha256_bytes(line),
                            "matched_urls": sorted(set(line_matches)),
                        })
            member_reports.append({
                "member": name,
                "line_n": line_n,
                "decompressed_bytes_scanned": byte_n,
                "matching_line_n": target_line_n,
            })
    return {
        "member_reports": member_reports,
        "match_n": len(matches),
        "matches": matches,
    }

def run(registry: Path, out: Path) -> dict[str, Any]:
    p = json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"] == "DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY", "STATUS")
    req(p["exact_base"] == "15cf52d412603729ff2e0eb1b13edf2718d158c2", "EXACT_BASE")
    req(
        p["parent"]["canonical_ledger_sha256"] ==
        "0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b",
        "LEDGER_SHA",
    )
    target = p["target"]
    req(target["round"] == 10, "FROZEN_ROUND")
    req(len(target["date_sequence"]) == 8, "FROZEN_DAY_N")
    req(target["date_sequence"][0] == target["published_date"], "WINDOW_START")
    req(
        normalize_identity(target["official_url"]) ==
        (target["normalized_host"], target["normalized_path"]),
        "TARGET_NORMALIZATION",
    )

    h = p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False, "ZERO_LABEL")
    req(
        h["match_payload_read"] is False and
        h["standings_payload_read"] is False and
        h["player_stats_payload_read"] is False,
        "NO_SPORT_PAYLOAD",
    )
    req(h["article_body_read"] is False and h["appointment_body_read"] is False, "NO_BODY")
    req(h["full_gkg_row_persisted"] is False, "NO_FULL_ROW")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False, "NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False, "NO_PAID_SECRET")
    req(h["candidate_weight"] == 0 and h["matrix_delta"] == 0, "ZERO_WEIGHT")

    src = p["source"]
    ac = p["acquisition_contract"]
    reports = []
    errors = []
    positive_days = []

    for day in target["date_sequence"]:
        u = build_daily_url(src["daily_url_template"], day)
        try:
            raw, final, headers = fetch(
                u,
                timeout=int(ac["request_timeout_seconds"]),
                limit=int(ac["max_zip_bytes"]),
                allowed_host=src["allowed_host"],
            )
            scan = scan_zip(raw, target, src["expected_zip_member_suffix"])
            report = {
                "date": day,
                "source_url": u,
                "final_url": final,
                "zip_sha256": sha256_bytes(raw),
                "zip_bytes": len(raw),
                "content_type": headers.get("content-type"),
                **scan,
            }
            reports.append(report)
            if scan["match_n"] > 0:
                positive_days.append(day)
        except Exception as exc:
            errors.append({
                "date": day,
                "source_url": u,
                "error": f"{type(exc).__name__}:{exc}"[:500],
            })

    positive = bool(positive_days)
    all_days_fetched = len(reports) == len(target["date_sequence"])
    classification = (
        p["decision_contract"]["positive_classification"]
        if positive else
        p["decision_contract"]["zero_match_classification"]
    )
    reason = (
        "EXACT_AIA_URL_PRESENT_IN_HISTORICAL_GDELT_DAILY_GKG"
        if positive else
        ("NO_EXACT_AIA_URL_MATCH_IN_EIGHT_DAY_GKG_WINDOW"
         if all_days_fetched else
         "INCOMPLETE_GDELT_DAILY_FETCH_NO_POSITIVE_MATCH")
    )

    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "football3-nova-n10-referee-aia-gdelt-gkg-daily-receipt-v1",
        "status": "N10_REFEREE_AIA_GDELT_DAILY_GKG_FEASIBILITY_COMPLETE",
        "classification": classification,
        "reason": reason,
        "exact_base": p["exact_base"],
        "registry_sha256": sha256_bytes(registry.read_bytes()),
        "target_round": target["round"],
        "window_day_n": len(target["date_sequence"]),
        "successful_day_n": len(reports),
        "error_n": len(errors),
        "reports": reports,
        "errors": errors,
        "positive_day_n": len(positive_days),
        "positive_days": positive_days,
        "all_days_fetched": all_days_fetched,
        "full_gkg_rows_persisted": False,
        "matching_full_lines_persisted": False,
        "article_body_read": False,
        "appointment_body_read": False,
        "match_payload_read": False,
        "standings_payload_read": False,
        "player_stats_payload_read": False,
        "day_level_observation_only": positive,
        "exact_observation_time_proven": False,
        "formal_available_at_proven": False,
        "fixture_level_binding_complete": False,
        "full_big5_data_ready": False,
        "referee_oof_allowed": False,
        "result_labels_read": 0,
        "score_values_read": 0,
        "training_performed": False,
        "scoring_performed": False,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
        "candidate_weight": 0,
        "matrix_delta": 0,
        "source_family_closed": False if not positive else False,
        "next_step": (
            p["decision_contract"]["next_if_positive"]
            if positive else
            p["decision_contract"]["next_if_zero"]
        ),
    }
    (out / "aia_gdelt_daily_gkg_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, sort_keys=True, ensure_ascii=False))
    return receipt

def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--registry", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    x = a.parse_args()
    run(x.registry, x.out)

if __name__ == "__main__":
    main()
