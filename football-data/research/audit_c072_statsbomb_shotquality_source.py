#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA_VERSION = "C072_STATSBOMB_SHOTQUALITY_SOURCE_AUDIT_V1"
REPO = "hudl/open-data"
COMMIT = "b0bc9f22dd77c206ddedc1d742893b3bbe64baec"
TREE = "bad02895ec3616202e076d9d96adcef48dc7c134"
SEED = "C072_STATSBOMB_SHOTQUALITY_20260818"
SAMPLE_N = 300
THRESHOLDS = [4, 6, 8, 10, 15, 20]

RAW = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/"
API_TREE = f"https://api.github.com/repos/{REPO}/git/trees/{TREE}?recursive=1"


def get_json(url: str, retries: int = 5):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-research-c072"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            time.sleep(min(8, 1 + i * 2))
    raise RuntimeError(f"GET failed {url}: {type(last).__name__}: {last}")


def pick(obj, path, default=None):
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    competitions = get_json(RAW + "data/competitions.json")
    tree = get_json(API_TREE)
    if tree.get("truncated"):
        raise RuntimeError("Git tree truncated; cannot certify full event-file coverage")
    event_ids = set()
    match_paths = set()
    for item in tree.get("tree", []):
        p = item.get("path", "")
        if p.startswith("data/events/") and p.endswith(".json"):
            try: event_ids.add(int(Path(p).stem))
            except Exception: pass
        if p.startswith("data/matches/") and p.endswith(".json"):
            match_paths.add(p)

    meta = []
    missing_match_files = []
    comp_lookup = {}
    for c in competitions:
        cid, sid = int(c["competition_id"]), int(c["season_id"])
        path = f"data/matches/{cid}/{sid}.json"
        comp_lookup[(cid, sid)] = c
        if path not in match_paths:
            missing_match_files.append(path)
            continue
        matches = get_json(RAW + path)
        for m in matches:
            # Outcome keys are intentionally never referenced.
            mid = int(m["match_id"])
            meta.append({
                "match_id": mid,
                "competition_id": cid,
                "season_id": sid,
                "competition_name": c.get("competition_name"),
                "competition_gender": c.get("competition_gender"),
                "competition_international": bool(c.get("competition_international")),
                "season_name": c.get("season_name"),
                "match_date": str(m.get("match_date")),
                "kick_off": str(m.get("kick_off")),
                "home_team_id": int(pick(m, "home_team.home_team_id")),
                "away_team_id": int(pick(m, "away_team.away_team_id")),
                "has_event_file": mid in event_ids,
            })
    frame = pd.DataFrame(meta)
    if frame.empty or frame.match_id.duplicated().any():
        raise RuntimeError("empty or duplicate match metadata")
    frame["date"] = pd.to_datetime(frame.match_date, errors="raise").dt.date
    frame = frame.sort_values(["date", "match_id"]).reset_index(drop=True)
    event_frame = frame[frame.has_event_file].copy()

    # Chronological prior event-match counts. Same calendar date never updates another target.
    team_hist = defaultdict(int)
    counts = []
    for date, group in frame.groupby("date", sort=True):
        for idx, r in group.iterrows():
            counts.append((idx, team_hist[int(r.home_team_id)], team_hist[int(r.away_team_id)]))
        for _, r in group.iterrows():
            if bool(r.has_event_file):
                team_hist[int(r.home_team_id)] += 1
                team_hist[int(r.away_team_id)] += 1
    hc = pd.Series({idx: h for idx, h, a in counts}); ac = pd.Series({idx: a for idx, h, a in counts})
    frame["home_prior_event_matches"] = hc.reindex(frame.index).astype(int)
    frame["away_prior_event_matches"] = ac.reindex(frame.index).astype(int)

    elig = {}
    for n in THRESHOLDS:
        mask = frame.has_event_file & (frame.home_prior_event_matches >= n) & (frame.away_prior_event_matches >= n)
        f = frame[mask]
        elig[str(n)] = {
            "eligible_targets": int(len(f)),
            "calendar_years": sorted(int(y) for y in pd.to_datetime(f.match_date).dt.year.unique()),
            "year_count": int(pd.to_datetime(f.match_date).dt.year.nunique()),
            "competition_season_count": int(f[["competition_id", "season_id"]].drop_duplicates().shape[0]),
            "competition_count": int(f.competition_id.nunique()),
            "gender_counts": f.competition_gender.value_counts(dropna=False).to_dict(),
            "date_min": str(f.date.min()) if len(f) else None,
            "date_max": str(f.date.max()) if len(f) else None,
        }
        if n == 8:
            f[["match_id","competition_id","season_id","competition_name","competition_gender","season_name","match_date","kick_off","home_team_id","away_team_id","home_prior_event_matches","away_prior_event_matches"]].to_csv(out / "threshold8_identity_manifest.csv", index=False)

    # Deterministic field audit on metadata/event intersection. No shot outcome field is used.
    sample_ids = sorted(
        event_frame.match_id.astype(int).tolist(),
        key=lambda mid: hashlib.sha256(f"{SEED}|{mid}".encode()).hexdigest(),
    )[:min(SAMPLE_N, len(event_frame))]
    sample = []
    total_shots = total_xg = 0
    matches_with_shots = 0
    matches_xg_complete = 0
    field_counts = defaultdict(int)
    xg_vals = []
    for j, mid in enumerate(sample_ids, 1):
        events = get_json(RAW + f"data/events/{mid}.json")
        shots = [e for e in events if pick(e, "type.name") == "Shot"]
        matches_with_shots += int(bool(shots))
        xg_n = 0
        for e in shots:
            xg = pick(e, "shot.statsbomb_xg")
            total_shots += 1
            if xg is not None:
                total_xg += 1; xg_n += 1; xg_vals.append(float(xg))
            for name, path in {
                "statsbomb_xg":"shot.statsbomb_xg",
                "location":"location",
                "body_part":"shot.body_part.name",
                "technique":"shot.technique.name",
                "shot_type":"shot.type.name",
                "under_pressure":"under_pressure",
                "play_pattern":"play_pattern.name",
            }.items():
                if pick(e, path) is not None: field_counts[name] += 1
        matches_xg_complete += int(bool(shots) and xg_n == len(shots))
        sample.append({"match_id":mid,"shot_rows":len(shots),"xg_nonnull":xg_n})
        if j % 50 == 0: print(f"sample events {j}/{len(sample_ids)}", flush=True)

    sample_df = pd.DataFrame(sample)
    sample_df.to_csv(out / "event_field_sample.csv", index=False)
    by_domain = frame.groupby(["competition_id","season_id","competition_name","competition_gender","season_name"], dropna=False, as_index=False).agg(
        metadata_matches=("match_id","size"), event_matches=("has_event_file","sum"), date_min=("date","min"), date_max=("date","max")
    )
    by_domain["event_rate"] = by_domain.event_matches / by_domain.metadata_matches
    by_domain.to_csv(out / "competition_season_coverage.csv", index=False)

    rate_xg = total_xg / total_shots if total_shots else 0.0
    rate_shot_matches = matches_with_shots / len(sample_ids) if sample_ids else 0.0
    gate = {
        "intersection_ge_2500": len(event_frame) >= 2500,
        "sample_xg_nonnull_rate_ge_098": rate_xg >= .98,
        "sample_matches_with_shots_ge_095": rate_shot_matches >= .95,
        "threshold8_targets_ge_1500": elig["8"]["eligible_targets"] >= 1500,
        "threshold8_at_least_3_years": elig["8"]["year_count"] >= 3,
    }
    passed = all(gate.values())
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "C072_SOURCE_AUDIT_COMPLETE",
        "verdict": "C072_STATSBOMB_SHOTQUALITY_SOURCE_GATE_PASS" if passed else "C072_STATSBOMB_SHOTQUALITY_SOURCE_GATE_STOP",
        "source": {"repo":REPO,"commit":COMMIT,"tree":TREE,"competition_season_rows":len(competitions),"match_files_in_tree":len(match_paths),"event_files_in_tree":len(event_ids)},
        "metadata": {"matches":int(len(frame)),"matches_with_event_file":int(len(event_frame)),"event_file_rate":float(len(event_frame)/len(frame)),"missing_match_file_paths":missing_match_files},
        "chronological_eligibility": elig,
        "sample_field_audit": {
            "sample_matches":len(sample_ids),"matches_with_shots":matches_with_shots,"matches_with_shots_rate":rate_shot_matches,
            "matches_all_shots_xg_complete":matches_xg_complete,"shot_rows":total_shots,"shot_xg_nonnull":total_xg,"shot_xg_nonnull_rate":rate_xg,
            "field_nonnull_counts":dict(field_counts),
            "xg_distribution": {"n":len(xg_vals),"mean":float(np.mean(xg_vals)) if xg_vals else None,"sd":float(np.std(xg_vals)) if xg_vals else None,"q25":float(np.quantile(xg_vals,.25)) if xg_vals else None,"q50":float(np.quantile(xg_vals,.5)) if xg_vals else None,"q75":float(np.quantile(xg_vals,.75)) if xg_vals else None,"max":float(np.max(xg_vals)) if xg_vals else None},
            "shot_outcome_field_accessed":False,
        },
        "gate": {**gate,"pass":passed},
        "boundary": {"target_result_model_fit":False,"pt_scoring":False,"hyperparameter_search":False,"fresh_confirmation_claim_allowed":False,"formal_weight":0,"C071_confirmation72180_opened":False,"C070F_confirmation1597_opened":False,"A05_opened":False,"protected_opened":False},
    }
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
