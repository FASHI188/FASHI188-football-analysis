#!/usr/bin/env python3
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import shutil
import tempfile
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/JaseZiv/worldfootballR_data/releases/tags/fotmob_match_details"
RELEASE_ID = 79989708
ASSET_NAMES = [
    "47_match_details.csv",
    "87_match_details.csv",
    "54_match_details.csv",
    "55_match_details.csv",
    "53_match_details.csv",
    "130_match_details.csv",
]
TARGET_N = 5000
MIN_XG_SHOTS = 6
HIGH_XG = 0.20
SEED_PREFIX = "C072N18A2_FOTMOB_HISTORY5000"
OUTDIR = Path("football-data/research/_n18a2_fotmob_history5000")

# Frozen schema aliases from the preregistered acquisition contract.
XG_ALIASES = ("expected_goals", "expectedGoals")
MINUTE_ALIASES = ("min", "minute")
ADDED_ALIASES = ("min_added", "minAdded")
SHOT_TYPE_ALIASES = ("shot_type", "shotType")
REQUIRED = ("match_id", "league_id", "parent_league_season", "match_time_utc", "home_team_id", "away_team_id", "team_id")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def first_existing(fieldnames, aliases):
    for x in aliases:
        if x in fieldnames:
            return x
    return None


def to_int(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        x = float(v)
        if not math.isfinite(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def parse_match_time(s):
    if not s:
        return None
    s = str(s).strip()
    formats = (
        "%a, %b %d, %Y, %H:%M UTC",
        "%a, %b %d, %Y, %I:%M %p UTC",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            d = dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
            return d.isoformat()
        except ValueError:
            pass
    return None


def rank_key(match_id):
    token = f"{SEED_PREFIX}|{match_id}".encode("utf-8")
    return hashlib.sha256(token).hexdigest()


def fetch_release():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "football3-n18a2-static-acquisition/1.0",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(RELEASE_API, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        obj = json.load(resp)
    if int(obj.get("id") or -1) != RELEASE_ID:
        raise RuntimeError(f"STOP_SOURCE_IDENTITY release_id={obj.get('id')} expected={RELEASE_ID}")
    by_name = {a.get("name"): a for a in obj.get("assets", [])}
    missing = [n for n in ASSET_NAMES if n not in by_name]
    if missing:
        raise RuntimeError(f"STOP_SOURCE_IDENTITY missing_assets={missing}")
    return obj, [by_name[n] for n in ASSET_NAMES]


def download_asset(asset, dest):
    url = asset.get("browser_download_url")
    if not url:
        raise RuntimeError(f"STOP_SOURCE_IDENTITY no browser_download_url for {asset.get('name')}")
    req = urllib.request.Request(url, headers={"User-Agent": "football3-n18a2-static-acquisition/1.0"})
    with urllib.request.urlopen(req, timeout=120) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)


def validate_schema(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            fields = next(reader)
        except StopIteration:
            raise RuntimeError(f"STOP_SCHEMA empty_file={path.name}")
    missing = [x for x in REQUIRED if x not in fields]
    xg_col = first_existing(fields, XG_ALIASES)
    minute_col = first_existing(fields, MINUTE_ALIASES)
    added_col = first_existing(fields, ADDED_ALIASES)
    shot_type_col = first_existing(fields, SHOT_TYPE_ALIASES)
    if missing or not xg_col:
        raise RuntimeError(
            f"STOP_SCHEMA file={path.name} missing_required={missing} xg_col={xg_col} fields={fields}"
        )
    return {
        "fieldnames": fields,
        "xg_col": xg_col,
        "minute_col": minute_col,
        "added_col": added_col,
        "shot_type_col": shot_type_col,
    }


def main():
    started = utc_now()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    release, assets = fetch_release()

    raw_receipts = []
    all_source_ids = set()
    match_meta = {}
    shot_rows_by_match = defaultdict(list)
    schema_by_asset = {}
    source_row_count = 0

    with tempfile.TemporaryDirectory(prefix="football3_n18a2_") as td:
        tdir = Path(td)
        for asset in assets:
            name = asset["name"]
            local = tdir / name
            print(f"DOWNLOAD {name} declared_bytes={asset.get('size')}", flush=True)
            download_asset(asset, local)
            actual_bytes = local.stat().st_size
            digest = sha256_file(local)
            if int(asset.get("size") or 0) and actual_bytes != int(asset.get("size")):
                raise RuntimeError(
                    f"STOP_SOURCE_IDENTITY size_mismatch file={name} declared={asset.get('size')} actual={actual_bytes}"
                )
            schema = validate_schema(local)
            schema_by_asset[name] = {
                "xg_col": schema["xg_col"],
                "minute_col": schema["minute_col"],
                "added_col": schema["added_col"],
                "shot_type_col": schema["shot_type_col"],
            }
            raw_receipts.append({
                "asset_id": asset.get("id"),
                "name": name,
                "declared_size": asset.get("size"),
                "downloaded_size": actual_bytes,
                "sha256": digest,
                "updated_at": asset.get("updated_at"),
                "browser_download_url": asset.get("browser_download_url"),
            })

            with open(local, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    source_row_count += 1
                    mid = to_int(row.get("match_id"))
                    hid = to_int(row.get("home_team_id"))
                    aid = to_int(row.get("away_team_id"))
                    tid = to_int(row.get("team_id"))
                    league_id = to_int(row.get("league_id"))
                    when = parse_match_time(row.get("match_time_utc"))
                    xg = to_float(row.get(schema["xg_col"]))
                    if mid is None:
                        continue
                    all_source_ids.add(mid)
                    if mid not in match_meta:
                        match_meta[mid] = {
                            "match_id": mid,
                            "league_id": league_id,
                            "league_name": row.get("league_name"),
                            "season": row.get("parent_league_season"),
                            "match_time_utc": when,
                            "home_team_id": hid,
                            "away_team_id": aid,
                        }
                    if None in (hid, aid, tid) or when is None or xg is None or not (0.0 <= xg <= 1.5):
                        continue
                    if tid == hid:
                        is_home = True
                    elif tid == aid:
                        is_home = False
                    else:
                        continue
                    shot_rows_by_match[mid].append({
                        "match_id": mid,
                        "is_home": is_home,
                        "xg": xg,
                        "x": to_float(row.get("x")),
                        "y": to_float(row.get("y")),
                        "minute": to_int(row.get(schema["minute_col"])) if schema["minute_col"] else None,
                        "added_time": to_int(row.get(schema["added_col"])) if schema["added_col"] else None,
                        "situation": row.get("situation"),
                        "shot_type": row.get(schema["shot_type_col"]) if schema["shot_type_col"] else None,
                    })
            print(f"PARSED {name} cumulative_source_matches={len(all_source_ids)} rows={source_row_count}", flush=True)

        usable = []
        for mid, meta in match_meta.items():
            shots = shot_rows_by_match.get(mid, [])
            home = [r for r in shots if r["is_home"]]
            away = [r for r in shots if not r["is_home"]]
            if (
                meta.get("home_team_id") is None
                or meta.get("away_team_id") is None
                or meta.get("match_time_utc") is None
                or len(shots) < MIN_XG_SHOTS
                or not home
                or not away
            ):
                continue
            usable.append(mid)

        usable.sort(key=lambda mid: (rank_key(mid), mid))
        if len(usable) < TARGET_N:
            summary = {
                "project": "football3",
                "experiment": "C072-N18A2",
                "status": "STOP_COVERAGE",
                "started_at_utc": started,
                "finished_at_utc": utc_now(),
                "release_id": RELEASE_ID,
                "source_match_count": len(all_source_ids),
                "usable_match_count": len(usable),
                "target_match_count": TARGET_N,
                "source_shot_rows": source_row_count,
                "asset_receipts": raw_receipts,
                "schema_by_asset": schema_by_asset,
                "persisted_outcome_fields": 0,
                "model_fits": 0,
                "target_scores": 0,
            }
            (OUTDIR / "fotmob_n18a2_history5000_summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
            )
            raise SystemExit(f"STOP_COVERAGE usable={len(usable)} target={TARGET_N}")

        selected = usable[:TARGET_N]
        selected_set = set(selected)

        matches_path = OUTDIR / "fotmob_n18a2_history5000_matches.jsonl.gz"
        shots_path = OUTDIR / "fotmob_n18a2_history5000_shots.jsonl.gz"
        all_ids_path = OUTDIR / "fotmob_n18a2_all_source_match_ids.txt.gz"

        selected_shot_count = 0
        league_counts = Counter()
        season_counts = Counter()
        dates = []

        with gzip.open(matches_path, "wt", encoding="utf-8", compresslevel=6) as mf, gzip.open(
            shots_path, "wt", encoding="utf-8", compresslevel=6
        ) as sf:
            for mid in selected:
                meta = match_meta[mid]
                shots = shot_rows_by_match[mid]
                home_xg = [r["xg"] for r in shots if r["is_home"]]
                away_xg = [r["xg"] for r in shots if not r["is_home"]]

                def stats(xs):
                    n = len(xs)
                    s = sum(xs)
                    mean = s / n if n else None
                    var = sum((x - mean) ** 2 for x in xs) / n if n else None
                    return {
                        "count": n,
                        "sum": s,
                        "mean": mean,
                        "variance": var,
                        "high_xg_count": sum(1 for x in xs if x >= HIGH_XG),
                    }

                outm = dict(meta)
                outm["selection_hash"] = rank_key(mid)
                outm["home_xg"] = stats(home_xg)
                outm["away_xg"] = stats(away_xg)
                outm["numeric_xg_shots"] = len(shots)
                mf.write(json.dumps(outm, ensure_ascii=False, sort_keys=True) + "\n")
                for r in shots:
                    sf.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
                    selected_shot_count += 1

                league_counts[str(meta.get("league_id"))] += 1
                season_counts[f"{meta.get('league_id')}|{meta.get('season')}"] += 1
                if meta.get("match_time_utc"):
                    dates.append(meta["match_time_utc"])

        with gzip.open(all_ids_path, "wt", encoding="utf-8", compresslevel=6) as af:
            for mid in sorted(all_source_ids):
                af.write(f"{mid}\n")

        summary = {
            "project": "football3",
            "experiment": "C072-N18A2",
            "status": "PASS_FOTMOB_STATIC_HISTORY5000",
            "role": "HISTORY_FEATURE_SOURCE_ONLY_ALL_SOURCE_IDS_GLOBALLY_CONSUMED_AS_TARGET_IDENTITIES",
            "started_at_utc": started,
            "finished_at_utc": utc_now(),
            "source_repository": "JaseZiv/worldfootballR_data",
            "release_id": RELEASE_ID,
            "release_tag": release.get("tag_name"),
            "release_published_at": release.get("published_at"),
            "frozen_assets": ASSET_NAMES,
            "asset_receipts": raw_receipts,
            "schema_by_asset": schema_by_asset,
            "source_shot_rows": source_row_count,
            "source_match_count": len(all_source_ids),
            "usable_match_count": len(usable),
            "selected_match_count": len(selected),
            "selected_shot_count": selected_shot_count,
            "competition_counts": dict(sorted(league_counts.items())),
            "competition_season_counts": dict(sorted(season_counts.items())),
            "first_match_time_utc": min(dates) if dates else None,
            "last_match_time_utc": max(dates) if dates else None,
            "selection_rule": "SHA256(C072N18A2_FOTMOB_HISTORY5000|match_id), first 5000 usable",
            "usable_gate": ">=6 finite xG shots, >=1 each side, valid IDs/time",
            "persisted_outcome_fields": 0,
            "model_fits": 0,
            "target_scores": 0,
            "matches_sha256": sha256_file(matches_path),
            "shots_sha256": sha256_file(shots_path),
            "all_source_ids_sha256": sha256_file(all_ids_path),
            "matches_bytes": matches_path.stat().st_size,
            "shots_bytes": shots_path.stat().st_size,
            "all_source_ids_bytes": all_ids_path.stat().st_size,
        }
        summary_path = OUTDIR / "fotmob_n18a2_history5000_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), flush=True)

        if len(selected_set) != TARGET_N:
            raise RuntimeError("internal selection cardinality error")


if __name__ == "__main__":
    main()
