#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE_ID = "OPENFOOTBALL_FOOTBALL_JSON"
SCHEMA = "football3-nova-n1-mhsendur-openfootball-canonical-binding-v1"


class BindingError(ValueError):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def stable_team_id(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise BindingError("team name must be non-empty")
    return f"openfootball:team:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:20]}"


def surface_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def build_alias_maps(binding: dict) -> tuple[dict[str, str], dict[str, str]]:
    mhsendur: dict[str, str] = {}
    openfootball: dict[str, str] = {}
    for group in binding.get("team_alias_groups", []):
        canonical_key = surface_key(group.get("canonical"))
        if not canonical_key:
            raise BindingError("alias group canonical key must be non-empty")
        for origin, target in (("mhsendur", mhsendur), ("openfootball", openfootball)):
            names = group.get(origin, [])
            if not isinstance(names, list) or not names:
                raise BindingError(f"alias group {group.get('canonical')!r} missing {origin} names")
            for name in names:
                key = surface_key(name)
                prior = target.get(key)
                if prior is not None and prior != canonical_key:
                    raise BindingError(f"ambiguous {origin} alias {name!r}: {prior} vs {canonical_key}")
                target[key] = canonical_key
    return mhsendur, openfootball


def team_key(name: str, origin: str, alias_maps: tuple[dict[str, str], dict[str, str]]) -> str:
    key = surface_key(name)
    if not key:
        raise BindingError("empty normalized team name")
    mapping = alias_maps[0] if origin == "mhsendur" else alias_maps[1]
    return mapping.get(key, key)


def season_label(start: int) -> str:
    return f"{start}/{str(start + 1)[2:]}"


def season_path(start: int, file_code: str) -> str:
    return f"{start}-{str(start + 1)[2:]}/{file_code}"


def kickoff_utc(date_s: str, time_s: str, tz_name: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_s or "")):
        raise BindingError(f"invalid date {date_s!r}")
    time_text = str(time_s or "").strip()
    if re.fullmatch(r"\d{2}:\d{2}", time_text):
        time_text += ":00"
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_text):
        raise BindingError(f"invalid time {time_s!r}")
    local = datetime.fromisoformat(f"{date_s}T{time_text}").replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def mhsendur_kickoff_utc(value: str, tz_name: str) -> str:
    text = str(value or "").strip()
    try:
        naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise BindingError(f"invalid mhsendur date {value!r}") from exc
    return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_sources(openfootball_lock: dict, freeze: dict, freeze_receipt: dict, binding: dict) -> None:
    if binding.get("schema_version") != SCHEMA:
        raise BindingError("unexpected binding schema_version")
    src = openfootball_lock.get("source", {})
    if src.get("source_id") != SOURCE_ID:
        raise BindingError("unexpected OpenFootball source_id")
    if src.get("license_id") != "CC0-1.0":
        raise BindingError("OpenFootball source must remain CC0-1.0")
    if not re.fullmatch(r"[0-9a-f]{40}", str(src.get("revision", ""))):
        raise BindingError("OpenFootball revision must be frozen 40-hex commit")
    if binding.get("openfootball_revision") != src.get("revision"):
        raise BindingError("binding/OpenFootball revision drift")
    cohort = freeze.get("qualified_feature_cohorts", {})
    if int(binding.get("season_start_min", -1)) != int(cohort.get("season_start_min", -2)):
        raise BindingError("season_start_min drift")
    if int(binding.get("season_start_max", -1)) != int(cohort.get("season_start_max", -2)):
        raise BindingError("season_start_max drift")
    expected = {k: int(v) for k, v in cohort.get("expected_match_counts", {}).items()}
    if binding.get("expected_match_counts") != expected:
        raise BindingError("expected match counts drift")
    if int(binding.get("expected_total_match_count", -1)) != int(cohort.get("expected_total_match_count", -2)):
        raise BindingError("expected total match count drift")
    if int(freeze_receipt.get("qualified_match_count", -1)) != int(binding["expected_total_match_count"]):
        raise BindingError("materialized freeze receipt count drift")
    if freeze_receipt.get("status") != "REUSABLE_FEATURE_LAYER_FROZEN_PENDING_CANONICAL_ID_BINDING":
        raise BindingError("feature layer is not in pending canonical binding state")
    if any(bool(binding.get("governance", {}).get(k)) for k in ("formal_v2_changed", "current_changed", "production_changed")):
        raise BindingError("formal state changes forbidden")
    comps = binding.get("competitions")
    if not isinstance(comps, dict) or set(comps) != set(expected):
        raise BindingError("competition mapping must exactly cover frozen cohorts")
    build_alias_maps(binding)


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Football3-Nova-Canonical-Binding/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def download_openfootball_payloads(openfootball_lock: dict, binding: dict) -> dict[str, bytes]:
    src = openfootball_lock["source"]
    base = src["raw_base_url"].rstrip("/")
    revision = src["revision"]
    out: dict[str, bytes] = {}
    for league, comp in sorted(binding["competitions"].items()):
        for start in range(int(binding["season_start_min"]), int(binding["season_start_max"]) + 1):
            path = season_path(start, comp["file_code"])
            out[path] = fetch_bytes(f"{base}/{revision}/{path}")
    return out


def normalize_openfootball_match(match: dict, comp: dict, season_start: int, path: str, revision: str, raw_sha: str) -> dict:
    required = ("date", "time", "team1", "team2")
    missing = [k for k in required if k not in match or not str(match.get(k, "")).strip()]
    if missing:
        raise BindingError(f"{path}: OpenFootball match missing identity fields {missing}")
    home = str(match["team1"]).strip()
    away = str(match["team2"]).strip()
    if not home or not away or home == away:
        raise BindingError(f"{path}: invalid team identity")
    kickoff = kickoff_utc(match["date"], match["time"], comp["timezone"])
    identity_basis = {
        "source_id": SOURCE_ID,
        "source_revision": revision,
        "source_path": path,
        "competition_id": comp["competition_id"],
        "season": season_label(season_start),
        "date": match["date"],
        "time": match["time"],
        "home_team": home,
        "away_team": away,
    }
    return {
        "match_id": f"openfootball:{sha256_hex(canonical(identity_basis))}",
        "competition_id": comp["competition_id"],
        "season": season_label(season_start),
        "source_date": str(match["date"]),
        "kickoff": kickoff,
        "home_team_id": stable_team_id(home),
        "away_team_id": stable_team_id(away),
        "home_team_name": home,
        "away_team_name": away,
        "source_id": SOURCE_ID,
        "source_revision": revision,
        "source_path": path,
        "input_sha256": raw_sha,
    }


def incomplete_identity_fields(match: dict) -> list[str]:
    return [
        field for field in ("date", "time", "team1", "team2")
        if field not in match or not str(match.get(field, "")).strip()
    ]


def build_openfootball_catalog(openfootball_lock: dict, binding: dict, payloads: dict[str, bytes]) -> tuple[list[dict], list[dict]]:
    revision = openfootball_lock["source"]["revision"]
    rows: list[dict] = []
    receipts: list[dict] = []
    for league, comp in sorted(binding["competitions"].items()):
        for start in range(int(binding["season_start_min"]), int(binding["season_start_max"]) + 1):
            path = season_path(start, comp["file_code"])
            raw = payloads.get(path)
            if raw is None:
                raise BindingError(f"missing OpenFootball payload {path}")
            raw_sha = sha256_hex(raw)
            obj = json.loads(raw.decode("utf-8"))
            matches = obj.get("matches")
            if not isinstance(matches, list):
                raise BindingError(f"{path}: matches must be list")
            file_rows: list[dict] = []
            excluded_incomplete: list[dict] = []
            for match in matches:
                missing = incomplete_identity_fields(match)
                if missing:
                    if missing != ["time"]:
                        raise BindingError(f"{path}: OpenFootball match missing identity fields {missing}")
                    excluded_incomplete.append({
                        "date": str(match.get("date", "")),
                        "team1": str(match.get("team1", "")),
                        "team2": str(match.get("team2", "")),
                        "missing_identity_fields": missing,
                    })
                    continue
                file_rows.append(normalize_openfootball_match(match, comp, start, path, revision, raw_sha))
            rows.extend(file_rows)
            receipts.append({
                "league": league,
                "season_start": start,
                "path": path,
                "source_blob_sha1": git_blob_sha1(raw),
                "input_sha256": raw_sha,
                "source_match_object_count": len(matches),
                "match_count": len(file_rows),
                "identity_incomplete_excluded_count": len(excluded_incomplete),
                "identity_incomplete_excluded_sample": excluded_incomplete[:20],
            })
    ids = [row["match_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise BindingError("OpenFootball canonical match_id collision")
    return rows, receipts


def projection_sha(projection: list[dict]) -> str:
    return sha256_hex(b"\n".join(canonical(row) for row in projection) + b"\n")


def verify_projection(projection: list[dict], freeze_receipt: dict, binding: dict) -> None:
    if len(projection) != int(binding["expected_total_match_count"]):
        raise BindingError("feature projection match count drift")
    observed_sha = projection_sha(projection)
    if observed_sha != freeze_receipt.get("feature_projection_sha256"):
        raise BindingError(
            "feature projection SHA does not match same-run freeze receipt: "
            f"observed={observed_sha} receipt={freeze_receipt.get('feature_projection_sha256')}"
        )
    counts = Counter(row.get("league") for row in projection)
    if dict(sorted(counts.items())) != dict(sorted(binding["expected_match_counts"].items())):
        raise BindingError(f"feature projection competition counts drift: {dict(counts)}")
    source_keys = set()
    feature_shas = set()
    for row in projection:
        for field in (
            "source_local_match_key", "source_id", "source_revision", "league", "season_start", "date",
            "home_team", "away_team", "home_ppda", "away_ppda", "home_deep", "away_deep", "feature_input_sha256",
        ):
            if field not in row:
                raise BindingError(f"feature projection missing {field}")
        source_keys.add(row["source_local_match_key"])
        feature_shas.add(row["feature_input_sha256"])
    if len(source_keys) != len(projection) or len(feature_shas) != len(projection):
        raise BindingError("feature projection identity collision")


def bind_projection(projection: list[dict], source_rows: list[dict], binding: dict) -> tuple[list[dict], dict]:
    alias_maps = build_alias_maps(binding)
    comp_by_league = binding["competitions"]
    exact_index: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    date_team_index: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    source_by_date: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in source_rows:
        season = row["season"]
        source_date = row.get("source_date") or str(row["kickoff"])[:10]
        home_key = team_key(row["home_team_name"], "openfootball", alias_maps)
        away_key = team_key(row["away_team_name"], "openfootball", alias_maps)
        exact_index[(row["competition_id"], season, row["kickoff"], home_key, away_key)].append(row)
        date_team_index[(row["competition_id"], season, source_date, home_key, away_key)].append(row)
        source_by_date[(row["competition_id"], season, source_date)].append(row)

    bound: list[dict] = []
    failures: list[dict] = []
    used_match_ids: set[str] = set()
    binding_modes: Counter[str] = Counter()
    for feat in projection:
        league = feat["league"]
        comp = comp_by_league.get(league)
        if comp is None:
            raise BindingError(f"unsupported feature league {league!r}")
        season = season_label(int(feat["season_start"]))
        feature_date = str(feat["date"])[:10]
        feature_kickoff = mhsendur_kickoff_utc(feat["date"], binding["mhsendur_timezone"])
        home_key = team_key(feat["home_team"], "mhsendur", alias_maps)
        away_key = team_key(feat["away_team"], "mhsendur", alias_maps)
        exact_candidates = exact_index.get((comp["competition_id"], season, feature_kickoff, home_key, away_key), [])
        if len(exact_candidates) > 1:
            candidates = exact_candidates
            mode = "AMBIGUOUS_EXACT_KICKOFF"
        elif len(exact_candidates) == 1:
            candidates = exact_candidates
            mode = "EXACT_KICKOFF_TEAM"
        else:
            candidates = date_team_index.get((comp["competition_id"], season, feature_date, home_key, away_key), [])
            mode = "UNIQUE_DATE_TEAM_FALLBACK" if len(candidates) == 1 else "UNMATCHED_OR_AMBIGUOUS_DATE_TEAM"
        if len(candidates) != 1:
            same_date = source_by_date.get((comp["competition_id"], season, feature_date), [])
            failures.append({
                "league": league,
                "season_start": feat["season_start"],
                "date": feat["date"],
                "feature_kickoff": feature_kickoff,
                "home_team": feat["home_team"],
                "away_team": feat["away_team"],
                "home_key": home_key,
                "away_key": away_key,
                "candidate_count": len(candidates),
                "binding_mode": mode,
                "same_date_source_pairs": [
                    {
                        "kickoff": r["kickoff"],
                        "home_team": r["home_team_name"],
                        "away_team": r["away_team_name"],
                        "home_key": team_key(r["home_team_name"], "openfootball", alias_maps),
                        "away_key": team_key(r["away_team_name"], "openfootball", alias_maps),
                    }
                    for r in same_date[:20]
                ],
            })
            continue
        src = candidates[0]
        if src["match_id"] in used_match_ids:
            failures.append({
                "league": league,
                "date": feat["date"],
                "home_team": feat["home_team"],
                "away_team": feat["away_team"],
                "candidate_count": 1,
                "binding_mode": mode,
                "reason": "canonical match_id reused by multiple feature rows",
                "match_id": src["match_id"],
            })
            continue
        used_match_ids.add(src["match_id"])
        binding_modes[mode] += 1
        bound.append({
            "match_id": src["match_id"],
            "competition_id": src["competition_id"],
            "season": src["season"],
            "kickoff": src["kickoff"],
            "feature_kickoff": feature_kickoff,
            "binding_mode": mode,
            "home_team_id": src["home_team_id"],
            "away_team_id": src["away_team_id"],
            "source_id": src["source_id"],
            "source_revision": src["source_revision"],
            "input_sha256": src["input_sha256"],
            "source_path": src["source_path"],
            "source_local_match_key": feat["source_local_match_key"],
            "feature_source_id": feat["source_id"],
            "feature_source_revision": feat["source_revision"],
            "feature_input_sha256": feat["feature_input_sha256"],
            "home_ppda": feat["home_ppda"],
            "away_ppda": feat["away_ppda"],
            "home_deep": feat["home_deep"],
            "away_deep": feat["away_deep"],
        })

    if failures:
        raise BindingError(
            f"canonical binding fail-closed: failures={len(failures)} sample="
            + json.dumps(failures[:30], ensure_ascii=False, sort_keys=True)
        )
    if len(bound) != len(projection) or len(used_match_ids) != len(projection):
        raise BindingError("canonical binding is not one-to-one complete")
    bound.sort(key=lambda row: (row["feature_kickoff"], row["competition_id"], row["match_id"]))
    jsonl = b"\n".join(canonical(row) for row in bound) + b"\n"
    counts = Counter(row["competition_id"] for row in bound)
    receipt = {
        "status": "CANONICAL_MATCH_ID_BINDING_PASS",
        "canonical_match_id_binding_complete": True,
        "bound_match_count": len(bound),
        "bound_match_id_unique_count": len(used_match_ids),
        "binding_projection_sha256": sha256_hex(jsonl),
        "per_competition_bound_match_counts": dict(sorted(counts.items())),
        "binding_mode_counts": dict(sorted(binding_modes.items())),
        "binding_basis": ["competition", "season", "predeclared_team_alias", "exact_kickoff_or_unique_calendar_date"],
        "ambiguity_policy": "FAIL_CLOSED",
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "score_values_used": 0,
        "result_values_used": 0,
        "xg_values_used": 0,
        "candidate_roles_assigned": 0,
        "allowed_roles_after_canonical_binding": ["TRAIN", "DEVELOPMENT", "REUSABLE_BENCHMARK"],
        "candidate_confirmation_allowed": False,
        "reported_benchmark_predictions_must_be_oof": True,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }
    return bound, receipt


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise BindingError(f"{path}:{line_no}: invalid JSON") from exc
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openfootball-lock", required=True)
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--freeze-receipt", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--projection", required=True)
    parser.add_argument("--binding-out", required=True)
    parser.add_argument("--receipt-out", required=True)
    args = parser.parse_args()

    openfootball_lock = json.loads(Path(args.openfootball_lock).read_text(encoding="utf-8"))
    freeze = json.loads(Path(args.freeze).read_text(encoding="utf-8"))
    freeze_receipt = json.loads(Path(args.freeze_receipt).read_text(encoding="utf-8"))
    binding = json.loads(Path(args.binding).read_text(encoding="utf-8"))
    validate_sources(openfootball_lock, freeze, freeze_receipt, binding)
    projection = read_jsonl(Path(args.projection))
    verify_projection(projection, freeze_receipt, binding)
    source_rows, file_receipts = build_openfootball_catalog(
        openfootball_lock, binding, download_openfootball_payloads(openfootball_lock, binding)
    )
    if len(source_rows) != int(binding["expected_total_match_count"]):
        raise BindingError(
            f"OpenFootball historical cohort count drift: observed={len(source_rows)} "
            f"expected={binding['expected_total_match_count']}"
        )
    bound, receipt = bind_projection(projection, source_rows, binding)
    receipt.update({
        "schema_version": SCHEMA + "-receipt",
        "openfootball_source_revision": openfootball_lock["source"]["revision"],
        "openfootball_license_id": openfootball_lock["source"]["license_id"],
        "openfootball_file_count": len(file_receipts),
        "openfootball_match_count": len(source_rows),
        "openfootball_identity_incomplete_excluded_count": sum(
            int(item.get("identity_incomplete_excluded_count", 0)) for item in file_receipts
        ),
        "openfootball_file_receipts": file_receipts,
        "feature_projection_sha256": freeze_receipt["feature_projection_sha256"],
        "feature_source_revision": freeze_receipt["source_revision"],
    })
    out = Path(args.binding_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\n".join(canonical(row) for row in bound) + b"\n")
    rpath = Path(args.receipt_out)
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
