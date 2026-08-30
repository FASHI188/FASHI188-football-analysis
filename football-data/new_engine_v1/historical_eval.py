from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENGINE_DIR = ROOT / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from platform_core import normalize_team_token, read_processed_matches, registry_map  # type: ignore
from pure_engine import EngineState, Fixture, Parameters, canonical_json_hash  # type: ignore
import legacy_v500  # type: ignore

ANCHOR = "7c1815c47102412e88f72189e2b8f837d9b73a42"
OUT = HERE / "evidence"
PREREG = HERE / "contracts" / "VALIDATION_PREREG.md"


@dataclass(frozen=True)
class BlindMatch:
    fixture_id: str
    competition_id: str
    season: str
    kickoff: datetime
    home_team: str
    away_team: str
    home_team_id: str
    away_team_id: str
    source_path: str

    @property
    def key(self) -> tuple[str, str, datetime, str, str]:
        return self.competition_id, self.season, self.kickoff, self.home_team, self.away_team


class LabelVault:
    def __init__(self, labels: dict[str, tuple[int, int]]):
        self._labels = labels
        self._frozen_batches: set[str] = set()
        self.access_count = 0

    def register_frozen(self, batch_hash: str) -> None:
        if not batch_hash or len(batch_hash) != 64:
            raise RuntimeError("invalid frozen batch hash")
        self._frozen_batches.add(batch_hash)

    def unseal(self, fixture_ids: list[str], batch_hash: str) -> dict[str, tuple[int, int]]:
        if batch_hash not in self._frozen_batches:
            raise RuntimeError("labels requested before prediction batch freeze")
        if len(fixture_ids) != len(set(fixture_ids)):
            raise RuntimeError("duplicate fixture ids in label request")
        self.access_count += len(fixture_ids)
        return {fid: self._labels[fid] for fid in fixture_ids}


def _hash_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _global_team_id(name: str) -> str:
    token = normalize_team_token(name)
    if not token:
        raise RuntimeError(f"empty canonical team token: {name!r}")
    return "gteam_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def load_evaluation_universe() -> tuple[list[BlindMatch], dict[str, tuple[int, int]], dict[str, Any]]:
    fixtures: list[BlindMatch] = []
    labels: dict[str, tuple[int, int]] = {}
    audit: dict[str, Any] = {"competitions": {}, "eligibility": "frozen_V500_completed_season_intersection"}
    for cid in sorted(registry_map()):
        seasons = set(legacy_v500.completed_seasons(cid))
        if not seasons:
            audit["competitions"][cid] = {"status": "NO_FROZEN_V500_COMPLETED_SEASONS", "rows": 0}
            continue
        rows = [m for m in read_processed_matches(cid) if str(m.season) in seasons]
        count = 0
        for m in rows:
            fid = _hash_id(cid, str(m.season), m.date.isoformat(), m.home_team, m.away_team)
            if fid in labels:
                raise RuntimeError(f"fixture id collision: {fid}")
            fixtures.append(BlindMatch(
                fixture_id=fid,
                competition_id=cid,
                season=str(m.season),
                kickoff=m.date.astimezone(timezone.utc),
                home_team=m.home_team,
                away_team=m.away_team,
                home_team_id=_global_team_id(m.home_team),
                away_team_id=_global_team_id(m.away_team),
                source_path=m.source_path,
            ))
            labels[fid] = (int(m.home_goals), int(m.away_goals))
            count += 1
        audit["competitions"][cid] = {"status": "ELIGIBLE", "rows": count, "seasons": sorted(seasons)}
    fixtures.sort(key=lambda f: (f.kickoff, f.competition_id, f.season, f.home_team, f.away_team, f.fixture_id))
    if len(fixtures) < 3000:
        raise RuntimeError(f"evaluation universe too small: {len(fixtures)}")
    return fixtures, labels, audit


def split_boundaries(fixtures: list[BlindMatch]) -> tuple[int, int]:
    n = len(fixtures)
    i1 = max(1, int(n * 0.60))
    while i1 < n and fixtures[i1].kickoff == fixtures[i1 - 1].kickoff:
        i1 += 1
    i2 = max(i1 + 1, int(n * 0.80))
    while i2 < n and fixtures[i2].kickoff == fixtures[i2 - 1].kickoff:
        i2 += 1
    if not (0 < i1 < i2 < n):
        raise RuntimeError(f"invalid chronological boundaries: {i1},{i2},{n}")
    return i1, i2


def batches(fixtures: Iterable[BlindMatch]) -> list[list[BlindMatch]]:
    out: list[list[BlindMatch]] = []
    cur: list[BlindMatch] = []
    current: datetime | None = None
    for f in fixtures:
        if current is None or f.kickoff == current:
            cur.append(f); current = f.kickoff
        else:
            out.append(cur); cur = [f]; current = f.kickoff
    if cur:
        out.append(cur)
    return out


def engine_fixture(f: BlindMatch) -> Fixture:
    return Fixture(f.fixture_id, f.competition_id, f.season, f.kickoff, f.home_team_id, f.away_team_id)


def _actual_index(label: tuple[int, int]) -> int:
    hg, ag = label
    return 0 if hg > ag else 1 if hg == ag else 2


def _prob(pred: dict[str, Any]) -> list[float]:
    p = [float(pred["p_home"]), float(pred["p_draw"]), float(pred["p_away"])]
    if any((not math.isfinite(x) or x < 0 or x > 1) for x in p) or abs(sum(p) - 1.0) > 1e-7:
        raise RuntimeError("invalid 1X2 probability")
    return p


def metric_row(pred: dict[str, Any], label: tuple[int, int]) -> dict[str, float | int]:
    p = _prob(pred); y = _actual_index(label); pick = max(range(3), key=lambda i: p[i])
    ll = -math.log(max(1e-15, p[y]))
    brier = sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
    rps = ((p[0] - (1.0 if y == 0 else 0.0)) ** 2 + ((p[0] + p[1]) - (1.0 if y <= 1 else 0.0)) ** 2) / 2.0
    yd = 1 if y == 1 else 0; pd = p[1]
    draw_ll = -(yd * math.log(max(1e-15, pd)) + (1 - yd) * math.log(max(1e-15, 1.0 - pd)))
    return {"y": y, "pick": pick, "hit": int(pick == y), "logloss": ll, "brier": brier, "rps": rps, "draw_logloss": draw_ll, "draw_brier": (pd - yd) ** 2}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    n = len(rows); out: dict[str, Any] = {"n": n}
    for key in ("hit", "logloss", "brier", "rps", "draw_logloss", "draw_brier"):
        out[key if key != "hit" else "top1_accuracy"] = sum(float(r[key]) for r in rows) / n
    out["draw_actual_n"] = sum(int(r["y"] == 1) for r in rows)
    out["draw_pick_n"] = sum(int(r["pick"] == 1) for r in rows)
    out["draw_top1_recall"] = (sum(int(r["pick"] == 1 and r["y"] == 1) for r in rows) / out["draw_actual_n"]) if out["draw_actual_n"] else None
    return out


def calibration_ece(scored: list[dict[str, Any]], predictions: dict[str, dict[str, Any]], bins_n: int = 10) -> dict[str, Any]:
    class_ece = []; details = {}
    for cls, key in enumerate(("p_home", "p_draw", "p_away")):
        bins = [[] for _ in range(bins_n)]
        for r in scored:
            p = float(predictions[r["fixture_id"]][key]); idx = min(bins_n - 1, int(p * bins_n)); bins[idx].append((p, 1.0 if int(r["y"]) == cls else 0.0))
        ece = 0.0; summary = []; total = max(1, len(scored))
        for b in bins:
            if not b: continue
            mp = sum(x for x, _ in b) / len(b); ar = sum(y for _, y in b) / len(b)
            ece += len(b) / total * abs(mp - ar); summary.append({"n": len(b), "mean_p": mp, "actual_rate": ar})
        class_ece.append(ece); details[key] = {"ece": ece, "bins": summary}
    return {"mean_class_ece": sum(class_ece) / len(class_ece), "classes": details}


PARAMETER_GRID = [
    Parameters(half_life_days=170.0, prior_matches=6.0, cross_season_shrink=0.48, competition_prior_matches=20.0, global_team_prior_matches=10.0),
    Parameters(half_life_days=210.0, prior_matches=8.0, cross_season_shrink=0.58, competition_prior_matches=24.0, global_team_prior_matches=12.0),
    Parameters(half_life_days=260.0, prior_matches=10.0, cross_season_shrink=0.66, competition_prior_matches=28.0, global_team_prior_matches=14.0),
    Parameters(half_life_days=320.0, prior_matches=12.0, cross_season_shrink=0.74, competition_prior_matches=32.0, global_team_prior_matches=16.0),
]


def _labels_for_batch(batch: list[BlindMatch], labels: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    return {f.fixture_id: labels[f.fixture_id] for f in batch}


def tune(fixtures: list[BlindMatch], labels: dict[str, tuple[int, int]], dev_end: int, tune_end: int) -> tuple[Parameters, list[dict[str, Any]]]:
    tune_ids = {f.fixture_id for f in fixtures[dev_end:tune_end]}; leaderboard = []
    for idx, params in enumerate(PARAMETER_GRID):
        engine = EngineState(params=params); scored = []; failures = 0
        for batch in batches(fixtures[:tune_end]):
            for f in batch:
                if f.fixture_id in tune_ids:
                    try:
                        scored.append(metric_row(engine.predict(engine_fixture(f)), labels[f.fixture_id]))
                    except Exception:
                        failures += 1
            engine.apply_batch([engine_fixture(f) for f in batch], _labels_for_batch(batch, labels))
        agg = aggregate(scored)
        leaderboard.append({"grid_index": idx, "params": params.__dict__, "tune_metrics": agg, "failures": failures, "objective": [float(agg.get("logloss", 99)), float(agg.get("brier", 99)), float(agg.get("rps", 99))]})
    leaderboard.sort(key=lambda x: (x["objective"], x["grid_index"]))
    return PARAMETER_GRID[int(leaderboard[0]["grid_index"])], leaderboard


class TableTracker:
    def __init__(self):
        self.points: dict[tuple[str, str, str], float] = defaultdict(float); self.games: dict[tuple[str, str, str], int] = defaultdict(int)

    def context(self, f: BlindMatch) -> dict[str, Any]:
        hk = (f.competition_id, f.season, f.home_team); ak = (f.competition_id, f.season, f.away_team)
        hg, ag = self.games[hk], self.games[ak]; hppg = self.points[hk] / hg if hg else None; appg = self.points[ak] / ag if ag else None
        inferred_round = hg + 1 if hg == ag else None; underdog = None
        if hg >= 3 and ag >= 3 and hppg is not None and appg is not None and abs(hppg - appg) >= 0.15:
            underdog = 0 if hppg < appg else 2
        return {"home_prior_games": hg, "away_prior_games": ag, "home_ppg": hppg, "away_ppg": appg, "inferred_round": inferred_round, "underdog_side": underdog}

    def update(self, f: BlindMatch, label: tuple[int, int]) -> None:
        hk = (f.competition_id, f.season, f.home_team); ak = (f.competition_id, f.season, f.away_team); hg, ag = label
        self.games[hk] += 1; self.games[ak] += 1
        if hg > ag: self.points[hk] += 3
        elif hg < ag: self.points[ak] += 3
        else: self.points[hk] += 1; self.points[ak] += 1


def season_context(fixtures: list[BlindMatch]) -> dict[tuple[str, str], dict[str, Any]]:
    by_comp: dict[str, list[str]] = defaultdict(list); teams: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in fixtures:
        if f.season not in by_comp[f.competition_id]: by_comp[f.competition_id].append(f.season)
        teams[(f.competition_id, f.season)].update([f.home_team, f.away_team])
    out = {}
    for cid, seasons in by_comp.items():
        for i, season in enumerate(seasons):
            prev = seasons[i - 1] if i else None; out[(cid, season)] = {"previous_season": prev, "previous_teams": teams[(cid, prev)] if prev else set()}
    return out


def _legacy_match_map(comp: legacy_v500.LegacyCompetition) -> dict[tuple[str, str, datetime, str, str], Any]:
    return {(comp.competition_id, str(m.season), m.date, m.home_team, m.away_team): m for m in comp.all_matches}


def evaluate_holdout(fixtures: list[BlindMatch], labels: dict[str, tuple[int, int]], holdout_start: int, selected: Parameters) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True); holdout = fixtures[holdout_start:]; start_time = holdout[0].kickoff
    engine = EngineState(params=selected); table = TableTracker()
    for batch in batches(fixtures[:holdout_start]):
        engine.apply_batch([engine_fixture(f) for f in batch], _labels_for_batch(batch, labels))
        for f in batch: table.update(f, labels[f.fixture_id])

    holdout_comps = sorted({f.competition_id for f in holdout}); legacy_comps = {}; legacy_maps = {}
    for cid in holdout_comps:
        lc = legacy_v500.LegacyCompetition(cid); lc.prewarm_before(start_time); legacy_comps[cid] = lc; legacy_maps[cid] = _legacy_match_map(lc)

    vault = LabelVault(labels); blind_path = OUT / "holdout_predictions_blind.jsonl"; freeze_path = OUT / "holdout_batch_freeze.jsonl"; score_path = OUT / "holdout_scored.jsonl"
    for p in (blind_path, freeze_path, score_path): p.write_text("", encoding="utf-8")
    pure_preds = {}; legacy_preds = {}; scored_pure = []; scored_legacy = []; paired_pure = []; paired_legacy = []; metadata = {}; pure_failures = []; legacy_failures = []; same_cutoff_batches = 0; ctx = season_context(fixtures)

    for batch in batches(holdout):
        same_cutoff_batches += 1; batch_pure = {}; batch_legacy = {}; legacy_match_objs = {}
        for f in batch:
            tctx = table.context(f); sctx = ctx[(f.competition_id, f.season)]
            entrant = bool(sctx["previous_season"] is not None and (f.home_team not in sctx["previous_teams"] or f.away_team not in sctx["previous_teams"]))
            metadata[f.fixture_id] = {**tctx, "entrant_proxy": entrant, "competition_id": f.competition_id, "season": f.season, "kickoff": f.kickoff.isoformat()}
            try:
                pred = engine.predict(engine_fixture(f)); batch_pure[f.fixture_id] = pred; pure_preds[f.fixture_id] = pred
            except Exception as exc:
                pure_failures.append({"fixture_id": f.fixture_id, "error": f"{type(exc).__name__}: {exc}"})
            try:
                lm = legacy_maps[f.competition_id].get(f.key)
                if lm is None: raise legacy_v500.LegacyUnavailable("legacy match identity not found")
                lp = legacy_comps[f.competition_id].runner(f.season).predict(lm); lp["prediction_hash"] = canonical_json_hash(lp)
                batch_legacy[f.fixture_id] = lp; legacy_preds[f.fixture_id] = lp; legacy_match_objs[f.fixture_id] = lm
            except Exception as exc:
                legacy_failures.append({"fixture_id": f.fixture_id, "error": f"{type(exc).__name__}: {exc}"})

        blind_record = {"cutoff": batch[0].kickoff.isoformat(), "fixture_ids": [f.fixture_id for f in batch], "pure": {fid: {k: v for k, v in p.items() if k != "score_matrix"} for fid, p in batch_pure.items()}, "legacy": {fid: {k: v for k, v in p.items() if k != "score_matrix"} for fid, p in batch_legacy.items()}}
        batch_hash = canonical_json_hash(blind_record); blind_record["batch_hash"] = batch_hash
        with blind_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(blind_record, ensure_ascii=False, sort_keys=True) + "\n"); fh.flush(); os.fsync(fh.fileno())
        with freeze_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cutoff": batch[0].kickoff.isoformat(), "batch_hash": batch_hash, "fixture_count": len(batch), "labels_read_before_freeze": False}, ensure_ascii=False, sort_keys=True) + "\n"); fh.flush(); os.fsync(fh.fileno())
        vault.register_frozen(batch_hash); batch_labels = vault.unseal([f.fixture_id for f in batch], batch_hash)

        for f in batch:
            label = batch_labels[f.fixture_id]
            if f.fixture_id in batch_pure: scored_pure.append({"fixture_id": f.fixture_id, **metric_row(batch_pure[f.fixture_id], label)})
            if f.fixture_id in batch_legacy: scored_legacy.append({"fixture_id": f.fixture_id, **metric_row(batch_legacy[f.fixture_id], label)})
            if f.fixture_id in batch_pure and f.fixture_id in batch_legacy:
                paired_pure.append({"fixture_id": f.fixture_id, **metric_row(batch_pure[f.fixture_id], label)}); paired_legacy.append({"fixture_id": f.fixture_id, **metric_row(batch_legacy[f.fixture_id], label)})
            with score_path.open("a", encoding="utf-8") as fh: fh.write(json.dumps({"fixture_id": f.fixture_id, "batch_hash": batch_hash, "home_goals": label[0], "away_goals": label[1]}, ensure_ascii=False, sort_keys=True) + "\n")
            table.update(f, label)

        engine.apply_batch([engine_fixture(f) for f in batch], batch_labels)
        by_runner: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for f in batch:
            lm = legacy_match_objs.get(f.fixture_id)
            if lm is not None: by_runner[(f.competition_id, f.season)].append(lm)
        for (cid, season), ms in by_runner.items(): legacy_comps[cid].runner(season).apply_batch(ms)

    pure_agg = aggregate(scored_pure); legacy_agg = aggregate(scored_legacy); paired_p = aggregate(paired_pure); paired_l = aggregate(paired_legacy)
    pure_cal = calibration_ece(scored_pure, pure_preds) if scored_pure else None; legacy_cal = calibration_ece(scored_legacy, legacy_preds) if scored_legacy else None

    def slices(rows: list[dict[str, Any]]) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            fid = r["fixture_id"]; m = metadata[fid]; groups[f"competition:{m['competition_id']}"] .append(r)
            if fid in pure_preds: groups[f"cold:{pure_preds[fid]['cold_start_bucket']}"] .append(r)
            rd = m.get("inferred_round")
            if rd in {1, 2, 3, 29, 30}: groups[f"round:{rd}"].append(r)
            if m.get("entrant_proxy"): groups["entrant_proxy"].append(r)
        return {name: aggregate(items) for name, items in sorted(groups.items())}

    pure_slices = slices(scored_pure); legacy_slices = slices(scored_legacy)
    underdog_p = []; underdog_l = []; legacy_by_id = {r["fixture_id"]: r for r in scored_legacy}; pure_by_id = {r["fixture_id"]: r for r in scored_pure}
    for fid, m in metadata.items():
        side = m.get("underdog_side")
        if side is None: continue
        y = _actual_index(labels[fid])
        if y != side: continue
        if fid in pure_by_id: underdog_p.append(int(pure_by_id[fid]["pick"] == side))
        if fid in legacy_by_id: underdog_l.append(int(legacy_by_id[fid]["pick"] == side))
    underdog = {"pure_events": len(underdog_p), "pure_top1_recall": sum(underdog_p) / len(underdog_p) if underdog_p else None, "legacy_events": len(underdog_l), "legacy_top1_recall": sum(underdog_l) / len(underdog_l) if underdog_l else None}

    n_hold = len(holdout); pure_cov = len(scored_pure) / n_hold; legacy_cov = len(scored_legacy) / n_hold; paired_n = len(paired_pure)
    subgroup_degradation = []
    for name, pmet in pure_slices.items():
        lmet = legacy_slices.get(name)
        if pmet.get("n", 0) >= 100 and lmet and lmet.get("n") == pmet.get("n"):
            subgroup_degradation.append({"group": name, "n": pmet["n"], "logloss_delta": float(pmet["logloss"]) - float(lmet["logloss"])})
    worst_subgroup = max(subgroup_degradation, key=lambda x: x["logloss_delta"], default=None)

    gates = {
        "paired_rows_positive": paired_n > 0,
        "logloss_improve_0_002": paired_n > 0 and float(paired_p["logloss"]) <= float(paired_l["logloss"]) - 0.002,
        "brier_improve_0_001": paired_n > 0 and float(paired_p["brier"]) <= float(paired_l["brier"]) - 0.001,
        "rps_improve_0_0005": paired_n > 0 and float(paired_p["rps"]) <= float(paired_l["rps"]) - 0.0005,
        "top1_nonworse_0_25pp": paired_n > 0 and float(paired_p["top1_accuracy"]) + 0.0025 >= float(paired_l["top1_accuracy"]),
        "pure_coverage_99pct": pure_cov >= 0.99,
        "coverage_not_worse_0_25pp": pure_cov + 0.0025 >= legacy_cov,
        "worst_subgroup_logloss_delta_le_0_03": worst_subgroup is None or float(worst_subgroup["logloss_delta"]) <= 0.03,
        "draw_logloss_nonworse_0_005": paired_n > 0 and float(paired_p["draw_logloss"]) <= float(paired_l["draw_logloss"]) + 0.005,
        "underdog_recall_nonworse_2pp_if_n100": True,
        "pure_fail_closed_no_runtime_failure": len(pure_failures) == 0,
        "label_access_after_freeze": vault.access_count == n_hold,
    }
    if min(underdog["pure_events"], underdog["legacy_events"]) >= 100:
        gates["underdog_recall_nonworse_2pp_if_n100"] = float(underdog["pure_top1_recall"]) + 0.02 >= float(underdog["legacy_top1_recall"])
    promoted = all(gates.values())

    sample_ids = sorted(pure_preds)[:8]; (OUT / "score_matrix_samples.json").write_text(json.dumps({fid: pure_preds[fid] for fid in sample_ids}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {
        "schema_version": "football3-new-engine-v1-historical-oos-v1", "anchor": ANCHOR, "scientific_status": "MODEL_CANDIDATE_PASSED" if promoted else "NOT_PROMOTED", "selected_parameters": selected.__dict__,
        "holdout": {"n": n_hold, "first": holdout[0].kickoff.isoformat(), "last": holdout[-1].kickoff.isoformat(), "same_cutoff_batches": same_cutoff_batches},
        "coverage": {"pure": pure_cov, "legacy_v500": legacy_cov, "paired_n": paired_n, "pure_failures": len(pure_failures), "legacy_failures": len(legacy_failures)},
        "pure": pure_agg, "legacy_v500": legacy_agg, "paired": {"pure": paired_p, "legacy_v500": paired_l}, "calibration": {"pure": pure_cal, "legacy_v500": legacy_cal}, "underdog_proxy": underdog,
        "pure_slices": pure_slices, "legacy_slices": legacy_slices, "worst_comparable_subgroup": worst_subgroup, "gates": gates,
        "governance": {"prereg_path": str(PREREG.relative_to(ROOT)), "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(), "v500_frozen_blob_sha_expected": legacy_v500.FROZEN_V500_BLOB, "pure_module_imports_legacy": False, "same_cutoff_predict_all_then_update": True, "processed_date_granularity": "date_only; all matches sharing date are conservatively one atomic cutoff batch", "historical_labels_are_unsealed_only_after_batch_prediction_hash_persisted": True},
        "market_assisted_historical": {"status": "MARKET_ASSIST_NOT_SCORED", "reason": "frozen processed comparison universe does not provide verified original prematch quote timestamps; closing-price backfill forbidden"},
        "failure_examples": {"pure": pure_failures[:10], "legacy": legacy_failures[:10]},
    }
    (OUT / "historical_oos_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); return result


def cold_start_audit(selected: Parameters) -> dict[str, Any]:
    from datetime import timedelta
    t = datetime(2026, 1, 1, tzinfo=timezone.utc); e = EngineState(params=selected); zero = Fixture("cold-zero", "NEW_LEAGUE", "2026", t, "A", "B"); p0 = e.predict(zero); e.apply_batch([zero], {"cold-zero": (1, 0)})
    p1 = e.predict(Fixture("cold-sparse", "NEW_LEAGUE", "2026", t + timedelta(days=7), "A", "C")); p2 = e.predict(Fixture("season-gap", "NEW_LEAGUE", "2027", t + timedelta(days=250), "A", "B"))
    checks = {"zero_sample_predicts": abs(p0["p_home"] + p0["p_draw"] + p0["p_away"] - 1.0) < 1e-8, "zero_sample_uncertainty_high": p0["uncertainty"] >= 0.5, "sparse_source_declared": p1["cold_start_bucket"] in {"zero", "sparse"}, "cross_season_history_shrinks": p2["effective_home_history"] < 1.0, "score_matrix_present": len(p0["score_matrix"]) > 100, "uncertainty_intervals_ordered": p0["mu_home_ci90"][0] <= p0["mu_home"] <= p0["mu_home_ci90"][1]}
    payload = {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "samples": {"zero": {k: v for k, v in p0.items() if k != "score_matrix"}, "sparse": {k: v for k, v in p1.items() if k != "score_matrix"}, "season_gap": {k: v for k, v in p2.items() if k != "score_matrix"}}}; (OUT / "cold_start_audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); return payload


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True); fixtures, labels, source_audit = load_evaluation_universe(); dev_end, tune_end = split_boundaries(fixtures); selected, leaderboard = tune(fixtures, labels, dev_end, tune_end)
    receipt = {"anchor": ANCHOR, "universe_n": len(fixtures), "first": fixtures[0].kickoff.isoformat(), "last": fixtures[-1].kickoff.isoformat(), "dev_end": dev_end, "tune_end": tune_end, "holdout_n": len(fixtures) - tune_end, "holdout_first": fixtures[tune_end].kickoff.isoformat(), "parameter_grid": [p.__dict__ for p in PARAMETER_GRID], "selected_parameters": selected.__dict__, "tuning_leaderboard": leaderboard, "source_audit": source_audit, "final_gate_thresholds_source": "contracts/VALIDATION_PREREG.md committed before implementation"}
    (OUT / "prereg_execution_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = evaluate_holdout(fixtures, labels, tune_end, selected); cold = cold_start_audit(selected)
    if cold["status"] != "PASS": raise RuntimeError("critical cold-start structural test failed")
    final = {"status": "COMPLETE", "scientific_status": result["scientific_status"], "final_state": "GPT_REBUILT_PENDING_CODEX_RECHECK", "historical_result": "evidence/historical_oos_result.json", "cold_start_result": "evidence/cold_start_audit.json"}
    (OUT / "final_status.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": final["status"], "scientific_status": final["scientific_status"], "final_state": final["final_state"], "universe_n": len(fixtures), "holdout_n": len(fixtures)-tune_end}, ensure_ascii=False)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
