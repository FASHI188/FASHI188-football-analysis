from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strict import GovernanceError, canonical_json_bytes, sha256_file, strict_goal_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = ROOT / "config"
PROCESSED = ROOT / "processed"
EVIDENCE = HERE / "evidence"


def normalize_team_token(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"\b(fc|cf|afc|sc|ac|sv|fk|sk|club|football|calcio)\b", " ", text)
    return re.sub(r"[^0-9a-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", "", text)


def stable_team_id(competition_id: str, canonical_name: str) -> str:
    token = f"{competition_id}|{normalize_team_token(canonical_name)}".encode("utf-8")
    if not normalize_team_token(canonical_name):
        raise GovernanceError("empty canonical team identity")
    return "team_" + hashlib.sha256(token).hexdigest()[:16]


def fixture_id(competition_id: str, season: str, cutoff: datetime, home: str, away: str) -> str:
    raw = "|".join((competition_id, season, cutoff.astimezone(timezone.utc).isoformat(), home, away))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _season_years(season: str) -> tuple[int | None, int | None]:
    token = str(season).strip()
    m = re.fullmatch(r"(20\d{2})[/-](\d{2})", token)
    if m:
        a = int(m.group(1))
        return a, a + 1
    if re.fullmatch(r"20\d{2}", token):
        a = int(token)
        return a, a
    return None, None


def parse_match_date(value: str, season: str) -> datetime:
    raw = str(value or "").strip()
    if not raw or raw.startswith("line-"):
        raise GovernanceError(f"invalid match date: {raw!r}")
    for fmt in (
        "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y",
        "%a %b %d %Y", "%A %b %d %Y", "%b %d %Y",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    first, second = _season_years(season)
    for fmt in ("%a %b %d", "%A %b %d", "%b %d"):
        try:
            partial = datetime.strptime(raw + " 2000", fmt + " %Y")
        except ValueError:
            continue
        if first is None:
            raise GovernanceError(f"date lacks year and season cannot resolve it: {raw!r}/{season!r}")
        year = first if partial.month >= 7 else (second or first)
        return partial.replace(year=year, tzinfo=timezone.utc)
    raise GovernanceError(f"unsupported match date: {raw!r}")


def load_aliases() -> dict[str, dict[str, str]]:
    path = CONFIG / "team_aliases.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    comps = data.get("competitions", {})
    if not isinstance(comps, dict):
        raise GovernanceError("team_aliases competitions invalid")
    return comps


def canonical_team_name(competition_id: str, raw: str, aliases: dict[str, dict[str, str]]) -> str:
    mapping = aliases.get(competition_id, {})
    if raw in mapping:
        name = mapping[raw]
    else:
        lookup = {normalize_team_token(k): v for k, v in mapping.items()}
        name = lookup.get(normalize_team_token(raw), raw.strip())
    if not isinstance(name, str) or not name.strip():
        raise GovernanceError("empty canonical team name")
    return name.strip()


def eligible_competitions() -> tuple[list[str], dict[str, str]]:
    registry_path = CONFIG / "platform_registry.json"
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    comps = data.get("competitions")
    if not isinstance(comps, list):
        raise GovernanceError("platform registry competitions invalid")
    eligible: list[str] = []
    blocked: dict[str, str] = {}
    for item in comps:
        cid = str(item.get("competition_id") or "")
        if not cid:
            raise GovernanceError("competition without id")
        if item.get("cross_league_strength_gate") == "market_anchor_required":
            blocked[cid] = "BLOCKED_POLICY_PURE_LANE_REQUIRES_MARKET_ANCHOR"
        else:
            eligible.append(cid)
    return eligible, blocked


@dataclass(frozen=True)
class Match:
    fixture_id: str
    competition_id: str
    season: str
    cutoff: datetime
    home_team: str
    away_team: str
    home_team_id: str
    away_team_id: str
    home_goals: int
    away_goals: int
    source_path: str
    source_sha256: str
    source_line: int
    round_index: int = 0

    def feature_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "competition_id": self.competition_id,
            "season": self.season,
            "cutoff": self.cutoff.astimezone(timezone.utc).isoformat(),
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_line": self.source_line,
            "round_index": self.round_index,
        }

    def labeled_dict(self) -> dict[str, Any]:
        out = self.feature_dict()
        out["home_goals"] = self.home_goals
        out["away_goals"] = self.away_goals
        return out


def _row_competition(raw: dict[str, str], path: Path) -> str:
    return raw.get("competition_id") or raw.get("league_id") or path.parent.name


def load_matches() -> tuple[list[Match], dict[str, Any]]:
    aliases = load_aliases()
    eligible, blocked = eligible_competitions()
    source_info: dict[str, dict[str, Any]] = {}
    rows: list[Match] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for cid in sorted(eligible):
        directory = PROCESSED / cid
        if not directory.exists():
            raise GovernanceError(f"processed directory missing: {cid}")
        for path in sorted(directory.glob("*.csv")):
            rel = str(path.relative_to(ROOT))
            digest = sha256_file(path)
            stat = path.stat()
            count = 0
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for line_no, raw0 in enumerate(reader, start=2):
                    raw = {str(k).strip(): "" if v is None else str(v).strip() for k, v in raw0.items() if k}
                    if not raw.get("HomeTeam") or not raw.get("AwayTeam"):
                        continue
                    if raw.get("FTHG", "") == "" or raw.get("FTAG", "") == "":
                        continue
                    season = raw.get("season") or raw.get("Season") or ""
                    cutoff = parse_match_date(raw.get("Date", ""), season)
                    hg = strict_goal_text(raw["FTHG"], f"{rel}:{line_no}:FTHG")
                    ag = strict_goal_text(raw["FTAG"], f"{rel}:{line_no}:FTAG")
                    observed_cid = _row_competition(raw, path)
                    if observed_cid != cid:
                        raise GovernanceError(f"competition mismatch {rel}:{line_no}: {observed_cid} != {cid}")
                    home = canonical_team_name(cid, raw["HomeTeam"], aliases)
                    away = canonical_team_name(cid, raw["AwayTeam"], aliases)
                    if home == away or normalize_team_token(home) == normalize_team_token(away):
                        raise GovernanceError(f"same/ambiguous team identity {rel}:{line_no}")
                    key = (cid, season, cutoff.isoformat(), normalize_team_token(home), normalize_team_token(away))
                    if key in seen:
                        raise GovernanceError(f"duplicate fixture identity: {key}")
                    seen.add(key)
                    fid = fixture_id(cid, str(season), cutoff, home, away)
                    rows.append(Match(
                        fid, cid, str(season), cutoff, home, away,
                        stable_team_id(cid, home), stable_team_id(cid, away),
                        hg, ag, rel, digest, line_no, 0,
                    ))
                    count += 1
            source_info[rel] = {"sha256": digest, "bytes": stat.st_size, "rows_used": count}
    if len(rows) < 3000:
        raise GovernanceError(f"historical universe unexpectedly small: {len(rows)}")
    rows.sort(key=lambda m: (m.cutoff, m.competition_id, m.season, m.home_team, m.away_team, m.fixture_id))

    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    enriched: list[Match] = []
    for m in rows:
        hk = (m.competition_id, m.season, m.home_team_id)
        ak = (m.competition_id, m.season, m.away_team_id)
        rnd = max(counts[hk], counts[ak]) + 1
        enriched.append(Match(**{**m.__dict__, "round_index": rnd}))
        counts[hk] += 1
        counts[ak] += 1

    audit = {
        "eligible_competitions": sorted(eligible),
        "blocked_competitions": blocked,
        "source_files": dict(sorted(source_info.items())),
        "platform_registry_sha256": sha256_file(CONFIG / "platform_registry.json"),
        "team_aliases_sha256": sha256_file(CONFIG / "team_aliases.json") if (CONFIG / "team_aliases.json").exists() else None,
    }
    return enriched, audit


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def freeze_dataset() -> dict[str, Any]:
    matches, audit = load_matches()
    n = len(matches)
    boundary = max(1, int(n * 0.85))
    while boundary < n and matches[boundary].cutoff == matches[boundary - 1].cutoff:
        boundary += 1
    if not (0 < boundary < n):
        raise GovernanceError("invalid final holdout boundary")
    research = matches[:boundary]
    final = matches[boundary:]

    research_path = EVIDENCE / "research_rows.jsonl"
    final_features_path = EVIDENCE / "final_features.jsonl"
    final_labels_path = EVIDENCE / "final_labels.jsonl"
    universe_path = EVIDENCE / "universe_canonical.jsonl"

    research_sha = _write_jsonl(research_path, [m.labeled_dict() for m in research])
    final_features_sha = _write_jsonl(final_features_path, [m.feature_dict() for m in final])
    final_labels_sha = _write_jsonl(final_labels_path, [
        {"fixture_id": m.fixture_id, "home_goals": m.home_goals, "away_goals": m.away_goals} for m in final
    ])
    universe_sha = _write_jsonl(universe_path, [m.labeled_dict() for m in matches])

    comps: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        comps[m.competition_id].add(m.season)

    manifest = {
        "schema_version": "football3-v2-universe-freeze-v1",
        "anchor": "7c1815c47102412e88f72189e2b8f837d9b73a42",
        "n": n,
        "research_n": len(research),
        "final_n": len(final),
        "first_cutoff": matches[0].cutoff.isoformat(),
        "last_cutoff": matches[-1].cutoff.isoformat(),
        "research_last_cutoff": research[-1].cutoff.isoformat(),
        "final_first_cutoff": final[0].cutoff.isoformat(),
        "same_cutoff_not_split": research[-1].cutoff != final[0].cutoff,
        "competitions": {cid: sorted(seasons) for cid, seasons in sorted(comps.items())},
        "universe_sha256": universe_sha,
        "research_rows_sha256": research_sha,
        "final_features_sha256": final_features_sha,
        "final_labels_sha256": final_labels_sha,
        "audit": audit,
        "known_at_policy": "completed historical match rows; target features only use strictly prior cutoff state; date-only source -> whole date atomic batch",
        "labels_isolated_files": True,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "universe_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(freeze_dataset(), ensure_ascii=False, indent=2))
