from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

OUTCOMES = ("home", "draw", "away")
FAMILY = "INDEPENDENT_POISSON_FROZEN"


class V21Error(RuntimeError):
    pass


def _finite(x: float, name: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(x, bool):
        raise V21Error(f"{name} must be numeric, not bool")
    y = float(x)
    if not math.isfinite(y):
        raise V21Error(f"{name} must be finite")
    if lo is not None and y < lo:
        raise V21Error(f"{name} below {lo}")
    if hi is not None and y > hi:
        raise V21Error(f"{name} above {hi}")
    return y


def _iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise V21Error("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str) -> datetime:
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise V21Error("serialized datetime must be timezone-aware")
    return d.astimezone(timezone.utc)


def _decay(days: float, half_life_days: float) -> float:
    if days < -1e-12:
        raise V21Error("time reversal / future state")
    return math.exp(-math.log(2.0) * max(0.0, days) / half_life_days)


def _clip(x: float, lo: float, hi: float) -> float:
    if not math.isfinite(x):
        raise V21Error("nonfinite model value")
    return min(hi, max(lo, x))


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


@dataclass(frozen=True)
class Parameters:
    team_half_life_days: float = 240.0
    competition_half_life_days: float = 900.0
    team_prior_matches: float = 12.0
    competition_prior_matches: float = 48.0
    residual_strength: float = 0.60
    cross_season_shrink: float = 0.65
    global_home_rate: float = 1.38
    global_away_rate: float = 1.12
    min_rate: float = 0.08
    max_rate: float = 6.0
    max_goals: int = 14
    team_venue_bias_enabled: bool = False

    def __post_init__(self) -> None:
        _finite(self.team_half_life_days, "team_half_life_days", 30.0, 3000.0)
        _finite(self.competition_half_life_days, "competition_half_life_days", 60.0, 6000.0)
        _finite(self.team_prior_matches, "team_prior_matches", 1.0, 200.0)
        _finite(self.competition_prior_matches, "competition_prior_matches", 2.0, 1000.0)
        _finite(self.residual_strength, "residual_strength", 0.0, 2.0)
        _finite(self.cross_season_shrink, "cross_season_shrink", 0.0, 1.0)
        _finite(self.global_home_rate, "global_home_rate", 0.1, 5.0)
        _finite(self.global_away_rate, "global_away_rate", 0.1, 5.0)
        _finite(self.min_rate, "min_rate", 0.001, 5.0)
        _finite(self.max_rate, "max_rate", 0.1, 20.0)
        if not self.min_rate < self.max_rate:
            raise V21Error("min_rate must be < max_rate")
        if type(self.max_goals) is not int or not (8 <= self.max_goals <= 20):
            raise V21Error("max_goals must be strict int in [8,20]")
        if self.team_venue_bias_enabled is not False:
            raise V21Error("phase-1 team venue bias is preregistered DISABLED")


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
        for name in ("fixture_id", "competition_id", "season", "home_team_id", "away_team_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                raise V21Error(f"{name} must be non-empty string")
        if self.home_team_id == self.away_team_id:
            raise V21Error("home/away team identity collision")
        _iso(self.kickoff)
        if self.round_index is not None and (type(self.round_index) is not int or self.round_index <= 0):
            raise V21Error("round_index must be positive strict int or None")


@dataclass
class TeamState:
    attack_residual_sum: float = 0.0
    defence_residual_sum: float = 0.0
    evidence: float = 0.0
    last_cutoff: str | None = None
    last_season: str | None = None
    season_transition_count: int = 0


@dataclass
class CompetitionState:
    home_goals: float = 0.0
    away_goals: float = 0.0
    evidence: float = 0.0
    last_cutoff: str | None = None


class EngineState:
    """V2.1 pure-football base.

    Team attack/defence are common-scale PIT residual states. Competition home/away
    baselines are separate intercepts and enter each mean exactly once.
    """

    def __init__(self, params: Parameters | None = None):
        self.params = params or Parameters()
        self.teams: dict[str, TeamState] = {}
        self.competitions: dict[str, CompetitionState] = {}
        self.seen_fixtures: set[str] = set()
        self.pending_predictions: dict[str, dict[str, Any]] = {}
        self.last_prediction_cutoff: str | None = None
        self.last_applied_cutoff: str | None = None

    def _team_key(self, competition_id: str, team_id: str) -> str:
        return f"{competition_id}|{team_id}"

    def _team(self, competition_id: str, team_id: str) -> TeamState:
        return self.teams.setdefault(self._team_key(competition_id, team_id), TeamState())

    def _competition(self, competition_id: str) -> CompetitionState:
        return self.competitions.setdefault(competition_id, CompetitionState())

    def _prepare_team_season(self, st: TeamState, season: str) -> None:
        if st.last_season is None:
            st.last_season = season
            return
        if st.last_season != season:
            s = self.params.cross_season_shrink
            st.attack_residual_sum *= s
            st.defence_residual_sum *= s
            st.evidence *= s
            st.last_season = season
            st.season_transition_count += 1

    def _competition_rates(self, competition_id: str, cutoff: datetime) -> tuple[float, float, float]:
        st = self.competitions.get(competition_id)
        if st is None:
            return self.params.global_home_rate, self.params.global_away_rate, 0.0
        hg, ag, ev = st.home_goals, st.away_goals, st.evidence
        if st.last_cutoff is not None:
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

    def _team_components(
        self, competition_id: str, team_id: str, season: str, cutoff: datetime, common_scale: float
    ) -> tuple[float, float, float]:
        st = self.teams.get(self._team_key(competition_id, team_id))
        if st is None:
            return 0.0, 0.0, 0.0
        self._prepare_team_season(st, season)
        attack = st.attack_residual_sum
        defence = st.defence_residual_sum
        ev = st.evidence
        if st.last_cutoff is not None:
            days = (cutoff - _dt(st.last_cutoff)).total_seconds() / 86400.0
            d = _decay(days, self.params.team_half_life_days)
            attack *= d
            defence *= d
            ev *= d
        denom = max(1e-9, common_scale) * (ev + self.params.team_prior_matches)
        a = self.params.residual_strength * attack / denom
        dfn = self.params.residual_strength * defence / denom
        return a, dfn, max(0.0, ev)

    def _predict_one(self, fixture: Fixture, *, include_matrix: bool = True) -> dict[str, Any]:
        fixture.validate()
        cutoff = fixture.kickoff.astimezone(timezone.utc)
        ch, ca, cev = self._competition_rates(fixture.competition_id, cutoff)
        common = 0.5 * (ch + ca)
        ha, hd, hev = self._team_components(fixture.competition_id, fixture.home_team_id, fixture.season, cutoff, common)
        aa, ad, aev = self._team_components(fixture.competition_id, fixture.away_team_id, fixture.season, cutoff, common)
        log_h = math.log(ch) + ha - ad
        log_a = math.log(ca) + aa - hd
        mu_h = _clip(math.exp(log_h), self.params.min_rate, self.params.max_rate)
        mu_a = _clip(math.exp(log_a), self.params.min_rate, self.params.max_rate)
        summary = independent_poisson_summary(mu_h, mu_a, self.params.max_goals)
        p = summary["p"]
        payload = {
            "schema_version": "football3-v2-1-base-prediction-v1", "engine": "Football3-V2.1-base-repair",
            "joint_family": FAMILY, "fixture_id": fixture.fixture_id, "competition_id": fixture.competition_id,
            "season": fixture.season, "kickoff": _iso(cutoff), "home_team_id": fixture.home_team_id,
            "away_team_id": fixture.away_team_id, "round_index": fixture.round_index,
            "mu_home": mu_h, "mu_away": mu_a, "p_home": p["home"], "p_draw": p["draw"], "p_away": p["away"],
            "matrix_mean_home": summary["mean_home"], "matrix_mean_away": summary["mean_away"],
            "components": {
                "competition_home_rate": ch, "competition_away_rate": ca,
                "competition_home_intercept": math.log(ch), "competition_away_intercept": math.log(ca),
                "home_attack": ha, "home_defence": hd, "away_attack": aa, "away_defence": ad,
                "team_common_scale": common, "team_venue_bias_home": 0.0, "team_venue_bias_away": 0.0,
            },
            "home_evidence": hev, "away_evidence": aev, "competition_evidence": cev,
            "formal_weight": 0, "formal_enablement": False,
        }
        if include_matrix:
            payload["score_matrix"] = independent_poisson_matrix(mu_h, mu_a, self.params.max_goals)
        payload["prediction_sha256"] = canonical_sha256(payload)
        return payload

    def predict_batch(self, fixtures: Iterable[Fixture], *, include_matrix: bool = True) -> list[dict[str, Any]]:
        batch = list(fixtures)
        if not batch:
            return []
        for f in batch:
            f.validate()
        cutoffs = {f.kickoff.astimezone(timezone.utc) for f in batch}
        if len(cutoffs) != 1:
            raise V21Error("predict_batch requires exact same kickoff")
        cutoff = next(iter(cutoffs))
        if self.last_prediction_cutoff is not None and cutoff < _dt(self.last_prediction_cutoff):
            raise V21Error("prediction cutoff time reversal")
        ids = [f.fixture_id for f in batch]
        if len(ids) != len(set(ids)):
            raise V21Error("duplicate fixture within kickoff batch")
        teams = [t for f in batch for t in (f.home_team_id, f.away_team_id)]
        if len(teams) != len(set(teams)):
            raise V21Error("same team appears more than once in same kickoff batch")
        for f in batch:
            if f.fixture_id in self.seen_fixtures or f.fixture_id in self.pending_predictions:
                raise V21Error(f"duplicate/pending fixture {f.fixture_id}")
        rows = [self._predict_one(f, include_matrix=include_matrix) for f in batch]
        for f, row in zip(batch, rows):
            self.pending_predictions[f.fixture_id] = {
                "fixture": {"fixture_id": f.fixture_id, "competition_id": f.competition_id, "season": f.season,
                            "kickoff": _iso(f.kickoff), "home_team_id": f.home_team_id, "away_team_id": f.away_team_id,
                            "round_index": f.round_index},
                "mu_home": row["mu_home"], "mu_away": row["mu_away"], "prediction_sha256": row["prediction_sha256"],
            }
        self.last_prediction_cutoff = _iso(cutoff)
        return rows

    def apply_batch(self, fixtures: Iterable[Fixture], labels: dict[str, tuple[int, int, datetime]], *, as_of: datetime) -> None:
        batch = list(fixtures)
        if not batch:
            return
        now = _dt(_iso(as_of))
        for f in batch:
            f.validate()
        cutoffs = {f.kickoff.astimezone(timezone.utc) for f in batch}
        if len(cutoffs) != 1:
            raise V21Error("apply_batch requires exact same kickoff")
        cutoff = next(iter(cutoffs))
        ids = [f.fixture_id for f in batch]
        if len(ids) != len(set(ids)) or set(ids) != set(labels):
            raise V21Error("batch fixture/label identity mismatch")
        teams = [t for f in batch for t in (f.home_team_id, f.away_team_id)]
        if len(teams) != len(set(teams)):
            raise V21Error("same team appears more than once in same kickoff batch")
        if self.last_applied_cutoff is not None and cutoff < _dt(self.last_applied_cutoff):
            raise V21Error("applied fixture cutoff time reversal")
        prepared = []
        for f in batch:
            pending = self.pending_predictions.get(f.fixture_id)
            if pending is None:
                raise V21Error("result update without frozen pre-match prediction")
            got_fixture = {"fixture_id": f.fixture_id, "competition_id": f.competition_id, "season": f.season,
                           "kickoff": _iso(f.kickoff), "home_team_id": f.home_team_id, "away_team_id": f.away_team_id,
                           "round_index": f.round_index}
            if pending["fixture"] != got_fixture:
                raise V21Error("frozen fixture identity changed before update")
            lab = labels[f.fixture_id]
            if not isinstance(lab, tuple) or len(lab) != 3:
                raise V21Error("label must be (home_goals, away_goals, result_available_at)")
            hg, ag, available = lab
            if type(hg) is not int or type(ag) is not int or hg < 0 or ag < 0:
                raise V21Error("goals must be nonnegative strict integers")
            available_utc = _dt(_iso(available))
            if available_utc < cutoff:
                raise V21Error("result availability precedes fixture cutoff")
            if available_utc > now:
                raise V21Error("future result attempted before availability")
            prepared.append((f, hg, ag, pending))
        updates = []
        for f, hg, ag, pending in prepared:
            mu_h, mu_a = float(pending["mu_home"]), float(pending["mu_away"])
            updates.append((f, hg, ag, hg - mu_h, ag - mu_a, mu_a - ag, mu_h - hg))
        by_comp: dict[str, list[tuple[Any, ...]]] = {}
        for row in updates:
            by_comp.setdefault(row[0].competition_id, []).append(row)
        for cid, rows in sorted(by_comp.items()):
            comp = self._competition(cid)
            if comp.last_cutoff is not None:
                days = (cutoff - _dt(comp.last_cutoff)).total_seconds() / 86400.0
                d = _decay(days, self.params.competition_half_life_days)
                comp.home_goals *= d; comp.away_goals *= d; comp.evidence *= d
            comp.home_goals += sum(r[1] for r in rows); comp.away_goals += sum(r[2] for r in rows)
            comp.evidence += float(len(rows)); comp.last_cutoff = _iso(cutoff)
        for f, hg, ag, h_att, a_att, h_def, a_def in updates:
            for team_id, att, dfn in ((f.home_team_id, h_att, h_def), (f.away_team_id, a_att, a_def)):
                st = self._team(f.competition_id, team_id)
                self._prepare_team_season(st, f.season)
                if st.last_cutoff is not None:
                    days = (cutoff - _dt(st.last_cutoff)).total_seconds() / 86400.0
                    d = _decay(days, self.params.team_half_life_days)
                    st.attack_residual_sum *= d; st.defence_residual_sum *= d; st.evidence *= d
                st.attack_residual_sum += att; st.defence_residual_sum += dfn; st.evidence += 1.0
                st.last_cutoff = _iso(cutoff)
            self.seen_fixtures.add(f.fixture_id); self.pending_predictions.pop(f.fixture_id, None)
        self.last_applied_cutoff = _iso(cutoff)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "football3-v2-1-engine-state-v1", "params": asdict(self.params),
                "teams": {k: asdict(v) for k, v in sorted(self.teams.items())},
                "competitions": {k: asdict(v) for k, v in sorted(self.competitions.items())},
                "seen_fixtures": sorted(self.seen_fixtures),
                "pending_predictions": {k: self.pending_predictions[k] for k in sorted(self.pending_predictions)},
                "last_prediction_cutoff": self.last_prediction_cutoff, "last_applied_cutoff": self.last_applied_cutoff}

    def serialize(self) -> bytes:
        return canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "EngineState":
        if obj.get("schema_version") != "football3-v2-1-engine-state-v1":
            raise V21Error("state schema mismatch")
        out = cls(Parameters(**obj["params"]))
        out.teams = {k: TeamState(**v) for k, v in obj.get("teams", {}).items()}
        out.competitions = {k: CompetitionState(**v) for k, v in obj.get("competitions", {}).items()}
        out.seen_fixtures = set(map(str, obj.get("seen_fixtures", [])))
        out.pending_predictions = {str(k): dict(v) for k, v in obj.get("pending_predictions", {}).items()}
        out.last_prediction_cutoff = obj.get("last_prediction_cutoff"); out.last_applied_cutoff = obj.get("last_applied_cutoff")
        return out

    @classmethod
    def deserialize(cls, raw: bytes) -> "EngineState":
        return cls.from_dict(json.loads(raw.decode("utf-8")))


def poisson_pmf(mean: float, max_goals: int) -> list[float]:
    m = _finite(mean, "poisson.mean", 1e-12, 20.0)
    if type(max_goals) is not int or max_goals < 1:
        raise V21Error("max_goals invalid")
    out = [math.exp(-m)]
    for k in range(1, max_goals + 1):
        out.append(out[-1] * m / k)
    return out


def independent_poisson_summary(mu_home: float, mu_away: float, max_goals: int = 14) -> dict[str, Any]:
    hp, ap = poisson_pmf(mu_home, max_goals), poisson_pmf(mu_away, max_goals)
    sh, sa = sum(hp), sum(ap); total = sh * sa
    if not math.isfinite(total) or total <= 0:
        raise V21Error("matrix total invalid")
    cum_a = home = draw = 0.0
    for i, ph in enumerate(hp):
        home += ph * cum_a; draw += ph * ap[i]; cum_a += ap[i]
    home /= total; draw /= total; away = 1.0 - home - draw
    if min(home, draw, away) < -1e-14:
        raise V21Error("summary 1X2 invalid")
    return {"p": {"home": max(0.0, home), "draw": max(0.0, draw), "away": max(0.0, away)},
            "mean_home": sum(i * ph for i, ph in enumerate(hp)) / sh,
            "mean_away": sum(i * pa for i, pa in enumerate(ap)) / sa, "pre_normalization_total": total}


def independent_poisson_matrix(mu_home: float, mu_away: float, max_goals: int = 14) -> list[list[float]]:
    hp, ap = poisson_pmf(mu_home, max_goals), poisson_pmf(mu_away, max_goals)
    matrix = [[x * y for y in ap] for x in hp]; total = sum(sum(row) for row in matrix)
    if not math.isfinite(total) or total <= 0:
        raise V21Error("matrix total invalid")
    out = [[v / total for v in row] for row in matrix]
    if any((not math.isfinite(v) or v < 0) for row in out for v in row):
        raise V21Error("matrix invalid")
    if abs(sum(sum(row) for row in out) - 1.0) > 1e-12:
        raise V21Error("matrix does not sum to one")
    return out


def matrix_1x2(matrix: list[list[float]]) -> dict[str, float]:
    total = sum(sum(row) for row in matrix)
    if not math.isfinite(total) or total <= 0:
        raise V21Error("matrix total invalid")
    out = {"home": 0.0, "draw": 0.0, "away": 0.0}
    for i, row in enumerate(matrix):
        for j, raw in enumerate(row):
            p = float(raw) / total
            if not math.isfinite(p) or p < 0:
                raise V21Error("matrix cell invalid")
            out["home" if i > j else "draw" if i == j else "away"] += p
    if abs(sum(out.values()) - 1.0) > 1e-12:
        raise V21Error("1X2 integration invalid")
    return out


def matrix_mean_goals(matrix: list[list[float]]) -> tuple[float, float]:
    total = sum(sum(row) for row in matrix)
    if total <= 0 or not math.isfinite(total):
        raise V21Error("matrix total invalid")
    return (sum(i * p for i, row in enumerate(matrix) for p in row) / total,
            sum(j * p for row in matrix for j, p in enumerate(row)) / total)
