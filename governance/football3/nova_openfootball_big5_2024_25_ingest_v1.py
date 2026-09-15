#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE_ID = "OPENFOOTBALL_FOOTBALL_JSON"

class IngestError(ValueError):
    pass

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()

def stable_team_id(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise IngestError("team name must be non-empty")
    return f"openfootball:team:{hashlib.sha256(name.strip().encode('utf-8')).hexdigest()[:20]}"

def kickoff_utc(date_s: str, time_s: str, tz_name: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_s or ""):
        raise IngestError(f"invalid date {date_s!r}")
    if not re.fullmatch(r"\d{2}:\d{2}", time_s or ""):
        raise IngestError(f"invalid time {time_s!r}")
    local = datetime.fromisoformat(f"{date_s}T{time_s}:00").replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def normalize_match(match: dict, comp: dict, raw_sha256: str, revision: str) -> dict:
    for key in ("date", "time", "team1", "team2", "score"):
        if key not in match:
            raise IngestError(f"{comp['competition_id']}: match missing {key}")
    score = match["score"]
    if not isinstance(score, dict):
        raise IngestError(f"{comp['competition_id']}: score must be object")
    ft = score.get("ft")
    if ft is None:
        hg = ag = result = None
        result_value_present = False
    elif isinstance(ft, list) and len(ft) == 2 and all(isinstance(x, int) and x >= 0 for x in ft):
        hg, ag = ft
        result = "H" if hg > ag else "A" if hg < ag else "D"
        result_value_present = True
    else:
        raise IngestError(f"{comp['competition_id']}: invalid ft score")
    home, away = match["team1"].strip(), match["team2"].strip()
    if home == away:
        raise IngestError("home and away team must differ")
    kick = kickoff_utc(match["date"], match["time"], comp["timezone"])
    identity_basis = {
        "source_id": SOURCE_ID,
        "source_revision": revision,
        "source_path": comp["path"],
        "competition_id": comp["competition_id"],
        "season": "2024/25",
        "date": match["date"],
        "time": match["time"],
        "home_team": home,
        "away_team": away,
    }
    match_id = f"openfootball:{sha256_hex(canonical(identity_basis))}"
    return {
        "match_id": match_id,
        "competition_id": comp["competition_id"],
        "season": "2024/25",
        "kickoff": kick,
        "home_team_id": stable_team_id(home),
        "away_team_id": stable_team_id(away),
        "home_team_name": home,
        "away_team_name": away,
        "source_id": SOURCE_ID,
        "source_revision": revision,
        "source_path": comp["path"],
        "source_blob_sha1": comp["source_blob_sha1"],
        "input_sha256": raw_sha256,
        "home_goals": hg,
        "away_goals": ag,
        "result_1x2": result,
        "result_value_present": result_value_present,
        "research_label_exposed_before_assignment": True,
        "fresh_candidate_confirmation_eligible": False,
    }

def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Football3-Nova-History-Ingest/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def validate_lock(lock: dict) -> None:
    if lock.get("schema_version") != "football3-nova-openfootball-big5-2024-25-lock-v1":
        raise IngestError("unexpected schema_version")
    src = lock.get("source", {})
    if src.get("source_id") != SOURCE_ID:
        raise IngestError("unexpected source_id")
    if src.get("license_id") != "CC0-1.0":
        raise IngestError("OpenFootball source must remain CC0-1.0")
    rev = src.get("revision", "")
    if not re.fullmatch(r"[0-9a-f]{40}", rev):
        raise IngestError("source revision must be frozen 40-hex commit")
    comps = lock.get("competitions")
    if not isinstance(comps, list) or len(comps) != 5:
        raise IngestError("exactly five competitions required")
    if sum(int(c["expected_matches"]) for c in comps) != int(lock["expected_total_matches"]):
        raise IngestError("expected count total mismatch")
    gov = lock.get("governance", {})
    for key in ("historical_rows_are_permanent", "source_revision_is_immutable", "published_results_are_exposed"):
        if gov.get(key) is not True:
            raise IngestError(f"governance {key} must be true")
    if gov.get("fresh_confirmation_eligible_by_default") is not False:
        raise IngestError("published historical rows cannot be fresh confirmation by default")
    if gov.get("candidate_roles_assigned_in_this_batch") is not False:
        raise IngestError("ingest batch must not assign candidate roles")
    if any(gov.get(k) is not False for k in ("formal_v2_changed", "current_changed", "production_changed")):
        raise IngestError("formal state changes forbidden")

def ingest(lock: dict, payloads: dict[str, bytes], require_frozen: bool) -> tuple[list[dict], dict]:
    validate_lock(lock)
    src = lock["source"]
    revision = src["revision"]
    rows = []
    file_receipts = []
    for comp in lock["competitions"]:
        path = comp["path"]
        if path not in payloads:
            raise IngestError(f"missing payload {path}")
        raw = payloads[path]
        blob = git_blob_sha1(raw)
        if blob != comp["source_blob_sha1"]:
            raise IngestError(f"{path}: git blob sha mismatch {blob}")
        raw_sha = sha256_hex(raw)
        if require_frozen:
            expected_raw = comp.get("raw_sha256")
            if not expected_raw or raw_sha != expected_raw:
                raise IngestError(f"{path}: frozen raw sha256 mismatch")
        obj = json.loads(raw.decode("utf-8"))
        matches = obj.get("matches")
        if not isinstance(matches, list):
            raise IngestError(f"{path}: matches must be list")
        if len(matches) != int(comp["expected_matches"]):
            raise IngestError(f"{path}: expected {comp['expected_matches']} matches, got {len(matches)}")
        comp_rows = [normalize_match(m, comp, raw_sha, revision) for m in matches]
        rows.extend(comp_rows)
        present = sum(1 for r in comp_rows if r["result_value_present"])
        file_receipts.append({
            "competition_id": comp["competition_id"],
            "path": path,
            "source_blob_sha1": blob,
            "raw_sha256": raw_sha,
            "match_count": len(comp_rows),
            "result_value_present_count": present,
            "result_value_missing_count": len(comp_rows) - present,
        })
    ids = [r["match_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise IngestError("duplicate match_id across normalized rows")
    if len(rows) != int(lock["expected_total_matches"]):
        raise IngestError("normalized total match count mismatch")
    rows.sort(key=lambda r: (r["kickoff"], r["competition_id"], r["match_id"]))
    set_sha = sha256_hex(b"\n".join(canonical(r) for r in rows) + b"\n")
    if require_frozen:
        expected = lock.get("normalized_set_sha256")
        if not expected or set_sha != expected:
            raise IngestError("frozen normalized_set_sha256 mismatch")
    present_total = sum(1 for r in rows if r["result_value_present"])
    receipt = {
        "status": "PASS",
        "source_id": SOURCE_ID,
        "source_revision": revision,
        "license_id": src["license_id"],
        "source_count": 1,
        "competition_count": len(lock["competitions"]),
        "match_count": len(rows),
        "expected_match_count": lock["expected_total_matches"],
        "file_receipts": file_receipts,
        "normalized_set_sha256": set_sha,
        "published_results_exposed": len(rows),
        "result_value_present_count": present_total,
        "result_value_missing_count": len(rows) - present_total,
        "missing_result_values_fabricated": False,
        "fresh_confirmation_eligible_count": 0,
        "candidate_roles_assigned": 0,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    return rows, receipt

def download_payloads(lock: dict) -> dict[str, bytes]:
    src = lock["source"]
    base = src["raw_base_url"].rstrip("/")
    rev = src["revision"]
    return {c["path"]: fetch_bytes(f"{base}/{rev}/{c['path']}") for c in lock["competitions"]}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--require-frozen", action="store_true")
    args = ap.parse_args()
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    payloads = download_payloads(lock)
    rows, receipt = ingest(lock, payloads, args.require_frozen)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "matches.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")

if __name__ == "__main__":
    main()
