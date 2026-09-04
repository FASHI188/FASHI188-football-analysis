#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live
import runtime as rt

SCHEMA = "football3-ligue1-2025-26-xg-repair-v1"
COMP = "FRA_Ligue1"
SEASON = "2025/26"
UNDERSTAT_YEAR = 2025
EXPECTED_FORMAL = 306
EXPECTED_BASE = 304
EXPECTED_REPAIR_ROWS = 3
EXPECTED_EXCLUDED_BASE_ROWS = 1
BASE_REL = Path("football-data/evidence/xg/understat_2025_26_linked/FRA_Ligue1.jsonl")
OFFICIAL_REL = Path("football-data/processed/FRA_Ligue1/2025-26.csv")
REPAIR_REL = Path("football-data/evidence/xg/understat_2025_26_linked_repairs/FRA_Ligue1.jsonl")
MANIFEST_REL = Path("football-data/manifests/ligue1_2025_26_xg_repair_v1.json")
ALIASES = {"Paris Saint Germain": "Paris SG"}


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def norm(name: str) -> str:
    return rt._normalize_team(ALIASES.get(str(name).strip(), str(name).strip()))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            if type(obj) is not dict:
                raise RuntimeError(f"json object required: {path}")
            rows.append(obj)
    return rows


def source_dt(value: str) -> datetime:
    x = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return x


def official_rows(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("HomeTeam") or not row.get("AwayTeam"):
                continue
            if str(row.get("stage") or "regular_league") != "regular_league":
                continue
            try:
                d = datetime.strptime(str(row["Date"]), "%d/%m/%Y").date().isoformat()
                hg = int(float(row["FTHG"])); ag = int(float(row["FTAG"]))
            except Exception:
                continue
            out.append({
                "date": d,
                "home_team": str(row["HomeTeam"]).strip(),
                "away_team": str(row["AwayTeam"]).strip(),
                "home_goals": hg,
                "away_goals": ag,
                "source_code": str(row.get("source_code") or "F1"),
                "stage": str(row.get("stage") or "regular_league"),
            })
    return out


def pair_key(home: str, away: str) -> tuple[str, str]:
    return norm(home), norm(away)


def link_current_source(repo_root: Path, observed_at: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    official = official_rows(repo_root / OFFICIAL_REL)
    if len(official) != EXPECTED_FORMAL:
        raise RuntimeError(f"formal Ligue1 cardinality {len(official)} != {EXPECTED_FORMAL}")
    official_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for row in official:
        k = pair_key(row["home_team"], row["away_team"])
        if k in official_by_pair:
            raise RuntimeError(f"duplicate ordered formal pair: {k}")
        official_by_pair[k] = row

    payload, source_sha, url = live._understat_payload(COMP, live.UNDERSTAT[COMP], UNDERSTAT_YEAR)
    dates = payload.get("dates") or []
    results = [x for x in dates if isinstance(x, dict) and bool(x.get("isResult"))]
    if len(results) != EXPECTED_FORMAL:
        raise RuntimeError(f"current Understat result cardinality {len(results)} != {EXPECTED_FORMAL}")

    linked = []
    used_pairs: set[tuple[str, str]] = set()
    used_ids: set[str] = set()
    for item in results:
        h = str((item.get("h") or {}).get("title") or "").strip()
        a = str((item.get("a") or {}).get("title") or "").strip()
        uid = str(item.get("id") or "").strip()
        raw_dt = str(item.get("datetime") or "").strip()
        if not h or not a or not uid or not raw_dt:
            raise RuntimeError("current Understat identity field missing")
        if uid in used_ids:
            raise RuntimeError(f"duplicate Understat id: {uid}")
        used_ids.add(uid)
        k = pair_key(h, a)
        if k in used_pairs:
            raise RuntimeError(f"duplicate Understat ordered pair: {k}")
        used_pairs.add(k)
        off = official_by_pair.get(k)
        if off is None:
            raise RuntimeError(f"identity-only ordered pair not found in formal schedule: {h} v {a}")
        actual = source_dt(raw_dt)
        offset = (datetime.fromisoformat(off["date"]).date() - actual.date()).days
        if abs(offset) > 2:
            raise RuntimeError(f"identity-linked date offset >2d: {h} v {a}: {offset}")
        goals = item.get("goals") or {}; xg = item.get("xG") or {}
        try:
            hg = int(float(goals.get("h"))); ag = int(float(goals.get("a")))
            hx = float(xg.get("h")); ax = float(xg.get("a"))
        except Exception as exc:
            raise RuntimeError(f"result/xG missing or invalid: {h} v {a}") from exc
        if (hg, ag) != (off["home_goals"], off["away_goals"]):
            raise RuntimeError(f"post-identity result conflict: {h} v {a}: source={(hg,ag)} formal={(off['home_goals'],off['away_goals'])}")
        linked.append({
            "away_goals": ag,
            "away_name_similarity": 1.0,
            "away_team_source": a,
            "away_xg": ax,
            "competition_id": COMP,
            "date_offset_days": offset,
            "formal_pit_eligible": False,
            "home_goals": hg,
            "home_name_similarity": 1.0,
            "home_team_source": h,
            "home_xg": hx,
            "identity_bridge_status": "PASS_ORDERED_TEAM_IDENTITY_NO_SCORE_SELECTION_REPAIR_V1",
            "identity_score": 1.0,
            "match_datetime_source": raw_dt,
            "official_away_team": off["away_team"],
            "official_date": off["date"],
            "official_home_team": off["home_team"],
            "official_source_code": off["source_code"],
            "official_stage": off["stage"],
            "schema_version": "V5.1.1-understat-match-xg-r1",
            "season": SEASON,
            "source_observed_at_utc": observed_at,
            "source_role": "RETROSPECTIVE_MATCH_LEVEL_XG",
            "source_snapshot_sha256": source_sha,
            "source_url": url,
            "target_match_xg_allowed_as_predictor": False,
            "understat_match_id": uid,
            "xg_margin": hx - ax,
            "xg_total": hx + ax,
        })

    if len(linked) != EXPECTED_FORMAL or len(used_pairs) != EXPECTED_FORMAL:
        raise RuntimeError("current Understat identity-only link is not one-to-one complete")
    linked.sort(key=lambda r: (r["official_date"], r["official_home_team"], r["official_away_team"], r["understat_match_id"]))
    meta = {
        "understat_source_url": url,
        "understat_source_sha256": source_sha,
        "understat_result_rows": len(results),
        "formal_rows": len(official),
        "linked_rows": len(linked),
    }
    return linked, meta


def comparable(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "official_date": row.get("official_date"),
        "official_home_team": row.get("official_home_team"),
        "official_away_team": row.get("official_away_team"),
        "home_goals": int(row.get("home_goals")),
        "away_goals": int(row.get("away_goals")),
        "home_xg": float(row.get("home_xg")),
        "away_xg": float(row.get("away_xg")),
        "understat_match_id": str(row.get("understat_match_id")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--observed-at", default=None)
    args = ap.parse_args()
    repo_root = args.repo_root.resolve(); out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    observed_at = args.observed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    base_path = repo_root / BASE_REL
    base = load_jsonl(base_path)
    if len(base) != EXPECTED_BASE:
        raise RuntimeError(f"frozen base linked rows {len(base)} != {EXPECTED_BASE}")
    base_sha = sha_file(base_path)
    base_by_pair = {pair_key(r["official_home_team"], r["official_away_team"]): r for r in base}
    if len(base_by_pair) != EXPECTED_BASE:
        raise RuntimeError("frozen base has duplicate ordered pair")

    refreshed, source_meta = link_current_source(repo_root, observed_at)
    refreshed_by_pair = {pair_key(r["official_home_team"], r["official_away_team"]): r for r in refreshed}
    drift = []
    for k, old in base_by_pair.items():
        new = refreshed_by_pair.get(k)
        if new is None or comparable(old) != comparable(new):
            drift.append({"pair": list(k), "old": comparable(old), "new": None if new is None else comparable(new)})
    if len(drift) != EXPECTED_EXCLUDED_BASE_ROWS:
        raise RuntimeError(f"expected exactly {EXPECTED_EXCLUDED_BASE_ROWS} corrupt base mapping, got {len(drift)}: " + json.dumps(drift[:5], ensure_ascii=False, sort_keys=True))

    drift_keys = {tuple(x["pair"]) for x in drift}
    repair_rows = []
    for k, row in refreshed_by_pair.items():
        if k not in base_by_pair or k in drift_keys:
            repair_rows.append(row)
    repair_rows.sort(key=lambda r: (r["official_date"], r["official_home_team"], r["official_away_team"], r["understat_match_id"]))
    if len(repair_rows) != EXPECTED_REPAIR_ROWS:
        raise RuntimeError(f"repair rows {len(repair_rows)} != {EXPECTED_REPAIR_ROWS}")
    kept_base_pairs = set(base_by_pair) - drift_keys
    combined_pairs = kept_base_pairs | {pair_key(r["official_home_team"], r["official_away_team"]) for r in repair_rows}
    if len(combined_pairs) != EXPECTED_FORMAL:
        raise RuntimeError(f"combined ordered pairs {len(combined_pairs)} != {EXPECTED_FORMAL}")

    repair_bytes = b"".join(canon(r) + b"\n" for r in repair_rows)
    (out / "FRA_Ligue1.repair.jsonl").write_bytes(repair_bytes)
    repair_sha = sha_bytes(repair_bytes)
    manifest = {
        "schema_version": SCHEMA,
        "status": "CANDIDATE_COMPLETE",
        "competition_id": COMP,
        "season": SEASON,
        "generated_at_utc": observed_at,
        "identity_selection": "ordered home/away team identity only after explicit provider alias; score/xG not used for selection",
        "post_identity_result_check": True,
        "max_abs_date_offset_days": max(abs(int(r["date_offset_days"])) for r in refreshed),
        "base_linked_path": str(BASE_REL),
        "base_linked_sha256": base_sha,
        "base_linked_rows": len(base),
        "repair_repo_path": str(REPAIR_REL),
        "repair_sha256": repair_sha,
        "repair_rows": len(repair_rows),
        "excluded_base_rows": len(drift),
        "excluded_base_mappings": drift,
        "combined_rows": len(base) - len(drift) + len(repair_rows),
        "formal_rows": EXPECTED_FORMAL,
        "unmatched_rows": 0,
        "ambiguous_rows": 0,
        "repair_identities": [
            {"official_date": r["official_date"], "home": r["official_home_team"], "away": r["official_away_team"], "understat_match_id": r["understat_match_id"], "home_xg": r["home_xg"], "away_xg": r["away_xg"]}
            for r in repair_rows
        ],
        **source_meta,
        "target_match_xg_used": False,
        "historical_pit_claim": False,
        "use_role": "LAGGED_HISTORY_ONLY_AFTER_SOURCE_OBSERVATION",
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
    (out / "manifest.json").write_bytes(canon(manifest) + b"\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
