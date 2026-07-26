#!/usr/bin/env python3
"""V6.36.0 fixed Gold-500 historical feature library.

Purpose
-------
Build one reusable development benchmark from matches whose *currently executable*
pre-match feature families are all present before selection:
- V6.32 synchronized research market + formal probabilities;
- immutable pre-match xG/npxG/xPTS/PPDA/deep state;
- strictly prior shots/SOT;
- same-day-safe Elo/form/rest context;
- V6.33 player-core history from prior lineups, dated transfers and valuations.

Selection is based only on feature completeness and identity matching, never on the
match result. The eligible intersection is sorted, shuffled once with seed 636500,
and the first 500 are frozen as 100 Fast / 300 Confirm / 100 Sealed.

Important limitation
--------------------
This is a complete library for the executable feature families above. It does NOT
pretend to have strict historical pre-kickoff injury/suspension bulletins or a
published expected-XI source when those evidence contracts are absent. Those are
recorded as explicit gaps rather than fabricated.

Research only. formal_weight=0. CURRENT V5.0.1 unchanged.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import Counter
from datetime import timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import dynamic_strength_oof_screen_v470 as dyn  # noqa: E402
import validate_direct_xg_shot_market_catboost_random100_v6320 as v632  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402

OUT_DIR = ROOT / "manifests" / "gold500_v6360"
FEATURES_OUT = OUT_DIR / "gold500_features_v6360.jsonl"
LABELS_OUT = OUT_DIR / "gold500_development_labels_v6360.jsonl"
MANIFEST_OUT = ROOT / "manifests" / "v6_gold500_feature_library_v6360_status.json"
SEED = 636500
TARGET = 500
FAST_N = 100
CONFIRM_N = 300
SEALED_N = 100
TEST_SEASON = "2025/26"

PLAYER_DIFF_KEYS = (
    "log_top11_value", "log_top5_value", "log_top3_value", "log_expected11_value",
    "median_top11_log_value", "top3_share", "valuation_coverage", "expected_vs_value_overlap",
    "current_prior_match_count", "current_unique_starters", "incoming_count", "outgoing_count",
)
PLAYER_FEATURE_NAMES = [f"player_diff_{k}" for k in PLAYER_DIFF_KEYS] + [
    "home_log_top11_value", "away_log_top11_value",
    "home_log_expected11_value", "away_log_expected11_value",
    "home_valuation_coverage", "away_valuation_coverage",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def game_name_lookup(cid: str, cache: Path) -> dict[int, tuple[str, str]]:
    """Read Transfermarkt game names for exact pre-existing game ids."""
    cfg = v633.load_json(dyn.EVIDENCE_CONFIG)
    route = cfg["competition_mapping"][cid]
    external_id = route["transfermarkt_competition_id"]
    path = dyn.download("games", cfg, cache)
    out: dict[int, tuple[str, str]] = {}
    for raw in dyn.csv_rows(path):
        if str(raw.get("competition_id") or "") != external_id:
            continue
        gid = dyn.integer(raw.get("game_id"))
        if gid is None:
            continue
        home = str(raw.get("home_club_name") or raw.get("home_club") or raw.get("home_name") or "").strip()
        away = str(raw.get("away_club_name") or raw.get("away_club") or raw.get("away_name") or "").strip()
        if home and away:
            out[int(gid)] = (home, away)
    return out


def build_player_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = v633.load_config()
    data_by_comp: dict[str, dict[str, Any]] = {}
    indexes_by_comp: dict[str, dict[str, Any]] = {}
    params_by_comp: dict[str, dict[str, float]] = {}
    dynamic_selection: dict[str, dict[str, Any]] = {}
    names_by_comp: dict[str, dict[int, tuple[str, str]]] = {}

    for cid in v633.v6280.COMPS:
        data = v633.load_domain_data(cid, v633.CACHE)
        indexes = v633.build_season_indexes(data)
        artifact = v633.load_json(v633.MODEL_ROOT / cid / "model.json")
        raw_test = artifact["point_in_time_parameters"].get(TEST_SEASON)
        if not raw_test:
            raise v633.PlatformError(f"{cid}: missing {TEST_SEASON} point-in-time parameters")
        params = v633._merge_parameters(config, raw_test)
        selection = v633.v6280.choose_candidate(cid, params, data, indexes)
        data_by_comp[cid] = data
        indexes_by_comp[cid] = indexes
        params_by_comp[cid] = params
        dynamic_selection[cid] = selection["selected"]
        names_by_comp[cid] = game_name_lookup(cid, v633.CACHE)

    wanted_players = v633._all_player_ids(data_by_comp)
    valuations, valuation_audit = v633._load_valuations(wanted_players)

    rows: list[dict[str, Any]] = []
    name_missing = 0
    for cid in v633.v6280.COMPS:
        rs = v633._season_rows(
            cid,
            TEST_SEASON,
            dynamic_selection[cid],
            data_by_comp[cid],
            indexes_by_comp[cid],
            params_by_comp[cid],
            valuations,
        )
        for row in rs:
            try:
                gid = int(str(row["match_key"]).rsplit(":", 1)[-1])
            except (TypeError, ValueError):
                continue
            names = names_by_comp[cid].get(gid)
            if not names:
                name_missing += 1
                continue
            home_name, away_name = names
            row = dict(row)
            row["home_name"] = home_name
            row["away_name"] = away_name
            row["identity_key"] = (
                cid,
                str(row["date"]),
                v632._token(cid, home_name),
                v632._token(cid, away_name),
            )
            row["player_features"] = v633._pair_features(row["home_player_context"], row["away_player_context"])
            rows.append(row)

    return rows, {
        "rows": len(rows),
        "missing_transfermarkt_game_names": name_missing,
        "valuation_audit": valuation_audit,
    }


def partition_name(i: int) -> str:
    if i < FAST_N:
        return "A_FAST100"
    if i < FAST_N + CONFIRM_N:
        return "B_CONFIRM300"
    return "C_SEALED100"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base_rows, base_audit, base_feature_names = v632._build_rows()
    base_test = [dict(r) for r in base_rows if str(r["season"]) == TEST_SEASON]
    player_rows, player_audit = build_player_rows()

    player_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    duplicate_player_keys = 0
    for row in player_rows:
        key = tuple(row["identity_key"])
        if key in player_map:
            duplicate_player_keys += 1
            continue
        player_map[key] = row

    joined: list[dict[str, Any]] = []
    misses = Counter()
    seen_keys: set[tuple[str, str, str, str]] = set()
    for row in base_test:
        cid = str(row["competition_id"])
        key = (
            cid,
            str(row["date"]),
            v632._token(cid, str(row["home_team"])),
            v632._token(cid, str(row["away_team"])),
        )
        if key in seen_keys:
            misses["duplicate_base_identity"] += 1
            continue
        seen_keys.add(key)
        player = player_map.get(key)
        if player is None:
            misses["player_identity_or_context"] += 1
            continue
        if len(row["x"]) != len(base_feature_names):
            misses["base_feature_length"] += 1
            continue
        pf = [float(x) for x in player["player_features"]]
        if len(pf) != len(PLAYER_FEATURE_NAMES):
            misses["player_feature_length"] += 1
            continue
        if int(row["y"]) != int(player["y"]):
            raise RuntimeError(f"joined label disagreement for {key}")
        if list(row["actual_score"]) != list(player["actual_score"]):
            raise RuntimeError(f"joined score disagreement for {key}")

        joined.append({
            "identity_key": key,
            "competition_id": cid,
            "season": TEST_SEASON,
            "date": str(row["date"]),
            "home_team": str(row["home_team"]),
            "away_team": str(row["away_team"]),
            "base_features": [float(x) for x in row["x"]],
            "player_features": pf,
            "market": [float(x) for x in row["market"]],
            "formal": [float(x) for x in row["formal"]],
            "home_player_context": player["home_player_context"],
            "away_player_context": player["away_player_context"],
            "label": int(row["y"]),
            "actual_score": [int(x) for x in row["actual_score"]],
        })

    if len(joined) < TARGET:
        raise RuntimeError(
            f"Gold500 requires >=500 exact complete intersections; found {len(joined)}. "
            f"misses={dict(misses)} player_rows={len(player_rows)}"
        )

    joined.sort(key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    random.Random(SEED).shuffle(joined)
    gold = joined[:TARGET]

    feature_lines: list[str] = []
    label_lines: list[str] = []
    comp_counts = Counter()
    partitions = Counter()
    sealed_identity_material: list[dict[str, Any]] = []

    for i, row in enumerate(gold):
        part = partition_name(i)
        comp_counts[row["competition_id"]] += 1
        partitions[part] += 1
        public = {
            "gold_index": i,
            "partition": part,
            "competition_id": row["competition_id"],
            "season": row["season"],
            "date": row["date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "base_features": row["base_features"],
            "player_features": row["player_features"],
            "market": row["market"],
            "formal": row["formal"],
            "home_player_context": row["home_player_context"],
            "away_player_context": row["away_player_context"],
        }
        feature_lines.append(json.dumps(public, ensure_ascii=False, sort_keys=True))

        label_payload = {
            "gold_index": i,
            "partition": part,
            "label": row["label"],
            "actual_score": row["actual_score"],
        }
        if part != "C_SEALED100":
            label_lines.append(json.dumps(label_payload, ensure_ascii=False, sort_keys=True))
        else:
            sealed_identity_material.append({
                "gold_index": i,
                "identity": list(row["identity_key"]),
                "label_hash": sha256_bytes(canonical_json_bytes(label_payload)),
            })

    FEATURES_OUT.write_text("\n".join(feature_lines) + "\n", encoding="utf-8")
    LABELS_OUT.write_text("\n".join(label_lines) + "\n", encoding="utf-8")

    feature_bytes = FEATURES_OUT.read_bytes()
    label_bytes = LABELS_OUT.read_bytes()
    manifest = {
        "schema_version": "V6.36.0-gold500-feature-library-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_FIXED_COMPLETE_FEATURE_INTERSECTION",
        "selection_contract": {
            "season": TEST_SEASON,
            "selection_basis": "PREMATCH_FEATURE_COMPLETENESS_ONLY",
            "seed": SEED,
            "eligible_complete_intersection": len(joined),
            "gold_count": len(gold),
            "fast100": FAST_N,
            "confirm300": CONFIRM_N,
            "sealed100": SEALED_N,
            "result_used_for_selection": False,
            "confidence_filtering": False,
            "league_dropping_after_results": False,
            "seed_replacement": False,
        },
        "feature_contract": {
            "base_feature_count": len(base_feature_names),
            "base_feature_names": base_feature_names,
            "player_feature_count": len(PLAYER_FEATURE_NAMES),
            "player_feature_names": PLAYER_FEATURE_NAMES,
            "total_numeric_feature_count": len(base_feature_names) + len(PLAYER_FEATURE_NAMES),
            "families": [
                "closing_1x2_retrospective_research",
                "formal_1x2",
                "prematch_xg_npxg_xpts_ppda_deep",
                "prior_shots_sot",
                "same_day_safe_elo_form_rest",
                "player_core_prior_lineups_transfers_valuations",
            ],
            "explicit_nonclaims": [
                "strict historical pre-kickoff injury/suspension bulletin coverage is not complete",
                "published historical expected-XI coverage is not complete",
                "retrospective closing odds are research-only, not formal frozen PIT quotes",
            ],
        },
        "coverage": {
            "competition_counts": dict(sorted(comp_counts.items())),
            "partition_counts": dict(partitions),
            "base_test_rows": len(base_test),
            "player_test_rows": len(player_rows),
            "join_misses": dict(misses),
            "duplicate_player_keys": duplicate_player_keys,
        },
        "audits": {
            "base": base_audit,
            "player": player_audit,
        },
        "artifacts": {
            "features_path": str(FEATURES_OUT.relative_to(ROOT)),
            "features_sha256": sha256_bytes(feature_bytes),
            "development_labels_path": str(LABELS_OUT.relative_to(ROOT)),
            "development_labels_sha256": sha256_bytes(label_bytes),
            "sealed100_label_hashes": sealed_identity_material,
        },
        "governance": {
            "research_only": True,
            "current_unchanged": True,
            "A_FAST100_may_be_used_for_fast_screen": True,
            "B_CONFIRM300_open_only_after_A_gate": True,
            "C_SEALED100_labels_not_emitted": True,
            "whole_season_testing_only_after_staged_gate": True,
        },
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "eligible_complete_intersection": len(joined),
        "gold500": len(gold),
        "competition_counts": dict(sorted(comp_counts.items())),
        "numeric_features": len(base_feature_names) + len(PLAYER_FEATURE_NAMES),
        "features_sha256": manifest["artifacts"]["features_sha256"],
        "development_labels_sha256": manifest["artifacts"]["development_labels_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
