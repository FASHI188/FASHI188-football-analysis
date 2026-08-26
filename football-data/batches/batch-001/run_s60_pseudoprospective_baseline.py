#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments"
R24 = EXP / "top1_r24_history_scale_curve"
R23 = EXP / "top1_r23_fresh_40k_confirmation"
R18 = EXP / "top1_r18_fresh_efl"
R17 = EXP / "top1_r17_fresh_forward"
for p in (R24, R23, R18, R17):
    sys.path.insert(0, str(p))

import run_experiment_r24 as r24  # noqa: E402
import run_experiment_r23 as r23  # noqa: E402
import run_experiment_r18 as r18  # noqa: E402
import run_experiment_r17 as r17  # noqa: E402

r9 = r24.r9

EXPECTED_LOCK_SHA256 = "028f6b1e3443bbd144a04941e2764365b861fcf2af6cca81402c1cc29ffa84a6"
HISTORY_N = 60000
CLASSIFIER_TRAIN_N = 24123
# The first locked target is 2024-08-15. Because the lock carries local clock time but
# not an authoritative timezone, use a deliberately conservative batch-wide cutoff.
# This is earlier than T-24h for the first target and prevents accidental boundary leakage.
HISTORY_KNOWN_CUTOFF_UTC = pd.Timestamp("2024-08-14T00:00:00Z")
TARGET_MIN_DATE = "2024-08-15"
DIVS = ("E0", "SP1", "I1", "D1", "F1")
HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FORBIDDEN = {"FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR", "RESULT", "SCORE"}
TOP1 = {0: "home", 1: "draw", 2: "away"}


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def read_lock(path: Path) -> list[dict[str, str]]:
    if fsha(path) != EXPECTED_LOCK_SHA256:
        raise RuntimeError("Batch-001 zero-label lock SHA256 mismatch")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = set(reader.fieldnames or [])
        if FORBIDDEN & {x.upper() for x in header}:
            raise RuntimeError("forbidden result field entered target lock")
        rows = list(reader)
    if len(rows) != 100:
        raise RuntimeError(f"expected 100 target identities, got {len(rows)}")
    if min(x["match_date_iso"] for x in rows) != TARGET_MIN_DATE:
        raise RuntimeError("target minimum date drift")
    return rows


def historical_rows(work: Path) -> tuple[list[dict], dict]:
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
        & (fx["kickoff"] < HISTORY_KNOWN_CUTOFF_UTC)
    ]
    st = st[
        (st["xg_covered"] == True)
        & (st["xg_nulled"] == False)
        & st["home_xg"].notna()
        & st["away_xg"].notna()
        & st["known"].notna()
        & (st["known"] < HISTORY_KNOWN_CUTOFF_UTC)
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
        "known_cutoff_utc": HISTORY_KNOWN_CUTOFF_UTC.isoformat(),
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
        # Same-date outcomes never feed another match on the same date.
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


def map_targets(lock: list[dict[str, str]], history: list[dict], teams, leagues):
    cmap = {div: r23.comp_id(leagues, div) for div in DIVS}
    fallback = r17.team_index(teams, set())
    allowed = defaultdict(set)
    for x in history:
        allowed[x["competition_id"]].add(x["home_team"])
        allowed[x["competition_id"]].add(x["away_team"])

    primary = {div: r17.team_index(teams, allowed[cmap[div][0]]) for div in DIVS}
    mapped = []
    audit = []
    for z in lock:
        div = z["Div"]
        cid = cmap[div][0]
        hid, hc = r17.resolve_team(z["HomeTeam"], primary[div], fallback)
        aid, ac = r17.resolve_team(z["AwayTeam"], primary[div], fallback)
        audit.append(
            {
                "batch_index": int(z["batch_index"]),
                "division": div,
                "home": z["HomeTeam"],
                "home_id": hid,
                "home_candidates": hc,
                "away": z["AwayTeam"],
                "away_id": aid,
                "away_candidates": ac,
            }
        )
        if hid is None or aid is None:
            continue
        mapped.append(
            {
                "batch_index": int(z["batch_index"]),
                "match_key_sha256": z["match_key_sha256"],
                "date": z["match_date_iso"],
                "time_local_source": z["Time"],
                "division": div,
                "competition_id": cid,
                "home": z["HomeTeam"],
                "away": z["AwayTeam"],
                "home_team": hid,
                "away_team": aid,
                # Deliberate dummy values: S60 prediction does not read target outcomes/xG.
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
                "match_key_sha256": x["match_key_sha256"],
                "date": x["date"],
                "time_local_source": x["time_local_source"],
                "division": x["division"],
                "home": x["home"],
                "away": x["away"],
                "home_team_id": x["home_team"],
                "away_team_id": x["away_team"],
                "p_home": p["p_home"],
                "p_draw": p["p_draw"],
                "p_away": p["p_away"],
                "top1": TOP1[int(p["top1"])],
                "raw_state": {
                    "mu_home": raw["mu_home"],
                    "mu_away": raw["mu_away"],
                    "xg_mu_home": raw["xg_mu_home"],
                    "xg_mu_away": raw["xg_mu_away"],
                    "home_history": raw["home_history"],
                    "away_history": raw["away_history"],
                    "comp_history": raw["comp_history"],
                },
            }
        )
    return rows


def main() -> int:
    lock_path = Path(os.environ["BATCH001_LOCK"])
    out_dir = Path(os.environ.get("BATCH001_S60_OUT", "batch001_s60_out"))
    work = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    lock = read_lock(lock_path)
    history, source_meta = historical_rows(work)
    hist_pred, state = build_history(history)
    if len(hist_pred) != HISTORY_N:
        raise RuntimeError("history prediction row count drift")
    train = hist_pred[-CLASSIFIER_TRAIN_N:]
    if len(train) != CLASSIFIER_TRAIN_N:
        raise RuntimeError("classifier training row count drift")
    model = r24.model(train)

    teams, leagues, team_sha, league_sha = load_metadata(work)
    mapped, audit, cmap = map_targets(lock, history, teams, leagues)
    unresolved = [x for x in audit if x["home_id"] is None or x["away_id"] is None]
    if unresolved:
        (out_dir / "mapping_diagnostic.json").write_text(
            json.dumps({"status": "FAIL_UNRESOLVED_TARGET_IDENTITIES", "mapped": len(mapped), "unresolved": unresolved}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"target identity mapping incomplete: mapped={len(mapped)} unresolved={len(unresolved)}")
    if len(mapped) != 100:
        raise RuntimeError(f"expected 100 mapped targets, got {len(mapped)}")

    rows = predict(model, state, mapped)
    picks = {k: sum(x["top1"] == k for x in rows) for k in ("home", "draw", "away")}
    payload = {
        "schema_version": "football3-batch001-s60-pseudoprospective-baseline-v1",
        "status": "S60_BASELINE_LOCKED_NO_TARGET_LABELS",
        "classification": "RETROSPECTIVE_PSEUDO_PROSPECTIVE_ZERO_TARGET_LABEL_BASELINE",
        "batch_lock_sha256": EXPECTED_LOCK_SHA256,
        "model_definition": {
            "name": "S60",
            "state_history_rows": HISTORY_N,
            "classifier_training_rows": CLASSIFIER_TRAIN_N,
            "feature_head": "R9b K1",
            "classifier": "StandardScaler + multinomial LogisticRegression(C=0.5, random_state=0)",
            "architecture_source": "R24/R25/R27 S60 line",
        },
        "information_boundary": {
            "batch_target_min_date": TARGET_MIN_DATE,
            "historical_known_cutoff_utc": HISTORY_KNOWN_CUTOFF_UTC.isoformat(),
            "cutoff_reason": "conservative batch-wide cutoff because target lock local times lack authoritative timezone",
            "target_result_fields_loaded": False,
            "target_xg_loaded": False,
            "target_market_prices_loaded": False,
            "target_rows_used_to_update_state": False,
            "earlier_batch_target_results_used_for_later_targets": False,
        },
        "source_meta": {
            **source_meta,
            "teams_sha256": team_sha,
            "leagues_sha256": league_sha,
        },
        "history_contract": {
            "strict_prior_rows": True,
            "historical_xg_known_before_cutoff_required": True,
            "same_date_history_results_withheld_until_all_same_date_raw_predictions_built": True,
            "history_rows": HISTORY_N,
            "classifier_train_rows": CLASSIFIER_TRAIN_N,
            "classifier_train_first_date": train[0]["date"],
            "classifier_train_last_date": train[-1]["date"],
        },
        "competition_map": {k: {"id": v[0], "texts": v[1]} for k, v in cmap.items()},
        "mapping_coverage": len(mapped) / len(lock),
        "top1_picks": picks,
        "rows": rows,
        "mapping_audit": audit,
    }
    out_path = out_dir / "batch001_s60_baseline_predictions.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "status": payload["status"],
        "rows": len(rows),
        "mapping_coverage": payload["mapping_coverage"],
        "top1_picks": picks,
        "history_first_date": source_meta["history_first_date"],
        "history_last_date": source_meta["history_last_date"],
        "classifier_train_first_date": train[0]["date"],
        "classifier_train_last_date": train[-1]["date"],
        "predictions_sha256": fsha(out_path),
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        work.rmdir()
    except OSError:
        pass
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
