#!/usr/bin/env python3
"""V6.8.5.3 pristine prospective multi-line total/score research chain.

Research-only. A new immutable epoch is created on first execution. Only Kambi ladder snapshots
observed at/after that epoch can create a freeze, and only while the fixture is still in the future.
Each event is frozen once from the earliest eligible 1h..72h prematch snapshot.

The experiment isolates the incremental value of multiple total-goal lines:
- explicit prior: current-season competition empirical score distribution using matches strictly
  before the snapshot calendar date, with numerical epsilon support only;
- single-line arm: same prior constrained to de-vigged 1X2 + ordinary FT O/U 2.5;
- multi-line arm: same prior constrained to de-vigged 1X2 + every usable ordinary FT half-goal
  total line through the audited V6.8.2 minimum-KL/IPF solver.

Official settlement reuses the audited ESPN regulation-score resolver. No historical backfill,
formal request, formal prediction freeze, CURRENT mutation, formal-weight change, or runtime
probability change is allowed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for _path in (ENGINE, VALIDATION):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from platform_core import (  # noqa: E402
    PlatformError,
    atomic_write_json,
    load_json,
    parse_iso_datetime,
    read_processed_matches,
    sha256_json,
)
import v6_multiline_market_matrix_projection_v682 as ipf  # noqa: E402
import v6_pristine_forward_result_resolver_v612 as result_common  # noqa: E402

LADDERS = ROOT / "evidence" / "market_ladders_v680" / "kambi_full_time_ladders.json"
EPOCH_FILE = ROOT / "manifests" / "v6_multiline_research_forward_epoch_v6853.json"
FREEZE_DIR = ROOT / "forward" / "v6_multiline_research_freezes_v6853"
RESULT_DIR = ROOT / "evidence" / "multiline_research_results_v6853"
STATUS = ROOT / "manifests" / "v6_multiline_research_forward_v6853_status.json"

EPOCH_SCHEMA = "V6.8.5.3-multiline-research-forward-epoch-r1"
FREEZE_SCHEMA = "V6.8.5.3-multiline-research-freeze-r1"
RESULT_SCHEMA = "V6.8.5.3-multiline-research-result-r1"
STATUS_SCHEMA = "V6.8.5.3-multiline-research-forward-status-r1"
MIN_LEAD = timedelta(hours=1)
MAX_LEAD = timedelta(hours=72)
MIN_RESULT_AGE = timedelta(hours=2)
MIN_PRIOR_MATCHES = 20
EPS_SUPPORT = 1e-9
SCORE_MAX = 10

COMP_MAP = {
    "Premier League": "ENG_PremierLeague",
    "Bundesliga": "GER_Bundesliga",
    "Serie A": "ITA_SerieA",
    "Ligue 1": "FRA_Ligue1",
    "LaLiga": "ESP_LaLiga",
    "La Liga": "ESP_LaLiga",
    "Liga Portugal": "POR_PrimeiraLiga",
    "Primeira Liga": "POR_PrimeiraLiga",
    "Eredivisie": "NED_Eredivisie",
    "Super League": "SUI_SuperLeague",
    "Scottish Premiership": "SCO_Premiership",
    "Premiership": "SCO_Premiership",
    "Allsvenskan": "SWE_Allsvenskan",
    "Eliteserien": "NOR_Eliteserien",
    "J1 League": "JPN_J1",
    "J League": "JPN_J1",
    "J.League": "JPN_J1",
    "K-League 1": "KOR_KLeague1",
    "K League 1": "KOR_KLeague1",
    "Brasileirao Serie A": "BRA_SerieA",
    "Brasileirão Serie A": "BRA_SerieA",
    "Liga Profesional Argentina": "ARG_Primera",
    "Major League Soccer": "USA_MLS",
    "MLS": "USA_MLS",
    "Champions League": "UEFA_ChampionsLeague",
    "UEFA Champions League": "UEFA_ChampionsLeague",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_epoch(now: datetime) -> dict[str, Any]:
    if EPOCH_FILE.exists():
        row = load_json(EPOCH_FILE)
        if row.get("schema_version") != EPOCH_SCHEMA or row.get("status") != "FROZEN":
            raise PlatformError("invalid V6.8.5.3 research epoch")
        return row
    row = {
        "schema_version": EPOCH_SCHEMA,
        "status": "FROZEN",
        "epoch_timestamp_utc": now.isoformat(),
        "formal_current_version": "V5.0.1",
        "rule": {
            "snapshot_policy": "first_eligible_snapshot_after_epoch",
            "lead_window_hours": [1, 72],
            "one_freeze_per_kambi_event_id": True,
            "prior": "same_competition_same_calendar_season_empirical_score_distribution_strictly_before_snapshot_date",
            "prior_minimum_matches": MIN_PRIOR_MATCHES,
            "prior_support": f"0..{SCORE_MAX}_home_x_0..{SCORE_MAX}_away_with_numerical_epsilon_only",
            "single_line_arm": "devigged_1x2_plus_ordinary_FT_OU2.5_minimum_KL_IPF",
            "multiline_arm": "devigged_1x2_plus_all_usable_ordinary_FT_half_goal_totals_V6.8.2_minimum_KL_IPF",
            "fast100_role": "research_screen_only_no_automatic_promotion",
            "historical_backfill": False,
        },
        "governance": {
            "research_only": True,
            "formal_request_generation": False,
            "formal_prediction_freeze_generation": False,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "postmatch_reprojection_forbidden": True,
        },
    }
    EPOCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(EPOCH_FILE, row)
    return row


def season_for(competition_id: str, kickoff: datetime) -> str:
    # The currently covered prospective domains are calendar-year leagues. Other domains are
    # fail-closed until an explicit season mapper is added rather than silently guessing.
    if competition_id in {
        "ARG_Primera", "BRA_SerieA", "KOR_KLeague1", "NOR_Eliteserien",
        "SWE_Allsvenskan", "USA_MLS", "JPN_J1",
    }:
        return str(kickoff.year)
    raise PlatformError(f"no explicit V6.8.5.3 season mapper for {competition_id}")


def empirical_prior(competition_id: str, season: str, observed: datetime) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    try:
        rows = read_processed_matches(competition_id)
    except Exception as exc:
        return None, {"status": "UNAVAILABLE_HISTORY_READ", "error": f"{type(exc).__name__}: {exc}"}
    history = [
        m for m in rows
        if str(m.season) == season and m.date.date() < observed.date()
    ]
    history.sort(key=lambda m: m.date)
    usable = []
    outside = 0
    for m in history:
        h, a = int(m.home_goals), int(m.away_goals)
        if 0 <= h <= SCORE_MAX and 0 <= a <= SCORE_MAX:
            usable.append((h, a, m.date.date().isoformat()))
        else:
            outside += 1
    if len(usable) < MIN_PRIOR_MATCHES:
        return None, {
            "status": "INSUFFICIENT_CURRENT_SEASON_HISTORY",
            "competition_id": competition_id,
            "season": season,
            "strictly_prior_match_count": len(usable),
            "minimum_required": MIN_PRIOR_MATCHES,
            "same_day_matches_excluded": True,
            "outside_support_excluded": outside,
        }
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for h, a, _date in usable:
        counts[(h, a)] += 1
    matrix = []
    total = 0.0
    for h in range(SCORE_MAX + 1):
        for a in range(SCORE_MAX + 1):
            weight = float(counts.get((h, a), 0)) + EPS_SUPPORT
            total += weight
            matrix.append({"home_goals": h, "away_goals": a, "probability": weight})
    for cell in matrix:
        cell["probability"] = float(cell["probability"]) / total
    audit = {
        "status": "READY",
        "competition_id": competition_id,
        "season": season,
        "strictly_prior_match_count": len(usable),
        "earliest_history_date": usable[0][2],
        "latest_history_date": usable[-1][2],
        "snapshot_date_excluded": observed.date().isoformat(),
        "same_day_matches_excluded": True,
        "outside_support_excluded": outside,
        "support_max_goals_per_team": SCORE_MAX,
        "epsilon_support": EPS_SUPPORT,
        "probability_sum_residual": abs(sum(float(c["probability"]) for c in matrix) - 1.0),
        "prior_sha256": sha256_json(matrix),
    }
    return matrix, audit


def single_line_project(prior: list[dict[str, Any]], bundle: dict[str, Any]) -> dict[str, Any]:
    prior = ipf.renorm(prior)
    one = ipf.select_1x2(bundle)
    targets = ipf.total_targets(bundle)
    selected = next(((line, target) for line, target in targets if abs(float(line) - 2.5) <= 1e-9), None)
    if selected is None:
        return {"status": "OU2.5_NOT_AVAILABLE"}
    line, total_target = selected
    candidate = prior
    for iteration in range(1, ipf.MAX_ITER + 1):
        candidate = ipf.scale_partition(candidate, ipf.outcome_group, one, "1x2")
        candidate = ipf.scale_partition(candidate, ipf.total_group(line), total_target, "OU2.5")
        one_residual = ipf.max_residual(ipf.marginal(candidate, ipf.outcome_group), one)
        total_residual = ipf.max_residual(ipf.marginal(candidate, ipf.total_group(line)), total_target)
        worst = max(one_residual, total_residual)
        if worst <= ipf.TOL:
            p_sum = sum(p for _h, _a, p in ipf.rows(candidate))
            return {
                "status": "SINGLELINE_MARKET_MATRIX_READY",
                "method": "minimum_KL_IPF_1x2_plus_OU2.5",
                "objective": "minimize_KL(candidate||explicit_prior)_subject_to_1x2_and_OU2.5",
                "iterations": iteration,
                "converged": True,
                "de_vigged_1x2_target": one,
                "de_vigged_total_target": {str(line): total_target},
                "one_x_two_max_residual": one_residual,
                "total_line_max_residual": total_residual,
                "max_constraint_residual": worst,
                "probability_sum_residual": abs(p_sum - 1.0),
                "kl_from_prior": ipf.kl(candidate, prior),
                "total_goals_distribution": ipf.total_distribution(candidate),
                "score_diagnostics": ipf.score_diagnostics(candidate),
                "candidate_matrix": candidate,
            }
    return {"status": "IPF_NONCONVERGENCE", "iterations": ipf.MAX_ITER}


def freeze_path(event_id: Any) -> Path:
    return FREEZE_DIR / f"event_{str(event_id)}.json"


def result_path(event_id: Any) -> Path:
    return RESULT_DIR / f"event_{str(event_id)}.json"


def scan_and_freeze(now: datetime, epoch: dict[str, Any]) -> dict[str, int]:
    stats: Counter = Counter()
    if not LADDERS.exists():
        return {"ladder_file_missing": 1}
    epoch_dt = parse_iso_datetime(epoch["epoch_timestamp_utc"], "epoch_timestamp_utc")
    payload = load_json(LADDERS)
    candidates: dict[str, tuple[datetime, dict[str, Any], str, datetime]] = {}
    for bundle in payload.get("bundles") or []:
        stats["bundles_seen"] += 1
        try:
            event_id = str(bundle.get("event_id") or "").strip()
            source_comp = str(bundle.get("competition_source") or "").strip()
            competition_id = COMP_MAP.get(source_comp)
            if not event_id or not competition_id:
                stats["unmapped_or_missing_identity"] += 1
                continue
            home = str(bundle.get("home_team_source") or "").strip()
            away = str(bundle.get("away_team_source") or "").strip()
            observed = parse_iso_datetime(str(bundle.get("observed_at_utc") or ""), "observed_at_utc")
            kickoff = parse_iso_datetime(str(bundle.get("kickoff_utc") or ""), "kickoff_utc")
            if not home or not away:
                stats["missing_teams"] += 1
                continue
            if observed < epoch_dt:
                stats["before_epoch"] += 1
                continue
            if observed > now or observed >= kickoff:
                stats["invalid_snapshot_timing"] += 1
                continue
            # No first-run historical backfill: an event must still be in the future when frozen.
            if kickoff <= now:
                stats["already_started_not_backfilled"] += 1
                continue
            lead = kickoff - observed
            if lead < MIN_LEAD or lead > MAX_LEAD:
                stats["outside_1_72h_window"] += 1
                continue
            path = freeze_path(event_id)
            if path.exists():
                stats["already_frozen"] += 1
                continue
            if len(ipf.total_targets(bundle)) < 2:
                stats["insufficient_multiline_half_goals"] += 1
                continue
            if not any(abs(float(line) - 2.5) <= 1e-9 for line, _target in ipf.total_targets(bundle)):
                stats["ou25_missing"] += 1
                continue
            previous = candidates.get(event_id)
            if previous is None or observed < previous[0]:
                candidates[event_id] = (observed, bundle, competition_id, kickoff)
        except Exception:
            stats["bundle_rejected_exception"] += 1
    FREEZE_DIR.mkdir(parents=True, exist_ok=True)
    for event_id, (observed, bundle, competition_id, kickoff) in sorted(candidates.items(), key=lambda kv: (kv[1][0], kv[0])):
        try:
            season = season_for(competition_id, kickoff)
            prior, prior_audit = empirical_prior(competition_id, season, observed)
            if prior is None:
                stats["prior_unavailable"] += 1
                continue
            single = single_line_project(prior, bundle)
            multi = ipf.project(prior, bundle)
            if single.get("status") != "SINGLELINE_MARKET_MATRIX_READY":
                stats["singleline_not_ready"] += 1
                continue
            if multi.get("status") != "MULTILINE_MARKET_MATRIX_READY":
                stats["multiline_not_ready"] += 1
                continue
            raw_path = str(bundle.get("raw_path") or "")
            raw_abs = ROOT / raw_path if raw_path else None
            record = {
                "schema_version": FREEZE_SCHEMA,
                "status": "FROZEN",
                "recorded_at_utc": now.isoformat(),
                "research_epoch_timestamp_utc": epoch["epoch_timestamp_utc"],
                "fixture_identity": {
                    "event_id": event_id,
                    "competition_source": bundle.get("competition_source"),
                    "competition_id": competition_id,
                    "season": season,
                    "home_team": bundle.get("home_team_source"),
                    "away_team": bundle.get("away_team_source"),
                    "kickoff_utc": kickoff.isoformat(),
                    "freeze_observed_at_utc": observed.isoformat(),
                    "lead_minutes": (kickoff - observed).total_seconds() / 60.0,
                    "settlement_scope": "90_minutes_including_stoppage",
                },
                "source_ladder": {
                    "aggregate_path": str(LADDERS.relative_to(ROOT)),
                    "aggregate_file_sha256": file_sha(LADDERS),
                    "bundle_sha256": sha256_json(bundle),
                    "raw_path": raw_path or None,
                    "raw_file_sha256": file_sha(raw_abs) if raw_abs is not None and raw_abs.exists() else bundle.get("raw_file_sha256"),
                    "ordinary_half_goal_total_lines_used_by_multiline": sorted(float(line) for line, _target in ipf.total_targets(bundle)),
                },
                "explicit_prior": {
                    "audit": prior_audit,
                    "matrix": prior,
                    "score_diagnostics": ipf.score_diagnostics(prior),
                    "total_goals_distribution": ipf.total_distribution(prior),
                },
                "singleline_arm": single,
                "multiline_arm": multi,
                "governance": {
                    "research_only": True,
                    "same_prior_both_market_arms": True,
                    "same_snapshot_both_market_arms": True,
                    "singleline_is_fixed_OU2.5": True,
                    "multiline_uses_all_ordinary_half_goal_totals": True,
                    "historical_backfill": False,
                    "one_freeze_per_event": True,
                    "postmatch_reprojection_forbidden": True,
                    "formal_probability_change": False,
                    "formal_weight_change": False,
                    "runtime_probability_change": False,
                    "current_rule_change": False,
                },
            }
            record["freeze_sha256"] = sha256_json({k: v for k, v in record.items() if k != "freeze_sha256"})
            atomic_write_json(freeze_path(event_id), record)
            stats["new_freezes"] += 1
        except Exception:
            stats["freeze_exception"] += 1
    return dict(sorted(stats.items()))


def resolve_result(freeze: dict[str, Any], now: datetime, cache: dict[str, Any]) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    identity = freeze["fixture_identity"]
    cid = str(identity["competition_id"])
    if cid not in result_common.DOMAINS:
        return None, "domain_unmapped_for_espn", {}
    kickoff = parse_iso_datetime(identity["kickoff_utc"], "kickoff_utc")
    matches = []
    pages = []
    for date_token, payload, url in result_common.fetch_scoreboards(cid, kickoff, cache):
        pages.append({"date_token": date_token, "url": url})
        for raw_event in payload.get("events") or []:
            if not isinstance(raw_event, dict):
                continue
            try:
                event_kickoff = parse_iso_datetime(str(raw_event.get("date") or ""), "espn_event_date")
            except Exception:
                continue
            if abs(event_kickoff - kickoff) > result_common.KICKOFF_TOLERANCE:
                continue
            competitions = raw_event.get("competitions") or []
            if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
                continue
            comp = competitions[0]
            competitors = comp.get("competitors") or []
            home = next((r for r in competitors if isinstance(r, dict) and r.get("homeAway") == "home"), None)
            away = next((r for r in competitors if isinstance(r, dict) and r.get("homeAway") == "away"), None)
            if not isinstance(home, dict) or not isinstance(away, dict):
                continue
            if result_common.team_matches(cid, home, str(identity["home_team"])) and result_common.team_matches(cid, away, str(identity["away_team"])):
                key = (str(raw_event.get("id") or ""), event_kickoff.isoformat())
                matches.append((key, raw_event, comp, event_kickoff, url, date_token))
    unique = {row[0]: row for row in matches}
    if not unique:
        return None, "identity_not_found", {"pages": pages}
    if len(unique) > 1:
        return None, "identity_ambiguous", {"candidate_count": len(unique), "pages": pages}
    _key, raw_event, comp, event_kickoff, url, date_token = next(iter(unique.values()))
    score = result_common.regulation_score(raw_event, comp)
    if score is None:
        return None, "not_final_or_90m_score_unavailable", {"event_id": raw_event.get("id"), "url": url}
    hg, ag, method = score
    receipt = {
        "schema_version": RESULT_SCHEMA,
        "status": "SETTLED",
        "event_id": identity["event_id"],
        "competition_id": cid,
        "home_team": identity["home_team"],
        "away_team": identity["away_team"],
        "kickoff_utc": identity["kickoff_utc"],
        "home_goals_90": int(hg),
        "away_goals_90": int(ag),
        "settlement_scope": "90_minutes_including_stoppage",
        "source": {
            "name": "ESPN public soccer scoreboard API",
            "url": url,
            "observed_at_utc": now.isoformat(),
            "source_record_id": str(raw_event.get("id") or "") or None,
            "scoreboard_date_token": date_token,
            "event_kickoff_utc": event_kickoff.isoformat(),
            "regulation_score_extraction": method,
        },
        "freeze_sha256": freeze.get("freeze_sha256"),
    }
    receipt["result_sha256"] = sha256_json({k: v for k, v in receipt.items() if k != "result_sha256"})
    return receipt, "resolved", {"score": [hg, ag], "url": url, "method": method}


def settle(now: datetime) -> dict[str, int]:
    stats: Counter = Counter()
    cache: dict[str, Any] = {}
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(FREEZE_DIR.glob("event_*.json")) if FREEZE_DIR.exists() else []:
        stats["freeze_files_seen"] += 1
        try:
            freeze = load_json(path)
            event_id = str((freeze.get("fixture_identity") or {}).get("event_id") or "")
            if not event_id:
                stats["invalid_freeze_identity"] += 1
                continue
            rpath = result_path(event_id)
            if rpath.exists():
                stats["already_settled"] += 1
                continue
            kickoff = parse_iso_datetime(freeze["fixture_identity"]["kickoff_utc"], "kickoff_utc")
            if now < kickoff + MIN_RESULT_AGE:
                stats["not_old_enough"] += 1
                continue
            stats["eligible_for_resolution"] += 1
            try:
                receipt, status, _audit = resolve_result(freeze, now, cache)
            except Exception:
                stats["resolver_exception"] += 1
                continue
            stats[status] += 1
            if receipt is not None:
                atomic_write_json(rpath, receipt)
                stats["new_results_settled"] += 1
        except Exception:
            stats["settlement_exception"] += 1
    return dict(sorted(stats.items()))


def matrix_metrics(matrix: list[dict[str, Any]], hg: int, ag: int) -> dict[str, float]:
    ranked = sorted([(float(c["probability"]), int(c["home_goals"]), int(c["away_goals"])) for c in matrix], reverse=True)
    actual_p = next((p for p, h, a in ranked if h == hg and a == ag), EPS_SUPPORT)
    top1 = ranked[:1]
    top3 = ranked[:3]
    score_top1 = float(any(h == hg and a == ag for _p, h, a in top1))
    score_top3 = float(any(h == hg and a == ag for _p, h, a in top3))
    total_probs = [0.0] * 8
    for p, h, a in ranked:
        t = h + a
        total_probs[t if t <= 6 else 7] += p
    actual_t = hg + ag
    actual_bucket = actual_t if actual_t <= 6 else 7
    total_rank = sorted([(p, i) for i, p in enumerate(total_probs)], reverse=True)
    total_top1 = float(total_rank[0][1] == actual_bucket)
    total_top2 = float(actual_bucket in {x[1] for x in total_rank[:2]})
    cum_p = 0.0
    total_rps = 0.0
    for i in range(7):
        cum_p += total_probs[i]
        cum_y = 1.0 if actual_bucket <= i else 0.0
        total_rps += (cum_p - cum_y) ** 2
    return {
        "joint_log": -math.log(max(EPS_SUPPORT, actual_p)),
        "score_top1": score_top1,
        "score_top3": score_top3,
        "total_top1": total_top1,
        "total_top2": total_top2,
        "total_rps": total_rps,
    }


def summarize(values: list[dict[str, float]]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    keys = ["joint_log", "score_top1", "score_top3", "total_top1", "total_top2", "total_rps"]
    out: dict[str, Any] = {"count": len(values)}
    for key in keys:
        out[key] = sum(v[key] for v in values) / len(values)
    return out


def evaluate(now: datetime, freeze_stats: dict[str, int], settle_stats: dict[str, int], epoch: dict[str, Any]) -> dict[str, Any]:
    arms: dict[str, list[dict[str, float]]] = {"prior": [], "singleline": [], "multiline": []}
    by_comp: dict[str, dict[str, list[dict[str, float]]]] = defaultdict(lambda: {"prior": [], "singleline": [], "multiline": []})
    freeze_count = 0
    open_count = 0
    invalid_pairs = []
    for fpath in sorted(FREEZE_DIR.glob("event_*.json")) if FREEZE_DIR.exists() else []:
        freeze_count += 1
        freeze = load_json(fpath)
        event_id = str(freeze["fixture_identity"]["event_id"])
        rpath = result_path(event_id)
        if not rpath.exists():
            open_count += 1
            continue
        result = load_json(rpath)
        if result.get("freeze_sha256") != freeze.get("freeze_sha256"):
            invalid_pairs.append(event_id)
            continue
        hg, ag = int(result["home_goals_90"]), int(result["away_goals_90"])
        matrices = {
            "prior": freeze["explicit_prior"]["matrix"],
            "singleline": freeze["singleline_arm"]["candidate_matrix"],
            "multiline": freeze["multiline_arm"]["candidate_matrix"],
        }
        cid = str(freeze["fixture_identity"]["competition_id"])
        for name, matrix in matrices.items():
            metric = matrix_metrics(matrix, hg, ag)
            arms[name].append(metric)
            by_comp[cid][name].append(metric)
    summary = {name: summarize(vals) for name, vals in arms.items()}
    settled_count = summary["multiline"].get("count", 0)
    delta = {}
    if settled_count:
        s, m = summary["singleline"], summary["multiline"]
        delta = {
            "multiline_minus_singleline_score_top1_pp": 100.0 * (m["score_top1"] - s["score_top1"]),
            "multiline_minus_singleline_score_top3_pp": 100.0 * (m["score_top3"] - s["score_top3"]),
            "multiline_minus_singleline_total_top1_pp": 100.0 * (m["total_top1"] - s["total_top1"]),
            "multiline_minus_singleline_total_top2_pp": 100.0 * (m["total_top2"] - s["total_top2"]),
            "multiline_minus_singleline_joint_log": m["joint_log"] - s["joint_log"],
            "multiline_minus_singleline_total_rps": m["total_rps"] - s["total_rps"],
        }
    by_comp_summary = {
        cid: {name: summarize(vals) for name, vals in group.items()}
        for cid, group in sorted(by_comp.items())
    }
    fast100_ready = settled_count >= 100
    fast100_signal = False
    if fast100_ready and delta:
        hit_gain = max(delta["multiline_minus_singleline_total_top1_pp"], delta["multiline_minus_singleline_score_top1_pp"])
        proper_nonworse = delta["multiline_minus_singleline_joint_log"] <= 0 and delta["multiline_minus_singleline_total_rps"] <= 0
        fast100_signal = hit_gain >= 5.0 and proper_nonworse
    return {
        "schema_version": STATUS_SCHEMA,
        "generated_at_utc": now.isoformat(),
        "status": "WARN_INVALID_FREEZE_RESULT_LINKS" if invalid_pairs else "PASS",
        "formal_current_version": "V5.0.1",
        "research_epoch_timestamp_utc": epoch["epoch_timestamp_utc"],
        "freeze_scan": freeze_stats,
        "settlement_scan": settle_stats,
        "freeze_count": freeze_count,
        "settled_count": settled_count,
        "open_count": open_count,
        "invalid_freeze_result_event_ids": invalid_pairs,
        "arms": summary,
        "multiline_minus_singleline": delta,
        "by_competition": by_comp_summary,
        "fast100": {
            "minimum_settled": 100,
            "ready": fast100_ready,
            "screen_signal": fast100_signal,
            "screen_rule": "at_least_5pp_gain_in_total_top1_or_score_top1_AND_joint_log_nonworse_AND_total_rps_nonworse",
            "role": "research_screen_only_not_promotion",
        },
        "governance": {
            "research_only": True,
            "new_epoch_no_historical_backfill": True,
            "official_90m_result_settlement": True,
            "processed_repository_not_used_for_settlement": True,
            "postmatch_reprojection_forbidden": True,
            "automatic_promotion": False,
            "formal_probability_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }


def main() -> int:
    now = now_utc()
    epoch = ensure_epoch(now)
    freeze_stats = scan_and_freeze(now, epoch)
    settle_stats = settle(now)
    report = evaluate(now, freeze_stats, settle_stats, epoch)
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATUS, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
