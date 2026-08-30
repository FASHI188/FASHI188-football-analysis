from __future__ import annotations

import math
from datetime import datetime, timezone


class MarketAssistError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MarketAssistError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _norm(values: list[float]) -> list[float]:
    if not values or any((not math.isfinite(v) or v <= 0) for v in values):
        raise MarketAssistError("probability vector invalid")
    s = sum(values)
    if s <= 0:
        raise MarketAssistError("probability mass invalid")
    return [v / s for v in values]


def assist(pure_prediction: dict, decimal_1x2: tuple[float, float, float], observed_at: datetime, kickoff: datetime, beta: float = 0.35) -> dict:
    observed_at, kickoff = _utc(observed_at), _utc(kickoff)
    if observed_at >= kickoff:
        raise MarketAssistError("price snapshot is not strictly prematch")
    if not 0.0 <= beta <= 1.0:
        raise MarketAssistError("beta out of range")
    try:
        pure = [float(pure_prediction[k]) for k in ("p_home", "p_draw", "p_away")]
    except Exception as exc:
        raise MarketAssistError("pure prediction missing") from exc
    pure = _norm(pure)
    odds = [float(x) for x in decimal_1x2]
    if any((not math.isfinite(x) or x <= 1.0) for x in odds):
        raise MarketAssistError("decimal price invalid")
    implied = _norm([1.0 / x for x in odds])
    combined = _norm([math.exp((1.0 - beta) * math.log(max(1e-15, p)) + beta * math.log(max(1e-15, q))) for p, q in zip(pure, implied)])
    return {
        "engine": "Football3-New-Engine-V1-assisted",
        "fixture_id": pure_prediction.get("fixture_id"),
        "observed_at": observed_at.isoformat(),
        "kickoff": kickoff.isoformat(),
        "beta": beta,
        "p_home": combined[0],
        "p_draw": combined[1],
        "p_away": combined[2],
        "source": "verified_prematch_1x2_only",
    }
