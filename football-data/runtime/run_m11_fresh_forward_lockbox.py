#!/usr/bin/env python3
"""M11 fresh-forward enrollment and four-config outcome-blind prediction lockbox.

Consumes only prospectively captured Kambi PIT market snapshots plus safe fixture/team
metadata for current-match identity. Historical outcomes are read only from the frozen
S60 and R43Q development histories; no fresh-match outcome/status/stat field is read.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import re
import shutil
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.r43_probability_matrix_adapters import R43YScoreMatrixTransportComponent
from components.r43u_fixed_diagonal import R43UFixedDiagonalInflationComponent
from identity.team_identity import TeamIdentityResolver
from pipeline.runtime_authority import authority_receipt, build_operational_s60_engine
from pipeline.s60_numerical_baseline import CLASSIFIER_TRAIN_ROWS, HISTORY_ROWS, S60NumericalBaseline
from pipeline.unified_inference import FixtureRequest, one_x_two
from pit.feature_store import PointInTimeFeatureStore

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
PREREG = ROOT / "governance" / "r43gov0" / "prereg" / "m11_fresh_forward_300.json"
REGISTRY = ROOT / "config" / "v6_full17_capture_identity_v6484.json"
BASE = ROOT / "experiments" / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
EXTRA = ROOT / "experiments" / "top1_r25_fresh_s60_confirmation" / "r24_artifact" / "data" / "extra_r24_xg_60000.csv"
Q_LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
LOCKBOX = ROOT / "forward" / "m11_fresh_forward_300_lockbox.json"
OUTDIR = ROOT / "runtime" / "results" / "m11_fresh_forward_lockbox"
BASE_SHA256 = "6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba"
EXTRA_SHA256 = "477d1a4f542850e4e2981b98acbbbba0c261b14f37fc0f5f618d5cb1234452bc"
Q_SOURCE_BLOB = "299b86ed07e49af0b9ec5c7632f519e91e836158"
PREREG_LOCK_COMMIT = "5ad4d48acee540dc2f6bb651228b202b06a4f713"
PREREG_LOCK_UTC = datetime(2026, 8, 29, 14, 12, 17, tzinfo=timezone.utc)
TARGET = 300
CLASSES = ("home", "draw", "away")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: object) -> datetime:
    x = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError(f"timezone missing:{value}")
    return x.astimezone(timezone.utc)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def top1(p: dict[str, float]) -> str:
    return max(CLASSES, key=lambda k: (float(p[k]), -CLASSES.index(k)))


def matrix_hash(cells: list[dict[str, Any]]) -> str:
    rows = sorted(
        ({"home_goals": int(c["home_goals"]), "away_goals": int(c["away_goals"]), "probability": float(c["probability"])} for c in cells),
        key=lambda x: (x["home_goals"], x["away_goals"]),
    )
    return stable_hash(rows)


def np_matrix_to_cells(m: np.ndarray) -> list[dict[str, Any]]:
    return [
        {"home_goals": int(h), "away_goals": int(a), "probability": float(m[h, a])}
        for h in range(m.shape[0]) for a in range(m.shape[1])
    ]


def alias_key(value: object) -> str:
    token = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().casefold()
    for item in ("football club", " fc", "fc ", " afc", "cf ", " cf", "&", ".", "-", "'", "/"):
        token = token.replace(item, " ")
    token = re.sub(r"\s+", " ", token).strip()
    fixed = {
        "wolverhampton wanderers": "wolves",
        "brighton hove albion": "brighton",
        "tottenham hotspur": "tottenham",
        "bayern munchen": "bayern munich",
        "borussia monchengladbach": "borussia m gladbach",
        "internazionale": "inter",
        "olympique marseille": "marseille",
        "olympique de marseille": "marseille",
        "olympique lyonnais": "lyon",
        "koln": "cologne",
        "1 koln": "cologne",
    }
    return fixed.get(token, token)


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "football3-m11-fresh-lockbox/1"})
    with urllib.request.urlopen(req, timeout=300) as response, path.open("wb") as fh:
        for block in iter(lambda: response.read(1 << 20), b""):
            fh.write(block)


def load_csv(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for x in csv.DictReader(fh):
            x["home_goals"] = int(x["home_goals"])
            x["away_goals"] = int(x["away_goals"])
            x["home_xg"] = float(x["home_xg"])
            x["away_xg"] = float(x["away_xg"])
            rows.append(x)
    return rows


def load_s60_history(cutoff: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sha256(BASE) != BASE_SHA256:
        raise RuntimeError("R9b frozen 20k base hash mismatch")
    if sha256(EXTRA) != EXTRA_SHA256:
        raise RuntimeError("R24 frozen 60k extra history hash mismatch")
    base, extra = load_csv(BASE), load_csv(EXTRA)
    if len(base) != 20000 or len(extra) != 60000:
        raise RuntimeError(f"S60 history source count mismatch base={len(base)} extra={len(extra)}")
    if {x["game_id"] for x in base} & {x["game_id"] for x in extra}:
        raise RuntimeError("S60 history identity overlap")
    pool = extra + base
    eligible = []
    for x in pool:
        known = iso(x["xg_known_at"])
        if known < cutoff:
            eligible.append(x)
    eligible.sort(key=lambda x: (x["date"], x["game_id"]))
    if len(eligible) < HISTORY_ROWS:
        raise RuntimeError(f"strict-prior S60 history {len(eligible)} < {HISTORY_ROWS}")
    history = eligible[-HISTORY_ROWS:]
    receipt = {
        "cutoff_utc": cutoff.isoformat(),
        "eligible_pool_rows": len(eligible),
        "history_rows": len(history),
        "history_first_date": history[0]["date"],
        "history_last_date": history[-1]["date"],
        "base_sha256": BASE_SHA256,
        "extra_sha256": EXTRA_SHA256,
    }
    return history, receipt


def load_q_source(path: Path):
    spec = importlib.util.spec_from_file_location("football3_m11_r43q_source", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen R43Q source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def freeze_q_historical_calibrator(q) -> tuple[tuple[float, float], dict[str, Any]]:
    ledger = json.loads(Q_LEDGER.read_text(encoding="utf-8"))
    preds: dict[str, dict[str, Any]] = {}
    settled: dict[str, dict[str, Any]] = {}
    for event in ledger.get("events") or []:
        mid = str(event.get("match_id"))
        if event.get("event_type") == "MARKET_PREDICTION_FROZEN":
            preds[mid] = event
        elif event.get("event_type") == "RESULT_SETTLED":
            settled[mid] = event
    rows = []
    max_kickoff = None
    for mid, se in settled.items():
        pe = preds.get(mid)
        if pe is None:
            continue
        payload = pe["payload"]
        fixture = payload["fixture_identity"]
        surfaces = payload["frozen_surfaces"]
        kickoff = iso(fixture["kickoff_at"])
        frozen = iso(pe["event_timestamp_utc"])
        if frozen >= kickoff or kickoff >= PREREG_LOCK_UTC:
            continue
        y = str(se["payload"]["result"]["actual_result"])
        if y not in CLASSES:
            continue
        market = q.devig_1x2(surfaces["one_x_two_odds"])
        lh, la, _ = q.infer_lambdas(surfaces["asian_handicap"], surfaces["over_under"], market)
        matrix = q.score_matrix(lh, la)
        raw = q.matrix_1x2(matrix)
        rows.append({"market": market, "latent_raw": raw, "matrix_raw": matrix, "y": y})
        max_kickoff = kickoff if max_kickoff is None or kickoff > max_kickoff else max_kickoff
    if len(rows) < 30:
        raise RuntimeError(f"R43Q historical calibrator rows too small:{len(rows)}")
    ab = q.fit_draw_cal(rows)
    receipt = {
        "source_blob_sha": Q_SOURCE_BLOB,
        "historical_ledger_sha256": sha256(Q_LEDGER),
        "historical_training_rows": len(rows),
        "max_training_kickoff_utc": max_kickoff.isoformat() if max_kickoff else None,
        "draw_calibrator_intercept_shift": float(ab[0]),
        "draw_calibrator_raw_minus_market_slope": float(ab[1]),
        "draw_cal_penalty": float(q.DRAW_CAL_PENALTY),
        "fresh_outcomes_used": False,
        "parameter_search": False,
    }
    return (float(ab[0]), float(ab[1])), receipt


def registry_aliases() -> dict[tuple[str, str], set[str]]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], set[str]] = {}
    for cid, comp in (data.get("competitions") or {}).items():
        for team in comp.get("teams") or []:
            canonical = str(team.get("canonical_name") or "").strip()
            if not canonical:
                continue
            vals = {canonical}
            vals.update(str(x).strip() for x in (team.get("provider_alias_tokens") or []) if str(x).strip())
            out[(str(cid), canonical)] = vals
    return out


def load_probe_candidates(probe_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gate_path = probe_root / "m11_probe" / "gate.json"
    if not gate_path.exists():
        raise FileNotFoundError(gate_path)
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if gate.get("status") != "PASS_FRESH_ATOMIC_PIT_AVAILABLE":
        raise RuntimeError(f"probe gate not pass:{gate.get('status')}")
    candidates = []
    for c in gate.get("candidates") or []:
        rel = str(c["formal_snapshot_path"])
        p = probe_root / "football-data" / rel
        if not p.exists():
            raise FileNotFoundError(p)
        s = json.loads(p.read_text(encoding="utf-8"))
        observed = iso(s["source_observed_at_utc"])
        kickoff = iso(s["kickoff_utc"])
        cutoff = kickoff - timedelta(minutes=60)
        same = s.get("surface_observed_at_utc") or {}
        atomic = all(iso(same[k]) == observed for k in ("one_x_two", "asian_handicap", "over_under"))
        if not atomic or observed < PREREG_LOCK_UTC or observed > cutoff:
            continue
        if not all(k in s.get("one_x_two", {}) for k in ("home", "draw", "away")):
            continue
        if not all(k in s.get("asian_handicap", {}) for k in ("line", "home", "away")):
            continue
        if not all(k in s.get("over_under", {}) for k in ("line", "over", "under")):
            continue
        candidates.append({"gate": c, "snapshot": s, "snapshot_path": p, "cutoff": cutoff})
    candidates.sort(key=lambda x: (x["snapshot"]["kickoff_utc"], str(x["gate"].get("provider_event_id"))))
    return candidates, gate


def build_safe_hf_mapping(candidates: list[dict[str, Any]], work: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    teams_path = work / "teams.parquet"
    fixtures_path = work / "fixtures_safe.parquet"
    download(f"{HF}/teams.parquet?download=true", teams_path)
    download(f"{HF}/fixtures.parquet?download=true", fixtures_path)
    teams = pd.read_parquet(teams_path)
    fixtures = pd.read_parquet(fixtures_path, columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id"])
    fixtures["date_utc"] = pd.to_datetime(fixtures["date_utc"], utc=True)
    text_cols = [c for c in teams.columns if ("name" in c.casefold()) and (pd.api.types.is_string_dtype(teams[c].dtype) or teams[c].dtype == object)]
    alias_ids: dict[str, set[str]] = {}
    for row in teams.itertuples(index=False):
        d = row._asdict()
        if d.get("id") is None:
            continue
        tid = str(int(d["id"]))
        for col in text_cols:
            value = d.get(col)
            if value is None or str(value).casefold() == "nan":
                continue
            key = alias_key(value)
            if key:
                alias_ids.setdefault(key, set()).add(tid)
    reg = registry_aliases()
    mapped: dict[str, dict[str, Any]] = {}
    audits = []
    for item in candidates:
        s = item["snapshot"]
        cid = str(s["competition_id"])
        home = str(s["home_team"])
        away = str(s["away_team"])
        event_id = str(item["gate"].get("provider_event_id"))
        home_tokens = reg.get((cid, home), {home})
        away_tokens = reg.get((cid, away), {away})
        home_ids = sorted({tid for token in home_tokens for tid in alias_ids.get(alias_key(token), set())})
        away_ids = sorted({tid for token in away_tokens for tid in alias_ids.get(alias_key(token), set())})
        kickoff = pd.Timestamp(iso(s["kickoff_utc"]))
        rec: dict[str, Any] = {
            "provider_event_id": event_id,
            "competition_id": cid,
            "home_team": home,
            "away_team": away,
            "kickoff_utc": s["kickoff_utc"],
            "home_candidate_ids": home_ids,
            "away_candidate_ids": away_ids,
            "status": "UNRESOLVED",
            "outcome_columns_read": False,
            "status_columns_read": False,
            "fuzzy_matching_used": False,
        }
        if home_ids and away_ids:
            q = fixtures[
                fixtures["home_team_id"].astype(str).isin(home_ids)
                & fixtures["away_team_id"].astype(str).isin(away_ids)
                & (fixtures["date_utc"] >= kickoff - pd.Timedelta(days=2))
                & (fixtures["date_utc"] <= kickoff + pd.Timedelta(days=2))
            ].copy()
            q["kickoff_delta_seconds"] = (q["date_utc"] - kickoff).abs().dt.total_seconds()
            q = q.sort_values(["kickoff_delta_seconds", "id"])
            chosen = None
            rule = None
            if len(q) == 1:
                chosen = q.iloc[0]
                rule = "unique_exact_team_pair_within_2d"
            elif len(q) > 1:
                non_midnight = q[~((q["date_utc"].dt.hour == 0) & (q["date_utc"].dt.minute == 0) & (q["date_utc"].dt.second == 0))]
                if len(non_midnight) == 1:
                    chosen = non_midnight.iloc[0]
                    rule = "unique_non_midnight_schedule_for_exact_team_pair"
                elif len(q) >= 1:
                    best = float(q.iloc[0]["kickoff_delta_seconds"])
                    second = float(q.iloc[1]["kickoff_delta_seconds"]) if len(q) > 1 else math.inf
                    if best <= 6 * 3600 and second > best + 60:
                        chosen = q.iloc[0]
                        rule = "unique_nearest_schedule_within_6h"
            rec["fixture_candidates"] = [
                {
                    "fixture_id": str(int(r.id)),
                    "date_utc": r.date_utc.isoformat(),
                    "league_id": str(int(r.league_id)),
                    "home_team_id": str(int(r.home_team_id)),
                    "away_team_id": str(int(r.away_team_id)),
                    "kickoff_delta_seconds": float(r.kickoff_delta_seconds),
                }
                for r in q.head(8).itertuples(index=False)
            ]
            if chosen is not None:
                rec.update({
                    "status": "RESOLVED_SAFE_FIXTURE_METADATA",
                    "selection_rule": rule,
                    "fixture_id": str(int(chosen["id"])),
                    "hf_kickoff_utc": chosen["date_utc"].isoformat(),
                    "league_id": str(int(chosen["league_id"])),
                    "home_team_id": str(int(chosen["home_team_id"])),
                    "away_team_id": str(int(chosen["away_team_id"])),
                })
                mapped[event_id] = rec
        audits.append(rec)
    receipt = {
        "teams_sha256": sha256(teams_path),
        "fixtures_safe_sha256": sha256(fixtures_path),
        "teams_name_columns": text_cols,
        "candidate_count": len(candidates),
        "resolved_count": len(mapped),
        "unresolved_count": len(candidates) - len(mapped),
        "outcome_columns_read": False,
        "status_columns_read": False,
        "fuzzy_matching_used": False,
        "audits": audits,
    }
    teams_path.unlink(missing_ok=True)
    fixtures_path.unlink(missing_ok=True)
    return mapped, receipt


def q_predict(q, ab: tuple[float, float], snapshot: dict[str, Any]) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    market = q.devig_1x2(snapshot["one_x_two"])
    lh, la, fit_obj = q.infer_lambdas(snapshot["asian_handicap"], snapshot["over_under"], market)
    raw_matrix = q.score_matrix(lh, la)
    raw = q.matrix_1x2(raw_matrix)
    probabilities, calibrated_matrix = q.apply_draw_cal({"market": market, "latent_raw": raw, "matrix_raw": raw_matrix}, ab)
    cells = np_matrix_to_cells(calibrated_matrix)
    return ({k: float(probabilities[k]) for k in CLASSES}, cells, {
        "market_devig_1x2": {k: float(market[k]) for k in CLASSES},
        "lambda_home": float(lh),
        "lambda_away": float(la),
        "latent_fit_objective": float(fit_obj),
        "latent_raw_1x2": {k: float(raw[k]) for k in CLASSES},
    })


def copy_evidence(probe_root: Path, snapshot_path: Path, snapshot: dict[str, Any]) -> dict[str, str]:
    rel_formal = snapshot_path.relative_to(probe_root / "football-data")
    dst_formal = ROOT / rel_formal
    dst_formal.parent.mkdir(parents=True, exist_ok=True)
    if not dst_formal.exists():
        shutil.copy2(snapshot_path, dst_formal)
    adapter = snapshot.get("source_adapter") or {}
    raw_rel = adapter.get("parent_raw_evidence_path")
    copied_raw = None
    if raw_rel:
        src_raw = probe_root / "football-data" / str(raw_rel)
        if src_raw.exists():
            dst_raw = ROOT / str(raw_rel)
            dst_raw.parent.mkdir(parents=True, exist_ok=True)
            if not dst_raw.exists():
                shutil.copy2(src_raw, dst_raw)
            copied_raw = str(raw_rel)
    return {"formal_snapshot_path": str(rel_formal), "raw_evidence_path": copied_raw}


def load_existing() -> dict[str, Any]:
    if not LOCKBOX.exists():
        return {
            "schema_version": "football3-r43gov0-m11-fresh-forward-lockbox-v1",
            "status": "ENROLLING_OUTCOME_BLIND",
            "prereg_lock_commit": PREREG_LOCK_COMMIT,
            "prereg_lock_utc": PREREG_LOCK_UTC.isoformat(),
            "target_rows": TARGET,
            "rows": [],
        }
    data = json.loads(LOCKBOX.read_text(encoding="utf-8"))
    if data.get("schema_version") != "football3-r43gov0-m11-fresh-forward-lockbox-v1":
        raise RuntimeError("existing M11 lockbox schema mismatch")
    if data.get("prereg_lock_commit") != PREREG_LOCK_COMMIT:
        raise RuntimeError("existing M11 prereg lock mismatch")
    if len(data.get("rows") or []) > TARGET:
        raise RuntimeError("existing M11 lockbox exceeds target")
    return data


def run(probe_root: Path, q_source: Path) -> dict[str, Any]:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("fresh_forward") or int(prereg.get("target_settled_matches")) != TARGET:
        raise RuntimeError("M11 prereg mismatch")
    if list(prereg.get("fixed_configs") or {}) != ["operational_control_s60", "mechanism_control_q", "ablation_q_u", "primary_candidate_q_u_y"]:
        raise RuntimeError("M11 four-config contract drift")
    candidates, probe_gate = load_probe_candidates(probe_root)
    existing = load_existing()
    existing_keys = {(str(r["provider_event_id"]), str(r["kickoff_utc"])) for r in existing.get("rows") or []}
    candidates = [c for c in candidates if (str(c["gate"].get("provider_event_id")), str(c["snapshot"]["kickoff_utc"])) not in existing_keys]
    if not candidates:
        receipt = {
            "status": "PASS_NO_NEW_ELIGIBLE_CANDIDATES",
            "existing_rows": len(existing.get("rows") or []),
            "new_rows": 0,
            "target_rows": TARGET,
        }
        (OUTDIR / "gate.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        return receipt

    work = OUTDIR / "_work"
    work.mkdir(parents=True, exist_ok=True)
    mapping, mapping_receipt = build_safe_hf_mapping(candidates, work)
    mapped_candidates = [c for c in candidates if str(c["gate"].get("provider_event_id")) in mapping]
    if not mapped_candidates:
        gate = {
            "status": "BLOCKED_SAFE_IDENTITY_MAPPING",
            "probe_candidates": len(candidates),
            "mapped_candidates": 0,
            "mapping": mapping_receipt,
        }
        (OUTDIR / "gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n")
        return gate

    common_cutoff = min(iso(c["snapshot"]["source_observed_at_utc"]) for c in mapped_candidates)
    history, history_receipt = load_s60_history(common_cutoff)
    baseline = S60NumericalBaseline.fit_from_history(history)

    provenance = stable_hash({"teams": mapping_receipt["teams_sha256"], "fixtures": mapping_receipt["fixtures_safe_sha256"]})
    identity_records = []
    seen_identity = set()
    for rec in mapping.values():
        ns = f"m11:hf:{rec['competition_id']}"
        for tid in (rec["home_team_id"], rec["away_team_id"]):
            key = (ns, tid)
            if key in seen_identity:
                continue
            seen_identity.add(key)
            identity_records.append({
                "source_namespace": ns,
                "source_team_id": tid,
                "canonical_team_id": tid,
                "mapping_method": "safe_hf_fixture_metadata_exact_pair",
                "provenance_hash": provenance,
            })
    resolver = TeamIdentityResolver(identity_records)
    engine = build_operational_s60_engine(resolver, PointInTimeFeatureStore(), baseline)

    q = load_q_source(q_source)
    ab, q_train_receipt = freeze_q_historical_calibrator(q)
    u = R43UFixedDiagonalInflationComponent(enabled=True)
    y = R43YScoreMatrixTransportComponent(enabled=True)

    new_rows = []
    late = []
    numerical_errors = []
    for item in mapped_candidates:
        s = item["snapshot"]
        event_id = str(item["gate"].get("provider_event_id"))
        m = mapping[event_id]
        kickoff = iso(s["kickoff_utc"])
        cutoff = kickoff - timedelta(minutes=60)
        observed = iso(s["source_observed_at_utc"])
        if observed > cutoff:
            late.append({"provider_event_id": event_id, "reason": "market_snapshot_after_T60"})
            continue
        try:
            ns = f"m11:hf:{m['competition_id']}"
            request = FixtureRequest(
                fixture_id=m["fixture_id"],
                as_of=observed,
                home_source_namespace=ns,
                home_source_team_id=m["home_team_id"],
                home_source_name=s["home_team"],
                away_source_namespace=ns,
                away_source_team_id=m["away_team_id"],
                away_source_name=s["away_team"],
            )
            s60 = engine.predict("m11_fresh_forward", request, {
                "competition_id": m["league_id"],
                "target_date": kickoff.date().isoformat(),
            })
            s60_p = {k: float(s60.probabilities[k]) for k in CLASSES}
            q_p, q_cells, q_detail = q_predict(q, ab, s)
            u_cells = u.apply(q_cells, None, {})
            u_p = {k: float(v) for k, v in one_x_two(u_cells).items()}
            y_cells = y.apply(u_cells, None, {"r43y_source_r43u0_probabilities": u_p})
            y_p = {k: float(v) for k, v in one_x_two(y_cells).items()}
            for name, probs in (("s60", s60_p), ("q", q_p), ("q_u", u_p), ("q_u_y", y_p)):
                if any((not math.isfinite(float(probs[k]))) or float(probs[k]) <= 0 for k in CLASSES):
                    raise ValueError(f"invalid {name} probability")
                if abs(sum(float(probs[k]) for k in CLASSES) - 1.0) > 1e-9:
                    raise ValueError(f"{name} probability sum drift")
            frozen_at = now_utc()
            if frozen_at > cutoff:
                late.append({"provider_event_id": event_id, "reason": "prediction_frozen_after_T60", "frozen_at_utc": frozen_at.isoformat(), "cutoff_utc": cutoff.isoformat()})
                continue
            evidence = copy_evidence(probe_root, item["snapshot_path"], s)
            row = {
                "provider_event_id": event_id,
                "fixture_id": m["fixture_id"],
                "competition_id": s["competition_id"],
                "hf_league_id": m["league_id"],
                "home_team": s["home_team"],
                "away_team": s["away_team"],
                "hf_home_team_id": m["home_team_id"],
                "hf_away_team_id": m["away_team_id"],
                "kickoff_utc": kickoff.isoformat(),
                "prediction_cutoff_utc": cutoff.isoformat(),
                "market_observed_at_utc": observed.isoformat(),
                "prediction_frozen_at_utc": frozen_at.isoformat(),
                "market_snapshot_sha256": s.get("raw_snapshot_sha256"),
                "market_source_url": s.get("source_url"),
                "provider_name": s.get("provider_name"),
                "provider_group": s.get("provider_group"),
                "frozen_surfaces": {
                    "one_x_two": s["one_x_two"],
                    "asian_handicap": s["asian_handicap"],
                    "over_under": s["over_under"],
                },
                "evidence": evidence,
                "identity_mapping": {
                    "status": m["status"],
                    "selection_rule": m.get("selection_rule"),
                    "hf_fixture_kickoff_utc": m["hf_kickoff_utc"],
                    "outcome_columns_read": False,
                    "status_columns_read": False,
                    "fuzzy_matching_used": False,
                },
                "predictions": {
                    "operational_control_s60": {
                        "probabilities": s60_p,
                        "top1": top1(s60_p),
                        "score_matrix_hash": s60.score_matrix_hash,
                        "activation_receipt": s60.feature_activation_receipt,
                        "component_chain": [dict(x) for x in s60.component_chain],
                    },
                    "mechanism_control_q": {
                        "probabilities": q_p,
                        "top1": top1(q_p),
                        "score_matrix_hash": matrix_hash(q_cells),
                        "detail": q_detail,
                    },
                    "ablation_q_u": {
                        "probabilities": u_p,
                        "top1": top1(u_p),
                        "score_matrix_hash": matrix_hash(u_cells),
                    },
                    "primary_candidate_q_u_y": {
                        "probabilities": y_p,
                        "top1": top1(y_p),
                        "score_matrix_hash": matrix_hash(y_cells),
                    },
                },
                "outcome_known_at_enrollment": False,
                "fresh_match_outcome_fields_read": False,
            }
            row["row_hash"] = stable_hash({k: v for k, v in row.items() if k != "row_hash"})
            new_rows.append(row)
        except Exception as exc:
            numerical_errors.append({"provider_event_id": event_id, "error": f"{type(exc).__name__}:{exc}"})

    combined = list(existing.get("rows") or []) + new_rows
    combined.sort(key=lambda r: (r["kickoff_utc"], str(r["provider_event_id"])))
    combined = combined[:TARGET]
    for i, row in enumerate(combined, 1):
        row["enrollment_index"] = i
    lockbox = {
        "schema_version": "football3-r43gov0-m11-fresh-forward-lockbox-v1",
        "status": "LOCKED_300_OUTCOME_BLIND" if len(combined) == TARGET else "ENROLLING_OUTCOME_BLIND",
        "prereg_lock_commit": PREREG_LOCK_COMMIT,
        "prereg_lock_utc": PREREG_LOCK_UTC.isoformat(),
        "prereg_sha256": sha256(PREREG),
        "target_rows": TARGET,
        "enrolled_rows": len(combined),
        "four_config_lockbox": ["operational_control_s60", "mechanism_control_q", "ablation_q_u", "primary_candidate_q_u_y"],
        "runtime_authority": authority_receipt(),
        "s60_history": history_receipt,
        "s60_fit_receipt": baseline.fit_receipt.to_dict(),
        "r43q_training_lock": q_train_receipt,
        "r43u_diagonal_factor": float(prereg["fixed_configs"]["ablation_q_u"]["r43u_diagonal_factor"]),
        "r43y_draw_logit_intercept": float(prereg["fixed_configs"]["primary_candidate_q_u_y"]["r43y_draw_logit_intercept"]),
        "governance": {
            "fresh_forward": True,
            "selection_may_use_outcomes": False,
            "selection_may_use_postmatch_data": False,
            "prediction_cutoff": "T_minus_60_minutes",
            "prediction_cutoff_tolerance_minutes": 0,
            "atomic_market_same_snapshot": True,
            "fresh_outcomes_read": False,
            "lineup_numeric_1x2_enabled": False,
            "player_technical_numeric_1x2_enabled": False,
            "head_coach_numeric_1x2_enabled": False,
            "availability_numeric_1x2_enabled": False,
            "retune_on_this_300": False,
        },
        "rows": combined,
    }
    lockbox["lockbox_hash"] = stable_hash({k: v for k, v in lockbox.items() if k != "lockbox_hash"})
    LOCKBOX.parent.mkdir(parents=True, exist_ok=True)
    LOCKBOX.write_text(json.dumps(lockbox, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    gate = {
        "schema_version": "football3-r43gov0-m11-fresh-forward-enrollment-gate-v1",
        "status": "PASS_PREDICTIONS_LOCKED_OUTCOME_BLIND" if new_rows else ("PASS_NO_NEW_ROWS" if combined else "BLOCKED_NO_LOCKED_ROWS"),
        "probe_status": probe_gate.get("status"),
        "probe_candidates": len(candidates),
        "safe_identity_mapped": len(mapped_candidates),
        "new_rows_locked": len(new_rows),
        "total_enrolled_rows": len(combined),
        "target_rows": TARGET,
        "late_or_cutoff_rejections": late,
        "numerical_error_count": len(numerical_errors),
        "numerical_errors": numerical_errors,
        "mapping_unresolved_count": mapping_receipt["unresolved_count"],
        "all_locked_rows_have_four_configs": all(set(r["predictions"]) == {"operational_control_s60", "mechanism_control_q", "ablation_q_u", "primary_candidate_q_u_y"} for r in combined),
        "all_locked_rows_market_at_or_before_T60": all(iso(r["market_observed_at_utc"]) <= iso(r["prediction_cutoff_utc"]) for r in combined),
        "all_locked_rows_prediction_at_or_before_T60": all(iso(r["prediction_frozen_at_utc"]) <= iso(r["prediction_cutoff_utc"]) for r in combined),
        "fresh_outcomes_read": False,
        "lockbox_hash": lockbox["lockbox_hash"],
        "mapping_receipt": mapping_receipt,
        "s60_history": history_receipt,
        "r43q_training_lock": q_train_receipt,
    }
    if numerical_errors:
        gate["status"] = "WARN_PARTIAL_NUMERICAL_REJECTIONS" if new_rows else "BLOCKED_NUMERICAL"
    if not gate["all_locked_rows_have_four_configs"] or not gate["all_locked_rows_market_at_or_before_T60"] or not gate["all_locked_rows_prediction_at_or_before_T60"]:
        gate["status"] = "BLOCKED_CONTRACT_VIOLATION"
    (OUTDIR / "gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: gate[k] for k in (
        "status", "probe_candidates", "safe_identity_mapped", "new_rows_locked", "total_enrolled_rows",
        "mapping_unresolved_count", "numerical_error_count", "all_locked_rows_have_four_configs",
        "all_locked_rows_market_at_or_before_T60", "all_locked_rows_prediction_at_or_before_T60")}, ensure_ascii=False, indent=2))
    return gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-root", required=True)
    parser.add_argument("--r43q-source", required=True)
    args = parser.parse_args()
    gate = run(Path(args.probe_root), Path(args.r43q_source))
    return 0 if str(gate.get("status", "")).startswith("PASS") or str(gate.get("status", "")).startswith("WARN") else 2


if __name__ == "__main__":
    raise SystemExit(main())
