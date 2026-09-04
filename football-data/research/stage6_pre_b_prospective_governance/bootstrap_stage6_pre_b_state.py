from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import types
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CUTOFF = datetime.fromisoformat("2026-09-04T11:00:00+00:00")
LEGACY_HEAD = "c5366a405804176130247dfc3d655c6218ce2563"
LEGACY_SOURCE_PATH = "football-data/research/v3_1_1_prospective_confirmation/understat_source_preflight.py"
LEGACY_BOOTSTRAP_PATH = "football-data/research/v3_1_1_prospective_confirmation/prospective_state_bootstrap.py"
LEGACY_FROZEN_STATE_PATH = "football-data/research/v3_1_1_prospective_confirmation/FROZEN_CANDIDATE_STATE.json"
BIG5 = ("Bundesliga", "EPL", "La liga", "Ligue 1", "Serie A")
SEASON_SLUG = {
    "EPL": "EPL",
    "La liga": "La_Liga",
    "Bundesliga": "Bundesliga",
    "Serie A": "Serie_A",
    "Ligue 1": "Ligue_1",
}
B_HALF_LIFE_MATCHES = 16.0
B_EPS = 1e-12


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha_obj(obj) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_show(path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{LEGACY_HEAD}:{path}"])


def load_legacy_modules():
    source_bytes = git_show(LEGACY_SOURCE_PATH)
    source_mod = types.ModuleType("understat_source_preflight")
    source_mod.__file__ = str(ROOT / LEGACY_SOURCE_PATH)
    sys.modules["understat_source_preflight"] = source_mod
    exec(compile(source_bytes, source_mod.__file__, "exec"), source_mod.__dict__)

    bootstrap_bytes = git_show(LEGACY_BOOTSTRAP_PATH)
    boot_mod = types.ModuleType("v311_prospective_state_bootstrap_locked")
    boot_mod.__file__ = str(ROOT / LEGACY_BOOTSTRAP_PATH)
    exec(compile(bootstrap_bytes, boot_mod.__file__, "exec"), boot_mod.__dict__)
    boot_mod.CUTOFF = CUTOFF
    return source_mod, boot_mod, {
        "source_sha256": sha_bytes(source_bytes),
        "bootstrap_sha256": sha_bytes(bootstrap_bytes),
    }


class BState:
    __slots__ = ("deep", "press", "n")

    def __init__(self, deep: float = 0.0, press: float = 0.0, n: int = 0):
        self.deep = float(deep)
        self.press = float(press)
        self.n = int(n)

    def update(self, deep: float, press: float, alpha: float) -> None:
        if self.n == 0:
            self.deep = float(deep)
            self.press = float(press)
        else:
            self.deep = (1.0 - alpha) * self.deep + alpha * float(deep)
            self.press = (1.0 - alpha) * self.press + alpha * float(press)
        self.n += 1

    def dump(self) -> dict:
        return {"deep": self.deep, "press": self.press, "n": self.n}


def b_alpha() -> float:
    return 1.0 - math.exp(math.log(0.5) / B_HALF_LIFE_MATCHES)


def finite_nonnegative(value, name: str) -> float:
    out = float(value)
    if not math.isfinite(out) or out < 0:
        raise ValueError(f"{name} invalid")
    return out


def b_values_from_ratio(deep, ppda_ratio):
    d = math.log1p(max(0.0, finite_nonnegative(deep, "deep")))
    p = finite_nonnegative(ppda_ratio, "ppda_ratio")
    return d, -math.log(max(B_EPS, p))


def b_values_from_history(row: dict):
    if not isinstance(row, dict) or "deep" not in row or "ppda" not in row:
        raise ValueError("history deep/ppda missing")
    ppda = row["ppda"]
    if not isinstance(ppda, dict) or "att" not in ppda or "def" not in ppda:
        raise ValueError("history ppda att/def missing")
    att = finite_nonnegative(ppda["att"], "ppda.att")
    deff = finite_nonnegative(ppda["def"], "ppda.def")
    if deff <= 0:
        raise ValueError("history ppda.def zero")
    return b_values_from_ratio(row["deep"], att / deff)


def bootstrap_b_state(old_db: pathlib.Path, source_mod):
    alpha = b_alpha()
    states: dict[str, dict[str, BState]] = defaultdict(dict)
    rec = {
        "historical_db_rows": 0,
        "historical_updates": 0,
        "historical_missing_or_invalid": 0,
        "online_history_rows_seen": 0,
        "online_updates_pre_cutoff": 0,
        "online_rows_at_or_after_cutoff_ignored": 0,
        "online_missing_or_invalid": 0,
        "league_payloads": {},
    }

    con = sqlite3.connect(str(old_db))
    con.row_factory = sqlite3.Row
    qs = ",".join("?" for _ in BIG5)
    sql = f"""
      select fid,h_id,a_id,date,league,season,h_deep,a_deep,h_ppda,a_ppda
      from general_game_stats
      where league in ({qs}) and season between 2014 and 2023
      order by date,fid
    """
    rows = [dict(r) for r in con.execute(sql, BIG5)]
    con.close()
    rec["historical_db_rows"] = len(rows)
    for row in rows:
        league = str(row["league"])
        for side in ("h", "a"):
            try:
                d, p = b_values_from_ratio(row[f"{side}_deep"], row[f"{side}_ppda"])
                team = str(int(row[f"{side}_id"]))
            except Exception:
                rec["historical_missing_or_invalid"] += 1
                continue
            st = states[league].setdefault(team, BState())
            st.update(d, p, alpha)
            rec["historical_updates"] += 1

    for season in (2024, 2025, 2026):
        for league in BIG5:
            slug = SEASON_SLUG[league]
            url = f"https://understat.com/getLeagueData/{slug}/{season}"
            raw, obj, meta = source_mod.fetch_ajax_json(url)
            teams = obj.get("teams")
            if not isinstance(teams, dict):
                raise RuntimeError(f"{league}|{season}: teams missing")
            payload_rec = {
                "url": url,
                "decoded_sha256": source_mod.sha256(raw),
                "transport": meta,
                "team_n": len(teams),
                "pre_cutoff_updates": 0,
                "ignored_at_or_after_cutoff": 0,
                "missing_or_invalid": 0,
            }
            for team_id, team in teams.items():
                history = team.get("history") if isinstance(team, dict) else None
                if not isinstance(history, list):
                    raise RuntimeError(f"{league}|{season}|{team_id}: history missing")
                for hrow in history:
                    rec["online_history_rows_seen"] += 1
                    if not isinstance(hrow, dict) or not hrow.get("date"):
                        rec["online_missing_or_invalid"] += 1
                        payload_rec["missing_or_invalid"] += 1
                        continue
                    dt = source_mod.parse_dt(hrow["date"])
                    if dt >= CUTOFF:
                        rec["online_rows_at_or_after_cutoff_ignored"] += 1
                        payload_rec["ignored_at_or_after_cutoff"] += 1
                        continue
                    try:
                        d, p = b_values_from_history(hrow)
                        team_key = str(int(team_id))
                    except Exception:
                        rec["online_missing_or_invalid"] += 1
                        payload_rec["missing_or_invalid"] += 1
                        continue
                    st = states[league].setdefault(team_key, BState())
                    st.update(d, p, alpha)
                    rec["online_updates_pre_cutoff"] += 1
                    payload_rec["pre_cutoff_updates"] += 1
            rec["league_payloads"][f"{league}|{season}"] = payload_rec
            time.sleep(0.1)

    pack = {
        "half_life_matches": B_HALF_LIFE_MATCHES,
        "alpha": alpha,
        "transform": {
            "deep": "log1p(max(0,deep))",
            "press": "-ln(max(1e-12,ppda_ratio))",
        },
        "as_of_utc": CUTOFF.isoformat().replace("+00:00", "Z"),
        "leagues": {
            league: {team: state.dump() for team, state in sorted(team_map.items())}
            for league, team_map in sorted(states.items())
        },
    }
    if set(pack["leagues"]) != set(BIG5):
        raise RuntimeError(f"B state leagues incomplete: {set(pack['leagues'])}")
    if sum(len(x) for x in pack["leagues"].values()) < 100:
        raise RuntimeError("B state suspiciously small")
    return pack, rec


def safe_fetch_bridge_process(boot_mod, bridge: list[dict], workers: int):
    source_mod = boot_mod.source

    def one(row):
        if row["kickoff"] >= CUTOFF:
            raise RuntimeError("target match AJAX fetch forbidden in bootstrap")
        url = f"https://understat.com/getMatchData/{row['mid']}"
        raw, obj, meta = source_mod.fetch_ajax_json(url)
        base_meta = {
            "decoded_sha256": source_mod.sha256(raw),
            "wire_sha256": meta["wire_sha256"],
            "content_encoding": meta["content_encoding"],
        }
        try:
            shots = source_mod.match_shots(obj)
        except RuntimeError as exc:
            if "shots empty" in str(exc):
                return row["mid"], None, {**base_meta, "status": "MISSING_EMPTY_SHOTS_NO_UPDATE"}
            raise
        stats = boot_mod.aggregate_shots(shots)
        if stats is None:
            return row["mid"], None, {**base_meta, "status": "MISSING_INSUFFICIENT_BILATERAL_PROCESS_NO_UPDATE"}
        return row["mid"], stats, {**base_meta, "status": "OK"}

    out = {}
    metas = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, row): row["mid"] for row in bridge}
        for fut in as_completed(futs):
            mid, stats, meta = fut.result()
            out[mid] = stats
            metas[mid] = meta
            done += 1
            if done % 250 == 0:
                print(f"bridge_shot_schema_processed_n={done}", flush=True)
    return out, metas


def run(old_db: pathlib.Path, xg_identity: pathlib.Path, contract_path: pathlib.Path, out: pathlib.Path, workers: int):
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("status") != "FROZEN_BEFORE_PROSPECTIVE_RECEIPT_ENROLLMENT":
        raise RuntimeError("state bootstrap contract status drift")
    if contract.get("as_of_utc") != "2026-09-04T11:00:00Z" or contract.get("required_n") != 1335:
        raise RuntimeError("state bootstrap contract cutoff/N drift")

    source_mod, boot_mod, legacy_code = load_legacy_modules()
    frozen_state = json.loads(git_show(LEGACY_FROZEN_STATE_PATH).decode("utf-8"))
    if frozen_state.get("candidate_head") != "a90762a97515f3edd564e8ad204db0d0d4231494":
        raise RuntimeError("frozen V3.1.1 candidate state head drift")
    if frozen_state.get("candidate_state_mutation_allowed") is not False:
        raise RuntimeError("candidate mutation guard drift")

    priors, proc_states, proc_queue, existing_ids, process_base_n = boot_mod.load_process_base(old_db)
    formal_state, formal_pending, formal_labels, formal_base_n = boot_mod.replay_formal_base(old_db, xg_identity)
    bridge, future, league_provenance = boot_mod.fetch_bridge_and_future(existing_ids)
    if any(row["kickoff"] >= CUTOFF for row in bridge):
        raise RuntimeError("bridge contains target/post-cutoff row")
    shot_stats, shot_transport = safe_fetch_bridge_process(boot_mod, bridge, workers)
    boot_mod.replay_bridge(
        formal_state,
        formal_pending,
        formal_labels,
        proc_states,
        proc_queue,
        bridge,
        shot_stats,
    )

    v311_formal_pack = boot_mod.serialize_formal(formal_state)
    v311_process_pack = boot_mod.serialize_process(priors, proc_states)
    process_valid = 0
    for row in future:
        hp, _ = boot_mod.profile_at(v311_process_pack, row["league"], row["process_home_id"], CUTOFF)
        ap, _ = boot_mod.profile_at(v311_process_pack, row["league"], row["process_away_id"], CUTOFF)
        process_valid += int(hp is not None and ap is not None)

    b_pack, b_rec = bootstrap_b_state(old_db, source_mod)
    shot_status_counts = defaultdict(int)
    for meta in shot_transport.values():
        shot_status_counts[str(meta["status"])] += 1

    package = {
        "schema_version": "football3-stage6-pre-b-cutoff-state-package-v1",
        "status": "CUTOFF_STATE_READY_ZERO_LABEL",
        "research_only": True,
        "as_of_utc": "2026-09-04T11:00:00Z",
        "required_n": 1335,
        "queue_identity_sha256": "6cfcaba8e2f82af0996a404eb3fc5bb477174aebd09c9b10c7434d95e59c8dfc",
        "target_labels_read_for_scoring": False,
        "target_match_pages_fetched": 0,
        "interim_scoring_run": False,
        "candidate_modified": False,
        "formal_v2_modified": False,
        "CURRENT_changed": False,
        "production_pointer_changed": False,
        "formal_enablement_changed": False,
        "legacy_v311_scaffold": {
            "head": LEGACY_HEAD,
            **legacy_code,
            "frozen_candidate_state_blob_expected": "000c092b7b0c29a05dee7a70d0b59ead722f8f4e",
            "frozen_candidate_state_payload_sha256": frozen_state["canonical_payload_sha256"],
        },
        "v311_frozen_candidate_state": frozen_state,
        "v311_formal_state": v311_formal_pack,
        "v311_process_state": v311_process_pack,
        "b_deep_ppda_state": b_pack,
        "bootstrap_evidence": {
            "historical_formal_fixture_n": formal_base_n,
            "historical_v311_process_fixture_n": process_base_n,
            "bridge_completed_pre_cutoff_n": len(bridge),
            "bridge_process_match_ajax_n": len(shot_transport),
            "bridge_process_status_counts": dict(sorted(shot_status_counts.items())),
            "bridge_process_missing_no_update_n": sum(v for k, v in shot_status_counts.items() if k != "OK"),
            "bridge_missing_policy": "NO_PROCESS_STATE_UPDATE_NO_IMPUTATION_RECORD_MISSING",
            "future_discovery_n_after_cutoff": len(future),
            "v311_process_profile_valid_n_at_cutoff": process_valid,
            "post_cutoff_match_ajax_fetched": 0,
            "raw_ajax_payload_persisted": False,
            "raw_shot_rows_persisted": False,
            "league_ajax_provenance": league_provenance,
            "bridge_shot_transport_summary_sha256": sha_obj({str(k): v for k, v in sorted(shot_transport.items())}),
            "b_state_receipt": b_rec,
        },
    }
    package["v311_formal_state_sha256"] = sha_obj(v311_formal_pack)
    package["v311_process_state_sha256"] = sha_obj(v311_process_pack)
    package["b_deep_ppda_state_sha256"] = sha_obj(b_pack)
    package["package_sha256"] = sha_obj({k: v for k, v in package.items() if k != "package_sha256"})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(package, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": package["status"],
        "bridge_n": len(bridge),
        "bridge_process_status_counts": dict(sorted(shot_status_counts.items())),
        "future_discovery_n": len(future),
        "v311_process_profile_valid_n_at_cutoff": process_valid,
        "v311_formal_state_sha256": package["v311_formal_state_sha256"],
        "v311_process_state_sha256": package["v311_process_state_sha256"],
        "b_deep_ppda_state_sha256": package["b_deep_ppda_state_sha256"],
        "package_sha256": package["package_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-db", type=pathlib.Path, required=True)
    ap.add_argument("--xg-identity", type=pathlib.Path, required=True)
    ap.add_argument("--contract", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if not 1 <= args.workers <= 8:
        raise SystemExit("workers must be 1..8")
    run(args.old_db, args.xg_identity, args.contract, args.out, args.workers)
