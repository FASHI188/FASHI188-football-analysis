from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import pathlib
import random
import re
import shutil
import statistics
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import candidate_b_diagnostic as cbd
import historical_pit_replay as core
from candidate_c import UNCERTAINTY_BY_GRADE, c3_confirmed_xi, c4_bench, combine_effects, lineup_residual_component
from player_strength import PlayerVector

BASE_HEAD = "a58c530501e51a29ac448ed2ee2f50f36795d2d9"
COHORT_SHA = "4663d6a534840e4b80975ee104e86bed0b5402cb332ee28d46c9cac4da2c9cba"
COHORT_N = 300
SHARD_N = 6
SHARD_SIZE = 50
PL_COMPETITION = 8
PL_SEASON = 2023
PL_BASE = "https://sdp-prem-prod.premier-league-prod.pulselive.com"
PL_MATCHES = f"{PL_BASE}/api/v2/matches?competition={PL_COMPETITION}&season={PL_SEASON}&_limit=100"
PL_LINEUP = PL_BASE + "/api/v3/matches/{match_id}/lineups"
RELEASE_HOURS = 3
MIN_ACTIVE_N = 60
MIN_LL_IMPROVEMENT = 0.002
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260901
MODELS = (
    "protected_v2",
    "candidate_c_no_lineup",
    "oracle_confirmed_xi",
    "oracle_confirmed_xi_bench",
    "oracle_full",
)
OUTCOMES = ("home", "draw", "away")
TAGS = (
    "HISTORICAL_LINEUP_ORACLE",
    "UPPER_BOUND_ONLY",
    "NOT_STRICT_PIT",
    "NOT_PROMOTION_ELIGIBLE",
    "POST_VIEW_RESEARCH",
)
FORBIDDEN_SANITIZED_KEYS = {
    "score", "result", "home_score", "away_score", "homegoals", "awaygoals",
    "event", "events", "shots", "shot", "goals", "goal", "assists", "assist",
    "rating", "ratings", "minutes", "minute", "substitution", "substitutiontime",
    "statistics", "stats", "period", "clock", "halftimescore",
}


class OracleError(RuntimeError):
    pass


def dt(value: str) -> datetime:
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise OracleError(f"timezone required: {value!r}")
    return d.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def canon(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dump(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def readjl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def writejl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n" for x in rows), encoding="utf-8")


def exact_files(root: pathlib.Path, names: list[str]) -> dict[str, dict[str, Any]]:
    return {name: {"sha256": sha_file(root / name), "bytes": (root / name).stat().st_size} for name in names}


def verify_file_set(root: pathlib.Path, payload: dict[str, dict[str, Any]]) -> None:
    expected = set(payload)
    actual = {p.name for p in root.iterdir() if p.is_file()}
    if actual != expected | ({"artifact_manifest.json"} if (root / "artifact_manifest.json").exists() else set()):
        extra = actual - expected
        if extra not in (set(), {"artifact_manifest.json"}, {"source_index_manifest.json"},
                         {"oracle_source_shard_manifest.json"}, {"oracle_source_manifest.json"},
                         {"oracle_prediction_shard_manifest.json"}, {"oracle_pre_score_manifest.json"}):
            raise OracleError(f"file set mismatch extra={sorted(extra)} missing={sorted(expected-actual)}")
    for name, meta in payload.items():
        p = root / name
        if not p.exists() or p.stat().st_size != int(meta["bytes"]) or sha_file(p) != str(meta["sha256"]):
            raise OracleError(f"payload mismatch {name}")


def norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def fetch(url: str, *, timeout: int = 30, attempts: int = 4) -> tuple[bytes, dict[str, str]]:
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "football3-lineup-oracle/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), {str(k): str(v) for k, v in r.headers.items()}
        except Exception as exc:
            last = exc
            time.sleep(min(2 ** i, 8))
    raise OracleError(f"public keyless Premier League source fetch failed: {url}: {last}")


def _page_url(base: str, nxt: str | None) -> str:
    if not nxt:
        return base
    return base + "&_next=" + urllib.parse.quote(str(nxt), safe="")


def load_base_cohort(base: pathlib.Path) -> list[dict[str, Any]]:
    bm = json.load(open(base / "base_freeze_manifest.json"))
    if bm.get("cohort_identity_sha256") != COHORT_SHA:
        raise OracleError("base cohort SHA mismatch")
    cm = json.load(open(base / "cohort_manifest.json"))
    if cm.get("cohort_identity_sha256") != COHORT_SHA or int(cm.get("n", 0)) != COHORT_N:
        raise OracleError("cohort manifest identity mismatch")
    rows = cm["rows"]
    if len(rows) != COHORT_N or canon(rows) != COHORT_SHA:
        raise OracleError("cohort rows no longer reproduce frozen SHA")
    return rows


def collect_index(base: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    cohort = load_base_cohort(base)
    raw_dir = out / "raw"
    raw_dir.mkdir()
    all_matches: list[dict[str, Any]] = []
    raw_receipts: list[dict[str, Any]] = []
    nxt = None
    page = 0
    while True:
        url = _page_url(PL_MATCHES, nxt)
        raw, headers = fetch(url)
        path = raw_dir / f"matches_{page:02d}.json"
        path.write_bytes(raw)
        payload = json.loads(raw)
        data = payload.get("data")
        if not isinstance(data, list):
            raise OracleError("PL match index missing data array")
        all_matches.extend(data)
        raw_receipts.append({
            "url": url, "file": str(path.relative_to(out)), "sha256": sha_bytes(raw), "bytes": len(raw),
            "auth": "NONE", "api_key": False, "source": "OFFICIAL_PREMIER_LEAGUE_PUBLIC_WEBSITE_SDP",
        })
        nxt = (payload.get("pagination") or {}).get("_next")
        page += 1
        if not nxt:
            break
        if page > 10:
            raise OracleError("unexpected PL match pagination")
    idx: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for m in all_matches:
        mid = str(m.get("matchId") or m.get("id") or "")
        ko = str(m.get("kickoff") or "")
        ht = (m.get("homeTeam") or {}).get("name") or (m.get("homeTeam") or {}).get("shortName") or ""
        at = (m.get("awayTeam") or {}).get("name") or (m.get("awayTeam") or {}).get("shortName") or ""
        if mid and ko and ht and at:
            idx[(iso(dt(ko))[:10], norm(ht), norm(at))].append({"pl_match_id": mid, "kickoff_utc": iso(dt(ko)), "home": ht, "away": at})
    mapped: list[dict[str, Any]] = []
    used: set[str] = set()
    for r in cohort:
        key = (str(r["kickoff_utc"])[:10], norm(r["home_team"]), norm(r["away_team"]))
        cand = idx.get(key, [])
        if len(cand) != 1:
            raise OracleError(f"PL match identity conflict/miss {key}: {len(cand)}")
        z = cand[0]
        if z["pl_match_id"] in used:
            raise OracleError("PL match ID reused across frozen cohort")
        used.add(z["pl_match_id"])
        mapped.append({
            "fixture_id": str(r["fixture_id"]), "pl_match_id": z["pl_match_id"],
            "kickoff_utc": str(r["kickoff_utc"]), "home_team_id": str(r["home_team_id"]),
            "away_team_id": str(r["away_team_id"]), "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
        })
    if len(mapped) != COHORT_N:
        raise OracleError("PL identity map not exactly 300")
    writejl(out / "match_identity_index.jsonl", mapped)
    dump(out / "raw_source_receipts.json", {"rows": raw_receipts, "rows_sha256": canon(raw_receipts), "count": len(raw_receipts)})
    names = ["match_identity_index.jsonl", "raw_source_receipts.json"]
    manifest = {
        "schema_version": "football3-lineup-oracle-source-index-v1",
        "status": "HISTORICAL_LINEUP_ORACLE_SOURCE_INDEX_FROZEN",
        "tags": list(TAGS), "cohort_identity_sha256": COHORT_SHA, "n": COHORT_N,
        "source": "OFFICIAL_PREMIER_LEAGUE_PUBLIC_WEBSITE_SDP", "source_auth": "NONE",
        "external_api_key_used": False, "provider_secret_used": False,
        "match_identity_rule": "fixture date + canonical home + canonical away; score/result fields never persisted into sanitized identity index",
        "raw_source_sha256": canon(raw_receipts),
        "payload": exact_files(out, names),
        "raw_payload": {str(p.relative_to(out)): {"sha256": sha_file(p), "bytes": p.stat().st_size} for p in sorted(raw_dir.iterdir())},
    }
    dump(out / "source_index_manifest.json", manifest)
    return manifest


def _display_name(p: dict[str, Any]) -> str:
    for k in ("knownName", "displayName", "name"):
        if p.get(k):
            return str(p[k]).strip()
    first = str(p.get("firstName") or "").strip()
    last = str(p.get("lastName") or "").strip()
    return (first + " " + last).strip()


def _flatten_ids(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for x in value:
            if isinstance(x, list):
                out.extend(_flatten_ids(x))
            elif isinstance(x, (str, int)):
                out.append(str(x))
    return out


def sanitize_team(team: dict[str, Any], expected_team_id: str) -> dict[str, Any]:
    formation = team.get("formation") or {}
    starters = _flatten_ids(formation.get("lineup"))
    bench_ids = [str(x) for x in (formation.get("subs") or [])]
    if len(starters) != 11 or len(set(starters)) != 11:
        raise OracleError(f"official lineup does not contain unique XI for team {expected_team_id}")
    if not bench_ids or len(set(bench_ids)) != len(bench_ids):
        raise OracleError(f"official lineup bench missing/duplicate for team {expected_team_id}")
    if set(starters) & set(bench_ids):
        raise OracleError("starter/bench overlap in official lineup")
    players = team.get("players") or []
    pmap = {str(p.get("id")): p for p in players if p.get("id") is not None}
    def row(pid: str) -> dict[str, Any]:
        p = pmap.get(pid)
        if p is None:
            raise OracleError(f"official lineup player identity {pid} missing")
        name = _display_name(p)
        pos = str(p.get("position") or "").strip()
        if not name or not pos:
            raise OracleError(f"official player name/position missing {pid}")
        return {"source_player_id": "pl:" + pid, "name": name, "position": pos}
    managers = team.get("managers") or []
    if not managers:
        raise OracleError("official lineup manager missing")
    manager = managers[0]
    manager_name = _display_name(manager)
    manager_id = str(manager.get("id") or "")
    formation_name = str(formation.get("formation") or "").strip()
    if not manager_name or not manager_id or not formation_name:
        raise OracleError("official lineup formation/manager identity incomplete")
    team_id = str(team.get("teamId") or team.get("id") or "")
    return {
        "source_team_id": team_id,
        "expected_team_id": str(expected_team_id),
        "formation": formation_name,
        "manager": {"source_manager_id": "pl:" + manager_id, "name": manager_name},
        "starting_xi": [row(x) for x in starters],
        "bench": [row(x) for x in bench_ids],
    }


def assert_sanitized(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = re.sub(r"[^a-z0-9]+", "", str(k).lower())
            if nk in FORBIDDEN_SANITIZED_KEYS:
                raise OracleError(f"forbidden target/postmatch field reached sanitized Oracle packet: {k}")
            assert_sanitized(v)
    elif isinstance(obj, list):
        for x in obj:
            assert_sanitized(x)


def collect_lineup_shard(base: pathlib.Path, index_root: pathlib.Path, shard: int, out: pathlib.Path) -> dict[str, Any]:
    if shard < 0 or shard >= SHARD_N:
        raise OracleError("invalid shard")
    out.mkdir(parents=True, exist_ok=True)
    cohort = load_base_cohort(base)
    im = json.load(open(index_root / "source_index_manifest.json"))
    if im.get("cohort_identity_sha256") != COHORT_SHA or im.get("external_api_key_used") or im.get("provider_secret_used"):
        raise OracleError("source index governance mismatch")
    idx = readjl(index_root / "match_identity_index.jsonl")
    if [str(x["fixture_id"]) for x in idx] != [str(x["fixture_id"]) for x in cohort]:
        raise OracleError("source index order/identity mismatch")
    start, end = shard * SHARD_SIZE, (shard + 1) * SHARD_SIZE
    raw_dir = out / "raw"
    raw_dir.mkdir()
    packets: list[dict[str, Any]] = []
    raw_receipts: list[dict[str, Any]] = []
    for i, (r, ident) in enumerate(zip(cohort[start:end], idx[start:end]), 1):
        fid = str(r["fixture_id"])
        if str(ident["fixture_id"]) != fid:
            raise OracleError("shard fixture binding mismatch")
        url = PL_LINEUP.format(match_id=ident["pl_match_id"])
        raw, _ = fetch(url)
        raw_path = raw_dir / f"{fid}.json"
        raw_path.write_bytes(raw)
        payload = json.loads(raw)
        home = payload.get("home_team")
        away = payload.get("away_team")
        if not isinstance(home, dict) or not isinstance(away, dict):
            raise OracleError(f"official lineup sides missing {fid}")
        packet = {
            "fixture_id": fid, "pl_match_id": str(ident["pl_match_id"]), "kickoff_utc": str(r["kickoff_utc"]),
            "home_team_id": str(r["home_team_id"]), "away_team_id": str(r["away_team_id"]),
            "home_team": str(r["home_team"]), "away_team": str(r["away_team"]),
            "research_tags": list(TAGS),
            "home": sanitize_team(home, str(r["home_team_id"])),
            "away": sanitize_team(away, str(r["away_team_id"])),
            "source": {
                "provider": "OFFICIAL_PREMIER_LEAGUE_PUBLIC_WEBSITE_SDP",
                "url": url, "raw_sha256": sha_bytes(raw), "raw_bytes": len(raw), "auth": "NONE",
            },
        }
        assert_sanitized(packet)
        packets.append(packet)
        raw_receipts.append({"fixture_id": fid, "url": url, "file": str(raw_path.relative_to(out)), "sha256": sha_bytes(raw), "bytes": len(raw), "auth": "NONE"})
        if i % 10 == 0:
            print(f"[oracle-source shard={shard}] {i}/50 fixture={fid}", flush=True)
        time.sleep(0.10)
    expected_ids = [str(x["fixture_id"]) for x in cohort[start:end]]
    if [str(x["fixture_id"]) for x in packets] != expected_ids:
        raise OracleError("source shard order mismatch")
    writejl(out / "oracle_lineup_packets.jsonl", packets)
    dump(out / "raw_source_receipts.json", {"rows": raw_receipts, "rows_sha256": canon(raw_receipts), "count": len(raw_receipts)})
    names = ["oracle_lineup_packets.jsonl", "raw_source_receipts.json"]
    manifest = {
        "schema_version": "football3-lineup-oracle-source-shard-v1",
        "status": "HISTORICAL_LINEUP_ORACLE_SOURCE_SHARD_FROZEN", "tags": list(TAGS),
        "shard": shard, "start": start, "end_exclusive": end, "n": SHARD_SIZE, "fixture_ids": expected_ids,
        "cohort_identity_sha256": COHORT_SHA, "source_index_sha256": sha_file(index_root / "source_index_manifest.json"),
        "source": "OFFICIAL_PREMIER_LEAGUE_PUBLIC_WEBSITE_SDP", "source_auth": "NONE",
        "external_api_key_used": False, "provider_secret_used": False,
        "forbidden_target_fields_persisted": False,
        "actual_substitution_usage_persisted": False,
        "target_match_event_or_stat_endpoint_called": False,
        "raw_source_sha256": canon(raw_receipts),
        "packet_sha256": sha_file(out / "oracle_lineup_packets.jsonl"),
        "payload": exact_files(out, names),
        "raw_payload": {str(p.relative_to(out)): {"sha256": sha_file(p), "bytes": p.stat().st_size} for p in sorted(raw_dir.iterdir())},
    }
    dump(out / "oracle_source_shard_manifest.json", manifest)
    return manifest


def discover(root: pathlib.Path, filename: str) -> list[tuple[pathlib.Path, dict[str, Any]]]:
    out = []
    for p in sorted(root.rglob(filename)):
        out.append((p.parent, json.load(open(p))))
    return out


def merge_source(base: pathlib.Path, shards: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    cohort = load_base_cohort(base)
    found = discover(shards, "oracle_source_shard_manifest.json")
    by_shard = {int(m["shard"]): (r, m) for r, m in found}
    if set(by_shard) != set(range(SHARD_N)) or len(found) != SHARD_N:
        raise OracleError("source shard set mismatch")
    packets: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    raw_receipts: list[dict[str, Any]] = []
    for shard in range(SHARD_N):
        root, m = by_shard[shard]
        if m.get("cohort_identity_sha256") != COHORT_SHA or m.get("external_api_key_used") or m.get("provider_secret_used"):
            raise OracleError("source shard governance mismatch")
        start, end = shard * SHARD_SIZE, (shard + 1) * SHARD_SIZE
        expected = [str(x["fixture_id"]) for x in cohort[start:end]]
        if list(map(str, m["fixture_ids"])) != expected:
            raise OracleError("source shard identity mismatch")
        rows = readjl(root / "oracle_lineup_packets.jsonl")
        if [str(x["fixture_id"]) for x in rows] != expected:
            raise OracleError("source packet order mismatch")
        for row in rows:
            assert_sanitized(row)
        packets.extend(rows)
        rr = json.load(open(root / "raw_source_receipts.json"))["rows"]
        raw_receipts.extend(rr)
        receipts.append({"shard": shard, "manifest_sha256": sha_file(root / "oracle_source_shard_manifest.json"), "packet_sha256": m["packet_sha256"], "raw_source_sha256": m["raw_source_sha256"]})
    expected_all = [str(x["fixture_id"]) for x in cohort]
    got = [str(x["fixture_id"]) for x in packets]
    if got != expected_all or len(set(got)) != COHORT_N:
        raise OracleError("merged source missing/duplicate/extra/order conflict")
    writejl(out / "oracle_lineup_packets.jsonl", packets)
    dump(out / "source_shard_receipts.json", {"rows": receipts, "rows_sha256": canon(receipts), "count": len(receipts)})
    dump(out / "raw_source_receipts.json", {"rows": raw_receipts, "rows_sha256": canon(raw_receipts), "count": len(raw_receipts)})
    names = ["oracle_lineup_packets.jsonl", "source_shard_receipts.json", "raw_source_receipts.json"]
    manifest = {
        "schema_version": "football3-lineup-oracle-source-merged-v1",
        "status": "HISTORICAL_LINEUP_ORACLE_SOURCE_FROZEN", "tags": list(TAGS),
        "n": COHORT_N, "source_shard_n": SHARD_N, "cohort_identity_sha256": COHORT_SHA,
        "source": "OFFICIAL_PREMIER_LEAGUE_PUBLIC_WEBSITE_SDP", "source_auth": "NONE",
        "external_api_key_used": False, "provider_secret_used": False,
        "raw_source_file_n": len(raw_receipts),
        "raw_source_aggregate_sha256": canon(raw_receipts),
        "packet_sha256": sha_file(out / "oracle_lineup_packets.jsonl"),
        "payload": exact_files(out, names),
    }
    dump(out / "oracle_source_manifest.json", manifest)
    return manifest


def _load_vectors(snapshot: dict[str, Any]) -> dict[str, PlayerVector]:
    return {str(pid): PlayerVector(**raw) for pid, raw in (snapshot.get("vectors") or {}).items()}


def _source_rows(packet: dict[str, Any], side: str, area: str) -> list[dict[str, Any]]:
    return list((packet.get(side) or {}).get(area) or [])


def build_registry(snapshot: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    registry: dict[str, dict[str, str]] = defaultdict(dict)
    conflicts = []
    for tid, matches in (snapshot.get("usage") or {}).items():
        seen: dict[str, set[str]] = defaultdict(set)
        for rec in matches:
            for p in rec.get("players") or []:
                name = norm(p.get("player_name") or "")
                pid = str(p.get("player_id") or "")
                if name and pid:
                    seen[name].add(pid)
        for name, ids in seen.items():
            if len(ids) > 1:
                conflicts.append({"team_id": str(tid), "normalized_name": name, "player_ids": sorted(ids)})
            elif ids:
                registry[str(tid)][name] = next(iter(ids))
    if conflicts:
        raise OracleError(f"prior-history player identity conflict: {conflicts[:3]}")
    return registry, {"team_n": len(registry), "alias_n": sum(len(x) for x in registry.values()), "conflict_n": 0}


def map_player(row: dict[str, Any], team_id: str, registry: dict[str, dict[str, str]]) -> tuple[str, str]:
    source_id = str(row["source_player_id"])
    name = norm(row["name"])
    choices = registry.get(str(team_id), {})
    if name in choices:
        return choices[name], "EXACT_NORMALIZED_NAME"
    toks = name.split()
    if toks:
        last = toks[-1]
        first = toks[0][0] if toks[0] else ""
        cand = [(n, pid) for n, pid in choices.items() if n.split() and n.split()[-1] == last and n.split()[0][:1] == first]
        ids = sorted({pid for _, pid in cand})
        if len(ids) == 1:
            return ids[0], "UNIQUE_LAST_NAME_FIRST_INITIAL"
    contain = [(n, pid) for n, pid in choices.items() if name and (name in n or n in name)]
    ids = sorted({pid for _, pid in contain})
    if len(ids) == 1:
        return ids[0], "UNIQUE_NAME_CONTAINMENT"
    scored = sorted(((difflib.SequenceMatcher(None, name, n).ratio(), n, pid) for n, pid in choices.items()), reverse=True)
    if scored and scored[0][0] >= 0.84 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
        return scored[0][2], "UNIQUE_FUZZY_NAME"
    return "oracle_" + source_id.replace(":", "_"), "UNMATCHED_SHRINK_TO_EMPIRICAL_TEAM_REFERENCE"


def mapped_lineup(packet: dict[str, Any], side: str, team_id: str, registry: dict[str, dict[str, str]], area: str) -> tuple[list[dict[str, Any]], Counter]:
    counts = Counter()
    out = []
    for row in _source_rows(packet, side, area):
        pid, reason = map_player(row, team_id, registry)
        counts[reason] += 1
        out.append({"player_id": pid, "source_player_id": row["source_player_id"], "name": row["name"], "position": row["position"], "identity_rule": reason})
    return out, counts


def prior_context_usage(all_packets: list[dict[str, Any]], target_packet: dict[str, Any], target_cutoff: str, registry: dict[str, dict[str, str]], mode: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    co = dt(target_cutoff)
    usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts = {"home": 0, "away": 0}
    target_ko = dt(target_packet["kickoff_utc"])
    for prior in all_packets:
        pko = dt(prior["kickoff_utc"])
        if pko >= target_ko:
            continue
        known = pko + timedelta(hours=RELEASE_HOURS)
        if known >= co:
            continue
        for side in ("home", "away"):
            tid = str(prior[f"{side}_team_id"])
            target_side = "home" if tid == str(target_packet["home_team_id"]) else "away" if tid == str(target_packet["away_team_id"]) else None
            if target_side is None:
                continue
            a = prior[side]
            t = target_packet[target_side]
            if mode == "coach_formation":
                if a["manager"]["source_manager_id"] != t["manager"]["source_manager_id"] or a["formation"] != t["formation"]:
                    continue
            elif mode == "coach":
                if a["manager"]["source_manager_id"] != t["manager"]["source_manager_id"]:
                    continue
            else:
                raise OracleError("unknown context mode")
            mapped, _ = mapped_lineup(prior, side, tid, registry, "starting_xi")
            if len(mapped) != 11 or len({x["player_id"] for x in mapped}) != 11:
                continue
            usage[tid].append({
                "known_at": iso(known), "match_id": "oracle:" + str(prior["pl_match_id"]),
                "players": [{"player_id": x["player_id"], "started": True} for x in mapped],
            })
            counts[target_side] += 1
    return dict(usage), counts


def _effect_prediction(base_pred: dict[str, float], effect: Any, lock: dict[str, Any], eng: Any) -> dict[str, float]:
    return core.c_effect_pred(base_pred, effect, lock, eng)


def predict_shard(base: pathlib.Path, prior: pathlib.Path, source: pathlib.Path, state_root: pathlib.Path, shard: int, out: pathlib.Path) -> dict[str, Any]:
    if shard < 0 or shard >= SHARD_N:
        raise OracleError("invalid shard")
    out.mkdir(parents=True, exist_ok=True)
    cohort = load_base_cohort(base)
    sm = json.load(open(source / "oracle_source_manifest.json"))
    if sm.get("cohort_identity_sha256") != COHORT_SHA or sm.get("external_api_key_used") or sm.get("provider_secret_used"):
        raise OracleError("Oracle merged source governance mismatch")
    packets = readjl(source / "oracle_lineup_packets.jsonl")
    if len(packets) != COHORT_N:
        raise OracleError("Oracle source n mismatch")
    pm = json.load(open(prior / "merged_prediction_manifest.json"))
    if pm.get("cohort_identity_sha256") != COHORT_SHA or pm.get("labels_read") != 0 or pm.get("scorer_invoked") is not False:
        raise OracleError("prior pre-score Artifact is not label-free frozen cohort")
    prior_rows = readjl(prior / "historical_pit_predictions.jsonl")
    prior_map = {str(x["fixture_id"]): x for x in prior_rows}
    if len(prior_map) != COHORT_N:
        raise OracleError("prior prediction map n mismatch")
    stm = json.load(open(state_root / "source_freeze_manifest.json"))
    cp = stm.get("checkpoint_resume") or {}
    if cp.get("fixture_state_sha_checked_n") != SHARD_SIZE or cp.get("fixture_state_sha_matched_n") != SHARD_SIZE or cp.get("fixture_state_sha_mismatch_n") != 0:
        raise OracleError("repaired state checkpoint SHA gate failed")
    states = readjl(state_root / "offline_state_snapshots.jsonl")
    start, end = shard * SHARD_SIZE, (shard + 1) * SHARD_SIZE
    expected = cohort[start:end]
    expected_ids = [str(x["fixture_id"]) for x in expected]
    if [str(x["fixture_id"]) for x in states] != expected_ids:
        raise OracleError("state shard fixture identity/order mismatch")
    state_map = {str(x["fixture_id"]): x for x in states}
    packet_map = {str(x["fixture_id"]): x for x in packets}
    lock = json.load(open(base / "v2_lock.json"))
    eng = cbd.engine()
    rows: list[dict[str, Any]] = []
    active = Counter()
    identity_counts = Counter()
    context_counts = Counter()
    for i, r in enumerate(expected, 1):
        fid = str(r["fixture_id"])
        packet = packet_map[fid]
        snapshot = state_map[fid]
        if core.canon({"vectors": snapshot["vectors"], "usage": snapshot["usage"]}) != snapshot["state_sha256"]:
            raise OracleError(f"state SHA changed before Oracle prediction {fid}")
        registry, _ = build_registry(snapshot)
        home_xi, hc = mapped_lineup(packet, "home", str(r["home_team_id"]), registry, "starting_xi")
        away_xi, ac = mapped_lineup(packet, "away", str(r["away_team_id"]), registry, "starting_xi")
        home_bench, hb = mapped_lineup(packet, "home", str(r["home_team_id"]), registry, "bench")
        away_bench, ab = mapped_lineup(packet, "away", str(r["away_team_id"]), registry, "bench")
        identity_counts.update(hc); identity_counts.update(ac); identity_counts.update(hb); identity_counts.update(ab)
        if len(home_xi) != 11 or len(away_xi) != 11:
            raise OracleError("sanitized Oracle XI no longer 11")
        for side_name, lineup in (("home", home_xi), ("away", away_xi)):
            ids = [x["player_id"] for x in lineup]
            if len(set(ids)) != len(ids):
                raise OracleError(f"Oracle player identity collision in target {side_name} XI {fid}")
        prev = prior_map[fid]
        base_pred = prev["protected_v2"]
        no_lineup = prev["candidate_c_historical"]
        vectors = _load_vectors(snapshot)
        usage = snapshot.get("usage") or {}
        cutoff = str(r["prediction_cutoff_utc"])
        xi_effect = c3_confirmed_xi(
            vectors=vectors, usage=usage, home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]),
            confirmed_lineups={"home": home_xi, "away": away_xi}, cutoff=cutoff,
        )
        bench_effect = c4_bench(
            vectors=vectors, home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]),
            bench={"home": home_bench, "away": away_bench}, evidence_uncertainty=UNCERTAINTY_BY_GRADE["CONFIRMED_LINEUP_PIT"],
        )
        xi_bench_effect = combine_effects([e for e in (xi_effect, bench_effect) if e.active], grade="CONFIRMED_LINEUP_PIT")
        full_lineup = xi_effect
        context_mode = "GENERIC_ROLLING_REFERENCE"
        ctx_usage, ctx_n = prior_context_usage(packets, packet, cutoff, registry, "coach_formation")
        ctx_effect = lineup_residual_component(
            component="ORACLE_XI_COACH_FORMATION", vectors=vectors, usage=ctx_usage,
            home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]),
            home_player_ids=[x["player_id"] for x in home_xi], away_player_ids=[x["player_id"] for x in away_xi],
            cutoff=cutoff, evidence_uncertainty=UNCERTAINTY_BY_GRADE["CONFIRMED_LINEUP_PIT"],
        )
        if ctx_effect.active:
            full_lineup = ctx_effect
            context_mode = "SAME_COACH_AND_FORMATION_REFERENCE"
        else:
            coach_usage, coach_n = prior_context_usage(packets, packet, cutoff, registry, "coach")
            coach_effect = lineup_residual_component(
                component="ORACLE_XI_COACH", vectors=vectors, usage=coach_usage,
                home_team_id=str(r["home_team_id"]), away_team_id=str(r["away_team_id"]),
                home_player_ids=[x["player_id"] for x in home_xi], away_player_ids=[x["player_id"] for x in away_xi],
                cutoff=cutoff, evidence_uncertainty=UNCERTAINTY_BY_GRADE["CONFIRMED_LINEUP_PIT"],
            )
            if coach_effect.active:
                full_lineup = coach_effect
                context_mode = "SAME_COACH_REFERENCE"
                ctx_n = coach_n
        context_counts[context_mode] += 1
        full_effect = combine_effects([e for e in (full_lineup, bench_effect) if e.active], grade="CONFIRMED_LINEUP_PIT")
        p_xi = _effect_prediction(base_pred, xi_effect, lock, eng)
        p_xib = _effect_prediction(base_pred, xi_bench_effect, lock, eng)
        p_full = _effect_prediction(base_pred, full_effect, lock, eng)
        active["candidate_c_no_lineup"] += int(bool(prev["candidate_c_historical_effect"]["active"]))
        active["oracle_confirmed_xi"] += int(xi_effect.active)
        active["oracle_confirmed_xi_bench"] += int(xi_bench_effect.active)
        active["oracle_full"] += int(full_effect.active)
        rows.append({
            "fixture_id": fid, "kickoff_utc": r["kickoff_utc"], "cutoff_utc": cutoff,
            "home_team_id": r["home_team_id"], "away_team_id": r["away_team_id"],
            "home_team": r["home_team"], "away_team": r["away_team"],
            "round_index": r.get("round_index"), "shared_cold_start_bucket": r.get("shared_cold_start_bucket"),
            "research_tags": list(TAGS),
            "protected_v2": base_pred, "candidate_c_no_lineup": no_lineup,
            "oracle_confirmed_xi": p_xi, "oracle_confirmed_xi_bench": p_xib, "oracle_full": p_full,
            "effects": {
                "oracle_confirmed_xi": xi_effect.to_dict(),
                "oracle_bench": bench_effect.to_dict(),
                "oracle_confirmed_xi_bench": xi_bench_effect.to_dict(),
                "oracle_full": full_effect.to_dict(),
            },
            "formation_coach_context": {"mode": context_mode, "reference_n_home": ctx_n["home"], "reference_n_away": ctx_n["away"]},
            "source_packet_sha256": canon(packet), "state_sha256": snapshot["state_sha256"],
            "target_result_fields_read": False, "target_event_fields_read": False, "target_postmatch_stats_read": False,
        })
        if i % 10 == 0:
            print(f"[oracle-predict shard={shard}] {i}/50 fixture={fid} xi={int(xi_effect.active)} full={int(full_effect.active)} context={context_mode}", flush=True)
    if [str(x["fixture_id"]) for x in rows] != expected_ids:
        raise OracleError("Oracle prediction shard order mismatch")
    writejl(out / "oracle_predictions.jsonl", rows)
    names = ["oracle_predictions.jsonl"]
    manifest = {
        "schema_version": "football3-lineup-oracle-prediction-shard-v1",
        "status": "HISTORICAL_LINEUP_ORACLE_PREDICTION_SHARD_FROZEN", "tags": list(TAGS),
        "shard": shard, "start": start, "end_exclusive": end, "n": SHARD_SIZE, "fixture_ids": expected_ids,
        "cohort_identity_sha256": COHORT_SHA, "source_packet_sha256": sm["packet_sha256"],
        "state_snapshot_sha256": stm["state_snapshot_sha256"],
        "state_sha_checked_n": SHARD_SIZE, "state_sha_mismatch_n": 0,
        "prediction_sha256": sha_file(out / "oracle_predictions.jsonl"),
        "model_activation_n": {m: active[m] for m in MODELS if m != "protected_v2"},
        "model_fallback_n": {m: SHARD_SIZE - active[m] for m in MODELS if m != "protected_v2"},
        "identity_mapping_counts": dict(identity_counts), "formation_coach_context_counts": dict(context_counts),
        "labels_read": 0, "scorer_invoked": False, "external_network_requests": 0,
        "target_match_result_score_read": False, "target_match_event_stats_read": False,
        "actual_substitution_or_substitution_time_read": False,
        "payload": exact_files(out, names),
    }
    dump(out / "oracle_prediction_shard_manifest.json", manifest)
    return manifest


def merge_predictions(base: pathlib.Path, prior: pathlib.Path, source: pathlib.Path, predictions: pathlib.Path, out: pathlib.Path, *, head: str, parent: str, code_paths: list[pathlib.Path]) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    cohort = load_base_cohort(base)
    sm = json.load(open(source / "oracle_source_manifest.json"))
    if sm.get("cohort_identity_sha256") != COHORT_SHA:
        raise OracleError("source cohort SHA mismatch at prediction merge")
    found = discover(predictions, "oracle_prediction_shard_manifest.json")
    by_shard = {int(m["shard"]): (r, m) for r, m in found}
    if set(by_shard) != set(range(SHARD_N)) or len(found) != SHARD_N:
        raise OracleError("prediction shard set mismatch")
    all_rows: list[dict[str, Any]] = []
    receipts = []
    active = Counter()
    fallback = Counter()
    identity_counts = Counter()
    context_counts = Counter()
    for shard in range(SHARD_N):
        root, m = by_shard[shard]
        if m.get("cohort_identity_sha256") != COHORT_SHA or m.get("labels_read") != 0 or m.get("scorer_invoked") is not False or m.get("external_network_requests") != 0:
            raise OracleError("prediction shard governance mismatch")
        start, end = shard * SHARD_SIZE, (shard + 1) * SHARD_SIZE
        expected = [str(x["fixture_id"]) for x in cohort[start:end]]
        if list(map(str, m["fixture_ids"])) != expected:
            raise OracleError("prediction shard identity mismatch")
        rows = readjl(root / "oracle_predictions.jsonl")
        if [str(x["fixture_id"]) for x in rows] != expected:
            raise OracleError("prediction payload identity/order mismatch")
        all_rows.extend(rows)
        receipts.append({"shard": shard, "manifest_sha256": sha_file(root / "oracle_prediction_shard_manifest.json"), "prediction_sha256": m["prediction_sha256"], "state_snapshot_sha256": m["state_snapshot_sha256"]})
        active.update(m["model_activation_n"]); fallback.update(m["model_fallback_n"]); identity_counts.update(m["identity_mapping_counts"]); context_counts.update(m["formation_coach_context_counts"])
    expected_all = [str(x["fixture_id"]) for x in cohort]
    got = [str(x["fixture_id"]) for x in all_rows]
    if got != expected_all or len(set(got)) != COHORT_N:
        raise OracleError("merged Oracle predictions missing/duplicate/extra/order conflict")
    writejl(out / "oracle_predictions.jsonl", all_rows)
    model_sha: dict[str, str] = {}
    model_files: dict[str, str] = {}
    for model in MODELS:
        name = f"predictions_{model}.jsonl"
        writejl(out / name, [{"fixture_id": r["fixture_id"], "prediction": r[model]} for r in all_rows])
        model_sha[model] = sha_file(out / name)
        model_files[model] = name
    shutil.copy2(base / "cohort_manifest.json", out / "cohort_manifest.json")
    shutil.copy2(source / "oracle_source_manifest.json", out / "oracle_source_manifest.json")
    shutil.copy2(source / "raw_source_receipts.json", out / "raw_source_receipts.json")
    dump(out / "prediction_shard_receipts.json", {"rows": receipts, "rows_sha256": canon(receipts), "count": len(receipts)})
    rules = {
        "schema_version": "football3-lineup-oracle-preregistered-rules-v1",
        "tags": list(TAGS),
        "models": list(MODELS),
        "player_capability_state": "EXACT_REPAIRED_PRE_TARGET_STATE_FROM_PRIOR_300_REPLAY; NO_TARGET_MATCH_UPDATE",
        "same_kickoff_batch": "NO_STATE_UPDATE_IN_ORACLE_PREDICTION; PRIOR_CONTEXT_ACCEPTED_ONLY_IF_PRIOR_KICKOFF_PLUS_3H_STRICTLY_BEFORE_TARGET_CUTOFF",
        "oracle_xi": "EXISTING_CANDIDATE_C_C3_CONFIRMED_XI_RESIDUAL",
        "oracle_xi_bench": "EXISTING_C3_PLUS_EXISTING_C4_BENCH",
        "oracle_full": "SAME_COACH+FORMATION PRIOR ACTUAL XI REFERENCE IF BOTH SIDES HAVE >=3; ELSE SAME_COACH >=3; ELSE EXISTING_GENERIC C3; THEN EXISTING C4 BENCH",
        "unknown_player_capability": "EXISTING_CANDIDATE_C_EMPIRICAL_TEAM_REFERENCE_SHRINK_WITH_UNCERTAINTY; NO DEFAULT STRENGTH",
        "minimum_active_n": MIN_ACTIVE_N,
        "minimum_mean_logloss_improvement": MIN_LL_IMPROVEMENT,
        "decision_requires_logloss_ci95_high_lt_zero": True,
        "decision_requires_brier_ci95_high_lt_zero": True,
        "decision_requires_rps_mean_delta_lte_zero": True,
        "top1_delta_floor": -0.005,
        "bootstrap_n": BOOTSTRAP_N, "bootstrap_seed": BOOTSTRAP_SEED,
        "no_post_score_tuning": True,
    }
    rules["rules_sha256"] = canon(rules)
    dump(out / "oracle_rules.json", rules)
    code_sha = {str(p): sha_file(p) for p in code_paths}
    dump(out / "model_rule_sha_receipt.json", {"files": code_sha, "files_sha256": canon(code_sha), "oracle_rules_sha256": sha_file(out / "oracle_rules.json")})
    names = ["oracle_predictions.jsonl", "cohort_manifest.json", "oracle_source_manifest.json", "raw_source_receipts.json",
             "prediction_shard_receipts.json", "oracle_rules.json", "model_rule_sha_receipt.json"] + list(model_files.values())
    pre = {
        "schema_version": "football3-lineup-oracle-pre-score-v1",
        "status": "HISTORICAL_LINEUP_ORACLE_PREDICTIONS_FROZEN", "tags": list(TAGS),
        "branch": "football3/context-translator-lineup-oracle-v1", "base_head": BASE_HEAD,
        "head": head, "parent": parent, "n": COHORT_N, "cohort_identity_sha256": COHORT_SHA,
        "source_manifest_sha256": sha_file(out / "oracle_source_manifest.json"),
        "source_packet_sha256": sm["packet_sha256"], "raw_source_aggregate_sha256": sm["raw_source_aggregate_sha256"],
        "model_rule_sha_receipt_sha256": sha_file(out / "model_rule_sha_receipt.json"),
        "model_prediction_sha256": model_sha,
        "combined_prediction_sha256": sha_file(out / "oracle_predictions.jsonl"),
        "model_activation_n": {"protected_v2": COHORT_N, **{m: active[m] for m in MODELS if m != "protected_v2"}},
        "model_fallback_n": {"protected_v2": 0, **{m: fallback[m] for m in MODELS if m != "protected_v2"}},
        "identity_mapping_counts": dict(identity_counts), "formation_coach_context_counts": dict(context_counts),
        "labels_read_in_prediction_phase": False, "scorer_invoked": False, "new_or_target_labels_read_n": 0,
        "external_data_api_key_used": False, "provider_secret_used": False,
        "target_result_score_read": False, "target_events_stats_ratings_read": False,
        "actual_substitution_or_time_read": False,
        "formal_weight": 0, "formal_promotion_eligible": False, "formal_enablement": False,
        "payload": exact_files(out, names),
    }
    dump(out / "oracle_pre_score_manifest.json", pre)
    return pre


def outcome(label: dict[str, Any]) -> str:
    h, a = int(label["home_goals"]), int(label["away_goals"])
    return "home" if h > a else "draw" if h == a else "away"


def probs(p: dict[str, Any]) -> dict[str, float]:
    z = {"home": float(p["p_home"]), "draw": float(p["p_draw"]), "away": float(p["p_away"])}
    if min(z.values()) < 0 or abs(sum(z.values()) - 1.0) > 1e-8:
        raise OracleError("invalid probability vector")
    return z


def allowed_labels(path: pathlib.Path, allowed: set[str]) -> dict[str, Any]:
    rx = re.compile(r'"fixture_id"\s*:\s*"([^"]+)"')
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            m = rx.search(line)
            if not m:
                raise OracleError("label row missing fixture_id")
            fid = m.group(1)
            if fid not in allowed:
                continue
            z = json.loads(line)
            out[fid] = {"fixture_id": fid, "home_goals": z["home_goals"], "away_goals": z["away_goals"]}
    if set(out) != allowed:
        raise OracleError(f"historical label whitelist incomplete {len(out)}/{len(allowed)}")
    return out


def metric_rows(rows: list[dict[str, Any]], labels: dict[str, Any], model: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "logloss": None, "brier": None, "rps": None, "top1": None}
    ll = br = rp = top = 0.0
    for r in rows:
        p = probs(r[model])
        y = outcome(labels[r["fixture_id"]])
        ll += -math.log(max(1e-15, p[y]))
        br += sum((p[k] - (1.0 if y == k else 0.0)) ** 2 for k in OUTCOMES)
        rp += ((p["home"] - (1.0 if y == "home" else 0.0)) ** 2 + ((p["home"] + p["draw"]) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2) / 2.0
        top += max(OUTCOMES, key=lambda k: p[k]) == y
    n = len(rows)
    return {"n": n, "logloss": ll / n, "brier": br / n, "rps": rp / n, "top1": top / n}


def loss_vector(rows: list[dict[str, Any]], labels: dict[str, Any], model: str, metric: str) -> list[float]:
    out = []
    for r in rows:
        p = probs(r[model]); y = outcome(labels[r["fixture_id"]])
        if metric == "logloss":
            out.append(-math.log(max(1e-15, p[y])))
        elif metric == "brier":
            out.append(sum((p[k] - (1.0 if y == k else 0.0)) ** 2 for k in OUTCOMES))
        elif metric == "rps":
            out.append(((p["home"] - (1.0 if y == "home" else 0.0)) ** 2 + ((p["home"] + p["draw"]) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2) / 2.0)
        else:
            raise OracleError("unknown bootstrap metric")
    return out


def percentile(xs: list[float], q: float) -> float:
    ys = sorted(xs); pos = (len(ys) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi: return ys[lo]
    return ys[lo] * (hi - pos) + ys[hi] * (pos - lo)


def bootstrap_delta(rows: list[dict[str, Any]], labels: dict[str, Any], candidate: str, metric: str) -> dict[str, Any]:
    a = loss_vector(rows, labels, candidate, metric)
    b = loss_vector(rows, labels, "protected_v2", metric)
    d = [x - y for x, y in zip(a, b)]
    rng = random.Random(BOOTSTRAP_SEED + {"logloss": 11, "brier": 23, "rps": 37}[metric])
    vals = [sum(d[rng.randrange(len(d))] for _ in range(len(d))) / len(d) for _ in range(BOOTSTRAP_N)]
    return {"mean_delta": statistics.fmean(d), "ci95_low": percentile(vals, 0.025), "ci95_high": percentile(vals, 0.975), "bootstrap_n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED}


def score_final(frozen: pathlib.Path, label_vault: pathlib.Path, out: pathlib.Path, *, head: str, parent: str, run_id: int, changed_paths: list[str]) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    pre = json.load(open(frozen / "oracle_pre_score_manifest.json"))
    if pre.get("head") != head or pre.get("parent") != parent or pre.get("cohort_identity_sha256") != COHORT_SHA:
        raise OracleError("pre-score exact HEAD/parent/cohort freeze mismatch")
    if pre.get("labels_read_in_prediction_phase") or pre.get("scorer_invoked") is not False:
        raise OracleError("predictor/scorer separation violated")
    for name, meta in pre["payload"].items():
        src = frozen / name
        if sha_file(src) != meta["sha256"] or src.stat().st_size != meta["bytes"]:
            raise OracleError(f"frozen payload changed before score: {name}")
        shutil.copy2(src, out / name)
    shutil.copy2(frozen / "oracle_pre_score_manifest.json", out / "oracle_pre_score_manifest.json")
    rows = readjl(out / "oracle_predictions.jsonl")
    if len(rows) != COHORT_N:
        raise OracleError("scorer cohort n mismatch")
    ids = {str(r["fixture_id"]) for r in rows}
    labels = allowed_labels(label_vault, ids)
    overall = {m: metric_rows(rows, labels, m) for m in MODELS}
    def subgroup(name: str, subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {"group": name, "n": len(subset), "models": {m: metric_rows(subset, labels, m) for m in MODELS}}
    groups = {
        "actual_draw": subgroup("actual_draw", [r for r in rows if outcome(labels[r["fixture_id"]]) == "draw"]),
        "actual_home": subgroup("actual_home", [r for r in rows if outcome(labels[r["fixture_id"]]) == "home"]),
        "actual_away": subgroup("actual_away", [r for r in rows if outcome(labels[r["fixture_id"]]) == "away"]),
        "weak_team_win": subgroup("weak_team_win", [r for r in rows if outcome(labels[r["fixture_id"]]) in {"home", "away"} and outcome(labels[r["fixture_id"]]) == ("home" if r["protected_v2"]["p_home"] < r["protected_v2"]["p_away"] else "away")]),
    }
    for bucket in sorted({str(r.get("shared_cold_start_bucket")) for r in rows}):
        groups["cold_start_" + bucket] = subgroup("cold_start_" + bucket, [r for r in rows if str(r.get("shared_cold_start_bucket")) == bucket])
    team_counts = Counter()
    for r in rows:
        team_counts[str(r["home_team"])] += 1; team_counts[str(r["away_team"])] += 1
    for team, n in sorted(team_counts.items(), key=lambda x: (-x[1], x[0])):
        if n >= 20:
            groups["team_" + norm(team).replace(" ", "_")] = subgroup(team, [r for r in rows if team in {r["home_team"], r["away_team"]}])
    for model in MODELS[1:]:
        groups["active_" + model] = subgroup("active_" + model, [r for r in rows if bool((r.get("effects") or {}).get(model, {}).get("active"))] if model.startswith("oracle_") else [])
    bootstrap = {metric: bootstrap_delta(rows, labels, "oracle_full", metric) for metric in ("logloss", "brier", "rps")}
    base = overall["protected_v2"]; full = overall["oracle_full"]
    top_delta = full["top1"] - base["top1"]
    active_n = int(pre["model_activation_n"]["oracle_full"])
    found = (
        active_n >= MIN_ACTIVE_N
        and bootstrap["logloss"]["mean_delta"] <= -MIN_LL_IMPROVEMENT
        and bootstrap["logloss"]["ci95_high"] < 0
        and bootstrap["brier"]["ci95_high"] < 0
        and bootstrap["rps"]["mean_delta"] <= 0
        and top_delta >= -0.005
    )
    status = "ORACLE_INCREMENT_FOUND_DATA_ACQUISITION_REQUIRED" if found else "ORACLE_NO_INCREMENT_STOP_TRANSLATOR"
    score = {
        "schema_version": "football3-lineup-oracle-score-v1", "status": status, "tags": list(TAGS),
        "scientific_claim": "UPPER_BOUND_ONLY_ACTUAL_HISTORICAL_LINEUP_NOT_STRICT_PIT_NOT_PROMOTION_ELIGIBLE",
        "n": COHORT_N, "cohort_identity_sha256": COHORT_SHA,
        "models": overall, "subgroups": groups,
        "activation_n": pre["model_activation_n"], "fallback_n": pre["model_fallback_n"],
        "formation_coach_context_counts": pre["formation_coach_context_counts"],
        "bootstrap_oracle_full_vs_protected_v2": bootstrap,
        "decision_rule_preregistered": json.load(open(out / "oracle_rules.json")),
        "stable_non_micro_improvement": found,
        "labels_read_only_after_all_model_prediction_sha_freeze": True,
        "historical_selected_cohort_labels_scored_n": COHORT_N,
        "post_score_parameter_or_structure_change": False,
        "formal_weight": 0, "formal_promotion_eligible": False, "formal_enablement": False,
    }
    dump(out / "oracle_score.json", score)
    dump(out / "label_access_receipt.json", {
        "schema_version": "football3-lineup-oracle-label-access-v1",
        "prediction_phase_label_reads": 0, "historical_selected_cohort_labels_scored_after_total_prediction_freeze_n": COHORT_N,
        "selection_changed_after_label_access": False, "scoring_only": True, "scorer_network_requests": 0,
    })
    dump(out / "oracle_gate.json", {
        "schema_version": "football3-lineup-oracle-gate-v1", "status": status, "terminal": True,
        "pipeline_integrity": "PASS", "scientific_scope": list(TAGS), "formal_promotion_eligible": False,
    })
    payload_names = list(pre["payload"]) + ["oracle_pre_score_manifest.json", "oracle_score.json", "label_access_receipt.json", "oracle_gate.json"]
    manifest = {
        "schema_version": "football3-lineup-oracle-artifact-v1", "status": status, "tags": list(TAGS),
        "branch": "football3/context-translator-lineup-oracle-v1", "base_head": BASE_HEAD,
        "head": head, "parent": parent, "run_id": int(run_id), "n": COHORT_N, "cohort_identity_sha256": COHORT_SHA,
        "source_packet_sha256": pre["source_packet_sha256"], "raw_source_aggregate_sha256": pre["raw_source_aggregate_sha256"],
        "model_prediction_sha256": pre["model_prediction_sha256"], "model_rule_sha_receipt_sha256": pre["model_rule_sha_receipt_sha256"],
        "prediction_phase_label_reads": 0, "scorer_network_requests": 0,
        "external_data_api_key_used": False, "provider_secret_used": False,
        "protected_v2_modified": False, "main_modified": False, "current_modified": False, "airtable_modified": False,
        "pr334_modified": False, "r5_modified": False, "formal_weight": 0, "formal_promotion_eligible": False,
        "formal_enablement": False, "merge_used": False, "ready_used": False, "force_used": False,
        "changed_paths": sorted(changed_paths),
        "payload": exact_files(out, payload_names),
    }
    dump(out / "artifact_manifest.json", manifest)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("collect-index"); x.add_argument("--base", type=pathlib.Path, required=True); x.add_argument("--out", type=pathlib.Path, required=True)
    x = sp.add_parser("collect-lineup-shard"); x.add_argument("--base", type=pathlib.Path, required=True); x.add_argument("--index", type=pathlib.Path, required=True); x.add_argument("--shard", type=int, required=True); x.add_argument("--out", type=pathlib.Path, required=True)
    x = sp.add_parser("merge-source"); x.add_argument("--base", type=pathlib.Path, required=True); x.add_argument("--shards", type=pathlib.Path, required=True); x.add_argument("--out", type=pathlib.Path, required=True)
    x = sp.add_parser("predict-shard"); x.add_argument("--base", type=pathlib.Path, required=True); x.add_argument("--prior", type=pathlib.Path, required=True); x.add_argument("--source", type=pathlib.Path, required=True); x.add_argument("--state", type=pathlib.Path, required=True); x.add_argument("--shard", type=int, required=True); x.add_argument("--out", type=pathlib.Path, required=True)
    x = sp.add_parser("merge-predictions"); x.add_argument("--base", type=pathlib.Path, required=True); x.add_argument("--prior", type=pathlib.Path, required=True); x.add_argument("--source", type=pathlib.Path, required=True); x.add_argument("--predictions", type=pathlib.Path, required=True); x.add_argument("--out", type=pathlib.Path, required=True); x.add_argument("--head", required=True); x.add_argument("--parent", required=True); x.add_argument("--code-path", action="append", default=[])
    x = sp.add_parser("score-final"); x.add_argument("--frozen", type=pathlib.Path, required=True); x.add_argument("--label-vault", type=pathlib.Path, required=True); x.add_argument("--out", type=pathlib.Path, required=True); x.add_argument("--head", required=True); x.add_argument("--parent", required=True); x.add_argument("--run-id", type=int, required=True); x.add_argument("--changed-paths", type=pathlib.Path, required=True)
    a = ap.parse_args()
    if a.cmd == "collect-index":
        result = collect_index(a.base, a.out)
    elif a.cmd == "collect-lineup-shard":
        result = collect_lineup_shard(a.base, a.index, a.shard, a.out)
    elif a.cmd == "merge-source":
        result = merge_source(a.base, a.shards, a.out)
    elif a.cmd == "predict-shard":
        result = predict_shard(a.base, a.prior, a.source, a.state, a.shard, a.out)
    elif a.cmd == "merge-predictions":
        result = merge_predictions(a.base, a.prior, a.source, a.predictions, a.out, head=a.head, parent=a.parent, code_paths=[pathlib.Path(x) for x in a.code_path])
    else:
        changed = [x for x in a.changed_paths.read_text(encoding="utf-8").splitlines() if x]
        result = score_final(a.frozen, a.label_vault, a.out, head=a.head, parent=a.parent, run_id=a.run_id, changed_paths=changed)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
