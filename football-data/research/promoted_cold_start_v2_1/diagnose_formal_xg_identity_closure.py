#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from formal_fast_runtime_v1 import runtime as rt

ALIAS_SCHEMA = "V6.18.8-understat-iterative-schedule-anchor-alias-closure-r1"
ALIAS_CLASSIFICATION = "DATA_IDENTITY_CLOSURE_AUDIT_ONLY_NO_MODEL_FIT"


def _load_alias_closure(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    raw = path.read_bytes()
    d = json.loads(raw.decode("utf-8"))
    if d.get("schema_version") != ALIAS_SCHEMA:
        raise RuntimeError(f"unexpected alias-closure schema: {d.get('schema_version')!r}")
    if d.get("status") != "PASS":
        raise RuntimeError("alias-closure manifest is not PASS")
    if d.get("classification") != ALIAS_CLASSIFICATION:
        raise RuntimeError("alias-closure manifest is not audit-only/no-model-fit")
    design = d.get("design") or {}
    required = {
        "one_to_one_both_directions_required": True,
        "exact_identity_conflict_forbidden": True,
        "final_attachment_is_exact_after_alias_mapping": True,
        "fuzzy_training_rows": False,
    }
    for k, v in required.items():
        if design.get(k) is not v:
            raise RuntimeError(f"alias-closure design contract mismatch: {k}={design.get(k)!r}")
    gate = d.get("xg_research_coverage_gate") or {}
    if gate.get("pass") is not True:
        raise RuntimeError("alias-closure xG research coverage gate did not pass")

    out: dict[str, dict[str, str]] = {}
    total = 0
    for comp in ("ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"):
        dom = (d.get("domains") or {}).get(comp) or {}
        q = dom.get("qualified_aliases") or {}
        cmap: dict[str, str] = {}
        inverse: dict[str, str] = {}
        for key, rec in q.items():
            platform = str(rec.get("platform_token") or key).strip()
            understat = str(rec.get("understat_token") or "").strip()
            if not platform or not understat:
                raise RuntimeError(f"empty qualified alias in {comp}: {key!r}")
            if platform != rt._normalize_team(platform):
                raise RuntimeError(f"non-normalized platform token in {comp}: {platform!r}")
            if understat != rt._normalize_team(understat):
                raise RuntimeError(f"non-normalized Understat token in {comp}: {understat!r}")
            if platform in cmap and cmap[platform] != understat:
                raise RuntimeError(f"platform token maps to multiple Understat tokens: {comp} {platform}")
            if understat in inverse and inverse[understat] != platform:
                raise RuntimeError(f"Understat token maps from multiple platform tokens: {comp} {understat}")
            cmap[platform] = understat
            inverse[understat] = platform
        out[comp] = cmap
        total += len(cmap)

    expected_total = int(d.get("qualified_alias_count") or -1)
    if total != expected_total:
        raise RuntimeError(f"qualified alias count mismatch: parsed={total} manifest={expected_total}")
    return out, {
        "path": str(path),
        "git_blob_sha_expected_by_workflow": "87b43c4a45890828312a8a93b3868b7b2f471df3",
        "schema_version": d["schema_version"],
        "classification": d["classification"],
        "generated_at_utc": d.get("generated_at_utc"),
        "qualified_alias_count": total,
        "aggregate": d.get("aggregate"),
        "domain_rates": d.get("domain_rates"),
        "design": {k: design.get(k) for k in required},
    }


def _source_rows(understat_db: Path, confirmation_dir: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if rt._sha_file(understat_db) != rt.BINDINGS["understat_frozen.db"]["sha256"]:
        raise rt.RuntimeGateError("Understat frozen database SHA mismatch")
    cident = confirmation_dir / "confirmation_identity.jsonl"
    cvault = confirmation_dir / "confirmation_xg_result_vault.jsonl"
    if rt._sha_file(cident) != rt.BINDINGS["confirmation_identity.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation identity SHA mismatch")
    if rt._sha_file(cvault) != rt.BINDINGS["confirmation_xg_result_vault.jsonl"]["sha256"]:
        raise rt.RuntimeGateError("confirmation vault SHA mismatch")

    source: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    con = sqlite3.connect(str(understat_db))
    con.row_factory = sqlite3.Row
    try:
        raw = [dict(r) for r in con.execute(
            "select fid,date,league,season,team_h,team_a,h_goals,a_goals,h_xg,a_xg "
            "from general_game_stats where league in ('Bundesliga','EPL','La liga','Ligue 1','Serie A') "
            "and season in (2022,2023) order by date,fid"
        )]
    finally:
        con.close()
    if len(raw) != rt.EXPECTED_XG_OLD_N:
        raise rt.RuntimeGateError(f"old XG selected row count mismatch: {len(raw)}")
    for r in raw:
        dt = datetime.fromisoformat(str(r["date"])).replace(tzinfo=timezone.utc)
        comp = rt.BIG5[str(r["league"])]
        key = (comp, dt.date().isoformat(), rt._normalize_team(str(r["team_h"])), rt._normalize_team(str(r["team_a"])))
        if key in source:
            raise rt.RuntimeGateError(f"duplicate old XG identity: {key}")
        source[key] = {
            "source_kind": "understat_frozen_db",
            "source_fixture_id": f"understat:{int(r['fid'])}",
            "home_goals": int(r["h_goals"]),
            "away_goals": int(r["a_goals"]),
            "home_xg": float(r["h_xg"]),
            "away_xg": float(r["a_xg"]),
            "release_at": (dt + timedelta(hours=3)).isoformat(),
        }

    identities = [json.loads(x) for x in cident.read_text(encoding="utf-8").splitlines() if x.strip()]
    vault_rows = [json.loads(x) for x in cvault.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(identities) != 1752 or len(vault_rows) != 1752:
        raise rt.RuntimeGateError("confirmation row count mismatch")
    vault = {str(r["fixture_id"]): r for r in vault_rows}
    if len(vault) != 1752:
        raise rt.RuntimeGateError("confirmation vault duplicate id")
    for r in identities:
        sid = str(r["fixture_id"])
        v = vault.get(sid)
        if v is None:
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        if str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation kickoff mismatch")
        comp = rt.BIG5[str(r["league"])]
        key = (comp, dt.date().isoformat(), rt._normalize_team(str(r["home_team"])), rt._normalize_team(str(r["away_team"])))
        if key in source:
            raise rt.RuntimeGateError(f"duplicate combined XG identity: {key}")
        source[key] = {
            "source_kind": "confirmation_vault",
            "source_fixture_id": sid,
            "home_goals": int(v["home_goals"]),
            "away_goals": int(v["away_goals"]),
            "home_xg": float(v["home_xg"]),
            "away_xg": float(v["away_xg"]),
            "release_at": str(v["release_at"]),
            "identity_record": {
                "kickoff": str(r["kickoff"]),
                "league": str(r["league"]),
                "home_team": str(r["home_team"]),
                "away_team": str(r["away_team"]),
            },
        }
    return source


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--alias-closure", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo = Path(args.repo_root)
    alias_path = Path(args.alias_closure)
    alias_map, alias_meta = _load_alias_closure(alias_path)
    history, v1src = rt.load_frozen_v1_history(repo)
    source = _source_rows(Path(args.understat_db), Path(args.confirmation_dir))

    formal_index: dict[tuple[str, str, str, str], rt.HistoryFixture] = {}
    alias_usage: dict[str, int] = {c: 0 for c in alias_map}
    for r in history:
        if r.competition_id not in alias_map or r.season not in ("2022/23", "2023/24", "2024/25"):
            continue
        home0 = rt._normalize_team(r.home_team_name)
        away0 = rt._normalize_team(r.away_team_name)
        home = alias_map[r.competition_id].get(home0, home0)
        away = alias_map[r.competition_id].get(away0, away0)
        if home != home0:
            alias_usage[r.competition_id] += 1
        if away != away0:
            alias_usage[r.competition_id] += 1
        key = (r.competition_id, r.kickoff.date().isoformat(), home, away)
        if key in formal_index:
            raise RuntimeError(f"formal identity collision after frozen alias closure: {key}")
        formal_index[key] = r

    missing = []
    conflicts = []
    joined = 0
    for key, f in formal_index.items():
        s = source.get(key)
        if s is None:
            missing.append({
                "join_key": list(key),
                "formal_fixture_id": f.fixture_id,
                "competition_id": f.competition_id,
                "season": f.season,
                "kickoff": f.kickoff.isoformat(),
                "home_team": f.home_team_name,
                "away_team": f.away_team_name,
                "formal_score": [f.home_goals, f.away_goals],
                "formal_source_path": f.source_path,
            })
            continue
        joined += 1
        if (s["home_goals"], s["away_goals"]) != (f.home_goals, f.away_goals):
            conflicts.append({
                "join_key": list(key),
                "formal_fixture_id": f.fixture_id,
                "competition_id": f.competition_id,
                "season": f.season,
                "kickoff": f.kickoff.isoformat(),
                "home_team": f.home_team_name,
                "away_team": f.away_team_name,
                "formal_score": [f.home_goals, f.away_goals],
                "formal_source_path": f.source_path,
                "formal_source_sha256": f.source_sha256,
                "xg_source_kind": s["source_kind"],
                "xg_source_fixture_id": s["source_fixture_id"],
                "xg_source_score": [s["home_goals"], s["away_goals"]],
                "xg": [s["home_xg"], s["away_xg"]],
                "xg_release_at": s["release_at"],
                "xg_identity_record": s.get("identity_record"),
            })
    extra = [{"join_key": list(k), **{kk: vv for kk, vv in v.items() if kk not in ("home_xg", "away_xg")}}
             for k, v in source.items() if k not in formal_index]

    by_comp_missing: dict[str, int] = {}
    by_comp_extra: dict[str, int] = {}
    for x in missing:
        by_comp_missing[x["competition_id"]] = by_comp_missing.get(x["competition_id"], 0) + 1
    for x in extra:
        comp = str(x["join_key"][0])
        by_comp_extra[comp] = by_comp_extra.get(comp, 0) + 1

    payload = {
        "schema_version": "football3-formal-xg-existing-identity-closure-diagnostic-v1",
        "diagnostic_only": True,
        "candidate_metrics_computed": False,
        "candidate_parameters_or_gates_modified": False,
        "scientific_source_semantics_modified": False,
        "production_runtime_modified": False,
        "team_aliases_json_modified": False,
        "identity_bridge_source": alias_meta,
        "formal_universe_n": len(formal_index),
        "xg_source_n": len(source),
        "joined_n": joined,
        "missing_n": len(missing),
        "extra_n": len(extra),
        "conflict_n": len(conflicts),
        "missing_by_competition": by_comp_missing,
        "extra_by_competition": by_comp_extra,
        "alias_side_usages_by_competition": alias_usage,
        "conflicts": conflicts,
        "missing": missing,
        "extra": extra,
        "frozen_v1_history": v1src,
        "bindings": {
            "understat_sha256": rt._sha_file(Path(args.understat_db)),
            "confirmation_identity_sha256": rt._sha_file(Path(args.confirmation_dir) / "confirmation_identity.jsonl"),
            "confirmation_vault_sha256": rt._sha_file(Path(args.confirmation_dir) / "confirmation_xg_result_vault.jsonl"),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS_DIAGNOSTIC",
        "formal_universe_n": len(formal_index),
        "xg_source_n": len(source),
        "joined_n": joined,
        "missing_n": len(missing),
        "extra_n": len(extra),
        "conflict_n": len(conflicts),
        "missing_by_competition": by_comp_missing,
        "extra_by_competition": by_comp_extra,
        "conflict_fixture_ids": [x["formal_fixture_id"] for x in conflicts],
    }, sort_keys=True))
    for x in missing[:20]:
        print("MISSING", json.dumps(x, ensure_ascii=False, sort_keys=True))
    for x in extra[:20]:
        print("EXTRA", json.dumps(x, ensure_ascii=False, sort_keys=True))
    for x in conflicts:
        print("CONFLICT", json.dumps(x, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
