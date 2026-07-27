#!/usr/bin/env python3
"""V6.13.1 neutral 17-domain fixed1000 benchmark.

V6.13.0 revealed a design defect: requiring complete historical 1X2 odds before a
match could enter the benchmark made market-data availability decide benchmark
membership and removed K League 1 / Champions League entirely. This repair freezes
membership from match identity + 90m result only. Each prediction track then reports
its own availability/coverage on the same immutable 1000 matches.

Research only. No CURRENT/runtime/formal-weight change.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_1x2_fixed1000_benchmark_v6130 as old
from diagnose_1x2_market_anchor_v697 import _extract_odds
from platform_core import parse_match_date

TARGET_COMPETITIONS = old.TARGET_COMPETITIONS
BENCHMARK_N = 1000
SEASONS_PER_COMP = 2
SEED = "V6.13.1-NEUTRAL-FIXED1000-20260727"
SCHEMA = "V6.13.1-neutral-fixed1000-1x2-benchmark-r1"
BENCHMARK_PATH = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
STATUS_PATH = ROOT / "manifests" / "v6_1x2_neutral_fixed1000_v6131_status.json"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def team(raw: dict[str, str], side: str) -> str:
    keys = ("HomeTeam", "Home", "home_team", "home") if side == "home" else ("AwayTeam", "Away", "away_team", "away")
    for k in keys:
        v = str(raw.get(k) or "").strip()
        if v:
            return v
    return ""


def rank(row: dict[str, Any]) -> str:
    return hashlib.sha256((SEED + "|" + old._identity_key(row)).encode("utf-8")).hexdigest()


def read_identity_pool() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    processed = ROOT / "processed"
    rows: list[dict[str, Any]] = []
    parse_fail = result_fail = identity_fail = 0
    duplicate = Counter()
    raw_market_available = 0

    for cid in TARGET_COMPETITIONS:
        comp_dir = processed / cid
        if not comp_dir.exists():
            continue
        for path in sorted(comp_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row_index, raw0 in enumerate(csv.DictReader(handle)):
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    actual = old._actual(raw)
                    if actual is None:
                        result_fail += 1
                        continue
                    season = old._season_label(raw, path)
                    date_raw = str(raw.get("Date") or raw.get("date") or "").strip()
                    if not date_raw:
                        parse_fail += 1
                        continue
                    try:
                        date_iso = parse_match_date(date_raw, season).isoformat()
                    except Exception:
                        parse_fail += 1
                        continue
                    home, away = team(raw, "home"), team(raw, "away")
                    if not home or not away:
                        identity_fail += 1
                        continue
                    row: dict[str, Any] = {
                        "competition_id": cid,
                        "season": season,
                        "date": date_iso,
                        "row_index": row_index,
                        "home_team": home,
                        "away_team": away,
                        "actual": actual,
                        "source_file": str(path.relative_to(ROOT)),
                    }
                    extracted = _extract_odds(raw)
                    if extracted is None:
                        row["market"] = None
                    else:
                        market, provider = extracted
                        probs = {d: float(market[d]) for d in old.DIRECTIONS}
                        total = sum(probs.values())
                        if total <= 0 or not math.isfinite(total):
                            row["market"] = None
                        else:
                            probs = {d: probs[d] / total for d in old.DIRECTIONS}
                            pick = max(old.DIRECTIONS, key=lambda d: probs[d])
                            row["market"] = {
                                "provider": provider,
                                "probabilities": probs,
                                "pick": pick,
                                "pmax": probs[pick],
                            }
                            raw_market_available += 1
                    duplicate[old._identity_key(row)] += 1
                    rows.append(row)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["competition_id"], r["season"])].append(r)

    selected_seasons: dict[str, list[str]] = {}
    eligible: list[dict[str, Any]] = []
    for cid in TARGET_COMPETITIONS:
        seasons = []
        for (gc, season), g in groups.items():
            if gc != cid or not g:
                continue
            first = min(str(x["date"]) for x in g)
            seasons.append((season, first, g))
        seasons.sort(key=lambda x: old._season_sort_key(x[0], x[1]))
        chosen = seasons[-SEASONS_PER_COMP:]
        selected_seasons[cid] = [x[0] for x in chosen]
        for _, _, g in chosen:
            eligible.extend(g)

    meta = {
        "raw_identity_result_rows": len(rows),
        "raw_rows_with_market": raw_market_available,
        "eligible_latest_two_season_rows": len(eligible),
        "selected_seasons": selected_seasons,
        "missing_competitions": [c for c in TARGET_COMPETITIONS if not selected_seasons.get(c)],
        "competitions_with_lt_two_seasons": [c for c in TARGET_COMPETITIONS if len(selected_seasons.get(c, [])) < 2],
        "duplicate_identity_keys": sum(1 for n in duplicate.values() if n > 1),
        "parse_failures": parse_fail,
        "result_failures": result_fail,
        "identity_failures": identity_fail,
    }
    return eligible, meta


def quotas(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    strata: dict[tuple[str, str], int] = Counter((r["competition_id"], r["season"]) for r in rows)
    if len(rows) < BENCHMARK_N:
        raise RuntimeError(f"identity pool too small: {len(rows)}")
    keys = sorted(strata)
    q = {k: 1 for k in keys}
    remain = BENCHMARK_N - len(keys)
    caps = {k: max(0, strata[k] - 1) for k in keys}
    total_cap = sum(caps.values())
    ideals = {k: (remain * caps[k] / total_cap if total_cap else 0.0) for k in keys}
    for k in keys:
        q[k] += min(caps[k], int(math.floor(ideals[k])))
    assigned = sum(q.values())
    order = sorted(keys, key=lambda k: (ideals[k] - math.floor(ideals[k]), strata[k], k), reverse=True)
    while assigned < BENCHMARK_N:
        changed = False
        for k in order:
            if q[k] < strata[k]:
                q[k] += 1
                assigned += 1
                changed = True
                if assigned == BENCHMARK_N:
                    break
        if not changed:
            raise RuntimeError("quota allocation exhausted")
    return q


def select(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    q = quotas(rows)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        strata[(r["competition_id"], r["season"])].append(r)
    out: list[dict[str, Any]] = []
    for k, g in sorted(strata.items()):
        out.extend(sorted(g, key=lambda r: (rank(r), old._identity_key(r)))[:q[k]])
    out.sort(key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    if len(out) != BENCHMARK_N:
        raise RuntimeError(f"benchmark size {len(out)} != {BENCHMARK_N}")
    return out


def market_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        m = r.get("market")
        if not m:
            continue
        out.append({
            **{k: r[k] for k in ("competition_id", "season", "date", "row_index", "home_team", "away_team", "actual")},
            "provider": m["provider"],
            "probabilities": m["probabilities"],
            "pick": m["pick"],
            "pmax": m["pmax"],
        })
    return out


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_comp: dict[str, dict[str, int]] = defaultdict(lambda: {"benchmark": 0, "market": 0})
    by_actual: dict[str, dict[str, int]] = defaultdict(lambda: {"benchmark": 0, "market": 0})
    for r in rows:
        cid, act = r["competition_id"], r["actual"]
        by_comp[cid]["benchmark"] += 1
        by_actual[act]["benchmark"] += 1
        if r.get("market"):
            by_comp[cid]["market"] += 1
            by_actual[act]["market"] += 1
    def finish(x: dict[str, dict[str, int]]) -> dict[str, Any]:
        return {k: {**v, "coverage": v["market"] / v["benchmark"] if v["benchmark"] else 0.0} for k, v in sorted(x.items())}
    m = sum(1 for r in rows if r.get("market"))
    return {"overall": {"benchmark": len(rows), "market": m, "coverage": m / len(rows)}, "by_competition": finish(by_comp), "by_actual": finish(by_actual)}


def persist(rows: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    BENCHMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA,
        "status": "FROZEN",
        "created_at_utc": now(),
        "formal_current_version": "V5.0.1",
        "classification": "NEUTRAL_MEMBERSHIP_RETROSPECTIVE_RESEARCH_FORMAL_WEIGHT_0",
        "seed": SEED,
        "target_n": BENCHMARK_N,
        "target_competitions": list(TARGET_COMPETITIONS),
        "seasons_per_competition": SEASONS_PER_COMP,
        "membership_policy": "identity+90m result only; latest two available seasons per formal domain; Hamilton proportional allocation; SHA256 rank independent of outcome correctness and model/market availability",
        "source_meta": meta,
        "rows": rows,
        "governance": {
            "immutable_membership": True,
            "market_availability_does_not_control_membership": True,
            "research_only": True,
            "formal_weight": 0,
            "not_promotion_evidence": True,
            "not_ev_evidence": True,
        },
    }
    if BENCHMARK_PATH.exists():
        old_payload = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
        if old_payload.get("schema_version") != SCHEMA:
            raise RuntimeError("benchmark schema drift")
        old_keys = [old._identity_key(r) for r in old_payload.get("rows", [])]
        new_keys = [old._identity_key(r) for r in rows]
        if old_keys != new_keys:
            raise RuntimeError("immutable V6.13.1 membership drift; bump version")
        return old_payload
    BENCHMARK_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    eligible, meta = read_identity_pool()
    rows = select(eligible)
    benchmark = persist(rows, meta)
    rows = benchmark["rows"]
    comps = sorted({r["competition_id"] for r in rows})
    strata = sorted({(r["competition_id"], r["season"]) for r in rows})
    mrows = market_rows(rows)
    two_season_ok = all(len(benchmark["source_meta"]["selected_seasons"].get(c, [])) >= 2 for c in TARGET_COMPETITIONS)
    full_domain_ok = set(comps) == set(TARGET_COMPETITIONS)
    status = "PASS" if full_domain_ok and two_season_ok and len(rows) == BENCHMARK_N else "PARTIAL_COVERAGE"

    report = {
        "schema_version": "V6.13.1-neutral-fixed1000-1x2-status-r1",
        "generated_at_utc": now(),
        "status": status,
        "formal_current_version": "V5.0.1",
        "benchmark_path": str(BENCHMARK_PATH.relative_to(ROOT)),
        "benchmark_sha256": sha(BENCHMARK_PATH),
        "benchmark_n": len(rows),
        "competition_count": len(comps),
        "competitions": comps,
        "season_strata_count": len(strata),
        "all_17_domains_present": full_domain_ok,
        "two_seasons_each_domain": two_season_ok,
        "market_track_coverage": coverage(rows),
        "market_track_metrics_on_available_subset": old.multiclass_scores(mrows),
        "market_track_calibration_top1": old.top1_ece(mrows),
        "market_track_draw_audit": old.draw_audit(mrows),
        "market_track_selective_rules": {
            "baseline_062_062": old.selective(mrows, 0.62, 0.62, False),
            "fixed_064_060": old.selective(mrows, 0.64, 0.60, False),
            "v6128_066_060": old.selective(mrows, 0.66, 0.60, False),
        },
        "diagnostic_100_variance_market_subset": old.deterministic_100_variance(mrows) if len(mrows) >= 100 else {"status": "INSUFFICIENT_MARKET_ROWS"},
        "v6130_defect_repair": {
            "defect": "market availability controlled benchmark membership and excluded KOR_KLeague1 + UEFA_ChampionsLeague",
            "v6130_competition_count": 15,
            "repair": "neutral membership first; each track reports coverage afterward",
            "benchmark_membership_conditioned_on_market": False,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "true_postfreeze_forward_gate_still_required": True,
        },
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "benchmark_n": len(rows),
        "competition_count": len(comps),
        "all_17_domains_present": full_domain_ok,
        "two_seasons_each_domain": two_season_ok,
        "market_coverage": report["market_track_coverage"]["overall"],
        "market_metrics": report["market_track_metrics_on_available_subset"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
