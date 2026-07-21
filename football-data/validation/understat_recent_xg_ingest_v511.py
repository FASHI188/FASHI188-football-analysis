#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "recent_xg_shadow_v511.json"
OUT_ROOT = ROOT / "evidence" / "xg" / "understat_2025_26"
MANIFEST = ROOT / "manifests" / "understat_recent_xg_ingest_v511_status.json"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FASHI188-football-analysis/1.0; research audit)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "gzip",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://understat.com/"
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        content = response.read()
        encoding = str(response.headers.get("Content-Encoding") or "").lower()
    decoded_bytes = gzip.decompress(content) if encoding == "gzip" or content[:2] == b"\x1f\x8b" else content
    return json.loads(decoded_bytes.decode("utf-8")), content, encoding


def _as_float(value):
    if value in (None, ""):
        return None
    return float(value)


def _as_int(value):
    if value in (None, ""):
        return None
    return int(float(value))


def _normalize_match(item: dict, competition_id: str, source_url: str, observed_at: str):
    if not item.get("isResult"):
        return None
    xg = item.get("xG") or {}
    goals = item.get("goals") or {}
    home = item.get("h") or {}
    away = item.get("a") or {}
    home_xg = _as_float(xg.get("h"))
    away_xg = _as_float(xg.get("a"))
    home_goals = _as_int(goals.get("h"))
    away_goals = _as_int(goals.get("a"))
    dt = item.get("datetime")
    if home_xg is None or away_xg is None or home_goals is None or away_goals is None or not dt:
        return None
    return {
        "schema_version": "V5.1.1-understat-match-xg-r1",
        "competition_id": competition_id,
        "season": "2025/26",
        "understat_match_id": str(item.get("id") or ""),
        "match_datetime_source": str(dt),
        "home_team_source": str(home.get("title") or ""),
        "away_team_source": str(away.get("title") or ""),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_xg": home_xg,
        "away_xg": away_xg,
        "xg_total": home_xg + away_xg,
        "xg_margin": home_xg - away_xg,
        "source_url": source_url,
        "source_observed_at_utc": observed_at,
        "source_role": "RETROSPECTIVE_MATCH_LEVEL_XG",
        "formal_pit_eligible": False,
        "target_match_xg_allowed_as_predictor": False
    }


def main() -> int:
    cfg = _load_json(CONFIG)
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    reports = {}
    failures = {}
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    for competition_id, spec in cfg["domains"].items():
        league = spec["understat_league"]
        url = f"https://understat.com/getLeagueData/{league}/2025"
        try:
            payload, content, content_encoding = _fetch_json(url)
            data = payload.get("dates") or []
            rows = []
            for item in data:
                row = _normalize_match(item, competition_id, url, observed_at)
                if row is not None:
                    rows.append(row)
            rows.sort(key=lambda r: (r["match_datetime_source"], r["home_team_source"], r["away_team_source"]))
            out_path = OUT_ROOT / f"{competition_id}.jsonl"
            out_path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
            min_rows = int(cfg["minimum_ingest_gate"][competition_id])
            reports[competition_id] = {
                "status": "PASS" if len(rows) >= min_rows else "FAIL_BELOW_MINIMUM_MATCH_COUNT",
                "source_url": url,
                "source_payload_sha256": _sha256_bytes(content),
                "content_encoding": content_encoding,
                "date_record_count": len(data),
                "result_xg_row_count": len(rows),
                "minimum_required": min_rows,
                "first_match_datetime": rows[0]["match_datetime_source"] if rows else None,
                "last_match_datetime": rows[-1]["match_datetime_source"] if rows else None,
                "output_path": str(out_path.relative_to(ROOT)),
                "formal_pit_eligible": False
            }
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    passed = [k for k, v in reports.items() if v["status"] == "PASS"]
    manifest = {
        "schema_version": "V5.1.1-understat-recent-xg-ingest-status-r4",
        "generated_at_utc": observed_at,
        "season": "2025/26",
        "endpoint_semantics": "Understat getLeagueData AJAX JSON; match rows from dates[]",
        "requested_domains": list(cfg["domains"].keys()),
        "completed_domains": list(reports.keys()),
        "passed_domains": passed,
        "reports": reports,
        "failures": failures,
        "status": "PASS" if len(passed) == len(cfg["domains"]) and not failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_policy": "Retrospective xG content may be used only as lagged historical shadow features; no formal PIT claim is made."
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
