#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import re
import urllib.request
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from nova_mhsendur_understat_header_audit_v1 import git_blob_sha1, validate_lock

TARGET_LEAGUES = ("Bundesliga", "EPL", "La_liga", "Ligue_1", "Serie_A")
MEMBER_RE = re.compile(
    r"^football_data_csv/(Bundesliga|EPL|La_liga|Ligue_1|Serie_A|RFPL)_(20\d{2})_(.+)\.csv$"
)
SAFE_COLUMNS = ("h_a", "ppda", "ppda_allowed", "deep", "deep_allowed", "date")


class CoverageError(ValueError):
    pass


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Football3-Nova-N1-Zero-Label-Coverage/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CoverageError("unable to decode CSV member")


def decimal_value(value, *, field: str) -> Decimal:
    text = str(value if value is not None else "").strip()
    if not text:
        raise CoverageError(f"empty {field}")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise CoverageError(f"invalid numeric {field} {text!r}") from exc
    if not parsed.is_finite():
        raise CoverageError(f"non-finite numeric {field} {text!r}")
    return parsed


def decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def num(value) -> str:
    return decimal_text(decimal_value(value, field="feature"))


def ppda_value(value) -> tuple[str, bool]:
    """Return canonical PPDA ratio and whether Understat's {att, def} form was used."""
    text = str(value if value is not None else "").strip()
    if not text:
        raise CoverageError("empty PPDA value")

    # Historical Understat team-history CSVs serialize ppda / ppda_allowed as
    # Python-dict-like strings, e.g. "{'att': 132, 'def': 23}".
    if text.startswith("{"):
        try:
            obj = ast.literal_eval(text)
        except (SyntaxError, ValueError) as exc:
            raise CoverageError(f"invalid PPDA mapping {text!r}") from exc
        if not isinstance(obj, dict) or set(obj) < {"att", "def"}:
            raise CoverageError(f"invalid PPDA mapping {text!r}")
        att = decimal_value(obj.get("att"), field="PPDA att")
        deff = decimal_value(obj.get("def"), field="PPDA def")
        if deff <= 0:
            raise CoverageError(f"invalid PPDA denominator {text!r}")
        with localcontext() as ctx:
            ctx.prec = 28
            ratio = att / deff
        return decimal_text(ratio), True

    return num(text), False


def ident(member: str):
    match = MEMBER_RE.match(member)
    return match.groups() if match else None


def project(zf, member: str, league: str, season: str, team: str):
    with zf.open(member) as fh:
        reader = csv.DictReader(io.StringIO(decode(fh.read())))
    if not reader.fieldnames:
        raise CoverageError(f"{member}: missing header")
    names = {name.strip().lower(): name for name in reader.fieldnames}
    missing = [name for name in SAFE_COLUMNS if name not in names]
    if missing:
        raise CoverageError(f"{member}: missing safe columns {missing}")

    out = []
    ppda_object_values = 0
    for row_index, row in enumerate(reader, start=2):
        home_away = (row[names["h_a"]] or "").strip().lower()
        date = (row[names["date"]] or "").strip()
        if home_away not in ("h", "a") or not date:
            raise CoverageError(f"{member}:{row_index}: invalid identity")

        ppda, ppda_obj = ppda_value(row[names["ppda"]])
        ppda_allowed, ppda_allowed_obj = ppda_value(row[names["ppda_allowed"]])
        ppda_object_values += int(ppda_obj) + int(ppda_allowed_obj)
        out.append(
            {
                "league": league,
                "season_start": season,
                "team": team,
                "date": date,
                "h_a": home_away,
                "ppda": ppda,
                "ppda_allowed": ppda_allowed,
                "deep": num(row[names["deep"]]),
                "deep_allowed": num(row[names["deep_allowed"]]),
            }
        )
    return out, ppda_object_values


def psha(rows):
    stripped = [{k: v for k, v in row.items() if k != "team"} for row in rows]
    return sha256(b"\n".join(canonical(row) for row in stripped) + b"\n")


def reciprocal(a, b) -> bool:
    return (
        a["date"] == b["date"]
        and a["h_a"] != b["h_a"]
        and a["ppda"] == b["ppda_allowed"]
        and a["ppda_allowed"] == b["ppda"]
        and a["deep"] == b["deep_allowed"]
        and a["deep_allowed"] == b["deep"]
    )


def audit(lock, archive: bytes):
    validate_lock(lock)
    blob = git_blob_sha1(archive)
    if blob != lock["source"]["archive_blob_sha1"]:
        raise CoverageError(f"archive blob SHA drift: {blob}")

    member_rows = {}
    meta = {}
    ppda_object_values = 0
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in sorted(zf.namelist()):
            parsed = ident(member)
            if not parsed or parsed[0] not in TARGET_LEAGUES:
                continue
            rows, member_ppda_objects = project(zf, member, *parsed)
            member_rows[member] = rows
            meta[member] = parsed
            ppda_object_values += member_ppda_objects

    if not member_rows:
        raise CoverageError("no target-league CSV members")

    lookup = {(league, season, team): member for member, (league, season, team) in meta.items()}
    logical = []
    duplicates = []
    conflicts = []
    for member in sorted(member_rows):
        league, season, team = meta[member]
        if team.endswith(" 2"):
            base = lookup.get((league, season, team[:-2]))
            if base:
                if psha(member_rows[member]) == psha(member_rows[base]):
                    duplicates.append({"duplicate": member, "canonical": base})
                    continue
                conflicts.append({"duplicate": member, "candidate_canonical": base})
        logical.append(member)

    rows = [row for member in logical for row in member_rows[member]]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["league"], row["season_start"], row["date"])].append(row)

    pairs = []
    unpaired = []
    ambiguous = []
    for _, group in sorted(groups.items()):
        homes = [row for row in group if row["h_a"] == "h"]
        aways = [row for row in group if row["h_a"] == "a"]
        candidates = {
            index: [
                away_index
                for away_index, away in enumerate(aways)
                if away["team"] != home["team"] and reciprocal(home, away)
            ]
            for index, home in enumerate(homes)
        }
        reverse_count = defaultdict(int)
        for away_indexes in candidates.values():
            for away_index in away_indexes:
                reverse_count[away_index] += 1

        used = set()
        for home_index, home in enumerate(homes):
            away_indexes = candidates[home_index]
            if len(away_indexes) == 1 and reverse_count[away_indexes[0]] == 1:
                away = aways[away_indexes[0]]
                used.update((id(home), id(away)))
                pairs.append(
                    {
                        "league": home["league"],
                        "season_start": home["season_start"],
                        "date": home["date"],
                        "home_team": home["team"],
                        "away_team": away["team"],
                        "home_ppda": home["ppda"],
                        "away_ppda": away["ppda"],
                        "home_deep": home["deep"],
                        "away_deep": away["deep"],
                    }
                )
            elif len(away_indexes) > 1:
                ambiguous.append(
                    {
                        "league": home["league"],
                        "season_start": home["season_start"],
                        "date": home["date"],
                        "team": home["team"],
                        "candidate_count": len(away_indexes),
                    }
                )

        ambiguous_keys = {
            (row["league"], row["season_start"], row["date"], row["team"])
            for row in ambiguous
        }
        for row in group:
            key = (row["league"], row["season_start"], row["date"], row["team"])
            if id(row) not in used and key not in ambiguous_keys:
                unpaired.append(
                    {k: row[k] for k in ("league", "season_start", "date", "team", "h_a")}
                )

    cohort_rows = defaultdict(int)
    cohort_matches = defaultdict(int)
    cohort_unpaired = defaultdict(int)
    cohort_ambiguous = defaultdict(int)
    for row in rows:
        cohort_rows[(row["league"], row["season_start"])] += 1
    for match in pairs:
        cohort_matches[(match["league"], match["season_start"])] += 1
    for row in unpaired:
        cohort_unpaired[(row["league"], row["season_start"])] += 1
    for row in ambiguous:
        cohort_ambiguous[(row["league"], row["season_start"])] += 1

    cohorts = {
        f"{league}:{season}": {
            "perspective_rows": cohort_rows[(league, season)],
            "paired_matches": cohort_matches[(league, season)],
            "unpaired_rows": cohort_unpaired[(league, season)],
            "ambiguous_home_rows": cohort_ambiguous[(league, season)],
        }
        for league, season in sorted(cohort_rows)
    }
    pairs.sort(
        key=lambda match: (
            match["season_start"],
            match["league"],
            match["date"],
            match["home_team"],
            match["away_team"],
        )
    )
    clean = (
        not conflicts
        and not unpaired
        and not ambiguous
        and len(rows) == 2 * len(pairs)
    )

    return {
        "status": "ZERO_LABEL_COVERAGE_QUALIFIED" if clean else "ZERO_LABEL_COVERAGE_PARTIAL",
        "source_repository": lock["source"]["repository"],
        "source_revision": lock["source"]["revision"],
        "archive_blob_sha1": blob,
        "permission_class": lock["permission"]["class"],
        "production_eligible": False,
        "target_leagues": list(TARGET_LEAGUES),
        "logical_member_count": len(logical),
        "exact_duplicate_member_count": len(duplicates),
        "exact_duplicate_members": duplicates,
        "duplicate_conflict_count": len(conflicts),
        "duplicate_conflicts": conflicts,
        "safe_perspective_row_count": len(rows),
        "paired_match_count": len(pairs),
        "unpaired_row_count": len(unpaired),
        "ambiguous_home_row_count": len(ambiguous),
        "unpaired_rows_sample": unpaired[:100],
        "ambiguous_rows_sample": ambiguous[:100],
        "per_cohort": cohorts,
        "feature_projection_sha256": sha256(
            b"\n".join(canonical(match) for match in pairs) + b"\n"
        ),
        "safe_columns_used": list(SAFE_COLUMNS),
        "ppda_object_values_parsed": ppda_object_values,
        "ppda_normalization": "att_div_def_when_mapping_else_numeric",
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    receipt = audit(lock, fetch_bytes(lock["source"]["raw_url"]))
    Path(args.out).write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
