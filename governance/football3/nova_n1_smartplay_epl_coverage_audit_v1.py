#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

DATASET_REPO = "Qazybek/smartplay-fpl-dataset"
DATASET_FILE = "smartplay_data.csv"
DOWNLOAD_URL = f"https://huggingface.co/datasets/{DATASET_REPO}/resolve/main/{DATASET_FILE}"
PROVIDER = "SmartPlayFPL / Qazybek"
DECLARED_LICENSE = "CC BY-NC 4.0"
SOURCE_PROJECT_COMMIT = "3f1b252da271cc031ed4de4ff7cbd736e57f5e45"
SOURCE_LICENSE_BLOB_SHA = "09e163799efe14f8e2d4f2a723ed3804ca35dc6e"
SOURCE_DATA_README_BLOB_SHA = "c0bd8917f7ff43985a1e06bdd82f91299e3c2ed9"
SOURCE_DOWNLOAD_HELPER_BLOB_SHA = "775284d08b71c297c2f21d6ca8510e0bff8a48ee"
LOCKED_TEST_ARTIFACT_ID = 9827606506
LOCKED_TEST_IDENTITY_SHA256 = "c861032ea523cb921eebc7455941e2a71ecb2f3ecc3a8df6858eef0b7aaf8d95"
LOCKED_TEST_FIXTURE_SET_SHA256 = "0f156c22df1976b929d0db24135830e8b3fac8546130e7bf5e08673918f22bc6"
TARGET_LEAGUE = "EPL"
TARGET_SEASON = 2024
TARGET_DATASET_SEASON = "2024-25"
TARGET_N = 380
UA = {
    "User-Agent": "Football3-Nova-N1-SmartPlay-EPL-coverage/1.0",
    "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.1",
    "Accept-Encoding": "identity",
}

SAFE_COLUMNS = (
    "season", "fixture", "team_name", "is_home", "match_date", "us_opponent",
    "opponent_team_name", "kickoff_time", "us_ppda", "us_opp_ppda", "us_deep", "us_deep_allowed",
)
FORBIDDEN_COLUMNS = {
    "total_points", "goals_scored", "assists", "clean_sheets", "goals_conceded", "own_goals",
    "penalties_saved", "penalties_missed", "bonus", "bps", "starts", "team_a_score", "team_h_score",
    "us_goals", "us_assists", "us_xg", "us_xa", "us_team_xg", "us_team_xga", "us_team_npxgd",
    "expected_points", "expected_points_pre_deadline", "expected_goals", "expected_assists",
}

ALIASES = {
    "arsenal": "Arsenal",
    "astonvilla": "Aston Villa",
    "bournemouth": "Bournemouth",
    "afcbournemouth": "Bournemouth",
    "brentford": "Brentford",
    "brighton": "Brighton",
    "brightonandhovealbion": "Brighton",
    "chelsea": "Chelsea",
    "crystalpalace": "Crystal Palace",
    "everton": "Everton",
    "fulham": "Fulham",
    "ipswich": "Ipswich",
    "ipswichtown": "Ipswich",
    "leicester": "Leicester",
    "leicestercity": "Leicester",
    "liverpool": "Liverpool",
    "mancity": "Manchester City",
    "manchestercity": "Manchester City",
    "manutd": "Manchester United",
    "manchesterunited": "Manchester United",
    "newcastle": "Newcastle United",
    "newcastleunited": "Newcastle United",
    "nottinghamforest": "Nottingham Forest",
    "nottmforest": "Nottingham Forest",
    "nottinghamforestfc": "Nottingham Forest",
    "southampton": "Southampton",
    "spurs": "Tottenham",
    "tottenham": "Tottenham",
    "tottenhamhotspur": "Tottenham",
    "westham": "West Ham",
    "westhamunited": "West Ham",
    "wolves": "Wolverhampton Wanderers",
    "wolverhamptonwanderers": "Wolverhampton Wanderers",
}


class CoverageAuditError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def norm_team(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def canonical_team(value: str) -> str:
    key = norm_team(value)
    if key not in ALIASES:
        raise CoverageAuditError(f"unmapped EPL team: {value!r} -> {key!r}")
    return ALIASES[key]


def parse_bool(value: str) -> bool:
    s = str(value).strip().casefold()
    if s in {"true", "1", "yes", "y"}:
        return True
    if s in {"false", "0", "no", "n"}:
        return False
    raise CoverageAuditError(f"invalid is_home value: {value!r}")


def parse_kickoff(value: str) -> str:
    s = str(value).strip()
    if not s:
        raise CoverageAuditError("empty kickoff_time")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoverageAuditError(f"invalid kickoff_time: {value!r}") from exc
    if dt.tzinfo is None:
        raise CoverageAuditError(f"kickoff_time lacks timezone: {value!r}")
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def finite_number(name: str, value: str, *, positive: bool = False) -> float | None:
    s = str(value).strip()
    if not s or s.casefold() in {"nan", "none", "null", "na", "n/a"}:
        return None
    try:
        x = float(s)
    except ValueError as exc:
        raise CoverageAuditError(f"invalid {name}: {value!r}") from exc
    if not math.isfinite(x):
        return None
    if positive and x <= 0:
        raise CoverageAuditError(f"non-positive {name}: {value!r}")
    if not positive and x < 0:
        raise CoverageAuditError(f"negative {name}: {value!r}")
    return x


def split_csv_record_raw(line: bytes) -> list[bytes]:
    """Parse one CSV record into raw field byte payloads without decoding values.

    The function supports RFC4180-style quoted fields and doubled quotes. It intentionally
    returns bytes so callers can decode only whitelisted safe columns. Embedded newlines are
    rejected by the caller because the source is consumed one physical line at a time.
    """
    line = line.rstrip(b"\r\n")
    fields: list[bytes] = []
    field = bytearray()
    i = 0
    in_quotes = False
    while i < len(line):
        b = line[i]
        if in_quotes:
            if b == 0x22:
                if i + 1 < len(line) and line[i + 1] == 0x22:
                    field.append(0x22)
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            field.append(b)
            i += 1
            continue
        if b == 0x22 and not field:
            in_quotes = True
            i += 1
            continue
        if b == 0x2C:
            fields.append(bytes(field))
            field.clear()
            i += 1
            continue
        field.append(b)
        i += 1
    if in_quotes:
        raise CoverageAuditError("embedded newline or unterminated quoted CSV field")
    fields.append(bytes(field))
    return fields


def decode_safe(raw: bytes) -> str:
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise CoverageAuditError("safe field is not UTF-8") from exc


def load_locked_identity(path: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_bytes()
    got_sha = sha256_bytes(raw)
    if got_sha != LOCKED_TEST_IDENTITY_SHA256:
        raise CoverageAuditError(f"locked identity sha mismatch: {got_sha} != {LOCKED_TEST_IDENTITY_SHA256}")
    rows = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
    forbidden = {"home_goals", "away_goals", "result", "winner", "home_xg", "away_xg", "h_goals", "a_goals"}
    for i, row in enumerate(rows):
        if forbidden & {str(k).casefold() for k in row}:
            raise CoverageAuditError(f"result-like field found in locked identity row {i}")
    target = [r for r in rows if r.get("league") == TARGET_LEAGUE and int(r.get("season")) == TARGET_SEASON]
    if len(target) != TARGET_N:
        raise CoverageAuditError(f"locked EPL target n mismatch: {len(target)} != {TARGET_N}")
    keys = [(parse_kickoff(str(r["kickoff"])), canonical_team(r["home_team"]), canonical_team(r["away_team"])) for r in target]
    if len(set(keys)) != TARGET_N:
        raise CoverageAuditError("duplicate locked EPL identity key")
    return target, {
        "artifact_id": LOCKED_TEST_ARTIFACT_ID,
        "identity_sha256": got_sha,
        "fixture_set_sha256": LOCKED_TEST_FIXTURE_SET_SHA256,
        "target_league": TARGET_LEAGUE,
        "target_season": TARGET_SEASON,
        "target_n": TARGET_N,
        "test_result_vault_opened": False,
        "test_labels_read": False,
    }


def fetch_and_project() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    req = urllib.request.Request(DOWNLOAD_URL, headers=UA)
    h = hashlib.sha256()
    wire_bytes = 0
    physical_rows = 0
    target_player_rows = 0
    incomplete_target_rows = 0
    safe_name_values: set[str] = set()
    safe_opponent_values: set[str] = set()
    season_counts: Counter[str] = Counter()
    team_fixture_features: dict[tuple[str, str, str, bool], set[tuple[float, float, float, float]]] = defaultdict(set)
    team_fixture_safe_rows: Counter[tuple[str, str, str, bool]] = Counter()
    exact_header: list[str] | None = None
    source_meta: dict[str, Any] = {}

    with urllib.request.urlopen(req, timeout=240) as resp:
        source_meta = {
            "requested_url": DOWNLOAD_URL,
            "final_url": resp.geturl(),
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type"),
            "content_length": resp.headers.get("Content-Length"),
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
            "retrieved_at": now(),
        }
        header_line = resp.readline()
        if not header_line:
            raise CoverageAuditError("empty SmartPlay dataset")
        h.update(header_line); wire_bytes += len(header_line)
        header_fields = split_csv_record_raw(header_line)
        exact_header = [decode_safe(x).lstrip("\ufeff") for x in header_fields]
        normalized = [x.casefold() for x in exact_header]
        if len(set(normalized)) != len(normalized):
            raise CoverageAuditError("duplicate CSV header names")
        positions = {name: i for i, name in enumerate(normalized)}
        missing = [x for x in SAFE_COLUMNS if x.casefold() not in positions]
        if missing:
            raise CoverageAuditError(f"safe columns missing: {missing}")
        if set(x.casefold() for x in SAFE_COLUMNS) & set(x.casefold() for x in FORBIDDEN_COLUMNS):
            raise CoverageAuditError("safe column contract intersects forbidden columns")
        safe_indices = {name: positions[name.casefold()] for name in SAFE_COLUMNS}
        max_safe_index = max(safe_indices.values())

        for line in resp:
            physical_rows += 1
            h.update(line); wire_bytes += len(line)
            if not line.strip():
                continue
            raw_fields = split_csv_record_raw(line)
            if len(raw_fields) != len(exact_header):
                raise CoverageAuditError(f"CSV field count drift row={physical_rows}: {len(raw_fields)} != {len(exact_header)}")
            if len(raw_fields) <= max_safe_index:
                raise CoverageAuditError(f"truncated safe row={physical_rows}")
            safe = {name: decode_safe(raw_fields[idx]) for name, idx in safe_indices.items()}
            season_counts[safe["season"]] += 1
            if safe["season"] != TARGET_DATASET_SEASON:
                continue
            target_player_rows += 1
            team_name = canonical_team(safe["team_name"])
            opponent_raw = safe["opponent_team_name"] or safe["us_opponent"]
            if not opponent_raw:
                raise CoverageAuditError(f"missing opponent name fixture={safe['fixture']!r}")
            opponent_name = canonical_team(opponent_raw)
            is_home = parse_bool(safe["is_home"])
            kickoff = parse_kickoff(safe["kickoff_time"])
            safe_name_values.add(safe["team_name"])
            safe_opponent_values.add(opponent_raw)
            key = (str(safe["fixture"]), kickoff, team_name, is_home)
            team_fixture_safe_rows[key] += 1
            ppda = finite_number("us_ppda", safe["us_ppda"], positive=True)
            opp_ppda = finite_number("us_opp_ppda", safe["us_opp_ppda"], positive=True)
            deep = finite_number("us_deep", safe["us_deep"], positive=False)
            deep_allowed = finite_number("us_deep_allowed", safe["us_deep_allowed"], positive=False)
            if None in {ppda, opp_ppda, deep, deep_allowed}:
                incomplete_target_rows += 1
                continue
            team_fixture_features[key].add((float(ppda), float(opp_ppda), float(deep), float(deep_allowed)))

    if exact_header is None:
        raise CoverageAuditError("header not read")

    inconsistent = {k: sorted(v) for k, v in team_fixture_features.items() if len(v) > 1}
    if inconsistent:
        sample = list(inconsistent.items())[:3]
        raise CoverageAuditError(f"inconsistent repeated team fixture features: {sample}")

    # Build one record per fixture from the two team-side records.
    by_fixture: dict[tuple[str, str], dict[bool, dict[str, Any]]] = defaultdict(dict)
    for key, values in team_fixture_features.items():
        fixture, kickoff, team_name, is_home = key
        if len(values) != 1:
            continue
        ppda, opp_ppda, deep, deep_allowed = next(iter(values))
        if is_home in by_fixture[(fixture, kickoff)]:
            raise CoverageAuditError(f"duplicate home/away side for fixture={fixture} kickoff={kickoff}")
        by_fixture[(fixture, kickoff)][is_home] = {
            "team": team_name,
            "ppda": ppda,
            "opp_ppda": opp_ppda,
            "deep": deep,
            "deep_allowed": deep_allowed,
        }

    projected: list[dict[str, Any]] = []
    missing_side_n = 0
    reciprocal_mismatch_n = 0
    for (fixture, kickoff), sides in sorted(by_fixture.items()):
        if set(sides) != {False, True}:
            missing_side_n += 1
            continue
        home = sides[True]; away = sides[False]
        # Understat pair consistency: each side's allowed/opponent feature must equal the other side's own feature.
        if not (
            math.isclose(home["opp_ppda"], away["ppda"], rel_tol=0, abs_tol=1e-9)
            and math.isclose(away["opp_ppda"], home["ppda"], rel_tol=0, abs_tol=1e-9)
            and math.isclose(home["deep_allowed"], away["deep"], rel_tol=0, abs_tol=1e-9)
            and math.isclose(away["deep_allowed"], home["deep"], rel_tol=0, abs_tol=1e-9)
        ):
            reciprocal_mismatch_n += 1
            continue
        projected.append({
            "source_fixture_id": fixture,
            "kickoff": kickoff,
            "home_team": home["team"],
            "away_team": away["team"],
            "h_deep": home["deep"],
            "a_deep": away["deep"],
            "h_ppda": home["ppda"],
            "a_ppda": away["ppda"],
        })

    source_meta.update({
        "wire_bytes_read": wire_bytes,
        "source_sha256": h.hexdigest(),
        "physical_data_rows_scanned": physical_rows,
        "target_player_rows": target_player_rows,
        "incomplete_target_player_rows": incomplete_target_rows,
        "season_counts": dict(sorted(season_counts.items())),
        "safe_team_names": sorted(safe_name_values),
        "safe_opponent_names": sorted(safe_opponent_values),
        "team_fixture_side_keys_n": len(team_fixture_safe_rows),
        "team_fixture_complete_feature_keys_n": len(team_fixture_features),
        "fixture_pairs_seen_n": len(by_fixture),
        "fixture_pairs_projected_n": len(projected),
        "missing_side_n": missing_side_n,
        "reciprocal_feature_mismatch_n": reciprocal_mismatch_n,
        "decoded_columns": list(SAFE_COLUMNS),
        "forbidden_value_columns_decoded": 0,
        "result_or_label_values_decoded": 0,
        "raw_dataset_persisted": False,
    })
    return projected, source_meta


def compare_to_locked_identity(target: Iterable[dict[str, Any]], source: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in target:
        key = (parse_kickoff(str(r["kickoff"])), canonical_team(r["home_team"]), canonical_team(r["away_team"]))
        if key in target_map:
            raise CoverageAuditError(f"duplicate locked target key: {key}")
        target_map[key] = r
    source_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in source:
        key = (str(r["kickoff"]), canonical_team(r["home_team"]), canonical_team(r["away_team"]))
        if key in source_map:
            raise CoverageAuditError(f"duplicate source fixture key: {key}")
        source_map[key] = r
    matched_keys = sorted(set(target_map) & set(source_map))
    missing = sorted(set(target_map) - set(source_map))
    extra = sorted(set(source_map) - set(target_map))
    projection: list[dict[str, Any]] = []
    for key in matched_keys:
        t = target_map[key]; s = source_map[key]
        projection.append({
            "fixture_id": str(t["fixture_id"]),
            "kickoff": key[0],
            "league": TARGET_LEAGUE,
            "season": TARGET_SEASON,
            "home_team_id": str(t["home_team_id"]),
            "away_team_id": str(t["away_team_id"]),
            "home_team": key[1],
            "away_team": key[2],
            "h_deep": s["h_deep"],
            "a_deep": s["a_deep"],
            "h_ppda": s["h_ppda"],
            "a_ppda": s["a_ppda"],
        })
    qualified = len(projection) == TARGET_N and not missing and not extra
    return {
        "status": "IDENTITY_COVERAGE_QUALIFIED" if qualified else "STOP_DATA_COVERAGE",
        "target_n": len(target_map),
        "source_n": len(source_map),
        "matched_n": len(matched_keys),
        "missing_n": len(missing),
        "extra_n": len(extra),
        "feature_complete_n": len(projection),
        "coverage_fraction": len(projection) / len(target_map) if target_map else 0.0,
        "missing_examples": [list(x) for x in missing[:20]],
        "extra_examples": [list(x) for x in extra[:20]],
    }, projection


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    target, identity_audit = load_locked_identity(args.identity)
    source_rows, source_audit = fetch_and_project()
    coverage, projection = compare_to_locked_identity(target, source_rows)
    projection_sha = sha256_bytes(canon(sorted(projection, key=lambda x: x["fixture_id"])))
    receipt = {
        "schema_version": "football3-nova-n1-smartplay-epl-coverage-audit-v1",
        "status": coverage["status"],
        "provider": PROVIDER,
        "dataset_repo": DATASET_REPO,
        "dataset_file": DATASET_FILE,
        "download_url": DOWNLOAD_URL,
        "declared_license": DECLARED_LICENSE,
        "source_project_commit": SOURCE_PROJECT_COMMIT,
        "source_license_blob_sha": SOURCE_LICENSE_BLOB_SHA,
        "source_data_readme_blob_sha": SOURCE_DATA_README_BLOB_SHA,
        "source_download_helper_blob_sha": SOURCE_DOWNLOAD_HELPER_BLOB_SHA,
        "license_scope_evidence": "Pinned SmartPlayFPL LICENSE is CC BY-NC 4.0; pinned data documentation names this training/evaluation CSV and documents the safe Understat team-match feature columns; pinned helper maps the CSV to the Hugging Face dataset repository.",
        "source_provenance_note": "This audit uses the redistributed SmartPlay dataset under its declared CC BY-NC 4.0 terms. It does not rely on or assert a direct Understat license.",
        "locked_test_identity": identity_audit,
        "source_audit": source_audit,
        "coverage": coverage,
        "safe_feature_projection_sha256": projection_sha,
        "safe_feature_projection_n": len(projection),
        "test_result_vault_opened": False,
        "test_raw_pages_opened": False,
        "test_labels_read": False,
        "external_result_or_label_values_decoded": 0,
        "scientific_parameters_changed": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "promotion_authorized": False,
        "audited_at": now(),
    }
    receipt_path = args.out / "smartplay_epl_coverage_audit.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if coverage["status"] == "IDENTITY_COVERAGE_QUALIFIED":
        feature_path = args.out / "smartplay_epl_feature_projection.jsonl"
        with feature_path.open("w", encoding="utf-8") as f:
            for row in sorted(projection, key=lambda x: x["fixture_id"]):
                f.write(canon(row).decode("utf-8") + "\n")
    print(json.dumps({
        "status": coverage["status"],
        "source_n": coverage["source_n"],
        "target_n": coverage["target_n"],
        "matched_n": coverage["matched_n"],
        "missing_n": coverage["missing_n"],
        "extra_n": coverage["extra_n"],
        "feature_complete_n": coverage["feature_complete_n"],
        "test_labels_read": False,
        "external_result_or_label_values_decoded": 0,
        "projection_sha256": projection_sha,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
