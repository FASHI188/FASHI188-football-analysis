from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE_DIR = ROOT / "engine"
import sys
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from platform_core import normalize_team_token, read_processed_matches  # type: ignore
from pure_engine import EngineState, Fixture, Parameters, canonical_json_hash  # type: ignore

FORWARD_DIR = HERE / "forward"
LOCK_PATH = FORWARD_DIR / "model_lock.json"
SAFE_CAPTURE_PATH = FORWARD_DIR / "safe_capture_candidates.json"
LEDGER_PATH = FORWARD_DIR / "ledger.jsonl"
STATUS_PATH = FORWARD_DIR / "status.json"
AUDIT_PATH = FORWARD_DIR / "latest_audit.json"
CHECKPOINTS = (30, 100, 300)
TARGET = 300
T60 = timedelta(minutes=60)
FORBIDDEN_LEDGER_KEYS = {"home_goals", "away_goals", "result", "outcome", "score", "status_result", "settled"}


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RuntimeError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def global_team_id(name: str) -> str:
    token = normalize_team_token(name)
    if not token:
        raise RuntimeError(f"empty canonical team token: {name!r}")
    return "gteam_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def history_fixture_id(cid: str, season: str, kickoff: datetime, home: str, away: str) -> str:
    raw = "|".join((cid, season, kickoff.isoformat(), home, away)).encode("utf-8")
    return "hist_" + hashlib.sha256(raw).hexdigest()[:24]


def load_lock() -> dict[str, Any]:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("scientific_status") != "MODEL_CANDIDATE_PASSED":
        raise RuntimeError("forward lock is not bound to MODEL_CANDIDATE_PASSED")
    if lock.get("candidate_source_head") != "7986b5b528338d1d359f1287677f4ab92e453f39":
        raise RuntimeError("unexpected candidate source head")
    if lock.get("pure_engine_sha256") != sha256_file(HERE / "pure_engine.py"):
        raise RuntimeError("pure engine hash drift")
    return lock


def bootstrap_history(lock: dict[str, Any]) -> tuple[EngineState, dict[str, Any]]:
    params = Parameters(**lock["selected_parameters"])
    expected = lock["training_universe"]
    allowed: dict[str, set[str]] = {cid: set(seasons) for cid, seasons in expected["competitions"].items()}
    rows: list[Any] = []
    for cid in sorted(allowed):
        rows.extend(m for m in read_processed_matches(cid) if str(m.season) in allowed[cid])
    rows.sort(key=lambda m: (m.date, m.competition_id, str(m.season), m.home_team, m.away_team))
    if len(rows) != int(expected["n"]):
        raise RuntimeError(f"frozen history count drift: {len(rows)} != {expected['n']}")
    if rows[0].date.isoformat() != expected["first"] or rows[-1].date.isoformat() != expected["last"]:
        raise RuntimeError("frozen history time range drift")

    digest = hashlib.sha256()
    engine = EngineState(params=params)
    grouped: dict[datetime, list[Any]] = defaultdict(list)
    for m in rows:
        grouped[m.date].append(m)
        digest.update(json.dumps([
            m.competition_id, str(m.season), m.date.isoformat(), m.home_team, m.away_team,
            int(m.home_goals), int(m.away_goals), m.source_path,
        ], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    for cutoff in sorted(grouped):
        fixtures: list[Fixture] = []
        labels: dict[str, tuple[int, int]] = {}
        for m in grouped[cutoff]:
            fid = history_fixture_id(m.competition_id, str(m.season), m.date, m.home_team, m.away_team)
            fixtures.append(Fixture(fid, m.competition_id, str(m.season), m.date, global_team_id(m.home_team), global_team_id(m.away_team)))
            labels[fid] = (int(m.home_goals), int(m.away_goals))
        engine.apply_batch(fixtures, labels)
    audit = {
        "history_n": len(rows),
        "history_first": rows[0].date.isoformat(),
        "history_last": rows[-1].date.isoformat(),
        "history_digest_sha256": digest.hexdigest(),
        "same_cutoff_predict_all_then_update": True,
        "source_anchor": lock["anchor"],
    }
    return engine, audit


def load_ledger() -> list[dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for lineno, line in enumerate(LEDGER_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        overlap = FORBIDDEN_LEDGER_KEYS.intersection(row)
        if overlap:
            raise RuntimeError(f"label-like ledger keys at line {lineno}: {sorted(overlap)}")
        event = str(row.get("provider_event_id") or "")
        if not event or event in seen_events:
            raise RuntimeError(f"duplicate/empty provider event in ledger line {lineno}")
        seen_events.add(event)
        out.append(row)
    if len(out) > TARGET:
        raise RuntimeError("ledger exceeds preregistered 300 target")
    return out


def season_hints() -> dict[str, str]:
    path = ROOT / "config" / "v6_full17_capture_identity_v6484.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(cid): str(comp.get("processed_latest_season_hint") or "") for cid, comp in (data.get("competitions") or {}).items()}


def eligibility(event: dict[str, Any], lock: dict[str, Any]) -> tuple[bool, str]:
    try:
        event_id = str(event["provider_event_id"]).strip()
        cid = str(event["competition_id"]).strip()
        home = str(event["canonical_home"]).strip()
        away = str(event["canonical_away"]).strip()
        kickoff = dt(event["kickoff_utc"])
        observed = dt(event["observed_at_utc"])
    except Exception as exc:
        return False, f"invalid_safe_projection:{type(exc).__name__}"
    if not event_id or not cid or not home or not away or home == away:
        return False, "identity_invalid"
    if str(event.get("provider_state")) != "NOT_STARTED":
        return False, "not_prematch"
    if observed < dt(lock["forward_not_before_utc"]):
        return False, "before_forward_lock"
    if observed > kickoff - T60:
        return False, "after_t60_cutoff"
    return True, "eligible"


def canonical_event_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Canonicalize only representation, never identity semantics.

    Provider event ids are compared separately. Competition/team strings remain
    exact after whitespace trimming. Kickoff is normalized to one UTC ISO form so
    equivalent `Z` and `+00:00` representations do not create false drift, while
    any actual time change remains fail-closed.
    """
    return (
        str(row.get("competition_id") or "").strip(),
        str(row.get("canonical_home") or "").strip(),
        str(row.get("canonical_away") or "").strip(),
        dt(str(row.get("kickoff_utc") or "")).isoformat(),
    )


def append_predictions(engine: EngineState, lock: dict[str, Any], safe: dict[str, Any], ledger: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    existing = {str(r["provider_event_id"]): r for r in ledger}
    hints = season_hints()
    rejected: dict[str, int] = defaultdict(int)
    added = 0
    for event in safe.get("events") or []:
        ok, reason = eligibility(event, lock)
        if not ok:
            rejected[reason] += 1
            continue
        event_id = str(event["provider_event_id"])
        if event_id in existing:
            prior = existing[event_id]
            if canonical_event_identity(event) != canonical_event_identity(prior):
                raise RuntimeError(f"provider event identity drift: {event_id}")
            continue
        if len(ledger) >= TARGET:
            break
        cid = str(event["competition_id"])
        season = hints.get(cid) or str(dt(event["kickoff_utc"]).year)
        fixture_id = f"newv1:kambi:{event_id}"
        fixture = Fixture(
            fixture_id=fixture_id,
            competition_id=cid,
            season=season,
            kickoff=dt(event["kickoff_utc"]),
            home_team_id=global_team_id(str(event["canonical_home"])),
            away_team_id=global_team_id(str(event["canonical_away"])),
        )
        pred = engine.predict(fixture)
        row = {
            "schema_version": "football3-new-engine-v1-forward-zero-label-v1",
            "provider": "kambi",
            "provider_event_id": event_id,
            "fixture_id": fixture_id,
            "competition_id": cid,
            "season": season,
            "canonical_home": str(event["canonical_home"]),
            "canonical_away": str(event["canonical_away"]),
            "kickoff_utc": dt(event["kickoff_utc"]).isoformat(),
            "observed_at_utc": dt(event["observed_at_utc"]).isoformat(),
            "prediction_cutoff": "T_minus_60_minutes_or_earlier",
            "safe_projection_sha256": sha256_file(SAFE_CAPTURE_PATH),
            "capture_manifest_sha256": safe.get("capture_manifest_sha256"),
            "candidate_source_head": lock["candidate_source_head"],
            "candidate_source_artifact_digest": lock["candidate_source_artifact_digest"],
            "same_match_reaudit_sha256": sha256_file(HERE / "evidence" / "historical_gate_reaudit.json"),
            "pure_engine_sha256": lock["pure_engine_sha256"],
            "prediction": pred,
            "outcomes_read": False,
            "labels_present": False,
        }
        row["row_hash"] = canonical_json_hash(row)
        ledger.append(row)
        existing[event_id] = row
        added += 1
    return ledger, {"added": added, "rejected": dict(sorted(rejected.items()))}


def write_ledger(ledger: list[dict[str, Any]]) -> str:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in ledger)
    LEDGER_PATH.write_text(text, encoding="utf-8")
    return sha256_bytes(text.encode("utf-8"))


def write_checkpoints(ledger: list[dict[str, Any]], lock: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for n in CHECKPOINTS:
        path = FORWARD_DIR / f"checkpoint_{n}.json"
        reached = len(ledger) >= n
        result[str(n)] = {"reached": reached, "path": str(path.relative_to(HERE)) if reached else None}
        if not reached:
            continue
        prefix = ledger[:n]
        payload = {
            "schema_version": "football3-new-engine-v1-forward-checkpoint-v1",
            "checkpoint_n": n,
            "candidate_source_head": lock["candidate_source_head"],
            "labels_present": False,
            "outcomes_read": False,
            "row_hashes": [row["row_hash"] for row in prefix],
            "prefix_hash": canonical_json_hash([row["row_hash"] for row in prefix]),
            "first_kickoff": prefix[0]["kickoff_utc"],
            "last_kickoff": prefix[-1]["kickoff_utc"],
        }
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != payload:
                raise RuntimeError(f"immutable checkpoint drift: {n}")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    lock = load_lock()
    reaudit_path = HERE / "evidence" / "historical_gate_reaudit.json"
    reaudit = json.loads(reaudit_path.read_text(encoding="utf-8"))
    if reaudit.get("corrected_scientific_status") != "MODEL_CANDIDATE_PASSED":
        raise RuntimeError("corrected same-match scientific gate is not passed")
    safe = json.loads(SAFE_CAPTURE_PATH.read_text(encoding="utf-8"))
    engine, history_audit = bootstrap_history(lock)
    ledger = load_ledger()
    before = len(ledger)
    ledger, cycle = append_predictions(engine, lock, safe, ledger)
    ledger_hash = write_ledger(ledger)
    checkpoints = write_checkpoints(ledger, lock)
    status = {
        "schema_version": "football3-new-engine-v1-forward-status-v1",
        "scientific_status": "MODEL_CANDIDATE_PASSED",
        "forward_status": "TARGET_300_FROZEN" if len(ledger) >= TARGET else "ENROLLING_ZERO_LABEL",
        "enrolled_rows": len(ledger),
        "target_rows": TARGET,
        "rows_before_cycle": before,
        "rows_added_cycle": cycle["added"],
        "labels_present": False,
        "outcomes_read": False,
        "pure_runner_safe_projection_only": True,
        "ledger_sha256": ledger_hash,
        "checkpoints": checkpoints,
        "candidate_source_head": lock["candidate_source_head"],
        "candidate_source_artifact_digest": lock["candidate_source_artifact_digest"],
        "same_match_reaudit_sha256": sha256_file(HERE / "evidence" / "historical_gate_reaudit.json"),
    }
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = {
        "schema_version": "football3-new-engine-v1-forward-cycle-audit-v1",
        "safe_capture_sha256": sha256_file(SAFE_CAPTURE_PATH),
        "capture_manifest_sha256": safe.get("capture_manifest_sha256"),
        "capture_status": safe.get("capture_status"),
        "safe_event_count": safe.get("safe_event_count"),
        "cycle": cycle,
        "history": history_audit,
        "ledger_sha256": ledger_hash,
        "enrolled_rows": len(ledger),
        "forbidden_label_keys_checked": sorted(FORBIDDEN_LEDGER_KEYS),
        "outcomes_read": False,
    }
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"forward_status": status["forward_status"], "enrolled_rows": len(ledger), "added": cycle["added"], "rejected": cycle["rejected"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
