from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


class DynamicBaseError(RuntimeError):
    pass


def _utc(value: datetime | str, name: str = "time") -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DynamicBaseError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(x: float, name: str) -> float:
    if isinstance(x, bool):
        raise DynamicBaseError(f"{name} bool invalid")
    x = float(x)
    if not math.isfinite(x):
        raise DynamicBaseError(f"{name} nonfinite")
    return x


def _clip(x: float, lo: float, hi: float) -> float:
    x = _finite(x, "clip")
    return min(hi, max(lo, x))


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def independent_poisson_summary(mu_home: float, mu_away: float, max_goals: int = 14) -> dict[str, Any]:
    """Fast exact equivalent of frozen V1 outer-product matrix normalization."""
    mh=_finite(mu_home,'mu_home'); ma=_finite(mu_away,'mu_away')
    if mh<=0 or ma<=0: raise DynamicBaseError('Poisson mean must be positive')
    hp=[math.exp(-mh)]; ap=[math.exp(-ma)]
    for k in range(1,max_goals+1):
        hp.append(hp[-1]*mh/k); ap.append(ap[-1]*ma/k)
    total=sum(hp)*sum(ap)
    if not math.isfinite(total) or total<=0: raise DynamicBaseError('invalid Poisson mass')
    h=d=a=0.0
    for i,ph in enumerate(hp):
        for j,pa in enumerate(ap):
            q=ph*pa/total
            if i>j: h+=q
            elif i==j: d+=q
            else: a+=q
    mean_h=sum(i*x for i,x in enumerate(hp))*sum(ap)/total
    mean_a=sum(j*x for j,x in enumerate(ap))*sum(hp)/total
    return {'p_home':h,'p_draw':d,'p_away':a,'mean_home':mean_h,'mean_away':mean_a}


@dataclass(frozen=True)
class DynamicParameters:
    dynamic_half_life_days: float
    dynamic_prior_matches: float
    dynamic_beta: float
    dynamic_cross_season_shrink: float
    min_effective_evidence: float = 2.0
    pooled_prior_weight: float = 0.50
    residual_clip: float = 2.5

    def __post_init__(self) -> None:
        if self.dynamic_half_life_days not in (90.0, 180.0, 360.0):
            raise DynamicBaseError("half-life outside frozen grid")
        if self.dynamic_prior_matches not in (4.0, 8.0, 16.0):
            raise DynamicBaseError("prior outside frozen grid")
        if self.dynamic_beta not in (0.05, 0.10, 0.15):
            raise DynamicBaseError("beta outside frozen grid")
        if self.dynamic_cross_season_shrink not in (0.40, 0.70):
            raise DynamicBaseError("season shrink outside frozen grid")
        if self.min_effective_evidence != 2.0 or self.pooled_prior_weight != 0.50 or self.residual_clip != 2.5:
            raise DynamicBaseError("fixed preregistered constants changed")


@dataclass
class ResidualState:
    residual_sum: float = 0.0
    weight: float = 0.0
    last_time: datetime | None = None
    last_season: str | None = None

    def snapshot(self, now: datetime, season: str, p: DynamicParameters) -> tuple[float, float]:
        now = _utc(now)
        if self.last_time is None:
            return 0.0, 0.0
        last = _utc(self.last_time)
        if now < last:
            raise DynamicBaseError("dynamic state time reversal")
        days = (now - last).total_seconds() / 86400.0
        decay = math.exp(-math.log(2.0) * days / p.dynamic_half_life_days)
        if self.last_season is not None and season != self.last_season:
            decay *= p.dynamic_cross_season_shrink
        return self.residual_sum * decay, self.weight * decay

    def advance_and_add(self, now: datetime, season: str, value: float, p: DynamicParameters) -> None:
        s, w = self.snapshot(now, season, p)
        self.residual_sum = s + _clip(value, -p.residual_clip, p.residual_clip)
        self.weight = w + 1.0
        self.last_time = _utc(now)
        self.last_season = str(season)


class DynamicEngineState:
    """Residual dynamic adapter over a frozen Football3 V1 engine module/state."""

    def __init__(self, v1_module: Any, v1_parameters: dict[str, Any], dynamic_parameters: DynamicParameters):
        self.v1 = v1_module
        self.v1_parameters = dict(v1_parameters)
        self.params = dynamic_parameters
        self.base = v1_module.EngineState(params=v1_module.Parameters(**self.v1_parameters))
        self.venue_attack: dict[tuple[str, str], ResidualState] = {}
        self.venue_defence: dict[tuple[str, str], ResidualState] = {}
        self.pooled_attack: dict[str, ResidualState] = {}
        self.pooled_defence: dict[str, ResidualState] = {}
        self.pending: dict[str, dict[str, Any]] = {}
        self.seen: set[str] = set()
        self.last_prediction_time: datetime | None = None
        self.last_apply_time: datetime | None = None

    def _fixture_identity(self, f: Any) -> dict[str, Any]:
        f.validate()
        return {
            "fixture_id": str(f.fixture_id), "competition_id": str(f.competition_id), "season": str(f.season),
            "kickoff": _utc(f.kickoff).isoformat(), "home_team_id": str(f.home_team_id), "away_team_id": str(f.away_team_id),
        }

    def _view_component(self, team_id: str, venue: str, component: str, season: str, now: datetime) -> tuple[float, float, str]:
        if venue not in ("home", "away") or component not in ("attack", "defence"):
            raise DynamicBaseError("invalid dynamic component")
        local_map = self.venue_attack if component == "attack" else self.venue_defence
        pool_map = self.pooled_attack if component == "attack" else self.pooled_defence
        local = local_map.get((team_id, venue))
        pooled = pool_map.get(team_id)
        ls, lw = (0.0, 0.0) if local is None else local.snapshot(now, season, self.params)
        ps, pw = (0.0, 0.0) if pooled is None else pooled.snapshot(now, season, self.params)
        effective = lw + self.params.pooled_prior_weight * pw
        if effective < self.params.min_effective_evidence:
            return 0.0, effective, "fallback_v1_insufficient_dynamic_evidence"
        pooled_mean = 0.0 if pw <= 0.0 else ps / pw
        pooled_reliability = min(1.0, pw / self.params.dynamic_prior_matches)
        prior_mean = self.params.pooled_prior_weight * pooled_reliability * pooled_mean
        signal = (ls + self.params.dynamic_prior_matches * prior_mean) / (lw + self.params.dynamic_prior_matches)
        return _clip(signal, -self.params.residual_clip, self.params.residual_clip), effective, "dynamic_residual"

    @staticmethod
    def _matrix_means(matrix: list[dict[str, Any]]) -> tuple[float, float]:
        mh = ma = total = 0.0
        for c in matrix:
            p = _finite(c["probability"], "matrix probability")
            if p < 0:
                raise DynamicBaseError("negative matrix probability")
            mh += int(c["home_goals"]) * p
            ma += int(c["away_goals"]) * p
            total += p
        if abs(total - 1.0) > 1e-8:
            raise DynamicBaseError("matrix mass != 1")
        return mh, ma

    def _predict_one_from_base(self, f: Any, base: dict[str, Any], *, include_matrix: bool = True) -> dict[str, Any]:
        ident = self._fixture_identity(f)
        for k in ('fixture_id','competition_id','season','home_team_id','away_team_id'):
            if str(base.get(k)) != ident[k]: raise DynamicBaseError('frozen V1 identity mismatch')
        if _utc(base.get('kickoff')) != _utc(f.kickoff): raise DynamicBaseError('frozen V1 kickoff mismatch')
        now = _utc(f.kickoff)
        ha, hae, has = self._view_component(str(f.home_team_id), 'home', 'attack', str(f.season), now)
        hd, hde, hds = self._view_component(str(f.home_team_id), 'home', 'defence', str(f.season), now)
        aa, aae, aas = self._view_component(str(f.away_team_id), 'away', 'attack', str(f.season), now)
        ad, ade, ads = self._view_component(str(f.away_team_id), 'away', 'defence', str(f.season), now)
        delta_h = self.params.dynamic_beta * (ha - ad); delta_a = self.params.dynamic_beta * (aa - hd)
        fallback = delta_h == 0.0 and delta_a == 0.0
        if fallback:
            mu_h=float(base['mu_home']); mu_a=float(base['mu_away']); p_home=float(base['p_home']); p_draw=float(base['p_draw']); p_away=float(base['p_away'])
            if include_matrix:
                matrix=base['score_matrix']; mh,ma=self._matrix_means(matrix)
            else:
                matrix=None
                if 'matrix_mean_home' in base and 'matrix_mean_away' in base: mh,ma=float(base['matrix_mean_home']),float(base['matrix_mean_away'])
                else: mh,ma=self._matrix_means(base['score_matrix'])
        else:
            mu_h=_clip(float(base['mu_home'])*math.exp(delta_h),float(self.base.params.min_rate),float(self.base.params.max_rate)); mu_a=_clip(float(base['mu_away'])*math.exp(delta_a),float(self.base.params.min_rate),float(self.base.params.max_rate))
            if include_matrix:
                matrix=self.v1.score_matrix(mu_h,mu_a); p_home,p_draw,p_away=self.v1.one_x_two(matrix); mh,ma=self._matrix_means(matrix)
            else:
                matrix=None; q=independent_poisson_summary(mu_h,mu_a,getattr(self.v1,'MAX_GOALS',14)); p_home,p_draw,p_away=q['p_home'],q['p_draw'],q['p_away']; mh,ma=q['mean_home'],q['mean_away']
        row={
            'schema_version':'football3-v1-1-dynamic-base-prediction-v1','engine':'Football3-V1.1-Dynamic-Base-research',**ident,
            'mu_home':mu_h,'mu_away':mu_a,'matrix_mean_home':mh,'matrix_mean_away':ma,'p_home':p_home,'p_draw':p_draw,'p_away':p_away,
            'base_v1_prediction_hash':base['prediction_hash'],
            'dynamic':{'home_attack':ha,'home_defence':hd,'away_attack':aa,'away_defence':ad,'home_attack_evidence':hae,'home_defence_evidence':hde,'away_attack_evidence':aae,'away_defence_evidence':ade,
                       'home_attack_source':has,'home_defence_source':hds,'away_attack_source':aas,'away_defence_source':ads,'delta_log_mu_home':delta_h,'delta_log_mu_away':delta_a,'fallback_exact_v1':fallback},
            'formal_weight':0,'formal_enablement':False}
        if include_matrix: row['score_matrix']=matrix
        row['prediction_sha256']=canonical_sha256(row); return row

    def _predict_one(self, f: Any, *, include_matrix: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        base=self.base.predict(f); return self._predict_one_from_base(f,base,include_matrix=include_matrix),base

    def _validate_prediction_batch(self, fixtures: Iterable[Any]) -> tuple[list[Any], list[dict[str,Any]], datetime]:
        batch=list(fixtures)
        if not batch: return [],[],datetime(1970,1,1,tzinfo=timezone.utc)
        identities=[self._fixture_identity(f) for f in batch]; cutoffs={_utc(f.kickoff) for f in batch}
        if len(cutoffs)!=1: raise DynamicBaseError('predict_batch requires exact same kickoff')
        now=next(iter(cutoffs))
        if self.last_prediction_time is not None and now<self.last_prediction_time: raise DynamicBaseError('prediction time reversal')
        ids=[x['fixture_id'] for x in identities]
        if len(ids)!=len(set(ids)): raise DynamicBaseError('duplicate fixture within kickoff batch')
        teams=[t for x in identities for t in (x['home_team_id'],x['away_team_id'])]
        if len(teams)!=len(set(teams)): raise DynamicBaseError('same team appears more than once in kickoff batch')
        for fid in ids:
            if fid in self.pending or fid in self.seen: raise DynamicBaseError('duplicate/pending fixture')
        return batch,identities,now

    def predict_batch(self, fixtures: Iterable[Any], *, include_matrix: bool = True) -> list[dict[str, Any]]:
        batch,identities,now=self._validate_prediction_batch(fixtures)
        if not batch: return []
        rows=[]
        for f,ident in zip(batch,identities):
            row,base=self._predict_one(f,include_matrix=include_matrix); rows.append(row); self.pending[ident['fixture_id']]={'identity':ident,'base_mu_home':float(base['mu_home']),'base_mu_away':float(base['mu_away']),'base_prediction_hash':base['prediction_hash'],'v1_1_prediction_sha256':row['prediction_sha256']}
        self.last_prediction_time=now; return rows

    def predict_batch_from_frozen_v1(self, fixtures: Iterable[Any], frozen_base: list[dict[str,Any]], *, include_matrix: bool = False) -> list[dict[str,Any]]:
        batch,identities,now=self._validate_prediction_batch(fixtures)
        if len(batch)!=len(frozen_base): raise DynamicBaseError('frozen V1 batch length mismatch')
        rows=[]
        for f,ident,base in zip(batch,identities,frozen_base):
            row=self._predict_one_from_base(f,base,include_matrix=include_matrix); rows.append(row); self.pending[ident['fixture_id']]={'identity':ident,'base_mu_home':float(base['mu_home']),'base_mu_away':float(base['mu_away']),'base_prediction_hash':base['prediction_hash'],'v1_1_prediction_sha256':row['prediction_sha256']}
        self.last_prediction_time=now; return rows

    def _add(self, maps: dict[Any, ResidualState], key: Any, when: datetime, season: str, signal: float) -> None:
        maps.setdefault(key, ResidualState()).advance_and_add(when, season, signal, self.params)

    def apply_batch(self, fixtures: Iterable[Any], labels: dict[str, tuple[int, int, datetime]], *, as_of: datetime, update_frozen_v1_state: bool = True) -> None:
        batch = list(fixtures)
        if not batch:
            return
        now = _utc(as_of, "as_of")
        identities = [self._fixture_identity(f) for f in batch]
        cutoffs = {_utc(f.kickoff) for f in batch}
        if len(cutoffs) != 1:
            raise DynamicBaseError("apply_batch requires exact same kickoff")
        kickoff = next(iter(cutoffs))
        ids = [x["fixture_id"] for x in identities]
        if len(ids) != len(set(ids)) or set(ids) != set(labels):
            raise DynamicBaseError("batch label identity mismatch")
        teams = [t for x in identities for t in (x["home_team_id"], x["away_team_id"])]
        if len(teams) != len(set(teams)):
            raise DynamicBaseError("same team appears more than once in kickoff batch")
        if self.last_apply_time is not None and now < self.last_apply_time:
            raise DynamicBaseError("result application time reversal")
        prepared = []
        for f, ident in zip(batch, identities):
            pending = self.pending.get(ident["fixture_id"])
            if pending is None:
                raise DynamicBaseError("result without frozen prediction")
            if pending["identity"] != ident:
                raise DynamicBaseError("fixture identity changed after freeze")
            lab = labels[ident["fixture_id"]]
            if not isinstance(lab, tuple) or len(lab) != 3:
                raise DynamicBaseError("label must be (home_goals, away_goals, result_available_at)")
            hg, ag, available = lab
            if type(hg) is not int or type(ag) is not int or hg < 0 or ag < 0 or hg > 30 or ag > 30:
                raise DynamicBaseError("invalid goal label")
            available = _utc(available, "result_available_at")
            if available > now or available <= kickoff:
                raise DynamicBaseError("future/invalid result availability")
            prepared.append((f, ident, hg, ag, available, pending))

        base_labels: dict[str, tuple[int, int]] = {}
        for f, ident, hg, ag, available, pending in prepared:
            mu_h = max(1e-9, float(pending["base_mu_home"])); mu_a = max(1e-9, float(pending["base_mu_away"]))
            home_signal = _clip((hg - mu_h) / math.sqrt(mu_h + 0.25), -self.params.residual_clip, self.params.residual_clip)
            away_signal = _clip((ag - mu_a) / math.sqrt(mu_a + 0.25), -self.params.residual_clip, self.params.residual_clip)
            ht, at, season = ident["home_team_id"], ident["away_team_id"], ident["season"]
            self._add(self.venue_attack, (ht, "home"), available, season, home_signal)
            self._add(self.venue_defence, (at, "away"), available, season, -home_signal)
            self._add(self.venue_attack, (at, "away"), available, season, away_signal)
            self._add(self.venue_defence, (ht, "home"), available, season, -away_signal)
            self._add(self.pooled_attack, ht, available, season, home_signal)
            self._add(self.pooled_defence, at, available, season, -home_signal)
            self._add(self.pooled_attack, at, available, season, away_signal)
            self._add(self.pooled_defence, ht, available, season, -away_signal)
            base_labels[ident["fixture_id"]] = (hg, ag)

        if update_frozen_v1_state:
            self.base.apply_batch(batch, base_labels)
        for _, ident, *_ in prepared:
            self.seen.add(ident["fixture_id"])
            del self.pending[ident["fixture_id"]]
        self.last_apply_time = now

    @staticmethod
    def residual_signal(goals: int, frozen_mu: float) -> float:
        if type(goals) is not int or goals < 0:
            raise DynamicBaseError("invalid goals")
        mu = _finite(frozen_mu, "frozen_mu")
        if mu <= 0:
            raise DynamicBaseError("frozen_mu must be positive")
        return (goals - mu) / math.sqrt(mu + 0.25)

    def state_digest(self) -> str:
        def st_map(m: dict[Any, ResidualState]) -> list[dict[str, Any]]:
            out = []
            for key, st in sorted(m.items(), key=lambda kv: repr(kv[0])):
                out.append({"key": list(key) if isinstance(key, tuple) else key,
                            "residual_sum": st.residual_sum, "weight": st.weight,
                            "last_time": None if st.last_time is None else _utc(st.last_time).isoformat(),
                            "last_season": st.last_season})
            return out
        payload = {
            "params": self.params.__dict__, "v1_parameters": self.v1_parameters,
            "venue_attack": st_map(self.venue_attack), "venue_defence": st_map(self.venue_defence),
            "pooled_attack": st_map(self.pooled_attack), "pooled_defence": st_map(self.pooled_defence),
            "pending": self.pending, "seen": sorted(self.seen),
            "last_prediction_time": None if self.last_prediction_time is None else _utc(self.last_prediction_time).isoformat(),
            "last_apply_time": None if self.last_apply_time is None else _utc(self.last_apply_time).isoformat(),
        }
        return canonical_sha256(payload)
