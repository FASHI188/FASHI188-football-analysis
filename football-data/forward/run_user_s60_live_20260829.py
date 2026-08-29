#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments"
R24 = EXP / "top1_r24_history_scale_curve"
R23 = EXP / "top1_r23_fresh_40k_confirmation"
R17 = EXP / "top1_r17_fresh_forward"
for p in (R24, R23, R17):
    sys.path.insert(0, str(p))

import run_experiment_r24 as r24  # noqa: E402
import run_experiment_r23 as r23  # noqa: E402
import run_experiment_r17 as r17  # noqa: E402

r9 = r24.r9

HISTORY_N = 60000
CLASSIFIER_TRAIN_N = 24123
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
TOP1 = {0: "home", 1: "draw", 2: "away"}
FORBIDDEN_TARGET_FIELDS = {
    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR", "RESULT", "SCORE",
    "HOME_GOALS", "AWAY_GOALS", "HOME_XG", "AWAY_XG",
    "MARKET_SNAPSHOT", "ODDS", "LINEUP_EVIDENCE",
}


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def validate_targets(payload: dict) -> list[dict]:
    rows = payload.get("fixtures")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("fixtures must be a non-empty list")
    out = []
    seen = set()
    for i, z in enumerate(rows, start=1):
        bad = FORBIDDEN_TARGET_FIELDS & {str(k).upper() for k in z}
        if bad:
            raise RuntimeError(f"target fixture contains forbidden fields: {sorted(bad)}")
        required = {"fixture_id", "division", "home", "away", "kickoff_utc"}
        missing = required - set(z)
        if missing:
            raise RuntimeError(f"target fixture missing fields: {sorted(missing)}")
        if z["fixture_id"] in seen:
            raise RuntimeError(f"duplicate fixture_id: {z['fixture_id']}")
        seen.add(z["fixture_id"])
        row = dict(z)
        row["batch_index"] = i
        out.append(row)
    return out


def historical_rows(work: Path, cutoff: pd.Timestamp) -> tuple[list[dict], dict]:
    fp = work / "fixtures.parquet"
    sp = work / "match_stats.parquet"
    r9.download(r9.FIX_URL, fp)
    r9.download(r9.STAT_URL, sp)
    fix_sha = fsha(fp)
    stat_sha = fsha(sp)

    fx = pd.read_parquet(
        fp,
        columns=[
            "id", "date_utc", "league_id", "home_team_id", "away_team_id",
            "goals_home", "goals_away", "status_norm", "is_played",
        ],
    )
    st = pd.read_parquet(
        sp,
        columns=[
            "fixture_id", "home_xg", "away_xg", "xg_covered", "xg_nulled", "known_at",
        ],
    )
    fx["kickoff"] = pd.to_datetime(fx["date_utc"], utc=True, errors="coerce")
    st["known"] = pd.to_datetime(st["known_at"], utc=True, errors="coerce")
    fx = fx[
        (fx["is_played"] == True)
        & (fx["status_norm"] == "FT")
        & fx["goals_home"].notna()
        & fx["goals_away"].notna()
        & fx["kickoff"].notna()
        & (fx["kickoff"] < cutoff)
    ]
    st = st[
        (st["xg_covered"] == True)
        & (st["xg_nulled"] == False)
        & st["home_xg"].notna()
        & st["away_xg"].notna()
        & st["known"].notna()
        & (st["known"] < cutoff)
    ]
    df = fx.merge(st, left_on="id", right_on="fixture_id", how="inner", validate="one_to_one")
    df = df[
        (df["known"] > df["kickoff"])
        & df["home_xg"].between(0, 6)
        & df["away_xg"].between(0, 6)
    ].copy()
    df["date"] = df["kickoff"].dt.date.astype(str)
    df = df.sort_values(["kickoff", "id"], kind="mergesort").drop_duplicates("id")
    eligible = len(df)
    if eligible < HISTORY_N:
        raise RuntimeError(f"strict-prior historical rows {eligible} < required {HISTORY_N}")
    df = df.tail(HISTORY_N)

    out = []
    for z in df.itertuples(index=False):
        out.append(
            {
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
            }
        )
    if len({x["game_id"] for x in out}) != HISTORY_N:
        raise RuntimeError("historical fixture identity duplicate")
    meta = {
        "fixtures_sha256": fix_sha,
        "match_stats_sha256": stat_sha,
        "eligible_strict_prior_rows": int(eligible),
        "selected_history_rows": len(out),
        "history_first_date": out[0]["date"],
        "history_last_date": out[-1]["date"],
        "known_cutoff_utc": cutoff.isoformat(),
    }
    fp.unlink(missing_ok=True)
    sp.unlink(missing_ok=True)
    return out, meta


def build_history(rows: list[dict]):
    state = r9.S()
    pred = []
    by = defaultdict(list)
    for x in rows:
        by[x["date"]].append(x)
    for day in sorted(by):
        pending = []
        for x in sorted(by[day], key=lambda z: z["game_id"]):
            raw = state.pred(x)
            pred.append({"date": day, "game_id": x["game_id"], "y": r9.actual(x), "raw": raw})
            pending.append((x, raw))
        # Keep the frozen S60 same-date anti-leakage rule.
        for x, raw in pending:
            state.update(x, raw)
    return pred, state


def load_metadata(work: Path):
    tp = work / "teams.parquet"
    lp = work / "leagues.parquet"
    r9.download(f"{HF}/teams.parquet?download=true", tp)
    r9.download(f"{HF}/leagues.parquet?download=true", lp)
    team_sha, league_sha = fsha(tp), fsha(lp)
    teams = pd.read_parquet(tp)
    leagues = pd.read_parquet(lp)
    tp.unlink(missing_ok=True)
    lp.unlink(missing_ok=True)
    return teams, leagues, team_sha, league_sha


def map_targets(targets: list[dict], history: list[dict], teams, leagues):
    divs = tuple(sorted({x["division"] for x in targets}))
    cmap = {div: r23.comp_id(leagues, div) for div in divs}
    fallback = r17.team_index(teams, set())
    allowed = defaultdict(set)
    for x in history:
        allowed[x["competition_id"]].add(x["home_team"])
        allowed[x["competition_id"]].add(x["away_team"])
    primary = {div: r17.team_index(teams, allowed[cmap[div][0]]) for div in divs}

    mapped = []
    audit = []
    for z in targets:
        div = z["division"]
        cid = cmap[div][0]
        hid, hc = r17.resolve_team(z["home"], primary[div], fallback)
        aid, ac = r17.resolve_team(z["away"], primary[div], fallback)
        audit.append(
            {
                "fixture_id": z["fixture_id"],
                "division": div,
                "home": z["home"],
                "home_id": hid,
                "home_candidates": hc,
                "away": z["away"],
                "away_id": aid,
                "away_candidates": ac,
            }
        )
        if hid is None or aid is None:
            continue
        mapped.append(
            {
                "batch_index": z["batch_index"],
                "fixture_id": z["fixture_id"],
                "kickoff_utc": z["kickoff_utc"],
                "division": div,
                "competition_id": cid,
                "home": z["home"],
                "away": z["away"],
                "home_team": hid,
                "away_team": aid,
                # Dummy target fields are required by the state interface but are never read by pred().
                "home_goals": 0,
                "away_goals": 0,
                "home_xg": 0.0,
                "away_xg": 0.0,
            }
        )
    return mapped, audit, cmap


def predict(model, state, mapped: list[dict]):
    rows = []
    for x in sorted(mapped, key=lambda z: z["batch_index"]):
        raw = state.pred(x)
        p = r23.pred(model, raw)
        rows.append(
            {
                "batch_index": x["batch_index"],
                "fixture_id": x["fixture_id"],
                "kickoff_utc": x["kickoff_utc"],
                "division": x["division"],
                "home": x["home"],
                "away": x["away"],
                "home_team_id": x["home_team"],
                "away_team_id": x["away_team"],
                "p_home": float(p["p_home"]),
                "p_draw": float(p["p_draw"]),
                "p_away": float(p["p_away"]),
                "top1": TOP1[int(p["top1"])],
                "raw_state": {
                    "mu_home": float(raw["mu_home"]),
                    "mu_away": float(raw["mu_away"]),
                    "xg_mu_home": float(raw["xg_mu_home"]),
                    "xg_mu_away": float(raw["xg_mu_away"]),
                    "home_history": int(raw["home_history"]),
                    "away_history": int(raw["away_history"]),
                    "comp_history": int(raw["comp_history"]),
                },
            }
        )
    return rows


def main() -> int:
    input_path = Path(os.environ.get("S60_LIVE_INPUT", "football-data/forward/inbox/user_s60_live_20260829.json"))
    out_dir = Path(os.environ.get("S60_LIVE_OUT", "s60_live_out"))
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    targets = validate_targets(payload)
    cutoff = pd.Timestamp(payload["freeze_time_utc"])
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    for z in targets:
        ko = pd.Timestamp(z["kickoff_utc"])
        if ko.tzinfo is None:
            ko = ko.tz_localize("UTC")
        else:
            ko = ko.tz_convert("UTC")
        if ko <= cutoff:
            raise RuntimeError(f"target not future at freeze: {z['fixture_id']}")

    work = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    history, source_meta = historical_rows(work, cutoff)
    hist_pred, state = build_history(history)
    if len(hist_pred) != HISTORY_N:
        raise RuntimeError("history prediction row count drift")
    train = hist_pred[-CLASSIFIER_TRAIN_N:]
    if len(train) != CLASSIFIER_TRAIN_N:
        raise RuntimeError("classifier training row count drift")
    model = r24.model(train)

    teams, leagues, team_sha, league_sha = load_metadata(work)
    mapped, audit, cmap = map_targets(targets, history, teams, leagues)
    unresolved = [x for x in audit if x["home_id"] is None or x["away_id"] is None]
    if unresolved:
        (out_dir / "mapping_diagnostic.json").write_text(
            json.dumps({"status": "FAIL_UNRESOLVED_TARGET_IDENTITIES", "unresolved": unresolved}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"target identity mapping incomplete: mapped={len(mapped)} unresolved={len(unresolved)}")
    if len(mapped) != len(targets):
        raise RuntimeError("mapped target row count drift")

    rows = predict(model, state, mapped)
    report = {
        "schema_version": "football3-user-s60-live-pure-model-v1",
        "status": "S60_LIVE_PURE_MODEL_COMPLETE",
        "classification": "TRUE_PROSPECTIVE_ZERO_TARGET_LABEL_PURE_MODEL",
        "model_definition": {
            "name": "S60",
            "state_history_rows": HISTORY_N,
            "classifier_training_rows": CLASSIFIER_TRAIN_N,
            "feature_head": "R9b K1",
            "classifier": "StandardScaler + multinomial LogisticRegression(C=0.5, random_state=0)",
            "architecture_source": "frozen Batch-001 S60 line / R24-R25-R27",
            "manual_probability_adjustment": False,
            "market_probability_fallback": False,
        },
        "information_boundary": {
            "freeze_time_utc": cutoff.isoformat(),
            "target_result_fields_loaded": False,
            "target_xg_loaded": False,
            "target_market_prices_loaded": False,
            "target_lineup_evidence_loaded": False,
            "target_rows_used_to_update_state": False,
        },
        "source_meta": {
            **source_meta,
            "teams_sha256": team_sha,
            "leagues_sha256": league_sha,
        },
        "history_contract": {
            "strict_prior_rows": True,
            "historical_xg_known_before_freeze_required": True,
            "same_date_history_results_withheld_until_all_same_date_raw_predictions_built": True,
            "history_rows": HISTORY_N,
            "classifier_train_rows": CLASSIFIER_TRAIN_N,
            "classifier_train_first_date": train[0]["date"],
            "classifier_train_last_date": train[-1]["date"],
        },
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in cmap.items()},
        "rows": rows,
        "mapping_audit": audit,
    }
    out_path = out_dir / "s60_live_predictions.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "status": report["status"],
        "rows": len(rows),
        "freeze_time_utc": cutoff.isoformat(),
        "history_first_date": source_meta["history_first_date"],
        "history_last_date": source_meta["history_last_date"],
        "classifier_train_first_date": train[0]["date"],
        "classifier_train_last_date": train[-1]["date"],
        "top1_picks": {k: sum(x["top1"] == k for x in rows) for k in ("home", "draw", "away")},
        "predictions_sha256": fsha(out_path),
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
