#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import runtime as rt
import formal_result_adjudication_v2 as formal_adjudication
import formal_result_adjudication_v1 as result_contract
from v6_understat_alias_qualification_v6187 import exact_mapped_fixture

BIG5 = ("ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1")
TARGET_SEASONS = ("2022/23", "2023/24", "2024/25")
ALIAS_SCHEMA = "V6.18.8-understat-iterative-schedule-anchor-alias-closure-r1"
ALIAS_CLASSIFICATION = "DATA_IDENTITY_CLOSURE_AUDIT_ONLY_NO_MODEL_FIT"
EXPECTED_JOIN = 5330
EXPECTED_SHORT_DRIFT = 7
UNION_FID = "8ac7540a70af27118955481e"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob(repo: Path, path: Path) -> str:
    rel = path.relative_to(repo)
    return subprocess.check_output(["git", "hash-object", str(rel)], cwd=repo, text=True).strip()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_alias_closure(path: Path) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("schema_version") != ALIAS_SCHEMA or d.get("status") != "PASS":
        raise RuntimeError("frozen v6.18.8 alias closure identity mismatch")
    if d.get("classification") != ALIAS_CLASSIFICATION:
        raise RuntimeError("v6.18.8 closure is not audit-only")
    design = d.get("design") or {}
    required = {
        "one_to_one_both_directions_required": True,
        "exact_identity_conflict_forbidden": True,
        "final_attachment_is_exact_after_alias_mapping": True,
        "fuzzy_training_rows": False,
    }
    for key, expected in required.items():
        if design.get(key) is not expected:
            raise RuntimeError(f"v6.18.8 design drift: {key}")
    out: dict[str, dict[str, str]] = {}
    total = 0
    for comp in BIG5:
        dom = (d.get("domains") or {}).get(comp) or {}
        rows = dom.get("qualified_aliases") or {}
        m: dict[str, str] = {}
        inv: dict[str, str] = {}
        for key, rec in rows.items():
            p = str(rec.get("platform_token") or key).strip()
            u = str(rec.get("understat_token") or "").strip()
            if not p or not u or p != rt._normalize_team(p) or u != rt._normalize_team(u):
                raise RuntimeError(f"invalid frozen alias token {comp}: {p!r}->{u!r}")
            if p in m and m[p] != u:
                raise RuntimeError(f"nonfunctional frozen alias {comp}: {p}")
            if u in inv and inv[u] != p:
                raise RuntimeError(f"non-injective frozen alias {comp}: {u}")
            m[p] = u
            inv[u] = p
        out[comp] = m
        total += len(m)
    if total != int(d.get("qualified_alias_count") or -1):
        raise RuntimeError("v6.18.8 qualified alias count drift")
    return out, {
        "schema_version": d["schema_version"],
        "classification": d["classification"],
        "qualified_alias_count": total,
        "generated_at_utc": d.get("generated_at_utc"),
        "design": {k: design.get(k) for k in required},
        "aggregate": d.get("aggregate"),
        "domain_rates": d.get("domain_rates"),
    }


def source_rows(understat_db: Path, confirmation_dir: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
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
            "competition_id": comp,
            "date": dt.date().isoformat(),
            "home": str(r["team_h"]),
            "away": str(r["team_a"]),
            "family": "UNDERSTAT_FROZEN_DB",
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
        raise rt.RuntimeGateError("confirmation vault duplicate fixture id")
    for r in identities:
        sid = str(r["fixture_id"])
        v = vault.get(sid)
        if v is None or str(v.get("kickoff")) != str(r["kickoff"]):
            raise rt.RuntimeGateError("confirmation identity/vault mismatch")
        dt = rt._parse_dt(str(r["kickoff"]), "confirmation kickoff")
        comp = rt.BIG5[str(r["league"])]
        key = (comp, dt.date().isoformat(), rt._normalize_team(str(r["home_team"])), rt._normalize_team(str(r["away_team"])))
        if key in source:
            raise rt.RuntimeGateError(f"duplicate combined XG identity: {key}")
        source[key] = {
            "competition_id": comp,
            "date": dt.date().isoformat(),
            "home": str(r["home_team"]),
            "away": str(r["away_team"]),
            "family": "CONFIRMATION_FROZEN_VAULT",
            "source_fixture_id": sid,
            "home_goals": int(v["home_goals"]),
            "away_goals": int(v["away_goals"]),
            "home_xg": float(v["home_xg"]),
            "away_xg": float(v["away_xg"]),
            "release_at": str(v["release_at"]),
        }
    if len(source) != EXPECTED_JOIN:
        raise RuntimeError(f"frozen XG source universe mismatch: {len(source)}")
    return source


def source_pair_indexes(source: dict[tuple[str, str, str, str], dict[str, Any]]):
    out: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {c: defaultdict(list) for c in BIG5}
    for (comp, day, h, a), row in source.items():
        out[comp][(h, a)].append({
            "date": day,
            "id": str(row["source_fixture_id"]),
            "source_fixture_id": str(row["source_fixture_id"]),
            "source_kind": str(row["family"]),
            "home_goals": int(row["home_goals"]),
            "away_goals": int(row["away_goals"]),
            "release_at": str(row["release_at"]),
        })
    return out


def v6188_date_gate(history, alias_map, source):
    pair_indexes = source_pair_indexes(source)
    formal_rows = [r for r in history if r.competition_id in BIG5 and r.season in TARGET_SEASONS]
    if len(formal_rows) != EXPECTED_JOIN:
        raise RuntimeError(f"formal universe mismatch before v6.18.8 date gate: {len(formal_rows)}")
    matched_source_keys: set[tuple[str, str, str, str]] = set()
    drift = []
    unresolved = []
    ambiguous = []
    duplicate_formal = set()
    seen_formal_keys = set()

    for r in formal_rows:
        h0 = rt._normalize_team(r.home_team_name)
        a0 = rt._normalize_team(r.away_team_name)
        h = alias_map[r.competition_id].get(h0, h0)
        a = alias_map[r.competition_id].get(a0, a0)
        fkey = (r.competition_id, r.kickoff.date().isoformat(), h, a)
        if fkey in seen_formal_keys:
            duplicate_formal.add(fkey)
        seen_formal_keys.add(fkey)

        fixture, reason = exact_mapped_fixture(h, a, r.kickoff.date().isoformat(), pair_indexes[r.competition_id])
        if fixture is None:
            rec = {
                "fixture_id": r.fixture_id,
                "competition_id": r.competition_id,
                "season": r.season,
                "formal_date": r.kickoff.date().isoformat(),
                "home_team": r.home_team_name,
                "away_team": r.away_team_name,
                "mapped_home_token": h,
                "mapped_away_token": a,
                "reason": reason,
            }
            unresolved.append(rec)
            if reason == "AMBIGUOUS_NEAREST_DATE":
                ambiguous.append(rec)
            continue

        skey = (r.competition_id, str(fixture["date"]), h, a)
        if skey in matched_source_keys:
            raise RuntimeError(f"duplicate v6.18.8 source attachment: {skey}")
        matched_source_keys.add(skey)
        if reason in ("DATE_DRIFT_1", "DATE_DRIFT_2"):
            source_day = datetime.fromisoformat(str(fixture["date"])).date()
            formal_day = r.kickoff.date()
            drift.append({
                "fixture_id": r.fixture_id,
                "competition_id": r.competition_id,
                "season": r.season,
                "home_team": r.home_team_name,
                "away_team": r.away_team_name,
                "formal_date": formal_day.isoformat(),
                "source_date": source_day.isoformat(),
                "signed_formal_minus_source_days": (formal_day - source_day).days,
                "v6188_match_reason": reason,
                "source_fixture_id": fixture["source_fixture_id"],
                "source_kind": fixture["source_kind"],
                "result_or_xg_used_for_alignment": False,
            })
        elif reason != "DATE_DRIFT_0":
            raise RuntimeError(f"unexpected v6.18.8 match reason: {reason}")

    extra = [list(k) for k in source if k not in matched_source_keys]
    return {
        "formal_n": len(formal_rows),
        "source_n": len(source),
        "joined_n": len(matched_source_keys),
        "missing_n": len(unresolved),
        "extra_n": len(extra),
        "ambiguous_n": len(ambiguous),
        "duplicate_n": len(duplicate_formal),
        "date_drift_1_or_2_n": len(drift),
        "date_drift_1_or_2": drift,
        "unresolved": unresolved,
        "unattached_source_keys": extra,
    }


def repo_binding(repo: Path, rel: str) -> dict[str, Any]:
    p = repo / rel
    return {
        "path": rel,
        "git_blob_sha": git_blob(repo, p),
        "sha256": sha256_file(p),
        "bytes": p.stat().st_size,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--understat-db", required=True)
    ap.add_argument("--confirmation-dir", required=True)
    ap.add_argument("--alias-closure", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    understat_db = Path(args.understat_db).resolve()
    confirmation_dir = Path(args.confirmation_dir).resolve()
    alias_path = Path(args.alias_closure).resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    alias_map, alias_meta = load_alias_closure(alias_path)
    history, v1src = rt.load_frozen_v1_history(repo)
    source = source_rows(understat_db, confirmation_dir)
    pre_date = v6188_date_gate(history, alias_map, source)

    if pre_date["joined_n"] != 5329 or pre_date["missing_n"] != 1 or pre_date["extra_n"] != 1:
        raise RuntimeError(f"v6.18.8 residual date gate unexpected: {pre_date}")
    if pre_date["ambiguous_n"] != 0 or pre_date["duplicate_n"] != 0:
        raise RuntimeError("v6.18.8 residual date gate ambiguous/duplicate")
    if pre_date["date_drift_1_or_2_n"] != EXPECTED_SHORT_DRIFT:
        raise RuntimeError(f"expected {EXPECTED_SHORT_DRIFT} short date drifts")
    if any(abs(int(x["signed_formal_minus_source_days"])) not in (1, 2) for x in pre_date["date_drift_1_or_2"]):
        raise RuntimeError("short date drift outside frozen v6.18.8 rule")

    only = pre_date["unresolved"][0]
    if (
        only["competition_id"], only["home_team"], only["away_team"], only["formal_date"], only["reason"]
    ) != ("ITA_SerieA", "Udinese", "Roma", "2024-04-25", "OUTSIDE_DATE_TOLERANCE"):
        raise RuntimeError(f"unexpected sole >2d residual: {only}")

    install_receipt = formal_adjudication.install()
    labels, xgsrc = rt.load_xg_labels(history, understat_db, confirmation_dir)
    ib = xgsrc.get("identity_bridge") or {}
    if int(xgsrc.get("joined_n") or -1) != EXPECTED_JOIN:
        raise RuntimeError("formal adjudicated XG joined count mismatch")
    if int(ib.get("missing_n") or -1) != 0 or int(ib.get("extra_n") or -1) != 0:
        raise RuntimeError("formal adjudicated XG missing/extra nonzero")
    if len(labels) != EXPECTED_JOIN or len(set(labels)) != EXPECTED_JOIN:
        raise RuntimeError("formal adjudicated XG duplicate label fixture id")

    unresolved_identity = []
    for audit in ib.get("audits") or []:
        unresolved_identity.extend(audit.get("unresolved") or [])
    if unresolved_identity:
        raise RuntimeError(f"formal identity audit unresolved: {unresolved_identity[:3]}")

    date_alignments = ib.get("date_alignments") or []
    short = [x for x in date_alignments if abs(int(x["signed_formal_minus_source_days"])) <= 2]
    long = [x for x in date_alignments if abs(int(x["signed_formal_minus_source_days"])) > 2]
    if len(short) != EXPECTED_SHORT_DRIFT:
        raise RuntimeError(f"formal date bridge short count mismatch: {len(short)}")
    if {x["fixture_id"] for x in short} != {x["fixture_id"] for x in pre_date["date_drift_1_or_2"]}:
        raise RuntimeError("formal short date bridge differs from frozen v6.18.8 attachments")
    if len(long) != 1:
        raise RuntimeError(f"expected one resumed cross-date fixture, got {len(long)}")

    resumed = long[0]
    if (
        resumed["competition_id"], resumed["home_team_name"], resumed["away_team_name"],
        resumed["source_date"], resumed["formal_date"], int(resumed["signed_formal_minus_source_days"])
    ) != ("ITA_SerieA", "Udinese", "Roma", "2024-04-14", "2024-04-25", 11):
        raise RuntimeError(f"resumed fixture identity drift: {resumed}")

    time_violations = []
    history_by_id = {r.fixture_id: r for r in history}
    for fid, lab in labels.items():
        fixture = history_by_id[fid]
        if lab.label.release_at <= fixture.kickoff:
            time_violations.append({"fixture_id": fid, "reason": "LABEL_RELEASE_NOT_AFTER_FORMAL_KICKOFF"})
    for row in date_alignments:
        original = parse_dt(row["original_release_at"])
        effective = parse_dt(row["effective_release_at"])
        if effective < original:
            time_violations.append({"fixture_id": row["fixture_id"], "reason": "EFFECTIVE_RELEASE_BEFORE_SOURCE_RELEASE"})
        if row.get("release_adjusted_to_conservative_formal_day_floor"):
            floor = datetime.fromisoformat(row["formal_date"]).replace(tzinfo=timezone.utc) + timedelta(
                hours=int(ib.get("date_bridge_pit_floor_hours_from_formal_day") or 27)
            )
            if effective < floor:
                time_violations.append({"fixture_id": row["fixture_id"], "reason": "DATE_BRIDGE_RELEASE_BEFORE_FORMAL_DAY_FLOOR"})

    resumed_effective = parse_dt(resumed["effective_release_at"])
    resumed_original = parse_dt(resumed["original_release_at"])
    resumed_floor = datetime(2024, 4, 25, tzinfo=timezone.utc) + timedelta(hours=27)
    if resumed_original.date().isoformat() != "2024-04-14":
        time_violations.append({"fixture_id": resumed["fixture_id"], "reason": "RESUMED_ORIGINAL_XG_DATE_DRIFT"})
    if resumed_effective < resumed_floor or resumed_effective <= datetime(2024, 4, 25, 23, 59, 59, tzinfo=timezone.utc):
        time_violations.append({"fixture_id": resumed["fixture_id"], "reason": "RESUMED_FINAL_RESULT_RELEASE_TOO_EARLY"})

    events = rt.history_delta_events(
        history, labels, None, datetime(2026, 7, 1, tzinfo=timezone.utc), None
    )
    resumed_releases = [
        e for e in events if e["fixture_id"] == resumed["fixture_id"] and e["event_type"] == "LABEL_RELEASE"
    ]
    if len(resumed_releases) != 1:
        raise RuntimeError(f"resumed fixture release-event count mismatch: {len(resumed_releases)}")
    rr = resumed_releases[0]
    if not (rr["enters_v1"] is True and rr["enters_xg"] is True):
        raise RuntimeError("resumed fixture must release final result+xG together only after completion")
    if parse_dt(rr["event_at"]) != resumed_effective:
        raise RuntimeError("resumed fixture event release differs from effective release")
    if [int(rr["home_goals"]), int(rr["away_goals"])] != [1, 2]:
        raise RuntimeError("resumed fixture final result drift")

    adjudications = formal_adjudication.adjudication_entries()
    if set(adjudications) != {UNION_FID}:
        raise RuntimeError(f"unexpected formal result adjudication set: {sorted(adjudications)}")
    union_entry = adjudications[UNION_FID]
    union_fixture = history_by_id[UNION_FID]
    union_label = labels[UNION_FID]
    if [union_fixture.home_goals, union_fixture.away_goals] != [0, 2]:
        raise RuntimeError("Union-Bochum formal settlement drift")
    if [union_label.label.home_goals, union_label.label.away_goals] != [1, 1]:
        raise RuntimeError("Union-Bochum on-field XG result drift")
    union_releases = [
        e for e in events if e["fixture_id"] == UNION_FID and e["event_type"] == "LABEL_RELEASE"
    ]
    if len(union_releases) != 2:
        raise RuntimeError(f"Union-Bochum split release count mismatch: {len(union_releases)}")
    xg_events = [e for e in union_releases if e.get("enters_xg") and not e.get("enters_v1")]
    settlement_events = [e for e in union_releases if e.get("enters_v1") and not e.get("enters_xg")]
    if len(xg_events) != 1 or len(settlement_events) != 1:
        raise RuntimeError("Union-Bochum route split drift")
    xe, se = xg_events[0], settlement_events[0]
    if [xe["home_goals"], xe["away_goals"]] != [1, 1] or xe.get("result_semantics") != "ON_FIELD_XG_RESULT":
        raise RuntimeError("Union-Bochum on-field event semantics drift")
    if [se["home_goals"], se["away_goals"]] != [0, 2] or se.get("result_semantics") != "AUTHORITATIVE_FINAL_SETTLEMENT":
        raise RuntimeError("Union-Bochum settlement event semantics drift")
    formal_at = parse_dt(union_entry["availability"]["formal_result_available_at"])
    if parse_dt(se["event_at"]) != formal_at or parse_dt(xe["event_at"]) >= formal_at:
        time_violations.append({"fixture_id": UNION_FID, "reason": "UNION_SPLIT_RELEASE_ORDER_INVALID"})

    applied = (xgsrc.get("result_adjudication") or {}).get("applied") or []
    if len(applied) != 1 or applied[0].get("fixture_id") != UNION_FID:
        raise RuntimeError("formal result adjudication consumption mismatch")

    repo_files = [
        "football-data/research/promoted_cold_start_v2_1/PREREGISTRATION.json",
        "football-data/research/promoted_cold_start_v2_1/evaluate.py",
        "football-data/formal_fast_runtime_v1/runtime.py",
        "football-data/config/team_aliases.json",
        "football-data/manifests/v6_understat_alias_closure_v6188_status.json",
        "football-data/validation/v6_understat_alias_qualification_v6187.py",
        "football-data/formal_gpt_gateway_v1/result_adjudications_v1.json",
        "football-data/formal_gpt_gateway_v1/formal_result_adjudication_v1.py",
        "football-data/formal_gpt_gateway_v1/formal_frozen_xg_identity_adjudication_v1.py",
        "football-data/formal_gpt_gateway_v1/formal_frozen_xg_identity_adjudication_v2.py",
        "football-data/formal_gpt_gateway_v1/formal_result_adjudication_v2.py",
        "football-data/formal_gpt_gateway_v1/formal_v1_release_order_replay_v1.py",
    ]
    bindings = {rel: repo_binding(repo, rel) for rel in repo_files}
    bindings["understat_frozen.db"] = {
        "path": str(understat_db),
        "sha256": sha256_file(understat_db),
        "bytes": understat_db.stat().st_size,
        "artifact_binding_sha256": rt.BINDINGS["understat_frozen.db"]["sha256"],
    }
    for name in ("confirmation_identity.jsonl", "confirmation_xg_result_vault.jsonl"):
        p = confirmation_dir / name
        bindings[name] = {
            "path": str(p),
            "sha256": sha256_file(p),
            "bytes": p.stat().st_size,
            "artifact_binding_sha256": rt.BINDINGS[name]["sha256"],
        }

    hard = {
        "joined": int(xgsrc["joined_n"]),
        "expected_joined": EXPECTED_JOIN,
        "missing": int(ib["missing_n"]),
        "extra": int(ib["extra_n"]),
        "ambiguous": 0,
        "duplicate": 0,
        "time_leakage": len(time_violations),
    }
    hard["pass"] = hard == {
        "joined": 5330,
        "expected_joined": 5330,
        "missing": 0,
        "extra": 0,
        "ambiguous": 0,
        "duplicate": 0,
        "time_leakage": 0,
    }
    if not hard["pass"]:
        raise RuntimeError(f"PRE_LABEL_HARD_GATE_FAIL: {hard}; violations={time_violations}")

    payload = {
        "schema_version": "football3-promoted-cold-start-v2-1-pre-label-semantic-closure-v1",
        "status": "PASS_PRE_LABEL_HARD_GATES",
        "research_only": True,
        "candidate_metrics_computed": False,
        "candidate_evaluator_invoked": False,
        "candidate_parameters_or_gates_modified": False,
        "candidate_sample_or_cohort_modified": False,
        "preregistration_modified": False,
        "evaluator_modified": False,
        "production_runtime_modified": False,
        "team_aliases_modified": False,
        "formal_model_or_current_modified": False,
        "run_head": head,
        "hard_gates": hard,
        "frozen_v6188_identity": alias_meta,
        "v6188_pre_label_date_alignment": pre_date,
        "formal_adjudication_install": install_receipt,
        "formal_xg_source_receipt": xgsrc,
        "special_semantics": {
            "udinese_roma_resumed_same_fixture": {
                "fixture_id": resumed["fixture_id"],
                "competition_id": resumed["competition_id"],
                "season": resumed["season"],
                "home_team": resumed["home_team_name"],
                "away_team": resumed["away_team_name"],
                "source_process_date": resumed["source_date"],
                "formal_completion_date": resumed["formal_date"],
                "source_fixture_id": resumed["source_fixture_id"],
                "source_family": resumed["source_family"],
                "original_source_release_at": resumed["original_release_at"],
                "effective_result_and_xg_release_at": resumed["effective_release_at"],
                "release_policy": "EXISTING_FORMAL_V2_UNIQUE_DATE_BRIDGE_WITH_CONSERVATIVE_FORMAL_DAY_PLUS_27H_FLOOR",
                "source_identity_date_rewritten": False,
                "final_result_released_before_completion": False,
                "fixture_specific_patch_created": False,
            },
            "union_berlin_bochum_dual_semantics": {
                "fixture_id": UNION_FID,
                "formal_result": [0, 2],
                "on_field_xg_result": [1, 1],
                "xg_release_at": xe["event_at"],
                "formal_result_available_at": se["event_at"],
                "manifest_semantics": union_entry["semantics"],
                "manifest_canonical_sha256": result_contract.MANIFEST_CANONICAL_SHA256,
                "source_score_rewritten": False,
                "fixture_specific_patch_created": False,
            },
        },
        "time_leakage_violations": time_violations,
        "source_bindings": bindings,
        "frozen_v1_history": v1src,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "head": head,
        "joined": hard["joined"],
        "missing": hard["missing"],
        "extra": hard["extra"],
        "ambiguous": hard["ambiguous"],
        "duplicate": hard["duplicate"],
        "time_leakage": hard["time_leakage"],
        "v6188_short_date_drifts": len(pre_date["date_drift_1_or_2"]),
        "resumed_fixture": resumed["fixture_id"],
        "result_adjudication_fixture": UNION_FID,
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
