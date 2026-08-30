from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

EPS = 1e-12
MAX_GOALS = 14


class EngineError(RuntimeError):
    pass


def _finite(x: float, name: str) -> float:
    x = float(x)
    if not math.isfinite(x):
        raise EngineError(f"{name} must be finite")
    return x


def _clip(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def _iso_utc(value: datetime, name: str = "kickoff") -> datetime:
    if not isinstance(value, datetime):
        raise EngineError(f"{name} must be datetime")
    if value.tzinfo is None:
        raise EngineError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def canonical_json_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Parameters:
    half_life_days: float = 210.0
    competition_half_life_days: float = 720.0
    prior_matches: float = 8.0
    competition_prior_matches: float = 24.0
    global_team_prior_matches: float = 12.0
    cross_season_shrink: float = 0.58
    global_team_weight: float = 0.35
    strength_exponent: float = 0.82
    min_rate: float = 0.12
    max_rate: float = 5.5

    def __post_init__(self) -> None:
        for name in (
            "half_life_days", "competition_half_life_days", "prior_matches",
            "competition_prior_matches", "global_team_prior_matches",
        ):
            if _finite(getattr(self, name), name) <= 0:
                raise EngineError(f"{name} must be positive")
        if not 0 < self.cross_season_shrink <= 1:
            raise EngineError("cross_season_shrink must be in (0,1]")
        if not 0 <= self.global_team_weight <= 1:
            raise EngineError("global_team_weight must be in [0,1]")
        if not 0 < self.strength_exponent <= 1.5:
            raise EngineError("strength_exponent out of range")


@dataclass
class DecayedTeam:
    goals_for: float = 0.0
    goals_against: float = 0.0
    weight: float = 0.0
    last_time: datetime | None = None
    last_season: str | None = None

    def snapshot(self, now: datetime, season: str, half_life_days: float, season_shrink: float) -> tuple[float, float, float]:
        now = _iso_utc(now)
        if self.last_time is None:
            return 0.0, 0.0, 0.0
        if now < self.last_time:
            raise EngineError("team state time reversal")
        days = (now - self.last_time).total_seconds() / 86400.0
        decay = math.exp(-math.log(2.0) * days / half_life_days)
        if self.last_season is not None and season != self.last_season:
            decay *= season_shrink
        return self.goals_for * decay, self.goals_against * decay, self.weight * decay

    def advance_and_add(self, now: datetime, season: str, gf: int, ga: int, half_life_days: float, season_shrink: float) -> None:
        a, d, w = self.snapshot(now, season, half_life_days, season_shrink)
        self.goals_for = a + gf
        self.goals_against = d + ga
        self.weight = w + 1.0
        self.last_time = _iso_utc(now)
        self.last_season = season


@dataclass
class DecayedCompetition:
    home_goals: float = 0.0
    away_goals: float = 0.0
    matches: float = 0.0
    last_time: datetime | None = None

    def snapshot(self, now: datetime, half_life_days: float) -> tuple[float, float, float]:
        now = _iso_utc(now)
        if self.last_time is None:
            return 0.0, 0.0, 0.0
        if now < self.last_time:
            raise EngineError("competition state time reversal")
        days = (now - self.last_time).total_seconds() / 86400.0
        decay = math.exp(-math.log(2.0) * days / half_life_days)
        return self.home_goals * decay, self.away_goals * decay, self.matches * decay

    def advance_and_add(self, now: datetime, hg: int, ag: int, half_life_days: float) -> None:
        h, a, n = self.snapshot(now, half_life_days)
        self.home_goals = h + hg
        self.away_goals = a + ag
        self.matches = n + 1.0
        self.last_time = _iso_utc(now)


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    competition_id: str
    season: str
    kickoff: datetime
    home_team_id: str
    away_team_id: str

    def validate(self) -> None:
        for name in ("fixture_id", "competition_id", "season", "home_team_id", "away_team_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise EngineError(f"{name} is empty")
        if self.home_team_id == self.away_team_id:
            raise EngineError("home and away identity collide")
        _iso_utc(self.kickoff)


@dataclass
class EngineState:
    params: Parameters = field(default_factory=Parameters)
    global_comp: DecayedCompetition = field(default_factory=DecayedCompetition)
    competitions: dict[str, DecayedCompetition] = field(default_factory=dict)
    teams_local: dict[tuple[str, str], DecayedTeam] = field(default_factory=dict)
    teams_global: dict[str, DecayedTeam] = field(default_factory=dict)
    seen_fixtures: set[str] = field(default_factory=set)
    last_update_time: datetime | None = None

    def _global_rates(self, now: datetime) -> tuple[float, float, float]:
        h, a, n = self.global_comp.snapshot(now, self.params.competition_half_life_days)
        # Stable football prior for a completely empty system.
        base_h, base_a, base_n = 1.38, 1.12, 20.0
        return (h + base_h * base_n) / (n + base_n), (a + base_a * base_n) / (n + base_n), n

    def _competition_rates(self, competition_id: str, now: datetime) -> tuple[float, float, float]:
        gh, ga, _ = self._global_rates(now)
        state = self.competitions.get(competition_id)
        if state is None:
            return gh, ga, 0.0
        h, a, n = state.snapshot(now, self.params.competition_half_life_days)
        p = self.params.competition_prior_matches
        return (h + gh * p) / (n + p), (a + ga * p) / (n + p), n

    def _team_view(self, competition_id: str, team_id: str, season: str, now: datetime, comp_average: float) -> dict[str, float | str]:
        local = self.teams_local.get((competition_id, team_id))
        if local is None:
            lgf = lga = ln = 0.0
        else:
            lgf, lga, ln = local.snapshot(now, season, self.params.half_life_days, self.params.cross_season_shrink)
        glob = self.teams_global.get(team_id)
        if glob is None:
            ggf = gga = gn = 0.0
        else:
            ggf, gga, gn = glob.snapshot(now, season, self.params.half_life_days * 1.4, self.params.cross_season_shrink)

        gp = self.params.global_team_prior_matches
        global_attack = (ggf + comp_average * gp) / (gn + gp) / max(EPS, comp_average)
        global_defence = (gga + comp_average * gp) / (gn + gp) / max(EPS, comp_average)
        gw = self.params.global_team_weight * min(1.0, gn / max(1.0, gp))
        prior_attack = (1.0 - gw) + gw * global_attack
        prior_defence = (1.0 - gw) + gw * global_defence

        lp = self.params.prior_matches
        attack_rate = (lgf + lp * comp_average * prior_attack) / (ln + lp)
        defence_rate = (lga + lp * comp_average * prior_defence) / (ln + lp)
        attack = _clip(attack_rate / max(EPS, comp_average), 0.40, 2.50)
        defence = _clip(defence_rate / max(EPS, comp_average), 0.40, 2.50)
        if ln >= 5.0:
            source = "team_competition"
        elif ln > 0.0:
            source = "sparse_team_competition"
        elif gn > 0.0:
            source = "cross_competition_team"
        else:
            source = "competition_or_global"
        return {
            "attack": attack,
            "defence": defence,
            "local_weight": ln,
            "global_weight": gn,
            "source": source,
        }

    def predict(self, fixture: Fixture) -> dict:
        fixture.validate()
        now = _iso_utc(fixture.kickoff)
        if fixture.fixture_id in self.seen_fixtures:
            raise EngineError(f"duplicate fixture id: {fixture.fixture_id}")
        if self.last_update_time is not None and now < self.last_update_time:
            raise EngineError("prediction time precedes already-applied state")

        ch, ca, cn = self._competition_rates(fixture.competition_id, now)
        avg = max(0.20, 0.5 * (ch + ca))
        home = self._team_view(fixture.competition_id, fixture.home_team_id, fixture.season, now, avg)
        away = self._team_view(fixture.competition_id, fixture.away_team_id, fixture.season, now, avg)
        e = self.params.strength_exponent
        mu_h = ch * (float(home["attack"]) ** e) * (float(away["defence"]) ** e)
        mu_a = ca * (float(away["attack"]) ** e) * (float(home["defence"]) ** e)
        mu_h = _clip(mu_h, self.params.min_rate, self.params.max_rate)
        mu_a = _clip(mu_a, self.params.min_rate, self.params.max_rate)

        matrix = score_matrix(mu_h, mu_a)
        probs = one_x_two(matrix)
        nh = float(home["local_weight"])
        na = float(away["local_weight"])
        info = self.params.prior_matches + nh + na + 0.25 * cn
        sigma = math.sqrt(2.0 / max(2.0, info))
        z = 1.6448536269514722
        ci_h = [_clip(mu_h * math.exp(-z * sigma), self.params.min_rate, self.params.max_rate), _clip(mu_h * math.exp(z * sigma), self.params.min_rate, self.params.max_rate)]
        ci_a = [_clip(mu_a * math.exp(-z * sigma), self.params.min_rate, self.params.max_rate), _clip(mu_a * math.exp(z * sigma), self.params.min_rate, self.params.max_rate)]
        uncertainty = _clip(sigma / 0.75, 0.0, 1.0)
        cold = min(nh, na)
        cold_bucket = "zero" if cold < 0.25 else "sparse" if cold < 5.0 else "established"
        prior_source = f"{home['source']}|{away['source']}"
        payload = {
            "engine": "Football3-New-Engine-V1-pure",
            "fixture_id": fixture.fixture_id,
            "competition_id": fixture.competition_id,
            "season": fixture.season,
            "kickoff": now.isoformat(),
            "home_team_id": fixture.home_team_id,
            "away_team_id": fixture.away_team_id,
            "mu_home": mu_h,
            "mu_away": mu_a,
            "mu_home_ci90": ci_h,
            "mu_away_ci90": ci_a,
            "uncertainty": uncertainty,
            "cold_start_bucket": cold_bucket,
            "prior_source": prior_source,
            "effective_home_history": nh,
            "effective_away_history": na,
            "effective_competition_history": cn,
            "score_matrix": matrix,
            "p_home": probs[0],
            "p_draw": probs[1],
            "p_away": probs[2],
        }
        payload["prediction_hash"] = canonical_json_hash(payload)
        return payload

    def apply_batch(self, fixtures: Iterable[Fixture], labels: dict[str, tuple[int, int]]) -> None:
        batch = list(fixtures)
        if not batch:
            return
        for f in batch:
            f.validate()
        times = {_iso_utc(f.kickoff) for f in batch}
        if len(times) != 1:
            raise EngineError("apply_batch requires one atomic kickoff time")
        now = next(iter(times))
        if self.last_update_time is not None and now < self.last_update_time:
            raise EngineError("batch update time reversal")
        ids = [f.fixture_id for f in batch]
        if len(ids) != len(set(ids)):
            raise EngineError("duplicate fixture within batch")
        if any(fid in self.seen_fixtures for fid in ids):
            raise EngineError("fixture already applied")
        if set(labels) != set(ids):
            raise EngineError("batch labels do not exactly match fixtures")

        prepared: list[tuple[Fixture, int, int]] = []
        for f in batch:
            hg, ag = labels[f.fixture_id]
            if isinstance(hg, bool) or isinstance(ag, bool):
                raise EngineError("boolean goals invalid")
            try:
                hg, ag = int(hg), int(ag)
            except Exception as exc:
                raise EngineError("goals must be integers") from exc
            if hg < 0 or ag < 0 or hg > 30 or ag > 30:
                raise EngineError("goals out of valid range")
            prepared.append((f, hg, ag))

        for f, hg, ag in prepared:
            comp = self.competitions.setdefault(f.competition_id, DecayedCompetition())
            comp.advance_and_add(now, hg, ag, self.params.competition_half_life_days)
            self.global_comp.advance_and_add(now, hg, ag, self.params.competition_half_life_days)
            self.teams_local.setdefault((f.competition_id, f.home_team_id), DecayedTeam()).advance_and_add(now, f.season, hg, ag, self.params.half_life_days, self.params.cross_season_shrink)
            self.teams_local.setdefault((f.competition_id, f.away_team_id), DecayedTeam()).advance_and_add(now, f.season, ag, hg, self.params.half_life_days, self.params.cross_season_shrink)
            self.teams_global.setdefault(f.home_team_id, DecayedTeam()).advance_and_add(now, f.season, hg, ag, self.params.half_life_days * 1.4, self.params.cross_season_shrink)
            self.teams_global.setdefault(f.away_team_id, DecayedTeam()).advance_and_add(now, f.season, ag, hg, self.params.half_life_days * 1.4, self.params.cross_season_shrink)
            self.seen_fixtures.add(f.fixture_id)
        self.last_update_time = now


def _poisson_probs(mu: float, max_goals: int = MAX_GOALS) -> list[float]:
    mu = _finite(mu, "mu")
    if mu <= 0:
        raise EngineError("mu must be positive")
    probs = [math.exp(-mu)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * mu / k)
    return probs


def score_matrix(mu_home: float, mu_away: float, max_goals: int = MAX_GOALS) -> list[dict]:
    hp, ap = _poisson_probs(mu_home, max_goals), _poisson_probs(mu_away, max_goals)
    cells = []
    total = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            p = ph * pa
            total += p
            cells.append({"home_goals": h, "away_goals": a, "probability": p})
    if not math.isfinite(total) or total <= 0:
        raise EngineError("invalid score mass")
    for c in cells:
        c["probability"] /= total
    residual = abs(sum(c["probability"] for c in cells) - 1.0)
    if residual > 1e-10:
        raise EngineError("score matrix normalization failed")
    return cells


def one_x_two(matrix: list[dict]) -> tuple[float, float, float]:
    h = d = a = 0.0
    seen: set[tuple[int, int]] = set()
    total = 0.0
    for cell in matrix:
        try:
            hg, ag, p = int(cell["home_goals"]), int(cell["away_goals"]), float(cell["probability"])
        except Exception as exc:
            raise EngineError("corrupt score matrix") from exc
        if hg < 0 or ag < 0 or not math.isfinite(p) or p < 0:
            raise EngineError("corrupt score matrix cell")
        if (hg, ag) in seen:
            raise EngineError("duplicate score matrix cell")
        seen.add((hg, ag))
        total += p
        if hg > ag:
            h += p
        elif hg == ag:
            d += p
        else:
            a += p
    if abs(total - 1.0) > 1e-8:
        raise EngineError("score matrix mass != 1")
    return h, d, a
