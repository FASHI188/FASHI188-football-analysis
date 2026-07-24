#!/usr/bin/env python3
"""V6.18.7 deterministic Understat team-alias qualification audit.

Data-quality research only; no model fit and no probability change.

V6.18.6 showed that date alignment barely changes xG-state attachment, while thousands
of platform fixtures fail because team tokens differ between Football-Data/platform and
Understat (for example Newcastle/Newcastle United). Fuzzy fixture matches are useful to
DISCOVER candidate aliases, but they are never directly accepted as training rows.

This audit qualifies a token alias only when ALL conditions hold within a competition:
1) evidence comes from a unique high-separation nearby fixture candidate (same home/away
   orientation, <=2 calendar days, pair similarity >=0.86, runner-up gap >=0.06);
2) the platform token maps to exactly one Understat token across all strong observations;
3) the Understat token maps back to exactly one platform token;
4) the pair is observed in >=3 independent fixtures;
5) neither side conflicts with an existing exact token identity in the opposite source;
6) token similarity is >=0.55 (the schedule evidence, not name similarity, is primary);
7) after freezing all qualified aliases, fixture matching is re-run as exact mapped token
   equality + unique nearest date <=2 days, and xG state must exist for both teams.

The output contains qualified aliases, rejected candidates and post-alias attachment
coverage. No fuzzy row itself is attached. Readiness for xG P(T)/P(D|T,X) research is
PASS only if aggregate coverage >=90% AND every included domain coverage >=90%.
"""
from __future__ import annotations

import difflib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any

import v6_market_residual_fusion_v620 as v620
import v6_understat_xg_residual_v624 as xg
import v6_understat_fixture_alignment_audit_v6186 as a186
import v6_understat_fixture_alignment_audit_v6186r3 as a186r3
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

ROOT = a186.ROOT
OUT = ROOT / "manifests" / "v6_understat_alias_qualification_v6187_status.json"
SEASONS = a186.SEASONS
YEAR_BY_SEASON = a186.YEAR_BY_SEASON
MAX_DATE_DRIFT_DAYS = a186.MAX_DATE_DRIFT_DAYS
PAIR_MIN = a186.FUZZY_MIN_PAIR_SCORE
PAIR_GAP_MIN = a186.FUZZY_MIN_GAP
MIN_ALIAS_OBSERVATIONS = 3
MIN_TOKEN_SIMILARITY = 0.55
RESEARCH_COVERAGE_GATE = 0.90
MAX_REJECT_EXAMPLES = 100


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def nearby_fixtures(date_index, platform_date: str):
    base_day = parse_day(platform_date)
    out = []
    for dd in range(-MAX_DATE_DRIFT_DAYS, MAX_DATE_DRIFT_DAYS + 1):
        d = date.fromordinal(base_day.toordinal() + dd).isoformat()
        for fixture in date_index.get(d, []):
            out.append(fixture)
    return out


def token_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def strong_unique_fixture_candidate(ph: str, pa: str, platform_date: str, date_index):
    ranked = []
    for f in nearby_fixtures(date_index, platform_date):
        score = a186.pair_score(ph, pa, f["home_token"], f["away_token"])
        ranked.append((score, f))
    ranked.sort(key=lambda z: (z[0], z[1]["date"], z[1]["id"]), reverse=True)
    if not ranked:
        return None, {"reason": "NO_NEARBY_FIXTURE"}
    best_score, best = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < PAIR_MIN:
        return None, {"reason": "PAIR_SIMILARITY_LOW", "best": best_score, "second": second}
    if best_score - second < PAIR_GAP_MIN:
        return None, {"reason": "PAIR_NOT_SEPARATED", "best": best_score, "second": second}
    return best, {"reason": "STRONG_UNIQUE", "best": best_score, "second": second}


def exact_mapped_fixture(ph: str, pa: str, platform_date: str, pair_index):
    ranked = []
    for f in pair_index.get((ph, pa), []):
        try:
            dd = abs((parse_day(f["date"]) - parse_day(platform_date)).days)
        except Exception:
            continue
        ranked.append((dd, f))
    ranked.sort(key=lambda z: (z[0], z[1]["date"], z[1]["id"]))
    if not ranked:
        return None, "NO_EXACT_MAPPED_PAIR"
    best_dd = ranked[0][0]
    tied = [f for dd, f in ranked if dd == best_dd]
    if best_dd > MAX_DATE_DRIFT_DAYS:
        return None, "OUTSIDE_DATE_TOLERANCE"
    if len(tied) != 1:
        return None, "AMBIGUOUS_NEAREST_DATE"
    return tied[0], f"DATE_DRIFT_{best_dd}"


def domain_inputs(cid: str, league: str):
    report = load_json(xg.base.REPORT_ROOT / f"{cid}.json")
    seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
    if tuple(seasons) != SEASONS:
        raise PlatformError(f"unexpected seasons {cid}: {seasons}")
    model_rows = v620._build_domain_rows_with_identity(cid, seasons)
    payloads = {}
    teams = {}
    fetch_audit = {}
    fixtures = []
    for season in seasons:
        payload, audit = a186r3.fetch_understat_payload_transport_safe(
            league, YEAR_BY_SEASON[season]
        )
        payloads[season] = payload
        teams[season] = payload["teams"]
        fetch_audit[season] = audit
        for f in a186.fixture_rows(cid, payload):
            x = dict(f)
            x["season"] = season
            fixtures.append(x)
    state_map, state_stats = xg._build_state_maps(cid, teams, 20.0)
    return seasons, model_rows, fixtures, state_map, state_stats, fetch_audit


def qualify_domain(cid: str, league: str):
    seasons, model_rows, fixtures, state_map, state_stats, fetch_audit = domain_inputs(cid, league)
    rows = [r for season in seasons for r in model_rows.get(season, [])]
    date_index = defaultdict(list)
    pair_index = defaultdict(list)
    understat_tokens = set()
    for f in fixtures:
        date_index[f["date"]].append(f)
        pair_index[(f["home_token"], f["away_token"])].append(f)
        understat_tokens.add(f["home_token"])
        understat_tokens.add(f["away_token"])

    platform_tokens = set()
    observations = Counter()
    platform_targets = defaultdict(Counter)
    understat_sources = defaultdict(Counter)
    fixture_evidence = defaultdict(list)
    discovery_stats = Counter()

    for r in rows:
        pdate = str(r["date"])
        ph = xg._understat_team_token(cid, str(r["home_team"]))
        pa = xg._understat_team_token(cid, str(r["away_team"]))
        platform_tokens.update((ph, pa))
        # Only discover aliases from rows that do not already have exact team-pair identity.
        if pair_index.get((ph, pa)):
            discovery_stats["already_exact_pair"] += 1
            continue
        best, info = strong_unique_fixture_candidate(ph, pa, pdate, date_index)
        discovery_stats[info["reason"]] += 1
        if best is None:
            continue
        # Record only token positions that actually differ.
        for side, ptoken, utoken in (
            ("home", ph, best["home_token"]),
            ("away", pa, best["away_token"]),
        ):
            if ptoken == utoken:
                continue
            key = (ptoken, utoken)
            observations[key] += 1
            platform_targets[ptoken][utoken] += 1
            understat_sources[utoken][ptoken] += 1
            if len(fixture_evidence[key]) < 8:
                fixture_evidence[key].append({
                    "side": side,
                    "platform_date": pdate,
                    "understat_date": best["date"],
                    "platform_home": r["home_team"],
                    "platform_away": r["away_team"],
                    "understat_home": best["home_title"],
                    "understat_away": best["away_title"],
                    "pair_similarity": info["best"],
                    "runner_up_similarity": info["second"],
                })

    qualified = {}
    rejected = []
    all_pairs = sorted(observations, key=lambda k: (-observations[k], k[0], k[1]))
    for ptoken, utoken in all_pairs:
        count = observations[(ptoken, utoken)]
        reasons = []
        if count < MIN_ALIAS_OBSERVATIONS:
            reasons.append("TOO_FEW_FIXTURE_OBSERVATIONS")
        if len(platform_targets[ptoken]) != 1:
            reasons.append("PLATFORM_TOKEN_HAS_MULTIPLE_TARGETS")
        if len(understat_sources[utoken]) != 1:
            reasons.append("UNDERSTAT_TOKEN_HAS_MULTIPLE_SOURCES")
        # Exact identity in the opposite source must not be displaced by an alias.
        if ptoken in understat_tokens:
            reasons.append("PLATFORM_TOKEN_ALREADY_EXISTS_AS_UNDERSTAT_IDENTITY")
        if utoken in platform_tokens:
            reasons.append("UNDERSTAT_TOKEN_ALREADY_EXISTS_AS_PLATFORM_IDENTITY")
        sim = token_similarity(ptoken, utoken)
        if sim < MIN_TOKEN_SIMILARITY:
            reasons.append("TOKEN_SIMILARITY_TOO_LOW")
        record = {
            "platform_token": ptoken,
            "understat_token": utoken,
            "observations": count,
            "token_similarity": sim,
            "platform_candidate_targets": dict(platform_targets[ptoken]),
            "understat_candidate_sources": dict(understat_sources[utoken]),
            "evidence": fixture_evidence[(ptoken, utoken)],
        }
        if reasons:
            record["reasons"] = reasons
            if len(rejected) < MAX_REJECT_EXAMPLES:
                rejected.append(record)
        else:
            qualified[ptoken] = {
                **record,
                "qualification": "DETERMINISTIC_ONE_TO_ONE_REPEATED_FIXTURE_EVIDENCE",
            }

    # Re-run attachment using only the frozen qualified token aliases.
    post = Counter()
    by_season = {}
    for season in seasons:
        sc = Counter()
        for r in model_rows.get(season, []):
            sc["input"] += 1
            pdate = str(r["date"])
            ph0 = xg._understat_team_token(cid, str(r["home_team"]))
            pa0 = xg._understat_team_token(cid, str(r["away_team"]))
            ph = qualified.get(ph0, {}).get("understat_token", ph0)
            pa = qualified.get(pa0, {}).get("understat_token", pa0)
            if ph != ph0 or pa != pa0:
                sc["rows_using_qualified_alias"] += 1
            fixture, reason = exact_mapped_fixture(ph, pa, pdate, pair_index)
            sc[reason] += 1
            if fixture is None:
                continue
            udate = fixture["date"]
            if state_map.get((udate, ph)) is None or state_map.get((udate, pa)) is None:
                sc["FIXTURE_MATCHED_STATE_MISSING"] += 1
                continue
            sc["state_attached"] += 1
        n = sc["input"]
        by_season[season] = {
            **dict(sorted(sc.items())),
            "state_attach_rate": sc["state_attached"] / n if n else None,
        }
        post.update(sc)

    n = post["input"]
    aggregate = {
        **dict(sorted(post.items())),
        "state_attach_rate": post["state_attached"] / n if n else None,
    }
    return {
        "state_stats": state_stats,
        "fixture_count": len(fixtures),
        "platform_row_count": len(rows),
        "discovery_stats": dict(sorted(discovery_stats.items())),
        "qualified_alias_count": len(qualified),
        "qualified_aliases": qualified,
        "rejected_alias_candidates": rejected,
        "post_alias_by_season": by_season,
        "post_alias_aggregate": aggregate,
        "fetch_audit": fetch_audit,
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    domains = {}
    failures = {}
    for cid, league in xg.UNDERSTAT_LEAGUES.items():
        try:
            domains[cid] = qualify_domain(cid, league)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    aggregate = Counter()
    domain_rates = {}
    alias_count = 0
    for cid, d in domains.items():
        a = d["post_alias_aggregate"]
        domain_rates[cid] = a.get("state_attach_rate")
        alias_count += int(d["qualified_alias_count"])
        for k, v in a.items():
            if isinstance(v, int):
                aggregate[k] += v
    n = aggregate["input"]
    overall_rate = aggregate["state_attached"] / n if n else None
    all_domains_gate = bool(
        domains
        and overall_rate is not None
        and overall_rate >= RESEARCH_COVERAGE_GATE
        and all(rate is not None and rate >= RESEARCH_COVERAGE_GATE for rate in domain_rates.values())
    )
    status = "PASS" if domains and not failures else "PARTIAL_FAIL" if domains else "FAIL_DATA"

    payload = {
        "schema_version": "V6.18.7-understat-deterministic-alias-qualification-r1",
        "generated_at_utc": generated.isoformat(),
        "status": status,
        "formal_current_version": "V5.0.1",
        "classification": "DATA_IDENTITY_AUDIT_ONLY_NO_MODEL_FIT",
        "design": {
            "candidate_discovery": "unique strong nearby fixture candidate only",
            "candidate_pair_similarity_min": PAIR_MIN,
            "candidate_runner_up_gap_min": PAIR_GAP_MIN,
            "date_tolerance_days": MAX_DATE_DRIFT_DAYS,
            "minimum_alias_fixture_observations": MIN_ALIAS_OBSERVATIONS,
            "minimum_token_similarity": MIN_TOKEN_SIMILARITY,
            "one_to_one_both_directions_required": True,
            "exact_identity_conflict_forbidden": True,
            "post_alias_attachment": "exact mapped home-away pair + unique nearest fixture + both pre-match xG states",
            "fuzzy_training_rows": False,
        },
        "qualified_alias_count": alias_count,
        "aggregate": {
            **dict(sorted(aggregate.items())),
            "state_attach_rate": overall_rate,
        },
        "domain_rates": domain_rates,
        "xg_research_coverage_gate": {
            "threshold_each_domain_and_aggregate": RESEARCH_COVERAGE_GATE,
            "pass": all_domains_gate,
            "next_if_pass": "freeze alias table then test xG incremental information for direct P(T) and conditional P(D|T,X)",
            "next_if_fail": "do not fit xG model; inspect remaining identity rejects / missing source coverage",
        },
        "domains": domains,
        "failures": failures,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "no_fuzzy_row_attachment": True,
            "no_model_fitting": True,
            "no_promotion": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": status,
        "qualified_alias_count": alias_count,
        "overall_attach_rate": overall_rate,
        "domain_rates": domain_rates,
        "coverage_gate_pass": all_domains_gate,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if domains else 1


if __name__ == "__main__":
    raise SystemExit(main())
