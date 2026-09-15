#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import pathlib
import re
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

SOURCE_PAGE = "https://www.kaggle.com/datasets/madferit/la-liga-matches-20142025-final-dataset"
DOWNLOAD_URL = "https://www.kaggle.com/api/v1/datasets/download/madferit/la-liga-matches-20142025-final-dataset"
PROVIDER = "Madferit94 - La Liga Matches 2014-2025 (Final Dataset)"
DECLARED_LICENSE = "CC BY 4.0"
LOCKED_ARCHIVE_SHA256 = "7f02a36204e383817387848f47b6aa16a4450a804f756ffb3e9317e0ac2185f9"
LOCKED_CSV_MEMBER = "la_liga_2014_2025_all_matches_final.csv"
LOCKED_TEST_ARTIFACT_ID = 9827606506
LOCKED_TEST_IDENTITY_SHA256 = "c861032ea523cb921eebc7455941e2a71ecb2f3ecc3a8df6858eef0b7aaf8d95"
LOCKED_TEST_FIXTURE_SET_SHA256 = "0f156c22df1976b929d0db24135830e8b3fac8546130e7bf5e08673918f22bc6"
TARGET_LEAGUE = "La liga"
TARGET_SEASON = 2024
TARGET_N = 380
TARGET_START = "2024-08-15"
TARGET_END_EXCLUSIVE = "2025-05-26"

EXPECTED_HEADER = [
    "match_id", "date", "home_team", "away_team", "home_goals", "away_goals",
    "home_xg", "away_xg", "home_shots", "away_shots", "home_sot", "away_sot",
    "home_deep", "away_deep", "home_ppda", "away_ppda", "home_xpts", "away_xpts",
    "season_mapped",
]
SAFE_INDEXES = {
    "match_id": 0,
    "date": 1,
    "home_team": 2,
    "away_team": 3,
    "home_deep": 12,
    "away_deep": 13,
    "home_ppda": 14,
    "away_ppda": 15,
    "season_mapped": 18,
}
FORBIDDEN_INDEXES = {4, 5, 6, 7, 8, 9, 10, 11, 16, 17}
UA = {
    "User-Agent": "Football3-Nova-N1-identity-coverage-audit/1.0",
    "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.1",
}

# Canonical names are exactly the locked Formal V2 confirmation identity names.
ALIASES = {
    "alaves": "Alaves", "deportivoalaves": "Alaves", "deportivoalavescf": "Alaves",
    "athleticclub": "Athletic Club", "athleticbilbao": "Athletic Club", "athleticclubbilbao": "Athletic Club",
    "atleticomadrid": "Atletico Madrid", "clubatleticodemadrid": "Atletico Madrid",
    "barcelona": "Barcelona", "fcbarcelona": "Barcelona",
    "celtavigo": "Celta Vigo", "rcdcelta": "Celta Vigo", "celtadevigo": "Celta Vigo",
    "espanyol": "Espanyol", "rcdespanyol": "Espanyol", "rcdespanyoldebarcelona": "Espanyol",
    "getafe": "Getafe", "getafecf": "Getafe",
    "girona": "Girona", "gironafc": "Girona",
    "laspalmas": "Las Palmas", "udlaspalmas": "Las Palmas",
    "leganes": "Leganes", "cdleganes": "Leganes",
    "mallorca": "Mallorca", "rcdmallorca": "Mallorca",
    "osasuna": "Osasuna", "caosasuna": "Osasuna",
    "rayovallecano": "Rayo Vallecano", "rayovallecanodemadrid": "Rayo Vallecano",
    "realbetis": "Real Betis", "realbetisbalompie": "Real Betis",
    "realmadrid": "Real Madrid", "realmadridcf": "Real Madrid",
    "realsociedad": "Real Sociedad", "realsociedaddefutbol": "Real Sociedad",
    "realvalladolid": "Real Valladolid", "realvalladolidcf": "Real Valladolid",
    "sevilla": "Sevilla", "sevillafc": "Sevilla",
    "valencia": "Valencia", "valenciacf": "Valencia",
    "villarreal": "Villarreal", "villarrealcf": "Villarreal",
}


class CoverageAuditError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def normalize_token(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def canonical_team(value: str) -> str:
    key = normalize_token(value)
    if key not in ALIASES:
        raise CoverageAuditError(f"unmapped team identity: {value!r} -> {key!r}")
    return ALIASES[key]


def parse_date(value: str) -> str:
    s = str(value).strip()
    if not s:
        raise CoverageAuditError("empty date")
    # Deterministic accepted forms only. No locale-dependent parser.
    for pat in (
        r"^(\d{4})-(\d{2})-(\d{2})",
        r"^(\d{4})/(\d{2})/(\d{2})",
        r"^(\d{2})/(\d{2})/(\d{4})$",
        r"^(\d{2})-(\d{2})-(\d{4})$",
    ):
        m = re.match(pat, s)
        if not m:
            continue
        a, b, c = m.groups()
        if len(a) == 4:
            y, mo, d = int(a), int(b), int(c)
        else:
            d, mo, y = int(a), int(b), int(c)
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError as exc:
            raise CoverageAuditError(f"invalid date: {value!r}") from exc
    raise CoverageAuditError(f"unsupported date format: {value!r}")


def finite_nonnegative(name: str, value: str) -> float:
    try:
        x = float(str(value).strip())
    except Exception as exc:
        raise CoverageAuditError(f"invalid {name}: {value!r}") from exc
    if not math.isfinite(x) or x < 0:
        raise CoverageAuditError(f"invalid {name}: {value!r}")
    return x


def finite_positive(name: str, value: str) -> float:
    x = finite_nonnegative(name, value)
    if x <= 0:
        raise CoverageAuditError(f"invalid {name}: {value!r}")
    return x


def load_locked_identity(path: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_bytes()
    got_sha = sha256(raw)
    if got_sha != LOCKED_TEST_IDENTITY_SHA256:
        raise CoverageAuditError(f"locked identity sha mismatch: {got_sha} != {LOCKED_TEST_IDENTITY_SHA256}")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != 1752:
        raise CoverageAuditError(f"locked identity n mismatch: {len(rows)} != 1752")
    # Only identity fields are permitted here. A result-like field indicates the wrong artifact member.
    forbidden = {"home_goals", "away_goals", "result", "winner", "home_xg", "away_xg", "h_goals", "a_goals"}
    for i, row in enumerate(rows):
        if forbidden & {str(k).casefold() for k in row}:
            raise CoverageAuditError(f"result-like field found in locked identity row {i}")
    target = [r for r in rows if r.get("league") == TARGET_LEAGUE and int(r.get("season")) == TARGET_SEASON]
    if len(target) != TARGET_N:
        raise CoverageAuditError(f"La Liga target identity n mismatch: {len(target)} != {TARGET_N}")
    keys = []
    for r in target:
        key = (parse_date(str(r["kickoff"])), canonical_team(str(r["home_team"])), canonical_team(str(r["away_team"])))
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise CoverageAuditError("duplicate key in locked La Liga identity")
    return target, {
        "artifact_id": LOCKED_TEST_ARTIFACT_ID,
        "identity_sha256": got_sha,
        "fixture_set_sha256": LOCKED_TEST_FIXTURE_SET_SHA256,
        "all_identity_n": len(rows),
        "target_league": TARGET_LEAGUE,
        "target_season": TARGET_SEASON,
        "target_n": len(target),
        "test_result_vault_opened": False,
        "test_labels_read": False,
    }


def download_source() -> tuple[bytes, dict[str, Any]]:
    req = urllib.request.Request(DOWNLOAD_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
        meta = {
            "retrieved_at": now(),
            "http_status": getattr(resp, "status", None),
            "content_type": resp.headers.get("Content-Type"),
            "bytes": len(raw),
            "sha256": sha256(raw),
            "final_url": resp.geturl(),
        }
    if meta["sha256"] != LOCKED_ARCHIVE_SHA256:
        raise CoverageAuditError(f"source archive drift: {meta['sha256']} != {LOCKED_ARCHIVE_SHA256}")
    return raw, meta


def _decode_safe_fields(raw_line: bytes) -> list[str]:
    # This source was header-audited as a flat 19-column CSV. To keep zero-label audit semantics,
    # reject quoted rows and decode only the whitelisted field byte slices. Result/xG values are
    # neither decoded nor exposed to the caller.
    if b'"' in raw_line:
        raise CoverageAuditError("quoted data row is outside locked flat-CSV parser contract")
    parts = raw_line.rstrip(b"\r\n").split(b",")
    if len(parts) != len(EXPECTED_HEADER):
        raise CoverageAuditError(f"unexpected field count: {len(parts)} != {len(EXPECTED_HEADER)}")
    if set(SAFE_INDEXES.values()) & FORBIDDEN_INDEXES:
        raise CoverageAuditError("safe field indexes intersect forbidden indexes")
    out = []
    for idx in SAFE_INDEXES.values():
        try:
            out.append(parts[idx].decode("utf-8").strip())
        except UnicodeDecodeError:
            out.append(parts[idx].decode("latin-1").strip())
    return out


def project_source_rows(raw_zip: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sha256(raw_zip) != LOCKED_ARCHIVE_SHA256:
        raise CoverageAuditError("archive SHA not locked")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    except zipfile.BadZipFile as exc:
        raise CoverageAuditError("source archive is not a ZIP") from exc
    bad = zf.testzip()
    if bad:
        raise CoverageAuditError(f"ZIP CRC failed: {bad}")
    names = [x.filename for x in zf.infolist() if not x.is_dir()]
    if LOCKED_CSV_MEMBER not in names:
        raise CoverageAuditError(f"locked CSV member missing: {LOCKED_CSV_MEMBER}")
    rows: list[dict[str, Any]] = []
    with zf.open(LOCKED_CSV_MEMBER) as f:
        header_line = f.readline()
        try:
            header = header_line.decode("utf-8-sig").strip().split(",")
        except UnicodeDecodeError:
            header = header_line.decode("latin-1").strip().split(",")
        if header != EXPECTED_HEADER:
            raise CoverageAuditError(f"header drift: {header!r}")
        for raw_line in f:
            if not raw_line.strip():
                continue
            safe = _decode_safe_fields(raw_line)
            x = dict(zip(SAFE_INDEXES.keys(), safe))
            date = parse_date(x["date"])
            if not (TARGET_START <= date < TARGET_END_EXCLUSIVE):
                continue
            row = {
                "source_match_id": str(x["match_id"]),
                "date": date,
                "home_team": canonical_team(x["home_team"]),
                "away_team": canonical_team(x["away_team"]),
                "home_deep": finite_nonnegative("home_deep", x["home_deep"]),
                "away_deep": finite_nonnegative("away_deep", x["away_deep"]),
                "home_ppda": finite_positive("home_ppda", x["home_ppda"]),
                "away_ppda": finite_positive("away_ppda", x["away_ppda"]),
                "source_season_mapped": str(x["season_mapped"]),
            }
            rows.append(row)
    keys = [(x["date"], x["home_team"], x["away_team"]) for x in rows]
    if len(set(keys)) != len(keys):
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        raise CoverageAuditError(f"duplicate projected source fixture identities: {dupes[:5]}")
    safe_projection_sha = sha256(canon(sorted(rows, key=lambda x: (x["date"], x["home_team"], x["away_team"]))))
    return rows, {
        "zip_crc": "PASS",
        "csv_member": LOCKED_CSV_MEMBER,
        "source_window_n": len(rows),
        "safe_projection_sha256": safe_projection_sha,
        "external_data_rows_scanned": len(rows),
        "external_forbidden_value_fields_decoded": 0,
        "external_goal_or_xg_values_persisted": False,
    }


def compare_coverage(target: Iterable[dict[str, Any]], source: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in target:
        key = (parse_date(str(r["kickoff"])), canonical_team(str(r["home_team"])), canonical_team(str(r["away_team"])))
        if key in target_map:
            raise CoverageAuditError(f"duplicate target key: {key}")
        target_map[key] = r
    source_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in source:
        key = (str(r["date"]), str(r["home_team"]), str(r["away_team"]))
        if key in source_map:
            raise CoverageAuditError(f"duplicate source key: {key}")
        source_map[key] = r
    missing = sorted(set(target_map) - set(source_map))
    extra = sorted(set(source_map) - set(target_map))
    matched = sorted(set(target_map) & set(source_map))
    projection: list[dict[str, Any]] = []
    for key in matched:
        t = target_map[key]
        s = source_map[key]
        projection.append({
            "fixture_id": str(t["fixture_id"]),
            "date": key[0],
            "league": TARGET_LEAGUE,
            "season": TARGET_SEASON,
            "home_team_id": str(t["home_team_id"]),
            "away_team_id": str(t["away_team_id"]),
            "home_team": key[1],
            "away_team": key[2],
            "home_deep": s["home_deep"],
            "away_deep": s["away_deep"],
            "home_ppda": s["home_ppda"],
            "away_ppda": s["away_ppda"],
        })
    status = "IDENTITY_COVERAGE_QUALIFIED" if len(matched) == TARGET_N and not missing and not extra else "STOP_DATA_COVERAGE"
    return {
        "status": status,
        "target_n": len(target_map),
        "source_window_n": len(source_map),
        "matched_n": len(matched),
        "missing_n": len(missing),
        "extra_n": len(extra),
        "missing_examples": [list(x) for x in missing[:20]],
        "extra_examples": [list(x) for x in extra[:20]],
        "feature_complete_n": len(projection),
        "coverage_fraction": len(matched) / len(target_map) if target_map else 0.0,
    }, projection


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    target, identity_audit = load_locked_identity(args.identity)
    source_raw, fetch = download_source()
    source_rows, source_audit = project_source_rows(source_raw)
    coverage, projection = compare_coverage(target, source_rows)

    projection_sha = sha256(canon(sorted(projection, key=lambda x: x["fixture_id"])))
    receipt = {
        "schema_version": "football3-nova-n1-external-identity-coverage-audit-v1",
        "status": coverage["status"],
        "provider": PROVIDER,
        "source_page": SOURCE_PAGE,
        "download_url": DOWNLOAD_URL,
        "declared_license": DECLARED_LICENSE,
        "locked_archive_sha256": LOCKED_ARCHIVE_SHA256,
        "locked_csv_member": LOCKED_CSV_MEMBER,
        "locked_test_identity": identity_audit,
        "fetch": fetch,
        "source_audit": source_audit,
        "coverage": coverage,
        "safe_feature_projection_sha256": projection_sha,
        "safe_feature_projection_n": len(projection),
        "test_result_vault_opened": False,
        "test_raw_pages_opened": False,
        "test_labels_read": False,
        "scientific_parameters_changed": False,
        "formal_v2_modified": False,
        "current_modified": False,
        "production_modified": False,
        "promotion_authorized": False,
        "audited_at": now(),
    }
    (args.out / "external_identity_coverage_audit.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    # Persist feature-only projection iff the locked La Liga cohort is exact. This is not a result/label file.
    if coverage["status"] == "IDENTITY_COVERAGE_QUALIFIED":
        with (args.out / "external_laliga_feature_projection.jsonl").open("w", encoding="utf-8") as f:
            for row in sorted(projection, key=lambda x: x["fixture_id"]):
                f.write(canon(row).decode("utf-8") + "\n")
    print(json.dumps({
        "status": coverage["status"], "target_n": coverage["target_n"], "matched_n": coverage["matched_n"],
        "missing_n": coverage["missing_n"], "extra_n": coverage["extra_n"],
        "test_labels_read": False, "projection_sha256": projection_sha,
    }, sort_keys=True))
    return 0 if coverage["status"] == "IDENTITY_COVERAGE_QUALIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
