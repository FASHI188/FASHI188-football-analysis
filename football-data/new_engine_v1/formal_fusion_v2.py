from __future__ import annotations

import copy
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
XG_ENGINE_PATH = ROOT / "historical_xg_challenger_v1" / "historical_xg_challenger.py"
FUSION_IDENTITY_PATH = ROOT / "historical_xg_fusion_v2" / "data" / "XG_FUSION_V2_DATA_IDENTITY.json"
MODEL_LOCK_PATH = HERE / "forward" / "model_lock.json"

FUSION_WEIGHT = 0.75
EXPECTED_V1_ENGINE_SHA256 = "cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318"
EXPECTED_V1_HEAD = "22f639304d2e32fc952dbec2255153ee45dcd41a"
EXPECTED_RESEARCH_HEAD = "d3b3e322f78c48b91477ef6e11054e51ac00fd85"
FORMAL_ENABLEMENT = False


class FormalFusionError(RuntimeError):
    pass


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise FormalFusionError(f"cannot load frozen module: {path}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_xg_module():
    return _load_module("football3_formal_hxg", XG_ENGINE_PATH)


def _load_v1_module():
    return _load_module("football3_formal_v1", HERE / "pure_engine.py")


def load_frozen_contract() -> dict[str, Any]:
    identity = json.loads(FUSION_IDENTITY_PATH.read_text(encoding="utf-8"))
    lock = json.loads(MODEL_LOCK_PATH.read_text(encoding="utf-8"))
    if identity["frozen_v1"]["head"] != EXPECTED_V1_HEAD:
        raise FormalFusionError("Frozen V1 HEAD drift")
    if identity["frozen_v1"]["engine_sha256"] != EXPECTED_V1_ENGINE_SHA256:
        raise FormalFusionError("Frozen V1 engine identity drift")
    if lock["pure_engine_sha256"] != EXPECTED_V1_ENGINE_SHA256:
        raise FormalFusionError("formal V1 model lock drift")
    if identity["fusion_contract"]["formula"] != "normalize((1-w)*p_V1 + w*p_XG)":
        raise FormalFusionError("fusion formula drift")
    if identity["fusion_contract"]["weight_grid"] != [0.25, 0.5, 0.75]:
        raise FormalFusionError("fusion weight grid drift")
    frozen_xg = identity["frozen_xg_parameters"]
    expected_xg = {
        "dynamic_half_life_days": 90.0,
        "dynamic_prior_matches": 4.0,
        "dynamic_beta": 0.15,
        "dynamic_cross_season_shrink": 0.4,
        "xg_pseudocount": 0.25,
        "residual_clip": 0.75,
        "min_effective_evidence": 3.0,
        "pooled_prior_weight": 0.5,
    }
    if frozen_xg != expected_xg:
        raise FormalFusionError("frozen XG parameters drift")
    if identity["formal_enablement"] is not False or identity["formal_weight"] != 0:
        raise FormalFusionError("research identity unexpectedly formally enabled")
    if lock["formal_activation"] is not False:
        raise FormalFusionError("Frozen V1 formal activation unexpectedly enabled")
    return {"identity": identity, "lock": lock}


def new_candidate_state():
    contract = load_frozen_contract()
    hxg = _load_xg_module()
    xg = contract["identity"]["frozen_xg_parameters"]
    params = hxg.XGParams(
        xg["dynamic_half_life_days"],
        xg["dynamic_prior_matches"],
        xg["dynamic_beta"],
        xg["dynamic_cross_season_shrink"],
    )
    v1 = _load_v1_module()
    return hxg.ChallengerState(v1, dict(hxg.EXPECTED_V1_PARAMS), params)


def apply_completed_xg_batch(state, fixtures: Iterable[Any], released_labels: dict[str, Any], available_at):
    """Apply only a fully released, already-ended kickoff batch.

    ChallengerState enforces identity, release-time and same-kickoff isolation.
    This wrapper deliberately provides no prospective queue or label-fetch path.
    """
    batch = list(fixtures)
    if not batch:
        return
    state.apply_released_batch(batch, released_labels, available_at)


def _matrix_key(cell: dict[str, Any]) -> tuple[int, int]:
    return int(cell["home_goals"]), int(cell["away_goals"])


def _canonical_matrix(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    for cell in matrix:
        key = _matrix_key(cell)
        p = float(cell["probability"])
        if key in out or not math.isfinite(p) or p < 0:
            raise FormalFusionError("invalid score matrix")
        out[key] = p
    total = math.fsum(out.values())
    if not math.isfinite(total) or total <= 0:
        raise FormalFusionError("invalid score matrix mass")
    return out


def _normalized_triplet(prediction: dict[str, Any]) -> tuple[float, float, float]:
    vals = tuple(float(prediction[k]) for k in ("p_home", "p_draw", "p_away"))
    if any((not math.isfinite(x) or x < 0) for x in vals):
        raise FormalFusionError("invalid 1X2 probability")
    s = math.fsum(vals)
    if s <= 0:
        raise FormalFusionError("invalid 1X2 mass")
    return tuple(x / s for x in vals)


def blend_active_predictions(v1_prediction: dict[str, Any], xg_prediction: dict[str, Any]) -> dict[str, Any]:
    """Lift the frozen 1X2 mixture to the full score distribution.

    The formal score matrix is the same global mixture of the two normalized
    component score distributions. Its 1X2 marginals must equal the frozen
    normalize((1-w)*p_V1 + w*p_XG) formula to numerical tolerance.
    """
    v1m = _canonical_matrix(v1_prediction["score_matrix"])
    xgm = _canonical_matrix(xg_prediction["score_matrix"])
    if set(v1m) != set(xgm):
        raise FormalFusionError("V1/XG score support mismatch")
    cells: list[dict[str, Any]] = []
    for hg, ag in sorted(v1m):
        p = (1.0 - FUSION_WEIGHT) * v1m[(hg, ag)] + FUSION_WEIGHT * xgm[(hg, ag)]
        cells.append({"home_goals": hg, "away_goals": ag, "probability": p})
    total = math.fsum(float(c["probability"]) for c in cells)
    for cell in cells:
        cell["probability"] = float(cell["probability"]) / total

    home = math.fsum(c["probability"] for c in cells if c["home_goals"] > c["away_goals"])
    draw = math.fsum(c["probability"] for c in cells if c["home_goals"] == c["away_goals"])
    away = math.fsum(c["probability"] for c in cells if c["home_goals"] < c["away_goals"])
    v1p = _normalized_triplet(v1_prediction)
    xgp = _normalized_triplet(xg_prediction)
    expected = tuple((1.0 - FUSION_WEIGHT) * a + FUSION_WEIGHT * b for a, b in zip(v1p, xgp))
    es = math.fsum(expected)
    expected = tuple(x / es for x in expected)
    actual = (home, draw, away)
    if max(abs(a - b) for a, b in zip(actual, expected)) > 5e-12:
        raise FormalFusionError("score-matrix/1X2 fusion identity mismatch")

    return {
        "schema_version": "football3-historical-xg-fusion-v2-formal-candidate-v1",
        "engine": "Football3-Historical-XG-Fusion-V2",
        "fixture_id": str(v1_prediction["fixture_id"]),
        "competition_id": str(v1_prediction["competition_id"]),
        "season": str(v1_prediction["season"]),
        "kickoff": str(v1_prediction["kickoff"]),
        "home_team_id": str(v1_prediction["home_team_id"]),
        "away_team_id": str(v1_prediction["away_team_id"]),
        "score_matrix": cells,
        "p_home": home,
        "p_draw": draw,
        "p_away": away,
        "fusion_weight_xg": FUSION_WEIGHT,
        "fusion_weight_v1": 1.0 - FUSION_WEIGHT,
        "distribution_semantics": "global mixture of Frozen V1 and frozen XG Challenger score distributions",
        "single_poisson_mu_claimed": False,
        "component_prediction_hashes": {
            "v1": v1_prediction.get("prediction_hash"),
            "xg": xg_prediction.get("prediction_hash"),
        },
    }


def predict_formal_batch(state, fixtures: Iterable[Any]) -> list[dict[str, Any]]:
    batch = list(fixtures)
    xg_predictions, v1_predictions = state.predict_batch(batch, include_matrix=True)
    if len(xg_predictions) != len(v1_predictions) or len(batch) != len(v1_predictions):
        raise FormalFusionError("component prediction length mismatch")
    out: list[dict[str, Any]] = []
    for fixture, xg_pred, v1_pred in zip(batch, xg_predictions, v1_predictions):
        dynamic = xg_pred.get("dynamic") or {}
        fallback = bool(dynamic.get("fallback_exact_v1"))
        if fallback:
            prediction = copy.deepcopy(v1_pred)
            if prediction != v1_pred:
                raise FormalFusionError("fallback copy mismatch")
            route = "FROZEN_V1_EXACT_FALLBACK"
        else:
            prediction = blend_active_predictions(v1_pred, xg_pred)
            route = "FUSION_V2_ACTIVE"
        out.append({
            "fixture_id": str(getattr(fixture, "fixture_id")),
            "prediction": prediction,
            "audit": {
                "route": route,
                "xg_weight": FUSION_WEIGHT if not fallback else 0.0,
                "v1_weight": 1.0 - FUSION_WEIGHT if not fallback else 1.0,
                "fallback_exact_v1": fallback,
                "formal_enablement": FORMAL_ENABLEMENT,
                "frozen_v1_head": EXPECTED_V1_HEAD,
                "research_acceptance_head": EXPECTED_RESEARCH_HEAD,
                "prospective_queue": False,
            },
        })
    return out
