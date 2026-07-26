#!/usr/bin/env python3
"""V6.36.1 Gold500 builder with schedule-fingerprint team crosswalk.

This revision keeps V6.36.0's feature-completeness-only selection contract but
replaces brittle cross-provider string equality with a result-blind team mapping.
For each competition, Transfermarkt and Football-Data team identities are matched
by the dates on which each team appears home/away during 2025/26. Match outcomes
are never used to create the crosswalk or select the Gold500 pool.
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import build_gold500_feature_library_v6360 as base

v632 = base.v632

OUT_DIR = base.OUT_DIR
FEATURES_OUT = base.FEATURES_OUT
LABELS_OUT = base.LABELS_OUT
MANIFEST_OUT = base.MANIFEST_OUT


def norm_name(value: str) -> str:
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


def fingerprints_from_base(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, set[str]]]]:
    out: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(lambda: defaultdict(lambda: {"home": set(), "away": set(), "all": set()}))
    for r in rows:
        cid = str(r["competition_id"]); date = str(r["date"])
        h = str(r["home_team"]); a = str(r["away_team"])
        out[cid][h]["home"].add(date); out[cid][h]["all"].add(date)
        out[cid][a]["away"].add(date); out[cid][a]["all"].add(date)
    return out


def fingerprints_from_player(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, set[str]]]]:
    out: dict[str, dict[str, dict[str, set[str]]]] = defaultdict(lambda: defaultdict(lambda: {"home": set(), "away": set(), "all": set()}))
    for r in rows:
        cid = str(r["competition_id"]); date = str(r["date"])
        h = str(r["home_name"]); a = str(r["away_name"])
        out[cid][h]["home"].add(date); out[cid][h]["all"].add(date)
        out[cid][a]["away"].add(date); out[cid][a]["all"].add(date)
    return out


def pair_score(tm: str, tf: dict[str, set[str]], fd: str, ff: dict[str, set[str]]) -> tuple[int, int, float]:
    home_overlap = len(tf["home"] & ff["home"])
    away_overlap = len(tf["away"] & ff["away"])
    all_overlap = len(tf["all"] & ff["all"])
    schedule_score = 4 * home_overlap + 4 * away_overlap + all_overlap
    role_overlap = home_overlap + away_overlap
    sim = SequenceMatcher(None, norm_name(tm), norm_name(fd)).ratio()
    return schedule_score, role_overlap, sim


def build_crosswalk(base_rows: list[dict[str, Any]], player_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    bf = fingerprints_from_base(base_rows)
    pf = fingerprints_from_player(player_rows)
    mapping: dict[str, dict[str, str]] = {}
    audit: dict[str, Any] = {}

    for cid in sorted(set(bf) & set(pf)):
        candidates = []
        per_tm = {}
        for tm_name, tm_fp in pf[cid].items():
            scored = []
            for fd_name, fd_fp in bf[cid].items():
                s, role, sim = pair_score(tm_name, tm_fp, fd_name, fd_fp)
                scored.append((s, role, sim, fd_name))
                candidates.append((s, role, sim, tm_name, fd_name))
            scored.sort(reverse=True)
            per_tm[tm_name] = scored[:3]

        candidates.sort(reverse=True)
        used_tm: set[str] = set(); used_fd: set[str] = set(); cm: dict[str, str] = {}
        assigned_meta = []
        for s, role, sim, tm_name, fd_name in candidates:
            if tm_name in used_tm or fd_name in used_fd:
                continue
            if role < 2:
                continue
            cm[tm_name] = fd_name
            used_tm.add(tm_name); used_fd.add(fd_name)
            second = per_tm[tm_name][1][0] if len(per_tm[tm_name]) > 1 else -1
            assigned_meta.append({
                "tm": tm_name, "fd": fd_name, "schedule_score": s,
                "role_overlap": role, "name_similarity": sim,
                "next_best_schedule_score": second,
                "schedule_margin": s - second,
            })

        mapping[cid] = cm
        audit[cid] = {
            "tm_teams": len(pf[cid]),
            "fd_teams": len(bf[cid]),
            "mapped_teams": len(cm),
            "unmapped_tm": sorted(set(pf[cid]) - set(cm)),
            "unmapped_fd": sorted(set(bf[cid]) - set(cm.values())),
            "min_role_overlap": min((x["role_overlap"] for x in assigned_meta), default=None),
            "min_schedule_margin": min((x["schedule_margin"] for x in assigned_meta), default=None),
            "assignments": assigned_meta,
        }
    return mapping, audit


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base_rows, base_audit, base_feature_names = v632._build_rows()
    base_test = [dict(r) for r in base_rows if str(r["season"]) == base.TEST_SEASON]
    player_rows, player_audit = base.build_player_rows()

    crosswalk, crosswalk_audit = build_crosswalk(base_test, player_rows)
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
        key = (
            cid,
            str(row["date"]),
            v632._token(cid, mapped_home),
            v632._token(cid, mapped_away),
        )
        if key in player_map:
            duplicate_player_keys += 1
            continue
        player_map[key] = row

    joined = []
    misses = Counter()
    seen_keys = set()
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
        pf = [float(x) for x in player["player_features"]]
        if len(row["x"]) != len(base_feature_names) or len(pf) != len(base.PLAYER_FEATURE_NAMES):
            misses["feature_length"] += 1
            continue
        if int(row["y"]) != int(player["y"]) or list(row["actual_score"]) != list(player["actual_score"]):
            raise RuntimeError(f"post-join label/score disagreement for {key}")
        joined.append({
            "identity_key": key,
            "competition_id": cid,
            "season": base.TEST_SEASON,
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

    if len(joined) < base.TARGET:
        diagnostic = {
            "schema_version": "V6.36.1-gold500-crosswalk-diagnostic-r1",
            "status": "FAIL_INSUFFICIENT_COMPLETE_INTERSECTION",
            "base_test_rows": len(base_test), "player_test_rows": len(player_rows),
            "joined": len(joined), "join_misses": dict(misses),
            "unmapped_player_rows": unmapped_player_rows,
            "duplicate_player_keys": duplicate_player_keys,
            "crosswalk": crosswalk_audit,
        }
        MANIFEST_OUT.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"Gold500 complete intersection only {len(joined)} after schedule crosswalk")

    joined.sort(key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    random.Random(base.SEED).shuffle(joined)
    gold = joined[:base.TARGET]

    feature_lines = []
    label_lines = []
    comp_counts = Counter(); partition_counts = Counter(); sealed_hashes = []
    for i, row in enumerate(gold):
        part = base.partition_name(i)
        comp_counts[row["competition_id"]] += 1; partition_counts[part] += 1
        public = {
            "gold_index": i, "partition": part,
            "competition_id": row["competition_id"], "season": row["season"], "date": row["date"],
            "home_team": row["home_team"], "away_team": row["away_team"],
            "base_features": row["base_features"], "player_features": row["player_features"],
            "market": row["market"], "formal": row["formal"],
            "home_player_context": row["home_player_context"], "away_player_context": row["away_player_context"],
        }
        feature_lines.append(json.dumps(public, ensure_ascii=False, sort_keys=True))
        label_payload = {"gold_index": i, "partition": part, "label": row["label"], "actual_score": row["actual_score"]}
        if part != "C_SEALED100":
            label_lines.append(json.dumps(label_payload, ensure_ascii=False, sort_keys=True))
        else:
            sealed_hashes.append({
                "gold_index": i,
                "identity": list(row["identity_key"]),
                "label_hash": base.sha256_bytes(base.canonical_json_bytes(label_payload)),
            })

    FEATURES_OUT.write_text("\n".join(feature_lines) + "\n", encoding="utf-8")
    LABELS_OUT.write_text("\n".join(label_lines) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "V6.36.1-gold500-feature-library-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_FIXED_COMPLETE_FEATURE_INTERSECTION",
        "selection_contract": {
            "season": base.TEST_SEASON,
            "selection_basis": "PREMATCH_FEATURE_COMPLETENESS_ONLY",
            "cross_source_identity_basis": "RESULT_BLIND_HOME_AWAY_SCHEDULE_DATE_FINGERPRINT",
            "seed": base.SEED,
            "eligible_complete_intersection": len(joined),
            "gold_count": len(gold),
            "fast100": base.FAST_N, "confirm300": base.CONFIRM_N, "sealed100": base.SEALED_N,
            "result_used_for_selection_or_crosswalk": False,
            "confidence_filtering": False, "league_dropping_after_results": False, "seed_replacement": False,
        },
        "feature_contract": {
            "base_feature_count": len(base_feature_names),
            "player_feature_count": len(base.PLAYER_FEATURE_NAMES),
            "total_numeric_feature_count": len(base_feature_names) + len(base.PLAYER_FEATURE_NAMES),
            "base_feature_names": base_feature_names,
            "player_feature_names": base.PLAYER_FEATURE_NAMES,
            "families": [
                "closing_1x2_retrospective_research", "formal_1x2",
                "prematch_xg_npxg_xpts_ppda_deep", "prior_shots_sot",
                "same_day_safe_elo_form_rest", "player_core_prior_lineups_transfers_valuations",
            ],
            "explicit_nonclaims": [
                "strict historical pre-kickoff injury/suspension bulletin coverage is not complete",
                "published historical expected-XI coverage is not complete",
                "retrospective closing odds are research-only, not formal frozen PIT quotes",
            ],
        },
        "coverage": {
            "competition_counts": dict(sorted(comp_counts.items())),
            "partition_counts": dict(partition_counts),
            "base_test_rows": len(base_test), "player_test_rows": len(player_rows),
            "join_misses": dict(misses), "unmapped_player_rows": unmapped_player_rows,
            "duplicate_player_keys": duplicate_player_keys,
        },
        "crosswalk_audit": crosswalk_audit,
        "audits": {"base": base_audit, "player": player_audit},
        "artifacts": {
            "features_path": str(FEATURES_OUT.relative_to(base.ROOT)),
            "features_sha256": base.sha256_bytes(FEATURES_OUT.read_bytes()),
            "development_labels_path": str(LABELS_OUT.relative_to(base.ROOT)),
            "development_labels_sha256": base.sha256_bytes(LABELS_OUT.read_bytes()),
            "sealed100_label_hashes": sealed_hashes,
        },
        "governance": {
            "research_only": True, "current_unchanged": True,
            "A_FAST100_may_be_used_for_fast_screen": True,
            "B_CONFIRM300_open_only_after_A_gate": True,
            "C_SEALED100_labels_not_emitted": True,
            "whole_season_testing_only_after_staged_gate": True,
        },
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "eligible_complete_intersection": len(joined), "gold500": len(gold),
        "competition_counts": dict(sorted(comp_counts.items())),
        "numeric_features": len(base_feature_names) + len(base.PLAYER_FEATURE_NAMES),
        "crosswalk_mapped_teams": {k: v["mapped_teams"] for k, v in crosswalk_audit.items()},
        "features_sha256": manifest["artifacts"]["features_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
