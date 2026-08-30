from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from strict import GovernanceError, canonical_json_bytes, finite_number, strict_nonnegative_int, validate_probability_vector

CLASSES = ("home", "draw", "away")
FAMILIES = (
    "INDEPENDENT_POISSON_FROZEN",
    "DIXON_COLES_LOW_SCORE",
    "DIAGONAL_INFLATION_BIVARIATE",
    "DYNAMIC_NB_DIAGONAL",
    "DYNAMIC_NB_MARCO",
    "DYNAMIC_NB_SARMANOV",
)


@dataclass(frozen=True)
class Parameters:
    half_life_days: float = 240.0
    competition_half_life_days: float = 720.0
    team_prior_matches: float = 8.0
    competition_prior_matches: float = 30.0
    cross_season_shrink: float = 0.60
    strength_exponent: float = 0.85
    min_rate: float = 0.08
    max_rate: float = 6.0
    global_home_rate: float = 1.38
    global_away_rate: float = 1.12
    max_goals: int = 14

    def __post_init__(self) -> None:
        finite_number(self.half_life_days, "half_life_days", lo=30.0, hi=2500.0)
        finite_number(self.competition_half_life_days, "competition_half_life_days", lo=60.0, hi=5000.0)
        finite_number(self.team_prior_matches, "team_prior_matches", lo=1.0, hi=200.0)
        finite_number(self.competition_prior_matches, "competition_prior_matches", lo=2.0, hi=1000.0)
        finite_number(self.cross_season_shrink, "cross_season_shrink", lo=0.0, hi=1.0)
        finite_number(self.strength_exponent, "strength_exponent", lo=0.1, hi=2.0)
        finite_number(self.min_rate, "min_rate", lo=0.001, hi=5.0)
        finite_number(self.max_rate, "max_rate", lo=0.1, hi=20.0)
        finite_number(self.global_home_rate, "global_home_rate", lo=0.1, hi=5.0)
        finite_number(self.global_away_rate, "global_away_rate", lo=0.1, hi=5.0)
        if not self.min_rate < self.max_rate:
            raise GovernanceError("min_rate must be strictly less than max_rate")
        if type(self.max_goals) is not int or not (8 <= self.max_goals <= 20):
            raise GovernanceError("max_goals must be strict int in [8,20]")


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    competition_id: str
    season: str
    kickoff: datetime
    home_team_id: str
    away_team_id: str
    round_index: int | None = None

    def validate(self) -> None:
        for field, value in (
            ("fixture_id", self.fixture_id),
            ("competition_id", self.competition_id),
            ("season", self.season),
            ("home_team_id", self.home_team_id),
            ("away_team_id", self.away_team_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise GovernanceError(f"{field} must be non-empty string")
        if self.home_team_id == self.away_team_id:
            raise GovernanceError("home and away identities must differ")
        if not isinstance(self.kickoff, datetime) or self.kickoff.tzinfo is None or self.kickoff.utcoffset() is None:
            raise GovernanceError("kickoff must be timezone-aware")
        if self.round_index is not None and (type(self.round_index) is not int or self.round_index <= 0):
            raise GovernanceError("round_index must be positive strict int or None")


@dataclass
class TeamState:
    gf: float = 0.0
    ga: float = 0.0
    evidence: float = 0.0
    last_cutoff: str | None = None
    last_season: str | None = None
    recent_dates: list[str] | None = None

    def ensure(self) -> None:
        if self.recent_dates is None:
            self.recent_dates = []


@dataclass
class CompetitionState:
    home_goals: float = 0.0
    away_goals: float = 0.0
    evidence: float = 0.0
    last_cutoff: str | None = None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dt(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise GovernanceError("state datetime missing timezone")
    return parsed.astimezone(timezone.utc)


def _decay(days: float, half_life: float) -> float:
    if days < -1e-9:
        raise GovernanceError("future state timestamp relative to fixture")
    return math.exp(-math.log(2.0) * max(0.0, days) / half_life)


def _clip(value: float, lo: float, hi: float) -> float:
    if not math.isfinite(value):
        raise GovernanceError("nonfinite model quantity")
    return min(hi, max(lo, value))


class EngineState:
    def __init__(self, params: Parameters):
        self.params = params
        self.teams: dict[str, TeamState] = {}
        self.competitions: dict[str, CompetitionState] = {}

    def _team(self, competition_id: str, team_id: str) -> TeamState:
        key = f"{competition_id}|{team_id}"
        st = self.teams.setdefault(key, TeamState())
        st.ensure()
        return st

    def _competition(self, competition_id: str) -> CompetitionState:
        return self.competitions.setdefault(competition_id, CompetitionState())

    def _view_team(self, competition_id: str, team_id: str, season: str, cutoff: datetime,
                   comp_for_rate: float, comp_against_rate: float) -> dict[str, float | str]:
        st = self._team(competition_id, team_id)
        gf, ga, ev = st.gf, st.ga, st.evidence
        if st.last_cutoff:
            days = (cutoff - _dt(st.last_cutoff)).total_seconds() / 86400.0
            d = _decay(days, self.params.half_life_days)
            gf *= d
            ga *= d
            ev *= d
        if st.last_season and st.last_season != season:
            gf *= self.params.cross_season_shrink
            ga *= self.params.cross_season_shrink
            ev *= self.params.cross_season_shrink
        prior = self.params.team_prior_matches
        for_rate = (gf + prior * comp_for_rate) / (ev + prior)
        against_rate = (ga + prior * comp_against_rate) / (ev + prior)
        return {
            "for_rate": _clip(for_rate, self.params.min_rate, self.params.max_rate),
            "against_rate": _clip(against_rate, self.params.min_rate, self.params.max_rate),
            "evidence": max(0.0, ev),
        }

    def _view_comp(self, competition_id: str, cutoff: datetime) -> tuple[float, float, float]:
        st = self._competition(competition_id)
        hg, ag, ev = st.home_goals, st.away_goals, st.evidence
        if st.last_cutoff:
            days = (cutoff - _dt(st.last_cutoff)).total_seconds() / 86400.0
            d = _decay(days, self.params.competition_half_life_days)
            hg *= d
            ag *= d
            ev *= d
        p = self.params.competition_prior_matches
        home = (hg + p * self.params.global_home_rate) / (ev + p)
        away = (ag + p * self.params.global_away_rate) / (ev + p)
        return (
            _clip(home, self.params.min_rate, self.params.max_rate),
            _clip(away, self.params.min_rate, self.params.max_rate),
            max(0.0, ev),
        )

    def _rest_and_density(self, competition_id: str, team_id: str, cutoff: datetime) -> tuple[float | None, int]:
        st = self._team(competition_id, team_id)
        st.ensure()
        dates = [_dt(x) for x in st.recent_dates or []]
        prior = [d for d in dates if d < cutoff]
        rest = None if not prior else (cutoff - max(prior)).total_seconds() / 86400.0
        density = sum(1 for d in prior if 0 < (cutoff - d).total_seconds() <= 14 * 86400)
        return rest, density

    def predict_features(self, fixture: Fixture) -> dict[str, Any]:
        fixture.validate()
        kickoff = fixture.kickoff.astimezone(timezone.utc)
        comp_h, comp_a, comp_ev = self._view_comp(fixture.competition_id, kickoff)
        home = self._view_team(fixture.competition_id, fixture.home_team_id, fixture.season, kickoff, comp_h, comp_a)
        away = self._view_team(fixture.competition_id, fixture.away_team_id, fixture.season, kickoff, comp_a, comp_h)
        exponent = self.params.strength_exponent
        home_attack = (float(home["for_rate"]) / max(comp_h, 1e-9)) ** exponent
        away_def = (float(away["against_rate"]) / max(comp_h, 1e-9)) ** exponent
        away_attack = (float(away["for_rate"]) / max(comp_a, 1e-9)) ** exponent
        home_def = (float(home["against_rate"]) / max(comp_a, 1e-9)) ** exponent
        mu_h = _clip(comp_h * home_attack * away_def, self.params.min_rate, self.params.max_rate)
        mu_a = _clip(comp_a * away_attack * home_def, self.params.min_rate, self.params.max_rate)
        hrest, hdens = self._rest_and_density(fixture.competition_id, fixture.home_team_id, kickoff)
        arest, adens = self._rest_and_density(fixture.competition_id, fixture.away_team_id, kickoff)
        hev = float(home["evidence"])
        aev = float(away["evidence"])
        evidence_min = min(hev, aev)
        if evidence_min < 0.5:
            cold = "zero"
        elif evidence_min < 6.0:
            cold = "sparse"
        else:
            cold = "established"
        uncertainty = min(
            1.0,
            0.15
            + 1.0 / math.sqrt(1.0 + hev)
            + 1.0 / math.sqrt(1.0 + aev)
            + 0.5 / math.sqrt(1.0 + comp_ev),
        ) / 2.5
        return {
            "fixture_id": fixture.fixture_id,
            "competition_id": fixture.competition_id,
            "season": fixture.season,
            "kickoff": _iso(kickoff),
            "home_team_id": fixture.home_team_id,
            "away_team_id": fixture.away_team_id,
            "round_index": fixture.round_index,
            "mu_home": mu_h,
            "mu_away": mu_a,
            "home_evidence": hev,
            "away_evidence": aev,
            "competition_evidence": comp_ev,
            "home_rest_days": hrest,
            "away_rest_days": arest,
            "home_density14": hdens,
            "away_density14": adens,
            "cold_start_bucket": cold,
            "uncertainty": uncertainty,
        }

    def apply_batch(self, fixtures: list[Fixture], labels: dict[str, tuple[int, int]]) -> None:
        if not fixtures:
            return
        cutoff = fixtures[0].kickoff.astimezone(timezone.utc)
        if any(f.kickoff.astimezone(timezone.utc) != cutoff for f in fixtures):
            raise GovernanceError("apply_batch requires exact same-cutoff batch")
        ids = [f.fixture_id for f in fixtures]
        if len(ids) != len(set(ids)) or set(ids) != set(labels):
            raise GovernanceError("batch fixture/label set mismatch")
        for f in fixtures:
            f.validate()
            hg, ag = labels[f.fixture_id]
            strict_nonnegative_int(hg, f"{f.fixture_id}.home_goals")
            strict_nonnegative_int(ag, f"{f.fixture_id}.away_goals")
        for f in fixtures:
            hg, ag = labels[f.fixture_id]
            comp = self._competition(f.competition_id)
            if comp.last_cutoff:
                days = (cutoff - _dt(comp.last_cutoff)).total_seconds() / 86400.0
                d = _decay(days, self.params.competition_half_life_days)
                comp.home_goals *= d
                comp.away_goals *= d
                comp.evidence *= d
            comp.home_goals += hg
            comp.away_goals += ag
            comp.evidence += 1.0
            comp.last_cutoff = _iso(cutoff)
            for team_id, gf, ga in ((f.home_team_id, hg, ag), (f.away_team_id, ag, hg)):
                st = self._team(f.competition_id, team_id)
                if st.last_cutoff:
                    days = (cutoff - _dt(st.last_cutoff)).total_seconds() / 86400.0
                    d = _decay(days, self.params.half_life_days)
                    st.gf *= d
                    st.ga *= d
                    st.evidence *= d
                if st.last_season and st.last_season != f.season:
                    st.gf *= self.params.cross_season_shrink
                    st.ga *= self.params.cross_season_shrink
                    st.evidence *= self.params.cross_season_shrink
                st.gf += gf
                st.ga += ga
                st.evidence += 1.0
                st.last_cutoff = _iso(cutoff)
                st.last_season = f.season
                st.ensure()
                st.recent_dates = [x for x in (st.recent_dates or []) if (_dt(x) <= cutoff and (cutoff - _dt(x)).days <= 60)]
                st.recent_dates.append(_iso(cutoff))

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": asdict(self.params),
            "teams": {k: asdict(v) for k, v in sorted(self.teams.items())},
            "competitions": {k: asdict(v) for k, v in sorted(self.competitions.items())},
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "EngineState":
        params = Parameters(**obj["params"])
        out = cls(params)
        out.teams = {k: TeamState(**v) for k, v in obj.get("teams", {}).items()}
        out.competitions = {k: CompetitionState(**v) for k, v in obj.get("competitions", {}).items()}
        for st in out.teams.values():
            st.ensure()
        return out


def poisson_pmf(mean: float, max_goals: int) -> list[float]:
    mean = finite_number(mean, "poisson.mean", lo=0.0, hi=20.0, lo_open=True)
    if type(max_goals) is not int or max_goals < 1:
        raise GovernanceError("max_goals invalid")
    p0 = math.exp(-mean)
    out = [p0]
    for k in range(1, max_goals + 1):
        out.append(out[-1] * mean / k)
    return out


def nb_pmf(mean: float, dispersion: float, max_goals: int) -> list[float]:
    mean = finite_number(mean, "nb.mean", lo=0.0, hi=20.0, lo_open=True)
    k = finite_number(dispersion, "nb.dispersion", lo=0.3, hi=100.0)
    p = k / (k + mean)
    q = mean / (k + mean)
    out = []
    for x in range(max_goals + 1):
        logp = (
            math.lgamma(x + k) - math.lgamma(k) - math.lgamma(x + 1)
            + k * math.log(p) + x * math.log(q)
        )
        out.append(math.exp(logp))
    return out


def _normalize(matrix: list[list[float]]) -> list[list[float]]:
    total = 0.0
    for row in matrix:
        for value in row:
            if not math.isfinite(value) or value < 0.0:
                raise GovernanceError("joint matrix has invalid cell")
            total += value
    if not math.isfinite(total) or total <= 0.0:
        raise GovernanceError("joint matrix total invalid")
    return [[v / total for v in row] for row in matrix]


def _outer(a: list[float], b: list[float]) -> list[list[float]]:
    return [[x * y for y in b] for x in a]


def dependence_context(features: dict[str, Any]) -> float:
    mh = finite_number(features["mu_home"], "mu_home", lo=0.0, hi=20.0, lo_open=True)
    ma = finite_number(features["mu_away"], "mu_away", lo=0.0, hi=20.0, lo_open=True)
    gap = abs(math.log(mh / ma))
    total = mh + ma
    evidence = min(float(features.get("home_evidence", 0.0)), float(features.get("away_evidence", 0.0)))
    evidence_factor = 0.65 + 0.35 * (1.0 - math.exp(-evidence / 8.0))
    return math.exp(-gap) * math.exp(-0.12 * (total - 2.55) ** 2) * evidence_factor


def apply_fitness(features: dict[str, Any], beta_rest: float, beta_density: float) -> dict[str, Any]:
    br = finite_number(beta_rest, "beta_rest", lo=-0.2, hi=0.2)
    bd = finite_number(beta_density, "beta_density", lo=-0.2, hi=0.2)
    out = dict(features)
    hr = features.get("home_rest_days")
    ar = features.get("away_rest_days")
    hd = int(features.get("home_density14", 0))
    ad = int(features.get("away_density14", 0))
    rest_diff = 0.0 if hr is None or ar is None else max(-14.0, min(14.0, float(hr) - float(ar))) / 7.0
    density_diff = max(-4, min(4, hd - ad))
    tilt = math.exp(br * rest_diff - bd * density_diff)
    out["mu_home"] = _clip(float(features["mu_home"]) * tilt, 0.05, 8.0)
    out["mu_away"] = _clip(float(features["mu_away"]) / tilt, 0.05, 8.0)
    return out


def joint_matrix(
    family: str,
    features: dict[str, Any],
    *,
    dispersion_home: float = 50.0,
    dispersion_away: float = 50.0,
    dependence: float = 0.0,
    max_goals: int = 14,
) -> list[list[float]]:
    if family not in FAMILIES:
        raise GovernanceError(f"unknown joint family: {family}")
    dep = finite_number(dependence, "dependence", lo=-4.0, hi=4.0)
    mh = finite_number(features["mu_home"], "mu_home", lo=0.0, hi=20.0, lo_open=True)
    ma = finite_number(features["mu_away"], "mu_away", lo=0.0, hi=20.0, lo_open=True)
    context = dependence_context(features)

    if family in ("INDEPENDENT_POISSON_FROZEN", "DIXON_COLES_LOW_SCORE", "DIAGONAL_INFLATION_BIVARIATE"):
        hp = poisson_pmf(mh, max_goals)
        ap = poisson_pmf(ma, max_goals)
    else:
        hp = nb_pmf(mh, dispersion_home, max_goals)
        ap = nb_pmf(ma, dispersion_away, max_goals)

    if family == "INDEPENDENT_POISSON_FROZEN":
        return _normalize(_outer(hp, ap))

    if family == "DIXON_COLES_LOW_SCORE":
        rho = _clip(dep, -0.18, 0.18)
        matrix = _outer(hp, ap)
        tau = {
            (0, 0): 1.0 - mh * ma * rho,
            (0, 1): 1.0 + mh * rho,
            (1, 0): 1.0 + ma * rho,
            (1, 1): 1.0 - rho,
        }
        for (x, y), factor in tau.items():
            if factor <= 0.0 or not math.isfinite(factor):
                raise GovernanceError("Dixon-Coles support became nonpositive")
            matrix[x][y] *= factor
        return _normalize(matrix)

    if family in ("DIAGONAL_INFLATION_BIVARIATE", "DYNAMIC_NB_DIAGONAL"):
        theta = _clip(dep, -1.0, 1.0) * context
        matrix = _outer(hp, ap)
        for x in range(min(len(matrix), len(matrix[0]))):
            score_shape = math.exp(-0.05 * (2 * x - (mh + ma)) ** 2)
            matrix[x][x] *= math.exp(theta * score_shape)
        return _normalize(matrix)

    if family == "DYNAMIC_NB_MARCO":
        delta = _clip(dep, -0.45, 0.45) * (0.5 + 0.5 * context)
        n = max_goals + 1
        cond_away_given_home = []
        for x in range(n):
            adj_a = _clip(ma * math.exp(delta * (x - mh) / (1.0 + mh)), 0.05, 8.0)
            cond_away_given_home.append(nb_pmf(adj_a, dispersion_away, max_goals))
        cond_home_given_away = []
        for y in range(n):
            adj_h = _clip(mh * math.exp(delta * (y - ma) / (1.0 + ma)), 0.05, 8.0)
            cond_home_given_away.append(nb_pmf(adj_h, dispersion_home, max_goals))
        matrix = [[0.0] * n for _ in range(n)]
        for x in range(n):
            for y in range(n):
                matrix[x][y] = 0.5 * (
                    hp[x] * cond_away_given_home[x][y]
                    + ap[y] * cond_home_given_away[y][x]
                )
        return _normalize(matrix)

    if family == "DYNAMIC_NB_SARMANOV":
        omega = _clip(dep, -2.5, 2.5) * (0.5 + 0.5 * context)
        kh = finite_number(dispersion_home, "dispersion_home", lo=0.3, hi=100.0)
        ka = finite_number(dispersion_away, "dispersion_away", lo=0.3, hi=100.0)
        ph = kh / (kh + mh)
        pa = ka / (ka + ma)
        qh = 1.0 - ph
        qa = 1.0 - pa
        ch = (ph / (1.0 - qh * ph)) ** kh
        ca = (pa / (1.0 - qa * pa)) ** ka
        hh = [ph ** x - ch for x in range(max_goals + 1)]
        ha = [pa ** y - ca for y in range(max_goals + 1)]
        matrix = _outer(hp, ap)
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                factor = 1.0 + omega * hh[x] * ha[y]
                if factor < 0.0 or not math.isfinite(factor):
                    raise GovernanceError("Sarmanov support became negative")
                matrix[x][y] *= factor
        return _normalize(matrix)

    raise GovernanceError("unreachable family")


def matrix_to_cells(matrix: list[list[float]]) -> list[dict[str, Any]]:
    norm = _normalize(matrix)
    return [
        {"home_goals": i, "away_goals": j, "probability": norm[i][j]}
        for i in range(len(norm))
        for j in range(len(norm[i]))
    ]


def matrix_1x2(matrix: list[list[float]]) -> dict[str, float]:
    norm = _normalize(matrix)
    out = {"home": 0.0, "draw": 0.0, "away": 0.0}
    for i, row in enumerate(norm):
        for j, p in enumerate(row):
            out["home" if i > j else "draw" if i == j else "away"] += p
    return validate_probability_vector(out, "matrix_1x2")


def exact_score_probability(matrix: list[list[float]], home_goals: int, away_goals: int) -> float:
    strict_nonnegative_int(home_goals, "home_goals")
    strict_nonnegative_int(away_goals, "away_goals")
    norm = _normalize(matrix)
    if home_goals >= len(norm) or away_goals >= len(norm[0]):
        return 1e-15
    return max(norm[home_goals][away_goals], 1e-15)


def softmax3(logits: list[float]) -> dict[str, float]:
    if len(logits) != 3 or any(not math.isfinite(x) for x in logits):
        raise GovernanceError("invalid logits")
    m = max(logits)
    ex = [math.exp(x - m) for x in logits]
    s = sum(ex)
    return validate_probability_vector({k: ex[i] / s for i, k in enumerate(CLASSES)}, "softmax3")


def head_features(features: dict[str, Any]) -> list[float]:
    mh = float(features["mu_home"])
    ma = float(features["mu_away"])
    gap = math.log(mh / ma)
    total = mh + ma
    context = dependence_context(features)
    evidence = math.log1p(min(float(features.get("home_evidence", 0.0)), float(features.get("away_evidence", 0.0))))
    return [1.0, gap, total - 2.6, context, evidence / 4.0]


def head_predict(features: dict[str, Any], weights: list[list[float]]) -> dict[str, float]:
    x = head_features(features)
    if len(weights) != 3 or any(len(row) != len(x) for row in weights):
        raise GovernanceError("1X2 head weight shape invalid")
    logits = [sum(float(w) * v for w, v in zip(row, x)) for row in weights]
    return softmax3(logits)


def kl_project_to_1x2(matrix: list[list[float]], target: dict[str, float]) -> list[list[float]]:
    target = validate_probability_vector(target, "kl_target")
    base = _normalize(matrix)
    current = matrix_1x2(base)
    factors = {}
    for cls in CLASSES:
        if current[cls] <= 0.0 and target[cls] > 0.0:
            raise GovernanceError(f"KL projection infeasible for {cls}")
        factors[cls] = 0.0 if current[cls] == 0.0 else target[cls] / current[cls]
    out = []
    for i, row in enumerate(base):
        new = []
        for j, p in enumerate(row):
            cls = "home" if i > j else "draw" if i == j else "away"
            new.append(p * factors[cls])
        out.append(new)
    out = _normalize(out)
    projected = matrix_1x2(out)
    if max(abs(projected[k] - target[k]) for k in CLASSES) > 1e-9:
        raise GovernanceError("KL projection constraints not met")
    return out


def prediction_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
