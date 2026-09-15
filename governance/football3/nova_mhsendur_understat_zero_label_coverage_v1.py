#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nova_mhsendur_understat_header_audit_v1 import git_blob_sha1, validate_lock

TARGET_LEAGUES = ("Bundesliga", "EPL", "La_liga", "Ligue_1", "Serie_A")
MEMBER_RE = re.compile(r"^football_data_csv/(Bundesliga|EPL|La_liga|Ligue_1|Serie_A|RFPL)_(20\d{2})_(.+)\.csv$")
SAFE_COLUMNS = ("h_a", "ppda", "ppda_allowed", "deep", "deep_allowed", "date")
FORBIDDEN_COLUMNS = ("result", "scored", "missed", "xg", "xga", "npxg", "npxga", "xpts", "wins", "draws", "loses", "pts", "npxgd")


class CoverageError(ValueError):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Football3-Nova-N1-Zero-Label-Coverage/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CoverageError("unable to decode CSV member")


def norm_num(value: str) -> str:
    text = (value or "").strip()
    if text == "":
        raise CoverageError("empty locked feature value")
    try:
        d = Decimal(text)
    except InvalidOperation as exc:
        raise CoverageError(f"invalid numeric feature value {text!r}") from exc
    if not d.is_finite():
        raise CoverageError(f"non-finite numeric feature value {text!r}")
    out = format(d.normalize(), "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    if out in {"-0", ""}:
        out = "0"
    return out


def parse_member_identity(member: str) -> tuple[str, str, str] | None:
    m = MEMBER_RE.match(member)
    if not m:
        return None
    league, season_start, team = m.groups()
    return league, season_start, team


def project_member(zf: zipfile.ZipFile, member: str, league: str, season_start: str, team: str) -> list[dict]:
    with zf.open(member, "r") as fh:
        text = decode_csv(fh.read())
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CoverageError(f"{member}: missing header")
    by_lower = {name.strip().lower(): name for name in reader.fieldnames}
    missing = [c for c in SAFE_COLUMNS if c not in by_lower]
    if missing:
        raise CoverageError(f"{member}: missing safe columns {missing}")
    rows = []
    for row_index, row in enumerate(reader, start=2):
        h_a = (row[by_lower["h_a"]] or "").strip().lower()
        if h_a not in {"h", "a"}:
            raise CoverageError(f"{member}:{row_index}: invalid h_a {h_a!r}")
        date = (row[by_lower["date"]] or "").strip()
        if not date:
            raise CoverageError(f"{member}:{row_index}: empty date")
        rows.append({
            "league": league,
            "season_start": season_start,
            "team": team,
            "date": date,
            "h_a": h_a,
            "ppda": norm_num(row[by_lower["ppda"]]),
            "ppda_allowed": norm_num(row[by_lower["ppda_allowed"]]),
            "deep": norm_num(row[by_lower["deep"]]),
            "deep_allowed": norm_num(row[by_lower["deep_allowed"]]),
        })
    return rows


def member_projection_sha(rows: list[dict]) -> str:
    stripped = [{k: v for k, v in r.items() if k != "team"} for r in rows]
    return sha256_hex(b"\n".join(canonical(r) for r in stripped) + b"\n")


def reciprocal(a: dict, b: dict) -> bool:
    return (
        a["date"] == b["date"]
        and a["h_a"] != b["h_a"]
        and a["ppda"] == b["ppda_allowed"]
        and a["ppda_allowed"] == b["ppda"]
        and a["deep"] == b["deep_allowed"]
        and a["deep_allowed"] == b["deep"]
    )


def audit(lock: dict, archive: bytes) -> dict:
    validate_lock(lock)
    observed_blob = git_blob_sha1(archive)
    if observed_blob != lock["source"]["archive_blob_sha1"]:
        raise CoverageError(f"archive blob SHA drift: {observed_blob}")

    member_rows: dict[str, list[dict]] = {}
    member_meta: dict[str, tuple[str, str, str]] = {}
    ignored_members = []
    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        for member in sorted(zf.namelist()):
            if member.endswith("/") or not member.lower().endswith(".csv"):
                continue
            ident = parse_member_identity(member)
            if ident is None:
                ignored_members.append(member)
                continue
            league, season_start, team = ident
            if league not in TARGET_LEAGUES:
                continue
            rows = project_member(zf, member, league, season_start, team)
            member_rows[member] = rows
            member_meta[member] = ident

    if not member_rows:
        raise CoverageError("no target-league CSV members")

    logical_members = []
    exact_duplicate_members = []
    duplicate_conflicts = []
    lookup = {(league, season, team): member for member, (league, season, team) in member_meta.items()}
    consumed = set()
    for member in sorted(member_rows):
        if member in consumed:
            continue
        league, season, team = member_meta[member]
        if team.endswith(" 2"):
            base_team = team[:-2]
            base_member = lookup.get((league, season, base_team))
            if base_member:
                if member_projection_sha(member_rows[member]) == member_projection_sha(member_rows[base_member]):
                    exact_duplicate_members.append({"duplicate": member, "canonical": base_member})
                    consumed.add(member)
                    continue
                duplicate_conflicts.append({"duplicate": member, "candidate_canonical": base_member})
        logical_members.append(member)

    rows = []
    for member in logical_members:
        rows.extend(member_rows[member])

    grouped: dict[tuple[str, str, str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["league"], r["season_start"], r["date"])].append(r)

    paired_matches = []
    ambiguous_rows = []
    unpaired_rows = []
    used_ids = set()
    for group_key in sorted(grouped):
        group = grouped[group_key]
        homes = [r for r in group if r["h_a"] == "h"]
        aways = [r for r in group if r["h_a"] == "a"]
        candidates: dict[int, list[int]] = {}
        for hi, h in enumerate(homes):
            candidates[hi] = [ai for ai, a in enumerate(aways) if a["team"] != h["team"] and reciprocal(h, a)]
        reverse_count = defaultdict(int)
        for ais in candidates.values():
            for ai in ais:
                reverse_count[ai] += 1
        for hi, h in enumerate(homes):
            ais = candidates[hi]
            if len(ais) == 1 and reverse_count[ais[0]] == 1:
                a = aways[ais[0]]
                hid = id(h); aid = id(a)
                if hid in used_ids or aid in used_ids:
                    continue
                used_ids.add(hid); used_ids.add(aid)
                paired_matches.append({
                    "league": h["league"],
                    "season_start": h["season_start"],
                    "date": h["date"],
                    "home_team": h["team"],
                    "away_team": a["team"],
                    "home_ppda": h["ppda"],
                    "away_ppda": a["ppda"],
                    "home_deep": h["deep"],
                    "away_deep": a["deep"],
                })
            elif len(ais) > 1:
                ambiguous_rows.append({"league": h["league"], "season_start": h["season_start"], "date": h["date"], "team": h["team"], "candidate_count": len(ais)})
        for r in group:
            if id(r) not in used_ids:
                if not any(x["league"] == r["league"] and x["season_start"] == r["season_start"] and x["date"] == r["date"] and x["team"] == r["team"] for x in ambiguous_rows):
                    unpaired_rows.append({"league": r["league"], "season_start": r["season_start"], "date": r["date"], "team": r["team"], "h_a": r["h_a"]})

    per_cohort = {}
    cohort_rows = defaultdict(int)
    cohort_matches = defaultdict(int)
    cohort_unpaired = defaultdict(int)
    cohort_ambiguous = defaultdict(int)
    for r in rows:
        cohort_wrows[(r["league"], r["season_start"])] += 1
    for m in paired_matches:
        cohort_matches[(m["league"], m["season_start"])] += 1
    for r in unpaired_rows:
        cohort_unpaired[(r["league"], r["season_start"])] += 1
    for r in ambiguous_rows:
        cohort_ambiguous[(r["league"], r["season_start"])] += 1
    for key in sorted(cohort_rows):
        league, season = key
        per_cohort[f"{league}:{season}"] = {
            "perspective_rows": cohort_rows[key],
            "paired_matches": cohort_matches[key],
            "unpaired_rows": cohort_unpaired[key],
            "ambiguous_home_rows": cohort_ambiguous[key],
        }

    paired_matches.sort(key=lambda m: (m["season_start"], m["league"], m["date"], m["home_team"], m["away_team"]))
    projection_sha = sha256_hex(b"\n".join(canonical(m) for m in paired_matches) + b"\n")
    clean = not duplicate_conflicts and not unpaired_rows and not ambiguous_rows and len(rows) == 2 * len(paired_matches)
    status = "ZERO_LABEL_COVERAGE_QUALIFIED" if clean else "ZERO_LABEL_COVERAGE_PARTIAL"
    return {
        "status": status,
        "source_repository": lock["source"]["repository"],
        "source_revision": lock["source"]["revision"],
        "archive_blob_sha1": observed_blob,
        "permission_class": lock["permission"]["class"],
        "production_eligible": False,
        "target_leagues": list(TARGET_LEAGUES),
        "logical_member_count": len(logical_members),
        "exact_duplicate_member_count": len(exact_duplicate_members),
        "exact_duplicate_members": exact_duplicate_members,
        "duplicate_conflict_count": len(duplicate_conflicts),
        "duplicate_conflicts": duplicate_conflicts,
        "safe_perspective_row_count": len(rows),
        "paired_match_count": len(paired_matches),
        "unpaired_row_count": len(unpaired_rows),
        "ambiguous_home_row_count": len(ambiguous_rows),
        "unpaired_rows_sample": unpaired_rows[:100],
        "ambiguous_rows_sample": ambiguous_rows[:100],
        "per_cohort": per_cohort,
        "feature_projection_sha256": projection_sha,
        "safe_columns_used": list(SAFE_COLUMNS),
        "forbidden_columns_used_for_identity_or_coverage": [],
        "result_values_used": 0,
        "score_values_used": 0,
      "xg_values_used": 0,
      "candidate_confirmation_allowed": False,
       "allowed_research_roles": ["TRAIN", "DEVELOPMENT", "REUSABLE_BENCHMARK"],
       "formal_v2_changed": False,
      "current_changed": False,
      "production_changed": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    archive = fetch_bytes(lock["source"]["raw_url"])
    receipt = audit(lock, archive)
    Path(args.out).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
