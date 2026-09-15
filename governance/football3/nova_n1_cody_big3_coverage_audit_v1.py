#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import math
import pathlib
import re
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

import nova_n1_smartplay_epl_coverage_audit_v1 as common

SCHEMA_VERSION = "football3-nova-n1-cody-big3-coverage-audit-v1"
PROVIDER = "Cody Tipton - Player stats per game - Understat"
SOURCE_PAGE = "https://www.kaggle.com/datasets/codytipton/player-stats-per-game-understat"
DOWNLOAD_URL = "https://www.kaggle.com/api/v1/datasets/download/codytipton/player-stats-per-game-understat"
DECLARED_LICENSE = "MIT"
EXPECTED_ARCHIVE_SHA256 = "2d77fa250bdac756bdb15cc14bb1b6c2e3a8f925acd32f120318fb015245c4c0"
DATA_MEMBER = "general_game_stats.csv"
LOCKED_TEST_ARTIFACT_ID = common.LOCKED_TEST_ARTIFACT_ID
LOCKED_TEST_IDENTITY_SHA256 = common.LOCKED_TEST_IDENTITY_SHA256
LOCKED_TEST_FIXTURE_SET_SHA256 = common.LOCKED_TEST_FIXTURE_SET_SHA256
TARGET_SEASON = 2024
TARGETS = {"Bundesliga": 306, "Serie A": 380, "Ligue 1": 306}
TARGET_TOTAL = sum(TARGETS.values())
IDENTITY_MATCH_CONTRACT = "unique calendar-date + canonical home/away teams; no source timezone inferred; locked target kickoff retained"

SAFE_COLUMNS = (
    "date", "season", "team_h", "team_a", "league", "h_deep", "a_deep", "h_ppda", "a_ppda"
)
FORBIDDEN_COLUMNS = {
    "h_goals", "a_goals", "h_xg", "a_xg", "h_w", "h_d", "h_l",
    "h_shot", "a_shot", "h_shotontarget", "a_shotontarget",
}
LEAGUE_ALIASES = {
    "bundesliga": "Bundesliga",
    "seriea": "Serie A",
    "ligue1": "Ligue 1",
}
UA = {
    "User-Agent": "Football3-Nova-N1-Cody-Big3-coverage/1.0",
    "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.1",
    "Accept-Encoding": "identity",
}


class CoverageAuditError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_date(value: str) -> str:
    s = str(value).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T].*)?$", s)
    if not m:
        raise CoverageAuditError(f"invalid source date: {value!r}")
    return m.group(1)


def target_date(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoverageAuditError(f"invalid target kickoff: {value!r}") from exc
    if dt.tzinfo is None:
        raise CoverageAuditError(f"target kickoff lacks timezone: {value!r}")
    return dt.astimezone(timezone.utc).date().isoformat()


def parse_season(value: str) -> int:
    s = str(value).strip()
    try:
        x = float(s)
    except ValueError as exc:
        raise CoverageAuditError(f"invalid season: {value!r}") from exc
    if not math.isfinite(x) or int(x) != x:
        raise CoverageAuditError(f"non-integral season: {value!r}")
    return int(x)


def canonical_league(value: str) -> str | None:
    key = common.norm_team(value)
    return LEAGUE_ALIASES.get(key)


def finite_nonnegative(name: str, value: str) -> float:
    try:
        x = float(str(value).strip())
    except ValueError as exc:
        raise CoverageAuditError(f"invalid {name}: {value!r}") from exc
    if not math.isfinite(x) or x < 0:
        raise CoverageAuditError(f"invalid {name}: {value!r}")
    return x


def ppda_value(name: str, value: str) -> float:
    s = str(value).strip()
    try:
        x = float(s)
    except ValueError:
        try:
            parsed = ast.literal_eval(s)
        except (ValueError, SyntaxError) as exc:
            raise CoverageAuditError(f"invalid {name}: {value!r}") from exc
        if not isinstance(parsed, dict):
            raise CoverageAuditError(f"invalid {name} structure: {value!r}")
        try:
            att = float(parsed["att"])
            deff = float(parsed["def"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CoverageAuditError(f"invalid {name} structure: {value!r}") from exc
        if not math.isfinite(att) or not math.isfinite(deff) or att < 0 or deff <= 0:
            raise CoverageAuditError(f"invalid {name} components: {value!r}")
        x = att / deff
    if not math.isfinite(x) or x <= 0:
        raise CoverageAuditError(f"non-positive {name}: {value!r}")
    return x


def load_locked_identity(path: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_bytes()
    got = sha256_bytes(raw)
    if got != LOCKED_TEST_IDENTITY_SHA256:
        raise CoverageAuditError(f"locked identity sha mismatch: {got} != {LOCKED_TEST_IDENTITY_SHA256}")
    rows = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
    forbidden = {"home_goals", "away_goals", "result", "winner", "home_xg", "away_xg", "h_goals", "a_goals"}
    for i, row in enumerate(rows):
        if forbidden & {str(k).casefold() for k in row}:
            raise CoverageAuditError(f"result-like field found in locked identity row {i}")
    target = [r for r in rows if r.get("league") in TARGETS and int(r.get("season")) == TARGET_SEASON]
    counts = Counter(str(r["league"]) for r in target)
    if dict(counts) != TARGETS:
        raise CoverageAuditError(f"locked Big3 counts mismatch: {dict(counts)} != {TARGETS}")
    team_maps: dict[str, dict[str, str]] = {league: {} for league in TARGETS}
    seen: set[tuple[str, str, str, str]] = set()
    for r in target:
        league = str(r["league"])
        for field in ("home_team", "away_team"):
            canonical = str(r[field])
            key = common.norm_team(canonical)
            prior = team_maps[league].get(key)
            if prior is not None and prior != canonical:
                raise CoverageAuditError(f"ambiguous target team normalization {league}: {key!r}")
            team_maps[league][key] = canonical
        ident = (league, target_date(str(r["kickoff"])), str(r["home_team"]), str(r["away_team"]))
        if ident in seen:
            raise CoverageAuditError(f"duplicate locked target date/team identity: {ident}")
        seen.add(ident)
    return target, {
        "artifact_id": LOCKED_TEST_ARTIFACT_ID,
        "identity_sha256": got,
        "fixture_set_sha256": LOCKED_TEST_FIXTURE_SET_SHA256,
        "target_season": TARGET_SEASON,
        "target_league_counts": dict(counts),
        "target_n": len(target),
        "team_normalization_maps": {league: dict(sorted(m.items())) for league, m in team_maps.items()},
        "test_result_vault_opened": False,
        "test_labels_read": False,
    }


def download_archive() -> tuple[bytes, dict[str, Any]]:
    req = urllib.request.Request(DOWNLOAD_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
        meta = {
            "requested_url": DOWNLOAD_URL,
            "final_url": resp.geturl(),
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type"),
            "bytes": len(raw),
            "retrieved_at": now(),
        }
    got = sha256_bytes(raw)
    meta["archive_sha256"] = got
    meta["expected_archive_sha256"] = EXPECTED_ARCHIVE_SHA256
    if got != EXPECTED_ARCHIVE_SHA256:
        raise CoverageAuditError(f"Cody archive sha drift: {got} != {EXPECTED_ARCHIVE_SHA256}")
    return raw, meta


def fetch_and_project(target: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_rows = list(target)
    team_maps: dict[str, dict[str, str]] = {league: {} for league in TARGETS}
    for r in target_rows:
        league = str(r["league"])
        for field in ("home_team", "away_team"):
            canonical = str(r[field])
            team_maps[league][common.norm_team(canonical)] = canonical

    raw, source_meta = download_archive()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise CoverageAuditError("Cody archive is not a ZIP") from exc
    names = set(zf.namelist())
    if DATA_MEMBER not in names:
        raise CoverageAuditError(f"missing archive member: {DATA_MEMBER}")

    rows: list[dict[str, Any]] = []
    physical_rows = 0
    safe_target_rows = 0
    skipped_other_league_or_season = 0
    unknown_teams: Counter[tuple[str, str]] = Counter()
    source_counts: Counter[str] = Counter()
    exact_header: list[str] | None = None
    member_sha = hashlib.sha256()

    with zf.open(DATA_MEMBER) as f:
        header_line = f.readline()
        member_sha.update(header_line)
        raw_header = common.split_csv_record_raw(header_line)
        exact_header = [common.decode_safe(x).lstrip("\ufeff") for x in raw_header]
        normalized = [x.casefold() for x in exact_header]
        if len(set(normalized)) != len(normalized):
            raise CoverageAuditError("duplicate general_game_stats header")
        positions = {name: i for i, name in enumerate(normalized)}
        missing = [x for x in SAFE_COLUMNS if x.casefold() not in positions]
        if missing:
            raise CoverageAuditError(f"safe columns missing: {missing}")
        if {x.casefold() for x in SAFE_COLUMNS} & {x.casefold() for x in FORBIDDEN_COLUMNS}:
            raise CoverageAuditError("safe column contract intersects forbidden columns")
        safe_indices = {name: positions[name.casefold()] for name in SAFE_COLUMNS}
        for line in f:
            physical_rows += 1
            member_sha.update(line)
            if not line.strip():
                continue
            fields = common.split_csv_record_raw(line)
            if len(fields) != len(exact_header):
                raise CoverageAuditError(f"CSV field count drift row={physical_rows}: {len(fields)} != {len(exact_header)}")
            safe = {name: common.decode_safe(fields[idx]) for name, idx in safe_indices.items()}
            league = canonical_league(safe["league"])
            season = parse_season(safe["season"])
            if league not in TARGETS or season != TARGET_SEASON:
                skipped_other_league_or_season += 1
                continue
            safe_target_rows += 1
            home_key = common.norm_team(safe["team_h"])
            away_key = common.norm_team(safe["team_a"])
            home = team_maps[league].get(home_key)
            away = team_maps[league].get(away_key)
            if home is None:
                unknown_teams[(league, safe["team_h"])] += 1
            if away is None:
                unknown_teams[(league, safe["team_a"])] += 1
            if home is None or away is None:
                continue
            row = {
                "date": source_date(safe["date"]),
                "league": league,
                "season": season,
                "home_team": home,
                "away_team": away,
                "h_deep": finite_nonnegative("h_deep", safe["h_deep"]),
                "a_deep": finite_nonnegative("a_deep", safe["a_deep"]),
                "h_ppda": ppda_value("h_ppda", safe["h_ppda"]),
                "a_ppda": ppda_value("a_ppda", safe["a_ppda"]),
            }
            rows.append(row)
            source_counts[league] += 1

    source_meta.update({
        "member": DATA_MEMBER,
        "member_sha256": member_sha.hexdigest(),
        "physical_data_rows_scanned": physical_rows,
        "safe_target_rows_seen": safe_target_rows,
        "skipped_other_league_or_season_rows": skipped_other_league_or_season,
        "safe_rows_projected": len(rows),
        "safe_source_league_counts": dict(source_counts),
        "unknown_team_value_n": sum(unknown_teams.values()),
        "unknown_team_examples": [
            {"league": league, "source_team": team, "row_occurrences": n}
            for (league, team), n in sorted(unknown_teams.items())[:50]
        ],
        "decoded_columns": list(SAFE_COLUMNS),
        "forbidden_value_columns_decoded": 0,
        "result_or_label_values_decoded": 0,
        "xg_or_probability_values_decoded": 0,
        "raw_archive_persisted": False,
        "raw_archive_uploaded": False,
        "archive_member_count": len(names),
    })
    return rows, source_meta


def compare_to_locked_identity(target: Iterable[dict[str, Any]], source: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in target:
        key = (str(r["league"]), target_date(str(r["kickoff"])), str(r["home_team"]), str(r["away_team"]))
        if key in target_map:
            raise CoverageAuditError(f"duplicate target key: {key}")
        target_map[key] = r
    source_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in source:
        key = (str(r["league"]), str(r["date"]), str(r["home_team"]), str(r["away_team"]))
        if key in source_map:
            raise CoverageAuditError(f"duplicate source key: {key}")
        source_map[key] = r

    matched = sorted(set(target_map) & set(source_map))
    missing = sorted(set(target_map) - set(source_map))
    extra = sorted(set(source_map) - set(target_map))
    projection: list[dict[str, Any]] = []
    for key in matched:
        t = target_map[key]
        s = source_map[key]
        projection.append({
            "fixture_id": str(t["fixture_id"]),
            "kickoff": common.parse_kickoff(str(t["kickoff"])),
            "league": key[0],
            "season": TARGET_SEASON,
            "home_team_id": str(t["home_team_id"]),
            "away_team_id": str(t["away_team_id"]),
            "home_team": key[2],
            "away_team": key[3],
            "h_deep": s["h_deep"],
            "a_deep": s["a_deep"],
            "h_ppda": s["h_ppda"],
            "a_ppda": s["a_ppda"],
        })

    per_league: dict[str, Any] = {}
    for league, expected in TARGETS.items():
        tkeys = {k for k in target_map if k[0] == league}
        skeys = {k for k in source_map if k[0] == league}
        mkeys = tkeys & skeys
        miss = tkeys - skeys
        ext = skeys - tkeys
        qualified = len(tkeys) == expected and len(skeys) == expected and len(mkeys) == expected and not miss and not ext
        per_league[league] = {
            "status": "IDENTITY_COVERAGE_QUALIFIED" if qualified else "STOP_DATA_COVERAGE",
            "target_n": len(tkeys),
            "source_n": len(skeys),
            "matched_n": len(mkeys),
            "missing_n": len(miss),
            "extra_n": len(ext),
            "feature_complete_n": sum(1 for x in projection if x["league"] == league),
            "coverage_fraction": len(mkeys) / len(tkeys) if tkeys else 0.0,
            "missing_examples": [list(x) for x in sorted(miss)[:15]],
            "extra_examples": [list(x) for x in sorted(ext)[:15]],
        }
    globally_qualified = all(v["status"] == "IDENTITY_COVERAGE_QUALIFIED" for v in per_league.values()) and len(projection) == TARGET_TOTAL
    return {
        "status": "IDENTITY_COVERAGE_QUALIFIED" if globally_qualified else "STOP_DATA_COVERAGE",
        "identity_match_contract": IDENTITY_MATCH_CONTRACT,
        "target_n": len(target_map),
        "source_n": len(source_map),
        "matched_n": len(matched),
        "missing_n": len(missing),
        "extra_n": len(extra),
        "feature_complete_n": len(projection),
        "coverage_fraction": len(projection) / len(target_map) if target_map else 0.0,
        "per_league": per_league,
        "missing_examples": [list(x) for x in missing[:30]],
        "extra_examples": [list(x) for x in extra[:30]],
    }, projection


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    target, identity_audit = load_locked_identity(args.identity)
    source_rows, source_audit = fetch_and_project(target)
    coverage, projection = compare_to_locked_identity(target, source_rows)
    projection_sha = sha256_bytes(canon(sorted(projection, key=lambda x: x["fixture_id"])))
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": coverage["status"],
        "provider": PROVIDER,
        "source_page": SOURCE_PAGE,
        "download_url": DOWNLOAD_URL,
        "declared_license": DECLARED_LICENSE,
        "license_scope_evidence": "Kaggle dataset page declares MIT for Player stats per game - Understat and documents general_game_stats through 2024/25 with date/team/deep/PPDA columns.",
        "archive_sha256": EXPECTED_ARCHIVE_SHA256,
        "locked_test_identity": identity_audit,
        "source_audit": source_audit,
        "coverage": coverage,
        "safe_feature_projection_sha256": projection_sha,
        "safe_feature_projection_n": len(projection),
        "test_result_vault_opened": False,
        "test_raw_pages_opened": False,
        "test_labels_read": False,
        "external_result_or_label_values_decoded": 0,
        "external_xg_or_probability_values_decoded": 0,
        "scientific_parameters_changed": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "promotion_authorized": False,
        "audited_at": now(),
    }
    out = args.out / "cody_big3_coverage_audit.json"
    out.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if coverage["status"] == "IDENTITY_COVERAGE_QUALIFIED":
        with (args.out / "cody_big3_feature_projection.jsonl").open("w", encoding="utf-8") as f:
            for row in sorted(projection, key=lambda x: x["fixture_id"]):
                f.write(canon(row).decode("utf-8") + "\n")
    print(json.dumps({
        "status": coverage["status"],
        "target_n": coverage["target_n"],
        "source_n": coverage["source_n"],
        "matched_n": coverage["matched_n"],
        "missing_n": coverage["missing_n"],
        "extra_n": coverage["extra_n"],
        "feature_complete_n": coverage["feature_complete_n"],
        "per_league": {k: v["status"] for k, v in coverage["per_league"].items()},
        "test_labels_read": False,
        "external_result_or_label_values_decoded": 0,
        "projection_sha256": projection_sha,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
