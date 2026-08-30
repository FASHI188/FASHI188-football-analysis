from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from strict import GovernanceError, finite_number, parse_utc


class LineupState(str, Enum):
    EXPECTED_LINEUP = "EXPECTED_LINEUP"
    CONFIRMED_LINEUP = "CONFIRMED_LINEUP"
    LINEUP_UNKNOWN = "LINEUP_UNKNOWN"


@dataclass(frozen=True)
class PlayerSnapshot:
    player_id: str
    position: str
    attack: float
    defence: float
    goalkeeper: float
    start_probability: float
    expected_minutes: float
    available: bool
    known_at: str

    def validate(self, cutoff: datetime) -> None:
        if not self.player_id or not self.position:
            raise GovernanceError("player identity/position required")
        finite_number(self.attack, "player.attack", lo=-3.0, hi=3.0)
        finite_number(self.defence, "player.defence", lo=-3.0, hi=3.0)
        finite_number(self.goalkeeper, "player.goalkeeper", lo=-3.0, hi=3.0)
        finite_number(self.start_probability, "player.start_probability", lo=0.0, hi=1.0)
        finite_number(self.expected_minutes, "player.expected_minutes", lo=0.0, hi=130.0)
        if type(self.available) is not bool:
            raise GovernanceError("player.available must be bool")
        if parse_utc(self.known_at, "player.known_at") >= cutoff:
            raise GovernanceError("player snapshot is not known strictly before cutoff")


@dataclass(frozen=True)
class LineupScenario:
    probability: float
    players: tuple[PlayerSnapshot, ...]
    bench_attack: float
    bench_defence: float
    replacement_coverage: float

    def validate(self, cutoff: datetime) -> None:
        finite_number(self.probability, "scenario.probability", lo=0.0, hi=1.0)
        finite_number(self.bench_attack, "scenario.bench_attack", lo=-3.0, hi=3.0)
        finite_number(self.bench_defence, "scenario.bench_defence", lo=-3.0, hi=3.0)
        finite_number(self.replacement_coverage, "scenario.replacement_coverage", lo=0.0, hi=1.0)
        ids = set()
        for p in self.players:
            p.validate(cutoff)
            if p.player_id in ids:
                raise GovernanceError("duplicate player in lineup scenario")
            ids.add(p.player_id)


def expected_lineup_mixture(scenarios: list[LineupScenario], cutoff: datetime) -> dict[str, float]:
    if not scenarios:
        raise GovernanceError("EXPECTED_LINEUP requires at least one scenario")
    for s in scenarios:
        s.validate(cutoff)
    total = sum(s.probability for s in scenarios)
    if abs(total - 1.0) > 1e-9:
        raise GovernanceError("lineup scenario probabilities must sum to 1")
    attack = defence = goalkeeper = bench_a = bench_d = coverage = 0.0
    scenario_attacks = []
    for s in scenarios:
        pa = sum(p.attack * p.expected_minutes / 90.0 for p in s.players if p.available)
        pd = sum(p.defence * p.expected_minutes / 90.0 for p in s.players if p.available)
        pg = sum(p.goalkeeper * p.expected_minutes / 90.0 for p in s.players if p.available and p.position.casefold() in {"gk", "goalkeeper"})
        attack += s.probability * pa
        defence += s.probability * pd
        goalkeeper += s.probability * pg
        bench_a += s.probability * s.bench_attack
        bench_d += s.probability * s.bench_defence
        coverage += s.probability * s.replacement_coverage
        scenario_attacks.append((s.probability, pa))
    mean = attack
    scenario_uncertainty = sum(w * (x - mean) ** 2 for w, x in scenario_attacks) ** 0.5
    return {
        "attack_adjustment": attack,
        "defence_adjustment": defence,
        "goalkeeper_adjustment": goalkeeper,
        "bench_attack": bench_a,
        "bench_defence": bench_d,
        "replacement_coverage": coverage,
        "lineup_uncertainty": scenario_uncertainty,
    }


@dataclass
class CoachRegimeState:
    coach_id: str
    regime_started_at: str
    evidence_matches: int = 0
    tempo: float = 0.0
    pressing: float = 0.0
    lead_contraction: float = 0.0
    trail_risk: float = 0.0

    def validate_for_cutoff(self, cutoff: datetime) -> None:
        if not self.coach_id:
            raise GovernanceError("coach identity required")
        if parse_utc(self.regime_started_at, "regime_started_at") >= cutoff:
            raise GovernanceError("coach regime not known before cutoff")
        if type(self.evidence_matches) is not int or self.evidence_matches < 0:
            raise GovernanceError("coach evidence must be nonnegative strict int")
        for name in ("tempo", "pressing", "lead_contraction", "trail_risk"):
            finite_number(getattr(self, name), f"coach.{name}", lo=-2.0, hi=2.0)

    @property
    def shrinkage_weight(self) -> float:
        return self.evidence_matches / (self.evidence_matches + 12.0)


def match_process_status(minute_source_manifest: dict[str, Any] | None) -> str:
    if not minute_source_manifest:
        return "BLOCKED_DATA"
    required = {"source_url", "retrieved_at_utc", "raw_sha256", "schema_version", "known_at_policy", "event_time_field"}
    if set(minute_source_manifest) != required:
        raise GovernanceError("minute source manifest must match explicit allowlist")
    parse_utc(minute_source_manifest["retrieved_at_utc"], "retrieved_at_utc")
    return "AVAILABLE_UNVALIDATED"
