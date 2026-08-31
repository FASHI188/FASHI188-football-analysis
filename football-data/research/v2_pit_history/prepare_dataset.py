from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATE_RE = re.compile(
    r"^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})(?:\s+(?P<year>\d{4}))?\s*$"
)
MATCHDAY_RE = re.compile(r"^\s*[▪#]?\s*Matchday\s+(?P<round>\d+)\s*$", re.I)
# Newer Football.TXT layout: [time] HOME  2-1 (1-0)  AWAY
MIDDLE_RE = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}:\d{2})\s+)?"
    r"(?P<home>.+?)\s{2,}(?P<hg>\d+)-(?P<ag>\d+)"
    r"(?:\s+\(\d+-\d+\))?\s{2,}(?P<away>.+?)\s*$"
)
# Older France/OpenFootball layout: [time] HOME v AWAY  2-1 (1-0).
# Long team names can leave only one space around the literal v separator.
V_RE = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}:\d{2})\s+)?"
    r"(?P<home>.+?)\s+v\s+(?P<away>.+?)\s{2,}"
    r"(?P<hg>\d+)-(?P<ag>\d+)(?:\s+\(\d+-\d+\))?\s*$",
    re.I,
)
MONTH = {m: i for i, m in enumerate(
    ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"), 1
)}
LABEL_FIELDS = {"home_goals", "away_goals", "result", "score", "ft_score"}


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def team_token(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    text = " ".join(text.split())
    return text


def team_id(name: str) -> str:
    return "team_" + hashlib.sha256(team_token(name).encode("utf-8")).hexdigest()[:20]


def fixture_id(comp: str, season: str, cutoff: str, home_id: str, away_id: str) -> str:
    raw = "|".join((comp, season, cutoff, home_id, away_id)).encode("utf-8")
    return "pit_" + hashlib.sha256(raw).hexdigest()[:24]


def season_year(season: str, month: int) -> int:
    start = int(season[:4])
    return start if month >= 7 else start + 1


def parse_file(path: Path, source: dict, season: str) -> tuple[list[dict], dict]:
    tz = ZoneInfo(source["timezone"])
    rows: list[dict] = []
    current_date = None
    inherited_time = None
    round_index = None
    skipped = Counter()

    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.rstrip()
        md = MATCHDAY_RE.match(line)
        if md:
            round_index = int(md.group("round"))
            continue
        dm = DATE_RE.match(line)
        if dm:
            month = MONTH[dm.group("month")]
            year = int(dm.group("year")) if dm.group("year") else season_year(season, month)
            current_date = (year, month, int(dm.group("day")))
            inherited_time = None
            continue
        if not line.strip() or line.lstrip().startswith(("#", "=", "▪")):
            continue

        mm = V_RE.match(line) or MIDDLE_RE.match(line)
        if not mm:
            # Only count plausible match-like lines; headers/tables are ignored.
            if re.search(r"\d+-\d+", line) or re.search(r"\bv\b", line, re.I):
                skipped["unparsed_match_like"] += 1
            continue
        if current_date is None:
            skipped["missing_date"] += 1
            continue
        clock = mm.group("time")
        if clock:
            inherited_time = clock
        elif inherited_time:
            clock = inherited_time
        else:
            skipped["missing_time"] += 1
            continue

        hour, minute = map(int, clock.split(":"))
        if hour > 23 or minute > 59:
            skipped["invalid_time"] += 1
            continue
        local = datetime(*current_date, hour, minute, tzinfo=tz)
        kickoff = local.astimezone(timezone.utc)
        cutoff = kickoff.isoformat()
        home = " ".join(mm.group("home").split())
        away = " ".join(mm.group("away").split())
        if not home or not away or team_token(home) == team_token(away):
            skipped["bad_identity"] += 1
            continue
        hg, ag = int(mm.group("hg")), int(mm.group("ag"))
        hid, aid = team_id(home), team_id(away)
        fid = fixture_id(source["competition_id"], season, cutoff, hid, aid)
        rows.append({
            "fixture_id": fid,
            "competition_id": source["competition_id"],
            "country": source["country"],
            "season": season,
            "cutoff": cutoff,
            "result_available_at": (kickoff + timedelta(hours=3)).isoformat(),
            "home_team": home,
            "away_team": away,
            "home_team_id": hid,
            "away_team_id": aid,
            "round_index": round_index,
            "home_goals": hg,
            "away_goals": ag,
            "source_path": f'{source["repo"]}/{path.as_posix().split(source["competition_id"] + "/", 1)[-1] if source["competition_id"] + "/" in path.as_posix() else path.name}',
            "source_line": lineno,
        })
    return rows, dict(skipped)


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return sha256_file(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources-lock", required=True)
    ap.add_argument("--source-root", required=True, help="directory containing one checkout per competition_id")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    lock_path = Path(args.sources_lock)
    source_root = Path(args.source_root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    dev_seasons = set(lock["development_seasons"])
    eval_seasons = set(lock["evaluation_seasons"])

    all_rows: list[dict] = []
    source_audit: list[dict] = []
    alias_map: dict[str, dict[str, str]] = defaultdict(dict)
    raw_sha_records = []

    for src in lock["sources"]:
        checkout = source_root / src["competition_id"]
        if not checkout.is_dir():
            raise RuntimeError(f"missing source checkout: {checkout}")
        license_path = checkout / src["license_path"]
        if not license_path.is_file():
            raise RuntimeError(f"missing license: {license_path}")
        license_text = license_path.read_text(encoding="utf-8", errors="replace").casefold()
        if not (
            "cc0" in license_text
            or "creative commons zero" in license_text
            or "public domain" in license_text
            or "publicdomain" in license_text
        ):
            raise RuntimeError(f"license gate failed for {src['repo']}")
        for season in sorted(dev_seasons | eval_seasons):
            rel = src["path_template"].format(season=season)
            path = checkout / rel
            if not path.is_file():
                raise RuntimeError(f"missing locked source file: {src['repo']}@{src['commit']}:{rel}")
            raw_sha = sha256_file(path)
            rows, skipped = parse_file(path, src, season)
            expected = int(src["expected"][season])
            coverage = len(rows) / max(1, expected)
            if coverage < 0.90:
                raise RuntimeError(
                    f"PIT parse coverage below 90%: {src['competition_id']} {season} "
                    f"parsed={len(rows)} expected={expected} skipped={skipped}"
                )
            for r in rows:
                alias_map[src["competition_id"]][r["home_team"]] = r["home_team_id"]
                alias_map[src["competition_id"]][r["away_team"]] = r["away_team_id"]
            all_rows.extend(rows)
            raw_sha_records.append({
                "competition_id": src["competition_id"], "season": season, "path": rel,
                "sha256": raw_sha, "bytes": path.stat().st_size,
            })
            source_audit.append({
                "competition_id": src["competition_id"], "season": season, "repo": src["repo"],
                "commit": src["commit"], "path": rel, "raw_sha256": raw_sha,
                "parsed": len(rows), "expected": expected, "coverage": coverage, "skipped": skipped,
                "license_sha256": sha256_file(license_path),
            })

    all_rows.sort(key=lambda r: (r["cutoff"], r["competition_id"], r["season"], r["fixture_id"]))
    dedup: dict[str, dict] = {}
    for row in all_rows:
        old = dedup.get(row["fixture_id"])
        if old is None:
            dedup[row["fixture_id"]] = row
        elif (old["home_goals"], old["away_goals"]) != (row["home_goals"], row["away_goals"]):
            raise RuntimeError(f"identity label conflict: {row['fixture_id']}")
    all_rows = sorted(dedup.values(), key=lambda r: (r["cutoff"], r["competition_id"], r["fixture_id"]))

    development = [r for r in all_rows if r["season"] in dev_seasons]
    evaluation = [r for r in all_rows if r["season"] in eval_seasons]
    if len(evaluation) < int(lock["minimum_evaluation_matches"]):
        raise RuntimeError(f"evaluation sample below hard gate: {len(evaluation)}")

    dev_rows = development
    eval_features = []
    eval_labels = []
    for r in evaluation:
        feature = {k: v for k, v in r.items() if k not in LABEL_FIELDS and k not in {"result_available_at"}}
        if LABEL_FIELDS.intersection(feature):
            raise RuntimeError("label-like field leaked into evaluation feature file")
        eval_features.append(feature)
        eval_labels.append({
            "fixture_id": r["fixture_id"],
            "cutoff": r["cutoff"],
            "result_available_at": r["result_available_at"],
            "home_goals": r["home_goals"],
            "away_goals": r["away_goals"],
        })

    dev_sha = write_jsonl(out / "development.jsonl", dev_rows)
    feature_sha = write_jsonl(out / "evaluation_features.jsonl", eval_features)
    label_sha = write_jsonl(out / "evaluation_label_vault.jsonl", eval_labels)
    alias_path = out / "alias_map.json"
    alias_path.write_text(json.dumps(alias_map, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    alias_sha = sha256_file(alias_path)
    raw_set_sha = sha256_bytes(canonical_json_bytes(sorted(raw_sha_records, key=lambda x:(x["competition_id"], x["season"]))))
    identity_sha = sha256_bytes(canonical_json_bytes([r["fixture_id"] for r in evaluation]))
    normalized_sha = sha256_bytes(canonical_json_bytes([
        {k:v for k,v in r.items() if k not in {"source_line"}} for r in all_rows
    ]))
    manifest = {
        "schema_version": "football3-v2-pit-dataset-manifest-v1",
        "research_only": True,
        "sources_lock_sha256": sha256_file(lock_path),
        "raw_source_set_sha256": raw_set_sha,
        "normalized_dataset_sha256": normalized_sha,
        "alias_map_sha256": alias_sha,
        "development_sha256": dev_sha,
        "evaluation_features_sha256": feature_sha,
        "evaluation_label_vault_sha256": label_sha,
        "evaluation_identity_sha256": identity_sha,
        "development_n": len(development),
        "evaluation_n": len(evaluation),
        "development_seasons": sorted(dev_seasons),
        "evaluation_seasons": sorted(eval_seasons),
        "result_availability_delay_hours": int(lock["result_availability_delay_hours"]),
        "source_audit": source_audit,
        "identity_conflicts": 0,
        "feature_label_intersection": sorted(LABEL_FIELDS.intersection(eval_features[0].keys()) if eval_features else []),
        "status": "PIT_DATASET_GATE_PASSED",
    }
    (out / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": manifest["status"], "development_n": len(development), "evaluation_n": len(evaluation),
        "raw_source_set_sha256": raw_set_sha, "normalized_dataset_sha256": normalized_sha,
        "evaluation_identity_sha256": identity_sha,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
