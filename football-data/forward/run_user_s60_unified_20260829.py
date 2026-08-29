#!/usr/bin/env python3
"""Unified zero-label S60 live runtime.

All predictions pass through TeamIdentityResolver -> shared PIT store ->
FeatureAssembler -> UnifiedInferenceEngine -> activation receipt.  The runner never
imports or calls R24/R23/R17 and never accepts target outcomes, xG, market prices,
or lineup/personnel inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import urllib.request

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from identity.team_identity import TeamIdentityResolver
from pipeline.runtime_authority import build_operational_s60_engine, authority_receipt
from pipeline.s60_numerical_baseline import HISTORY_ROWS, CLASSIFIER_TRAIN_ROWS, S60NumericalBaseline
from pipeline.unified_inference import FixtureRequest
from pit.feature_store import PointInTimeFeatureStore

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIX_URL = f"{HF}/fixtures.parquet?download=true"
STAT_URL = f"{HF}/match_stats.parquet?download=true"
FORBIDDEN_TARGET_FIELDS = {
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR", "RESULT", "SCORE",
    "HOME_GOALS", "AWAY_GOALS", "HOME_XG", "AWAY_XG", "MARKET_SNAPSHOT",
    "ODDS", "LINEUP_EVIDENCE", "AVAILABILITY", "HEAD_COACH", "PLAYER_TECHNICAL",
}


def download(url: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "football3-unified-s60-runtime/1"})
    with urllib.request.urlopen(req, timeout=300) as response, path.open("wb") as fh:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            fh.write(block)


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


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


def validate_targets(payload: dict) -> list[dict]:
    rows = payload.get("fixtures")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("fixtures must be a non-empty list")
    seen = set()
    out = []
    for index, raw in enumerate(rows, start=1):
        bad = FORBIDDEN_TARGET_FIELDS & {str(k).upper() for k in raw}
        if bad:
            raise RuntimeError(f"target fixture contains forbidden fields: {sorted(bad)}")
        missing = {"fixture_id", "division", "home", "away", "kickoff_utc"} - set(raw)
        if missing:
            raise RuntimeError(f"target fixture missing fields: {sorted(missing)}")
        if raw["fixture_id"] in seen:
            raise RuntimeError(f"duplicate fixture_id: {raw['fixture_id']}")
        seen.add(raw["fixture_id"])
        out.append({**raw, "batch_index": index})
    return out


def historical_rows(work: Path, cutoff: pd.Timestamp) -> tuple[list[dict], dict]:
    fp, sp = work / "fixtures.parquet", work / "match_stats.parquet"
    download(FIX_URL, fp)
    download(STAT_URL, sp)
    fix_sha, stat_sha = fsha(fp), fsha(sp)
    fx = pd.read_parquet(fp, columns=[
        "id", "date_utc", "league_id", "home_team_id", "away_team_id",
        "goals_home", "goals_away", "status_norm", "is_played",
    ])
    st = pd.read_parquet(sp, columns=[
        "fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at",
    ])
    fx["kickoff"] = pd.to_datetime(fx["date_utc"], utc=True, errors="coerce")
    st["known"] = pd.to_datetime(st["known_at"], utc=True, errors="coerce")
    fx = fx[(fx["is_played"] == True) & (fx["status_norm"] == "FT")
            & fx["goals_home"].notna() & fx["goals_away"].notna()
            & fx["kickoff"].notna() & (fx["kickoff"] < cutoff)]
    st = st[(st["xg_covered"] == True) & (st["xg_nulled"] == False)
            & st["home_xg"].notna() & st["away_xg"].notna()
            & st["known"].notna() & (st["known"] < cutoff)]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df = df[(df["known"] > df["kickoff"]) & df["home_xg"].between(0, 6) & df["away_xg"].between(0, 6)].copy()
    df["date"] = df["kickoff"].dt.date.astype(str)
    df = df.sort_values(["kickoff", "id"], kind="mergesort").drop_duplicates("id")
    eligible = len(df)
    if eligible < HISTORY_ROWS:
        raise RuntimeError(f"strict-prior historical rows {eligible} < {HISTORY_ROWS}")
    df = df.tail(HISTORY_ROWS)
    rows = [{
        "date": z.date,
        "game_id": str(int(z.id)),
        "competition_id": str(int(z.league_id)),
        "home_team": str(int(z.home_team_id)),
        "away_team": str(int(z.away_team_id)),
        "home_goals": int(z.goals_home),
        "away_goals": int(z.goals_away),
        "home_xg": float(z.home_xg),
        "away_xg": float(z.away_xg),
        "xg_known_at": z.known.isoformat(),
    } for z in df.itertuples(index=False)]
    if len({x["game_id"] for x in rows}) != HISTORY_ROWS:
        raise RuntimeError("historical fixture identity duplicate")
    fp.unlink(missing_ok=True); sp.unlink(missing_ok=True)
    return rows, {
        "fixtures_sha256": fix_sha,
        "match_stats_sha256": stat_sha,
        "eligible_strict_prior_rows": int(eligible),
        "selected_history_rows": len(rows),
        "history_first_date": rows[0]["date"],
        "history_last_date": rows[-1]["date"],
        "known_cutoff_utc": cutoff.isoformat(),
    }


def text_columns(df) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c].dtype) or df[c].dtype == object]


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


def build_identity_resolver(teams, history: list[dict], cmap: dict[str, str], divisions: set[str], provenance: str):
    allowed = {}
    for division in divisions:
        cid = cmap[division]
        ids = set()
        for row in history:
            if row["competition_id"] == cid:
                ids.add(row["home_team"]); ids.add(row["away_team"])
        allowed[division] = ids
    name_cols = [c for c in text_columns(teams) if c in {"name", "fd_name"} or "name" in c.casefold()]
    records = []
    for row in teams.itertuples(index=False):
        d = row._asdict(); tid = str(int(d["id"]))
        for division in divisions:
            if tid not in allowed[division]:
                continue
            ns = f"fd:{division.casefold()}"
            records.append({
                "source_namespace": ns, "source_team_id": tid, "canonical_team_id": tid,
                "mapping_method": "provider_team_id", "provenance_hash": provenance,
            })
            aliases = set()
            for col in name_cols:
                value = d.get(col)
                if value is not None and str(value).casefold() != "nan":
                    aliases.add(alias_key(value))
            for alias in sorted(a for a in aliases if a):
                records.append({
                    "source_namespace": ns, "approved_name_alias": alias,
                    "canonical_team_id": tid, "mapping_method": "provider_metadata_explicit_alias",
                    "provenance_hash": provenance,
                })
    return TeamIdentityResolver(records)


def main() -> int:
    input_path = Path(os.environ.get("S60_LIVE_INPUT", "football-data/forward/inbox/user_s60_live_20260829.json"))
    out_dir = Path(os.environ.get("S60_LIVE_OUT", "s60_live_out"))
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    targets = validate_targets(payload)
    cutoff = pd.Timestamp(payload["freeze_time_utc"])
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    for target in targets:
        kickoff = pd.Timestamp(target["kickoff_utc"])
        kickoff = kickoff.tz_localize("UTC") if kickoff.tzinfo is None else kickoff.tz_convert("UTC")
        if kickoff <= cutoff:
            raise RuntimeError(f"target not future at freeze: {target['fixture_id']}")

    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"; work.mkdir(parents=True, exist_ok=True)
    history, source_meta = historical_rows(work, cutoff)
    baseline = S60NumericalBaseline.fit_from_history(history)

    tp, lp = work / "teams.parquet", work / "leagues.parquet"
    download(f"{HF}/teams.parquet?download=true", tp)
    download(f"{HF}/leagues.parquet?download=true", lp)
    team_sha, league_sha = fsha(tp), fsha(lp)
    teams, leagues = pd.read_parquet(tp), pd.read_parquet(lp)
    divisions = {str(x["division"]) for x in targets}
    cmap = competition_map(leagues, divisions)
    resolver = build_identity_resolver(teams, history, cmap, divisions, team_sha)
    tp.unlink(missing_ok=True); lp.unlink(missing_ok=True)

    pit_store = PointInTimeFeatureStore()
    engine = build_operational_s60_engine(resolver, pit_store, baseline)
    rows = []
    for target in sorted(targets, key=lambda x: x["batch_index"]):
        kickoff = pd.Timestamp(target["kickoff_utc"])
        kickoff = kickoff.tz_localize("UTC") if kickoff.tzinfo is None else kickoff.tz_convert("UTC")
        ns = f"fd:{str(target['division']).casefold()}"
        request = FixtureRequest(
            fixture_id=str(target["fixture_id"]),
            as_of=cutoff.to_pydatetime(),
            home_source_namespace=ns,
            home_source_team_id=None,
            home_source_name=alias_key(target["home"]),
            away_source_namespace=ns,
            away_source_team_id=None,
            away_source_name=alias_key(target["away"]),
        )
        result = engine.predict(
            "live", request,
            {"competition_id": cmap[target["division"]], "target_date": kickoff.date().isoformat()},
        )
        rows.append({
            "batch_index": target["batch_index"], "fixture_id": target["fixture_id"],
            "kickoff_utc": kickoff.isoformat(), "division": target["division"],
            "home": target["home"], "away": target["away"],
            "canonical_home_team_id": result.canonical_home_team_id,
            "canonical_away_team_id": result.canonical_away_team_id,
            "p_home": result.probabilities["home"], "p_draw": result.probabilities["draw"],
            "p_away": result.probabilities["away"], "top1": result.top1,
            "score_matrix_hash": result.score_matrix_hash,
            "activation_receipt": result.feature_activation_receipt,
            "component_chain": [dict(x) for x in result.component_chain],
        })

    report = {
        "schema_version": "football3-user-s60-live-unified-v1",
        "status": "S60_LIVE_UNIFIED_COMPLETE",
        "classification": "TRUE_PROSPECTIVE_ZERO_TARGET_LABEL_UNIFIED_PIPELINE",
        "runtime_authority": authority_receipt(),
        "model_definition": {
            "name": "S60", "state_history_rows": HISTORY_ROWS,
            "classifier_training_rows": CLASSIFIER_TRAIN_ROWS,
            "manual_probability_adjustment": False, "market_probability_fallback": False,
            "direct_r24_r23_r17_runtime_calls": False,
        },
        "information_boundary": {
            "freeze_time_utc": cutoff.isoformat(), "target_result_fields_loaded": False,
            "target_xg_loaded": False, "target_market_prices_loaded": False,
            "target_lineup_evidence_loaded": False, "target_rows_used_to_update_state": False,
        },
        "unified_contract": {
            "team_identity_resolver_used": True, "shared_pit_store_used": True,
            "feature_assembler_used": True, "unified_inference_engine_used": True,
            "activation_receipt_emitted_per_fixture": True,
            "lineup_numeric_1x2_enabled": False, "player_technical_numeric_1x2_enabled": False,
            "head_coach_numeric_1x2_enabled": False, "availability_numeric_1x2_enabled": False,
        },
        "source_meta": {**source_meta, "teams_sha256": team_sha, "leagues_sha256": league_sha},
        "identity_resolver": resolver.diagnostics(),
        "competition_map": cmap,
        "rows": rows,
    }
    (out_dir / "s60_live_predictions.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "status": report["status"], "rows": len(rows), "freeze_time_utc": cutoff.isoformat(),
        "canonical_operational_baseline": "S60", "unified_pipeline": True,
        "resolver_fingerprint": resolver.fingerprint,
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if work.exists() and not any(work.iterdir()):
        work.rmdir()
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
