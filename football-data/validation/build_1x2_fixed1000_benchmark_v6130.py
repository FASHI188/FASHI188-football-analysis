#!/usr/bin/env python3
"""V6.13.0 fixed 1000-match 1X2 research benchmark.

Purpose
-------
Replace unstable ad-hoc 100-match / "best-data 500" diagnostics with one immutable,
representative, deterministic 1000-match benchmark drawn only from the 17 formal
competition domains and each domain's two most recent eligible seasons.

Governance
----------
Research only (formal_weight=0). Historical odds are retrospective closing-price
references and do not contain original tradable quote timestamps. This benchmark is
for model diagnosis/comparison only and can never satisfy CURRENT promotion or EV gates.

Key anti-leakage rules
----------------------
* Candidate inclusion is based on competition, season, parsable match identity/result,
  and availability of a complete 1X2 reference row. Correctness is never used.
* Sampling rank is SHA-256(identity + fixed seed), independent of outcome/prediction.
* The benchmark membership is persisted once and must not be rewritten silently.
* 100-match slices are diagnostic only; model ranking is based on the full 1000 plus
  proper scores, calibration, coverage and subgroup stability.
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

from diagnose_1x2_market_anchor_v697 import _extract_odds
from platform_core import parse_match_date
import validate_1x2_crossseason_phase_v6123 as phase

TARGET_COMPETITIONS = (
    "ARG_Primera", "BRA_SerieA", "ENG_PremierLeague", "ESP_LaLiga",
    "FRA_Ligue1", "GER_Bundesliga", "ITA_SerieA", "JPN_J1",
    "KOR_KLeague1", "NED_Eredivisie", "NOR_Eliteserien", "POR_PrimeiraLiga",
    "SCO_Premiership", "SUI_SuperLeague", "SWE_Allsvenskan",
    "UEFA_ChampionsLeague", "USA_MLS",
)
BENCHMARK_N = 1000
SEASONS_PER_COMP = 2
SEED = "V6.13.0-FIXED1000-20260727"
SCHEMA = "V6.13.0-fixed1000-1x2-benchmark-r1"
BENCHMARK_PATH = ROOT / "benchmarks" / "v6_1x2_fixed1000_v6130.json"
STATUS_PATH = ROOT / "manifests" / "v6_1x2_fixed1000_v6130_status.json"
Z95 = 1.959963984540054
DIRECTIONS = ("home", "draw", "away")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _actual(raw: dict[str, str]) -> str | None:
    ftr = str(raw.get("FTR") or raw.get("Result") or "").strip().upper()
    if ftr in {"H", "HOME"}:
        return "home"
    if ftr in {"D", "DRAW"}:
        return "draw"
    if ftr in {"A", "AWAY"}:
        return "away"
    try:
        hg = int(float(str(raw.get("FTHG") or raw.get("HG") or "")))
        ag = int(float(str(raw.get("FTAG") or raw.get("AG") or "")))
    except (TypeError, ValueError):
        return None
    return "home" if hg > ag else "away" if ag > hg else "draw"


def _season_label(raw: dict[str, str], path: Path) -> str:
    return str(raw.get("season") or raw.get("Season") or path.stem).strip()


def _season_sort_key(label: str, first_date: str) -> tuple[int, str]:
    return phase._season_sort_key(label, first_date)


def _identity_key(row: dict[str, Any]) -> str:
    return "|".join([
        str(row["competition_id"]), str(row["season"]), str(row["date"]),
        str(row["home_team"]), str(row["away_team"]), str(row["row_index"]),
    ])


def _sample_rank(row: dict[str, Any]) -> str:
    return hashlib.sha256((SEED + "|" + _identity_key(row)).encode("utf-8")).hexdigest()


def read_candidate_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    processed = ROOT / "processed"
    all_rows: list[dict[str, Any]] = []
    duplicate_keys: Counter[str] = Counter()
    parse_failures = 0
    odds_failures = 0
    result_failures = 0

    for cid in TARGET_COMPETITIONS:
        comp_dir = processed / cid
        if not comp_dir.exists():
            continue
        for path in sorted(comp_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row_index, raw0 in enumerate(csv.DictReader(handle)):
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    actual = _actual(raw)
                    if actual is None:
                        result_failures += 1
                        continue
                    extracted = _extract_odds(raw)
                    if extracted is None:
                        odds_failures += 1
                        continue
                    market, provider = extracted
                    season = _season_label(raw, path)
                    date_raw = str(raw.get("Date") or "").strip()
                    if not date_raw:
                        parse_failures += 1
                        continue
                    try:
                        date_iso = parse_match_date(date_raw, season).isoformat()
                    except Exception:
                        parse_failures += 1
                        continue
                    home = str(raw.get("HomeTeam") or raw.get("Home") or "").strip()
                    away = str(raw.get("AwayTeam") or raw.get("Away") or "").strip()
                    if not home or not away:
                        parse_failures += 1
                        continue
                    probs = {d: float(market[d]) for d in DIRECTIONS}
                    s = sum(probs.values())
                    if not math.isfinite(s) or s <= 0:
                        odds_failures += 1
                        continue
                    if abs(s - 1.0) > 1e-6:
                        probs = {d: probs[d] / s for d in DIRECTIONS}
                    pick = max(DIRECTIONS, key=lambda d: probs[d])
                    row = {
                        "competition_id": cid,
                        "season": season,
                        "date": date_iso,
                        "row_index": row_index,
                        "home_team": home,
                        "away_team": away,
                        "actual": actual,
                        "probabilities": probs,
                        "pick": pick,
                        "pmax": probs[pick],
                        "provider": provider,
                        "source_file": str(path.relative_to(ROOT)),
                    }
                    key = _identity_key(row)
                    duplicate_keys[key] += 1
                    all_rows.append(row)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in all_rows:
        groups[(r["competition_id"], r["season"])].append(r)

    selected_seasons: dict[str, list[str]] = {}
    eligible_rows: list[dict[str, Any]] = []
    for cid in TARGET_COMPETITIONS:
        seasons = []
        for (gc, season), rows in groups.items():
            if gc != cid or not rows:
                continue
            first = min(str(r["date"]) for r in rows)
            seasons.append((season, first, rows))
        seasons.sort(key=lambda x: _season_sort_key(x[0], x[1]))
        chosen = seasons[-SEASONS_PER_COMP:]
        selected_seasons[cid] = [x[0] for x in chosen]
        for _, _, rows in chosen:
            eligible_rows.extend(rows)

    meta = {
        "raw_candidate_rows": len(all_rows),
        "eligible_latest_two_season_rows": len(eligible_rows),
        "selected_seasons": selected_seasons,
        "missing_competitions": [c for c in TARGET_COMPETITIONS if not selected_seasons.get(c)],
        "competitions_with_lt_two_seasons": [c for c in TARGET_COMPETITIONS if len(selected_seasons.get(c, [])) < 2],
        "duplicate_identity_keys": sum(1 for n in duplicate_keys.values() if n > 1),
        "parse_failures": parse_failures,
        "odds_failures": odds_failures,
        "result_failures": result_failures,
    }
    return eligible_rows, meta


def hamilton_quotas(rows: list[dict[str, Any]], n: int) -> dict[tuple[str, str], int]:
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        strata[(r["competition_id"], r["season"])].append(r)
    if len(rows) < n:
        raise RuntimeError(f"eligible pool smaller than benchmark: pool={len(rows)} n={n}")
    keys = sorted(strata)
    quotas = {k: 1 for k in keys}
    remaining = n - len(keys)
    if remaining < 0:
        raise RuntimeError("benchmark smaller than number of strata")
    total_capacity = sum(max(0, len(strata[k]) - 1) for k in keys)
    if total_capacity < remaining:
        raise RuntimeError("insufficient stratum capacity")
    ideals = {}
    for k in keys:
        cap = max(0, len(strata[k]) - 1)
        ideals[k] = remaining * cap / total_capacity if total_capacity else 0.0
        quotas[k] += min(cap, int(math.floor(ideals[k])))
    assigned = sum(quotas.values())
    remainders = sorted(
        keys,
        key=lambda k: (ideals[k] - math.floor(ideals[k]), len(strata[k]), k),
        reverse=True,
    )
    while assigned < n:
        moved = False
        for k in remainders:
            if quotas[k] < len(strata[k]):
                quotas[k] += 1
                assigned += 1
                moved = True
                if assigned >= n:
                    break
        if not moved:
            raise RuntimeError("could not complete Hamilton allocation")
    return quotas


def build_membership(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    quotas = hamilton_quotas(rows, BENCHMARK_N)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        strata[(r["competition_id"], r["season"])].append(r)
    out: list[dict[str, Any]] = []
    for k, group in sorted(strata.items()):
        ranked = sorted(group, key=lambda r: (_sample_rank(r), _identity_key(r)))
        out.extend(ranked[:quotas[k]])
    out.sort(key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    if len(out) != BENCHMARK_N:
        raise RuntimeError(f"benchmark size mismatch: {len(out)}")
    return out


def wilson(hits: int, n: int, z: float = Z95) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = hits / n
    z2 = z * z
    den = 1 + z2 / n
    center = (p + z2 / (2 * n)) / den
    rad = z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n) / den
    return max(0.0, center - rad), min(1.0, center + rad)


def multiclass_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"count": 0}
    hits = sum(r["pick"] == r["actual"] for r in rows)
    eps = 1e-15
    logloss = 0.0
    brier = 0.0
    rps = 0.0
    for r in rows:
        p = r["probabilities"]
        y = {d: 1.0 if r["actual"] == d else 0.0 for d in DIRECTIONS}
        logloss -= math.log(max(eps, min(1.0, p[r["actual"]])))
        brier += sum((p[d] - y[d]) ** 2 for d in DIRECTIONS)
        cp1, cy1 = p["home"], y["home"]
        cp2, cy2 = p["home"] + p["draw"], y["home"] + y["draw"]
        rps += ((cp1 - cy1) ** 2 + (cp2 - cy2) ** 2) / 2.0
    lo, hi = wilson(hits, n)
    return {
        "count": n,
        "hits": int(hits),
        "accuracy": hits / n,
        "wilson95": [lo, hi],
        "log_loss": logloss / n,
        "brier": brier / n,
        "rps": rps / n,
    }


def top1_ece(rows: list[dict[str, Any]], bins: int = 10) -> dict[str, Any]:
    buckets = []
    ece = 0.0
    n = len(rows)
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sub = [r for r in rows if (lo <= r["pmax"] < hi) or (i == bins - 1 and r["pmax"] == 1.0)]
        if not sub:
            continue
        conf = statistics.mean(float(r["pmax"]) for r in sub)
        acc = statistics.mean(1.0 if r["pick"] == r["actual"] else 0.0 for r in sub)
        ece += len(sub) / n * abs(acc - conf)
        buckets.append({"lo": lo, "hi": hi, "count": len(sub), "mean_confidence": conf, "accuracy": acc})
    return {"ece": ece, "bins": buckets}


def draw_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    actual_draws = sum(r["actual"] == "draw" for r in rows)
    predicted_draw_top1 = sum(r["pick"] == "draw" for r in rows)
    correct_draw_top1 = sum(r["pick"] == "draw" and r["actual"] == "draw" for r in rows)
    mean_pdraw = statistics.mean(float(r["probabilities"]["draw"]) for r in rows)
    draw_brier = statistics.mean(
        (float(r["probabilities"]["draw"]) - (1.0 if r["actual"] == "draw" else 0.0)) ** 2 for r in rows
    )
    return {
        "actual_draw_rate": actual_draws / n if n else None,
        "mean_predicted_draw_probability": mean_pdraw if n else None,
        "draw_probability_bias": (mean_pdraw - actual_draws / n) if n else None,
        "draw_brier": draw_brier if n else None,
        "draw_top1_rate": predicted_draw_top1 / n if n else None,
        "draw_top1_precision": correct_draw_top1 / predicted_draw_top1 if predicted_draw_top1 else None,
        "draw_top1_recall": correct_draw_top1 / actual_draws if actual_draws else None,
    }


def selective(rows: list[dict[str, Any]], home_t: float, away_t: float, draws: bool = False) -> dict[str, Any]:
    selected = []
    for r in rows:
        if r["pick"] == "home" and r["pmax"] >= home_t:
            selected.append(r)
        elif r["pick"] == "away" and r["pmax"] >= away_t:
            selected.append(r)
        elif draws and r["pick"] == "draw":
            selected.append(r)
    base = multiclass_scores(selected)
    base.update({
        "coverage": len(selected) / len(rows) if rows else 0.0,
        "home_threshold": home_t,
        "away_threshold": away_t,
        "draws_selected": draws,
        "by_direction": {
            d: multiclass_scores([r for r in selected if r["pick"] == d]) for d in DIRECTIONS
        },
    })
    return base


def subgroup(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r[key])].append(r)
    return {k: multiclass_scores(v) for k, v in sorted(groups.items())}


def pmax_bins(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = ((0.0, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01))
    out = {}
    for lo, hi in bounds:
        sub = [r for r in rows if lo <= float(r["pmax"]) < hi]
        out[f"{lo:.2f}-{min(hi,1.0):.2f}"] = multiclass_scores(sub)
    return out


def windows100(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    windows = []
    for i in range(0, len(ordered), 100):
        chunk = ordered[i:i+100]
        if len(chunk) < 100:
            continue
        s = multiclass_scores(chunk)
        s.update({"start": i, "stop": i+100, "first_date": chunk[0]["date"], "last_date": chunk[-1]["date"]})
        windows.append(s)
    acc = [w["accuracy"] for w in windows]
    return {
        "windows": windows,
        "summary": {
            "count": len(windows),
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "median_accuracy": statistics.median(acc) if acc else None,
            "stdev_accuracy": statistics.pstdev(acc) if len(acc) > 1 else 0.0 if acc else None,
            "min_accuracy": min(acc) if acc else None,
            "max_accuracy": max(acc) if acc else None,
        },
    }


def deterministic_100_variance(rows: list[dict[str, Any]], reps: int = 200) -> dict[str, Any]:
    accs = []
    for rep in range(reps):
        ranked = sorted(
            rows,
            key=lambda r: hashlib.sha256((f"{SEED}|diag|{rep}|" + _identity_key(r)).encode("utf-8")).hexdigest(),
        )
        sub = ranked[:100]
        accs.append(multiclass_scores(sub)["accuracy"])
    s = sorted(accs)
    return {
        "repetitions": reps,
        "sample_size": 100,
        "mean_accuracy": statistics.mean(accs),
        "stdev_accuracy": statistics.pstdev(accs),
        "p05_accuracy": s[max(0, math.floor(0.05 * (len(s)-1)))],
        "p95_accuracy": s[min(len(s)-1, math.ceil(0.95 * (len(s)-1)))],
        "min_accuracy": min(accs),
        "max_accuracy": max(accs),
        "use": "DIAGNOSTIC_VARIANCE_ONLY_NOT_MODEL_SELECTION",
    }


def persist_membership(rows: list[dict[str, Any]], source_meta: dict[str, Any]) -> dict[str, Any]:
    BENCHMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA,
        "status": "FROZEN",
        "created_at_utc": utc_now(),
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_RESEARCH_BENCHMARK_FORMAL_WEIGHT_0",
        "seed": SEED,
        "target_n": BENCHMARK_N,
        "target_competitions": list(TARGET_COMPETITIONS),
        "seasons_per_competition": SEASONS_PER_COMP,
        "sampling_policy": "latest two eligible seasons per target competition; Hamilton proportional allocation; SHA256 identity rank; no correctness/outcome-dependent sampling",
        "source_meta": source_meta,
        "rows": rows,
        "governance": {
            "immutable_membership": True,
            "research_only": True,
            "formal_weight": 0,
            "historical_closing_odds_without_original_quote_timestamp": True,
            "not_promotion_evidence": True,
            "not_ev_evidence": True,
        },
    }
    if BENCHMARK_PATH.exists():
        old = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
        old_keys = [_identity_key(r) for r in old.get("rows", [])]
        new_keys = [_identity_key(r) for r in rows]
        if old.get("schema_version") != SCHEMA or old_keys != new_keys:
            raise RuntimeError("immutable fixed1000 membership drift detected; bump schema/version instead of rewriting")
        return old
    BENCHMARK_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    candidates, source_meta = read_candidate_rows()
    membership_rows = build_membership(candidates)
    benchmark = persist_membership(membership_rows, source_meta)
    rows = benchmark["rows"]

    all_metrics = multiclass_scores(rows)
    report = {
        "schema_version": "V6.13.0-fixed1000-1x2-status-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "benchmark_path": str(BENCHMARK_PATH.relative_to(ROOT)),
        "benchmark_sha256": file_sha(BENCHMARK_PATH),
        "benchmark_n": len(rows),
        "competition_count": len({r["competition_id"] for r in rows}),
        "season_strata_count": len({(r["competition_id"], r["season"]) for r in rows}),
        "baseline_market_argmax_all1000": all_metrics,
        "calibration_top1": top1_ece(rows),
        "draw_audit": draw_audit(rows),
        "selective_rules": {
            "baseline_062_062": selective(rows, 0.62, 0.62, False),
            "fixed_064_060": selective(rows, 0.64, 0.60, False),
            "current_v6128_frozen_066_060": selective(rows, 0.66, 0.60, False),
        },
        "by_competition": subgroup(rows, "competition_id"),
        "by_provider": subgroup(rows, "provider"),
        "by_actual_result": subgroup(rows, "actual"),
        "pmax_bins": pmax_bins(rows),
        "chronological_100_match_windows": windows100(rows),
        "deterministic_100_match_variance": deterministic_100_variance(rows),
        "methodological_repairs": {
            "best_data_500_replaced": True,
            "fixed_1000_membership": True,
            "representative_stratified_sampling": True,
            "result_dependent_sampling": False,
            "100_match_model_selection_disabled": True,
            "proper_scores_required": True,
            "draw_probability_separate_from_draw_argmax": True,
            "coverage_reported_for_selective_rules": True,
            "same_benchmark_for_all_rule_comparisons": True,
            "historical_and_true_forward_evidence_separated": True,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "runtime_probability_change": False,
            "automatic_promotion": False,
            "forward_gate_still_required": True,
        },
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "benchmark_n": report["benchmark_n"],
        "competition_count": report["competition_count"],
        "baseline": report["baseline_market_argmax_all1000"],
        "draw_audit": report["draw_audit"],
        "selective_rules": report["selective_rules"],
        "window_summary": report["chronological_100_match_windows"]["summary"],
        "sample100_variance": report["deterministic_100_match_variance"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
