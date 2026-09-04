#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CONTRACT_PATH = HERE / "MW3_XI_RECONSTRUCTION_CONTRACT.json"
FIXTURE_PATH = ROOT / "football-data" / "research" / "prospective_pit_availability_xi_shadow_mw3" / "ENG_PL_2026_27_MW3_FIXTURE_FREEZE.json"
DEFAULT_OUT = HERE / "artifacts" / "mw3_xi_reconstruction"

EVENT_LIVE = "https://fantasy.premierleague.com/api/event/{event}/live/"
EVENT_FIXTURES = "https://fantasy.premierleague.com/api/fixtures/?event={event}"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"

PASS = "MW3_PROSPECTIVE_XI_RECONSTRUCTION_LOCKED_WAITING_FOR_CONFIRMED_XI_LABELS"
STOP_SOURCE = "STOP_SOURCE_COVERAGE_NO_TARGET_XI_REVEAL"
STOP_GOV = "STOP_GOVERNANCE_NO_TARGET_XI_REVEAL"

DECAY = 0.78
SMOOTH = 0.50
TARGET_SUM = 11.0
EPS = 1e-8


class GateError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safe_int(x: Any) -> int | None:
    try:
        if x is None:
            return None
        return int(x)
    except (TypeError, ValueError):
        return None


def fetch_json(url: str) -> tuple[Any, dict[str, Any], bytes]:
    observed = utc_now()
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=35) as resp:  # nosec B310 fixed official HTTPS endpoints only
        raw = resp.read()
        status = int(getattr(resp, "status", 200))
        final_url = str(resp.geturl())
        ctype = str(resp.headers.get("content-type") or "")
    retrieved = utc_now()
    if status != 200:
        raise GateError(f"HTTP_{status}:{url}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GateError(f"INVALID_JSON:{url}:{type(exc).__name__}") from exc
    meta = {
        "requested_url": url,
        "final_url": final_url,
        "observed_at_utc": iso(observed),
        "retrieved_at_utc": iso(retrieved),
        "content_type": ctype,
        "sha256": sha256_bytes(raw),
        "size": len(raw),
    }
    return data, meta, raw


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def project_sum(probs: list[float], target_sum: float = TARGET_SUM) -> list[float]:
    n = len(probs)
    if n < int(target_sum):
        raise GateError(f"INSUFFICIENT_CANDIDATES:{n}")
    if n == int(target_sum):
        return [1.0] * n
    p = [min(1.0 - EPS, max(EPS, float(x))) for x in probs]
    logits = [math.log(x / (1.0 - x)) for x in p]
    lo, hi = -30.0, 30.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        qsum = sum(sigmoid(z + mid) for z in logits)
        if qsum > target_sum:
            hi = mid
        else:
            lo = mid
    shift = (lo + hi) / 2.0
    q = [sigmoid(z + shift) for z in logits]
    err = target_sum - sum(q)
    if abs(err) > 1e-7:
        raise GateError(f"PROJECTION_SUM_ERROR:{err}")
    return q


def raw_start_probability(start_gw1: int, start_gw2: int) -> float:
    return (DECAY * float(start_gw1) + float(start_gw2) + SMOOTH) / (DECAY + 1.0 + 2.0 * SMOOTH)


def primary_excluded(status: str | None, chance: Any) -> bool:
    s = (status or "").strip().lower()
    c = safe_int(chance)
    return s in {"i", "s", "u"} or c == 0


def stress_excluded(status: str | None, chance: Any) -> bool:
    s = (status or "").strip().lower()
    c = safe_int(chance)
    return primary_excluded(s, c) or s == "d" or (c is not None and c <= 50)


def rank_rows(rows: list[dict[str, Any]], prob_key: str) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            -float(r.get(prob_key, 0.0)),
            -int(r.get("locked_bootstrap_minutes", 0) or 0),
            int(r["player_id"]),
        ),
    )


def find_snapshot_root(snapshot_dir: Path) -> Path:
    manifests = list(snapshot_dir.rglob("artifact_manifest.json"))
    if len(manifests) != 1:
        raise GateError(f"LOCKED_ARTIFACT_MANIFEST_COUNT:{len(manifests)}")
    return manifests[0].parent


def verify_locked_snapshot(snapshot_dir: Path, contract: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = find_snapshot_root(snapshot_dir)
    manifest = load_json(root / "artifact_manifest.json")
    locked = contract["locked_snapshot_input"]
    if manifest.get("head_sha") != contract["parent_snapshot_head"]:
        raise GateError("LOCKED_ARTIFACT_HEAD_MISMATCH")
    if str(manifest.get("run_id")) != str(locked["run_id"]):
        raise GateError("LOCKED_ARTIFACT_RUN_MISMATCH")
    snapshot_path = root / "snapshot.json"
    if not snapshot_path.exists():
        raise GateError("LOCKED_SNAPSHOT_JSON_MISSING")
    if sha256_file(snapshot_path) != locked["snapshot_sha256"]:
        raise GateError("LOCKED_SNAPSHOT_SHA_MISMATCH")
    snapshot = load_json(snapshot_path)
    if snapshot.get("status") != "LOCKED_PREMATCH_INPUT":
        raise GateError("LOCKED_SNAPSHOT_STATUS_MISMATCH")
    cov = snapshot.get("coverage") or {}
    if cov.get("bound_teams") != 20 or cov.get("bound_future_fixtures") != 10:
        raise GateError("LOCKED_SNAPSHOT_COVERAGE_MISMATCH")
    if snapshot.get("hard_violations"):
        raise GateError("LOCKED_SNAPSHOT_HAS_HARD_VIOLATIONS")
    return root, manifest, snapshot


def validate_governance(contract: dict[str, Any], fixtures: dict[str, Any], started: datetime) -> list[str]:
    violations: list[str] = []
    if contract.get("status") != "FROZEN_BEFORE_MW3_CONFIRMED_XI_REVEAL":
        violations.append("CONTRACT_STATUS_MISMATCH")
    if contract.get("parent_snapshot_head") != "ff6d96d89d8ced2309c1a92aa2cb13506ca92bcf":
        violations.append("PARENT_SNAPSHOT_HEAD_MISMATCH")
    gov = contract.get("governance") or {}
    forbidden = [
        "target_result_access", "target_score_access", "target_confirmed_xi_access",
        "target_postmatch_event_access", "market_or_odds_access", "target_result_as_feature",
        "target_confirmed_xi_as_feature", "retrospective_availability_backfill",
        "post_view_parameter_tuning", "parameter_search", "training_or_refit",
        "same_target_postkickoff_source_allowed", "2023_confirmation_set_access", "3504_access",
        "formal_model_change_allowed", "formal_probability_change_allowed", "CURRENT_change_allowed",
        "production_pointer_change_allowed", "formal_weights_change_allowed",
    ]
    for key in forbidden:
        if gov.get(key) is not False:
            violations.append(f"FORBIDDEN_FLAG_NOT_FALSE:{key}")
    rows = fixtures.get("fixtures") or []
    if len(rows) != 10:
        violations.append("TARGET_FIXTURE_COUNT_NOT_10")
        return violations
    earliest = min(parse_utc(str(x["kickoff_at_utc"])) for x in rows)
    frozen_at = parse_utc(str(contract["frozen_at_utc"]))
    if frozen_at >= earliest:
        violations.append("CONTRACT_NOT_FROZEN_BEFORE_EARLIEST_KICKOFF")
    if started >= earliest:
        violations.append("XI_GENERATION_STARTED_AT_OR_AFTER_EARLIEST_KICKOFF")
    if fixtures.get("label_access") is not False or fixtures.get("confirmed_xi_access") is not False or fixtures.get("market_access") is not False:
        violations.append("INHERITED_FIXTURE_FREEZE_ACCESS_FLAGS_INVALID")
    return violations


def write_stop(out: Path, status: str, reason: str, started: datetime, violations: list[str], source_audit: dict[str, Any] | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    if source_audit is not None:
        (out / "source_audit.json").write_text(json.dumps(source_audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    final = {
        "schema_version": "football3-mw3-prospective-xi-final-v1",
        "status": status,
        "reason": reason,
        "generation_started_at_utc": iso(started),
        "generation_completed_at_utc": iso(utc_now()),
        "violations": violations,
        "research_only": True,
        "promotion_allowed": False,
        "target_result_access": False,
        "target_score_access": False,
        "target_confirmed_xi_access": False,
        "market_access": False,
        "2023_opened": False,
        "3504_opened": False,
        "formal_v2_unchanged": True,
        "v3_1_1_unchanged": True,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_weights_changed": False,
    }
    (out / "final_status.json").write_text(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(final, sort_keys=True))
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    started = utc_now()
    contract = load_json(CONTRACT_PATH)
    fixtures = load_json(FIXTURE_PATH)
    gov_violations = validate_governance(contract, fixtures, started)
    if gov_violations:
        return write_stop(out, STOP_GOV, "pre_source_governance_gate_failed", started, gov_violations)

    try:
        snapshot_root, snapshot_manifest, snapshot = verify_locked_snapshot(args.snapshot_dir, contract)
    except Exception as exc:
        return write_stop(out, STOP_GOV, "locked_snapshot_verification_failed", started, [f"{type(exc).__name__}:{exc}"])

    source_audit: dict[str, Any] = {
        "schema_version": "football3-mw3-prior-round-source-audit-v1",
        "generation_started_at_utc": iso(started),
        "locked_snapshot": {
            "artifact_id": contract["locked_snapshot_input"]["artifact_id"],
            "run_id": contract["locked_snapshot_input"]["run_id"],
            "snapshot_sha256": contract["locked_snapshot_input"]["snapshot_sha256"],
            "manifest_head_sha": snapshot_manifest.get("head_sha"),
        },
        "events": {},
        "violations": [],
    }

    event_starts: dict[int, dict[int, int]] = {}
    for event in (1, 2):
        try:
            live, live_meta, live_raw = fetch_json(EVENT_LIVE.format(event=event))
            fx, fx_meta, fx_raw = fetch_json(EVENT_FIXTURES.format(event=event))
            (raw_dir / f"event_{event}_live.json").write_bytes(live_raw)
            (raw_dir / f"event_{event}_fixtures.json").write_bytes(fx_raw)
        except Exception as exc:
            source_audit["violations"].append(f"EVENT_{event}_FETCH_FAILED:{type(exc).__name__}:{exc}")
            continue

        if not isinstance(live, dict) or not isinstance(live.get("elements"), list) or not live["elements"]:
            source_audit["violations"].append(f"EVENT_{event}_LIVE_ELEMENTS_EMPTY")
            elements = []
        else:
            elements = live["elements"]
        if not isinstance(fx, list) or len(fx) != 10:
            source_audit["violations"].append(f"EVENT_{event}_FIXTURE_COUNT:{len(fx) if isinstance(fx,list) else 'NONLIST'}")
            fixtures_ok = False
        else:
            fixtures_ok = all(x.get("finished") is True for x in fx if isinstance(x, dict)) and len(fx) == 10
            if not fixtures_ok:
                source_audit["violations"].append(f"EVENT_{event}_FIXTURES_NOT_ALL_FINISHED")

        starts: dict[int, int] = {}
        missing_starts = 0
        starts_one = 0
        nonbinary = 0
        for row in elements:
            if not isinstance(row, dict):
                continue
            pid = safe_int(row.get("id"))
            stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
            if "starts" not in stats:
                missing_starts += 1
                continue
            s = safe_int(stats.get("starts"))
            if pid is None or s is None:
                continue
            if s not in (0, 1):
                nonbinary += 1
            starts[pid] = 1 if s == 1 else 0
            if s == 1:
                starts_one += 1
        if missing_starts:
            source_audit["violations"].append(f"EVENT_{event}_MISSING_STARTS_FIELD:{missing_starts}")
        if nonbinary:
            source_audit["violations"].append(f"EVENT_{event}_NONBINARY_START_VALUES:{nonbinary}")
        if starts_one != 220:
            source_audit["violations"].append(f"EVENT_{event}_START_COUNT:{starts_one}")
        event_starts[event] = starts
        source_audit["events"][str(event)] = {
            "live": live_meta,
            "fixtures": fx_meta,
            "live_element_count": len(elements),
            "fixture_count": len(fx) if isinstance(fx, list) else None,
            "all_fixtures_finished": fixtures_ok,
            "starts_field_rows": len(starts),
            "starts_one_count": starts_one,
            "missing_starts_field_rows": missing_starts,
            "nonbinary_start_rows": nonbinary,
        }

    if source_audit["violations"]:
        return write_stop(out, STOP_SOURCE, "prior_round_source_coverage_gate_failed", started, source_audit["violations"], source_audit)

    locked_bootstrap = snapshot_root / "raw" / "fpl_bootstrap.json"
    if not locked_bootstrap.exists():
        return write_stop(out, STOP_GOV, "locked_bootstrap_missing", started, ["LOCKED_BOOTSTRAP_MISSING"], source_audit)
    bootstrap = load_json(locked_bootstrap)
    teams = bootstrap.get("teams") if isinstance(bootstrap, dict) else None
    elements = bootstrap.get("elements") if isinstance(bootstrap, dict) else None
    if not isinstance(teams, list) or not isinstance(elements, list):
        return write_stop(out, STOP_GOV, "locked_bootstrap_schema_invalid", started, ["LOCKED_BOOTSTRAP_SCHEMA_INVALID"], source_audit)

    by_tid = {safe_int(t.get("id")): t for t in teams if isinstance(t, dict) and safe_int(t.get("id")) is not None}
    by_code = {str(t.get("short_name") or "").upper(): t for t in teams if isinstance(t, dict)}
    players_by_tid: dict[int, list[dict[str, Any]]] = {}
    for p in elements:
        if not isinstance(p, dict):
            continue
        tid = safe_int(p.get("team"))
        pid = safe_int(p.get("id"))
        if tid is None or pid is None:
            continue
        players_by_tid.setdefault(tid, []).append(p)

    availability_by_pid: dict[int, dict[str, Any]] = {}
    team_snapshot_rows = snapshot.get("team_rows") or []
    for tr in team_snapshot_rows:
        if not isinstance(tr, dict):
            continue
        for p in tr.get("availability") or []:
            if not isinstance(p, dict):
                continue
            pid = safe_int(p.get("player_id"))
            if pid is not None:
                availability_by_pid[pid] = p

    target_codes = sorted(str(x).upper() for x in fixtures.get("expected_team_short_names") or [])
    team_receipts: list[dict[str, Any]] = []
    hard: list[str] = []
    for code in target_codes:
        team = by_code.get(code)
        if team is None:
            hard.append(f"TARGET_TEAM_NOT_IN_LOCKED_BOOTSTRAP:{code}")
            continue
        tid = safe_int(team.get("id"))
        candidates = players_by_tid.get(tid or -1, [])
        if len(candidates) < 11:
            hard.append(f"TEAM_CANDIDATES_LT11:{code}:{len(candidates)}")
            continue

        rows: list[dict[str, Any]] = []
        raw_probs: list[float] = []
        for p in candidates:
            pid = int(p["id"])
            s1 = int(event_starts[1].get(pid, 0))
            s2 = int(event_starts[2].get(pid, 0))
            rp = raw_start_probability(s1, s2)
            avail = availability_by_pid.get(pid, {})
            status = str(avail.get("status") or p.get("status") or "").strip().lower() or None
            chance = avail.get("chance_of_playing_this_round")
            row = {
                "player_id": pid,
                "player_name": p.get("web_name") or p.get("second_name"),
                "element_type": safe_int(p.get("element_type")),
                "locked_bootstrap_minutes": safe_int(p.get("minutes")) or 0,
                "start_gw1": s1,
                "start_gw2": s2,
                "raw_start_probability": rp,
                "availability_status": status,
                "chance_of_playing_this_round": chance,
                "availability_news": avail.get("news"),
                "availability_news_added": avail.get("news_added"),
                "primary_excluded": primary_excluded(status, chance),
                "stress_excluded": stress_excluded(status, chance),
            }
            rows.append(row)
            raw_probs.append(rp)

        base_q = project_sum(raw_probs)
        for row, q in zip(rows, base_q):
            row["base_probability"] = q

        for variant, exclusion_key in (("primary", "primary_excluded"), ("stress", "stress_excluded")):
            eligible_idx = [i for i, r in enumerate(rows) if not r[exclusion_key]]
            if len(eligible_idx) < 11:
                hard.append(f"{variant.upper()}_ELIGIBLE_LT11:{code}:{len(eligible_idx)}")
                for r in rows:
                    r[f"{variant}_probability"] = 0.0
                continue
            q = project_sum([raw_probs[i] for i in eligible_idx])
            qmap = dict(zip(eligible_idx, q))
            for i, row in enumerate(rows):
                row[f"{variant}_probability"] = qmap.get(i, 0.0)

        for variant in ("base", "primary", "stress"):
            key = f"{variant}_probability"
            if abs(sum(float(r.get(key, 0.0)) for r in rows) - 11.0) > 1e-6:
                hard.append(f"{variant.upper()}_PROBABILITY_SUM:{code}")

        base_ranked = rank_rows(rows, "base_probability")
        primary_ranked = rank_rows(rows, "primary_probability")
        stress_ranked = rank_rows(rows, "stress_probability")
        receipt = {
            "team_code": code,
            "team_id": tid,
            "team_name": team.get("name"),
            "candidate_count": len(rows),
            "base_expected_xi": [r["player_id"] for r in base_ranked[:11]],
            "primary_expected_xi": [r["player_id"] for r in primary_ranked if not r["primary_excluded"]][:11],
            "stress_expected_xi": [r["player_id"] for r in stress_ranked if not r["stress_excluded"]][:11],
            "base_expected_xi_names": [r["player_name"] for r in base_ranked[:11]],
            "primary_expected_xi_names": [r["player_name"] for r in primary_ranked if not r["primary_excluded"]][:11],
            "stress_expected_xi_names": [r["player_name"] for r in stress_ranked if not r["stress_excluded"]][:11],
            "primary_excluded_player_ids": [r["player_id"] for r in rows if r["primary_excluded"]],
            "stress_excluded_player_ids": [r["player_id"] for r in rows if r["stress_excluded"]],
            "candidates": rank_rows(rows, "base_probability"),
        }
        for key in ("base_expected_xi", "primary_expected_xi", "stress_expected_xi"):
            if len(receipt[key]) != 11 or len(set(receipt[key])) != 11:
                hard.append(f"XI_SIZE_OR_UNIQUE:{code}:{key}:{len(receipt[key])}")
        team_receipts.append(receipt)

    if len(team_receipts) != 20:
        hard.append(f"TEAM_RECEIPT_COUNT:{len(team_receipts)}")

    receipt_by_code = {x["team_code"]: x for x in team_receipts}
    generated = utc_now()
    fixture_receipts: list[dict[str, Any]] = []
    for fx in fixtures.get("fixtures") or []:
        hc = str(fx["home_short"]).upper()
        ac = str(fx["away_short"]).upper()
        h = receipt_by_code.get(hc)
        a = receipt_by_code.get(ac)
        if h is None or a is None:
            hard.append(f"FIXTURE_TEAM_RECEIPT_MISSING:{fx['match_id']}")
            continue
        fixture_receipts.append({
            "schema_version": "football3-mw3-preconfirmed-xi-receipt-v1",
            "match_id": fx["match_id"],
            "kickoff_at_utc": fx["kickoff_at_utc"],
            "home_team": fx["home_team"],
            "away_team": fx["away_team"],
            "home_short": hc,
            "away_short": ac,
            "generated_at_utc": iso(generated),
            "locked_snapshot_artifact_id": contract["locked_snapshot_input"]["artifact_id"],
            "locked_snapshot_sha256": contract["locked_snapshot_input"]["snapshot_sha256"],
            "prior_event_source_sha256": {
                "gw1_live": source_audit["events"]["1"]["live"]["sha256"],
                "gw1_fixtures": source_audit["events"]["1"]["fixtures"]["sha256"],
                "gw2_live": source_audit["events"]["2"]["live"]["sha256"],
                "gw2_fixtures": source_audit["events"]["2"]["fixtures"]["sha256"],
            },
            "home": {
                "base_expected_xi": h["base_expected_xi"],
                "primary_expected_xi": h["primary_expected_xi"],
                "stress_expected_xi": h["stress_expected_xi"],
            },
            "away": {
                "base_expected_xi": a["base_expected_xi"],
                "primary_expected_xi": a["primary_expected_xi"],
                "stress_expected_xi": a["stress_expected_xi"],
            },
            "governance": {
                "target_confirmed_xi_access": False,
                "target_result_access": False,
                "target_score_access": False,
                "market_access": False,
                "contains_1x2_probability": False,
                "2023_opened": False,
                "3504_opened": False,
            },
        })
    if len(fixture_receipts) != 10:
        hard.append(f"FIXTURE_RECEIPT_COUNT:{len(fixture_receipts)}")

    source_audit["generation_completed_at_utc"] = iso(utc_now())
    source_audit["violations"] = hard
    (out / "source_audit.json").write_text(json.dumps(source_audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "team_receipts.json").write_text(json.dumps(team_receipts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "fixture_receipts.json").write_text(json.dumps(fixture_receipts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if hard:
        return write_stop(out, STOP_GOV, "xi_reconstruction_hard_gate_failed", started, hard, source_audit)

    final = {
        "schema_version": "football3-mw3-prospective-xi-final-v1",
        "status": PASS,
        "reason": "all_preconfirmed_xi_reconstruction_gates_passed",
        "generation_started_at_utc": iso(started),
        "generation_completed_at_utc": iso(utc_now()),
        "locked_snapshot_artifact_id": contract["locked_snapshot_input"]["artifact_id"],
        "locked_snapshot_sha256": contract["locked_snapshot_input"]["snapshot_sha256"],
        "team_receipts": len(team_receipts),
        "fixture_receipts": len(fixture_receipts),
        "source_event_starts": {
            "gw1": source_audit["events"]["1"]["starts_one_count"],
            "gw2": source_audit["events"]["2"]["starts_one_count"],
        },
        "research_only": True,
        "promotion_allowed": False,
        "target_result_access": False,
        "target_score_access": False,
        "target_confirmed_xi_access": False,
        "market_access": False,
        "contains_1x2_probability": False,
        "2023_opened": False,
        "3504_opened": False,
        "formal_v2_unchanged": True,
        "v3_1_1_unchanged": True,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_weights_changed": False,
    }
    (out / "final_status.json").write_text(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(final, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
