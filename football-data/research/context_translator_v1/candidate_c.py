from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any
from datetime import datetime, timezone

from candidate_b import MIN_REFERENCE_LINEUPS, MAX_LOG_MU_RESIDUAL, rolling_reference_lineups
from player_strength import ATTACK_DIMS, DEFENCE_DIMS, PlayerVector

EPS = 1e-12
COMPONENT_BOUND = 0.12
UNCERTAINTY_BY_GRADE = {
    "CONFIRMED_LINEUP_PIT": 0.10,
    "POSSIBLE_XI_PIT": 0.35,
    "TEAM_NEWS_AVAILABILITY_PIT": 0.65,
    "NO_USABLE_ROSTER_EVIDENCE": 1.00,
}
TEMPO_DIMS = ("passing_progression", "carrying_progression", "pressing", "possession_retention_risk")


class CandidateCContractError(RuntimeError):
    pass


def _dt(text: str) -> datetime:
    d=datetime.fromisoformat(str(text).replace("Z","+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:raise CandidateCContractError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _clip(x: float, bound: float = COMPONENT_BOUND) -> float:
    return max(-bound, min(bound, float(x)))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _axes(v: PlayerVector) -> tuple[float, float, float]:
    attack = _mean([float(v.values[d]) for d in ATTACK_DIMS])
    defence = _mean([float(v.values[d]) for d in DEFENCE_DIMS])
    tempo = (
        float(v.values["passing_progression"])
        + float(v.values["carrying_progression"])
        + float(v.values["pressing"])
        - float(v.values["possession_retention_risk"])
    ) / 4.0
    return attack, defence, tempo


def _team_pool(vectors: dict[str, PlayerVector], team_id: str) -> list[PlayerVector]:
    return [v for v in vectors.values() if str(v.team_id) == str(team_id)]


def _reference_axes(vectors: dict[str, PlayerVector], team_id: str, role: str | None = None, *, exclude: set[str] | None = None) -> tuple[float, float, float, float] | None:
    exclude = exclude or set()
    pool = [v for v in _team_pool(vectors, team_id) if v.player_id not in exclude and (role is None or v.role == role)]
    if not pool and role is not None:
        pool = [v for v in _team_pool(vectors, team_id) if v.player_id not in exclude]
    if not pool:
        return None
    ax = [_axes(v) for v in pool]
    return _mean([x[0] for x in ax]), _mean([x[1] for x in ax]), _mean([x[2] for x in ax]), _mean([float(v.uncertainty) for v in pool])


@dataclass(frozen=True)
class SideDelta:
    delta_attack: float
    delta_defence: float
    delta_tempo: float
    uncertainty: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class ComponentEffect:
    component: str
    active: bool
    home: SideDelta
    away: SideDelta
    reason: str
    affected_player_ids: list[str]
    shrunk_player_n: int
    reference_n_home: int
    reference_n_away: int
    evidence_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def zero_side(uncertainty: float = 1.0) -> SideDelta:
    return SideDelta(0.0, 0.0, 0.0, float(uncertainty))


def zero_effect(component: str, reason: str, *, uncertainty: float = 1.0, reference_n_home: int = 0, reference_n_away: int = 0) -> ComponentEffect:
    return ComponentEffect(component, False, zero_side(uncertainty), zero_side(uncertainty), reason, [], 0, reference_n_home, reference_n_away, None)


def evidence_grade(packet: dict[str, Any], cutoff: str) -> str:
    """Highest usable evidence grade. Current historical collection may prove publication pre-cutoff without claiming T-15 observation."""
    if not packet.get("pit_legal"):
        return "NO_USABLE_ROSTER_EVIDENCE"
    src = packet.get("source") or {}
    available = src.get("available_at")
    if not available or _dt(str(available)) >= _dt(str(cutoff)):
        return "NO_USABLE_ROSTER_EVIDENCE"
    confirmed = packet.get("confirmed_lineups")
    bench = packet.get("bench")
    if isinstance(confirmed, dict) and isinstance(bench, dict):
        h = confirmed.get("home") or []
        a = confirmed.get("away") or []
        hb = bench.get("home") or []
        ab = bench.get("away") or []
        observed = src.get("source_observed_at")
        if observed and _dt(str(observed)) < _dt(str(cutoff)) and len(h) == len(a) == 11 and hb and ab and all(x.get("player_id") for x in h + a + hb + ab):
            return "CONFIRMED_LINEUP_PIT"
    possible = packet.get("predicted_lineups") or {}
    h = possible.get("home") or []
    a = possible.get("away") or []
    if len(h) == len(a) == 11 and all(x.get("player_id") for x in h + a):
        return "POSSIBLE_XI_PIT"
    if packet.get("status_records"):
        return "TEAM_NEWS_AVAILABILITY_PIT"
    return "NO_USABLE_ROSTER_EVIDENCE"


def _replacement_delta(v: PlayerVector, vectors: dict[str, PlayerVector]) -> tuple[float, float, float, float, bool] | None:
    same_role = _reference_axes(vectors, str(v.team_id), v.role, exclude={v.player_id})
    role_matched = same_role is not None
    ref = same_role or _reference_axes(vectors, str(v.team_id), None, exclude={v.player_id})
    if ref is None:
        return None
    va, vd, vt = _axes(v)
    ra, rd, rt, ru = ref
    return va - ra, vd - rd, vt - rt, max(float(v.uncertainty), ru), role_matched


def c1_availability_replacement(*, vectors: dict[str, PlayerVector], home_team_id: str, away_team_id: str, status_records: list[dict[str, Any]], evidence_uncertainty: float) -> ComponentEffect:
    """C1: explicit unavailability only. Frozen v1 packet safely supports SUSPENSION polarity; ambiguous injury text does not get a directional sign."""
    side = {str(home_team_id): [], str(away_team_id): []}
    affected: list[str] = []
    shrunk = 0
    usable = 0
    for rec in status_records or []:
        if rec.get("status_type") != "SUSPENSION":
            continue
        pid = str(rec.get("player_id") or "")
        if not pid or pid not in vectors:
            shrunk += 1
            continue
        v = vectors[pid]
        tid = str(v.team_id)
        if tid not in side:
            continue
        z = _replacement_delta(v, vectors)
        if z is None:
            shrunk += 1
            continue
        da, dd, dt, unc, role_matched = z
        side[tid].append((-0.020 * da, -0.020 * dd, -0.010 * dt, unc + (0.0 if role_matched else 0.20)))
        affected.append(pid)
        usable += 1
    if usable == 0:
        return zero_effect("C1", "NO_EXPLICIT_DIRECTIONAL_AVAILABILITY_WITH_PRE_CUTOFF_CAPABILITY", uncertainty=evidence_uncertainty + 0.25)
    def fold(tid: str) -> SideDelta:
        rows = side[tid]
        if not rows:
            return zero_side(evidence_uncertainty)
        return SideDelta(
            _clip(sum(x[0] for x in rows)),
            _clip(sum(x[1] for x in rows)),
            _clip(sum(x[2] for x in rows)),
            min(2.0, evidence_uncertainty + _mean([x[3] for x in rows]) + 0.10 * shrunk),
        )
    home = fold(str(home_team_id)); away = fold(str(away_team_id))
    ev = {"component": "C1", "home_team_id": str(home_team_id), "away_team_id": str(away_team_id), "affected": sorted(affected), "status_types": [x.get("status_type") for x in status_records or []]}
    return ComponentEffect("C1", True, home, away, "ACTIVE_EXPLICIT_SUSPENSION_REPLACEMENT_DIFFERENCE", sorted(affected), shrunk, 0, 0, _sha(ev))


def _lineup_axes_with_shrink(vectors: dict[str, PlayerVector], team_id: str, player_ids: list[str]) -> tuple[tuple[float, float, float], float, int] | None:
    if len(player_ids) != 11 or len(set(player_ids)) != 11:
        return None
    team_ref = _reference_axes(vectors, str(team_id))
    if team_ref is None:
        return None
    vals: list[tuple[float, float, float]] = []
    uncs: list[float] = []
    shrunk = 0
    for pid in player_ids:
        v = vectors.get(str(pid))
        if v is not None and str(v.team_id) == str(team_id):
            vals.append(_axes(v)); uncs.append(float(v.uncertainty))
        else:
            vals.append(team_ref[:3]); uncs.append(min(2.0, float(team_ref[3]) + 0.75)); shrunk += 1
    return (
        (_mean([x[0] for x in vals]), _mean([x[1] for x in vals]), _mean([x[2] for x in vals])),
        _mean(uncs),
        shrunk,
    )


def _lineup_residual_side(*, vectors: dict[str, PlayerVector], usage: dict[str, list[dict[str, Any]]], team_id: str, current: list[str], cutoff: str) -> tuple[SideDelta, int, int] | None:
    refs = rolling_reference_lineups(usage, str(team_id), cutoff=cutoff)
    if len(refs) < MIN_REFERENCE_LINEUPS:
        return None
    cur = _lineup_axes_with_shrink(vectors, str(team_id), current)
    if cur is None:
        return None
    ref_rows = [x for x in (_lineup_axes_with_shrink(vectors, str(team_id), r) for r in refs) if x is not None]
    if len(ref_rows) < MIN_REFERENCE_LINEUPS:
        return None
    ref_ax = tuple(_mean([x[0][i] for x in ref_rows]) for i in range(3))
    cur_ax, cur_unc, cur_shrunk = cur
    ref_unc = _mean([x[1] for x in ref_rows])
    ref_shrunk = sum(x[2] for x in ref_rows)
    delta = SideDelta(
        _clip(0.020 * (cur_ax[0] - ref_ax[0])),
        _clip(0.020 * (cur_ax[1] - ref_ax[1])),
        _clip(0.010 * (cur_ax[2] - ref_ax[2])),
        min(2.0, cur_unc + ref_unc + 0.05 * (cur_shrunk + ref_shrunk)),
    )
    return delta, len(refs), cur_shrunk + ref_shrunk


def lineup_residual_component(*, component: str, vectors: dict[str, PlayerVector], usage: dict[str, list[dict[str, Any]]], home_team_id: str, away_team_id: str, home_player_ids: list[str], away_player_ids: list[str], cutoff: str, evidence_uncertainty: float) -> ComponentEffect:
    hs = _lineup_residual_side(vectors=vectors, usage=usage, team_id=str(home_team_id), current=home_player_ids, cutoff=cutoff)
    aw = _lineup_residual_side(vectors=vectors, usage=usage, team_id=str(away_team_id), current=away_player_ids, cutoff=cutoff)
    if hs is None or aw is None:
        hr = len(rolling_reference_lineups(usage, str(home_team_id), cutoff=cutoff))
        ar = len(rolling_reference_lineups(usage, str(away_team_id), cutoff=cutoff))
        return zero_effect(component, "INSUFFICIENT_ROLLING_REFERENCE_OR_TEAM_CAPABILITY", uncertainty=evidence_uncertainty + 0.35, reference_n_home=hr, reference_n_away=ar)
    h, hn, hsh = hs; a, an, ash = aw
    h = SideDelta(h.delta_attack, h.delta_defence, h.delta_tempo, min(2.0, evidence_uncertainty + h.uncertainty))
    a = SideDelta(a.delta_attack, a.delta_defence, a.delta_tempo, min(2.0, evidence_uncertainty + a.uncertainty))
    ev = {"component": component, "cutoff": cutoff, "home_team_id": str(home_team_id), "away_team_id": str(away_team_id), "home": home_player_ids, "away": away_player_ids, "home_reference_n": hn, "away_reference_n": an}
    return ComponentEffect(component, True, h, a, "ACTIVE_ROLLING_REFERENCE_LINEUP_RESIDUAL", [], hsh + ash, hn, an, _sha(ev))


def c2_possible_xi(*, vectors: dict[str, PlayerVector], usage: dict[str, list[dict[str, Any]]], home_team_id: str, away_team_id: str, predicted_lineups: dict[str, list[dict[str, Any]]], cutoff: str) -> ComponentEffect:
    hp = predicted_lineups.get("home") or []; ap = predicted_lineups.get("away") or []
    if len(hp) != 11 or len(ap) != 11 or not all(x.get("player_id") for x in hp + ap):
        return zero_effect("C2", "POSSIBLE_XI_IDENTITY_NOT_COMPLETE", uncertainty=UNCERTAINTY_BY_GRADE["TEAM_NEWS_AVAILABILITY_PIT"])
    if any(x.get("starting_probability") is not None or x.get("expected_minutes") is not None for x in hp + ap):
        raise CandidateCContractError("Candidate C possible XI must not invent or silently consume unsupported per-player probability/minutes")
    return lineup_residual_component(component="C2", vectors=vectors, usage=usage, home_team_id=home_team_id, away_team_id=away_team_id, home_player_ids=[str(x["player_id"]) for x in hp], away_player_ids=[str(x["player_id"]) for x in ap], cutoff=cutoff, evidence_uncertainty=UNCERTAINTY_BY_GRADE["POSSIBLE_XI_PIT"])


def c3_confirmed_xi(*, vectors: dict[str, PlayerVector], usage: dict[str, list[dict[str, Any]]], home_team_id: str, away_team_id: str, confirmed_lineups: dict[str, list[dict[str, Any]]] | None, cutoff: str) -> ComponentEffect:
    if not isinstance(confirmed_lineups, dict):
        return zero_effect("C3", "NO_CONFIRMED_LINEUP_PIT", uncertainty=UNCERTAINTY_BY_GRADE["POSSIBLE_XI_PIT"])
    hp = confirmed_lineups.get("home") or []; ap = confirmed_lineups.get("away") or []
    if len(hp) != 11 or len(ap) != 11 or not all(x.get("player_id") for x in hp + ap):
        return zero_effect("C3", "CONFIRMED_XI_IDENTITY_NOT_COMPLETE", uncertainty=UNCERTAINTY_BY_GRADE["TEAM_NEWS_AVAILABILITY_PIT"])
    return lineup_residual_component(component="C3", vectors=vectors, usage=usage, home_team_id=home_team_id, away_team_id=away_team_id, home_player_ids=[str(x["player_id"]) for x in hp], away_player_ids=[str(x["player_id"]) for x in ap], cutoff=cutoff, evidence_uncertainty=UNCERTAINTY_BY_GRADE["CONFIRMED_LINEUP_PIT"])


def c4_bench(*, vectors: dict[str, PlayerVector], home_team_id: str, away_team_id: str, bench: dict[str, list[dict[str, Any]]] | None, evidence_uncertainty: float) -> ComponentEffect:
    if not isinstance(bench, dict):
        return zero_effect("C4", "NO_PREMATCH_BENCH_PIT", uncertainty=evidence_uncertainty)
    def side_delta(tid: str, rows: list[dict[str, Any]]) -> tuple[SideDelta, int] | None:
        ids = [str(x.get("player_id") or "") for x in rows]
        if not ids or any(not x for x in ids):
            return None
        ref = _reference_axes(vectors, tid)
        if ref is None:
            return None
        vals=[]; uncs=[]; shrunk=0
        for pid in ids:
            v=vectors.get(pid)
            if v is not None and str(v.team_id)==str(tid): vals.append(_axes(v));uncs.append(float(v.uncertainty))
            else: vals.append(ref[:3]);uncs.append(min(2.0,ref[3]+0.75));shrunk+=1
        return SideDelta(_clip(0.005*(_mean([x[0] for x in vals])-ref[0])),_clip(0.005*(_mean([x[1] for x in vals])-ref[1])),_clip(0.003*(_mean([x[2] for x in vals])-ref[2])),min(2.0,evidence_uncertainty+_mean(uncs)+0.05*shrunk)),shrunk
    h=side_delta(str(home_team_id),bench.get("home") or []);a=side_delta(str(away_team_id),bench.get("away") or [])
    if h is None or a is None:return zero_effect("C4","BENCH_IDENTITY_OR_CAPABILITY_INSUFFICIENT",uncertainty=evidence_uncertainty+0.25)
    ev={"component":"C4","home_team_id":str(home_team_id),"away_team_id":str(away_team_id),"home_bench_n":len(bench.get("home") or []),"away_bench_n":len(bench.get("away") or [])}
    return ComponentEffect("C4",True,h[0],a[0],"ACTIVE_PREMATCH_BENCH_DIFFERENCE",[],h[1]+a[1],0,0,_sha(ev))


def combine_effects(effects: list[ComponentEffect], *, grade: str) -> ComponentEffect:
    active=[e for e in effects if e.active]
    if not active:return zero_effect("FULL","NO_COMPONENT_ACTIVE",uncertainty=UNCERTAINTY_BY_GRADE[grade])
    def fold(which: str)->SideDelta:
        sides=[getattr(e,which) for e in active]
        return SideDelta(_clip(sum(x.delta_attack for x in sides),MAX_LOG_MU_RESIDUAL),_clip(sum(x.delta_defence for x in sides),MAX_LOG_MU_RESIDUAL),_clip(sum(x.delta_tempo for x in sides),MAX_LOG_MU_RESIDUAL),min(2.0,max(UNCERTAINTY_BY_GRADE[grade],_mean([x.uncertainty for x in sides]))))
    ev={"components":[e.component for e in active],"evidence":[e.evidence_sha256 for e in active]}
    return ComponentEffect("FULL",True,fold("home"),fold("away"),"ACTIVE_COMPONENT_UNION",sorted({p for e in active for p in e.affected_player_ids}),sum(e.shrunk_player_n for e in active),max((e.reference_n_home for e in active),default=0),max((e.reference_n_away for e in active),default=0),_sha(ev))


def deduplicated_c1_plus_lineup(c1: ComponentEffect, lineup: ComponentEffect, *, grade: str) -> tuple[ComponentEffect, dict[str, Any]]:
    """Avoid re-encoding the same replacement: a valid XI residual already contains the absence/replacement state."""
    if lineup.active:
        combined=combine_effects([lineup],grade=grade)
        return combined,{"c1_absorbed_by_lineup_residual":bool(c1.active),"reason":"LINEUP_RESIDUAL_ALREADY_ENCODES_CURRENT_XI_VS_REFERENCE"}
    return combine_effects([c1],grade=grade),{"c1_absorbed_by_lineup_residual":False,"reason":"NO_ACTIVE_LINEUP_RESIDUAL"}


def matchup_log_mu(effect: ComponentEffect) -> tuple[float, float]:
    """Single adapter into protected V2 score core; never touches 1X2 probabilities directly."""
    if not effect.active:return 0.0,0.0
    tempo=0.5*(effect.home.delta_tempo+effect.away.delta_tempo)
    raw_home=effect.home.delta_attack-effect.away.delta_defence+0.5*tempo
    raw_away=effect.away.delta_attack-effect.home.delta_defence+0.5*tempo
    unc=max(effect.home.uncertainty,effect.away.uncertainty)
    reliability=1.0/(1.0+max(0.0,unc))
    return max(-MAX_LOG_MU_RESIDUAL,min(MAX_LOG_MU_RESIDUAL,raw_home*reliability)),max(-MAX_LOG_MU_RESIDUAL,min(MAX_LOG_MU_RESIDUAL,raw_away*reliability))


def probability_mass_supported(packet: dict[str, Any]) -> bool:
    for area in (packet.get("predicted_lineups"),packet.get("confirmed_lineups"),packet.get("bench")):
        if not isinstance(area,dict):continue
        for side in ("home","away"):
            for row in area.get(side) or []:
                if row.get("starting_probability") is not None or row.get("availability_probability") is not None or row.get("entry_probability") is not None:
                    return True
    return False


def candidate_contract() -> dict[str, Any]:
    obj={
        "schema_version":"football3-context-translator-candidate-c-v1",
        "status":"RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC",
        "evidence_grades":["CONFIRMED_LINEUP_PIT","POSSIBLE_XI_PIT","TEAM_NEWS_AVAILABILITY_PIT","NO_USABLE_ROSTER_EVIDENCE"],
        "components":{
            "C1":"explicit injury/suspension availability replacement difference; frozen v1 inventory only has unambiguous suspension polarity",
            "C2":"source-listed possible XI residual versus rolling pre-cutoff reference XI; no per-player probability invented",
            "C3":"confirmed XI residual versus rolling pre-cutoff reference XI",
            "C4":"prematch bench-only difference, never starter-strength replay",
            "probability_mass":"active only when source supplies real probability input; otherwise exactly zero",
        },
        "independent_activation":True,
        "possible_and_confirmed_mutually_exclusive":True,
        "double_encoding_guard":"C1 is measured independently but absorbed when active C2/C3 already encodes the current XI replacement state",
        "missing_capability":"shrink to empirical pre-cutoff team/role reference and increase uncertainty; never synthesize a default player strength",
        "component_output_only":["delta_attack","delta_defence","delta_tempo","uncertainty"],
        "score_core_adapter":"protected V2 score-matrix core only; no direct 1X2 probability mutation",
        "minimum_rolling_reference_lineups":MIN_REFERENCE_LINEUPS,
        "max_log_mu_residual_safety_bound":MAX_LOG_MU_RESIDUAL,
        "forbidden":["fixed_outcome_multiplier","result_patch","direct_1x2_probability_patch","label_conditioned_adjustment","invented_start_probability","invented_expected_minutes","invented_bench_probability"],
    }
    obj["contract_sha256"]=_sha(obj);return obj
