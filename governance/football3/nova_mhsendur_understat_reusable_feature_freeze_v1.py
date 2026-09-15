#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

from nova_mhsendur_understat_header_audit_v1 import git_blob_sha1, validate_lock
from nova_mhsendur_understat_zero_label_coverage_v1 import ident, project, psha, reciprocal, canonical, sha256


class FreezeError(ValueError):
    pass


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Football3-Nova-Reusable-Feature-Freeze/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def validate_freeze(source_lock: dict, freeze: dict) -> None:
    validate_lock(source_lock)
    if freeze.get("schema_version") != "football3-nova-n1-mhsendur-reusable-feature-freeze-v1":
        raise FreezeError("unexpected freeze schema_version")
    src = freeze.get("source", {})
    locked = source_lock.get("source", {})
    for freeze_key, lock_key in (
        ("repository", "repository"),
        ("revision", "revision"),
        ("archive_path", "archive_path"),
        ("archive_blob_sha1", "archive_blob_sha1"),
    ):
        if src.get(freeze_key) != locked.get(lock_key):
            raise FreezeError(f"source lock drift for {freeze_key}")
    if src.get("permission_class") != "RESEARCH_ONLY_EXPLICIT_REUSE":
        raise FreezeError("permission class drift")
    if src.get("production_eligible") is not False:
        raise FreezeError("research-only feature source cannot be production eligible")
    policy = freeze.get("registration_policy", {})
    if policy.get("candidate_confirmation_allowed") is not False:
        raise FreezeError("historical source cannot be fresh confirmation")
    if policy.get("reported_benchmark_predictions_must_be_oof") is not True:
        raise FreezeError("OOF benchmark requirement missing")
    if any(policy.get(key) is not False for key in ("formal_v2_changed", "current_changed", "production_changed")):
        raise FreezeError("formal state changes are forbidden")


def source_match_key(source_id: str, revision: str, match: dict) -> str:
    identity = {
        "source_id": source_id,
        "source_revision": revision,
        "league": match["league"],
        "season_start": match["season_start"],
        "date": match["date"],
        "home_team": match["home_team"],
        "away_team": match["away_team"],
    }
    return sha256(canonical(identity))


def feature_input_sha(match: dict) -> str:
    return sha256(
        canonical(
            {
                "home_ppda": match["home_ppda"],
                "away_ppda": match["away_ppda"],
                "home_deep": match["home_deep"],
                "away_deep": match["away_deep"],
            }
        )
    )


def materialize(source_lock: dict, freeze: dict, archive: bytes) -> tuple[list[dict], dict]:
    validate_freeze(source_lock, freeze)
    observed_blob = git_blob_sha1(archive)
    if observed_blob != freeze["source"]["archive_blob_sha1"]:
        raise FreezeError(f"archive blob SHA drift: {observed_blob}")

    cohort = freeze["qualified_feature_cohorts"]
    season_min = int(cohort["season_start_min"])
    season_max = int(cohort["season_start_max"])
    competitions = set(cohort["competitions"])

    member_rows: dict[str, list[dict]] = {}
    member_meta: dict[str, tuple[str, str, str]] = {}
    ppda_object_values = 0
    with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
        for member in sorted(zf.namelist()):
            parsed = ident(member)
            if not parsed:
                continue
            league, season_start, team = parsed
            season_int = int(season_start)
            if league not in competitions or not (season_min <= season_int <= season_max):
                continue
            rows, parsed_objects = project(zf, member, league, season_start, team)
            member_rows[member] = rows
            member_meta[member] = (league, season_start, team)
            ppda_object_values += parsed_objects

    if not member_rows:
        raise FreezeError("no qualified feature members found")

    lookup = {
        (league, season, team): member
        for member, (league, season, team) in member_meta.items()
    }
    logical_members: list[str] = []
    exact_duplicates: list[dict] = []
    duplicate_conflicts: list[dict] = []
    for member in sorted(member_rows):
        league, season, team = member_meta[member]
        if team.endswith(" 2"):
            canonical_member = lookup.get((league, season, team[:-2]))
            if canonical_member:
                if psha(member_rows[member]) == psha(member_rows[canonical_member]):
                    exact_duplicates.append(
                        {"duplicate": member, "canonical": canonical_member}
                    )
                    continue
                duplicate_conflicts.append(
                    {
                        "duplicate": member,
                        "candidate_canonical": canonical_member,
                    }
                )
        logical_members.append(member)

    if duplicate_conflicts:
        raise FreezeError(f"qualified range has duplicate conflicts: {duplicate_conflicts}")

    rows = [row for member in logical_members for row in member_rows[member]]
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["league"], row["season_start"], row["date"])].append(row)

    paired_matches: list[dict] = []
    unpaired_rows: list[dict] = []
    ambiguous_rows: list[dict] = []
    for _, group in sorted(grouped.items()):
        homes = [row for row in group if row["h_a"] == "h"]
        aways = [row for row in group if row["h_a"] == "a"]
        candidates = {
            home_index: [
                away_index
                for away_index, away in enumerate(aways)
                if away["team"] != home["team"] and reciprocal(home, away)
            ]
            for home_index, home in enumerate(homes)
        }
        reverse_count = defaultdict(int)
        for away_indexes in candidates.values():
            for away_index in away_indexes:
                reverse_count[away_index] += 1

        used_ids: set[int] = set()
        for home_index, home in enumerate(homes):
            away_indexes = candidates[home_index]
            if len(away_indexes) == 1 and reverse_count[away_indexes[0]] == 1:
                away = aways[away_indexes[0]]
                used_ids.update((id(home), id(away)))
                paired_matches.append(
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
                ambiguous_rows.append(
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
            for row in ambiguous_rows
        }
        for row in group:
            key = (row["league"], row["season_start"], row["date"], row["team"])
            if id(row) not in used_ids and key not in ambiguous_keys:
                unpaired_rows.append(
                    {
                        field: row[field]
                        for field in ("league", "season_start", "date", "team", "h_a")
                    }
                )

    if ambiguous_rows or unpaired_rows or len(rows) != 2 * len(paired_matches):
        raise FreezeError(
            "qualified range is not internally complete: "
            f"ambiguous={len(ambiguous_rows)} unpaired={len(unpaired_rows)} "
            f"rows={len(rows)} pairs={len(paired_matches)}"
        )

    counts = defaultdict(int)
    for match in paired_matches:
        counts[match["league"]] += 1
    observed_counts = {competition: counts[competition] for competition in sorted(competitions)}
    expected_counts = {
        competition: int(value)
        for competition, value in cohort["expected_match_counts"].items()
    }
    if observed_counts != dict(sorted(expected_counts.items())):
        raise FreezeError(
            f"qualified cohort count drift: observed={observed_counts} expected={expected_counts}"
        )
    if len(paired_matches) != int(cohort["expected_total_match_count"]):
        raise FreezeError("qualified total match count drift")

    source_id = freeze["source"]["source_id"]
    revision = freeze["source"]["revision"]
    projection: list[dict] = []
    for match in paired_matches:
        projection.append(
            {
                "source_local_match_key": source_match_key(source_id, revision, match),
                "source_id": source_id,
                "source_revision": revision,
                "league": match["league"],
                "season_start": match["season_start"],
                "date": match["date"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "home_ppda": match["home_ppda"],
                "away_ppda": match["away_ppda"],
                "home_deep": match["home_deep"],
                "away_deep": match["away_deep"],
                "feature_input_sha256": feature_input_sha(match),
            }
        )
    projection.sort(
        key=lambda row: (
            row["season_start"],
            row["league"],
            row["date"],
            row["home_team"],
            row["away_team"],
        )
    )
    jsonl = b"\n".join(canonical(row) for row in projection) + b"\n"
    receipt = {
        "status": "REUSABLE_FEATURE_LAYER_FROZEN_PENDING_CANONICAL_ID_BINDING",
        "source_id": source_id,
        "source_repository": freeze["source"]["repository"],
        "source_revision": revision,
        "archive_blob_sha1": observed_blob,
        "permission_class": freeze["source"]["permission_class"],
        "production_eligible": False,
        "qualified_season_start_min": season_min,
        "qualified_season_start_max": season_max,
        "qualified_match_count": len(projection),
        "qualified_match_counts": observed_counts,
        "feature_projection_sha256": sha256(jsonl),
        "source_local_match_key_unique_count": len(
            {row["source_local_match_key"] for row in projection}
        ),
        "feature_input_sha256_unique_count": len(
            {row["feature_input_sha256"] for row in projection}
        ),
        "exact_duplicate_member_count_collapsed": len(exact_duplicates),
        "ppda_object_values_parsed": ppda_object_values,
        "safe_feature_fields": [
            "home_ppda",
            "away_ppda",
            "home_deep",
            "away_deep",
        ],
        "result_values_used": 0,
        "score_values_used": 0,
        "xg_values_used": 0,
        "canonical_match_id_binding_complete": False,
        "candidate_confirmation_allowed": False,
        "allowed_roles_after_canonical_binding": [
            "TRAIN",
            "DEVELOPMENT",
            "REUSABLE_BENCHMARK",
        ],
        "reported_benchmark_predictions_must_be_oof": True,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    if receipt["source_local_match_key_unique_count"] != len(projection):
        raise FreezeError("source-local match key collision")
    return projection, receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-lock", required=True)
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--projection-out", required=True)
    parser.add_argument("--receipt-out", required=True)
    args = parser.parse_args()

    source_lock = json.loads(Path(args.source_lock).read_text(encoding="utf-8"))
    freeze = json.loads(Path(args.freeze).read_text(encoding="utf-8"))
    archive = fetch_bytes(source_lock["source"]["raw_url"])
    projection, receipt = materialize(source_lock, freeze, archive)

    projection_path = Path(args.projection_out)
    projection_path.parent.mkdir(parents=True, exist_ok=True)
    projection_path.write_bytes(
        b"\n".join(canonical(row) for row in projection) + b"\n"
    )
    receipt_path = Path(args.receipt_out)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
