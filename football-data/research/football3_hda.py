from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# This is intentionally a ZERO-LABEL production module. Target-label scoring belongs
# only in a separately preregistered football3 V2 experiment runner/helper.
FOOTBALL3_ZERO_LABEL_ENGINEERING_SURFACE = "HDA_AGGREGATION_ONLY_NO_TARGET_LABEL_SCORING"

HDA_CLASS_ORDER: tuple[str, str, str] = ("HOME", "DRAW", "AWAY")
DEFAULT_PROBABILITY_TOLERANCE = 1e-12
DEFAULT_TIE_TOLERANCE = 1e-12
HDA_SCHEMA_VERSION = "football3_hda_v2"
SUPPORT_REGISTRY_SCHEMA_VERSION = "football3_hda_score_support_registry_v1"
CELL_SERIALIZATION_SCHEMA = "football3_hda_required_score_cells_v1"
SUPPORT_REGISTRY_PATH = Path(__file__).with_name("football3_hda_score_support_registry_v1.json")
COMPLETE_SUPPORT_KIND = "COMPLETE_FINITE_EMITTED_MATRIX"
PARTIAL_SUPPORT_KIND = "PARTIAL_UNRESOLVED_TAIL"
PARTIAL_HDA_UNRESOLVED_TAIL = "PARTIAL_HDA_UNRESOLVED_TAIL"
ROBUST_PARTIAL_TOP1 = "ROBUST_PARTIAL_TOP1"
TOP1_UNRESOLVED_DUE_TO_TAIL = "TOP1_UNRESOLVED_DUE_TO_TAIL"
TOP1_TIE = "TOP1_TIE"
TOP1 = "TOP1"
COMPLETE_HDA = "COMPLETE_HDA"
K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED = "K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED"


class HDAValidationError(ValueError):
    """Fail-closed HDA contract violation with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ScoreCell:
    home_goals: int
    away_goals: int
    probability: float

    @property
    def score(self) -> tuple[int, int]:
        return (self.home_goals, self.away_goals)


def _fail(code: str, message: str) -> None:
    raise HDAValidationError(code, message)


def _require_fixed_probability_tolerance(value: float | None) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("MISSING_OR_INVALID_PROBABILITY_TOLERANCE", "probability_tolerance must be explicitly declared")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        _fail("MISSING_OR_INVALID_PROBABILITY_TOLERANCE", "probability_tolerance must be finite and positive")
    if value != DEFAULT_PROBABILITY_TOLERANCE:
        _fail("UNFROZEN_PROBABILITY_TOLERANCE", f"probability_tolerance is frozen at {DEFAULT_PROBABILITY_TOLERANCE:g}")
    return value


def _require_fixed_tie_tolerance(value: float | None) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("MISSING_OR_INVALID_TIE_TOLERANCE", "tie_tolerance must be explicitly declared")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        _fail("MISSING_OR_INVALID_TIE_TOLERANCE", "tie_tolerance must be finite and positive")
    if value != DEFAULT_TIE_TOLERANCE:
        _fail("UNFROZEN_TIE_TOLERANCE", f"tie_tolerance is frozen at {DEFAULT_TIE_TOLERANCE:g}")
    return value


def _validate_class_order(class_order: Sequence[str] | None) -> tuple[str, str, str]:
    if class_order is None:
        _fail("MISSING_CLASS_ORDER", "class_order must be explicitly declared")
    order = tuple(class_order)
    if order != HDA_CLASS_ORDER:
        _fail("INVALID_CLASS_ORDER", f"class_order must be {list(HDA_CLASS_ORDER)}")
    return HDA_CLASS_ORDER


def canonical_required_score_cells(required_score_cells: Iterable[tuple[int, int]] | None) -> tuple[tuple[int, int], ...]:
    """Validate, sort and preserve exact support cells; duplicates are forbidden."""
    if required_score_cells is None:
        _fail("MISSING_SUPPORT_CONTRACT", "required_score_cells must be supplied")
    out: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for raw in required_score_cells:
        if not isinstance(raw, (tuple, list)) or len(raw) != 2:
            _fail("INVALID_REQUIRED_SCORE_CELL", f"invalid required score cell {raw!r}")
        h, a = raw
        if type(h) is not int or type(a) is not int or h < 0 or a < 0:
            _fail("INVALID_REQUIRED_SCORE_CELL", f"invalid required score cell {raw!r}")
        cell = (h, a)
        if cell in seen:
            _fail("DUPLICATE_REQUIRED_SCORE_CELL", f"duplicate required score cell {cell}")
        seen.add(cell)
        out.append(cell)
    if not out:
        _fail("EMPTY_SUPPORT_CONTRACT", "required_score_cells cannot be empty")
    if not any(h == a for h, a in out):
        _fail("SUPPORT_CONTRACT_HAS_NO_DIAGONAL", "score-support contract must contain a diagonal cell")
    return tuple(sorted(out))


def canonical_support_bytes(required_score_cells: Iterable[tuple[int, int]] | None) -> bytes:
    cells = canonical_required_score_cells(required_score_cells)
    return json.dumps([[h, a] for h, a in cells], separators=(",", ":"), ensure_ascii=True).encode("ascii")


def canonical_support_sha256(required_score_cells: Iterable[tuple[int, int]] | None) -> str:
    return hashlib.sha256(canonical_support_bytes(required_score_cells)).hexdigest()


def _generated_cells(generator: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(generator, dict) or generator.get("kind") != "total_leq":
        _fail("INVALID_SUPPORT_REGISTRY_GENERATOR", "support generator must be total_leq")
    max_total = generator.get("max_total_inclusive")
    if type(max_total) is not int or max_total < 0:
        _fail("INVALID_SUPPORT_REGISTRY_GENERATOR", "max_total_inclusive must be a nonnegative integer")
    return tuple((h, total - h) for total in range(max_total + 1) for h in range(total + 1))


def load_score_support_registry(path: str | Path | None = None) -> dict[str, dict[str, object]]:
    """Load and cryptographically self-check the frozen score-support registry."""
    registry_path = SUPPORT_REGISTRY_PATH if path is None else Path(path)
    if not registry_path.is_file():
        _fail("SUPPORT_REGISTRY_NOT_FOUND", f"support registry not found: {registry_path}")
    try:
        obj = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail("INVALID_SUPPORT_REGISTRY_JSON", f"cannot parse support registry: {exc}")
    if not isinstance(obj, dict):
        _fail("INVALID_SUPPORT_REGISTRY", "registry root must be an object")
    if obj.get("registry_schema_version") != SUPPORT_REGISTRY_SCHEMA_VERSION:
        _fail("UNKNOWN_SUPPORT_REGISTRY_VERSION", "registry_schema_version is not the frozen version")
    if obj.get("hda_schema_version") != HDA_SCHEMA_VERSION:
        _fail("SUPPORT_REGISTRY_HDA_SCHEMA_MISMATCH", "registry HDA schema does not match production schema")
    if obj.get("cell_serialization_schema") != CELL_SERIALIZATION_SCHEMA:
        _fail("UNKNOWN_CELL_SERIALIZATION_SCHEMA", "cell serialization schema is not frozen")
    entries = obj.get("supports")
    if not isinstance(entries, list) or not entries:
        _fail("EMPTY_SUPPORT_REGISTRY", "support registry must contain entries")

    by_id: dict[str, dict[str, object]] = {}
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            _fail("INVALID_SUPPORT_REGISTRY_ENTRY", "support entry must be an object")
        name = entry.get("name")
        support_id = entry.get("support_id")
        expected_hash = entry.get("required_score_cells_sha256")
        kind = entry.get("support_kind")
        if not isinstance(name, str) or not name or not isinstance(support_id, str) or len(support_id) != 64:
            _fail("INVALID_SUPPORT_REGISTRY_ENTRY", "support name/id invalid")
        if name in names or support_id in by_id:
            _fail("DUPLICATE_SUPPORT_REGISTRY_ENTRY", f"duplicate support registry identity: {name}")
        names.add(name)
        generated = _generated_cells(entry.get("generator"))
        generated_hash = canonical_support_sha256(generated)
        if expected_hash != generated_hash or support_id != generated_hash:
            _fail("SUPPORT_REGISTRY_HASH_MISMATCH", f"registry support {name} does not match canonical cell SHA-256")
        if entry.get("cell_count") != len(generated):
            _fail("SUPPORT_REGISTRY_CELL_COUNT_MISMATCH", f"registry support {name} has wrong cell_count")
        unresolved = entry.get("unresolved_tail")
        policy = entry.get("tail_probability_policy")
        if kind == COMPLETE_SUPPORT_KIND:
            if unresolved is not False or policy != "ZERO":
                _fail("SUPPORT_REGISTRY_TAIL_POLICY_MISMATCH", f"complete support {name} has invalid tail policy")
        elif kind == PARTIAL_SUPPORT_KIND:
            if unresolved is not True or policy != "POSITIVE":
                _fail("SUPPORT_REGISTRY_TAIL_POLICY_MISMATCH", f"partial support {name} has invalid tail policy")
            min_total = entry.get("unresolved_tail_min_total")
            max_known = max(h + a for h, a in generated)
            if type(min_total) is not int or min_total != max_known + 1:
                _fail("SUPPORT_REGISTRY_TAIL_BOUNDARY_MISMATCH", f"partial support {name} has invalid tail boundary")
        else:
            _fail("UNKNOWN_SUPPORT_KIND", f"unknown support kind {kind!r}")
        enriched = dict(entry)
        enriched["required_score_cells"] = tuple(sorted(generated))
        by_id[support_id] = enriched
    return by_id


def _validate_support_contract(
    *,
    score_support_id: str | None,
    schema_version: str | None,
    required_score_cells: Iterable[tuple[int, int]] | None,
    unresolved_tail: bool | None,
) -> tuple[dict[str, object], tuple[tuple[int, int], ...]]:
    if schema_version != HDA_SCHEMA_VERSION:
        _fail("INVALID_HDA_SCHEMA_VERSION", f"schema_version must be exactly {HDA_SCHEMA_VERSION}")
    if not isinstance(score_support_id, str) or not score_support_id:
        _fail("MISSING_SCORE_SUPPORT_IDENTITY", "score_support_id must be the canonical registered SHA-256")
    registry = load_score_support_registry()
    if score_support_id not in registry:
        _fail("UNKNOWN_SCORE_SUPPORT_ID", f"score_support_id is not registered: {score_support_id}")
    required = canonical_required_score_cells(required_score_cells)
    caller_hash = canonical_support_sha256(required)
    if caller_hash != score_support_id:
        _fail("SUPPORT_ID_REQUIRED_CELLS_HASH_MISMATCH", "score_support_id does not match required_score_cells canonical SHA-256")
    entry = registry[score_support_id]
    registered = entry["required_score_cells"]
    assert isinstance(registered, tuple)
    if required != registered:
        _fail("REQUIRED_SCORE_CELLS_REGISTRY_MISMATCH", "required_score_cells do not match the frozen registry")
    expected_tail = entry["unresolved_tail"]
    if type(unresolved_tail) is not bool:
        _fail("MISSING_TAIL_STATUS", "unresolved_tail must be explicitly declared as bool")
    if unresolved_tail is not expected_tail:
        _fail("SUPPORT_TAIL_STATUS_MISMATCH", "unresolved_tail disagrees with the registered support contract")
    return entry, required


def _coerce_cell(raw: ScoreCell | Mapping[str, object]) -> ScoreCell:
    if isinstance(raw, ScoreCell):
        cell = raw
    elif isinstance(raw, Mapping):
        missing = [k for k in ("home_goals", "away_goals", "probability") if k not in raw]
        if missing:
            _fail("MISSING_SCORE_CELL_FIELD", f"score cell missing fields: {missing}")
        cell = ScoreCell(raw["home_goals"], raw["away_goals"], raw["probability"])  # type: ignore[arg-type]
    else:
        _fail("INVALID_SCORE_CELL", f"score cell must be ScoreCell or mapping, got {type(raw).__name__}")
    h, a, p = cell.home_goals, cell.away_goals, cell.probability
    if type(h) is not int or type(a) is not int or h < 0 or a < 0:
        _fail("INVALID_SCORE", f"score must contain nonnegative integers, got {(h, a)!r}")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        _fail("INVALID_PROBABILITY", f"probability for {(h, a)} must be numeric")
    p = float(p)
    if not math.isfinite(p):
        _fail("NONFINITE_PROBABILITY", f"probability for {(h, a)} must be finite")
    if p < 0:
        _fail("NEGATIVE_PROBABILITY", f"probability for {(h, a)} is negative")
    if p > 1:
        _fail("PROBABILITY_ABOVE_ONE", f"probability for {(h, a)} exceeds 1")
    return ScoreCell(h, a, p)


def validate_score_matrix(
    score_cells: Iterable[ScoreCell | Mapping[str, object]],
    *,
    unresolved_tail: bool | None,
    tail_probability: float | None,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    score_support_id: str | None,
    schema_version: str | None,
    required_score_cells: Iterable[tuple[int, int]] | None,
) -> dict[str, object]:
    """Validate a score matrix against a frozen registered support. Never normalize."""
    tol = _require_fixed_probability_tolerance(probability_tolerance)
    order = _validate_class_order(class_order)
    support, required = _validate_support_contract(
        score_support_id=score_support_id,
        schema_version=schema_version,
        required_score_cells=required_score_cells,
        unresolved_tail=unresolved_tail,
    )
    if tail_probability is None or isinstance(tail_probability, bool) or not isinstance(tail_probability, (int, float)):
        _fail("MISSING_OR_INVALID_TAIL_PROBABILITY", "tail_probability must be explicitly declared")
    tail_probability = float(tail_probability)
    if not math.isfinite(tail_probability):
        _fail("NONFINITE_TAIL_PROBABILITY", "tail_probability must be finite")
    if tail_probability < 0 or tail_probability > 1:
        _fail("INVALID_TAIL_PROBABILITY", "tail_probability must lie in [0,1]")
    if support["support_kind"] == COMPLETE_SUPPORT_KIND and abs(tail_probability) > tol:
        _fail("TAIL_STATUS_MASS_CONFLICT", "complete registered support requires zero unresolved tail mass")
    if support["support_kind"] == PARTIAL_SUPPORT_KIND and tail_probability <= tol:
        _fail("UNRESOLVED_TAIL_REQUIRES_POSITIVE_MASS", "partial registered support requires positive unresolved tail mass")

    cells: list[ScoreCell] = []
    by_score: dict[tuple[int, int], float] = {}
    for raw in score_cells:
        cell = _coerce_cell(raw)
        if cell.score in by_score:
            if by_score[cell.score] == cell.probability:
                _fail("DUPLICATE_SCORE_CELL", f"duplicate score cell {cell.score}")
            _fail("CONFLICTING_DUPLICATE_SCORE_CELL", f"conflicting probabilities for score cell {cell.score}")
        by_score[cell.score] = cell.probability
        cells.append(cell)
    if not cells:
        _fail("EMPTY_SCORE_MATRIX", "score matrix cannot be empty")

    required_set = set(required)
    present = set(by_score)
    missing_diagonal = sorted(cell for cell in required_set - present if cell[0] == cell[1])
    if missing_diagonal:
        _fail("MISSING_REQUIRED_DIAGONAL", f"missing required diagonal cells: {missing_diagonal}")
    missing = sorted(required_set - present)
    if missing:
        _fail("MISSING_REQUIRED_SCORE_CELL", f"missing required score cells: {missing}")
    unknown = sorted(present - required_set)
    if unknown:
        _fail("SCORE_CELL_OUTSIDE_SUPPORT", f"score cells outside declared support: {unknown}")

    raw_known_sum = math.fsum(cell.probability for cell in cells)
    raw_total = raw_known_sum + tail_probability
    residual = raw_total - 1.0
    if abs(residual) > tol:
        _fail("PROBABILITY_MASS_NOT_CONSERVED", f"raw_known_sum={raw_known_sum:.17g}, tail_probability={tail_probability:.17g}, raw_total={raw_total:.17g}, residual={residual:.3g}")

    return {
        "status": "VALID_SCORE_MATRIX",
        "class_order": list(order),
        "probability_tolerance": tol,
        "score_support_id": score_support_id,
        "schema_version": schema_version,
        "support_kind": support["support_kind"],
        "support_name": support["name"],
        "support_contract_sha256": score_support_id,
        "unresolved_tail": unresolved_tail,
        "tail_probability": tail_probability,
        "raw_known_probability_sum": raw_known_sum,
        "raw_total_probability": raw_total,
        "probability_residual": residual,
        "score_cell_count": len(cells),
        "required_score_cell_count": len(required),
        "cells": cells,
    }


def _validate_hda_probability_mapping(probabilities: Mapping[str, float], *, class_order: Sequence[str] | None, probability_tolerance: float | None) -> dict[str, float]:
    tol = _require_fixed_probability_tolerance(probability_tolerance)
    order = _validate_class_order(class_order)
    if set(probabilities) != set(order):
        _fail("MISSING_OR_EXTRA_HDA_PROBABILITY", f"HDA probability keys must be exactly {list(order)}")
    out: dict[str, float] = {}
    for label in order:
        value = probabilities[label]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail("INVALID_HDA_PROBABILITY", f"{label} probability must be numeric")
        value = float(value)
        if not math.isfinite(value):
            _fail("NONFINITE_HDA_PROBABILITY", f"{label} probability must be finite")
        if value < 0 or value > 1:
            _fail("INVALID_HDA_PROBABILITY", f"{label} probability must lie in [0,1]")
        out[label] = value
    total = math.fsum(out.values())
    if abs(total - 1.0) > tol:
        _fail("HDA_PROBABILITY_MASS_NOT_CONSERVED", f"HDA probability sum is {total:.17g}")
    return out


def choose_hda_top1(probabilities: Mapping[str, float], *, class_order: Sequence[str] | None, probability_tolerance: float | None, tie_tolerance: float | None) -> dict[str, object]:
    """Choose HDA Top1 without allowing array order to break a numerical tie."""
    probs = _validate_hda_probability_mapping(probabilities, class_order=class_order, probability_tolerance=probability_tolerance)
    tie_tol = _require_fixed_tie_tolerance(tie_tolerance)
    max_prob = max(probs.values())
    tied = [label for label in HDA_CLASS_ORDER if max_prob - probs[label] <= tie_tol]
    ordered_values = sorted(probs.values(), reverse=True)
    margin = ordered_values[0] - ordered_values[1]
    if margin <= tie_tol:
        return {"status": TOP1_TIE, "top1_category": None, "tied_categories": tied, "top1_margin": margin, "near_tie": True, "tie_tolerance": tie_tol}
    winner = max(probs, key=probs.__getitem__)
    return {"status": TOP1, "top1_category": winner, "tied_categories": [], "top1_margin": margin, "near_tie": False, "tie_tolerance": tie_tol}


def _score_class(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HOME"
    if home_goals == away_goals:
        return "DRAW"
    return "AWAY"


def _max_specific_score(cells: Sequence[ScoreCell], tie_tolerance: float) -> dict[str, object]:
    max_p = max(cell.probability for cell in cells)
    maxima = sorted(cell.score for cell in cells if max_p - cell.probability <= tie_tolerance)
    if len(maxima) != 1:
        return {"max_specific_score": None, "max_specific_scores": maxima, "max_specific_score_probability": max_p, "max_specific_score_is_draw": None, "exact_score_top1_category": None, "exact_score_top1_status": "EXACT_SCORE_TOP1_TIE"}
    score = maxima[0]
    category = _score_class(*score)
    return {"max_specific_score": score, "max_specific_scores": maxima, "max_specific_score_probability": max_p, "max_specific_score_is_draw": score[0] == score[1], "exact_score_top1_category": category, "exact_score_top1_status": "EXACT_SCORE_TOP1"}


def aggregate_score_matrix_to_hda(
    score_cells: Iterable[ScoreCell | Mapping[str, object]],
    *,
    unresolved_tail: bool | None,
    tail_probability: float | None,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
    score_support_id: str | None,
    schema_version: str | None,
    required_score_cells: Iterable[tuple[int, int]] | None,
) -> dict[str, object]:
    """Aggregate a registered score matrix into H/D/A without labels, fitting or I/O."""
    validation = validate_score_matrix(
        score_cells,
        unresolved_tail=unresolved_tail,
        tail_probability=tail_probability,
        class_order=class_order,
        probability_tolerance=probability_tolerance,
        score_support_id=score_support_id,
        schema_version=schema_version,
        required_score_cells=required_score_cells,
    )
    tie_tol = _require_fixed_tie_tolerance(tie_tolerance)
    cells = validation["cells"]
    assert isinstance(cells, list)
    known_values: dict[str, list[float]] = {label: [] for label in HDA_CLASS_ORDER}
    for cell in cells:
        assert isinstance(cell, ScoreCell)
        known_values[_score_class(cell.home_goals, cell.away_goals)].append(cell.probability)
    known = {label: math.fsum(known_values[label]) for label in HDA_CLASS_ORDER}

    exact = _max_specific_score(cells, tie_tol)
    tail = float(validation["tail_probability"])
    base: dict[str, object] = {
        "class_order": list(HDA_CLASS_ORDER),
        "score_support_id": validation["score_support_id"],
        "schema_version": validation["schema_version"],
        "support_kind": validation["support_kind"],
        "support_name": validation["support_name"],
        "support_contract_sha256": validation["support_contract_sha256"],
        "probability_tolerance": validation["probability_tolerance"],
        "tie_tolerance": tie_tol,
        "raw_known_probability_sum": validation["raw_known_probability_sum"],
        "raw_total_probability": validation["raw_total_probability"],
        "probability_residual": validation["probability_residual"],
        "diagonal_probability_sum": known["DRAW"],
        "known_home_mass": known["HOME"],
        "known_draw_mass": known["DRAW"],
        "known_away_mass": known["AWAY"],
        "unresolved_tail_mass": tail,
        **exact,
    }
    if bool(validation["unresolved_tail"]):
        bounds = {label: {"lower": known[label], "upper": known[label] + tail} for label in HDA_CLASS_ORDER}
        robust: str | None = None
        robust_margin: float | None = None
        for label in HDA_CLASS_ORDER:
            lower = bounds[label]["lower"]
            competitor_upper = max(bounds[other]["upper"] for other in HDA_CLASS_ORDER if other != label)
            margin = lower - competitor_upper
            if margin > tie_tol:
                robust = label
                robust_margin = margin
                break
        base.update({"status": PARTIAL_HDA_UNRESOLVED_TAIL, "home_probability": None, "draw_probability": None, "away_probability": None, "hda_probability_sum": None, "hda_bounds": bounds, "top1_status": ROBUST_PARTIAL_TOP1 if robust is not None else TOP1_UNRESOLVED_DUE_TO_TAIL, "top1_category": robust, "top1_margin": robust_margin, "near_tie": None, "tied_categories": []})
    else:
        probs = dict(known)
        decision = choose_hda_top1(probs, class_order=HDA_CLASS_ORDER, probability_tolerance=probability_tolerance, tie_tolerance=tie_tolerance)
        base.update({"status": COMPLETE_HDA, "home_probability": probs["HOME"], "draw_probability": probs["DRAW"], "away_probability": probs["AWAY"], "hda_probability_sum": math.fsum(probs.values()), "hda_bounds": {label: {"lower": probs[label], "upper": probs[label]} for label in HDA_CLASS_ORDER}, "top1_status": decision["status"], "top1_category": decision["top1_category"], "top1_margin": decision["top1_margin"], "near_tie": decision["near_tie"], "tied_categories": decision["tied_categories"]})
    exact_category = base["exact_score_top1_category"]
    hda_top1 = base["top1_category"]
    base["exact_score_top1_and_hda_top1_agree"] = None if exact_category is None or hda_top1 is None else exact_category == hda_top1
    return base
