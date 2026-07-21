#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import load_json, read_processed_matches

CONFIG = ROOT / "config" / "clubelo_residual_challenger_v515.json"
REPORT_ROOT = ROOT / "validation" / "reports" / "formal_core_v460"
OUT_ROOT = ROOT / "evidence" / "clubelo_v515"
MANIFEST = ROOT / "manifests" / "clubelo_history_ingest_v515_status.json"

# Deterministic identity aliases derived from provider naming conventions and audited
# snapshot candidates. These affect entity identity only and carry no probability weight.
CLUBELO_TEAM_ALIASES = {
    "Nott'm Forest": "Forest",
    "Ath Bilbao": "Bilbao",
    "Ath Madrid": "Atletico",
    "Espanol": "Espanyol",
    "Vallecano": "Rayo Vallecano",
    "Bayern Munich": "Bayern",
    "Werder Bremen": "Werder",
    "Ein Frankfurt": "Frankfurt",
    "FC Koln": "Koeln",
    "M'gladbach": "Gladbach",
}


def _ascii(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _norm(value: str) -> str:
    text = _ascii(value).lower().replace("&", " and ")
    text = re.sub(r"\b(fc|cf|afc|sc|ac|sv|club|football|calcio)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _clubelo_history_slug(value: str) -> str:
    # Mirrors soccerdata.ClubElo.read_team_history(): remove spaces/apostrophes,
    # transliterate accents, then keep ASCII alphanumerics and hyphens only.
    compact = re.sub(r"[\s']", "", _ascii(value))
    return re.sub(r"[^a-zA-Z0-9-]", "", compact)


def _sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _fetch_csv(url: str, retries: int = 3) -> list[dict[str, str]]:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
            with urllib.request.urlopen(req, timeout=35) as response:
                data = response.read().decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(data)))
            if not rows:
                raise RuntimeError("empty CSV")
            return rows
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"ClubElo fetch failed after {retries} attempts: {url}: {last}")


def _report_seasons(cid: str) -> list[str]:
    report = load_json(REPORT_ROOT / f"{cid}.json")
    seasons = []
    for fold in report.get("folds") or []:
        season = str(fold.get("outer_season") or "")
        if season and season not in seasons:
            seasons.append(season)
    seasons.sort(key=lambda s: int(s[:4]))
    return seasons


def _anchor_dates(seasons: list[str]) -> list[str]:
    dates = set()
    for season in seasons:
        year = int(str(season)[:4])
        if "/" in season or ("-" in season and len(season) > 4):
            dates.add(f"{year}-10-01")
            dates.add(f"{year + 1}-03-01")
        else:
            dates.add(f"{year}-05-01")
            dates.add(f"{year}-10-01")
    return sorted(dates)


def _snapshot_candidates(country: str, anchor_dates: list[str]) -> tuple[set[str], dict[str, Any]]:
    names = set()
    audit = []
    for date in anchor_dates:
        rows = _fetch_csv(f"http://api.clubelo.com/{date}")
        filtered = [
            row for row in rows
            if str(row.get("Country") or "") == country and str(row.get("Level") or "") == "1"
        ]
        names.update(str(row.get("Club") or "").strip() for row in filtered if row.get("Club"))
        audit.append({"date": date, "country": country, "top_level_count": len(filtered)})
    return names, {"anchors": audit, "candidate_name_count": len(names)}


def _map_team(team: str, candidates: set[str], min_similarity: float, min_margin: float) -> dict[str, Any]:
    alias = CLUBELO_TEAM_ALIASES.get(team)
    if alias is not None:
        exact_alias = [name for name in candidates if _norm(name) == _norm(alias)]
        if len(exact_alias) == 1:
            return {
                "status": "PASS",
                "clubelo_name": exact_alias[0],
                "score": 1.0,
                "method": "EXPLICIT_AUDITED_CLUBELO_ALIAS",
                "alias_requested": alias,
            }
        return {
            "status": "FAIL",
            "reason": "EXPLICIT_ALIAS_NOT_UNIQUE_IN_PROVIDER_CANDIDATES",
            "alias_requested": alias,
            "matches": exact_alias,
        }

    exact = [name for name in candidates if _norm(name) == _norm(team)]
    if len(exact) == 1:
        return {"status": "PASS", "clubelo_name": exact[0], "score": 1.0, "method": "NORMALIZED_EXACT"}

    ranked = sorted(((_sim(team, name), name) for name in candidates), reverse=True)
    if not ranked:
        return {"status": "FAIL", "reason": "NO_CANDIDATES"}
    best_score, best_name = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < min_similarity:
        return {
            "status": "FAIL", "reason": "SIMILARITY_BELOW_GATE", "best_name": best_name,
            "best_score": best_score, "second_score": second_score
        }
    if best_score - second_score < min_margin:
        return {
            "status": "FAIL", "reason": "BEST_SECOND_MARGIN_BELOW_GATE", "best_name": best_name,
            "best_score": best_score, "second_score": second_score,
            "top_candidates": ranked[:5]
        }
    return {
        "status": "PASS", "clubelo_name": best_name, "score": best_score,
        "second_score": second_score, "method": "HIGH_THRESHOLD_FUZZY_UNIQUE"
    }


def _history(name: str) -> list[dict[str, Any]]:
    slug = _clubelo_history_slug(name)
    if not slug:
        raise RuntimeError(f"empty ClubElo history slug for {name!r}")
    url = f"http://api.clubelo.com/{slug}"
    rows = _fetch_csv(url)
    output = []
    for row in rows:
        try:
            elo = float(row["Elo"])
            from_date = str(row["From"])
            to_date = str(row["To"])
            datetime.fromisoformat(from_date)
            datetime.fromisoformat(to_date)
        except Exception:
            continue
        output.append({
            "clubelo_name": name,
            "clubelo_history_slug": slug,
            "country": str(row.get("Country") or ""),
            "level": str(row.get("Level") or ""),
            "elo": elo,
            "from": from_date,
            "to": to_date,
            "source_url": url,
        })
    if not output:
        raise RuntimeError(f"no usable ClubElo history for {name}")
    return output


def main() -> int:
    cfg = load_json(CONFIG)
    domains = cfg["domains"]
    gate = cfg["identity_gate"]
    domain_reports = {}
    global_histories: dict[str, list[dict[str, Any]]] = {}
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    for cid, country in domains.items():
        seasons = _report_seasons(cid)
        anchors = _anchor_dates(seasons)
        candidates, snapshot_audit = _snapshot_candidates(country, anchors)
        all_matches = read_processed_matches(cid)
        allowed = set(seasons)
        teams = sorted({m.home_team for m in all_matches if m.season in allowed} | {m.away_team for m in all_matches if m.season in allowed})
        mappings = {
            team: _map_team(team, candidates, float(gate["minimum_similarity"]), float(gate["minimum_best_second_margin"]))
            for team in teams
        }
        passed = {team: item for team, item in mappings.items() if item.get("status") == "PASS"}
        failed = {team: item for team, item in mappings.items() if item.get("status") != "PASS"}

        history_failures = {}
        for _team, item in passed.items():
            name = str(item["clubelo_name"])
            if name in global_histories:
                continue
            try:
                global_histories[name] = _history(name)
            except Exception as exc:
                history_failures[name] = f"{type(exc).__name__}: {exc}"

        usable = {
            team: item for team, item in passed.items()
            if str(item["clubelo_name"]) in global_histories
        }
        coverage = len(usable) / max(1, len(teams))
        domain_reports[cid] = {
            "competition_id": cid,
            "country": country,
            "seasons": seasons,
            "anchor_dates": anchors,
            "snapshot_audit": snapshot_audit,
            "processed_team_count": len(teams),
            "identity_pass_count": len(passed),
            "usable_history_team_count": len(usable),
            "coverage": coverage,
            "identity_failures": failed,
            "history_failures": history_failures,
            "mappings": mappings,
            "status": "PASS" if coverage >= 0.95 and not history_failures else "PARTIAL"
        }
        mapping_path = OUT_ROOT / f"{cid}_team_map.json"
        mapping_path.write_text(json.dumps({"competition_id": cid, "mappings": mappings}, ensure_ascii=False, indent=2), encoding="utf-8")

    history_path = OUT_ROOT / "club_histories.jsonl"
    with history_path.open("w", encoding="utf-8") as handle:
        for name in sorted(global_histories):
            for row in global_histories[name]:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    passed_domains = [cid for cid, report in domain_reports.items() if report["status"] == "PASS"]
    payload = {
        "schema_version": "V5.1.5-clubelo-history-ingest-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "requested_domains": list(domains),
        "passed_domains": passed_domains,
        "domain_reports": domain_reports,
        "unique_club_history_count": len(global_histories),
        "history_output": str(history_path.relative_to(ROOT)),
        "status": "PASS" if len(passed_domains) == len(domains) else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_rule": "For every target match, downstream code must select a rating interval containing target_date_minus_one_day.",
        "identity_policy": "Audited explicit aliases first, normalized exact second, high-threshold fuzzy only as last resort. Initial false Ath Madrid -> Real Madrid mapping is invalidated by this receipt.",
        "history_url_policy": "History endpoint slugs mirror soccerdata ClubElo implementation: transliterate accents, remove spaces/apostrophes, keep ASCII alphanumerics and hyphens."
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "passed_domains": passed_domains,
        "unique_club_history_count": len(global_histories),
        "coverage": {cid: r["coverage"] for cid, r in domain_reports.items()}
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
