#!/usr/bin/env python3
"""Batch-001 historical S60 replay through the unified runtime chain.

The immutable cohort and frozen R24 60k history artifact are evidence/data inputs
only. Numerical prediction never imports or calls R24/R23/R17. Each target passes
through TeamIdentityResolver -> shared PIT store -> FeatureAssembler ->
UnifiedInferenceEngine and emits an activation receipt.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
import urllib.request
from collections import defaultdict

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from identity.team_identity import RESOLVED, TeamIdentityResolver
from pipeline.runtime_authority import authority_receipt, build_operational_s60_engine
from pipeline.s60_numerical_baseline import CLASSIFIER_TRAIN_ROWS, HISTORY_ROWS, S60NumericalBaseline
from pipeline.unified_inference import FixtureRequest
from pit.feature_store import PointInTimeFeatureStore

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
LOCK = ROOT / "experiments" / "batch001_stage2_historical_s60_replay" / "batch_lock_artifact" / "batch001_locked_100.json"
BASE = ROOT / "experiments" / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
EXTRA = ROOT / "experiments" / "top1_r25_fresh_s60_confirmation" / "r24_artifact" / "data" / "extra_r24_xg_60000.csv"
OUT = ROOT / "runtime" / "results" / "batch001_unified_s60_replay"
BASE_SHA256 = "6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba"
EXTRA_SHA256 = "477d1a4f542850e4e2981b98acbbbba0c261b14f37fc0f5f618d5cb1234452bc"
TEAM_ID_OVERRIDES = {("E0", "Arsenal"): "3", ("F1", "Lyon"): "224"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-unified-replay/1"})
    with urllib.request.urlopen(req, timeout=300) as response, path.open("wb") as fh:
        for block in iter(lambda: response.read(1 << 20), b""):
            fh.write(block)


def alias_key(value: object) -> str:
    token = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().casefold()
    for item in ("football club", " fc", "fc ", " afc", "cf ", " cf", "&", ".", "-", "'", "/"):
        token = token.replace(item, " ")
    token = re.sub(r"\s+", " ", token).strip()
    fixed = {
        "wolverhampton wanderers": "wolves", "brighton hove albion": "brighton",
        "tottenham hotspur": "tottenham", "bayern munchen": "bayern munich",
        "borussia monchengladbach": "borussia m gladbach", "internazionale": "inter",
        "olympique marseille": "marseille", "olympique de marseille": "marseille",
        "olympique lyonnais": "lyon", "koln": "cologne", "1 koln": "cologne",
    }
    return fixed.get(token, token)


def text_columns(df) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype == object]


def load_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for x in csv.DictReader(fh):
            x["home_goals"] = int(x["home_goals"])
            x["away_goals"] = int(x["away_goals"])
            x["home_xg"] = float(x["home_xg"])
            x["away_xg"] = float(x["away_xg"])
            rows.append(x)
    return rows


def load_lock() -> dict:
    payload = json.loads(LOCK.read_text(encoding="utf-8"))
    if payload.get("status") != "LOCKED" or len(payload.get("rows") or []) != 100:
        raise RuntimeError("Batch-001 cohort lock mismatch")
    if payload.get("governance", {}).get("outcome_columns_read") is not False:
        raise RuntimeError("Batch-001 cohort was not outcome-blind")
    return payload


def competition_map(leagues, divisions: set[str]) -> dict[str, str]:
    cols = text_columns(leagues)
    out = {}
    for division in sorted(divisions):
        hits = []
        for row in leagues.itertuples(index=False):
            d = row._asdict()
            texts = [str(d.get(c) or "").strip() for c in cols]
            if any(t.casefold() == division.casefold() for t in texts):
                hits.append(str(int(d["id"])))
        unique = sorted(set(hits))
        if len(unique) != 1:
            raise RuntimeError(f"competition mapping ambiguous {division}: {unique}")
        out[division] = unique[0]
    return out


def build_resolver(teams, fixtures, cmap: dict[str, str], provenance: str) -> TeamIdentityResolver:
    """Use exact provider metadata, scoped to teams observed in each competition.

    The scope comes only from fixture league/team IDs; no result/status fields are
    loaded. This prevents unrelated clubs with the same provider alias from
    contaminating another division namespace while preserving fail-closed alias
    conflicts within the actual competition.
    """
    name_cols = [c for c in text_columns(teams) if c in {"name", "fd_name"} or "name" in c.casefold()]
    team_meta = {}
    for row in teams.itertuples(index=False):
        d = row._asdict(); tid = str(int(d["id"])); aliases = set()
        for col in name_cols:
            value = d.get(col)
            if value is not None and str(value).casefold() != "nan":
                key = alias_key(value)
                if key:
                    aliases.add(key)
        team_meta[tid] = aliases

    allowed_by_div = {}
    league_as_str = fixtures["league_id"].astype(str)
    for division, cid in cmap.items():
        q = fixtures[league_as_str == str(cid)]
        allowed_by_div[division] = set(q["home_team_id"].astype(str)) | set(q["away_team_id"].astype(str))

    records = []
    for division, allowed in allowed_by_div.items():
        ns = f"fd:{division.casefold()}"
        for tid in sorted(allowed):
            if tid not in team_meta:
                continue
            records.append({"source_namespace": ns, "source_team_id": tid, "canonical_team_id": tid,
                            "mapping_method": "provider_team_id_competition_scoped", "provenance_hash": provenance})
            for alias in sorted(team_meta[tid]):
                records.append({"source_namespace": ns, "approved_name_alias": alias, "canonical_team_id": tid,
                                "mapping_method": "provider_metadata_exact_alias_competition_scoped", "provenance_hash": provenance})
    return TeamIdentityResolver(records)


def resolve_targets(lock: dict, work: Path):
    tp, lp, fp = work / "teams.parquet", work / "leagues.parquet", work / "fixtures_safe.parquet"
    download(f"{HF}/teams.parquet?download=true", tp)
    download(f"{HF}/leagues.parquet?download=true", lp)
    download(f"{HF}/fixtures.parquet?download=true", fp)
    teams, leagues = pd.read_parquet(tp), pd.read_parquet(lp)
    fixtures = pd.read_parquet(fp, columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id"])
    fixtures["date_utc"] = pd.to_datetime(fixtures["date_utc"], utc=True)
    divisions = {str(x["division"]) for x in lock["rows"]}
    cmap = competition_map(leagues, divisions)
    resolver = build_resolver(teams, fixtures, cmap, sha256(tp))
    mapped, audit = [], []
    for z in lock["rows"]:
        div = str(z["division"]); ns = f"fd:{div.casefold()}"
        hid_override = TEAM_ID_OVERRIDES.get((div, str(z["home"])))
        aid_override = TEAM_ID_OVERRIDES.get((div, str(z["away"])))
        hr = resolver.resolve(ns, hid_override, alias_key(z["home"])) if hid_override else resolver.resolve(ns, None, alias_key(z["home"]))
        ar = resolver.resolve(ns, aid_override, alias_key(z["away"])) if aid_override else resolver.resolve(ns, None, alias_key(z["away"]))
        rec = {"batch_index": z["batch_index"], "division": div, "date": z["date"], "home": z["home"], "away": z["away"],
               "home_resolution": hr.to_dict(), "away_resolution": ar.to_dict(),
               "home_override_used": hid_override is not None, "away_override_used": aid_override is not None}
        if hr.status != RESOLVED or ar.status != RESOLVED:
            audit.append(rec); continue
        d0 = pd.Timestamp(z["date"], tz="UTC")
        match = fixtures[(fixtures["league_id"].astype(str) == cmap[div])
                         & (fixtures["home_team_id"].astype(str) == hr.canonical_team_id)
                         & (fixtures["away_team_id"].astype(str) == ar.canonical_team_id)
                         & (fixtures["date_utc"] >= d0 - pd.Timedelta(days=1))
                         & (fixtures["date_utc"] < d0 + pd.Timedelta(days=2))]
        rec["fixture_candidates"] = [{"id": str(int(x.id)), "date_utc": x.date_utc.isoformat()} for x in match.itertuples(index=False)]
        audit.append(rec)
        if len(match) != 1:
            continue
        x = next(match.itertuples(index=False))
        mapped.append({**z, "fixture_id": str(int(x.id)), "kickoff_utc": x.date_utc.isoformat(),
                       "competition_id": cmap[div], "home_team": hr.canonical_team_id,
                       "away_team": ar.canonical_team_id,
                       "nominal_cutoff_utc": (x.date_utc - pd.Timedelta(hours=24)).isoformat()})
    for p in (tp, lp, fp): p.unlink(missing_ok=True)
    if len(mapped) != 100:
        unresolved = [r for r in audit if r["home_resolution"]["status"] != RESOLVED or r["away_resolution"]["status"] != RESOLVED or len(r.get("fixture_candidates", [])) != 1]
        diagnostic = {
            "schema_version": "football3-batch001-unified-mapping-diagnostic-v2",
            "status": "FAIL_MAPPING_INCOMPLETE",
            "mapped": len(mapped), "expected": 100, "unresolved_count": len(unresolved),
            "competition_map": cmap, "unresolved": unresolved, "all_audit": audit,
            "identity_scope": "provider_team_ids_observed_in_safe_fixture_metadata_for_each_competition",
            "outcome_columns_read": False, "status_columns_read": False, "fuzzy_matching_enabled": False,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "mapping_diagnostic.json").write_text(json.dumps(diagnostic, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise RuntimeError(f"unified target mapping incomplete {len(mapped)}/100")
    return mapped, audit, cmap, resolver


def load_pool() -> list[dict]:
    if sha256(BASE) != BASE_SHA256:
        raise RuntimeError("R9b base snapshot hash mismatch")
    if sha256(EXTRA) != EXTRA_SHA256:
        raise RuntimeError("R24 60k history artifact hash mismatch")
    base, extra = load_csv(BASE), load_csv(EXTRA)
    if len(base) != 20000 or len(extra) != 60000:
        raise RuntimeError(f"history source count mismatch base={len(base)} extra={len(extra)}")
    if {x["game_id"] for x in base} & {x["game_id"] for x in extra}:
        raise RuntimeError("base/extra history identity overlap")
    pool = extra + base
    for x in pool:
        x["_known"] = pd.to_datetime(x["xg_known_at"], utc=True)
    return pool


def window_at(pool: list[dict], cutoff: pd.Timestamp) -> list[dict]:
    eligible = [x for x in pool if x["_known"] < cutoff]
    eligible.sort(key=lambda x: (x["date"], x["game_id"]))
    if len(eligible) < HISTORY_ROWS:
        raise RuntimeError(f"strict-prior S60 history {len(eligible)} < {HISTORY_ROWS}")
    return [{k: v for k, v in x.items() if k != "_known"} for x in eligible[-HISTORY_ROWS:]]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "_work"; work.mkdir(parents=True, exist_ok=True)
    lock = load_lock()
    targets, mapping_audit, cmap, resolver = resolve_targets(lock, work)
    pool = load_pool()
    by_date = defaultdict(list)
    for z in targets:
        by_date[z["date"]].append(z)
    predictions, date_audit = [], []
    for day in sorted(by_date):
        group = sorted(by_date[day], key=lambda z: z["batch_index"])
        cutoff = min(pd.to_datetime(z["nominal_cutoff_utc"], utc=True) for z in group)
        history = window_at(pool, cutoff)
        baseline = S60NumericalBaseline.fit_from_history(history)
        pit_store = PointInTimeFeatureStore()
        engine = build_operational_s60_engine(resolver, pit_store, baseline)
        date_audit.append({"date": day, "matches": len(group), "effective_cutoff_utc": cutoff.isoformat(),
                           "history_rows": len(history), "classifier_training_rows": CLASSIFIER_TRAIN_ROWS,
                           "history_first_date": history[0]["date"], "history_last_date": history[-1]["date"]})
        for z in group:
            ns = f"fd:{str(z['division']).casefold()}"
            request = FixtureRequest(fixture_id=z["fixture_id"], as_of=cutoff.to_pydatetime(),
                                     home_source_namespace=ns, home_source_team_id=z["home_team"], home_source_name=z["home"],
                                     away_source_namespace=ns, away_source_team_id=z["away_team"], away_source_name=z["away"])
            result = engine.predict("replay", request, {"competition_id": z["competition_id"], "target_date": z["date"]})
            predictions.append({"batch_index": z["batch_index"], "date": z["date"], "division": z["division"],
                                "home": z["home"], "away": z["away"], "fixture_id": z["fixture_id"],
                                "kickoff_utc": z["kickoff_utc"], "effective_cutoff_utc": cutoff.isoformat(),
                                "p_home": result.probabilities["home"], "p_draw": result.probabilities["draw"],
                                "p_away": result.probabilities["away"], "top1": result.top1,
                                "score_matrix_hash": result.score_matrix_hash,
                                "activation_receipt": result.feature_activation_receipt,
                                "component_chain": [dict(x) for x in result.component_chain]})
    predictions.sort(key=lambda x: x["batch_index"])
    report = {"schema_version": "football3-batch001-unified-s60-replay-v1",
              "status": "PREDICTIONS_LOCKED_UNIFIED_S60_BASELINE_ONLY",
              "cohort_sha256": lock["cohort_sha256"], "rows": len(predictions),
              "runtime_authority": authority_receipt(),
              "unified_contract": {"team_identity_resolver_used": True, "shared_pit_store_used": True,
                                   "feature_assembler_used": True, "unified_inference_engine_used": True,
                                   "activation_receipt_emitted_per_fixture": True,
                                   "direct_r24_r23_r17_runtime_calls": False,
                                   "lineup_numeric_1x2_enabled": False, "player_technical_numeric_1x2_enabled": False,
                                   "head_coach_numeric_1x2_enabled": False, "availability_numeric_1x2_enabled": False},
              "history_sources": {"base_sha256": BASE_SHA256, "extra_sha256": EXTRA_SHA256},
              "competition_map": cmap, "date_replay_audit": date_audit,
              "mapping_audit": mapping_audit, "predictions": predictions}
    (OUT / "batch001_unified_s60_predictions_locked.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    receipt = {"status": report["status"], "rows": len(predictions), "cohort_sha256": lock["cohort_sha256"],
               "canonical_operational_baseline": "S60", "unified_pipeline": True,
               "activation_receipts": sum(1 for x in predictions if x["activation_receipt"].get("receipt_hash"))}
    (OUT / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
