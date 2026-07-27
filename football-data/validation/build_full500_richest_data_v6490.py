#!/usr/bin/env python3
"""V6.49.0 result-blind Full500 richest-data benchmark builder.

User-directed change from V6.36 Gold500:
- keep the existing 2025/26 complete 71-feature intersection as the candidate universe;
- do NOT randomly choose the 500 identities;
- rank candidates only by pre-match data completeness/evidence depth;
- freeze the top 500 identities, then use seed 649500 only to partition 100/300/100.

Selection never uses match result, score, market correctness, confidence, model loss,
or league-specific accuracy. The ranking deliberately measures availability/quality of
information, not the football state itself.

Rank priority (lexicographic, highest first):
1. full market packet: early 1X2 + early/closing O/U 2.5 + early/closing AH + >=2
   individual closing bookmaker 1X2 triplets;
2. average-market packet completeness (preferred to single-book fallback);
3. number of complete market subfamilies and individual closing books;
4. minimum home/away player valuation coverage;
5. minimum expected-core/value overlap;
6. minimum strict-prior lineup history depth and unique-starter depth;
7. xG history depth already present in the 53-feature base.

Historical injury onsets are NOT used in ranking because the public injury table lacks
per-row publication timestamps. Published historical expected-XI coverage is also not
claimed. Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6360 as g0  # noqa: E402
import build_gold500_feature_library_v6361 as g1  # noqa: E402
import v6_market_residual_fusion_v620 as marketmod  # noqa: E402

v632 = g0.v632

TEST_SEASON = "2025/26"
TARGET = 500
FAST_N = 100
CONFIRM_N = 300
SEALED_N = 100
PARTITION_SEED = 649500

OUT_DIR = ROOT / "manifests" / "full500_v6490"
FEATURES_OUT = OUT_DIR / "full500_features_v6490.jsonl"
LABELS_OUT = OUT_DIR / "full500_development_labels_v6490.jsonl"
MANIFEST_OUT = ROOT / "manifests" / "v6_full500_richest_data_v6490_status.json"

EXCLUDE_BOOK_PREFIXES = {"Avg", "Max", "BbAv", "BbMx"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fnum(row: dict[str, str], key: str) -> float | None:
    try:
        x = float(str(row.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def odd(row: dict[str, str], key: str) -> float | None:
    x = fnum(row, key)
    return x if x is not None and x > 1.0 else None


def triplet(row: dict[str, str], keys: tuple[str, str, str]) -> bool:
    return all(odd(row, k) is not None for k in keys)


def pair(row: dict[str, str], keys: tuple[str, str]) -> bool:
    return all(odd(row, k) is not None for k in keys)


def early_1x2(row: dict[str, str]) -> tuple[bool, bool]:
    average = triplet(row, ("AvgH", "AvgD", "AvgA"))
    any_family = average or triplet(row, ("B365H", "B365D", "B365A")) or triplet(row, ("PSH", "PSD", "PSA"))
    return any_family, average


def ou_pair(row: dict[str, str]) -> tuple[bool, bool]:
    early_avg = pair(row, ("Avg>2.5", "Avg<2.5"))
    close_avg = pair(row, ("AvgC>2.5", "AvgC<2.5"))
    early_any = early_avg or pair(row, ("B365>2.5", "B365<2.5")) or pair(row, ("P>2.5", "P<2.5"))
    close_any = close_avg or pair(row, ("B365C>2.5", "B365C<2.5")) or pair(row, ("PC>2.5", "PC<2.5"))
    return early_any and close_any, early_avg and close_avg


def ah_side(row: dict[str, str], closing: bool, average_only: bool = False) -> bool:
    if closing:
        line = "AHCh"
        families = [("AvgCAHH", "AvgCAHA")]
        if not average_only:
            families += [("B365CAHH", "B365CAHA"), ("PCAHH", "PCAHA")]
    else:
        line = "AHh"
        families = [("AvgAHH", "AvgAHA")]
        if not average_only:
            families += [("B365AHH", "B365AHA"), ("PAHH", "PAHA")]
    if fnum(row, line) is None:
        return False
    return any(pair(row, fam) for fam in families)


def ah_pair(row: dict[str, str]) -> tuple[bool, bool]:
    any_pair = ah_side(row, False, False) and ah_side(row, True, False)
    avg_pair = ah_side(row, False, True) and ah_side(row, True, True)
    return any_pair, avg_pair


def individual_closing_books(row: dict[str, str]) -> list[str]:
    prefixes = []
    for key in row:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_BOOK_PREFIXES:
            continue
        if prefix + "CD" not in row or prefix + "CA" not in row:
            continue
        if triplet(row, (prefix + "CH", prefix + "CD", prefix + "CA")):
            prefixes.append(prefix)
    return sorted(set(prefixes))


def market_quality(raw: dict[str, str]) -> dict[str, Any]:
    e1, e1_avg = early_1x2(raw)
    ou, ou_avg = ou_pair(raw)
    ah, ah_avg = ah_pair(raw)
    books = individual_closing_books(raw)
    book_count = len(books)
    book2 = book_count >= 2
    full = e1 and ou and ah and book2
    packet_count = int(e1) + int(ou) + int(ah) + int(book2)
    average_packet_count = int(e1_avg) + int(ou_avg) + int(ah_avg)
    return {
        "full_market_packet": bool(full),
        "packet_count": int(packet_count),
        "average_packet_count": int(average_packet_count),
        "early_1x2": bool(e1),
        "average_early_1x2": bool(e1_avg),
        "ou_early_closing_pair": bool(ou),
        "average_ou_early_closing_pair": bool(ou_avg),
        "ah_early_closing_pair": bool(ah),
        "average_ah_early_closing_pair": bool(ah_avg),
        "individual_closing_book_count": int(book_count),
        "individual_closing_books": books,
    }


def raw_market_lookup(cid: str) -> tuple[dict[tuple[str, str, str, str], dict[str, str]], dict[str, Any]]:
    directory = ROOT / "processed" / cid
    lookup: dict[tuple[str, str, str, str], dict[str, str]] = {}
    stats = Counter()
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                stats["rows"] += 1
                season = str(raw.get("season") or raw.get("Season") or "").strip()
                if season != TEST_SEASON:
                    continue
                try:
                    date = marketmod._parse_date(str(raw.get("Date") or ""))
                except Exception:
                    stats["date_parse_rejected"] += 1
                    continue
                home = v632._token(cid, str(raw.get("HomeTeam") or ""))
                away = v632._token(cid, str(raw.get("AwayTeam") or ""))
                if not home or not away:
                    stats["identity_rejected"] += 1
                    continue
                key = (season, date, home, away)
                if key in lookup:
                    stats["duplicate_key"] += 1
                    continue
                lookup[key] = raw
                stats["attached"] += 1
    return lookup, dict(stats)


def build_complete_intersection() -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    base_rows, base_audit, base_feature_names = v632._build_rows()
    base_test = [dict(r) for r in base_rows if str(r["season"]) == TEST_SEASON]
    player_rows, player_audit = g0.build_player_rows()
    crosswalk, crosswalk_audit = g1.build_crosswalk(base_test, player_rows)

    player_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    duplicate_player_keys = 0
    unmapped_player_rows = 0
    for row in player_rows:
        cid = str(row["competition_id"])
        mapped_home = crosswalk.get(cid, {}).get(str(row["home_name"]))
        mapped_away = crosswalk.get(cid, {}).get(str(row["away_name"]))
        if not mapped_home or not mapped_away:
            unmapped_player_rows += 1
            continue
        key = (cid, str(row["date"]), v632._token(cid, mapped_home), v632._token(cid, mapped_away))
        if key in player_map:
            duplicate_player_keys += 1
            continue
        player_map[key] = row

    raw_by_domain = {}
    raw_audit = {}
    for cid in sorted({str(r["competition_id"]) for r in base_test}):
        raw_by_domain[cid], raw_audit[cid] = raw_market_lookup(cid)

    feature_index = {name: i for i, name in enumerate(base_feature_names)}
    if "xg_min_history_scaled" not in feature_index or "panel_min_n_scaled" not in feature_index:
        raise RuntimeError("expected history-depth fields absent from base feature contract")

    joined = []
    misses = Counter()
    seen = set()
    for row in base_test:
        cid = str(row["competition_id"])
        key4 = (cid, str(row["date"]), v632._token(cid, str(row["home_team"])), v632._token(cid, str(row["away_team"])))
        if key4 in seen:
            misses["duplicate_base_identity"] += 1
            continue
        seen.add(key4)
        player = player_map.get(key4)
        if player is None:
            misses["player_identity_or_context"] += 1
            continue
        pf = [float(x) for x in player["player_features"]]
        if len(row["x"]) != len(base_feature_names) or len(pf) != len(g0.PLAYER_FEATURE_NAMES):
            misses["feature_length"] += 1
            continue
        raw_key = (TEST_SEASON, key4[1], key4[2], key4[3])
        raw = raw_by_domain[cid].get(raw_key)
        if raw is None:
            misses["raw_market_identity"] += 1
            continue
        if int(row["y"]) != int(player["y"]) or list(row["actual_score"]) != list(player["actual_score"]):
            raise RuntimeError(f"joined label disagreement for {key4}")

        hctx = dict(player["home_player_context"])
        actx = dict(player["away_player_context"])
        mq = market_quality(raw)
        min_val = min(float(hctx.get("valuation_coverage", 0.0)), float(actx.get("valuation_coverage", 0.0)))
        min_overlap = min(float(hctx.get("expected_vs_value_overlap", 0.0)), float(actx.get("expected_vs_value_overlap", 0.0)))
        min_prior = min(int(hctx.get("current_prior_match_count", 0)), int(actx.get("current_prior_match_count", 0)))
        min_unique = min(int(hctx.get("current_unique_starters", 0)), int(actx.get("current_unique_starters", 0)))
        min_valued = min(int(hctx.get("valued_count", 0)), int(actx.get("valued_count", 0)))
        xg_history = float(row["x"][feature_index["xg_min_history_scaled"]])
        panel_history = float(row["x"][feature_index["panel_min_n_scaled"]])

        richness = {
            **mq,
            "min_valuation_coverage": min_val,
            "min_expected_vs_value_overlap": min_overlap,
            "min_current_prior_match_count": min_prior,
            "min_current_unique_starters": min_unique,
            "min_valued_player_count": min_valued,
            "xg_min_history_scaled": xg_history,
            "panel_min_n_scaled": panel_history,
        }
        identity_text = "|".join(map(str, key4))
        tiebreak = sha256_bytes(identity_text.encode("utf-8"))
        # Result-blind lexicographic ranking. Values are completeness/evidence depth only.
        rank_tuple = (
            int(richness["full_market_packet"]),
            int(richness["average_packet_count"]),
            int(richness["packet_count"]),
            min(8, int(richness["individual_closing_book_count"])),
            round(float(richness["min_valuation_coverage"]), 8),
            round(float(richness["min_expected_vs_value_overlap"]), 8),
            min(12, int(richness["min_current_prior_match_count"])),
            min(24, int(richness["min_current_unique_starters"])),
            min(20, int(richness["min_valued_player_count"])),
            round(float(richness["xg_min_history_scaled"]), 8),
            round(float(richness["panel_min_n_scaled"]), 8),
        )
        joined.append({
            "identity_key": key4,
            "competition_id": cid,
            "season": TEST_SEASON,
            "date": str(row["date"]),
            "home_team": str(row["home_team"]),
            "away_team": str(row["away_team"]),
            "base_features": [float(x) for x in row["x"]],
            "player_features": pf,
            "market": [float(x) for x in row["market"]],
            "formal": [float(x) for x in row["formal"]],
            "home_player_context": hctx,
            "away_player_context": actx,
            "richness": richness,
            "rank_tuple": rank_tuple,
            "rank_tiebreak_sha256": tiebreak,
            # Kept only for post-selection label artifact; never referenced by ranking.
            "label": int(row["y"]),
            "actual_score": [int(x) for x in row["actual_score"]],
        })

    return joined, {
        "base_test_rows": len(base_test),
        "player_test_rows": len(player_rows),
        "join_misses": dict(misses),
        "unmapped_player_rows": unmapped_player_rows,
        "duplicate_player_keys": duplicate_player_keys,
        "crosswalk": crosswalk_audit,
        "base": base_audit,
        "player": player_audit,
        "raw_market": raw_audit,
    }, base_feature_names


def partition_name(i: int) -> str:
    if i < FAST_N:
        return "A_FAST100"
    if i < FAST_N + CONFIRM_N:
        return "B_CONFIRM300"
    return "C_SEALED100"


def quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    def vals(key: str) -> list[float]:
        return [float(r["richness"][key]) for r in rows]
    return {
        "count": len(rows),
        "full_market_packet_count": sum(int(r["richness"]["full_market_packet"]) for r in rows),
        "full_market_packet_rate": sum(int(r["richness"]["full_market_packet"]) for r in rows) / len(rows),
        "average_packet_count_mean": statistics.fmean(vals("average_packet_count")),
        "individual_closing_book_count_mean": statistics.fmean(vals("individual_closing_book_count")),
        "individual_closing_book_count_median": statistics.median(vals("individual_closing_book_count")),
        "min_valuation_coverage_mean": statistics.fmean(vals("min_valuation_coverage")),
        "min_expected_vs_value_overlap_mean": statistics.fmean(vals("min_expected_vs_value_overlap")),
        "min_current_prior_match_count_mean": statistics.fmean(vals("min_current_prior_match_count")),
        "min_current_unique_starters_mean": statistics.fmean(vals("min_current_unique_starters")),
        "xg_min_history_scaled_mean": statistics.fmean(vals("xg_min_history_scaled")),
        "panel_min_n_scaled_mean": statistics.fmean(vals("panel_min_n_scaled")),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    eligible, audit, base_feature_names = build_complete_intersection()
    if len(eligible) < TARGET:
        raise RuntimeError(f"Full500 requires >=500 complete candidates; found {len(eligible)}")

    # Descend on completeness tuple; deterministic SHA only resolves exact ties.
    eligible_sorted = sorted(eligible, key=lambda r: (tuple(-float(x) for x in r["rank_tuple"]), r["rank_tiebreak_sha256"]))
    selected_identity_order = eligible_sorted[:TARGET]
    cutoff = selected_identity_order[-1]

    # Freeze identities before partition randomization. No result appears in this material.
    identity_material = [
        {
            "competition_id": r["competition_id"], "season": r["season"], "date": r["date"],
            "home_team": r["home_team"], "away_team": r["away_team"],
            "rank_tuple": list(r["rank_tuple"]), "rank_tiebreak_sha256": r["rank_tiebreak_sha256"],
        }
        for r in selected_identity_order
    ]
    identity_sha = sha256_bytes(canonical_json_bytes(identity_material))

    full = list(selected_identity_order)
    random.Random(PARTITION_SEED).shuffle(full)

    feature_lines = []
    label_lines = []
    sealed_hashes = []
    comp_counts = Counter()
    partition_counts = Counter()
    for i, row in enumerate(full):
        part = partition_name(i)
        comp_counts[row["competition_id"]] += 1
        partition_counts[part] += 1
        public = {
            "full_index": i,
            "partition": part,
            "competition_id": row["competition_id"], "season": row["season"], "date": row["date"],
            "home_team": row["home_team"], "away_team": row["away_team"],
            "base_features": row["base_features"], "player_features": row["player_features"],
            "market": row["market"], "formal": row["formal"],
            "home_player_context": row["home_player_context"], "away_player_context": row["away_player_context"],
            "richness": row["richness"],
        }
        feature_lines.append(json.dumps(public, ensure_ascii=False, sort_keys=True))
        label_payload = {
            "full_index": i, "partition": part,
            "label": row["label"], "actual_score": row["actual_score"],
        }
        if part != "C_SEALED100":
            label_lines.append(json.dumps(label_payload, ensure_ascii=False, sort_keys=True))
        else:
            sealed_hashes.append({
                "full_index": i,
                "identity": list(row["identity_key"]),
                "label_hash": sha256_bytes(canonical_json_bytes(label_payload)),
            })

    FEATURES_OUT.write_text("\n".join(feature_lines) + "\n", encoding="utf-8")
    LABELS_OUT.write_text("\n".join(label_lines) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "V6.49.0-full500-richest-data-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_RESULT_BLIND_RICHEST_DATA_500",
        "selection_contract": {
            "season": TEST_SEASON,
            "candidate_universe": "V6.36.1 71-feature complete intersection rebuilt result-blind",
            "eligible_complete_intersection": len(eligible),
            "selection_basis": "PREMATCH_DATA_COMPLETENESS_AND_EVIDENCE_DEPTH_ONLY",
            "selected_count": TARGET,
            "identity_freeze_sha256": identity_sha,
            "partition_seed_after_identity_freeze": PARTITION_SEED,
            "fast100": FAST_N, "confirm300": CONFIRM_N, "sealed100": SEALED_N,
            "result_used_for_selection": False,
            "score_used_for_selection": False,
            "market_correctness_used_for_selection": False,
            "model_metric_used_for_selection": False,
            "league_accuracy_used_for_selection": False,
            "confidence_filtering": False,
            "post_result_league_dropping": False,
            "seed_replacement": False,
        },
        "ranking_contract": {
            "order": [
                "full_market_packet",
                "average_market_packet_count",
                "complete_market_subfamily_count",
                "individual_closing_book_count_capped_8",
                "minimum_home_away_player_valuation_coverage",
                "minimum_home_away_expected_core_value_overlap",
                "minimum_home_away_strict_prior_lineup_matches_capped_12",
                "minimum_home_away_unique_prior_starters_capped_24",
                "minimum_home_away_valued_player_count_capped_20",
                "xg_history_depth",
                "panel_history_depth",
                "identity_sha256_tiebreak_only",
            ],
            "full_market_packet_definition": "early 1X2 + early/closing O/U2.5 + early/closing AH + at least 2 individual closing 1X2 books",
            "average_market_packet_preferred": True,
            "rank_500_cutoff_tuple": list(cutoff["rank_tuple"]),
            "rank_500_cutoff_identity": list(cutoff["identity_key"]),
        },
        "feature_contract": {
            "base_feature_count": len(base_feature_names),
            "player_feature_count": len(g0.PLAYER_FEATURE_NAMES),
            "core_numeric_feature_count": len(base_feature_names) + len(g0.PLAYER_FEATURE_NAMES),
            "selection_additional_families": [
                "early_to_closing_1x2_availability",
                "early_and_closing_ou25_availability",
                "early_and_closing_asian_handicap_availability",
                "individual_closing_bookmaker_triplet_count",
                "player_valuation_coverage",
                "expected_core_history_depth",
                "xg_and_panel_history_depth",
            ],
            "attachable_not_ranked": [
                "strict-prior manager/task state: availability is near-universal and ranking by state would not be completeness-only",
                "strict-prior cross-competition schedule load: true zero load is valid data and must not be ranked as missing",
            ],
            "explicit_nonclaims": [
                "historical injury-onset table lacks per-row publication timestamp and is not used to rank strict completeness",
                "published historical expected-XI coverage is not complete",
                "retrospective Football-Data market snapshots remain research-only rather than formal frozen PIT quotes",
            ],
        },
        "quality_comparison": {
            "eligible_1115_like_pool": quality_summary(eligible),
            "selected_full500": quality_summary(selected_identity_order),
        },
        "coverage": {
            "competition_counts": dict(sorted(comp_counts.items())),
            "partition_counts": dict(partition_counts),
        },
        "audits": audit,
        "artifacts": {
            "features_path": str(FEATURES_OUT.relative_to(ROOT)),
            "features_sha256": sha256_bytes(FEATURES_OUT.read_bytes()),
            "development_labels_path": str(LABELS_OUT.relative_to(ROOT)),
            "development_labels_sha256": sha256_bytes(LABELS_OUT.read_bytes()),
            "sealed100_label_hashes": sealed_hashes,
        },
        "governance": {
            "CURRENT_unchanged": True,
            "research_only": True,
            "old_Gold500_not_deleted": True,
            "Full500_replaces_Gold500_only_for_new_research_after_user_directive": True,
            "no_accuracy_test_run_by_this_builder": True,
            "B_CONFIRM300_open_only_after_new_A_FAST100_gate": True,
            "C_SEALED100_labels_not_emitted": True,
        },
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "eligible": len(eligible),
        "selected": TARGET,
        "identity_freeze_sha256": identity_sha,
        "competition_counts": dict(sorted(comp_counts.items())),
        "eligible_quality": manifest["quality_comparison"]["eligible_1115_like_pool"],
        "selected_quality": manifest["quality_comparison"]["selected_full500"],
        "rank_500_cutoff_tuple": manifest["ranking_contract"]["rank_500_cutoff_tuple"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
