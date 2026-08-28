#!/usr/bin/env python3
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R42H_DIR = ROOT / "football-data" / "experiments" / "r42h_player_technical_translation"
ENC = R42H_DIR / "encoded"
R42H_RUNNER = R42H_DIR / "run_r42h_player_technical_translation.py"
EXPECTED_R9B_SHA = "6ea5f6d98a6b43c1f34df58f08edfa52819415f79da88428947caae68d9170ba"
ALPHA = 0.5

# User ticket date is 2026-08-28 (+08 display). The underlying European fixtures are on 2026-08-27.
# Handicap annotations from the screenshot are intentionally ignored: 90-minute 1X2 only.
TARGETS = [
    {"ticket_code": "周四007", "home": "Celta Vigo", "away": "Osasuna", "home_team": "109", "away_team": "110", "competition": "La Liga", "competition_id": "5", "date": "2026-08-27"},
    {"ticket_code": "周四006", "home": "Anderlecht", "away": "Kairat Almaty", "home_team": "348", "away_team": "1885", "competition": "UEFA Europa League", "competition_id": "90", "date": "2026-08-27"},
    {"ticket_code": "周四005", "home": "Ferencvaros", "away": "Trabzonspor", "home_team": "1203", "away_team": "369", "competition": "UEFA Europa League", "competition_id": "90", "date": "2026-08-27"},
    {"ticket_code": "周四004", "home": "AGF Aarhus", "away": "Benfica", "home_team": "771", "away_team": "338", "competition": "UEFA Europa League", "competition_id": "90", "date": "2026-08-27"},
    {"ticket_code": "周四003", "home": "Salzburg", "away": "Mjallby", "home_team": "619", "away_team": "743", "competition": "UEFA Europa League", "competition_id": "90", "date": "2026-08-27"},
    {"ticket_code": "周四002", "home": "Omonia Nicosia", "away": "St Truiden", "home_team": "1182", "away_team": "361", "competition": "UEFA Europa League", "competition_id": "90", "date": "2026-08-27"},
    {"ticket_code": "周四001", "home": "Viktoria Plzen", "away": "Red Star Belgrade", "home_team": "1805", "away_team": "1305", "competition": "UEFA Europa League", "competition_id": "90", "date": "2026-08-27"},
]


def fsha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def reconstruct_r42h():
    b64 = "".join((ENC / f"run_r42h.py.gz.b64.part{i}").read_text(encoding="utf-8").strip() for i in (0, 1))
    src = gzip.decompress(base64.b64decode(b64))
    R42H_RUNNER.write_bytes(src)
    return hashlib.sha256(src).hexdigest()


def import_r42h():
    runner_sha = reconstruct_r42h()
    sys.path.insert(0, str(R42H_DIR))
    import run_r42h_player_technical_translation as h  # noqa: E402
    return h, runner_sha


def replay_and_fit(h):
    rows = h.r9.load()
    snapshot = ROOT / "football-data" / "experiments" / "top1_r9b_xg_hf" / "data" / "matches_r9b_xg_20000.csv"
    snap_sha = fsha(snapshot)
    if snap_sha != EXPECTED_R9B_SHA:
        raise RuntimeError(f"R9B snapshot drift: {snap_sha}")

    player_map, player_sha, matched_starters, player_path = h.r40c.download_player_rows(rows)
    stats_path = h.download_stats()
    tech_rows, tech_source = h.load_technical_rows(rows, Path(player_path), stats_path)

    base = h.r9.S()
    states = defaultdict(h.r40c.TeamState)
    base_ledger = h.r40c.Ledger()
    tech_ledger = h.TechnicalLedger()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)

    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda x: x["game_id"]):
            raw = base.pred(row)
            base_cf = h.r40c.context_features(row, states, base_ledger)
            tech_cf = h.live_technical_context(row, states, tech_ledger, base_ledger)
            pred.append({"date": day, "y": h.r9.actual(row), "raw": raw, "context_features": {**base_cf, **tech_cf}})
            pending.append((row, raw))

        # Strict same-date discipline: all target-date predictions would be frozen before any update.
        for row, raw in pending:
            fid = str(row["game_id"])
            htid, atid = str(row["home_team"]), str(row["away_team"])
            hi = player_map.get((fid, htid), [])
            ai = player_map.get((fid, atid), [])
            y = h.r9.actual(row)
            hu = 1.0 if y == 0 else 0.5 if y == 1 else 0.0
            au = 1.0 - hu
            he = float(raw["p_home"] + 0.5 * raw["p_draw"])
            ae = float(raw["p_away"] + 0.5 * raw["p_draw"])
            if hi:
                base_ledger.update(hi, hu - he, float(row["home_xg"]) - float(raw["xg_mu_home"]), float(row["away_xg"]) - float(raw["xg_mu_away"]))
                states[htid].xis.append(frozenset(pid for pid, _ in hi))
            if ai:
                base_ledger.update(ai, au - ae, float(row["away_xg"]) - float(raw["xg_mu_away"]), float(row["home_xg"]) - float(raw["xg_mu_home"]))
                states[atid].xis.append(frozenset(pid for pid, _ in ai))
            for rec in tech_rows.get((fid, htid), []):
                tech_ledger.update_row(rec)
            for rec in tech_rows.get((fid, atid), []):
                tech_ledger.update_row(rec)
            base.update(row, raw)

    b1 = h.r9.boundary(pred, h.r9.TARGET_BURN)
    b2 = h.r9.boundary(pred, b1 + h.r9.TARGET_TRAIN)
    train = pred[b1:b2]
    baseline_model = h.r40c.fit_model(train, h.BASE_NAMES)
    technical_model = h.r40c.fit_model(train, h.BASE_NAMES + h.TECH_NAMES)
    meta = {
        "r9b_snapshot_sha256": snap_sha,
        "fixture_players_sha256": player_sha,
        "fixture_players_stats_flat_sha256": h.EXPECTED_STATS_SHA256,
        "matched_starter_rows": int(matched_starters),
        "technical_source": tech_source,
        "train_rows": len(train),
        "train_end_date": max(x["date"] for x in train),
        "history_end_date": max(x["date"] for x in pred),
    }
    return base, states, base_ledger, tech_ledger, baseline_model, technical_model, meta


def prob_vec(p):
    return np.asarray([p["p_home"], p["p_draw"], p["p_away"]], dtype=float)


def decorate(v):
    v = np.asarray(v, dtype=float)
    v = np.clip(v, 1e-15, None)
    v = v / v.sum()
    labels = ["home", "draw", "away"]
    return {
        "home": float(v[0]),
        "draw": float(v[1]),
        "away": float(v[2]),
        "top1": labels[int(np.argmax(v))],
        "top1_probability": float(np.max(v)),
    }


def run():
    h, runner_sha = import_r42h()
    base, states, base_ledger, tech_ledger, baseline_model, technical_model, source_meta = replay_and_fit(h)
    results = []
    for i, t in enumerate(TARGETS, 1):
        row = {
            "date": t["date"],
            "game_id": f"trial7_{i}",
            "competition_id": t["competition_id"],
            "home_team": t["home_team"],
            "away_team": t["away_team"],
        }
        raw = base.pred(row)
        base_cf = h.r40c.context_features(row, states, base_ledger)
        tech_cf = h.live_technical_context(row, states, tech_ledger, base_ledger)
        p_base = h.model_prob(baseline_model, raw, base_cf, h.BASE_NAMES)
        full_cf = {**base_cf, **tech_cf}
        p_full = h.model_prob(technical_model, raw, full_cf, h.BASE_NAMES + h.TECH_NAMES)
        vb = prob_vec(p_base)
        vf = prob_vec(p_full)
        # Frozen R42K half-strength geometric shrink. No per-match search or manual draw manipulation.
        vh = np.exp((1.0 - ALPHA) * np.log(np.clip(vb, 1e-15, 1.0)) + ALPHA * np.log(np.clip(vf, 1e-15, 1.0)))
        vh /= vh.sum()
        hpids, hcert = h.r40c.expected_players(states[t["home_team"]])
        apids, acert = h.r40c.expected_players(states[t["away_team"]])
        results.append({
            **t,
            "baseline_r40c": decorate(vb),
            "full_r42h": decorate(vf),
            "r42k_half_shrink": decorate(vh),
            "technical_delta_half_minus_baseline": {
                "home": float(vh[0] - vb[0]), "draw": float(vh[1] - vb[1]), "away": float(vh[2] - vb[2])
            },
            "input_state": {
                "raw_mu_home": float(raw["mu_home"]),
                "raw_mu_away": float(raw["mu_away"]),
                "raw_mu_total": float(raw["mu_total"]),
                "home_history": int(raw["home_history"]),
                "away_history": int(raw["away_history"]),
                "competition_history": int(raw["comp_history"]),
                "home_expected_xi_available": bool(hpids),
                "away_expected_xi_available": bool(apids),
                "home_xi_certainty": float(hcert),
                "away_xi_certainty": float(acert),
                "tech_known_share_min": float(tech_cf["tech_known_share_min"]),
            },
        })

    payload = {
        "schema_version": "football3-trial7-r42k-pseudoprematch-20260828-v1",
        "status": "COMPLETE",
        "classification": "RETROSPECTIVE_STRICT_PREMATCH_PSEUDO_REPLAY_RESULT_BLINDED",
        "formal_weight": 0,
        "model": "R40C expected-XI baseline + R42H technical translation + frozen R42K alpha=0.5 geometric shrink",
        "governance": {
            "ticket_date": "2026-08-28",
            "target_match_date_utc_or_europe": "2026-08-27",
            "target_results_used": False,
            "target_confirmed_xi_used": False,
            "target_postmatch_stats_used": False,
            "target_odds_used": False,
            "handicap_used": False,
            "market_prices_used": False,
            "manual_match_adjustment": False,
            "manual_draw_override": False,
            "parameter_search_on_targets": False,
            "alpha": ALPHA,
            "same_rule_all_targets": True,
            "lineup_bridge_mode": "LEGACY_STRICT_PRIOR_EXPECTED_XI_NO_CURRENT_SEASON_OPENER_SNAPSHOT_AVAILABLE",
            "why_not_r42l_anchor": "R42L/R42E current-season availability anchor was built only for its frozen EPL targets; these cross-league targets do not have a pre-result current-season opener/availability snapshot in the frozen source.",
        },
        "source": {**source_meta, "r42h_reconstructed_runner_sha256": runner_sha},
        "target_count": len(results),
        "targets": results,
        "limitations": [
            "These matches were supplied after their scheduled kickoff window, so this is not counted as clean prospective evidence even though the runner is target-result blind.",
            "The frozen player-lineup source does not extend to these targets; expected-XI membership is therefore the last strictly prior historical estimate rather than a current-season opener bridge.",
            "No injury, suspension, fatigue, coach, next-match-importance or R43 context layer is injected yet; those are the next research stages.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "trial7_r42k_pseudoprematch_predictions.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def verify():
    p = OUT / "trial7_r42k_pseudoprematch_predictions.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["status"] == "COMPLETE" and d["formal_weight"] == 0 and d["target_count"] == 7
    g = d["governance"]
    assert not g["target_results_used"] and not g["target_confirmed_xi_used"] and not g["target_postmatch_stats_used"]
    assert not g["target_odds_used"] and not g["handicap_used"] and not g["manual_draw_override"]
    assert g["alpha"] == 0.5 and g["same_rule_all_targets"] is True
    for x in d["targets"]:
        for key in ("baseline_r40c", "full_r42h", "r42k_half_shrink"):
            q = x[key]
            assert abs(q["home"] + q["draw"] + q["away"] - 1.0) < 1e-10
    print("TRIAL7_R42K_PSEUDOPREMATCH_VERIFY_PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_trial7_r42k.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()
