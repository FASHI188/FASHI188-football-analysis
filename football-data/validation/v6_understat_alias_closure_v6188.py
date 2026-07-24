#!/usr/bin/env python3
"""V6.18.8 iterative schedule-anchor closure for Understat aliases.

Data-identity research only; no model fitting and no probability change.

V6.18.7 safely qualified 15 repeated one-to-one aliases and raised attachment from
~60% to ~78%, but some domains still had many unmatched fixtures with zero rejected
alias candidates. This is expected when BOTH team names in a fixture differ enough that
the initial pair-similarity discovery never reaches its threshold.

V6.18.8 never lowers the fuzzy threshold and never attaches a fuzzy row. Instead it
iterates deterministic schedule constraints:

Seed aliases:
- re-compute the V6.18.7 qualification rules exactly.

Closure step A (existing strong-pair evidence):
- after applying already-qualified aliases, a unique nearby fixture may now exceed the
  original pair similarity/separation thresholds; repeated one-to-one evidence can add
  another alias under the same V6.18.7 rules.

Closure step B (single-side schedule anchor):
- if one side of a platform fixture is already an exact/mapped Understat token, search
  nearby Understat fixtures in the SAME home/away orientation whose anchored side equals
  that token;
- only when exactly one fixture candidate remains may the opposite unknown token create
  an alias observation;
- an anchored alias requires >=3 independent fixture observations, one-to-one mapping in
  both directions, and no exact-identity conflict. Name similarity is recorded but is
  NOT required because the repeated exact opponent/date/orientation constraint is the
  identity proof.

The closure repeats until no new aliases or 10 iterations. Final xG state attachment is
still exact mapped home-away equality + unique nearest date <=2 days + both pre-match
states present. No fuzzy candidate row is ever attached.

Research readiness requires aggregate >=90% AND every domain >=90%.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

import v6_understat_alias_qualification_v6187 as q187
import v6_understat_xg_residual_v624 as xg
from platform_core import atomic_write_json

ROOT = q187.ROOT
OUT = ROOT / "manifests" / "v6_understat_alias_closure_v6188_status.json"
MAX_ITERATIONS = 10
MIN_OBS = q187.MIN_ALIAS_OBSERVATIONS
RESEARCH_COVERAGE_GATE = q187.RESEARCH_COVERAGE_GATE


def mapped(token: str, aliases: dict[str, dict]) -> str:
    return aliases.get(token, {}).get("understat_token", token)


def anchored_candidates(ph: str, pa: str, pdate: str, date_index):
    """Return deterministic one-side anchor observations for currently mapped tokens.

    Caller decides which original token is still unknown. We emit a candidate only if
    exactly one nearby fixture matches the known side in the same orientation.
    """
    nearby = q187.nearby_fixtures(date_index, pdate)
    out = []
    home_matches = [f for f in nearby if f["home_token"] == ph]
    if len(home_matches) == 1 and home_matches[0]["away_token"] != pa:
        out.append(("away", pa, home_matches[0]["away_token"], home_matches[0]))
    away_matches = [f for f in nearby if f["away_token"] == pa]
    if len(away_matches) == 1 and away_matches[0]["home_token"] != ph:
        out.append(("home", ph, away_matches[0]["home_token"], away_matches[0]))
    return out


def qualify_observations(
    observations: Counter,
    platform_targets,
    understat_sources,
    platform_tokens: set[str],
    understat_tokens: set[str],
    evidence,
    existing_aliases: dict[str, dict],
    source_type: str,
    require_name_similarity: bool,
):
    accepted = {}
    rejected = []
    used_understat = {v["understat_token"] for v in existing_aliases.values()}
    for ptoken, utoken in sorted(observations, key=lambda k: (-observations[k], k[0], k[1])):
        if ptoken in existing_aliases:
            continue
        count = observations[(ptoken, utoken)]
        reasons = []
        if count < MIN_OBS:
            reasons.append("TOO_FEW_FIXTURE_OBSERVATIONS")
        if len(platform_targets[ptoken]) != 1:
            reasons.append("PLATFORM_TOKEN_HAS_MULTIPLE_TARGETS")
        if len(understat_sources[utoken]) != 1:
            reasons.append("UNDERSTAT_TOKEN_HAS_MULTIPLE_SOURCES")
        if ptoken in understat_tokens:
            reasons.append("PLATFORM_TOKEN_ALREADY_EXISTS_AS_UNDERSTAT_IDENTITY")
        if utoken in platform_tokens:
            reasons.append("UNDERSTAT_TOKEN_ALREADY_EXISTS_AS_PLATFORM_IDENTITY")
        if utoken in used_understat:
            reasons.append("UNDERSTAT_TOKEN_ALREADY_USED_BY_QUALIFIED_ALIAS")
        sim = q187.token_similarity(ptoken, utoken)
        if require_name_similarity and sim < q187.MIN_TOKEN_SIMILARITY:
            reasons.append("TOKEN_SIMILARITY_TOO_LOW")
        record = {
            "platform_token": ptoken,
            "understat_token": utoken,
            "observations": count,
            "token_similarity": sim,
            "evidence_type": source_type,
            "platform_candidate_targets": dict(platform_targets[ptoken]),
            "understat_candidate_sources": dict(understat_sources[utoken]),
            "evidence": evidence[(ptoken, utoken)][:8],
        }
        if reasons:
            record["reasons"] = reasons
            rejected.append(record)
        else:
            accepted[ptoken] = {
                **record,
                "qualification": (
                    "ITERATIVE_STRONG_PAIR_ONE_TO_ONE"
                    if require_name_similarity
                    else "REPEATED_SINGLE_SIDE_EXACT_SCHEDULE_ANCHOR_ONE_TO_ONE"
                ),
            }
            used_understat.add(utoken)
    return accepted, rejected


def discover_iteration(rows, aliases, date_index, pair_index, platform_tokens, understat_tokens):
    strong_obs = Counter()
    strong_pt = defaultdict(Counter)
    strong_us = defaultdict(Counter)
    strong_ev = defaultdict(list)
    anchor_obs = Counter()
    anchor_pt = defaultdict(Counter)
    anchor_us = defaultdict(Counter)
    anchor_ev = defaultdict(list)
    stats = Counter()

    for r in rows:
        pdate = str(r["date"])
        ph0 = xg._understat_team_token(r["competition_id"], str(r["home_team"])) if "competition_id" in r else None
        pa0 = xg._understat_team_token(r["competition_id"], str(r["away_team"])) if "competition_id" in r else None
        # Rows supplied by domain wrapper always inject competition_id below.
        if ph0 is None or pa0 is None:
            continue
        ph = mapped(ph0, aliases)
        pa = mapped(pa0, aliases)
        fixture, _ = q187.exact_mapped_fixture(ph, pa, pdate, pair_index)
        if fixture is not None:
            stats["already_exact_after_current_aliases"] += 1
            continue

        best, info = q187.strong_unique_fixture_candidate(ph, pa, pdate, date_index)
        if best is not None:
            stats["strong_pair_candidate"] += 1
            for side, original, current, utoken in (
                ("home", ph0, ph, best["home_token"]),
                ("away", pa0, pa, best["away_token"]),
            ):
                if original in aliases or current == utoken:
                    continue
                key = (original, utoken)
                strong_obs[key] += 1
                strong_pt[original][utoken] += 1
                strong_us[utoken][original] += 1
                if len(strong_ev[key]) < 8:
                    strong_ev[key].append({
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

        # Anchored discovery uses mapped tokens; only an as-yet-unmapped original token
        # may receive a new alias from the opposite exact/mapped side.
        for side, current_unknown, utoken, f in anchored_candidates(ph, pa, pdate, date_index):
            original = pa0 if side == "away" else ph0
            if original in aliases or current_unknown == utoken:
                continue
            # The opposite side must genuinely be known: exact Understat identity or an
            # already-qualified alias, not merely an unresolved platform token.
            if side == "away":
                opposite_known = (ph0 in aliases) or (ph in understat_tokens)
            else:
                opposite_known = (pa0 in aliases) or (pa in understat_tokens)
            if not opposite_known:
                continue
            key = (original, utoken)
            anchor_obs[key] += 1
            anchor_pt[original][utoken] += 1
            anchor_us[utoken][original] += 1
            stats["single_side_anchor_observation"] += 1
            if len(anchor_ev[key]) < 8:
                anchor_ev[key].append({
                    "side": side,
                    "platform_date": pdate,
                    "understat_date": f["date"],
                    "platform_home": r["home_team"],
                    "platform_away": r["away_team"],
                    "understat_home": f["home_title"],
                    "understat_away": f["away_title"],
                    "proof": "opposite side exact/mapped + unique nearby fixture in same orientation",
                })

    strong_accept, strong_reject = qualify_observations(
        strong_obs, strong_pt, strong_us, platform_tokens, understat_tokens,
        strong_ev, aliases, "STRONG_PAIR", True,
    )
    combined = dict(aliases)
    combined.update(strong_accept)
    anchor_accept, anchor_reject = qualify_observations(
        anchor_obs, anchor_pt, anchor_us, platform_tokens, understat_tokens,
        anchor_ev, combined, "SINGLE_SIDE_EXACT_SCHEDULE_ANCHOR", False,
    )
    return strong_accept, anchor_accept, strong_reject, anchor_reject, stats


def qualify_domain(cid: str, league: str):
    seasons, model_rows, fixtures, state_map, state_stats, fetch_audit = q187.domain_inputs(cid, league)
    rows = []
    for season in seasons:
        for r in model_rows.get(season, []):
            x = dict(r)
            x["competition_id"] = cid
            rows.append(x)
    date_index = defaultdict(list)
    pair_index = defaultdict(list)
    understat_tokens = set()
    for f in fixtures:
        date_index[f["date"]].append(f)
        pair_index[(f["home_token"], f["away_token"])].append(f)
        understat_tokens.update((f["home_token"], f["away_token"]))
    platform_tokens = set()
    for r in rows:
        platform_tokens.add(xg._understat_team_token(cid, str(r["home_team"])))
        platform_tokens.add(xg._understat_team_token(cid, str(r["away_team"])))

    aliases = {}
    iterations = []
    all_rejected = []
    for idx in range(1, MAX_ITERATIONS + 1):
        strong, anchored, rej1, rej2, stats = discover_iteration(
            rows, aliases, date_index, pair_index, platform_tokens, understat_tokens
        )
        # Avoid conflicting same-iteration assignments; anchored proof is accepted only
        # when its platform token was not already added by strong-pair evidence.
        anchored = {k: v for k, v in anchored.items() if k not in strong}
        new = {**strong, **anchored}
        iterations.append({
            "iteration": idx,
            "aliases_before": len(aliases),
            "strong_pair_added": sorted(strong),
            "single_side_anchor_added": sorted(anchored),
            "aliases_added": len(new),
            "stats": dict(sorted(stats.items())),
        })
        all_rejected.extend(rej1[:30])
        all_rejected.extend(rej2[:30])
        if not new:
            break
        aliases.update(new)

    # Exact post-closure attachment only.
    post = Counter()
    by_season = {}
    for season in seasons:
        sc = Counter()
        for r in model_rows.get(season, []):
            sc["input"] += 1
            pdate = str(r["date"])
            ph0 = xg._understat_team_token(cid, str(r["home_team"]))
            pa0 = xg._understat_team_token(cid, str(r["away_team"]))
            ph = mapped(ph0, aliases)
            pa = mapped(pa0, aliases)
            if ph != ph0 or pa != pa0:
                sc["rows_using_qualified_alias"] += 1
            fixture, reason = q187.exact_mapped_fixture(ph, pa, pdate, pair_index)
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
    return {
        "state_stats": state_stats,
        "fixture_count": len(fixtures),
        "platform_row_count": len(rows),
        "iterations": iterations,
        "qualified_alias_count": len(aliases),
        "qualified_aliases": aliases,
        "rejected_examples": all_rejected[:100],
        "post_alias_by_season": by_season,
        "post_alias_aggregate": {
            **dict(sorted(post.items())),
            "state_attach_rate": post["state_attached"] / n if n else None,
        },
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
    overall = aggregate["state_attached"] / n if n else None
    gate_pass = bool(
        domains and overall is not None and overall >= RESEARCH_COVERAGE_GATE
        and all(rate is not None and rate >= RESEARCH_COVERAGE_GATE for rate in domain_rates.values())
    )
    status = "PASS" if domains and not failures else "PARTIAL_FAIL" if domains else "FAIL_DATA"
    payload = {
        "schema_version": "V6.18.8-understat-iterative-schedule-anchor-alias-closure-r1",
        "generated_at_utc": generated.isoformat(),
        "status": status,
        "formal_current_version": "V5.0.1",
        "classification": "DATA_IDENTITY_CLOSURE_AUDIT_ONLY_NO_MODEL_FIT",
        "design": {
            "max_iterations": MAX_ITERATIONS,
            "minimum_alias_observations": MIN_OBS,
            "strong_pair_thresholds_unchanged_from_v6187": True,
            "single_side_anchor_requires_opposite_exact_or_qualified_alias": True,
            "single_side_anchor_requires_unique_nearby_fixture_same_orientation": True,
            "single_side_anchor_name_similarity_minimum": None,
            "one_to_one_both_directions_required": True,
            "exact_identity_conflict_forbidden": True,
            "final_attachment_is_exact_after_alias_mapping": True,
            "fuzzy_training_rows": False,
        },
        "qualified_alias_count": alias_count,
        "aggregate": {
            **dict(sorted(aggregate.items())),
            "state_attach_rate": overall,
        },
        "domain_rates": domain_rates,
        "xg_research_coverage_gate": {
            "threshold_each_domain_and_aggregate": RESEARCH_COVERAGE_GATE,
            "pass": gate_pass,
            "next_if_pass": "freeze deterministic alias table and only then run xG P(T) and P(D|T,X) challengers",
            "next_if_fail": "keep xG modeling blocked; audit residual unmatched fixtures/source coverage without lowering identity rules",
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
        "overall_attach_rate": overall,
        "domain_rates": domain_rates,
        "coverage_gate_pass": gate_pass,
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if domains else 1


if __name__ == "__main__":
    raise SystemExit(main())
