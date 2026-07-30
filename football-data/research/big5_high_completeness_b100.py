#!/usr/bin/env python3
"""Research-only Big Five high-completeness 100-match benchmark.

Select 20 matches per league from the latest usable completed season using only
predefined data-completeness gates. Selection never uses match outcome or model
performance. The current formal-core Champion is then replayed with the existing
nested, time-ordered OOS parameter-selection chain.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from football_v460_engine import load_config  # noqa: E402
from nested_backtest_v460 import _objective, evaluate_season  # noqa: E402
from platform_core import (  # noqa: E402
    ROOT,
    canonical_team_name,
    load_aliases,
    parse_match_date,
    read_processed_matches,
)

OUT = ROOT.parent / "artifacts/research/big5_high_completeness_b100"
SEED = "BIG5-HIGH-COMPLETENESS-B100-V1"
TARGET_PER_LEAGUE = 20
BIG5 = {
    "ENG_PremierLeague": "英超",
    "GER_Bundesliga": "德甲",
    "ITA_SerieA": "意甲",
    "FRA_Ligue1": "法甲",
    "ESP_LaLiga": "西甲",
}
OUTCOMES = ("home", "draw", "away")

CORE_STATS = (
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR",
    "HS", "AS", "HST", "AST", "HC", "AC", "HY", "AY",
)
OPEN_1X2 = (
    ("B365H", "B365D", "B365A"),
    ("PSH", "PSD", "PSA"),
    ("AvgH", "AvgD", "AvgA"),
    ("MaxH", "MaxD", "MaxA"),
)
CLOSE_1X2 = (
    ("B365CH", "B365CD", "B365CA"),
    ("PSCH", "PSCD", "PSCA"),
    ("AvgCH", "AvgCD", "AvgCA"),
    ("MaxCH", "MaxCD", "MaxCA"),
)
OPEN_OU = (
    ("B365>2.5", "B365<2.5"),
    ("P>2.5", "P<2.5"),
    ("Avg>2.5", "Avg<2.5"),
    ("Max>2.5", "Max<2.5"),
)
CLOSE_OU = (
    ("B365C>2.5", "B365C<2.5"),
    ("PC>2.5", "PC<2.5"),
    ("AvgC>2.5", "AvgC<2.5"),
    ("MaxC>2.5", "MaxC<2.5"),
)
OPEN_AH = (
    ("B365AHH", "B365AHA"),
    ("PAHH", "PAHA"),
    ("AvgAHH", "AvgAHA"),
    ("MaxAHH", "MaxAHA"),
)
CLOSE_AH = (
    ("B365CAHH", "B365CAHA"),
    ("PCAHH", "PCAHA"),
    ("AvgCAHH", "AvgCAHA"),
    ("MaxCAHH", "MaxCAHA"),
)
OPTIONAL_STATS = ("HF", "AF", "HR", "AR", "Referee")


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def nonempty(value: Any) -> bool:
    return str(value if value is not None else "").strip() != ""


def numeric(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def odds(value: Any) -> bool:
    return numeric(value) and float(value) > 1.0


def complete_group(row: dict[str, str], groups: tuple[tuple[str, ...], ...], *, odds_values: bool = True) -> tuple[str, ...] | None:
    for group in groups:
        validator = odds if odds_values else numeric
        if all(validator(row.get(key)) for key in group):
            return group
    return None


def row_completeness(row: dict[str, str]) -> dict[str, Any]:
    open_1x2 = complete_group(row, OPEN_1X2)
    close_1x2 = complete_group(row, CLOSE_1X2)
    open_ou = complete_group(row, OPEN_OU)
    close_ou = complete_group(row, CLOSE_OU)
    open_ah = complete_group(row, OPEN_AH)
    close_ah = complete_group(row, CLOSE_AH)
    required_stats = all(nonempty(row.get(key)) for key in CORE_STATS)
    time_present = nonempty(row.get("Time"))
    open_ah_line = numeric(row.get("AHh"))
    close_ah_line = numeric(row.get("AHCh"))
    passed = all((
        required_stats,
        time_present,
        open_1x2 is not None,
        close_1x2 is not None,
        open_ou is not None,
        close_ou is not None,
        open_ah is not None,
        close_ah is not None,
        open_ah_line,
        close_ah_line,
    ))
    optional_count = sum(nonempty(row.get(key)) for key in OPTIONAL_STATS)
    return {
        "passed": passed,
        "required_stats_complete": required_stats,
        "kickoff_time_present": time_present,
        "open_1x2_group": list(open_1x2) if open_1x2 else None,
        "close_1x2_group": list(close_1x2) if close_1x2 else None,
        "open_ou_group": list(open_ou) if open_ou else None,
        "close_ou_group": list(close_ou) if close_ou else None,
        "open_ah_line": row.get("AHh") if open_ah_line else None,
        "close_ah_line": row.get("AHCh") if close_ah_line else None,
        "open_ah_group": list(open_ah) if open_ah else None,
        "close_ah_group": list(close_ah) if close_ah else None,
        "optional_stats_present": optional_count,
    }


def raw_rows(competition_id: str) -> dict[str, dict[str, Any]]:
    matches = read_processed_matches(competition_id)
    aliases = load_aliases()
    by_identity: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for match in matches:
        by_identity[(match.date.date().isoformat(), match.home_team, match.away_team)].append(match)

    output: dict[str, dict[str, Any]] = {}
    directory = ROOT / "processed" / competition_id
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for line_no, raw in enumerate(csv.DictReader(handle), start=2):
                row = {str(k).strip(): "" if v is None else str(v).strip() for k, v in raw.items() if k}
                if not row.get("HomeTeam") or not row.get("AwayTeam") or not row.get("Date"):
                    continue
                season_hint = row.get("season") or row.get("Season") or ""
                try:
                    date = parse_match_date(row["Date"], season_hint).date().isoformat()
                except Exception:
                    continue
                home = canonical_team_name(competition_id, row["HomeTeam"], aliases)
                away = canonical_team_name(competition_id, row["AwayTeam"], aliases)
                candidates = by_identity.get((date, home, away), [])
                if len(candidates) != 1:
                    continue
                match = candidates[0]
                key = f"{match.season}|{date}|{home}|{away}"
                quality = row_completeness(row)
                output[key] = {
                    "match_key": key,
                    "competition_id": competition_id,
                    "season": match.season,
                    "date": date,
                    "kickoff_time": row.get("Time"),
                    "home_team": home,
                    "away_team": away,
                    "actual_score": f"{match.home_goals}-{match.away_goals}",
                    "actual_outcome": match.result,
                    "source_path": str(path.relative_to(ROOT)),
                    "source_line": line_no,
                    "quality": quality,
                }
    return output


def season_order(matches: list[Any]) -> list[str]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        grouped[match.season].append(match)
    return sorted(grouped, key=lambda season: min(m.date for m in grouped[season]))


def champion_oos(competition_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    matches = read_processed_matches(competition_id)
    grouped: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        grouped[match.season].append(match)
    seasons = season_order(matches)
    config = load_config()
    candidates = config["candidate_parameters"]
    cache: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for candidate_index, candidate in enumerate(candidates):
        for season in seasons:
            ordered = sorted(grouped[season], key=lambda m: (m.date, m.home_team, m.away_team))
            cache[candidate_index][season] = evaluate_season(
                competition_id, ordered, candidate, use_team_effects=True
            )

    records: dict[str, dict[str, Any]] = {}
    folds = []
    for outer_index in range(1, len(seasons)):
        season = seasons[outer_index]
        prior = seasons[:outer_index]
        ranked = []
        for candidate_index, candidate in enumerate(candidates):
            prior_records = [record for prior_season in prior for record in cache[candidate_index][prior_season]]
            ranked.append((_objective(prior_records), candidate_index, candidate, len(prior_records)))
        objective, selected_index, selected_parameters, selection_count = sorted(ranked, key=lambda x: (x[0], x[1]))[0]
        outer_records = cache[selected_index][season]
        for record in outer_records:
            records[record["match_key"]] = record
        folds.append({
            "outer_season": season,
            "prior_seasons": prior,
            "selected_candidate_index": selected_index,
            "selected_parameters": selected_parameters,
            "selection_objective": objective,
            "selection_prediction_count": selection_count,
            "oos_records": len(outer_records),
        })
    return records, {"seasons": seasons, "folds": folds}


def deterministic_rank(competition_id: str, match_key: str) -> str:
    return hashlib.sha256(f"{SEED}|{competition_id}|{match_key}".encode("utf-8")).hexdigest()


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    confusion = {actual: {predicted: 0 for predicted in OUTCOMES} for actual in OUTCOMES}
    logloss = []
    brier = []
    rps = []
    for record in records:
        actual = record["actual_outcome"]
        probabilities = {key: float(record[f"p_{key}"]) for key in OUTCOMES}
        predicted = max(OUTCOMES, key=lambda key: (probabilities[key], -OUTCOMES.index(key)))
        confusion[actual][predicted] += 1
        logloss.append(-math.log(max(1e-15, probabilities[actual])))
        brier.append(float(record["one_x_two_brier"]))
        rps.append(float(record["one_x_two_rps"]))

    per_class = {}
    for label in OUTCOMES:
        tp = confusion[label][label]
        actual_n = sum(confusion[label].values())
        predicted_n = sum(confusion[actual][label] for actual in OUTCOMES)
        precision = tp / predicted_n if predicted_n else 0.0
        recall = tp / actual_n if actual_n else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "precision": precision, "recall": recall, "f1": f1,
            "actual_count": actual_n, "predicted_count": predicted_n,
        }
    correct = sum(confusion[label][label] for label in OUTCOMES)
    return {
        "count": len(records),
        "accuracy": correct / len(records),
        "balanced_accuracy": mean(per_class[label]["recall"] for label in OUTCOMES),
        "macro_f1": mean(per_class[label]["f1"] for label in OUTCOMES),
        "logloss": mean(logloss),
        "brier": mean(brier),
        "rps": mean(rps),
        "draw_precision": per_class["draw"]["precision"],
        "draw_recall": per_class["draw"]["recall"],
        "draw_f1": per_class["draw"]["f1"],
        "per_class": per_class,
        "confusion_matrix_actual_rows": confusion,
    }


def select_competition(competition_id: str) -> dict[str, Any]:
    raw = raw_rows(competition_id)
    oos, chain = champion_oos(competition_id)
    seasons = chain["seasons"]
    season_candidates = []
    for season in reversed(seasons[1:]):
        eligible = [
            {**meta, **oos[key]}
            for key, meta in raw.items()
            if meta["season"] == season and meta["quality"]["passed"] and key in oos
        ]
        if len(eligible) >= TARGET_PER_LEAGUE:
            season_candidates = eligible
            selected_season = season
            break
    else:
        selected_season = None
        season_candidates = []

    ordered = sorted(
        season_candidates,
        key=lambda record: deterministic_rank(competition_id, record["match_key"]),
    )
    selected = ordered[:TARGET_PER_LEAGUE]
    return {
        "competition_id": competition_id,
        "competition_zh": BIG5[competition_id],
        "selected_season": selected_season,
        "strict_complete_oos_candidates": len(season_candidates),
        "selected_count": len(selected),
        "selection_seed": SEED,
        "selection_rule": "strict data-completeness gate then SHA-256 deterministic rank; outcome and model performance excluded",
        "champion_metrics": metrics(selected),
        "selected_matches": selected,
        "nested_oos_chain": chain,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Big Five High-Completeness B100 Benchmark",
        "",
        "Research-only; retrospective high-completeness test, not a sealed blind gold-standard set.",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Target: {TARGET_PER_LEAGUE} per league, {TARGET_PER_LEAGUE * len(BIG5)} total",
        f"- Selected: {report['aggregate']['count']}",
        f"- Selection seed: `{SEED}`",
        "- Selection does not use outcome or model performance.",
        "- Actual official lineups are audit-only and are not injected into the replay model.",
        "",
        "## Current Champion baseline",
        "",
    ]
    metric = report["aggregate"]["champion_metrics"]
    if metric.get("count"):
        lines += [
            f"- Full 1X2 accuracy: {metric['accuracy']:.4%}",
            f"- Balanced accuracy: {metric['balanced_accuracy']:.4%}",
            f"- Macro-F1: {metric['macro_f1']:.4%}",
            f"- Draw precision / recall / F1: {metric['draw_precision']:.4%} / {metric['draw_recall']:.4%} / {metric['draw_f1']:.4%}",
            f"- LogLoss / Brier / RPS: {metric['logloss']:.6f} / {metric['brier']:.6f} / {metric['rps']:.6f}",
        ]
    lines += ["", "## Per league", ""]
    for cid, result in report["competitions"].items():
        m = result["champion_metrics"]
        lines.append(
            f"- {result['competition_zh']} `{cid}`: season={result['selected_season']}, "
            f"complete OOS candidates={result['strict_complete_oos_candidates']}, selected={result['selected_count']}, "
            + (f"accuracy={m['accuracy']:.4%}, draw F1={m['draw_f1']:.4%}" if m.get("count") else "no usable records")
        )
    lines += [
        "",
        "This benchmark is the fixed retrospective B100 comparison set for the matrix-compatible E3 experiment.",
        "No formal model, data, config, weights, or CURRENT files are modified.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    competitions = {}
    selected_all = []
    failures = []
    for competition_id in BIG5:
        try:
            result = select_competition(competition_id)
            competitions[competition_id] = result
            selected_all.extend(result["selected_matches"])
        except Exception as exc:
            failures.append({"competition_id": competition_id, "error": f"{type(exc).__name__}: {exc}"})

    report = {
        "schema_version": "1.0",
        "research_status": "PASS" if not failures and len(selected_all) == TARGET_PER_LEAGUE * len(BIG5) else "FAIL",
        "repository_head": repository_head(),
        "scope": "90_minutes_including_stoppage",
        "benchmark_name": "BIG5_HIGH_COMPLETENESS_B100",
        "benchmark_type": "RETROSPECTIVE_HIGH_COMPLETENESS_NOT_SEALED_BLIND",
        "selection_seed": SEED,
        "target_per_league": TARGET_PER_LEAGUE,
        "competitions": competitions,
        "aggregate": {
            "count": len(selected_all),
            "champion_metrics": metrics(selected_all),
            "actual_outcome_counts": dict(Counter(record["actual_outcome"] for record in selected_all)),
        },
        "failures": failures,
        "formal_mutation": {"model": 0, "data": 0, "config": 0, "current": 0},
    }
    (output_dir / "big5_high_completeness_b100.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "big5_high_completeness_b100.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["research_status"],
            "count": report["aggregate"]["count"],
            "metrics": report["aggregate"]["champion_metrics"],
            "failures": failures,
        }, ensure_ascii=False, indent=2))
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
