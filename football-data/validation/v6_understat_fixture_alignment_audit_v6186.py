#!/usr/bin/env python3
"""V6.18.6 Understat fixture-identity / xG-state attachment audit.

Data-quality research only. No model fitting, probability change, or promotion.

Motivation:
V6.2.4 used exact (platform date, normalized team token) lookups into Understat team
history state. Historical attachment coverage was low in EPL/LaLiga/Bundesliga. Before
using xG for direct P(T) or P(D|T,X), diagnose whether the missingness comes from:
- calendar-date drift between sources,
- team-token / alias mismatch,
- missing Understat fixture data,
- missing pre-match state despite a fixture identity match.

This audit uses Understat league fixture identities (home team, away team, datetime)
and the platform's strict formal prediction rows. It never changes a match identity.
Potential fuzzy matches are diagnostics only and are never accepted as training data.

Exact fixture-aligned attachment is counted only when:
1) normalized home/away tokens match exactly;
2) nearest Understat fixture is unique;
3) absolute source-date difference <= 2 days;
4) both pre-match team states exist on the Understat fixture date.

Research-only; formal_weight=0.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "validation"
E = ROOT / "engine"
for p in (V, E):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_market_residual_fusion_v620 as v620
import v6_understat_xg_residual_v624 as xg
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json, normalize_team_token

OUT = ROOT / "manifests" / "v6_understat_fixture_alignment_audit_v6186_status.json"
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")
YEAR_BY_SEASON = {"2022/23": 2022, "2023/24": 2023, "2024/25": 2024, "2025/26": 2025}
MAX_DATE_DRIFT_DAYS = 2
FUZZY_MIN_PAIR_SCORE = 0.86
FUZZY_MIN_GAP = 0.06
MAX_EXAMPLES_PER_DOMAIN = 30


def fetch_understat_payload(league: str, year: int) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://understat.com/getLeagueData/{league}/{year}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "football-v6.18.6-xg-alignment-audit/1.0",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://understat.com/league/{league}/{year}",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise PlatformError(f"invalid Understat payload: {url}")
    teams = payload.get("teams")
    dates = payload.get("dates")
    if not isinstance(teams, dict) or not teams:
        raise PlatformError(f"missing teams: {url}")
    if not isinstance(dates, (list, dict)) or not dates:
        raise PlatformError(f"missing dates: {url}")
    return payload, {
        "url": url,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "team_count": len(teams),
        "fixture_container_type": type(dates).__name__,
        "payload_keys": sorted(payload.keys()),
    }


def items(container: Any):
    if isinstance(container, dict):
        return list(container.values())
    if isinstance(container, list):
        return container
    return []


def understat_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) < 10:
        raise ValueError(text)
    return text[:10]


def parse_iso_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def fixture_rows(cid: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for raw in items(payload.get("dates")):
        if not isinstance(raw, dict):
            continue
        home = raw.get("h")
        away = raw.get("a")
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue
        home_title = str(home.get("title") or "").strip()
        away_title = str(away.get("title") or "").strip()
        dt = raw.get("datetime") or raw.get("date")
        if not home_title or not away_title or not dt:
            continue
        try:
            day = understat_date(dt)
            ht = xg._understat_team_token(cid, home_title)
            at = xg._understat_team_token(cid, away_title)
        except Exception:
            continue
        out.append({
            "id": str(raw.get("id") or ""),
            "date": day,
            "home_title": home_title,
            "away_title": away_title,
            "home_token": ht,
            "away_token": at,
            "is_result": bool(raw.get("isResult", True)),
        })
    return out


def pair_score(ph: str, pa: str, uh: str, ua: str) -> float:
    hs = difflib.SequenceMatcher(None, ph, uh).ratio()
    aas = difflib.SequenceMatcher(None, pa, ua).ratio()
    return (hs + aas) / 2.0


def summarize_domain(cid: str, model_rows_by_season, payloads_by_season, teams_by_season):
    state_map, state_stats = xg._build_state_maps(cid, teams_by_season, 20.0)
    all_fixtures = []
    for season in SEASONS:
        for f in fixture_rows(cid, payloads_by_season[season]):
            x = dict(f)
            x["season"] = season
            all_fixtures.append(x)

    pair_index = defaultdict(list)
    date_index = defaultdict(list)
    for f in all_fixtures:
        pair_index[(f["home_token"], f["away_token"])].append(f)
        date_index[f["date"]].append(f)

    domain = {
        "state_stats": state_stats,
        "understat_fixture_count": len(all_fixtures),
        "seasons": {},
        "examples": [],
    }
    agg = Counter()

    for season in SEASONS:
        rows = model_rows_by_season.get(season, [])
        c = Counter()
        for row in rows:
            c["input"] += 1
            pdate = str(row["date"])
            ph = xg._understat_team_token(cid, str(row["home_team"]))
            pa = xg._understat_team_token(cid, str(row["away_team"]))

            old_home = state_map.get((pdate, ph))
            old_away = state_map.get((pdate, pa))
            if old_home is not None and old_away is not None:
                c["old_exact_state_attach"] += 1

            exact_pair = pair_index.get((ph, pa), [])
            ranked_exact = []
            for f in exact_pair:
                try:
                    dd = abs((parse_iso_day(f["date"]) - parse_iso_day(pdate)).days)
                except Exception:
                    continue
                ranked_exact.append((dd, f))
            ranked_exact.sort(key=lambda z: (z[0], z[1]["date"], z[1]["id"]))

            chosen = None
            if ranked_exact:
                best_dd = ranked_exact[0][0]
                tied = [f for dd, f in ranked_exact if dd == best_dd]
                if len(tied) == 1 and best_dd <= MAX_DATE_DRIFT_DAYS:
                    chosen = tied[0]
                    c[f"exact_pair_date_drift_{best_dd}"] += 1
                    if best_dd > 0:
                        c["exact_pair_recovered_by_date_alignment"] += 1
                    uh, ua, udate = chosen["home_token"], chosen["away_token"], chosen["date"]
                    if state_map.get((udate, uh)) is not None and state_map.get((udate, ua)) is not None:
                        c["fixture_aligned_state_attach"] += 1
                    else:
                        c["fixture_matched_but_state_missing"] += 1
                elif best_dd > MAX_DATE_DRIFT_DAYS:
                    c["exact_pair_outside_date_tolerance"] += 1
                else:
                    c["exact_pair_ambiguous_nearest_date"] += 1
            else:
                c["no_exact_team_pair"] += 1

            if chosen is None:
                # Fuzzy diagnostics only. Never accepted for state attachment/model rows.
                nearby = []
                for dd in range(-MAX_DATE_DRIFT_DAYS, MAX_DATE_DRIFT_DAYS + 1):
                    target = parse_iso_day(pdate).toordinal() + dd
                    d = date.fromordinal(target).isoformat()
                    for f in date_index.get(d, []):
                        nearby.append((pair_score(ph, pa, f["home_token"], f["away_token"]), f))
                nearby.sort(key=lambda z: z[0], reverse=True)
                if nearby:
                    best_score, best = nearby[0]
                    second = nearby[1][0] if len(nearby) > 1 else 0.0
                    if best_score >= FUZZY_MIN_PAIR_SCORE and best_score - second >= FUZZY_MIN_GAP:
                        c["fuzzy_unique_diagnostic_candidate"] += 1
                        if len(domain["examples"]) < MAX_EXAMPLES_PER_DOMAIN:
                            domain["examples"].append({
                                "season": season,
                                "platform_date": pdate,
                                "platform_home": row["home_team"],
                                "platform_away": row["away_team"],
                                "platform_tokens": [ph, pa],
                                "understat_date": best["date"],
                                "understat_home": best["home_title"],
                                "understat_away": best["away_title"],
                                "understat_tokens": [best["home_token"], best["away_token"]],
                                "pair_similarity": best_score,
                                "runner_up_similarity": second,
                                "classification": "FUZZY_DIAGNOSTIC_ONLY_NOT_ACCEPTED"
                            })
                    else:
                        c["no_unique_fuzzy_candidate"] += 1
                else:
                    c["no_nearby_understat_fixture"] += 1

        exact = c["old_exact_state_attach"]
        aligned = c["fixture_aligned_state_attach"]
        n = c["input"]
        domain["seasons"][season] = {
            **dict(sorted(c.items())),
            "old_exact_state_attach_rate": exact / n if n else None,
            "fixture_aligned_state_attach_rate": aligned / n if n else None,
            "absolute_coverage_gain": (aligned - exact) / n if n else None,
        }
        agg.update(c)

    n = agg["input"]
    domain["aggregate"] = {
        **dict(sorted(agg.items())),
        "old_exact_state_attach_rate": agg["old_exact_state_attach"] / n if n else None,
        "fixture_aligned_state_attach_rate": agg["fixture_aligned_state_attach"] / n if n else None,
        "absolute_coverage_gain": (agg["fixture_aligned_state_attach"] - agg["old_exact_state_attach"]) / n if n else None,
    }
    return domain


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    domains = {}
    fetch_audit = {}
    failures = {}
    for cid, league in xg.UNDERSTAT_LEAGUES.items():
        try:
            report = load_json(xg.base.REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
            if tuple(seasons) != SEASONS:
                raise PlatformError(f"unexpected seasons {cid}: {seasons}")
            model_rows = v620._build_domain_rows_with_identity(cid, seasons)
            payloads = {}
            teams = {}
            fetch_audit[cid] = {}
            for season in seasons:
                payload, audit = fetch_understat_payload(league, YEAR_BY_SEASON[season])
                payloads[season] = payload
                teams[season] = payload["teams"]
                fetch_audit[cid][season] = audit
            domains[cid] = summarize_domain(cid, model_rows, payloads, teams)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        status = "PARTIAL_FAIL" if domains else "FAIL_DATA"
    else:
        status = "PASS"

    aggregate = Counter()
    for d in domains.values():
        aggregate.update({k: v for k, v in d["aggregate"].items() if isinstance(v, int)})
    n = aggregate["input"]
    aggregate_summary = {
        **dict(sorted(aggregate.items())),
        "old_exact_state_attach_rate": aggregate["old_exact_state_attach"] / n if n else None,
        "fixture_aligned_state_attach_rate": aggregate["fixture_aligned_state_attach"] / n if n else None,
        "absolute_coverage_gain": (aggregate["fixture_aligned_state_attach"] - aggregate["old_exact_state_attach"]) / n if n else None,
    }

    payload = {
        "schema_version": "V6.18.6-understat-fixture-alignment-audit-r1",
        "generated_at_utc": generated.isoformat(),
        "status": status,
        "formal_current_version": "V5.0.1",
        "classification": "DATA_QUALITY_AUDIT_ONLY_NO_MODEL_FIT",
        "design": {
            "domains": list(xg.UNDERSTAT_LEAGUES),
            "seasons": list(SEASONS),
            "fixture_identity": "exact normalized home-away pair with unique nearest Understat fixture <=2 calendar days",
            "fuzzy_matching": "diagnostic only; never accepted into training/model data",
            "pre_match_state": "Understat team history state stored before the fixture-date observation",
            "formal_rows": "platform formal prediction rows; no market odds required",
        },
        "aggregate": aggregate_summary,
        "domains": domains,
        "fetch_audit": fetch_audit,
        "failures": failures,
        "decision_support": {
            "minimum_preferred_fixture_aligned_coverage_for_xg_model_research": 0.90,
            "if_below_0_90": "repair explicit alias/date mapping before xG P(T) or P(D|T,X) challenge",
            "if_at_or_above_0_90": "fixture-aligned xG state may proceed to research challenge with mapping frozen"
        },
        "governance": {
            "research_only": true,
            "formal_weight": 0,
            "runtime_probability_change": false,
            "current_rule_change": false,
            "no_fuzzy_training_rows": true,
            "no_model_fitting": true,
            "no_promotion": true
        }
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": status,
        "aggregate": aggregate_summary,
        "domains": {cid: d["aggregate"] for cid, d in domains.items()},
        "failures": failures
    }, ensure_ascii=False, indent=2))
    return 0 if domains else 1


if __name__ == "__main__":
    raise SystemExit(main())
