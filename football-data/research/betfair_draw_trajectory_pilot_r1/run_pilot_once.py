#!/usr/bin/env python3
"""One-time, preregistered Betfair draw-trajectory pilot.

The script has three deliberately separated modes:

1. ``lock`` reconstructs eligible pre-match trajectories without parsing any
   message at or after kickoff and persists only aggregate identity hashes.
2. ``policy`` verifies the lock, reads labels only for the frozen policy half,
   and persists one selected fixed candidate.
3. ``confirm`` verifies both prior receipts, reads labels only for the frozen
   confirmation half, and performs the single preregistered confirmation test.

No raw or per-market derived rows are written to artifacts.
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
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
PARSER_PATH = ROOT / "research" / "betfair_basic_trajectory_r1" / "ingest_betfair_basic_trajectory_r1.py"
PARSER_CONFIG = ROOT / "research" / "betfair_basic_trajectory_r1" / "preregistration.json"
SOURCE_ROOT_RELATIVE = Path("data/dados_historicos/por_evento_id")
EXPECTED_PREREG_SHA256 = "b22b5b9c2eab4053c16e93bf82feb0036bde994b41e6e2fecdafa13212141c3b"
EXPECTED_SOURCE_COMMIT = "90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff"
EXPECTED_PREREG_HEAD = "34f8ffdb9d1de39cc62e52b7e0886823c99224e4"
PT_RE = re.compile(r'"pt"\s*:\s*(\d+)')


class PilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketRow:
    identity_hash: str
    canonical_identity: str
    source_path: str
    market_id: str
    event_id: str
    market_time_utc: str
    baseline: float
    candidates: dict[str, float]


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_lines(values: Iterable[str]) -> str:
    payload = "".join(f"{value}\n" for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PilotError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_parser_module():
    spec = importlib.util.spec_from_file_location("betfair_pilot_parser_helpers", PARSER_PATH)
    if spec is None or spec.loader is None:
        raise PilotError("cannot load frozen Betfair parser helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalize_name(value: str) -> str:
    return " ".join(str(value).casefold().strip().split())


def no_vig_draw_probability(home: float, draw: float, away: float) -> float:
    inv = [1.0 / home, 1.0 / draw, 1.0 / away]
    return inv[1] / sum(inv)


def clip_probability(value: float) -> float:
    return min(0.999999, max(0.000001, float(value)))


def verify_contracts(prereg_path: Path, authorization_path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    prereg = load_json(prereg_path)
    prereg_sha = canonical_json_sha256(prereg)
    if prereg_sha != EXPECTED_PREREG_SHA256:
        raise PilotError(f"preregistration hash mismatch: {prereg_sha}")
    if prereg.get("status") != "PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN":
        raise PilotError("frozen preregistration status changed")
    if prereg["upstream_evidence"].get("source_commit") != EXPECTED_SOURCE_COMMIT:
        raise PilotError("source commit changed")
    if prereg["upstream_evidence"].get("complete_core_trajectory_markets") != 65:
        raise PilotError("expected eligible count changed")
    if prereg["probability_contract"].get("model_fit_allowed") is not False:
        raise PilotError("model fitting was enabled")

    authorization = load_json(authorization_path)
    expected = {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-RUN-AUTH-R1",
        "authorization_status": "AUTHORIZED_ONE_TIME_RUN",
        "authorized_user_message": "开始吧",
        "authorized_at_local": "2026-08-06T17:52:00+08:00",
        "authorized_at_utc": "2026-08-06T09:52:00Z",
        "preregistration_head": EXPECTED_PREREG_HEAD,
        "preregistration_sha256": EXPECTED_PREREG_SHA256,
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
    return prereg, authorization, canonical_json_sha256(authorization)


def discover_candidates(source_checkout: Path) -> list[Path]:
    root = source_checkout / SOURCE_ROOT_RELATIVE
    if not root.is_dir():
        raise PilotError(f"source root missing: {root}")
    required = [b'"marketType":"MATCH_ODDS"', b'"eventTypeId":"1"', b'"The Draw"']
    candidates: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if all(token in data for token in required):
            candidates.append(path)
    return candidates


def blind_market(path: Path, source_checkout: Path, helper: Any, parser_cfg: dict[str, Any], prereg: dict[str, Any]) -> MarketRow | None:
    cutoffs = [int(value) for value in prereg["eligibility_contract"]["required_cutoffs_minutes_before_kickoff"]]
    definition: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None
    market_id: str | None = None
    market_time = None
    ltp: dict[int, float] = {}
    snapshots: dict[int, tuple[float, float, float]] = {}
    previous_pt_ms: int | None = None

    with path.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            match = PT_RE.search(raw_line)
            if match is None:
                raise PilotError(f"message without publish time: {path}")
            pt_ms = int(match.group(1))
            if previous_pt_ms is not None and pt_ms < previous_pt_ms:
                raise PilotError(f"non-monotonic stream: {path}")
            previous_pt_ms = pt_ms
            pt = helper.epoch_ms(pt_ms)

            # This is the label-blind boundary: once kickoff is known, messages
            # at or after kickoff are never parsed as JSON, so settlement status
            # cannot enter memory in lock or eligibility reconstruction.
            if market_time is not None and pt >= market_time:
                break

            message = json.loads(raw_line)
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
                    market_time = helper.parse_iso(str(definition["marketTime"]))
                    if pt >= market_time:
                        raise PilotError("first usable market definition occurs at or after kickoff")
                    mapping = helper.runner_map(definition, parser_cfg)
                for row in change.get("rc") or []:
                    if not isinstance(row, dict) or row.get("id") is None or "ltp" not in row:
                        continue
                    try:
                        value = float(row["ltp"])
                    except (TypeError, ValueError):
                        continue
                    if value >= float(parser_cfg["market_contract"]["ltp_minimum"]):
                        ltp[int(row["id"])] = value

            if definition is None or mapping is None or market_time is None or market_id is None:
                continue
            if definition.get("inPlay") is True:
                break
            runner_ids = [mapping["home_id"], mapping["draw_id"], mapping["away_id"]]
            if not all(runner_id in ltp for runner_id in runner_ids):
                continue
            current = (ltp[runner_ids[0]], ltp[runner_ids[1]], ltp[runner_ids[2]])
            for cutoff in cutoffs:
                target = market_time - helper.timedelta(minutes=cutoff)
                if pt <= target:
                    snapshots[cutoff] = current

    if definition is None or mapping is None or market_time is None or market_id is None:
        return None
    if any(cutoff not in snapshots for cutoff in cutoffs):
        return None
    runners = definition.get("runners") or []
    if len(runners) != 3:
        return None
    event_name = str(definition.get("eventName") or "")
    home_name = str(mapping["home_name"])
    away_name = str(mapping["away_name"])
    event_id = str(definition.get("eventId") or "")
    if not event_id or not event_name:
        raise PilotError(f"missing event identity: {path}")
    canonical_identity = "|".join(
        [
            market_id,
            event_id,
            helper.iso(market_time),
            normalize_name(home_name),
            normalize_name(away_name),
        ]
    )
    identity_hash = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    qd = {cutoff: no_vig_draw_probability(*snapshots[cutoff]) for cutoff in cutoffs}
    baseline = clip_probability(qd[15])
    candidates = {
        "DRAW_T15_PLUS_HALF_T90_MOVE": clip_probability(qd[15] + 0.5 * (qd[15] - qd[90])),
        "DRAW_T15_PLUS_HALF_T6H_MOVE": clip_probability(qd[15] + 0.5 * (qd[15] - qd[360])),
        "DRAW_T15_PLUS_HALF_T24H_MOVE": clip_probability(qd[15] + 0.5 * (qd[15] - qd[1440])),
    }
    return MarketRow(
        identity_hash=identity_hash,
        canonical_identity=canonical_identity,
        source_path=path.relative_to(source_checkout).as_posix(),
        market_id=market_id,
        event_id=event_id,
        market_time_utc=helper.iso(market_time),
        baseline=baseline,
        candidates=candidates,
    )


def reconstruct_eligible(source_checkout: Path, prereg: dict[str, Any]) -> tuple[list[MarketRow], dict[str, int]]:
    helper = load_parser_module()
    # The frozen helper module exposes datetime.timedelta as ``timedelta``.
    if not hasattr(helper, "timedelta"):
        from datetime import timedelta

        helper.timedelta = timedelta
    parser_cfg = load_json(PARSER_CONFIG)
    candidates = discover_candidates(source_checkout)
    eligible: list[MarketRow] = []
    blind_failures = 0
    for path in candidates:
        try:
            row = blind_market(path, source_checkout, helper, parser_cfg, prereg)
            if row is not None:
                eligible.append(row)
        except Exception:
            blind_failures += 1
    eligible.sort(key=lambda row: row.identity_hash)
    hashes = [row.identity_hash for row in eligible]
    if len(hashes) != len(set(hashes)):
        raise PilotError("duplicate canonical market identity")
    expected = int(prereg["eligibility_contract"]["expected_eligible_market_count_from_inventory"])
    if len(eligible) != expected:
        raise PilotError(f"eligible count mismatch: {len(eligible)} != {expected}")
    return eligible, {"candidate_files": len(candidates), "blind_parse_failures": blind_failures}


def split_rows(rows: list[MarketRow], prereg: dict[str, Any]) -> tuple[list[MarketRow], list[MarketRow]]:
    policy_count = len(rows) // 2
    return rows[:policy_count], rows[policy_count:]


def lock_receipt(rows: list[MarketRow], counts: dict[str, int], prereg_sha: str, authorization_sha: str) -> dict[str, Any]:
    policy, confirmation = split_rows(rows, {})
    all_hashes = [row.identity_hash for row in rows]
    policy_hashes = [row.identity_hash for row in policy]
    confirmation_hashes = [row.identity_hash for row in confirmation]
    return {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-LOCK-R1",
        "status": "PASS_ELIGIBILITY_AND_SPLIT_LOCKED_BEFORE_LABEL_ACCESS",
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "preregistration_sha256": prereg_sha,
        "authorization_sha256": authorization_sha,
        "candidate_files": counts["candidate_files"],
        "blind_parse_failures_outside_eligible_set": counts["blind_parse_failures"],
        "eligible_count": len(rows),
        "policy_count": len(policy),
        "confirmation_count": len(confirmation),
        "eligible_identity_set_sha256": sha256_lines(all_hashes),
        "policy_split_sha256": sha256_lines(policy_hashes),
        "confirmation_split_sha256": sha256_lines(confirmation_hashes),
        "ordered_split_sha256": sha256_lines(["POLICY", *policy_hashes, "CONFIRMATION", *confirmation_hashes]),
        "external_raw_data_accessed": True,
        "messages_at_or_after_kickoff_parsed": 0,
        "winner_labels_read": 0,
        "per_market_rows_persisted_or_uploaded": False,
        "model_fits": 0,
        "thresholds_selected": 0,
        "formal_weight": 0,
    }


def verify_lock(receipt: dict[str, Any], rows: list[MarketRow], prereg_sha: str, authorization_sha: str) -> tuple[list[MarketRow], list[MarketRow]]:
    if receipt.get("status") != "PASS_ELIGIBILITY_AND_SPLIT_LOCKED_BEFORE_LABEL_ACCESS":
        raise PilotError("lock status invalid")
    if receipt.get("preregistration_sha256") != prereg_sha or receipt.get("authorization_sha256") != authorization_sha:
        raise PilotError("lock contract hash mismatch")
    policy, confirmation = split_rows(rows, {})
    expected = lock_receipt(rows, {"candidate_files": receipt.get("candidate_files", 0), "blind_parse_failures": receipt.get("blind_parse_failures_outside_eligible_set", 0)}, prereg_sha, authorization_sha)
    for key in (
        "eligible_count",
        "policy_count",
        "confirmation_count",
        "eligible_identity_set_sha256",
        "policy_split_sha256",
        "confirmation_split_sha256",
        "ordered_split_sha256",
    ):
        if receipt.get(key) != expected.get(key):
            raise PilotError(f"lock receipt mismatch: {key}")
    return policy, confirmation


def read_outcome(path: Path, helper: Any, parser_cfg: dict[str, Any]) -> int | None:
    last_definition = None
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            for change in message.get("mc") or []:
                if isinstance(change, dict) and isinstance(change.get("marketDefinition"), dict):
                    last_definition = change["marketDefinition"]
    if not isinstance(last_definition, dict):
        return None
    mapping = helper.runner_map(last_definition, parser_cfg)
    winners = [int(row["id"]) for row in last_definition.get("runners") or [] if row.get("status") == "WINNER"]
    if len(winners) != 1:
        return None
    return 1 if winners[0] == mapping["draw_id"] else 0


def labelled_rows(rows: list[MarketRow], source_checkout: Path) -> list[tuple[MarketRow, int]]:
    helper = load_parser_module()
    parser_cfg = load_json(PARSER_CONFIG)
    labelled: list[tuple[MarketRow, int]] = []
    for row in rows:
        outcome = read_outcome(source_checkout / row.source_path, helper, parser_cfg)
        if outcome is not None:
            labelled.append((row, outcome))
    return labelled


def average_precision(y: list[int], scores: list[float], identity_hashes: list[str]) -> float:
    positives = sum(y)
    if positives == 0:
        return 0.0
    order = sorted(range(len(y)), key=lambda i: (-scores[i], identity_hashes[i]))
    hits = 0
    total = 0.0
    for rank, index in enumerate(order, start=1):
        if y[index] == 1:
            hits += 1
            total += hits / rank
    return total / positives


def roc_auc(y: list[int], scores: list[float]) -> float:
    positives = [scores[i] for i, value in enumerate(y) if value == 1]
    negatives = [scores[i] for i, value in enumerate(y) if value == 0]
    if not positives or not negatives:
        return 0.5
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            wins += 1.0 if positive > negative else 0.5 if positive == negative else 0.0
    return wins / (len(positives) * len(negatives))


def metric_bundle(labelled: list[tuple[MarketRow, int]], score_id: str) -> dict[str, float]:
    y = [label for _, label in labelled]
    hashes = [row.identity_hash for row, _ in labelled]
    if score_id == "DRAW_FAIR_T15":
        scores = [row.baseline for row, _ in labelled]
    else:
        scores = [row.candidates[score_id] for row, _ in labelled]
    brier = statistics.fmean((score - label) ** 2 for score, label in zip(scores, y))
    logloss = -statistics.fmean(label * math.log(score) + (1 - label) * math.log(1 - score) for score, label in zip(scores, y))
    return {
        "average_precision": average_precision(y, scores, hashes),
        "roc_auc": roc_auc(y, scores),
        "brier": brier,
        "logloss": logloss,
    }


def select_policy_candidate(labelled: list[tuple[MarketRow, int]], prereg: dict[str, Any]) -> tuple[str, dict[str, dict[str, float]]]:
    candidate_ids = [row["id"] for row in prereg["probability_contract"]["fixed_candidates"]]
    metrics = {"DRAW_FAIR_T15": metric_bundle(labelled, "DRAW_FAIR_T15")}
    for candidate_id in candidate_ids:
        metrics[candidate_id] = metric_bundle(labelled, candidate_id)
    selected = min(
        candidate_ids,
        key=lambda candidate_id: (
            -metrics[candidate_id]["average_precision"],
            metrics[candidate_id]["brier"],
            candidate_ids.index(candidate_id),
        ),
    )
    return selected, metrics


def policy_receipt(
    labelled: list[tuple[MarketRow, int]],
    selected: str,
    metrics: dict[str, dict[str, float]],
    lock: dict[str, Any],
    prereg: dict[str, Any],
) -> dict[str, Any]:
    rows = len(labelled)
    draws = sum(label for _, label in labelled)
    gate = rows >= int(prereg["sample_gates"]["minimum_policy_rows"]) and draws >= int(prereg["sample_gates"]["minimum_policy_draws"])
    return {
        "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-POLICY-R1",
        "status": "PASS_POLICY_CANDIDATE_FROZEN_BEFORE_CONFIRMATION_LABEL_ACCESS" if gate else "STOP_NO_RESULT_POLICY_SAMPLE_GATE_FAILED",
        "preregistration_sha256": lock["preregistration_sha256"],
        "authorization_sha256": lock["authorization_sha256"],
        "eligible_identity_set_sha256": lock["eligible_identity_set_sha256"],
        "policy_split_sha256": lock["policy_split_sha256"],
        "confirmation_split_sha256": lock["confirmation_split_sha256"],
        "policy_rows": rows,
        "policy_draws": draws,
        "selected_candidate": selected,
        "policy_metrics": metrics,
        "sample_gate_pass": gate,
        "policy_winner_labels_read": rows,
        "confirmation_winner_labels_read": 0,
        "model_fits": 0,
        "thresholds_selected": 0,
        "per_market_rows_persisted_or_uploaded": False,
        "formal_weight": 0,
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
    if policy.get("selected_candidate") not in {
        "DRAW_T15_PLUS_HALF_T90_MOVE",
        "DRAW_T15_PLUS_HALF_T6H_MOVE",
        "DRAW_T15_PLUS_HALF_T24H_MOVE",
    }:
        raise PilotError("selected candidate outside frozen catalog")


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise PilotError("empty percentile input")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def bootstrap_ap_delta(labelled: list[tuple[MarketRow, int]], candidate_id: str, repetitions: int, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    n = len(labelled)
    deltas: list[float] = []
    for _ in range(repetitions):
        sample = [labelled[rng.randrange(n)] for _ in range(n)]
        baseline = metric_bundle(sample, "DRAW_FAIR_T15")["average_precision"]
        candidate = metric_bundle(sample, candidate_id)["average_precision"]
        deltas.append(candidate - baseline)
    return {
        "repetitions": repetitions,
        "seed": seed,
        "p05": percentile(deltas, 0.05),
        "median": percentile(deltas, 0.50),
        "p95": percentile(deltas, 0.95),
    }


def confirmation_receipt(
    confirmation_labelled: list[tuple[MarketRow, int]],
    policy: dict[str, Any],
    lock: dict[str, Any],
    prereg: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = str(policy["selected_candidate"])
    confirmation_rows = len(confirmation_labelled)
    confirmation_draws = sum(label for _, label in confirmation_labelled)
    total_gate = int(lock["eligible_count"]) >= int(prereg["sample_gates"]["minimum_total_eligible"])
    confirmation_gate = (
        confirmation_rows >= int(prereg["sample_gates"]["minimum_confirmation_rows"])
        and confirmation_draws >= int(prereg["sample_gates"]["minimum_confirmation_draws"])
    )
    policy_gate = bool(policy.get("sample_gate_pass"))
    if not (total_gate and confirmation_gate and policy_gate):
        return {
            "schema_version": "BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R1",
            "status": "STOP_NO_RESULT_SAMPLE_GATE_FAILED",
            "selected_candidate": candidate_id,
            "eligible_count": lock["eligible_count"],
            "policy_rows": policy["policy_rows"],
            "policy_draws": policy["policy_draws"],
            "confirmation_rows": confirmation_rows,
            "confirmation_draws": confirmation_draws,
            "sample_gates": {"total": total_gate, "policy": policy_gate, "confirmation": confirmation_gate},
            "confirmation_winner_labels_read": confirmation_rows,
            "model_fits": 0,
            "thresholds_selected": 0,
            "formal_weight": 0,
        }

    baseline = metric_bundle(confirmation_labelled, "DRAW_FAIR_T15")
    candidate = metric_bundle(confirmation_labelled, candidate_id)
    deltas = {
        "average_precision_candidate_minus_baseline": candidate["average_precision"] - baseline["average_precision"],
        "roc_auc_candidate_minus_baseline": candidate["roc_auc"] - baseline["roc_auc"],
        "brier_candidate_minus_baseline": candidate["brier"] - baseline["brier"],
        "logloss_candidate_minus_baseline": candidate["logloss"] - baseline["logloss"],
    }
    bootstrap_cfg = prereg["confirmation_metrics"]["bootstrap"]
    bootstrap = bootstrap_ap_delta(
        confirmation_labelled,
        candidate_id,
        int(bootstrap_cfg["repetitions"]),
        int(bootstrap_cfg["seed"]),
    )
    gates = {
        "confirmation_average_precision_delta_strictly_positive": deltas["average_precision_candidate_minus_baseline"] > 0.0,
        "confirmation_average_precision_delta_bootstrap_p05_strictly_positive": bootstrap["p05"] > 0.0,
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
    prereg, _, authorization_sha = verify_contracts(args.prereg, args.authorization)
    rows, counts = reconstruct_eligible(args.source_checkout, prereg)
    receipt = lock_receipt(rows, counts, EXPECTED_PREREG_SHA256, authorization_sha)
    write_json(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def run_policy(args: argparse.Namespace) -> None:
    prereg, _, authorization_sha = verify_contracts(args.prereg, args.authorization)
    rows, _ = reconstruct_eligible(args.source_checkout, prereg)
    lock = load_json(args.lock_receipt)
    policy_rows, _ = verify_lock(lock, rows, EXPECTED_PREREG_SHA256, authorization_sha)
    labelled = labelled_rows(policy_rows, args.source_checkout)
    selected, metrics = select_policy_candidate(labelled, prereg)
    receipt = policy_receipt(labelled, selected, metrics, lock, prereg)
    write_json(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def run_confirm(args: argparse.Namespace) -> None:
    prereg, _, authorization_sha = verify_contracts(args.prereg, args.authorization)
    rows, _ = reconstruct_eligible(args.source_checkout, prereg)
    lock = load_json(args.lock_receipt)
    _, confirmation_rows = verify_lock(lock, rows, EXPECTED_PREREG_SHA256, authorization_sha)
    policy = load_json(args.policy_receipt)
    verify_policy(policy, lock)
    if not policy.get("sample_gate_pass"):
        labelled: list[tuple[MarketRow, int]] = []
    else:
        labelled = labelled_rows(confirmation_rows, args.source_checkout)
    receipt = confirmation_receipt(labelled, policy, lock, prereg)
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
