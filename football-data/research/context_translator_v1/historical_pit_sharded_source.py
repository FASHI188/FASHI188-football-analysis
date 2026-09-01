from __future__ import annotations

import concurrent.futures
import json
import pathlib
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import historical_pit_replay as core
import understat_compat as compat
from player_strength import estimate_player_vectors
from historical_pit_sharded_common import (
    MAX_SOURCE_WORKERS, SHARD_SIZE, SOURCE_ATTEMPTS, SOURCE_TIMEOUT_SECONDS,
    ShardError, bounded_fetch, exact_files, init_frozen_understat, now,
    sha_bytes, sha_file, shard_bounds, verify_file_set,
)


def fetch_sitemaps() -> tuple[list[str], list[dict[str, Any]], bool]:
    urls, audits, ok = [], [], True
    for url in core.SPORTSMOLE_SITEMAPS:
        raw, audit = bounded_fetch(url)
        audit["source_kind"] = "SPORTSMOLE_SITEMAP"
        if raw is None:
            ok = False; audits.append(audit); continue
        try:
            root = ET.fromstring(raw)
            locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
            candidates = [x for x in locs if "/football/" in x and ("/preview/" in x or "/injury-news/" in x) and x.endswith(".html")]
            urls.extend(candidates); audit["parse_status"] = "OK"; audit["candidate_urls"] = len(candidates)
        except Exception as exc:
            ok = False; audit["parse_status"] = "PARSE_ERROR"; audit["parse_error"] = f"{type(exc).__name__}: {exc}"[:300]
        audits.append(audit)
    return sorted(set(urls)), audits, ok


def _wayback(url: str, cutoff: datetime) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    audits = []; to = cutoff.strftime("%Y%m%d%H%M%S")
    query = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode({
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "to": to, "limit": "20", "collapse": "digest",
    })
    raw, audit = bounded_fetch(query); audit["source_kind"] = "WAYBACK_CDX"; audits.append(audit)
    if raw is None: return None, audits
    try:
        data = json.loads(raw); rows = data[1:] if isinstance(data, list) and data else []
        valid = [x for x in rows if len(x) >= 3 and str(x[0]).isdigit() and str(x[0]) <= to]
    except Exception as exc:
        audits[-1]["parse_status"] = "PARSE_ERROR"; audits[-1]["parse_error"] = f"{type(exc).__name__}: {exc}"[:300]; return None, audits
    if not valid:
        audits[-1]["parse_status"] = "NO_PRE_CUTOFF_CAPTURE"; return None, audits
    ts = max(str(x[0]) for x in valid); snap = f"https://web.archive.org/web/{ts}id_/{url}"
    body, baudit = bounded_fetch(snap); baudit["source_kind"] = "WAYBACK_SNAPSHOT"; baudit["capture_timestamp"] = ts; audits.append(baudit)
    if body is None: return None, audits
    return {"timestamp": ts, "url": snap, "raw": body}, audits


def fetch_fixture_source(row: dict[str, Any], urls: list[str], sitemap_ok: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    fid = str(row["fixture_id"]); kickoff = core.dt(str(row["kickoff_utc"])); cutoff = core.dt(str(row["prediction_cutoff_utc"]))
    audit: dict[str, Any] = {"fixture_id": fid, "home_team": row["home_team"], "away_team": row["away_team"], "cutoff_utc": core.iso(cutoff), "candidate_audits": [], "retrieved_at": now()}
    def fallback(reason: str, n: int = 0):
        audit["parse_status"] = reason
        return ({"fixture_id": fid, "kickoff_utc": row["kickoff_utc"], "cutoff_utc": row["prediction_cutoff_utc"], "home_team_id": row["home_team_id"], "away_team_id": row["away_team_id"], "home_team": row["home_team"], "away_team": row["away_team"], "pit_legal": False, "missing_reason": reason, "candidate_url_n": n}, audit)
    if not sitemap_ok: return fallback("SOURCE_UNAVAILABLE")
    try:
        candidates = core.sm_candidates(urls, str(row["home_team"]), str(row["away_team"]))
    except Exception as exc:
        raise ShardError(f"team alias conflict for fixture {fid}: {exc}") from exc
    audit["candidate_url_n"] = len(candidates); admitted = []; saw_non_network = False
    for url in candidates[:6]:
        candidate: dict[str, Any] = {"source_url": url, "events": []}
        raw, event = bounded_fetch(url); event["source_kind"] = "SPORTSMOLE_CURRENT"; candidate["events"].append(event)
        if raw is None:
            candidate["parse_status"] = "SOURCE_UNAVAILABLE"; audit["candidate_audits"].append(candidate); continue
        saw_non_network = True
        page = raw.decode("utf-8", "replace"); pub = core.page_time(page, "published"); mod = core.page_time(page, "modified")
        if pub is None or pub >= cutoff or (kickoff - pub).total_seconds() > 10 * 86400:
            candidate["parse_status"] = "PUBLICATION_TIME_NOT_ADMISSIBLE"; audit["candidate_audits"].append(candidate); continue
        proof_type = None; proof_at = None; proof_url = url; proof_raw = raw
        if mod is not None and mod <= cutoff:
            proof_type = "SOURCE_DECLARED_MODIFIED_AT_PRE_CUTOFF"; proof_at = mod
        else:
            wb, waudits = _wayback(url, cutoff); candidate["events"].extend(waudits)
            if wb is None:
                candidate["parse_status"] = "NO_PROVABLE_PRE_T15_SNAPSHOT"; audit["candidate_audits"].append(candidate); continue
            proof_raw = wb["raw"]; proof_url = wb["url"]
            proof_at = datetime.strptime(wb["timestamp"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            page = proof_raw.decode("utf-8", "replace"); p2 = core.page_time(page, "published"); m2 = core.page_time(page, "modified")
            if p2 is None or p2 >= cutoff or (m2 is not None and m2 > proof_at):
                candidate["parse_status"] = "WAYBACK_SNAPSHOT_TIME_CONFLICT"; audit["candidate_audits"].append(candidate); continue
            pub, mod, proof_type = p2, m2, "WAYBACK_SNAPSHOT_PRE_CUTOFF"
        section = core.team_news_section(page)
        if section is None:
            candidate["parse_status"] = "NO_TEAM_NEWS_SECTION"; audit["candidate_audits"].append(candidate); continue
        raw_section, text = section; possible = core.possible_lineups(text)
        source = {
            "source_url": url, "proof_url": proof_url, "proof_type": proof_type,
            "published_at": core.iso(pub), "source_proof_at": core.iso(proof_at), "retrieved_at": now(),
            "response_sha256": sha_bytes(proof_raw), "response_bytes": len(proof_raw),
            "raw_content_scope": "EXACT_H2_TEAM_NEWS_SECTION_STRUCTURED_FACTS_ONLY",
            "raw_content_sha256": sha_bytes(raw_section.encode()), "modified_at": None if mod is None else core.iso(mod),
        }
        admitted.append({"source": source, "text": text, "possible": possible})
        candidate["parse_status"] = "PIT_ADMISSIBLE"; candidate["published_at"] = source["published_at"]; candidate["proof_at"] = source["source_proof_at"]; candidate["response_sha256"] = source["response_sha256"]; audit["candidate_audits"].append(candidate)
    if not admitted:
        return fallback("NO_SOURCE_CONTENT_WITH_PRE_T15_PUBLICATION_PROOF" if saw_non_network or not candidates else "SOURCE_UNAVAILABLE", len(candidates))
    admitted.sort(key=lambda x: (core.dt(x["source"]["source_proof_at"]), core.dt(x["source"]["published_at"]), x["source"]["source_url"]), reverse=True)
    chosen = admitted[0]; audit["parse_status"] = "PIT_ADMISSIBLE"; audit["selected_source_url"] = chosen["source"]["source_url"]
    return ({
        "fixture_id": fid, "kickoff_utc": row["kickoff_utc"], "cutoff_utc": row["prediction_cutoff_utc"],
        "home_team_id": row["home_team_id"], "away_team_id": row["away_team_id"], "home_team": row["home_team"], "away_team": row["away_team"],
        "pit_legal": True, "source": chosen["source"], "source_text_for_identity_only": chosen["text"],
        "possible_lineup_source_names": None if chosen["possible"] is None else {"home": chosen["possible"][0], "away": chosen["possible"][1]},
        "confirmed_lineups": None, "bench": None,
        "confirmed_xi_policy": "NO_HISTORICAL_CONFIRMED_XI_ADMITTED_WITHOUT_INDEPENDENT_PRE_T15_OBSERVATION",
    }, audit)


def _update_history(z: dict[str, Any], events: list[dict[str, Any]], usage: dict[str, list[dict[str, Any]]], registry: dict[str, dict[str, str]], match_id: str, release_at: str) -> None:
    events.extend(z["events"])
    for tid, players in z["usage"].items(): usage[str(tid)].append({"players": players, "known_at": release_at, "match_id": match_id})
    for tid, aliases in z["aliases"].items():
        for p in aliases:
            nn = core.norm(p["player_name"]); old = registry[str(tid)].get(nn)
            if old is not None and old != p["player_id"]: raise ShardError(f"prior-history identity conflict {tid} {nn}")
            registry[str(tid)][nn] = p["player_id"]


def source_freeze(base: pathlib.Path, shard: int, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True); base_manifest = json.load(open(base / "base_freeze_manifest.json")); verify_file_set(base, base_manifest["payload"])
    if base_manifest["labels_read"] != 0: raise ShardError("base freeze labels_read != 0")
    start, end, tag = shard_bounds(shard); cohort = json.load(open(base / "cohort_manifest.json"))["rows"]; target = cohort[start:end]
    if len(target) != SHARD_SIZE: raise ShardError(f"{tag} target size != 50")
    expected_ids = [str(x["fixture_id"]) for x in target]; under_map, _ = init_frozen_understat(base); urls, sitemap_audits, sitemap_ok = fetch_sitemaps()
    fetched, audits = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_SOURCE_WORKERS) as executor:
        futures = {executor.submit(fetch_fixture_source, row, urls, sitemap_ok): str(row["fixture_id"]) for row in target}; done = 0
        for future in concurrent.futures.as_completed(futures):
            fid = futures[future]; source, source_audit = future.result(); fetched[fid], audits[fid] = source, source_audit; done += 1
            if done % 10 == 0 or done == SHARD_SIZE: print(f"[source-freeze {tag}] {done}/50 fixture={fid} status={source_audit.get('parse_status')}", flush=True)
    events: list[dict[str, Any]] = []; usage: dict[str, list[dict[str, Any]]] = defaultdict(list); registry: dict[str, dict[str, str]] = defaultdict(dict); pending: list[dict[str, Any]] = []
    state_rows, packets = [], []; groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cohort[:end]: groups[str(row["kickoff_utc"])].append(row)
    target_set = set(expected_ids)
    def release_ready(cutoff: datetime) -> None:
        ready = [x for x in pending if core.dt(x["release_at"]) < cutoff]; pending[:] = [x for x in pending if core.dt(x["release_at"]) >= cutoff]
        for x in sorted(ready, key=lambda q: (q["release_at"], q["fixture_id"])):
            history = compat.understat_roster(x["understat_match_id"], x["home_team_id"], x["away_team_id"], x["release_at"])
            _update_history(history, events, usage, registry, x["understat_match_id"], x["release_at"])
    for kickoff_text in sorted(groups, key=core.dt):
        kickoff = core.dt(kickoff_text); cutoff = kickoff - timedelta(minutes=core.T15_MINUTES); release_ready(cutoff); batch = sorted(groups[kickoff_text], key=lambda x: x["fixture_id"])
        for row in batch:
            fid = str(row["fixture_id"])
            if fid not in target_set: continue
            packet = core.bind_packet(fetched[fid], registry); vectors = estimate_player_vectors(events, [], as_of=core.iso(cutoff)) if events else {}; tids = {str(row["home_team_id"]), str(row["away_team_id"])}
            vector_subset = {pid: vector.to_dict() for pid, vector in vectors.items() if str(vector.team_id) in tids}; usage_subset = {tid: usage.get(tid, []) for tid in sorted(tids)}; packets.append(packet)
            state_rows.append({"fixture_id": fid, "cutoff_utc": core.iso(cutoff), "understat_match_id": under_map[fid], "vectors": vector_subset, "usage": usage_subset, "state_event_n": len(events), "state_usage_row_n": sum(len(x) for x in usage_subset.values()), "state_sha256": core.canon({"vectors": vector_subset, "usage": usage_subset})})
        for row in batch:
            pending.append({"fixture_id": str(row["fixture_id"]), "understat_match_id": under_map[str(row["fixture_id"])], "home_team_id": str(row["home_team_id"]), "away_team_id": str(row["away_team_id"]), "release_at": core.iso(kickoff + timedelta(hours=core.RELEASE_HOURS))})
    order = {fid: index for index, fid in enumerate(expected_ids)}; packets.sort(key=lambda x: order[str(x["fixture_id"])]); state_rows.sort(key=lambda x: order[str(x["fixture_id"])] )
    if [str(x["fixture_id"]) for x in packets] != expected_ids or [str(x["fixture_id"]) for x in state_rows] != expected_ids: raise ShardError(f"{tag} missing/duplicate/extra source rows")
    core.writejl(out / "fixture_identity.jsonl", target); core.writejl(out / "pit_roster_packets.jsonl", packets); core.writejl(out / "offline_state_snapshots.jsonl", state_rows); core.writejl(out / "source_fetch_audit.jsonl", [audits[fid] for fid in expected_ids]); core.dump(out / "sitemap_fetch_audit.json", {"rows": sitemap_audits, "all_sitemaps_available": sitemap_ok})
    missing = Counter(str(x.get("missing_reason")) for x in packets if not x.get("pit_legal")); payload_names = ["fixture_identity.jsonl", "pit_roster_packets.jsonl", "offline_state_snapshots.jsonl", "source_fetch_audit.jsonl", "sitemap_fetch_audit.json"]
    manifest = {
        "schema_version": "football3-historical-pit-source-freeze-shard-v1", "status": "HISTORICAL_PIT_REPLAY_SOURCE_SHARD_FROZEN",
        "shard": shard, "tag": tag, "start": start, "end_exclusive": end, "n": SHARD_SIZE, "fixture_ids": expected_ids,
        "fixture_identity_sha256": sha_file(out / "fixture_identity.jsonl"), "cohort_identity_sha256": base_manifest["cohort_identity_sha256"],
        "packet_sha256": sha_file(out / "pit_roster_packets.jsonl"), "state_snapshot_sha256": sha_file(out / "offline_state_snapshots.jsonl"),
        "source_fetch_audit_sha256": sha_file(out / "source_fetch_audit.jsonl"), "pit_legal_n": sum(bool(x.get("pit_legal")) for x in packets),
        "missing_reasons": dict(sorted(missing.items())), "identity_attempt_n": sum(int(x.get("identity_attempt_n", 0)) for x in packets),
        "identity_matched_n": sum(int(x.get("identity_matched_n", 0)) for x in packets), "labels_read": 0, "scorer_invoked": False,
        "raw_webpage_body_persisted": False, "request_timeout_seconds": SOURCE_TIMEOUT_SECONDS, "max_attempts": SOURCE_ATTEMPTS,
        "payload": exact_files(out, payload_names),
    }
    core.dump(out / "source_freeze_manifest.json", manifest); return manifest
