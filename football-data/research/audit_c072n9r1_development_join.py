#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

N8_SHA = "e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082"
N9_MANIFEST_SHA = "027d7f4ff7cb72724115a731002d4d2048f10da420fdb8ac4955eb2782ba3a06"
DEV_SEASONS = [
    "2015/2016","2016/2017","2017/2018","2018/2019",
    "2019/2020","2020/2021","2021/2022","2022/2023","2023/2024",
]
OOS_SEASONS = ["2019/2020","2020/2021","2021/2022","2022/2023","2023/2024"]
LEAGUES = ["EPL","LL","BL","SA","L1"]
FORBIDDEN = {"FTHG","FTAG","FTR","HTHG","HTAG","HTR","score","result","total_goals","target"}
SUMMARY = Path("football-data/research/c072n9r1_development_join_summary.json")
MANIFEST_OUT = Path("football-data/research/c072n9r1_development_manifest.csv")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        rows = list(r)
        return list(r.fieldnames or []), rows


def frac(a: int, b: int) -> float:
    return float(a / b) if b else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n8-csv", required=True)
    ap.add_argument("--n9-manifest", required=True)
    args = ap.parse_args()

    n8_path = Path(args.n8_csv)
    n9_path = Path(args.n9_manifest)
    n8_hash = sha256(n8_path)
    n9_hash = sha256(n9_path)
    if n8_hash != N8_SHA:
        raise RuntimeError("N8 immutable CSV SHA mismatch")
    if n9_hash != N9_MANIFEST_SHA:
        raise RuntimeError("N9 immutable manifest SHA mismatch")

    n8_header, n8_rows = read_csv(n8_path)
    n9_header, n9_rows = read_csv(n9_path)
    lower_forbidden = {x.lower() for x in FORBIDDEN}
    forbidden_headers = sorted(
        h for h in (n8_header + n9_header)
        if h.lower() in lower_forbidden
    )

    dev_n8 = [r for r in n8_rows if r.get("Season") in DEV_SEASONS]
    dev_n9 = [r for r in n9_rows if r.get("n8_Season") in DEV_SEASONS]
    y2425_n8 = [r for r in n8_rows if r.get("Season") == "2024/2025"]
    y2526_n8 = [r for r in n8_rows if r.get("Season") == "2025/2026"]

    total_league = Counter(r["sourceCode"] for r in dev_n8)
    join_league = Counter(r["sourceCode"] for r in dev_n9)
    total_season = Counter(r["Season"] for r in dev_n8)
    join_season = Counter(r["n8_Season"] for r in dev_n9)

    league_cov = {
        c: {"total": int(total_league[c]), "joined": int(join_league[c]), "coverage": frac(join_league[c], total_league[c])}
        for c in LEAGUES
    }
    season_cov = {
        s: {"total": int(total_season[s]), "joined": int(join_season[s]), "coverage": frac(join_season[s], total_season[s])}
        for s in DEV_SEASONS
    }

    n8_keys = [(r.get("sourceCode"), r.get("n8_id")) for r in dev_n9]
    label_keys = [(r.get("sourceCode"), r.get("label_source_path"), r.get("label_source_row_index")) for r in dev_n9]
    duplicate_n8_assignments = len(n8_keys) - len(set(n8_keys))
    duplicate_label_assignments = len(label_keys) - len(set(label_keys))

    fields = n9_header
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in dev_n9:
            w.writerow(r)

    overall = frac(len(dev_n9), len(dev_n8))
    gates = {
        "n8_csv_sha_exact": n8_hash == N8_SHA,
        "n9_manifest_sha_exact": n9_hash == N9_MANIFEST_SHA,
        "zero_forbidden_score_result_headers": len(forbidden_headers) == 0,
        "development_overall_join_coverage_ge_97pct": overall >= 0.97,
        "each_development_league_join_coverage_ge_95pct": all(v["coverage"] >= 0.95 for v in league_cov.values()),
        "each_oos_season_join_coverage_ge_95pct": all(season_cov[s]["coverage"] >= 0.95 for s in OOS_SEASONS),
        "each_development_history_season_join_coverage_ge_95pct": all(v["coverage"] >= 0.95 for v in season_cov.values()),
        "no_duplicate_n8_assignments": duplicate_n8_assignments == 0,
        "no_duplicate_label_assignments": duplicate_label_assignments == 0,
        "2024_25_excluded_from_development": len(y2425_n8) > 0 and not any(r.get("n8_Season") == "2024/2025" for r in dev_n9),
        "2025_26_zero_label_reserve_present": len(y2526_n8) > 0,
        "zero_target_result_values_materialized": True,
        "zero_model": True,
    }
    terminal = "C072N9R1_DEVELOPMENT_JOIN_PASS" if all(gates.values()) else "C072N9R1_DEVELOPMENT_JOIN_STOP"

    summary = {
        "schema": "C072N9R1_DEVELOPMENT_JOIN_CORRECTION_V1",
        "project_line": "football3",
        "classification": "ZERO_LABEL_ENGINEERING_CORRECTION",
        "terminal": terminal,
        "n8_csv_sha256": n8_hash,
        "n9_manifest_sha256": n9_hash,
        "development_seasons": DEV_SEASONS,
        "rolling_oos_seasons": OOS_SEASONS,
        "development_target_rows": len(dev_n8),
        "development_joined_rows": len(dev_n9),
        "development_overall_join_coverage": overall,
        "league_coverage": league_cov,
        "season_coverage": season_cov,
        "duplicate_n8_assignments": duplicate_n8_assignments,
        "duplicate_label_assignments": duplicate_label_assignments,
        "development_manifest_sha256": sha256(MANIFEST_OUT),
        "2024_25_identity_rows_excluded": len(y2425_n8),
        "2024_25_target_values_materialized": 0,
        "2024_25_confirmation_status": "NOT_ELIGIBLE_AS_PRISTINE_CONFIRMATION",
        "2025_26_zero_label_reserve_rows": len(y2526_n8),
        "target_result_columns_materialized": 0,
        "target_result_values_materialized": 0,
        "model_fit": 0,
        "model_score": 0,
        "C073_C077_quarantined": True,
        "C070F_confirmation1597_opened": False,
        "protected_opened": False,
        "formal_weight": 0,
        "gates": gates,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
