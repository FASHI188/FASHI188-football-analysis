#!/usr/bin/env python3
import argparse, hashlib, importlib.util, json, math, pathlib, statistics

HERE = pathlib.Path(__file__).resolve().parent
BASE_PATH = HERE.parent / "fplcache_pit_availability_dev_score_v1" / "score_fplcache_pit_availability_dev_v1_1.py"
spec = importlib.util.spec_from_file_location("base_scorer", BASE_PATH)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

ScoreError = base.ScoreError
read_json = base.read_json
read_jsonl = base.read_jsonl
read_gz_jsonl = base.read_gz_jsonl
write_json = base.write_json
write_jsonl = base.write_jsonl
canon_sha = base.canon_sha
parse_dt = base.parse_dt
minute_key = base.minute_key
outcome_idx = base.outcome_idx
integrate = base.integrate
iproject = base.iproject
paired_required_n = base.paired_required_n
team_impairment = base.team_impairment

def metric(rows, pkey, mkey=None):
    n = len(rows)
    if not n:
        raise ScoreError("empty metric cohort")
    ll = br = rps = score_ll = 0.0
    top = 0
    for r in rows:
        p = r[pkey]
        y = r["y"]
        ll += -math.log(max(float(p[y]), 1e-15))
        br += sum((float(p[k]) - (1.0 if y == k else 0.0)) ** 2 for k in range(3))
        c1 = float(p[0])
        c2 = float(p[0]) + float(p[1])
        o1 = 1.0 if y == 0 else 0.0
        o2 = 1.0 if y in (0, 1) else 0.0
        rps += ((c1 - o1) ** 2 + (c2 - o2) ** 2) / 2.0
        top += int(max(range(3), key=lambda k: p[k]) == y)
        if mkey:
            hg, ag = r["home_goals"], r["away_goals"]
            if not (0 <= hg < 15 and 0 <= ag < 15):
                raise ScoreError("score outside 0-14 matrix support")
            score_ll += -math.log(max(float(r[mkey][hg][ag]), 1e-15))
    out = {
        "n": n,
        "logloss": ll / n,
        "brier": br / n,
        "rps": rps / n,
        "top1_accuracy": top / n,
        "top1_correct": top,
    }
    if mkey:
        out["exact_score_logloss"] = score_ll / n
    return out

def eval_cohort(rows):
    b = metric(rows, "baseline_p", "baseline_matrix")
    c = metric(rows, "candidate_p", "candidate_matrix")
    return {
        "n": len(rows),
        "baseline": b,
        "candidate": c,
        "deltas": {
            "one_x_two_logloss_gain": b["logloss"] - c["logloss"],
            "one_x_two_brier_delta": c["brier"] - b["brier"],
            "one_x_two_rps_delta": c["rps"] - b["rps"],
            "top1_delta": c["top1_accuracy"] - b["top1_accuracy"],
            "top1_net_correct": c["top1_correct"] - b["top1_correct"],
            "exact_score_logloss_gain": b["exact_score_logloss"] - c["exact_score_logloss"],
        },
        "paired_one_x_two": paired_required_n(rows),
    }

def temporal_consensus_tilt(d24, d6, d90):
    med = statistics.median([float(d24), float(d6), float(d90)])
    if not -1.000000000001 <= med <= 1.000000000001:
        raise ScoreError(f"consensus delta outside impairment domain: {med}")
    tilt = med / (1.0 + abs(med))
    if abs(tilt) > 0.500000000001:
        raise ScoreError(f"bounded tilt exceeded: {tilt}")
    return med, tilt

def transform_1x2(p, tilt):
    z = p[0] * math.exp(tilt) + p[1] + p[2] * math.exp(-tilt)
    return [p[0] * math.exp(tilt) / z, p[1] / z, p[2] * math.exp(-tilt) / z]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--fpl-dir", required=True)
    ap.add_argument("--stress-dir", required=True)
    ap.add_argument("--history-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    c = read_json(a.contract)

    if c["status"] != "FROZEN_BEFORE_BULK_OUTCOME_SCORING":
        raise ScoreError("contract not frozen")
    auth = c["authorization"]
    if any(auth[x] for x in (
        "training_allowed", "tuning_allowed", "parameter_search_allowed", "candidate_selection_allowed"
    )):
        raise ScoreError("forbidden authorization drift")
    cand = c["single_frozen_candidate"]
    if cand["id"] != "FPLCACHE-AVAIL-TEMPORAL-MEDIAN-BOUNDED-TILT-V1":
        raise ScoreError("candidate identity drift")
    if cand["parameter_grid"] != {}:
        raise ScoreError("parameter grid not empty")

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fpl = pathlib.Path(a.fpl_dir)
    stress = pathlib.Path(a.stress_dir)
    hist = pathlib.Path(a.history_dir)
    season_labels = set(c["development_scope"]["fplcache_season_labels"])
    under_alias = c["fixture_join"]["team_aliases_understat_to_fplcache_fixture"]
    snap_alias = c["fixture_join"]["fplcache_fixture_to_snapshot_team_aliases"]

    cut_by_pair, needed = {}, set()
    for r in read_gz_jsonl(fpl / "fixture_cutoff_map.jsonl.gz"):
        if r["season"] not in season_labels:
            continue
        k = (r["season"], r["home_team"], r["away_team"])
        if k in cut_by_pair:
            raise ScoreError(f"duplicate fpl pair {k}")
        cut_by_pair[k] = r
        for w in ("T_MINUS_24H", "T_MINUS_6H", "T_MINUS_90M"):
            x = r["cutoffs"][w]
            if not x["acceptable_staleness"]:
                raise ScoreError(f"unacceptable staleness {k} {w}")
            needed.add(x["snapshot"]["path"])
    if len(cut_by_pair) != int(c["development_scope"]["all_pair_unique_fixture_count"]):
        raise ScoreError(f"fpl pair count {len(cut_by_pair)}")

    snapshots = {}
    for s in read_gz_jsonl(fpl / "availability_snapshots.jsonl.gz"):
        pth = s.get("source", {}).get("path")
        if pth in needed:
            snapshots[pth] = s
    if needed - set(snapshots):
        raise ScoreError("missing selected snapshots")

    fixtures = {r["fixture_id"]: r for r in read_jsonl(hist / "data/fixtures.jsonl")
                if r["league"] == "EPL" and int(r["season"]) in (2024, 2025)}
    preds = {r["fixture_id"]: r for r in read_jsonl(stress / "evidence/predictions_label_free.jsonl")
             if r["league"] == "EPL" and int(r["season"]) in (2024, 2025)}
    labels = {r["fixture_id"]: r for r in read_jsonl(hist / "data/label_vault.jsonl")
              if r["fixture_id"] in fixtures}
    if len(fixtures) != 760 or len(preds) != 760 or len(labels) != 760:
        raise ScoreError(f"input count {len(fixtures)} {len(preds)} {len(labels)}")

    all_rows, pred_out, pair_unresolved, primary_unresolved = [], [], [], []
    max_matrix_diff = max_prob_delta = max_abs_tilt = 0.0
    sign_consensus_all = 0

    for fid in sorted(fixtures, key=lambda z: (parse_dt(fixtures[z]["kickoff"]), z)):
        fr, pr, lab = fixtures[fid], preds[fid], labels[fid]
        season = f"{int(fr['season'])}-{str(int(fr['season']) + 1)[-2:]}"
        home = under_alias.get(fr["home_team_name"], fr["home_team_name"])
        away = under_alias.get(fr["away_team_name"], fr["away_team_name"])
        pk = (season, home, away)
        cr = cut_by_pair.get(pk)
        if cr is None:
            pair_unresolved.append({"fixture_id": fid, "key": pk})
            continue

        exact = minute_key(fr["kickoff"]) == minute_key(cr["kickoff_utc"])
        if not exact:
            primary_unresolved.append({
                "fixture_id": fid,
                "understat_kickoff": fr["kickoff"],
                "fpl_kickoff": cr["kickoff_utc"],
            })

        imp = {}
        for w in ("T_MINUS_24H", "T_MINUS_6H", "T_MINUS_90M"):
            snap = snapshots[cr["cutoffs"][w]["snapshot"]["path"]]
            imp[w] = (team_impairment(snap, home, snap_alias),
                      team_impairment(snap, away, snap_alias))

        d24 = imp["T_MINUS_24H"][1] - imp["T_MINUS_24H"][0]
        d6 = imp["T_MINUS_6H"][1] - imp["T_MINUS_6H"][0]
        d90 = imp["T_MINUS_90M"][1] - imp["T_MINUS_90M"][0]
        consensus_d, tilt = temporal_consensus_tilt(d24, d6, d90)
        max_abs_tilt = max(max_abs_tilt, abs(tilt))
        signs = [0 if abs(x) <= 1e-15 else (1 if x > 0 else -1) for x in (d24, d6, d90)]
        nz = [s for s in signs if s]
        sign_consensus = bool(nz) and len(set(nz)) == 1
        sign_consensus_all += int(sign_consensus)

        p = [float(x) for x in pr["v3_1_1_1x2"]]
        q = transform_1x2(p, tilt)
        cm = iproject(pr["v3_1_1_matrix"], q)
        qi = integrate(cm)
        max_matrix_diff = max(max_matrix_diff, max(abs(x - y) for x, y in zip(q, qi)))
        max_prob_delta = max(max_prob_delta, max(abs(x - y) for x, y in zip(p, q)))

        hg, ag = int(lab["home_goals"]), int(lab["away_goals"])
        row = {
            "fixture_id": fid, "season": int(fr["season"]), "exact_kickoff_identity": exact,
            "home_goals": hg, "away_goals": ag, "y": outcome_idx(hg, ag),
            "baseline_p": p, "candidate_p": q,
            "baseline_matrix": pr["v3_1_1_matrix"], "candidate_matrix": cm,
            "availability_delta_t24": d24, "availability_delta_t6": d6,
            "availability_delta_t90": d90, "temporal_consensus_delta": consensus_d,
            "effective_tilt": tilt, "temporal_sign_consensus": sign_consensus,
        }
        all_rows.append(row)
        pred_out.append({
            "fixture_id": fid, "season": int(fr["season"]),
            "home_team": fr["home_team_name"], "away_team": fr["away_team_name"],
            "understat_kickoff": fr["kickoff"], "fpl_kickoff": cr["kickoff_utc"],
            "primary_exact_kickoff_identity": exact,
            "home_impairment_t24": imp["T_MINUS_24H"][0], "away_impairment_t24": imp["T_MINUS_24H"][1],
            "home_impairment_t6": imp["T_MINUS_6H"][0], "away_impairment_t6": imp["T_MINUS_6H"][1],
            "home_impairment_t90": imp["T_MINUS_90M"][0], "away_impairment_t90": imp["T_MINUS_90M"][1],
            "availability_delta_t24": d24, "availability_delta_t6": d6,
            "availability_delta_t90": d90, "temporal_consensus_delta": consensus_d,
            "effective_tilt": tilt, "temporal_sign_consensus": sign_consensus,
            "baseline_v3_1_1_1x2": p, "candidate_1x2": q,
            "candidate_matrix_sha256": canon_sha(cm),
        })

    if pair_unresolved or len(all_rows) != 760:
        raise ScoreError(f"pair unresolved {len(pair_unresolved)} rows {len(all_rows)}")

    primary = [r for r in all_rows if r["exact_kickoff_identity"]]
    secondary = all_rows
    if len(primary) != int(c["development_scope"]["primary_exact_kickoff_fixture_count"]):
        raise ScoreError(f"primary count {len(primary)}")
    bys = {2024: sum(r["season"] == 2024 for r in primary),
           2025: sum(r["season"] == 2025 for r in primary)}
    exp = c["development_scope"]["primary_by_season"]
    if bys != {2024: int(exp["2024-25"]), 2025: int(exp["2025-26"])}:
        raise ScoreError(f"primary season counts {bys}")

    pri, sec = eval_cohort(primary), eval_cohort(secondary)
    season_blocks, seasons_ll_ok, seasons_top_ok = [], True, True
    for s in (2024, 2025):
        e = eval_cohort([r for r in primary if r["season"] == s])
        ll_ok = e["deltas"]["one_x_two_logloss_gain"] >= -1e-15
        top_ok = e["deltas"]["top1_net_correct"] >= 0
        seasons_ll_ok &= ll_ok
        seasons_top_ok &= top_ok
        season_blocks.append({"season": s, **e,
                              "logloss_nondegrade": ll_ok, "top1_nondegrade": top_ok})

    active = sum(abs(r["effective_tilt"]) > 1e-15 for r in primary)
    sign_consensus_primary = sum(r["temporal_sign_consensus"] for r in primary)
    g, d = c["development_gates"], pri["deltas"]
    gates = {
        "primary_fixture_count_exact": len(primary) == int(g["primary_fixture_count_exact"]),
        "primary_unresolved_fixture_count": 0 <= int(g["primary_unresolved_fixture_count_max"]),
        "pair_key_unresolved_count":
            len(pair_unresolved) == int(c["fixture_join"]["secondary_required_unresolved_count"]),
        "kickoff_mismatch_count_frozen":
            len(primary_unresolved) == int(c["identity_preflight"]["kickoff_mismatch_count"]),
        "signal_active_fixture_min": active >= int(g["signal_active_fixture_min"]),
        "pooled_1x2_logloss_gain":
            d["one_x_two_logloss_gain"] >= float(g["pooled_1x2_logloss_gain_min"]) - 1e-15,
        "pooled_1x2_brier_delta":
            d["one_x_two_brier_delta"] <= float(g["pooled_1x2_brier_delta_max"]) + 1e-15,
        "pooled_1x2_rps_delta":
            d["one_x_two_rps_delta"] <= float(g["pooled_1x2_rps_delta_max"]) + 1e-15,
        "pooled_top1_net_correct":
            d["top1_net_correct"] >= int(g["pooled_top1_net_correct_min"]),
        "both_season_blocks_1x2_logloss_nondegrade": seasons_ll_ok,
        "both_season_blocks_top1_nondegrade": seasons_top_ok,
        "exact_score_gain_identity":
            abs(d["exact_score_logloss_gain"] - d["one_x_two_logloss_gain"])
            <= float(g["exact_score_logloss_gain_must_equal_1x2_logloss_gain_within"]),
        "matrix_to_candidate_1x2":
            max_matrix_diff <= float(g["matrix_to_candidate_1x2_max_abs_diff"]),
        "bounded_tilt": max_abs_tilt <= float(g["effective_tilt_abs_max"]) + 1e-15,
    }
    status = c["terminal"]["pass"] if all(gates.values()) else c["terminal"]["fail"]

    result = {
        "schema_version": "football3-fplcache-pit-availability-temporal-consensus-dev-score-result-v1",
        "status": status, "classification": c["classification"],
        "fresh_confirmation": False, "promotion_allowed": False,
        "training_performed": False, "tuning_performed": False,
        "parameter_search_performed": False, "candidate_selection_performed": False,
        "formal_weight_changed": False, "CURRENT_changed": False,
        "production_pointer_changed": False, "formal_enablement_changed": False,
        "primary_exact_kickoff": pri, "secondary_pair_unique_sensitivity": sec,
        "primary_season_blocks": season_blocks,
        "primary_signal_active_fixture_count": active,
        "primary_temporal_sign_consensus_fixture_count": sign_consensus_primary,
        "all760_temporal_sign_consensus_fixture_count": sign_consensus_all,
        "max_effective_tilt_abs_all760": max_abs_tilt,
        "max_probability_abs_delta_all760": max_prob_delta,
        "matrix_to_candidate_1x2_max_abs_diff": max_matrix_diff,
        "identity": {"pair_unresolved_count": len(pair_unresolved),
                     "kickoff_mismatch_count": len(primary_unresolved),
                     "kickoff_mismatch_examples": primary_unresolved[:10]},
        "gates": gates,
        "contract_sha256": hashlib.sha256(pathlib.Path(a.contract).read_bytes()).hexdigest(),
    }
    write_json(out / "development_score_result.json", result)
    write_json(out / "primary_season_blocks.json", season_blocks)
    write_jsonl(out / "predictions_label_free.jsonl", pred_out)
    (out / "artifact_slug.txt").write_text(
        f"{status}__n_{len(primary)}__x1gain_{d['one_x_two_logloss_gain']:.6f}"
        f"__top1net_{d['top1_net_correct']}__active_{active}\n", encoding="utf-8")
    print(json.dumps({
        "status": status, "primary_n": len(primary), "secondary_n": len(secondary),
        "active": active, "temporal_sign_consensus_primary": sign_consensus_primary,
        "x1gain": d["one_x_two_logloss_gain"],
        "brier_delta": d["one_x_two_brier_delta"],
        "rps_delta": d["one_x_two_rps_delta"],
        "top1_delta": d["top1_delta"], "top1_net_correct": d["top1_net_correct"],
        "required_n": pri["paired_one_x_two"]["required_n"],
        "secondary_x1gain": sec["deltas"]["one_x_two_logloss_gain"],
        "secondary_top1_delta": sec["deltas"]["top1_delta"],
        "secondary_top1_net_correct": sec["deltas"]["top1_net_correct"],
        "max_tilt": max_abs_tilt, "max_prob_delta": max_prob_delta, "gates": gates,
    }, sort_keys=True))

if __name__ == "__main__":
    main()
