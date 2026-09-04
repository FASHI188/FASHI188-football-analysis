from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STAGE6_B = ROOT / "football-data/research/stage6_pre_b_deep_ppda"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(STAGE6_B) not in sys.path:
    sys.path.insert(0, str(STAGE6_B))

import bootstrap_stage6_pre_b_state as bootstrap
import common as stage6_common

QUEUE_SHA = "6cfcaba8e2f82af0996a404eb3fc5bb477174aebd09c9b10c7434d95e59c8dfc"
CUTOFF = datetime.fromisoformat("2026-09-04T11:00:00+00:00")
FIRST_KICKOFF = datetime.fromisoformat("2026-09-04T17:00:00+00:00")
REQUIRED_N = 1335
V31_HEAD = "7689ec0726fbed6fd2474e1a237b49ab7ad768c7"
V311_HEAD = "a90762a97515f3edd564e8ad204db0d0d4231494"
USR1_HEAD = "485807562f0d1c859ba13355dc671e980de5eb9e"
LEGACY_HEAD = "c5366a405804176130247dfc3d655c6218ce2563"
MAIN_CONTRACT_BLOB = "4769b3e21c459b457acb685ad5933157f2647cb0"
CUTOFF_PACKAGE_SHA = "b4713b483117a455db06ce25362d6993f5d8242a2ea035577e1fbeda3a6e41ae"
FORMAL_STATE_SHA = "19c872d8b38369348437656c9be21961a3fbcdc5fbc7466179bf7d42ff13a0ae"
PROCESS_STATE_SHA = "913b5702e5065517a05d9a76e66f093d453a7bbf40fcb2ee7bd1c36736c1273f"
B_STATE_SHA = "36ce77ae50de7bfaeb6ecfc233827edcc4c52702ff3c095cabd85e08b291c330"
COMP_TO_LEAGUE = {
    "EPL": "EPL",
    "La_liga": "La liga",
    "Bundesliga": "Bundesliga",
    "Serie_A": "Serie A",
    "Ligue_1": "Ligue 1",
}


class EnrollmentError(RuntimeError):
    pass


def canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha_obj(obj: Any) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def load_file_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise EnrollmentError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_git_module(name: str, head: str, repo_path: str):
    raw = subprocess.check_output(["git", "show", f"{head}:{repo_path}"])
    path = pathlib.Path("/tmp") / f"{name}.py"
    path.write_bytes(raw)
    return load_file_module(name, path), sha_bytes(raw)


def load_git_json(head: str, repo_path: str) -> tuple[dict, str]:
    raw = subprocess.check_output(["git", "show", f"{head}:{repo_path}"])
    return json.loads(raw), sha_bytes(raw)


def b_process_scores(b_pack: dict, league: str) -> dict[str, float]:
    league_states = b_pack["leagues"].get(league)
    if not isinstance(league_states, dict) or not league_states:
        return {}
    vals = [(team, row) for team, row in league_states.items() if int(row.get("n", 0)) >= 1]
    if not vals:
        return {}
    md = sum(float(row["deep"]) for _, row in vals) / len(vals)
    mp = sum(float(row["press"]) for _, row in vals) / len(vals)
    sd = math.sqrt(sum((float(row["deep"]) - md) ** 2 for _, row in vals) / len(vals))
    sp = math.sqrt(sum((float(row["press"]) - mp) ** 2 for _, row in vals) / len(vals))
    if sd <= 1e-9 or sp <= 1e-9:
        return {}
    return {
        team: 0.5 * ((float(row["deep"]) - md) / sd) + 0.5 * ((float(row["press"]) - mp) / sp)
        for team, row in vals
    }


def b_predict(base: list[float], home_score: float | None, away_score: float | None, coef: float = 0.10):
    p = [float(x) for x in base]
    if home_score is None or away_score is None:
        return p, False, None
    d = p[1]
    denom = p[0] + p[2]
    if denom <= 0:
        return p, False, None
    hside = min(max(p[0] / denom, 1e-9), 1.0 - 1e-9)
    edge = float(home_score) - float(away_score)
    clipped = max(-3.0, min(3.0, edge))
    z = math.log(hside / (1.0 - hside)) + float(coef) * clipped
    z = max(-40.0, min(40.0, z))
    q = 1.0 / (1.0 + math.exp(-z))
    out = [(1.0 - d) * q, d, (1.0 - d) * (1.0 - q)]
    if abs(sum(out) - 1.0) > 1e-12 or min(out) <= 0.0 or max(out) >= 1.0:
        raise EnrollmentError("B probability invalid")
    return out, True, edge


def row_identity_from_queue(q: dict) -> dict:
    return {
        "competition": q["competition"],
        "season": q["season"],
        "home_team": q["home_team"],
        "away_team": q["away_team"],
        "scheduled_kickoff_utc": q["scheduled_kickoff_utc"],
    }


def queue_digest(fixtures: list[dict]) -> str:
    ids = [str(x["fixture_identity_sha256"]) for x in fixtures]
    return hashlib.sha256(("\n".join(ids) + "\n").encode("utf-8")).hexdigest()


def atomic_groups(rows: list[tuple[dict, dict]]):
    out = []
    cur = []
    key = None
    for q, r in rows:
        k = r["kickoff"]
        if key is None or k == key:
            cur.append((q, r))
            key = k
        else:
            out.append(cur)
            cur = [(q, r)]
            key = k
    if cur:
        out.append(cur)
    return out


def verify_contract(contract: dict) -> None:
    if contract.get("status") != "FROZEN_BEFORE_RECEIPT_GENERATION":
        raise EnrollmentError("enrollment contract status drift")
    if contract["queue"]["required_n"] != REQUIRED_N or contract["queue"]["locked_n"] != REQUIRED_N:
        raise EnrollmentError("required_n drift")
    if contract["queue"]["ordered_queue_identity_sha256"] != QUEUE_SHA:
        raise EnrollmentError("queue SHA drift")
    if contract["cutoff_state"]["package_sha256"] != CUTOFF_PACKAGE_SHA:
        raise EnrollmentError("cutoff package SHA drift")
    if contract["state_use_rule"]["target_result_or_xg_updates_during_enrollment"] != "FORBIDDEN":
        raise EnrollmentError("target update guard drift")
    if contract["state_use_rule"]["target_match_ajax_pages"] != "FORBIDDEN":
        raise EnrollmentError("target match page guard drift")


def load_locked_queue(queue_path: pathlib.Path) -> list[dict]:
    q = json.loads(queue_path.read_text(encoding="utf-8"))
    fixtures = q.get("fixtures")
    if not isinstance(fixtures, list) or len(fixtures) != REQUIRED_N:
        raise EnrollmentError(f"queue count drift {0 if not isinstance(fixtures,list) else len(fixtures)}")
    if q.get("required_n") != REQUIRED_N or q.get("target_labels_opened") is not False:
        raise EnrollmentError("queue metadata drift")
    if q.get("ordered_queue_identity_sha256") != QUEUE_SHA or queue_digest(fixtures) != QUEUE_SHA:
        raise EnrollmentError("ordered queue digest drift")
    for row in fixtures:
        if sha_obj(row_identity_from_queue(row)) != row["fixture_identity_sha256"]:
            raise EnrollmentError("fixture canonical identity hash drift")
    return fixtures


def map_queue_to_future(fixtures: list[dict], future: list[dict]) -> list[tuple[dict, dict]]:
    by_mid = {int(r["mid"]): r for r in future}
    if len(by_mid) != len(future):
        raise EnrollmentError("future match id collision")
    out = []
    for q in fixtures:
        mid = int(q["understat_match_id"])
        r = by_mid.get(mid)
        if r is None:
            raise EnrollmentError(f"locked fixture absent from current future discovery {mid}")
        league = COMP_TO_LEAGUE.get(str(q["competition"]))
        if league != r["league"]:
            raise EnrollmentError(f"league identity drift {mid}")
        if str(q["home_team"]) != str(r["home_team"]) or str(q["away_team"]) != str(r["away_team"]):
            raise EnrollmentError(f"team identity drift {mid}")
        if parse_iso(q["scheduled_kickoff_utc"]) != r["kickoff"]:
            raise EnrollmentError(f"locked kickoff drift {mid}")
        out.append((q, r))
    out.sort(key=lambda x: (x[1]["kickoff"], x[1]["mid"]))
    if [x[0]["fixture_identity_sha256"] for x in out] != [x["fixture_identity_sha256"] for x in fixtures]:
        raise EnrollmentError("queue order drift after future mapping")
    return out


def build_model_and_modules():
    usr, usr_sha = load_git_module(
        "enroll_usr1",
        USR1_HEAD,
        "football-data/research/historical_fusion_v3_upset_safe/historical_fusion_v3_upset_safe.py",
    )
    v31, v31_sha = load_git_module(
        "enroll_v31",
        V31_HEAD,
        "football-data/research/historical_fusion_v3_1/historical_fusion_v3_1.py",
    )
    joint = load_file_module(
        "enroll_v311_joint",
        ROOT / "football-data/research/historical_fusion_v3_1_1_joint_score/historical_fusion_v3_1_1_joint_score.py",
    )
    frozen, frozen_sha = load_git_json(
        LEGACY_HEAD,
        "football-data/research/v3_1_1_prospective_confirmation/FROZEN_CANDIDATE_STATE.json",
    )
    if frozen.get("candidate_head") != V311_HEAD or frozen.get("candidate_state_mutation_allowed") is not False:
        raise EnrollmentError("frozen V3.1.1 state drift")
    p = frozen["payload"]
    model = usr.Model(
        list(p["means"]),
        list(p["sds"]),
        list(p["active_columns"]),
        list(p["beta"]),
        float(p["lambda"]),
    )
    return usr, v31, joint, model, frozen, {
        "usr1_source_sha256": usr_sha,
        "v31_source_sha256": v31_sha,
        "frozen_candidate_state_sha256": frozen_sha,
    }


def reconstruct_cutoff_state(old_db: pathlib.Path, xg_identity: pathlib.Path, workers: int):
    source_mod, legacy, legacy_code = bootstrap.load_legacy_modules()
    priors, proc_states, proc_queue, existing_ids, _ = legacy.load_process_base(old_db)
    formal_state, formal_pending, formal_labels, _ = legacy.replay_formal_base(old_db, xg_identity)
    bridge, future, league_provenance = legacy.fetch_bridge_and_future(existing_ids)
    if any(r["kickoff"] >= CUTOFF for r in bridge):
        raise EnrollmentError("bridge crossed target cutoff")
    shot_stats, shot_transport = bootstrap.safe_fetch_bridge_process(legacy, bridge, workers)
    legacy.replay_bridge(formal_state, formal_pending, formal_labels, proc_states, proc_queue, bridge, shot_stats)
    formal_pack = legacy.serialize_formal(formal_state)
    process_pack = legacy.serialize_process(priors, proc_states)
    b_pack, b_receipt = bootstrap.bootstrap_b_state(old_db, source_mod)
    if bootstrap.sha_obj(formal_pack) != FORMAL_STATE_SHA:
        raise EnrollmentError("formal cutoff state SHA mismatch")
    if bootstrap.sha_obj(process_pack) != PROCESS_STATE_SHA:
        raise EnrollmentError("process cutoff state SHA mismatch")
    if bootstrap.sha_obj(b_pack) != B_STATE_SHA:
        raise EnrollmentError("B cutoff state SHA mismatch")
    return legacy, formal_state, process_pack, b_pack, future, league_provenance, shot_transport, b_receipt, legacy_code


def verify_sealed_cutoff(path: pathlib.Path) -> dict:
    p = json.loads(path.read_text(encoding="utf-8"))
    if p.get("package_sha256") != CUTOFF_PACKAGE_SHA:
        raise EnrollmentError("sealed cutoff package SHA mismatch")
    if p.get("as_of_utc") != "2026-09-04T11:00:00Z" or p.get("required_n") != REQUIRED_N:
        raise EnrollmentError("sealed cutoff identity drift")
    if p.get("v311_formal_state_sha256") != FORMAL_STATE_SHA:
        raise EnrollmentError("sealed formal state SHA drift")
    if p.get("v311_process_state_sha256") != PROCESS_STATE_SHA:
        raise EnrollmentError("sealed process state SHA drift")
    if p.get("b_deep_ppda_state_sha256") != B_STATE_SHA:
        raise EnrollmentError("sealed B state SHA drift")
    if p.get("target_labels_read_for_scoring") is not False or int(p.get("target_match_pages_fetched", -1)) != 0:
        raise EnrollmentError("sealed zero-label boundary drift")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", type=pathlib.Path, required=True)
    ap.add_argument("--queue", type=pathlib.Path, required=True)
    ap.add_argument("--cutoff-state", type=pathlib.Path, required=True)
    ap.add_argument("--old-db", type=pathlib.Path, required=True)
    ap.add_argument("--xg-identity", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if not 1 <= a.workers <= 8:
        raise SystemExit("workers must be 1..8")

    start = utc_now()
    if start >= FIRST_KICKOFF:
        raise EnrollmentError("STOP_MISSED_PREKICKOFF_RECEIPT_NO_BACKFILL")

    contract = json.loads(a.contract.read_text(encoding="utf-8"))
    verify_contract(contract)
    fixtures = load_locked_queue(a.queue)
    sealed_cutoff = verify_sealed_cutoff(a.cutoff_state)

    legacy, formal_state, process_pack, b_pack, future, league_provenance, shot_transport, b_state_receipt, legacy_code = reconstruct_cutoff_state(
        a.old_db, a.xg_identity, a.workers
    )
    mapped = map_queue_to_future(fixtures, future)
    if len(mapped) != REQUIRED_N:
        raise EnrollmentError("mapped queue count drift")

    usr, v31, joint, model, frozen_v311, engine_receipt = build_model_and_modules()
    b_scores = {league: b_process_scores(b_pack, league) for league in COMP_TO_LEAGUE.values()}

    main_contract_bytes = (ROOT / "football-data/research/stage6_pre_b_prospective_confirmation/STAGE6_PRE_B_PROSPECTIVE_CONFIRMATION_CONTRACT.json").read_bytes()
    if subprocess.check_output([
        "git", "hash-object", "football-data/research/stage6_pre_b_prospective_confirmation/STAGE6_PRE_B_PROSPECTIVE_CONFIRMATION_CONTRACT.json"
    ], text=True).strip() != MAIN_CONTRACT_BLOB:
        raise EnrollmentError("main contract blob drift")
    main_contract_sha256 = sha_bytes(main_contract_bytes)

    observation_manifest = {
        "schema_version": "football3-stage6-pre-b-source-observation-manifest-v1",
        "feature_cutoff_utc": "2026-09-04T11:00:00Z",
        "cutoff_state_artifact_id": 9934686684,
        "cutoff_state_artifact_digest": "sha256:42f6b6a69fc14439e6068a52dd752447976e3581c02390a714453cdd7f9b3492",
        "cutoff_state_package_sha256": sealed_cutoff["package_sha256"],
        "queue_identity_sha256": QUEUE_SHA,
        "current_future_discovery_league_payloads": league_provenance,
        "target_match_ajax_pages_fetched": 0,
        "target_result_or_goal_values_read": 0,
        "legacy_code": legacy_code,
        "engine_receipt": engine_receipt,
    }
    observation_manifest_sha = sha_obj(observation_manifest)

    receipts = []
    max_matrix_error = 0.0
    max_prob_delta = 0.0
    active_n = 0
    fallback_n = 0
    groups = atomic_groups(mapped)

    for group in groups:
        now = utc_now()
        group_kickoff = group[0][1]["kickoff"]
        if now >= group_kickoff:
            raise EnrollmentError(f"STOP_MISSED_PREKICKOFF_RECEIPT_NO_BACKFILL {iso(group_kickoff)}")
        frs = [
            legacy.hxg.FixtureRow(
                r["fixture_id"], r["competition_id"], r["season"], r["kickoff"],
                r["home_team_id"], r["away_team_id"], r["home_team"], r["away_team"]
            )
            for _, r in group
        ]
        xp, bp = formal_state.predict_batch(frs, include_matrix=False)
        for (q, r), f, x, b in zip(group, frs, xp, bp):
            rec = legacy.formal.prediction_record(legacy.hxg, b, x, 0.75)
            row = {
                "fixture_id": f.fixture_id,
                "league": r["league"],
                "season": int(r["season"]),
                "kickoff": f.kickoff.isoformat(),
                "home_team_id": f.home_team_id,
                "away_team_id": f.away_team_id,
                "v1_mu_home": float(b["mu_home"]),
                "v1_mu_away": float(b["mu_away"]),
                "xg_mu_home": float(x["mu_home"]),
                "xg_mu_away": float(x["mu_away"]),
                "v1": rec["v1"],
                "xg": rec["xg"],
                "fusion": rec["fusion"],
                "fallback_exact_v1": bool(rec["fallback_exact_v1"]),
                "cold_start_bucket": rec["cold_start_bucket"],
            }
            hp, hw = legacy.profile_at(process_pack, r["league"], r["process_home_id"], r["kickoff"])
            ap, aw = legacy.profile_at(process_pack, r["league"], r["process_away_id"], r["kickoff"])
            proc = {
                f.fixture_id: {
                    "valid": bool(hp is not None and ap is not None),
                    "home": hp,
                    "away": ap,
                    "home_weight": hw,
                    "away_weight": aw,
                }
            }
            v31_p = v31.predict_variant(usr, model, row, proc, "V3.1-A", {"residual_scale": 0.25})
            baseline_matrix = joint.candidate_matrix("V3.1.1-A", {}, row, v31_p)
            if not joint.matrix_valid(baseline_matrix):
                raise EnrollmentError("V3.1.1 baseline matrix invalid")
            baseline_p = joint.integrate(baseline_matrix)
            if max(abs(float(a0) - float(b0)) for a0, b0 in zip(baseline_p, v31_p)) > 1e-12:
                raise EnrollmentError("V3.1.1 matrix-to-1x2 mismatch")

            home_num = str(r["process_home_id"])
            away_num = str(r["process_away_id"])
            league_scores = b_scores.get(r["league"], {})
            cand_p, active, raw_edge = b_predict(
                baseline_p,
                league_scores.get(home_num),
                league_scores.get(away_num),
                0.10,
            )
            if active:
                candidate_matrix = stage6_common.region_rescale(baseline_matrix, baseline_p, cand_p)
                active_n += 1
                state_label = "ACTIVE"
            else:
                candidate_matrix = [[float(v) for v in rr] for rr in baseline_matrix]
                cand_p = [float(v) for v in baseline_p]
                fallback_n += 1
                state_label = "EXACT_FROZEN_V311_FALLBACK"
            got = stage6_common.integrate_matrix(candidate_matrix)
            err = max(abs(float(got[i]) - float(cand_p[i])) for i in range(3))
            max_matrix_error = max(max_matrix_error, err)
            prob_delta = max(abs(float(cand_p[i]) - float(baseline_p[i])) for i in range(3))
            max_prob_delta = max(max_prob_delta, prob_delta)
            if err > 1e-12:
                raise EnrollmentError("candidate matrix-to-1x2 mismatch")
            if prob_delta > 0.08 + 1e-15:
                raise EnrollmentError("B outcome probability delta cap exceeded")

            generated = utc_now()
            if generated >= r["kickoff"]:
                raise EnrollmentError(f"receipt generated at/after kickoff {f.fixture_id}")
            receipt = {
                "schema_version": "football3-stage6-pre-b-immutable-prediction-receipt-v1",
                "fixture_identity_sha256": q["fixture_identity_sha256"],
                "understat_match_id": int(r["mid"]),
                "competition": q["competition"],
                "season": q["season"],
                "home_team": q["home_team"],
                "away_team": q["away_team"],
                "scheduled_kickoff_utc": q["scheduled_kickoff_utc"],
                "feature_cutoff_utc": "2026-09-04T11:00:00Z",
                "generated_at_utc": iso(generated),
                "source_observation_manifest_sha256": observation_manifest_sha,
                "frozen_v311_head": V311_HEAD,
                "candidate_contract_blob": MAIN_CONTRACT_BLOB,
                "candidate_contract_sha256": main_contract_sha256,
                "baseline_1x2": [float(v) for v in baseline_p],
                "candidate_1x2": [float(v) for v in cand_p],
                "baseline_score_matrix": [[float(v) for v in rr] for rr in baseline_matrix],
                "candidate_score_matrix": [[float(v) for v in rr] for rr in candidate_matrix],
                "baseline_score_matrix_sha256": sha_obj(baseline_matrix),
                "candidate_score_matrix_sha256": sha_obj(candidate_matrix),
                "active_or_fallback_state": state_label,
                "b_raw_edge": None if raw_edge is None else float(raw_edge),
                "target_label_read": false if False else False,
                "target_match_ajax_page_fetched": False,
            }
            receipt["receipt_sha256"] = sha_obj(receipt)
            receipts.append(receipt)

    if len(receipts) != REQUIRED_N:
        raise EnrollmentError(f"receipt count {len(receipts)} != {REQUIRED_N}")
    if [r["fixture_identity_sha256"] for r in receipts] != [q["fixture_identity_sha256"] for q in fixtures]:
        raise EnrollmentError("receipt identity order drift")
    if queue_digest(receipts) != QUEUE_SHA:
        raise EnrollmentError("receipt ordered identity digest drift")
    if active_n + fallback_n != REQUIRED_N:
        raise EnrollmentError("active/fallback count mismatch")
    if any(parse_iso(r["generated_at_utc"]) >= parse_iso(r["scheduled_kickoff_utc"]) for r in receipts):
        raise EnrollmentError("post-kickoff receipt detected")

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "source_observation_manifest.json").write_text(
        json.dumps(observation_manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (a.out / "receipts.jsonl").open("w", encoding="utf-8") as fh:
        for r in receipts:
            fh.write(canon(r).decode("utf-8") + "\n")
    ledger = [{
        "fixture_identity_sha256": r["fixture_identity_sha256"],
        "scheduled_kickoff_utc": r["scheduled_kickoff_utc"],
        "receipt_sha256": r["receipt_sha256"],
        "active_or_fallback_state": r["active_or_fallback_state"],
    } for r in receipts]
    (a.out / "receipt_ledger.json").write_text(
        json.dumps({
            "schema_version": "football3-stage6-pre-b-receipt-ledger-v1",
            "receipt_n": len(ledger),
            "ordered_queue_identity_sha256": QUEUE_SHA,
            "ordered_receipt_sha256": sha_obj([x["receipt_sha256"] for x in ledger]),
            "receipts": ledger,
        }, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    final = {
        "schema_version": "football3-stage6-pre-b-enrollment-final-v1",
        "status": "STAGE6_PRE_B_FRESH_CONFIRMATION_RECEIPTS_FULLY_ENROLLED_LABELS_SEALED",
        "research_only": True,
        "required_n": REQUIRED_N,
        "receipt_n": len(receipts),
        "active_n": active_n,
        "fallback_n": fallback_n,
        "ordered_queue_identity_sha256": QUEUE_SHA,
        "first_kickoff_utc": fixtures[0]["scheduled_kickoff_utc"],
        "last_kickoff_utc": fixtures[-1]["scheduled_kickoff_utc"],
        "source_observation_manifest_sha256": observation_manifest_sha,
        "max_matrix_to_1x2_abs_error": max_matrix_error,
        "max_outcome_probability_abs_delta": max_prob_delta,
        "all_generated_before_locked_kickoff": True,
        "target_labels_opened": False,
        "target_result_or_goal_values_read": 0,
        "target_match_ajax_pages_fetched": 0,
        "target_state_updates_applied": 0,
        "interim_scoring": False,
        "candidate_modified": False,
        "formal_v2_modified": False,
        "v311_modified": False,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_enablement_changed": False,
        "post_enrollment_action": "WAIT_ALL_1335_FIXTURES_COMPLETE_THEN_ONE_SHOT_LABEL_REVEAL_AND_SCORE",
    }
    (a.out / "final_status.json").write_text(json.dumps(final, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(final, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
