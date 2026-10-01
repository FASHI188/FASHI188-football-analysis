#!/usr/bin/env python3
"""Open and evaluate the frozen exact-400 B06 package with the frozen World Model V2.
Research-only. The authoritative B06 identity is fetched from its hash-bound commit.
"""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, re, sys, urllib.request
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "research" / "world_model_v2"
sys.path.insert(0, str(HELPER))
from wmv2_source import MatchMeta, _download_bytes  # type: ignore

BASE_EVAL_PATH = Path(__file__).with_name("evaluate_world_model_v2.py")
spec = importlib.util.spec_from_file_location("wmv2_base_eval", BASE_EVAL_PATH)
base_eval = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = base_eval
spec.loader.exec_module(base_eval)

B06_ID_URL = "https://raw.githubusercontent.com/FASHI188/FASHI188-football-analysis/2d8fc80ed92f63b24c92ab153aa27b81e9da2d45/football-data/research/world_model_v2_b06/B06_MATCHES.jsonl"
B06_GIT_BLOB_SHA1 = "a6d5d8dc02e5a7d484ca68a6c8990c086374e981"
B06_IDENTITY_SHA256 = "4fa5b1a82d7f4b5dd6e07eca6ad157378ff6b287a9cf0843b1593f20a0283046"
COMPETITIONS_URL = "https://raw.githubusercontent.com/hudl/open-data/master/data/competitions.json"
MATCH_URL = "https://raw.githubusercontent.com/hudl/open-data/master/data/matches/{competition_id}/{season_id}.json"
# Frozen B06 exclusion ledger from the identity construction contract.
EXCLUDED_COMPETITION_IDS = {2, 7, 9, 11, 12, 37, 43, 49, 55, 223}


def git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def load_b06_ids() -> set[int]:
    raw = _download_bytes(B06_ID_URL)
    got = git_blob_sha1(raw)
    if got != B06_GIT_BLOB_SHA1:
        raise RuntimeError(f"B06 identity blob mismatch {got}")
    ids = [int(json.loads(line)["match_id"]) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(ids) != 400 or len(set(ids)) != 400:
        raise RuntimeError(f"B06 identity cardinality invalid: rows={len(ids)} unique={len(set(ids))}")
    return set(ids)


def iter_top_level_objects(raw: bytes) -> Iterable[bytes]:
    # Extract top-level JSON objects from a list without structurally decoding unselected rows.
    start = None; depth = 0; in_string = False; escape = False
    for i, ch in enumerate(raw):
        if in_string:
            if escape: escape = False
            elif ch == 92: escape = True
            elif ch == 34: in_string = False
            continue
        if ch == 34:
            in_string = True
        elif ch == 123:
            if depth == 0: start = i
            depth += 1
        elif ch == 125:
            depth -= 1
            if depth == 0 and start is not None:
                yield raw[start:i+1]
                start = None
    if depth != 0 or in_string:
        raise RuntimeError("malformed match JSON while extracting top-level objects")


def load_b06_matches():
    selected = load_b06_ids()
    comps_raw = _download_bytes(COMPETITIONS_URL)
    comps = json.loads(comps_raw.decode("utf-8"))
    found: dict[int, MatchMeta] = {}
    source_counts: dict[str, int] = {}
    selected_object_hashes: dict[int, str] = {}

    for c in comps:
        cid, sid = int(c["competition_id"]), int(c["season_id"])
        if cid in EXCLUDED_COMPETITION_IDS:
            continue
        url = MATCH_URL.format(competition_id=cid, season_id=sid)
        raw = _download_bytes(url)
        count = 0
        for obj_raw in iter_top_level_objects(raw):
            m = re.search(rb'"match_id"\s*:\s*(\d+)', obj_raw)
            if not m:
                continue
            mid = int(m.group(1))
            if mid not in selected:
                continue
            # Authorization boundary: only the 400 selected B06 objects may now be JSON-decoded.
            item = json.loads(obj_raw.decode("utf-8"))
            meta = MatchMeta(
                match_id=mid,
                match_date=str(item["match_date"]),
                kick_off=str(item.get("kick_off") or ""),
                home_id=int(item["home_team"]["home_team_id"]),
                away_id=int(item["away_team"]["away_team_id"]),
                home=str(item["home_team"]["home_team_name"]),
                away=str(item["away_team"]["away_team_name"]),
            )
            if mid in found:
                raise RuntimeError(f"duplicate selected B06 match_id {mid}")
            found[mid] = meta
            selected_object_hashes[mid] = hashlib.sha256(obj_raw).hexdigest()
            count += 1
        if count:
            source_counts[f"{cid}/{sid}"] = count

    missing = sorted(selected - set(found))
    if missing:
        raise RuntimeError(f"could not reconstruct {len(missing)} B06 selected metadata objects; first={missing[:10]}")
    matches = sorted(found.values(), key=lambda m: (m.match_date, m.kick_off, m.match_id))
    ledger_sha = hashlib.sha256("\n".join(f"{mid}:{selected_object_hashes[mid]}" for mid in sorted(selected_object_hashes)).encode()).hexdigest()
    return matches, ledger_sha, source_counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_eval.load_matches = load_b06_matches
    result: dict[str, Any] = base_eval.evaluate(args)
    result["b06"] = {
        "package_id": "B06",
        "identity_rows": 400,
        "authoritative_identity_git_blob_sha1": B06_GIT_BLOB_SHA1,
        "identity_sha256_from_freeze_receipt": B06_IDENTITY_SHA256,
        "selected_match_metadata_structured_parse": True,
        "unselected_match_metadata_structured_parse": False,
        "authorized_event_target_open": True,
    }
    boundary = result.setdefault("boundary", {})
    boundary.update({
        "formal_weight": 0,
        "b05_opened": False,
        "new_protected_labels_opened": 400,
        "reserved_confirmation_panel_opened": False,
        "automatic_confirmation": False,
        "automatic_promotion": False,
        "pit_status": "RETROSPECTIVE_OPEN_DATA_NOT_PROVEN_HISTORICAL_PIT",
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps({
        "status": result.get("status"),
        "research_metric_gate_pass": result.get("research_metric_gate_pass"),
        "coverage": result.get("coverage"),
        "split": result.get("split"),
        "deltas": (result.get("metrics") or {}).get("deltas_candidate_minus_baseline"),
        "bootstrap": result.get("bootstrap"),
        "development_gate": result.get("development_gate"),
        "theta": {k:v for k,v in (result.get("theta") or {}).items() if k != "path"},
        "boundary": boundary,
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
