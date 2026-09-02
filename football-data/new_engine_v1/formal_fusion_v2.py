from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from historical_xg_challenger_v1 import historical_xg_challenger as hxg
from new_engine_v1 import pure_engine as v1_engine

FOOTBALL3_FORMAL_WIRING_CONTRACT = "football-data/historical_xg_fusion_v2/contracts/FORMAL_FUSION_V2_WIRING.json"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FUSION_IDENTITY_PATH = ROOT / "historical_xg_fusion_v2" / "data" / "XG_FUSION_V2_DATA_IDENTITY.json"
MODEL_LOCK_PATH = HERE / "forward" / "model_lock.json"

FUSION_WEIGHT = 0.75
EXPECTED_V1_ENGINE_SHA256 = "cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318"
EXPECTED_V1_HEAD = "22f639304d2e32fc952dbec2255153ee45dcd41a"
EXPECTED_RESEARCH_HEAD = "d3b3e322f78c48b91477ef6e11054e51ac00fd85"
FORMAL_ENABLEMENT = False


class FormalFusionError(RuntimeError):
    pass


def _require_candidate_state(state: hxg.ChallengerState) -> hxg.ChallengerState:
    if type(state) is not hxg.ChallengerState:
        raise FormalFusionError("unknown candidate state object")
    return state


def _fixture_identity(fixture: hxg.FixtureRow) -> tuple[str, str, str, str, str, str]:
    if type(fixture) is not hxg.FixtureRow:
        raise FormalFusionError("unknown fixture object")
    try:
        fixture_id = fixture.fixture_id
        competition_id = fixture.competition_id
        season = fixture.season
        kickoff = fixture.kickoff
        home_team_id = fixture.home_team_id
        away_team_id = fixture.away_team_id
    except AttributeError as exc:
        raise FormalFusionError("fixture contract field missing") from exc

    for name, value in (
        ("fixture_id", fixture_id),
        ("competition_id", competition_id),
        ("season", season),
        ("home_team_id", home_team_id),
        ("away_team_id", away_team_id),
    ):
        if type(value) is not str or not value.strip():
            raise FormalFusionError(f"fixture contract field invalid: {name}")
    if home_team_id == away_team_id:
        raise FormalFusionError("fixture home/away identity collision")
    if type(kickoff) is not datetime or kickoff.tzinfo is None:
        raise FormalFusionError("fixture kickoff must be timezone-aware datetime")
    kickoff_key = kickoff.astimezone(timezone.utc).isoformat()
    return fixture_id, competition_id, season, kickoff_key, home_team_id, away_team_id


def _prediction_identity(prediction: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    if type(prediction) is not dict:
        raise FormalFusionError("unknown component prediction object")
    try:
        values = (
            prediction["fixture_id"],
            prediction["competition_id"],
            prediction["season"],
            prediction["kickoff"],
            prediction["home_team_id"],
            prediction["away_team_id"],
        )
    except KeyError as exc:
        raise FormalFusionError("component prediction identity field missing") from exc
    if any(type(value) is not str for value in values):
        raise FormalFusionError("component prediction identity field type invalid")
    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise FormalFusionError("component prediction identity field empty")
    return normalized


def _require_prediction_matches_fixture(
    fixture: hxg.FixtureRow,
    prediction: dict[str, Any],
    *,
    component: str,
) -> None:
    expected = _fixture_identity(fixture)
    actual = _prediction_identity(prediction)
    if actual != expected:
        raise FormalFusionError(f"{component} prediction/fixture identity mismatch")


def _require_component_identity_match(
    v1_prediction: dict[str, Any],
    xg_prediction: dict[str, Any],
) -> None:
    if _prediction_identity(v1_prediction) != _prediction_identity(xg_prediction):
        raise FormalFusionError("V1/XG component identity mismatch")


def _read_required_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FormalFusionError(f"{label} unreadable") from exc
    if type(value) is not dict:
        raise FormalFusionError(f"{label} root must be object")
    return value


def load_frozen_contract() -> dict[str, Any]:
    identity = _read_required_json_object(FUSION_IDENTITY_PATH, "fusion identity")
    lock = _read_required_json_object(MODEL_LOCK_PATH, "model lock")
    try:
        frozen_v1 = identity["frozen_v1"]
        fusion_contract = identity["fusion_contract"]
        frozen_xg = identity["frozen_xg_parameters"]
        identity_formal_enablement = identity["formal_enablement"]
        identity_formal_weight = identity["formal_weight"]
        lock_engine_sha256 = lock["pure_engine_sha256"]
        lock_formal_activation = lock["formal_activation"]
    except KeyError as exc:
        raise FormalFusionError("frozen contract field missing") from exc
    if type(frozen_v1) is not dict or type(fusion_contract) is not dict or type(frozen_xg) is not dict:
        raise FormalFusionError("frozen contract object type invalid")
    try:
        frozen_v1_head = frozen_v1["head"]
        frozen_v1_engine_sha256 = frozen_v1["engine_sha256"]
        fusion_formula = fusion_contract["formula"]
        fusion_weight_grid = fusion_contract["weight_grid"]
    except KeyError as exc:
        raise FormalFusionError("frozen contract nested field missing") from exc
    if frozen_v1_head != EXPECTED_V1_HEAD:
        raise FormalFusionError("Frozen V1 HEAD drift")
    if frozen_v1_engine_sha256 != EXPECTED_V1_ENGINE_SHA256:
        raise FormalFusionError("Frozen V1 engine identity drift")
    if lock_engine_sha256 != EXPECTED_V1_ENGINE_SHA256:
        raise FormalFusionError("formal V1 model lock drift")
    if fusion_formula != "normalize((1-w)*p_V1 + w*p_XG)":
        raise FormalFusionError("fusion formula drift")
    if fusion_weight_grid != [0.25, 0.5, 0.75]:
        raise FormalFusionError("fusion weight grid drift")
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
    if identity_formal_enablement is not False or identity_formal_weight != 0:
        raise FormalFusionError("research identity unexpectedly formally enabled")
    if lock_formal_activation is not False:
        raise FormalFusionError("Frozen V1 formal activation unexpectedly enabled")
    return {"identity": identity, "lock": lock}


def new_candidate_state() -> hxg.ChallengerState:
    contract = load_frozen_contract()
    xg = contract["identity"]["frozen_xg_parameters"]
    params = hxg.XGParams(
        xg["dynamic_half_life_days"],
        xg["dynamic_prior_matches"],
        xg["dynamic_beta"],
        xg["dynamic_cross_season_shrink"],
    )
    return hxg.ChallengerState(v1_engine, dict(hxg.EXPECTED_V1_PARAMS), params)


def apply_completed_xg_batch(
    state: hxg.ChallengerState,
    fixtures: Iterable[hxg.FixtureRow],
    released_labels: dict[str, Any],
    available_at: datetime,
) -> None:
    """Apply only a fully released, already-ended kickoff batch.

    ChallengerState enforces identity, release-time and same-kickoff isolation.
    This wrapper deliberately provides no prospective queue or label-fetch path.
    """
    _require_candidate_state(state)
    batch = list(fixtures)
    for fixture in batch:
        _fixture_identity(fixture)
    if type(released_labels) is not dict:
        raise FormalFusionError("released label container must be dict")
    if not batch:
        if released_labels:
            raise FormalFusionError("released labels supplied for empty fixture batch")
        return
    expected_ids = {fixture.fixture_id for fixture in batch}
    if set(released_labels) != expected_ids:
        raise FormalFusionError("released label identity set mismatch")
    state.apply_released_batch(batch, released_labels, available_at)


def _matrix_key(cell: dict[str, Any]) -> tuple[int, int]:
    if type(cell) is not dict:
        raise FormalFusionError("invalid score matrix cell object")
    try:
        return int(cell["home_goals"]), int(cell["away_goals"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalFusionError("invalid score matrix cell identity") from exc


def _canonical_matrix(matrix: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    if type(matrix) is not list:
        raise FormalFusionError("invalid score matrix object")
    out: dict[tuple[int, int], float] = {}
    for cell in matrix:
        key = _matrix_key(cell)
        try:
            p = float(cell["probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalFusionError("invalid score matrix probability") from exc
        if key in out or not math.isfinite(p) or p < 0:
            raise FormalFusionError("invalid score matrix")
        out[key] = p
    total = math.fsum(out.values())
    if not math.isfinite(total) or total <= 0:
        raise FormalFusionError("invalid score matrix mass")
    return out


def _normalized_triplet(prediction: dict[str, Any]) -> tuple[float, float, float]:
    if type(prediction) is not dict:
        raise FormalFusionError("invalid 1X2 prediction object")
    try:
        vals = tuple(float(prediction[k]) for k in ("p_home", "p_draw", "p_away"))
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalFusionError("invalid 1X2 probability field") from exc
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
    _require_component_identity_match(v1_prediction, xg_prediction)
    try:
        v1_matrix = v1_prediction["score_matrix"]
        xg_matrix = xg_prediction["score_matrix"]
    except KeyError as exc:
        raise FormalFusionError("component score matrix missing") from exc
    v1m = _canonical_matrix(v1_matrix)
    xgm = _canonical_matrix(xg_matrix)
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

    identity = _prediction_identity(v1_prediction)
    return {
        "schema_version": "football3-historical-xg-fusion-v2-formal-candidate-v1",
        "engine": "Football3-Historical-XG-Fusion-V2",
        "fixture_id": identity[0],
        "competition_id": identity[1],
        "season": identity[2],
        "kickoff": identity[3],
        "home_team_id": identity[4],
        "away_team_id": identity[5],
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


def predict_formal_batch(
    state: hxg.ChallengerState,
    fixtures: Iterable[hxg.FixtureRow],
) -> list[dict[str, Any]]:
    _require_candidate_state(state)
    batch = list(fixtures)
    for fixture in batch:
        _fixture_identity(fixture)
    xg_predictions, v1_predictions = state.predict_batch(batch, include_matrix=True)
    if type(xg_predictions) is not list or type(v1_predictions) is not list:
        raise FormalFusionError("component prediction container mismatch")
    if len(xg_predictions) != len(v1_predictions) or len(batch) != len(v1_predictions):
        raise FormalFusionError("component prediction length mismatch")
    out: list[dict[str, Any]] = []
    for fixture, xg_pred, v1_pred in zip(batch, xg_predictions, v1_predictions):
        _require_prediction_matches_fixture(fixture, xg_pred, component="XG")
        _require_prediction_matches_fixture(fixture, v1_pred, component="V1")
        _require_component_identity_match(v1_pred, xg_pred)
        try:
            dynamic = xg_pred["dynamic"]
        except KeyError as exc:
            raise FormalFusionError("XG dynamic metadata missing") from exc
        if type(dynamic) is not dict or "fallback_exact_v1" not in dynamic:
            raise FormalFusionError("XG fallback metadata missing")
        fallback_flag = dynamic["fallback_exact_v1"]
        if type(fallback_flag) is not bool:
            raise FormalFusionError("XG fallback metadata invalid")
        fallback = fallback_flag
        if fallback:
            prediction = copy.deepcopy(v1_pred)
            if prediction != v1_pred:
                raise FormalFusionError("fallback copy mismatch")
            route = "FROZEN_V1_EXACT_FALLBACK"
        else:
            prediction = blend_active_predictions(v1_pred, xg_pred)
            route = "FUSION_V2_ACTIVE"
        out.append({
            "fixture_id": fixture.fixture_id,
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
