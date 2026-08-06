#!/usr/bin/env python3
"""Run the preregistered Betfair draw-trajectory pilot exactly once.

Modes are deliberately separated so that eligibility/split hashes are persisted
before any settlement label is read, and the policy selection is persisted
before confirmation labels are read. Only aggregate receipts are written.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "research" / "betfair_basic_trajectory_r1" / "ingest_betfair_basic_trajectory_r1.py"
HELPER_CONFIG = ROOT / "research" / "betfair_basic_trajectory_r1" / "preregistration.json"
SOURCE_ROOT = Path("data/dados_historicos/por_evento_id")
EXPECTED_PREREG_SHA = "b22b5b9c2eab4053c16e93bf82feb0036bde994b41e6e2fecdafa13212141c3b"
EXPECTED_PREREG_HEAD = "34f8ffdb9d1de39cc62e52b7e0886823c99224e4"
EXPECTED_SOURCE_COMMIT = "90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff"
CANDIDATE_IDS = [
    "DRAW_T15_PLUS_HALF_T90_MOVE",
    "DRAW_T15_PLUS_HALF_T6H_MOVE",
    "DRAW_T15_PLUS_HALF_T24H_MOVE",
]
PT_RE = re.compile(r'"pt"\s*:\s*(\d+)')


class PilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketRow:
    identity_hash: str
    source_path: str
    baseline: float
    candidates: dict[str, float]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PilotError(f"JSON root must be object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def lines_sha(values: Iterable[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in values).encode("utf-8")).hexdigest()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise PilotError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)


def epoch_ms(value: int | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def normalize_name(value: str) -> str:
    return " ".join(str(value).casefold().strip().split())


def clip(value: float) -> float:
    return min(0.999999, max(0.000001, float(value)))


def q_draw(prices: tuple[float, float, float]) -> float:
    inverse = [1.0 / value for value in prices]
    return inverse[1] / sum(inverse)


def load_helper():
    spec = importlib.util.spec_from_file_location("betfair_pilot_helpers", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise PilotError("cannot load frozen Betfair helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def verify_contracts(prereg_path: Path, authorization_path: Path) -> tuple[dict[str, Any], str]:
    prereg = load_json(prereg_path)
    prereg_sha = canonical_sha(prereg)
    if prereg_sha != EXPECTED_PREREG_SHA:
        raise PilotError(f"preregistration hash mismatch: {prereg_sha}")
    if prereg.get("status") != "PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN":
        raise PilotError("preregistration state changed")
    if prereg["upstream_evidence"].get("source_commit") != EXPECTED_SOURCE_COMMIT:
        raise PilotError("source commit changed")
    if prereg["eligibility_contract"].get("expected_eligible_market_count_from_inventory") != 65:
        raise PilotError("eligible count changed")
    if [row.get("id") for row in prereg["probability_contract"]["fixed_candidates"]] != CANDIDATE_IDS:
        raise PilotError("candidate catalog changed")

    authorization = load_json(authorization_path)
    expected = {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-RUN-AUTH-R1",
        "authorization_status": "AUTHORIZED_ONE_TIME_RUN",
        "authorized_user_message": "开始吧",
        "authorized_at_local": "2026-08-06T17:52:00+08:00",
        "authorized_at_utc": "2026-08-06T09:52:00Z",
        "preregistration_head": EXPECTED_PREREG_HEAD,
        "preregistration_sha256": EXPECTED_PREREG_SHA,
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "external_raw_data_transient_read_allowed": True,
        "winner_label_access_allowed": True,
        "one_time_run_only": True,
        "rerun_allowed": False,
        "raw_or_per_market_artifact_upload_allowed": False,
        "model_fit_allowed": False,
        "threshold_selection_allowed": False,
        "formal_weight": 0,
        "current_match_use_allowed": False,
        "formal_ev_allowed": False,
    }
    for key, expected_value in expected.items():
        if authorization.get(key) != expected_value:
            raise PilotError(f"authorization mismatch: {key}")
    return prereg, canonical_sha(authorization)


def discover_candidates(source_checkout: Path) -> list[Path]:
    root = source_checkout / SOURCE_ROOT
    if not root.is_dir():
        raise PilotError(f"source root missing: {root}")
    required = [b'"marketType":"MATCH_ODDS"', b'"eventTypeId":"1"', b'"The Draw"']
    rows: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            if all(token in data for token in required):
                rows.append(path)
    return rows


def blind_market(path: Path, checkout: Path, helper: Any, cfg: dict[str, Any], prereg: dict[str, Any]) -> MarketRow | None:
    cutoffs = [int(value) for value in prereg["eligibility_contract"]["required_cutoffs_minutes_before_kickoff"]]
    definition: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None
    market_id: str | None = None
    market_time: datetime | None = None
    ltp: dict[int, float] = {}
    snapshots: dict[int, tuple[float, float, float]] = {}
    previous_pt: int | None = None

    with path.open("r", encoding="utf-8-sig") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            match = PT_RE.search(raw)
            if match is None:
                raise PilotError("publish time missing")
            pt_number = int(match.group(1))
            if previous_pt is not None and pt_number < previous_pt:
                raise PilotError("non-monotonic stream")
            previous_pt = pt_number
            pt = epoch_ms(pt_number)

            # Once kickoff is known, at/after-kickoff messages are not parsed as
            # JSON. Lock and eligibility reconstruction therefore cannot load a
            # WINNER/LOSER settlement status into memory.
            if market_time is not None and pt >= market_time:
                break

            message = json.loads(raw)
            for change in message.get("mc") or []:
                if not isinstance(change, dict):
                    continue
                if change.get("id") is not None:
                    market_id = str(change["id"])
                incoming = change.get("marketDefinition")
                if isinstance(incoming, dict):
                    definition = incoming
                    if str(definition.get("eventTypeId")) != "1" or definition.get("marketType") != "MATCH_ODDS":
                        return None
                    market_time = parse_iso(str(definition["marketTime"]))
                    if pt >= market_time:
                        raise PilotError("first definition occurs at/after kickoff")
                    mapping = helper.runner_map(definition, cfg)
                for runner_change in change.get("rc") or []:
                    if not isinstance(runner_change, dict) or runner_change.get("id") is None or "ltp" not in runner_change:
                        continue
                    try:
                        value = float(runner_change["ltp"])
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(value) and value >= float(cfg["market_contract"]["ltp_minimum"]):
                        ltp[int(runner_change["id"])] = value

            if definition is None or mapping is None or market_id is None or market_time is None:
                continue
            if definition.get("inPlay") is True:
                break
            runner_ids = [mapping["home_id"], mapping["draw_id"], mapping["away_id"]]
            if not all(runner_id in ltp for runner_id in runner_ids):
                continue
            current = (ltp[runner_ids[0]], ltp[runner_ids[1]], ltp[runner_ids[2]])
            for cutoff in cutoffs:
                if pt <= market_time - timedelta(minutes=cutoff):
                    snapshots[cutoff] = current

    if definition is None or mapping is None or market_id is None or market_time is None:
        return None
    if any(cutoff not in snapshots for cutoff in cutoffs):
        return None
    if len(definition.get("runners") or []) != 3:
        return None
    event_id = str(definition.get("eventId") or "")
    event_name = str(definition.get("eventName") or "")
    if not event_id or not event_name:
        raise PilotError("event identity missing")
    canonical_identity = "|".join(
        [
            market_id,
            event_id,
            iso_utc(market_time),
            normalize_name(mapping["home_name"]),
            normalize_name(mapping["away_name"]),
        ]
    )
    identity_hash = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    draw_probs = {cutoff: q_draw(snapshots[cutoff]) for cutoff in cutoffs}
    baseline = clip(draw_probs[15])
    candidates = {
        "DRAW_T15_PLUS_HALF_T90_MOVE": clip(draw_probs[15] + 0.5 * (draw_probs[15] - draw_probs[90])),
        "DRAW_T15_PLUS_HALF_T6H_MOVE": clip(draw_probs[15] + 0.5 * (draw_probs[15] - draw_probs[360])),
        "DRAW_T15_PLUS_HALF_T24H_MOVE": clip(draw_probs[15] + 0.5 * (draw_probs[15] - draw_probs[1440])),
    }
    return MarketRow(identity_hash, path.relative_to(checkout).as_posix(), baseline, candidates)


def reconstruct_eligible(checkout: Path, prereg: dict[str, Any]) -> tuple[list[MarketRow], dict[str, int]]:
    helper = load_helper()
    cfg = load_json(HELPER_CONFIG)
    candidates = discover_candidates(checkout)
    eligible: list[MarketRow] = []
    outside_failures = 0
    for path in candidates:
        try:
            row = blind_market(path, checkout, helper, cfg, prereg)
            if row is not None:
                eligible.append(row)
        except Exception:
            outside_failures += 1
    eligible.sort(key=lambda row: row.identity_hash)
    hashes = [row.identity_hash for row in eligible]
    if len(hashes) != len(set(hashes)):
        raise PilotError("duplicate canonical market identity")
    expected = int(prereg["eligibility_contract"]["expected_eligible_market_count_from_inventory"])
    if len(eligible) != expected:
        raise PilotError(f"eligible count mismatch: {len(eligible)} != {expected}")
    return eligible, {"candidate_files": len(candidates), "outside_eligibility_failures": outside_failures}


def split_rows(rows: list[MarketRow]) -> tuple[list[MarketRow], list[MarketRow]]:
    split = len(rows) // 2
    return rows[:split], rows[split:]


def make_lock(rows: list[MarketRow], counts: dict[str, int], auth_sha: str) -> dict[str, Any]:
    policy, confirmation = split_rows(rows)
    all_hashes = [row.identity_hash for row in rows]
    policy_hashes = [row.identity_hash for row in policy]
    confirmation_hashes = [row.identity_hash for row in confirmation]
    return {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-LOCK-R1",
        "status": "PASS_ELIGIBILITY_AND_SPLIT_LOCKED_BEFORE_LABEL_ACCESS",
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "preregistration_sha256": EXPECTED_PREREG_SHA,
        "authorization_sha256": auth_sha,
        "candidate_files": counts["candidate_files"],
        "outside_eligibility_failures": counts["outside_eligibility_failures"],
        "execution_parse_or_identity_failures": 0,
        "eligible_count": len(rows),
        "policy_count": len(policy),
        "confirmation_count": len(confirmation),
        "eligible_identity_set_sha256": lines_sha(all_hashes),
        "policy_split_sha256": lines_sha(policy_hashes),
        "confirmation_split_sha256": lines_sha(confirmation_hashes),
        "ordered_split_sha256": lines_sha(["POLICY", *policy_hashes, "CONFIRMATION", *confirmation_hashes]),
        "external_raw_data_accessed": True,
        "messages_at_or_after_kickoff_parsed": 0,
        "winner_labels_read": 0,
        "per_market_rows_persisted_or_uploaded": False,
        "model_fits": 0,
        "thresholds_selected": 0,
        "formal_weight": 0,
    }


def verify_lock(lock: dict[str, Any], rows: list[MarketRow], auth_sha: str) -> tuple[list[MarketRow], list[MarketRow]]:
    if lock.get("status") != "PASS_ELIGIBILITY_AND_SPLIT_LOCKED_BEFORE_LABEL_ACCESS":
        raise PilotError("lock status invalid")
    if lock.get("preregistration_sha256") != EXPECTED_PREREG_SHA or lock.get("authorization_sha256") != auth_sha:
        raise PilotError("lock contract hash mismatch")
    policy, confirmation = split_rows(rows)
    expected = make_lock(
        rows,
        {
            "candidate_files": int(lock.get("candidate_files", 0)),
            "outside_eligibility_failures": int(lock.get("outside_eligibility_failures", 0)),
        },
        auth_sha,
    )
    for key in (
        "eligible_count",
        "policy_count",
        "confirmation_count",
        "eligible_identity_set_sha256",
        "policy_split_sha256",
        "confirmation_split_sha256",
        "ordered_split_sha256",
    ):
        if lock.get(key) != expected.get(key):
            raise PilotError(f"lock receipt mismatch: {key}")
    return policy, confirmation


def read_draw_label(path: Path, helper: Any, cfg: dict[str, Any]) -> int | None:
    final_definition = None
    with path.open("r", encoding="utf-8-sig") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            message = json.loads(raw)
            for change in message.get("mc") or []:
                if isinstance(change, dict) and isinstance(change.get("marketDefinition"), dict):
                    final_definition = change["marketDefinition"]
    if not isinstance(final_definition, dict):
        return None
    mapping = helper.runner_map(final_definition, cfg)
    winners = [int(row["id"]) for row in final_definition.get("runners") or [] if row.get("status") == "WINNER"]
    if len(winners) != 1:
        return None
    return int(winners[0] == mapping["draw_id"])


def labelled(rows: list[MarketRow], checkout: Path) -> list[tuple[MarketRow, int]]:
    helper = load_helper()
    cfg = load_json(HELPER_CONFIG)
    result: list[tuple[MarketRow, int]] = []
    for row in rows:
        label = read_draw_label(checkout / row.source_path, helper, cfg)
        if label is not None:
            result.append((row, label))
    return result


def average_precision(y: list[int], scores: list[float], hashes: list[str]) -> float:
    positives = sum(y)
    if positives == 0:
        return 0.0
    order = sorted(range(len(y)), key=lambda index: (-scores[index], hashes[index]))
    hits = 0
    total = 0.0
    for rank, index in enumerate(order, start=1):
        if y[index] == 1:
            hits += 1
            total += hits / rank
    return total / positives


def auc(y: list[int], scores: list[float]) -> float:
    positives = [score for score, label in zip(scores, y) if label == 1]
    negatives = [score for score, label in zip(scores, y) if label == 0]
    if not positives or not negatives:
        return 0.5
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            wins += 1.0 if positive > negative else 0.5 if positive == negative else 0.0
    return wins / (len(positives) * len(negatives))


def metrics(rows: list[tuple[MarketRow, int]], score_id: str) -> dict[str, float]:
    if not rows:
        raise PilotError("cannot score empty labelled set")
    y = [label for _, label in rows]
    hashes = [row.identity_hash for row, _ in rows]
    scores = [row.baseline if score_id == "DRAW_FAIR_T15" else row.candidates[score_id] for row, _ in rows]
    return {
        "average_precision": average_precision(y, scores, hashes),
        "roc_auc": auc(y, scores),
        "brier": statistics.fmean((score - label) ** 2 for score, label in zip(scores, y)),
        "logloss": -statistics.fmean(label * math.log(score) + (1 - label) * math.log(1 - score) for score, label in zip(scores, y)),
    }


def policy_result(rows: list[tuple[MarketRow, int]], lock: dict[str, Any], prereg: dict[str, Any]) -> dict[str, Any]:
    row_count = len(rows)
    draw_count = sum(label for _, label in rows)
    gate = row_count >= int(prereg["sample_gates"]["minimum_policy_rows"]) and draw_count >= int(prereg["sample_gates"]["minimum_policy_draws"])
    base = {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-POLICY-R1",
        "preregistration_sha256": lock["preregistration_sha256"],
        "authorization_sha256": lock["authorization_sha256"],
        "eligible_identity_set_sha256": lock["eligible_identity_set_sha256"],
        "policy_split_sha256": lock["policy_split_sha256"],
        "confirmation_split_sha256": lock["confirmation_split_sha256"],
        "policy_rows": row_count,
        "policy_draws": draw_count,
        "sample_gate_pass": gate,
        "policy_winner_labels_read": row_count,
        "confirmation_winner_labels_read": 0,
        "model_fits": 0,
        "thresholds_selected": 0,
        "per_market_rows_persisted_or_uploaded": False,
        "formal_weight": 0,
    }
    if not gate:
        return {**base, "status": "STOP_NO_RESULT_POLICY_SAMPLE_GATE_FAILED", "selected_candidate": None, "policy_metrics": {}}
    bundles = {"DRAW_FAIR_T15": metrics(rows, "DRAW_FAIR_T15")}
    for candidate_id in CANDIDATE_IDS:
        bundles[candidate_id] = metrics(rows, candidate_id)
    selected = min(
        CANDIDATE_IDS,
        key=lambda candidate_id: (
            -bundles[candidate_id]["average_precision"],
            bundles[candidate_id]["brier"],
            CANDIDATE_IDS.index(candidate_id),
        ),
    )
    return {
        **base,
        "status": "PASS_POLICY_CANDIDATE_FROZEN_BEFORE_CONFIRMATION_LABEL_ACCESS",
        "selected_candidate": selected,
        "policy_metrics": bundles,
    }


def verify_policy(policy: dict[str, Any], lock: dict[str, Any]) -> None:
    for key in (
        "preregistration_sha256",
        "authorization_sha256",
        "eligible_identity_set_sha256",
        "policy_split_sha256",
        "confirmation_split_sha256",
    ):
        if policy.get(key) != lock.get(key):
            raise PilotError(f"policy receipt mismatch: {key}")
    selected = policy.get("selected_candidate")
    if policy.get("sample_gate_pass") and selected not in CANDIDATE_IDS:
        raise PilotError("selected candidate outside frozen catalog")
    if not policy.get("sample_gate_pass") and selected is not None:
        raise PilotError("failed policy gate cannot select candidate")


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def bootstrap_ap_delta(rows: list[tuple[MarketRow, int]], candidate_id: str, repetitions: int, seed: int) -> dict[str, float | int]:
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(repetitions):
        sample = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        deltas.append(metrics(sample, candidate_id)["average_precision"] - metrics(sample, "DRAW_FAIR_T15")["average_precision"])
    return {
        "repetitions": repetitions,
        "seed": seed,
        "p05": percentile(deltas, 0.05),
        "median": percentile(deltas, 0.50),
        "p95": percentile(deltas, 0.95),
    }


def final_result(rows: list[tuple[MarketRow, int]], policy: dict[str, Any], lock: dict[str, Any], prereg: dict[str, Any]) -> dict[str, Any]:
    confirmation_rows = len(rows)
    confirmation_draws = sum(label for _, label in rows)
    total_gate = int(lock["eligible_count"]) >= int(prereg["sample_gates"]["minimum_total_eligible"])
    policy_gate = bool(policy.get("sample_gate_pass"))
    confirmation_gate = (
        confirmation_rows >= int(prereg["sample_gates"]["minimum_confirmation_rows"])
        and confirmation_draws >= int(prereg["sample_gates"]["minimum_confirmation_draws"])
    )
    if not (total_gate and policy_gate and confirmation_gate):
        return {
            "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R1",
            "status": "STOP_NO_RESULT_SAMPLE_GATE_FAILED",
            "selected_candidate": policy.get("selected_candidate"),
            "eligible_count": lock["eligible_count"],
            "policy_rows": policy["policy_rows"],
            "policy_draws": policy["policy_draws"],
            "confirmation_rows": confirmation_rows,
            "confirmation_draws": confirmation_draws,
            "sample_gates": {"total": total_gate, "policy": policy_gate, "confirmation": confirmation_gate},
            "policy_winner_labels_read": policy["policy_winner_labels_read"],
            "confirmation_winner_labels_read": confirmation_rows,
            "model_fits": 0,
            "thresholds_selected": 0,
            "rerun_allowed": False,
            "formal_weight": 0,
        }

    candidate_id = str(policy["selected_candidate"])
    baseline = metrics(rows, "DRAW_FAIR_T15")
    candidate = metrics(rows, candidate_id)
    deltas = {
        "average_precision_candidate_minus_baseline": candidate["average_precision"] - baseline["average_precision"],
        "roc_auc_candidate_minus_baseline": candidate["roc_auc"] - baseline["roc_auc"],
        "brier_candidate_minus_baseline": candidate["brier"] - baseline["brier"],
        "logloss_candidate_minus_baseline": candidate["logloss"] - baseline["logloss"],
    }
    bootstrap_cfg = prereg["confirmation_metrics"]["bootstrap"]
    bootstrap = bootstrap_ap_delta(rows, candidate_id, int(bootstrap_cfg["repetitions"]), int(bootstrap_cfg["seed"]))
    gates = {
        "confirmation_average_precision_delta_strictly_positive": deltas["average_precision_candidate_minus_baseline"] > 0.0,
        "confirmation_average_precision_delta_bootstrap_p05_strictly_positive": float(bootstrap["p05"]) > 0.0,
        "confirmation_roc_auc_delta_nonnegative": deltas["roc_auc_candidate_minus_baseline"] >= 0.0,
        "confirmation_brier_delta_nonpositive": deltas["brier_candidate_minus_baseline"] <= 0.0,
    }
    passed = all(gates.values())
    return {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R1",
        "status": "PASS_TRAJECTORY_PILOT_INCREMENT_JUSTIFIES_LARGER_LICENSED_DATASET_ONLY" if passed else "STOP_TRAJECTORY_PILOT_NO_INCREMENT",
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "preregistration_sha256": lock["preregistration_sha256"],
        "authorization_sha256": lock["authorization_sha256"],
        "eligible_identity_set_sha256": lock["eligible_identity_set_sha256"],
        "ordered_split_sha256": lock["ordered_split_sha256"],
        "selected_candidate": candidate_id,
        "eligible_count": lock["eligible_count"],
        "policy_rows": policy["policy_rows"],
        "policy_draws": policy["policy_draws"],
        "confirmation_rows": confirmation_rows,
        "confirmation_draws": confirmation_draws,
        "confirmation_baseline_metrics": baseline,
        "confirmation_candidate_metrics": candidate,
        "confirmation_deltas": deltas,
        "average_precision_delta_bootstrap": bootstrap,
        "research_pass_gates": gates,
        "research_gate_pass": passed,
        "external_raw_data_accessed": True,
        "policy_winner_labels_read": policy["policy_winner_labels_read"],
        "confirmation_winner_labels_read": confirmation_rows,
        "model_fits": 0,
        "thresholds_selected": 0,
        "rerun_allowed": False,
        "per_market_rows_persisted_or_uploaded": False,
        "basic_ltp_treated_as_executable_price": False,
        "formal_weight": 0,
        "current_match_use_allowed": False,
        "formal_ev_allowed": False,
    }


def run_lock(args: argparse.Namespace) -> None:
    prereg, auth_sha = verify_contracts(args.prereg, args.authorization)
    rows, counts = reconstruct_eligible(args.source_checkout, prereg)
    receipt = make_lock(rows, counts, auth_sha)
    write_json(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def run_policy(args: argparse.Namespace) -> None:
    prereg, auth_sha = verify_contracts(args.prereg, args.authorization)
    rows, _ = reconstruct_eligible(args.source_checkout, prereg)
    lock = load_json(args.lock_receipt)
    policy_rows, _ = verify_lock(lock, rows, auth_sha)
    receipt = policy_result(labelled(policy_rows, args.source_checkout), lock, prereg)
    write_json(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def run_confirm(args: argparse.Namespace) -> None:
    prereg, auth_sha = verify_contracts(args.prereg, args.authorization)
    rows, _ = reconstruct_eligible(args.source_checkout, prereg)
    lock = load_json(args.lock_receipt)
    _, confirmation_rows = verify_lock(lock, rows, auth_sha)
    policy = load_json(args.policy_receipt)
    verify_policy(policy, lock)
    labelled_confirmation = labelled(confirmation_rows, args.source_checkout) if policy.get("sample_gate_pass") else []
    receipt = final_result(labelled_confirmation, policy, lock, prereg)
    write_json(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["lock", "policy", "confirm"])
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lock-receipt", type=Path)
    parser.add_argument("--policy-receipt", type=Path)
    args = parser.parse_args()
    if args.mode == "lock":
        run_lock(args)
    elif args.mode == "policy":
        if args.lock_receipt is None:
            raise PilotError("policy mode requires --lock-receipt")
        run_policy(args)
    else:
        if args.lock_receipt is None or args.policy_receipt is None:
            raise PilotError("confirm mode requires --lock-receipt and --policy-receipt")
        run_confirm(args)


if __name__ == "__main__":
    main()
