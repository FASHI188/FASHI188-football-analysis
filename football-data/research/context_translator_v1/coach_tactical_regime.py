from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

REGIME_DIMS = ("tempo", "high_press", "defensive_line_height", "passing_directness", "attacking_width",
               "transition_attack", "set_piece_attack", "set_piece_defence", "leading_contraction",
               "trailing_risk", "substitution_timing")
MATCHUP_DIMS = ("press_vs_buildup", "wide_vs_fullback", "aerial_vs_aerial", "counter_vs_highline",
                "possession_vs_lowblock", "setpiece_vs_setpiece", "striker_vs_cb")


class TacticalError(RuntimeError):
    pass


def _dt(v: str) -> datetime:
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise TacticalError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CoachRegime:
    coach_id: str
    team_id: str
    regime_start: str
    regime_end_if_known: str | None
    vector: dict[str, float]
    evidence: float
    uncertainty: float
    source_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_regime(coach_id: str, team_id: str, regime_start: str, historical_rows: list[dict[str, Any]], *,
                    cutoff: str, league_prior: dict[str, float] | None = None) -> CoachRegime:
    co = _dt(cutoff); start = _dt(regime_start); prior = league_prior or {d: 0.0 for d in REGIME_DIMS}
    usable = []
    for r in historical_rows:
        if r.get("coach_id") != coach_id or r.get("team_id") != team_id:
            continue
        k = _dt(r["known_at"])
        if not (start <= k < co):
            continue
        vals = r.get("tactical_features", {})
        if any(d not in REGIME_DIMS for d in vals):
            raise TacticalError("unknown tactical dimension")
        usable.append((k, vals, float(r.get("exposure", 1.0)), str(r.get("source_sha256", ""))))
    sums = defaultdict(float); ev = 0.0; source = []
    for k, vals, exposure, sha in usable:
        w = exposure * math.exp(-math.log(2)*max((co-k).days,0)/120.0)
        ev += w; source.append(sha)
        for d in REGIME_DIMS:
            sums[d] += w * float(vals.get(d, prior.get(d, 0.0)))
    shrink = ev/(ev+8.0)
    vec = {d: shrink*(sums[d]/max(ev,1e-9)) + (1-shrink)*float(prior.get(d,0.0)) for d in REGIME_DIMS}
    return CoachRegime(coach_id, team_id, regime_start, None, vec, ev, 1/math.sqrt(1+ev), _sha(source))


def fit_matchup_coefficients(rows: list[dict[str, Any]], *, ridge: float = 20.0) -> dict[str, float]:
    out = {}
    for d in MATCHUP_DIMS:
        xx = xy = 0.0
        for r in rows:
            x = float(r.get("matchup", {}).get(d, 0.0)); y = float(r.get("target_log_mu_residual", 0.0))
            xx += x*x; xy += x*y
        out[d] = xy/(xx+ridge)
    return out


def tactical_matchup(home: CoachRegime, away: CoachRegime, coeffs: dict[str, float]) -> tuple[float, float, str]:
    raw = {
        "press_vs_buildup": home.vector["high_press"] - away.vector["passing_directness"],
        "wide_vs_fullback": home.vector["attacking_width"] - away.vector["defensive_line_height"],
        "aerial_vs_aerial": home.vector["set_piece_attack"] - away.vector["set_piece_defence"],
        "counter_vs_highline": home.vector["transition_attack"] - away.vector["defensive_line_height"],
        "possession_vs_lowblock": -home.vector["passing_directness"] + away.vector["leading_contraction"],
        "setpiece_vs_setpiece": home.vector["set_piece_attack"] - away.vector["set_piece_defence"],
        "striker_vs_cb": 0.0,
    }
    delta = sum(float(coeffs.get(k,0.0))*v for k,v in raw.items())
    delta = max(-0.25, min(0.25, delta))
    return delta, -delta, _sha({"raw":raw,"coeffs":coeffs})
