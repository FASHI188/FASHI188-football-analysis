from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from player_strength import PlayerVector, lineup_components

REFERENCE_MATCHES = 8
MIN_REFERENCE_LINEUPS = 3
MAX_LOG_MU_RESIDUAL = 0.25
EPS = 1e-12


class CandidateBContractError(RuntimeError):
    pass


def _dt(text: str) -> datetime:
    d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise CandidateBContractError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _role(row: dict[str, Any]) -> str:
    dist = row.get("role_distribution") or {}
    if not isinstance(dist, dict) or not dist:
        return "UNK"
    return max(dist.items(), key=lambda x: (float(x[1]), str(x[0])))[0]


def _validate_expected(rows: list[dict[str, Any]], cutoff: str) -> None:
    seen: set[str] = set()
    co = _dt(cutoff)
    required = {"player_id", "starting_probability", "availability_probability", "expected_minutes_distribution", "uncertainty", "known_at"}
    for row in rows:
        if not required.issubset(row):
            raise CandidateBContractError("expected-lineup row missing fields")
        pid = str(row["player_id"])
        if pid in seen:
            raise CandidateBContractError("duplicate expected-lineup player identity")
        seen.add(pid)
        if _dt(str(row["known_at"])) >= co:
            raise CandidateBContractError("expected-lineup evidence not strictly pre-cutoff")
        for key in ("starting_probability", "availability_probability"):
            x = float(row[key])
            if not math.isfinite(x) or not 0.0 <= x <= 1.0:
                raise CandidateBContractError(f"{key} outside [0,1]")
        minutes = float((row.get("expected_minutes_distribution") or {}).get("mean", -1.0))
        if not math.isfinite(minutes) or minutes < 0.0:
            raise CandidateBContractError("invalid expected minutes")
        unc = float(row.get("uncertainty", 1.0))
        if not math.isfinite(unc) or unc < 0.0:
            raise CandidateBContractError("invalid lineup uncertainty")


def _rank(rows: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    _validate_expected(rows, cutoff)
    return sorted(
        rows,
        key=lambda r: (
            float(r["starting_probability"]) * float(r["availability_probability"]),
            float((r.get("expected_minutes_distribution") or {}).get("mean", 0.0)),
            str(r["player_id"]),
        ),
        reverse=True,
    )


def _presence(row: dict[str, Any]) -> float:
    return max(0.0, min(1.0, float(row["starting_probability"]) * float(row["availability_probability"])))


def _entry_mass(row: dict[str, Any]) -> float:
    minutes = max(0.0, min(90.0, float((row.get("expected_minutes_distribution") or {}).get("mean", 0.0))))
    return max(0.0, min(1.0, float(row["availability_probability"]))) * max(0.0, 1.0 - float(row["starting_probability"])) * (minutes / 90.0)


def _replace_candidate(ranked: list[dict[str, Any]], starters: list[dict[str, Any]]) -> tuple[list[str] | None, float, dict[str, Any] | None]:
    starter_ids = {str(x["player_id"]) for x in starters}
    benches = [x for x in ranked if str(x["player_id"]) not in starter_ids]
    if not benches:
        return None, 0.0, None
    bench = max(benches, key=lambda x: (_entry_mass(x), str(x["player_id"])))
    if _entry_mass(bench) <= EPS:
        return None, 0.0, None
    br = _role(bench)
    compatible = [x for x in starters if br != "UNK" and _role(x) == br]
    pool = compatible or starters
    replace = min(pool, key=lambda x: (_presence(x), str(x["player_id"])))
    ratio = min(1.0, _entry_mass(bench) / max(_presence(replace), EPS))
    alt = [str(bench["player_id"]) if str(x["player_id"]) == str(replace["player_id"]) else str(x["player_id"]) for x in starters]
    detail = {
        "bench_player_id": str(bench["player_id"]),
        "replaced_player_id": str(replace["player_id"]),
        "bench_entry_mass": _entry_mass(bench),
        "replaced_presence_mass": _presence(replace),
        "role_match": bool(compatible),
    }
    return alt, ratio, detail


@dataclass(frozen=True)
class MassScenario:
    scenario_id: str
    probability: float
    home_player_ids: list[str]
    away_player_ids: list[str]
    source: str
    uncertainty: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResidualEffect:
    active: bool
    log_mu_home_delta: float
    log_mu_away_delta: float
    uncertainty: float
    reason: str
    home_reference_n: int
    away_reference_n: int
    evidence_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_probability_mass_scenarios(home_expected: list[dict[str, Any]], away_expected: list[dict[str, Any]], *, cutoff: str) -> list[MassScenario]:
    """B2: appearance/absence/substitution probability mass only; no player ability input."""
    hr = _rank(home_expected, cutoff)
    ar = _rank(away_expected, cutoff)
    if len(hr) < 11 or len(ar) < 11:
        return []
    hs = hr[:11]
    ass = ar[:11]
    h0 = [str(x["player_id"]) for x in hs]
    a0 = [str(x["player_id"]) for x in ass]
    halt, hratio, hdetail = _replace_candidate(hr, hs)
    aalt, aratio, adetail = _replace_candidate(ar, ass)
    specs: list[tuple[list[str], list[str], float, str, dict[str, Any]]] = [(h0, a0, 1.0, "MODAL", {})]
    if halt is not None and hratio > EPS:
        specs.append((halt, a0, hratio, "HOME_ABSENCE_SUB_MASS", hdetail or {}))
    if aalt is not None and aratio > EPS:
        specs.append((h0, aalt, aratio, "AWAY_ABSENCE_SUB_MASS", adetail or {}))
    if halt is not None and aalt is not None and hratio > EPS and aratio > EPS:
        specs.append((halt, aalt, hratio * aratio, "BOTH_ABSENCE_SUB_MASS", {"home": hdetail, "away": adetail}))
    total = sum(max(0.0, x[2]) for x in specs)
    if total <= 0.0:
        return []
    expected_unc = sum(float(x.get("uncertainty", 1.0)) for x in hs + ass) / 22.0
    out: list[MassScenario] = []
    for home, away, w, source, detail in specs:
        payload = {"cutoff": cutoff, "home": home, "away": away, "source": source, "detail": detail}
        out.append(MassScenario("b2_" + _sha(payload)[:16], w / total, home, away, source, expected_unc))
    if abs(sum(x.probability for x in out) - 1.0) > 1e-10:
        raise CandidateBContractError("B2 probability mass failed normalization")
    return out


def rolling_reference_lineups(usage: dict[str, list[dict[str, Any]]], team_id: str, *, cutoff: str, limit: int = REFERENCE_MATCHES) -> list[list[str]]:
    co = _dt(cutoff)
    valid: list[tuple[datetime, str, list[str]]] = []
    for rec in usage.get(str(team_id), []):
        known = _dt(str(rec.get("known_at", "")))
        if known >= co:
            raise CandidateBContractError("reference lineup evidence not strictly pre-cutoff")
        players = rec.get("players") or []
        starters = [str(x["player_id"]) for x in players if bool(x.get("started"))]
        if len(starters) == 11 and len(set(starters)) == 11:
            valid.append((known, str(rec.get("match_id", "")), starters))
    valid.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in valid[-max(1, int(limit)):]]


def _components_strict(vectors: dict[str, PlayerVector], lineup: list[str]) -> tuple[float, float, float, float] | None:
    if len(lineup) != 11 or len(set(lineup)) != 11 or any(pid not in vectors for pid in lineup):
        return None
    return lineup_components(vectors, lineup)


def _side_residual(vectors: dict[str, PlayerVector], current: list[str], references: list[list[str]]) -> tuple[tuple[float, float, float], float] | None:
    cur = _components_strict(vectors, current)
    if cur is None:
        return None
    ref_components = [z for z in (_components_strict(vectors, x) for x in references) if z is not None]
    if len(ref_components) < MIN_REFERENCE_LINEUPS:
        return None
    ref = tuple(sum(x[i] for x in ref_components) / len(ref_components) for i in range(4))
    residual = (cur[0] - ref[0], cur[1] - ref[1], cur[2] - ref[2])
    dispersion = sum(abs(x[i] - ref[i]) for x in ref_components for i in range(3)) / (3.0 * len(ref_components))
    uncertainty = max(0.0, cur[3] + dispersion)
    return residual, uncertainty


def capability_residual(*, vectors: dict[str, PlayerVector], usage: dict[str, list[dict[str, Any]]], home_team_id: str, away_team_id: str, home_player_ids: list[str], away_player_ids: list[str], cutoff: str) -> ResidualEffect:
    """B1: capability only as residual to each team's rolling pre-cutoff reference XI."""
    home_refs = rolling_reference_lineups(usage, str(home_team_id), cutoff=cutoff)
    away_refs = rolling_reference_lineups(usage, str(away_team_id), cutoff=cutoff)
    hs = _side_residual(vectors, home_player_ids, home_refs)
    aw = _side_residual(vectors, away_player_ids, away_refs)
    if hs is None or aw is None:
        return ResidualEffect(False, 0.0, 0.0, 1.0, "INSUFFICIENT_SHARED_PIT_REFERENCE", len(home_refs), len(away_refs), None)
    (ha, hd, hg), hu = hs
    (aa, ad, ag), au = aw
    dh = max(-MAX_LOG_MU_RESIDUAL, min(MAX_LOG_MU_RESIDUAL, ha - ad - ag))
    da = max(-MAX_LOG_MU_RESIDUAL, min(MAX_LOG_MU_RESIDUAL, aa - hd - hg))
    evidence = {
        "cutoff": cutoff,
        "home_team_id": str(home_team_id),
        "away_team_id": str(away_team_id),
        "home_player_ids": list(home_player_ids),
        "away_player_ids": list(away_player_ids),
        "home_reference_lineups": home_refs,
        "away_reference_lineups": away_refs,
        "home_residual_components": [ha, hd, hg],
        "away_residual_components": [aa, ad, ag],
    }
    return ResidualEffect(True, dh, da, (hu + au) / 2.0, "ACTIVE", len(home_refs), len(away_refs), _sha(evidence))


def candidate_contract() -> dict[str, Any]:
    obj = {
        "schema_version": "football3-context-translator-candidate-b-v1",
        "status": "RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC",
        "b1": "player capability residual relative to rolling reference XI only",
        "b2": "appearance/absence/substitution probability-mass redistribution only; no player ability input",
        "shared_contract": "same PIT cutoff, player identity, rolling reference lineups, uncertainty semantics",
        "insufficient_data": "ZERO_CORRECTION_EXACT_PROTECTED_V2_FALLBACK",
        "forbidden": ["fixed_outcome_multiplier", "result_patch", "direct_1x2_probability_patch", "label_conditioned_adjustment"],
        "reference_matches": REFERENCE_MATCHES,
        "minimum_reference_lineups": MIN_REFERENCE_LINEUPS,
        "max_log_mu_residual_safety_bound": MAX_LOG_MU_RESIDUAL,
    }
    obj["contract_sha256"] = _sha(obj)
    return obj
