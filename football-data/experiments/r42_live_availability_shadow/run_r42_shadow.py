#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
import urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R40C_DIR = HERE.parent / "top1_r40c_role_aware_expected_xi"
PIT_ROOT = HERE.parents[1] / "prematch_pit"
sys.path.insert(0, str(R40C_DIR))
import run_experiment_r40c as r40c  # noqa: E402

r9 = r40c.r9
r33 = r40c.r33

HF = "https://huggingface.co/datasets/eatpizzanot/soccer-dataset/resolve/main"
FIXTURES_URL = f"{HF}/fixtures.parquet?download=true"
TEAMS_URL = f"{HF}/teams.parquet?download=true"
PLAYERS_URL = f"{HF}/players.parquet?download=true"
EXPECTED_FIXTURES_SHA256 = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
TARGET = {
    "competition": "Premier League",
    "home_team": "Crystal Palace",
    "away_team": "Manchester City",
    "kickoff_at_utc": "2026-08-28T19:00:00Z",
}
LEDGER_PATH = PIT_ROOT / "ledger" / "prematch_events_v2.jsonl"


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "football3-r42-live-shadow"})
    with urllib.request.urlopen(req, timeout=300) as r, path.open("wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)


def norm_text(x: str) -> str:
    s = unicodedata.normalize("NFKD", str(x or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def compact(x: str) -> str:
    return norm_text(x).replace(" ", "")


def replay_history():
    rows = r9.load()
    player_map, player_sha, matched_starters, fixture_players_path = r40c.download_player_rows(rows)
    base = r9.S()
    states = defaultdict(r40c.TeamState)
    ledger = r40c.Ledger()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            raw = base.pred(row)
            cf = r40c.context_features(row, states, ledger)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "context_features": cf})
            pending.append((row, raw))
        for row, raw in pending:
            fid = str(row["game_id"])
            hi = player_map.get((fid, row["home_team"]), [])
            ai = player_map.get((fid, row["away_team"]), [])
            y = r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"])
            ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                ledger.update(
                    hi,
                    hu - he,
                    float(row["home_xg"]) - float(raw["xg_mu_home"]),
                    float(row["away_xg"]) - float(raw["xg_mu_away"]),
                )
                states[row["home_team"]].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                ledger.update(
                    ai,
                    au - ae,
                    float(row["away_xg"]) - float(raw["xg_mu_away"]),
                    float(row["home_xg"]) - float(raw["xg_mu_home"]),
                )
                states[row["away_team"]].xis.append(frozenset(pid for pid, _ in ai))
            base.update(row, raw)
    return rows, pred, base, states, ledger, {
        "fixture_players_sha256": player_sha,
        "matched_starter_rows": matched_starters,
        "fixture_players_path": str(fixture_players_path),
    }


def load_target_fixture(tmp: Path):
    fp = tmp / "fixtures.parquet"
    tp = tmp / "teams.parquet"
    download(FIXTURES_URL, fp)
    download(TEAMS_URL, tp)
    fixture_sha = fsha(fp)
    if fixture_sha != EXPECTED_FIXTURES_SHA256:
        raise RuntimeError(f"fixtures source drift: {fixture_sha}")
    teams = pd.read_parquet(tp, columns=["id", "name"])
    name_to_ids = defaultdict(list)
    id_to_name = {}
    for x in teams.itertuples(index=False):
        name_to_ids[norm_text(x.name)].append(int(x.id))
        id_to_name[int(x.id)] = str(x.name)
    hids = name_to_ids[norm_text(TARGET["home_team"])]
    aids = name_to_ids[norm_text(TARGET["away_team"])]
    if len(hids) != 1 or len(aids) != 1:
        raise RuntimeError(f"target team identity not unique: home={hids} away={aids}")
    h, a = hids[0], aids[0]
    fx = pd.read_parquet(fp, columns=["id", "date_utc", "league_id", "home_team_id", "away_team_id", "status_norm", "is_played"])
    fx["dt"] = pd.to_datetime(fx["date_utc"], utc=True)
    target_dt = pd.Timestamp(TARGET["kickoff_at_utc"])
    hit = fx[(fx["home_team_id"] == h) & (fx["away_team_id"] == a) & (fx["dt"] == target_dt)]
    if len(hit) != 1:
        nearby = fx[(fx["home_team_id"] == h) & (fx["away_team_id"] == a)].sort_values("dt")
        raise RuntimeError(f"target fixture not uniquely found at kickoff; candidates={nearby[['id','date_utc','league_id']].tail(5).to_dict('records')}")
    x = hit.iloc[0]
    row = {
        "date": target_dt.date().isoformat(),
        "game_id": str(int(x["id"])),
        "competition_id": str(int(x["league_id"])),
        "home_team": str(h),
        "away_team": str(a),
    }
    return row, {
        "fixture_id": int(x["id"]),
        "league_id": int(x["league_id"]),
        "home_team_id": h,
        "away_team_id": a,
        "home_team_name": id_to_name[h],
        "away_team_name": id_to_name[a],
        "date_utc": target_dt.isoformat(),
        "status_norm_in_source": str(x["status_norm"]),
        "is_played_in_source": bool(x["is_played"]),
        "fixtures_sha256": fixture_sha,
        "teams_sha256": fsha(tp),
    }, fp, tp


def read_pit_events():
    if not LEDGER_PATH.is_file():
        raise RuntimeError(f"missing PIT ledger: {LEDGER_PATH}")
    out = []
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        ev = rec.get("event") or {}
        gate = rec.get("gate") or {}
        if not gate.get("eligible"):
            continue
        if ev.get("event_type") != "player_availability":
            continue
        if ev.get("home_team") != TARGET["home_team"] or ev.get("away_team") != TARGET["away_team"]:
            continue
        if ev.get("kickoff_at_utc") != TARGET["kickoff_at_utc"]:
            continue
        out.append(rec)
    if not out:
        raise RuntimeError("no eligible live availability observations for target fixture")
    return out


def load_player_names(tmp: Path):
    p = tmp / "players.parquet"
    download(PLAYERS_URL, p)
    df = pd.read_parquet(p, columns=["id", "name"])
    by_id = {}
    by_surname = defaultdict(list)
    by_compact = defaultdict(list)
    for x in df.itertuples(index=False):
        pid = int(x.id)
        name = str(x.name)
        by_id[pid] = name
        toks = norm_text(name).split()
        if toks:
            by_surname[toks[-1]].append(pid)
        by_compact[compact(name)].append(pid)
    return by_id, by_surname, by_compact, fsha(p), p


def recent_team_players(st):
    return set(pid for xi in st.xis for pid in xi)


def resolve_player(name: str, team_recent: set[int], ledger, by_id, by_surname, by_compact):
    live_norm = norm_text(name)
    live_compact = compact(name)
    toks = live_norm.split()
    surname = toks[-1] if toks else ""
    candidate_ids = set(by_compact.get(live_compact, []))
    candidate_ids.update(by_surname.get(surname, []))
    scored = []
    for pid in candidate_ids:
        cand = by_id[pid]
        c_norm = norm_text(cand)
        c_compact = compact(cand)
        c_toks = c_norm.split()
        score = SequenceMatcher(None, live_compact, c_compact).ratio()
        if c_compact == live_compact:
            score += 1.0
        if c_toks and toks and c_toks[-1] == toks[-1]:
            score += 0.25
            if c_toks[0][:1] == toks[0][:1]:
                score += 0.15
        if pid in team_recent:
            score += 0.75
        n = int(ledger.n.get(pid, 0))
        if n > 0:
            score += min(0.25, math.log1p(n) / 25.0)
        scored.append((score, n, pid, cand))
    scored.sort(reverse=True)
    if not scored:
        return {"resolved": False, "reason": "NO_NAME_CANDIDATE", "live_name": name, "candidates": []}
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None
    margin = top[0] - second[0] if second else 999.0
    resolved = top[0] >= 0.75 and (margin >= 0.08 or top[2] in team_recent)
    return {
        "resolved": bool(resolved),
        "live_name": name,
        "player_id": int(top[2]) if resolved else None,
        "dataset_name": top[3] if resolved else None,
        "score": float(top[0]),
        "margin_to_second": float(margin),
        "in_recent_team_xis": bool(top[2] in team_recent),
        "ledger_matches": int(top[1]),
        "candidates": [
            {"player_id": int(x[2]), "name": x[3], "score": float(x[0]), "ledger_matches": int(x[1]), "in_recent_team_xis": bool(x[2] in team_recent)}
            for x in scored[:5]
        ],
    }


def ranked_expected(st):
    xs = list(st.xis)
    if len(xs) < r40c.MIN_PRIOR_XI:
        return [], []
    raw = defaultdict(float)
    total = 0.0
    for lag, xi in enumerate(reversed(xs)):
        w = r40c.DECAY ** lag
        total += w
        for pid in xi:
            raw[pid] += w
    ranked = sorted(raw.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    return [pid for pid, _ in ranked[:11]], [(pid, float(w / total if total else 0.0)) for pid, w in ranked]


def positional_side_from_pids(pids, ledger):
    buckets = {role: [] for role in r40c.ROLES}
    known = 0
    for pid in pids:
        role = ledger.last_role.get(pid)
        if role in r40c.ROLES:
            known += 1
            buckets[role].append(ledger.values(pid)[0])
    out = {"role_known_share": known / len(pids) if pids else 0.0}
    for role in r40c.ROLES:
        vals = buckets[role]
        out[f"{role}_result"] = float(np.mean(vals)) if vals else 0.0
    return out


def positional_context(home_pids, away_pids, ledger):
    h = positional_side_from_pids(home_pids, ledger)
    a = positional_side_from_pids(away_pids, ledger)
    z = {
        "home_role_known_share": h["role_known_share"],
        "away_role_known_share": a["role_known_share"],
        "role_known_share_diff": h["role_known_share"] - a["role_known_share"],
    }
    for role in r40c.ROLES:
        z[f"home_{role}_result"] = h[f"{role}_result"]
        z[f"away_{role}_result"] = a[f"{role}_result"]
        z[f"diff_{role}_result"] = h[f"{role}_result"] - a[f"{role}_result"]
    return z


def apply_exclusions(expected, ranked, excluded, ledger):
    selected = list(expected)
    changes = []
    for pid in excluded:
        if pid not in selected:
            changes.append({"excluded_player_id": pid, "hit_expected_xi": False, "replacement_player_id": None})
            continue
        role = ledger.last_role.get(pid)
        selected.remove(pid)
        replacement = None
        for cand, _ in ranked:
            if cand in selected or cand in excluded:
                continue
            if role and ledger.last_role.get(cand) == role:
                replacement = cand
                break
        if replacement is None:
            for cand, _ in ranked:
                if cand not in selected and cand not in excluded:
                    replacement = cand
                    break
        if replacement is not None:
            selected.append(replacement)
        changes.append({
            "excluded_player_id": pid,
            "excluded_role": role,
            "hit_expected_xi": True,
            "replacement_player_id": replacement,
            "replacement_role": ledger.last_role.get(replacement) if replacement is not None else None,
        })
    return selected, changes


def proba(model, vector):
    arr = model.predict_proba([vector])[0]
    classes = list(model[-1].classes_)
    v = np.zeros(3, dtype=float)
    for cls, p in zip(classes, arr):
        v[int(cls)] = float(p)
    return {"home": float(v[0]), "draw": float(v[1]), "away": float(v[2]), "top1": ["home", "draw", "away"][int(np.argmax(v))]}


def run():
    tmp = HERE / "data"
    tmp.mkdir(parents=True, exist_ok=True)
    rows, pred, base, states, ledger, hist_meta = replay_history()
    target_row, fixture_meta, fp, tp = load_target_fixture(tmp)
    player_by_id, by_surname, by_compact, players_sha, master_players_path = load_player_names(tmp)
    events = read_pit_events()

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train = pred[b1:b2]
    k1 = r33.baseline_model(train)
    role_model = r40c.fit_model(train, r40c.POSITIONAL_RESULT_NAMES)

    home_expected, home_ranked = ranked_expected(states[target_row["home_team"]])
    away_expected, away_ranked = ranked_expected(states[target_row["away_team"]])
    if len(home_expected) < 10 or len(away_expected) < 10:
        raise RuntimeError(f"insufficient strict-prior expected XI history: home={len(home_expected)} away={len(away_expected)}")

    resolutions = []
    confirmed_exclusions = {"home": set(), "away": set()}
    doubtful_exclusions = {"home": set(), "away": set()}
    for rec in events:
        ev = rec["event"]
        side = ev["team_side"]
        name = ev["player"].get("player_name") or str(ev["player"].get("player_id") or "")
        st = states[target_row[f"{side}_team"]]
        resolved = resolve_player(name, recent_team_players(st), ledger, player_by_id, by_surname, by_compact)
        status = ev["availability_status"]
        pid = resolved.get("player_id")
        if resolved["resolved"] and pid is not None:
            if status in {"out", "suspended"}:
                confirmed_exclusions[side].add(int(pid))
            elif status == "doubtful":
                doubtful_exclusions[side].add(int(pid))
        resolutions.append({
            "record_id": rec["record_id"],
            "team_side": side,
            "availability_status": status,
            "observed_at_utc": ev["observed_at_utc"],
            "source_name": ev["source_name"],
            "resolution": resolved,
            "role": ledger.last_role.get(pid) if pid is not None else None,
            "player_strength": (
                {"result": ledger.values(pid)[0], "attack": ledger.values(pid)[1], "defense": ledger.values(pid)[2], "matches": ledger.values(pid)[3]}
                if pid is not None and resolved["resolved"] else None
            ),
            "in_strict_prior_expected_xi": bool(pid in (home_expected if side == "home" else away_expected)) if pid is not None else False,
        })

    home_conf, home_changes = apply_exclusions(home_expected, home_ranked, confirmed_exclusions["home"], ledger)
    away_conf, away_changes = apply_exclusions(away_expected, away_ranked, confirmed_exclusions["away"], ledger)
    home_all, home_all_changes = apply_exclusions(home_conf, home_ranked, doubtful_exclusions["home"], ledger)
    away_all, away_all_changes = apply_exclusions(away_conf, away_ranked, doubtful_exclusions["away"], ledger)

    raw = base.pred(target_row)
    base_prob = proba(k1, list(r9.feat_k1(raw)))
    pre_cf = positional_context(home_expected, away_expected, ledger)
    conf_cf = positional_context(home_conf, away_conf, ledger)
    all_cf = positional_context(home_all, away_all, ledger)
    pre_prob = proba(role_model, list(r9.feat_k1(raw)) + [pre_cf[n] for n in r40c.POSITIONAL_RESULT_NAMES])
    conf_prob = proba(role_model, list(r9.feat_k1(raw)) + [conf_cf[n] for n in r40c.POSITIONAL_RESULT_NAMES])
    all_prob = proba(role_model, list(r9.feat_k1(raw)) + [all_cf[n] for n in r40c.POSITIONAL_RESULT_NAMES])

    def delta(a, b):
        return {k: float(b[k] - a[k]) for k in ("home", "draw", "away")}

    summary = {
        "schema_version": "football3-r42-live-availability-shadow-v1",
        "status": "COMPLETE",
        "classification": "LIVE_PREMATCH_MECHANISM_SHADOW_NOT_FORMAL_PREDICTION_NOT_VALIDATION",
        "formal_weight": 0,
        "target": TARGET,
        "governance": {
            "result_label_accessed": False,
            "target_current_match_xi_used": False,
            "target_postmatch_data_used": False,
            "only_gate_eligible_pit_events_used": True,
            "historical_player_updates_strictly_after_historical_predictions": True,
            "probability_layer": "R40C positional-result challenger which failed formal historical-test logloss gate; shifts are diagnostic only",
            "formal_probability_change_allowed": False,
        },
        "source": {
            "history": hist_meta,
            "target_fixture": fixture_meta,
            "players_sha256": players_sha,
            "pit_ledger": str(LEDGER_PATH),
            "eligible_target_events": len(events),
        },
        "entity_resolution": resolutions,
        "strict_prior_expected_xi": {
            "home_player_ids": home_expected,
            "away_player_ids": away_expected,
            "home_known_names": [player_by_id.get(pid) for pid in home_expected],
            "away_known_names": [player_by_id.get(pid) for pid in away_expected],
        },
        "availability_scenarios": {
            "confirmed_out_only": {
                "home_excluded_ids": sorted(confirmed_exclusions["home"]),
                "away_excluded_ids": sorted(confirmed_exclusions["away"]),
                "home_changes": home_changes,
                "away_changes": away_changes,
            },
            "confirmed_out_plus_doubtful_as_out": {
                "home_doubtful_ids": sorted(doubtful_exclusions["home"]),
                "away_doubtful_ids": sorted(doubtful_exclusions["away"]),
                "home_incremental_changes": home_all_changes,
                "away_incremental_changes": away_all_changes,
            },
        },
        "shadow_probabilities": {
            "K1_without_live_player_layer": base_prob,
            "R40C_before_live_availability": pre_prob,
            "R42_confirmed_out_only": conf_prob,
            "R42_confirmed_out_plus_doubtful_as_out": all_prob,
            "delta_confirmed_out_vs_R40C_pre": delta(pre_prob, conf_prob),
            "delta_all_out_vs_R40C_pre": delta(pre_prob, all_prob),
        },
        "feature_deltas": {
            "confirmed_out_minus_pre": {n: float(conf_cf[n] - pre_cf[n]) for n in r40c.POSITIONAL_RESULT_NAMES},
            "all_out_minus_pre": {n: float(all_cf[n] - pre_cf[n]) for n in r40c.POSITIONAL_RESULT_NAMES},
        },
        "limitations": [
            "The frozen historical source snapshot predates the target match and can miss summer transfers or role changes in the expected-XI history.",
            "An 'available' event does not imply a player starts; available players are not forced into the XI.",
            "A 'doubtful' event is not treated as absent in the confirmed-out scenario; a separate worst-case scenario is shown.",
            "R40C was not formally promoted, so these probability shifts are mechanism diagnostics only and must not be presented as validated betting probabilities.",
        ],
        "next_research_gate": "Accumulate future PIT observations and confirmed XI outcomes, then evaluate whether availability-adjusted expected-XI features improve locked pre-match predictions out of sample. Do not fit absence weights on this single live match.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r42_shadow.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for p in (fp, tp, master_players_path, Path(hist_meta["fixture_players_path"])):
        try:
            p.unlink()
        except Exception:
            pass
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r42_shadow.json").read_text(encoding="utf-8"))
    assert s["status"] == "COMPLETE"
    assert s["classification"] == "LIVE_PREMATCH_MECHANISM_SHADOW_NOT_FORMAL_PREDICTION_NOT_VALIDATION"
    g = s["governance"]
    assert not g["result_label_accessed"] and not g["target_current_match_xi_used"] and not g["target_postmatch_data_used"]
    assert g["only_gate_eligible_pit_events_used"] and not g["formal_probability_change_allowed"]
    assert s["source"]["eligible_target_events"] >= 5
    assert s["source"]["target_fixture"]["is_played_in_source"] is False
    print("R42_SHADOW_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_r42_shadow.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
