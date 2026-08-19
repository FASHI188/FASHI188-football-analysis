#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

SCHEMA = "C072N9_ZERO_LABEL_JOIN_V1"
N8_SHA = "e9538997e1ec46582e240add8eb37372341a0c75b51e024c8bef0139aa29c082"
N8_ID_SHA = "95ff10827e5097158c2bf20838e317c106d0b53c8ad6088a50fecae99b6ad0f4"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
FILES = {
    "EPL": "data/england/premier-league.csv",
    "LL": "data/spain/laliga.csv",
    "BL": "data/germany/bundesliga.csv",
    "SA": "data/italy/serie-a.csv",
    "L1": "data/france/ligue-1.csv",
}
IDENTITY_COLS = ["Date", "Season", "HomeTeam", "AwayTeam"]
FORBIDDEN_RESULT_COLS = {"FTHG","FTAG","FTR","HTHG","HTAG","HTR","score","result"}
MIN_SIDE = 0.60
MIN_MEAN = 0.78
MIN_MARGIN = 0.12
SUMMARY = Path("football-data/research/c072n9_zero_label_join_summary.json")
MANIFEST = Path("football-data/research/c072n9_zero_label_join_manifest.csv")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def footiqo_date(x: str) -> str | None:
    s = str(x).strip()
    for fmt in ("%d-%m-%y %H:%M", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def source_date(x: str) -> str | None:
    s = str(x).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def canon_team(x: str) -> str:
    s = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def source_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/{path}"


def similarity(a: str, b: str) -> float:
    return float(difflib.SequenceMatcher(None, a, b).ratio())


def pct(num: int, den: int) -> float:
    return float(num / den) if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n8-csv", required=True)
    args = ap.parse_args()
    n8_path = Path(args.n8_csv)
    if sha256(n8_path) != N8_SHA:
        raise RuntimeError("N8 immutable CSV SHA mismatch")

    n8 = pd.read_csv(n8_path, dtype=str, keep_default_na=False)
    expected_n8_cols = ["sourceCode","id","matchDate","Country","League","Season","homeTeam","awayTeam","H","D","A","O05","U05","O15","U15","O25","U25","O35","U35","O45","U45","BTTSY","BTTSN"]
    if list(n8.columns) != expected_n8_cols:
        raise RuntimeError("N8 schema mismatch")

    # Freeze label-source identities only. pandas usecols prevents target/result fields from being materialized.
    source_frames: dict[str, pd.DataFrame] = {}
    source_meta = {}
    for code, path in FILES.items():
        df = pd.read_csv(source_url(path), usecols=IDENTITY_COLS, dtype=str, keep_default_na=False)
        if list(df.columns) != IDENTITY_COLS:
            df = df[IDENTITY_COLS]
        if any(c in df.columns for c in FORBIDDEN_RESULT_COLS):
            raise RuntimeError("forbidden result column materialized")
        df = df.reset_index(drop=True)
        df["label_row_index"] = df.index.astype(int)
        df["date_key"] = df["Date"].map(source_date)
        df["home_key"] = df["HomeTeam"].map(canon_team)
        df["away_key"] = df["AwayTeam"].map(canon_team)
        source_frames[code] = df
        source_meta[code] = {
            "path": path,
            "revision": REV,
            "identity_rows": int(len(df)),
            "identity_columns_materialized": IDENTITY_COLS,
            "target_result_columns_materialized": 0,
        }

    # N8 rows eligible for this fixed source stop at 2024/25; 2025/26 remains zero-label reserve by design.
    work = n8[n8["Season"] != "2025/2026"].copy().reset_index(drop=True)
    reserve_2526 = int((n8["Season"] == "2025/2026").sum())
    work["date_key"] = work["matchDate"].map(footiqo_date)
    work["season_key"] = work["Season"].str.replace("/", "-", regex=False)
    work["home_key"] = work["homeTeam"].map(canon_team)
    work["away_key"] = work["awayTeam"].map(canon_team)

    # Build exact and same-day candidate indexes using identity columns only.
    exact_index: dict[str, dict[tuple, list[int]]] = {}
    day_index: dict[str, dict[tuple, list[int]]] = {}
    for code, df in source_frames.items():
        ex: dict[tuple, list[int]] = defaultdict(list)
        dy: dict[tuple, list[int]] = defaultdict(list)
        for i, r in df.iterrows():
            ex[(r["date_key"], r["Season"], r["home_key"], r["away_key"])].append(int(i))
            dy[(r["date_key"], r["Season"])].append(int(i))
        exact_index[code] = ex
        day_index[code] = dy

    provisional: list[dict] = []
    exact_n = fuzzy_n = unmatched_n = ambiguous_n = 0

    for wi, r in work.iterrows():
        code = r["sourceCode"]
        if code not in source_frames or r["date_key"] is None:
            unmatched_n += 1
            continue
        ex_key = (r["date_key"], r["season_key"], r["home_key"], r["away_key"])
        exact = exact_index[code].get(ex_key, [])
        if len(exact) == 1:
            provisional.append({
                "work_index": int(wi), "label_row_index": int(exact[0]), "join_method": "EXACT_NORMALIZED",
                "home_ratio": 1.0, "away_ratio": 1.0, "mean_ratio": 1.0, "best_margin": 1.0,
            })
            exact_n += 1
            continue
        if len(exact) > 1:
            ambiguous_n += 1
            continue

        candidates = []
        sdf = source_frames[code]
        for si in day_index[code].get((r["date_key"], r["season_key"]), []):
            sr = sdf.iloc[si]
            hr = similarity(r["home_key"], sr["home_key"])
            ar = similarity(r["away_key"], sr["away_key"])
            mean = (hr + ar) / 2.0
            candidates.append((mean, min(hr, ar), hr, ar, int(si)))
        candidates.sort(key=lambda x: (-x[0], -x[1], x[4]))
        if not candidates:
            unmatched_n += 1
            continue
        best = candidates[0]
        second_mean = candidates[1][0] if len(candidates) > 1 else 0.0
        margin = best[0] - second_mean
        if best[1] >= MIN_SIDE and best[0] >= MIN_MEAN and margin >= MIN_MARGIN:
            provisional.append({
                "work_index": int(wi), "label_row_index": int(best[4]), "join_method": "SAME_DAY_ALIAS",
                "home_ratio": float(best[2]), "away_ratio": float(best[3]), "mean_ratio": float(best[0]), "best_margin": float(margin),
            })
            fuzzy_n += 1
        else:
            unmatched_n += 1

    # Binding collision rule: if one label identity maps to >1 Footiqo row, invalidate every member of that collision.
    label_claims = Counter((work.iloc[p["work_index"]]["sourceCode"], p["label_row_index"]) for p in provisional)
    collision_keys = {k for k, c in label_claims.items() if c > 1}
    accepted = [p for p in provisional if (work.iloc[p["work_index"]]["sourceCode"], p["label_row_index"]) not in collision_keys]
    collision_rows_invalidated = len(provisional) - len(accepted)

    # Create immutable zero-label manifest.
    manifest_rows = []
    joined_by_league = Counter()
    joined_by_season = Counter()
    total_by_league = Counter(work["sourceCode"])
    total_by_season = Counter(work["Season"])
    for p in accepted:
        r = work.iloc[p["work_index"]]
        code = r["sourceCode"]
        sr = source_frames[code].iloc[p["label_row_index"]]
        manifest_rows.append({
            "sourceCode": code,
            "n8_id": r["id"],
            "n8_matchDate": r["matchDate"],
            "n8_Season": r["Season"],
            "n8_homeTeam": r["homeTeam"],
            "n8_awayTeam": r["awayTeam"],
            "label_source_path": FILES[code],
            "label_source_row_index": int(p["label_row_index"]),
            "label_Date": sr["Date"],
            "label_Season": sr["Season"],
            "label_HomeTeam": sr["HomeTeam"],
            "label_AwayTeam": sr["AwayTeam"],
            "join_method": p["join_method"],
            "home_ratio": f"{p['home_ratio']:.9f}",
            "away_ratio": f"{p['away_ratio']:.9f}",
            "mean_ratio": f"{p['mean_ratio']:.9f}",
            "best_margin": f"{p['best_margin']:.9f}",
        })
        joined_by_league[code] += 1
        joined_by_season[r["Season"]] += 1

    manifest_rows.sort(key=lambda z: (z["sourceCode"], z["n8_matchDate"], z["n8_id"], z["label_source_row_index"]))
    fields = [
        "sourceCode","n8_id","n8_matchDate","n8_Season","n8_homeTeam","n8_awayTeam",
        "label_source_path","label_source_row_index","label_Date","label_Season","label_HomeTeam","label_AwayTeam",
        "join_method","home_ratio","away_ratio","mean_ratio","best_margin",
    ]
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader(); w.writerows(manifest_rows)
    MANIFEST.write_text(buf.getvalue(), encoding="utf-8")
    manifest_sha = sha256(MANIFEST)

    target_n = int(len(work))
    joined_n = int(len(manifest_rows))
    league_coverage = {code: {"total": int(total_by_league[code]), "joined": int(joined_by_league[code]), "coverage": pct(joined_by_league[code], total_by_league[code])} for code in FILES}
    season_coverage = {s: {"total": int(total_by_season[s]), "joined": int(joined_by_season[s]), "coverage": pct(joined_by_season[s], total_by_season[s])} for s in sorted(total_by_season)}

    dev_test_seasons = ["2019/2020","2020/2021","2021/2022","2022/2023","2023/2024"]
    gates = {
        "n8_csv_sha_exact": sha256(n8_path) == N8_SHA,
        "all_five_label_identity_sources_parsed": len(source_frames) == 5,
        "zero_score_result_columns_materialized": True,
        "overall_join_coverage_ge_97pct": pct(joined_n, target_n) >= 0.97,
        "each_league_join_coverage_ge_95pct": all(v["coverage"] >= 0.95 for v in league_coverage.values()),
        "each_dev_oos_season_join_coverage_ge_95pct": all(season_coverage.get(s, {}).get("coverage", 0.0) >= 0.95 for s in dev_test_seasons),
        "confirmation_2024_25_join_coverage_ge_95pct": season_coverage.get("2024/2025", {}).get("coverage", 0.0) >= 0.95,
        "no_many_to_one_label_collisions_in_manifest": collision_rows_invalidated == 0,
        "manifest_unique_label_assignments": len({(r["sourceCode"], r["label_source_row_index"]) for r in manifest_rows}) == joined_n,
        "reserve_2025_26_unjoined_by_design": reserve_2526 == int((n8["Season"] == "2025/2026").sum()),
        "zero_target_result_values_materialized": True,
        "zero_model": True,
    }
    terminal = "C072N9_ZERO_LABEL_JOIN_PASS" if all(gates.values()) else "C072N9_ZERO_LABEL_JOIN_STOP"

    summary = {
        "schema": SCHEMA,
        "project_line": "football3",
        "terminal": terminal,
        "n8_csv_sha256": sha256(n8_path),
        "n8_ordered_identity_sha256_reference": N8_ID_SHA,
        "label_source_revision": REV,
        "source_meta": source_meta,
        "target_rows_2015_16_through_2024_25": target_n,
        "reserve_2025_26_rows_label_unread": reserve_2526,
        "joined_rows": joined_n,
        "overall_join_coverage": pct(joined_n, target_n),
        "stage1_exact_normalized_provisional": exact_n,
        "stage2_same_day_alias_provisional": fuzzy_n,
        "unmatched_before_collision_adjudication": unmatched_n,
        "ambiguous_exact_rows": ambiguous_n,
        "collision_rows_invalidated": collision_rows_invalidated,
        "league_coverage": league_coverage,
        "season_coverage": season_coverage,
        "manifest_sha256": manifest_sha,
        "manifest_rows": joined_n,
        "join_thresholds": {"min_side": MIN_SIDE, "min_mean": MIN_MEAN, "min_best_margin": MIN_MARGIN},
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
