from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


class LineupError(RuntimeError):
    pass


def _dt(text: str) -> datetime:
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise LineupError("timezone required")
    return d.astimezone(timezone.utc)


def _sha(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class LineupScenario:
    scenario_id: str
    route: str
    probability: float
    home_player_ids: list[str]
    away_player_ids: list[str]
    known_at_max: str | None
    uncertainty: float
    scenario_sha256: str

    def schema_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_player_row(row: dict[str, Any], cutoff: str) -> None:
    required = {"player_id", "starting_probability", "availability_probability", "expected_minutes_distribution",
                "injury_status", "suspension_status", "return_status", "rotation_probability", "role_distribution",
                "replacement_quality", "uncertainty", "known_at"}
    if not required.issubset(row):
        raise LineupError("lineup player row missing fields")
    if _dt(row["known_at"]) >= _dt(cutoff):
        raise LineupError("lineup evidence not strictly pre-cutoff")
    for f in ("starting_probability", "availability_probability", "rotation_probability"):
        x = float(row[f])
        if not 0 <= x <= 1:
            raise LineupError(f"{f} outside [0,1]")


def _top_xi(players: list[dict[str, Any]], cutoff: str) -> tuple[list[str], list[dict[str, Any]]]:
    for p in players:
        _validate_player_row(p, cutoff)
    ranked = sorted(players, key=lambda p: float(p["starting_probability"]) * float(p["availability_probability"]), reverse=True)
    return [str(p["player_id"]) for p in ranked[:11]], ranked


def build_lineup_scenarios(home_players: list[dict[str, Any]] | None, away_players: list[dict[str, Any]] | None,
                           *, cutoff: str, confirmed: dict[str, Any] | None = None, max_scenarios: int = 4) -> list[LineupScenario]:
    co = _dt(cutoff)
    if confirmed is not None:
        required = {"published_at", "home_player_ids", "away_player_ids", "source_sha256"}
        if set(confirmed) != required or _dt(confirmed["published_at"]) >= co:
            raise LineupError("CONFIRMED_LINEUP requires exact pre-cutoff publication evidence")
        payload = {"route": "CONFIRMED_LINEUP", "home": confirmed["home_player_ids"], "away": confirmed["away_player_ids"],
                   "known_at": confirmed["published_at"], "source": confirmed["source_sha256"]}
        sid = "confirmed_" + _sha(payload)[:16]
        return [LineupScenario(sid, "CONFIRMED_LINEUP", 1.0, list(confirmed["home_player_ids"]),
                               list(confirmed["away_player_ids"]), confirmed["published_at"], 0.05, _sha(payload))]
    if not home_players or not away_players:
        payload = {"route": "LINEUP_UNKNOWN", "cutoff": cutoff}
        return [LineupScenario("unknown_" + _sha(payload)[:16], "LINEUP_UNKNOWN", 1.0, [], [], None, 1.0, _sha(payload))]
    hxi, hr = _top_xi(home_players, cutoff); axi, ar = _top_xi(away_players, cutoff)
    candidates: list[tuple[list[str], list[str], float, float, str]] = []
    base_prob = math.prod(max(1e-6, float(p["starting_probability"]) * float(p["availability_probability"])) for p in hr[:11])
    abase_prob = math.prod(max(1e-6, float(p["starting_probability"]) * float(p["availability_probability"])) for p in ar[:11])
    candidates.append((hxi, axi, base_prob * abase_prob, 0.20, max([p["known_at"] for p in hr[:11]+ar[:11]])))
    if len(hr) > 11:
        alt = hxi[:-1] + [str(hr[11]["player_id"])]
        p = base_prob * (1-float(hr[10]["starting_probability"])) * float(hr[11]["starting_probability"]) * abase_prob
        candidates.append((alt, axi, max(p, 1e-9), 0.35, max([x["known_at"] for x in hr[:12]+ar[:11]])))
    if len(ar) > 11:
        alt = axi[:-1] + [str(ar[11]["player_id"])]
        p = abase_prob * (1-float(ar[10]["starting_probability"])) * float(ar[11]["starting_probability"]) * base_prob
        candidates.append((hxi, alt, max(p, 1e-9), 0.35, max([x["known_at"] for x in hr[:11]+ar[:12]])))
    candidates = candidates[:max_scenarios]
    total = sum(c[2] for c in candidates)
    out: list[LineupScenario] = []
    for home, away, weight, unc, known in candidates:
        payload = {"route": "EXPECTED_LINEUP", "home": home, "away": away, "known_at": known}
        sid = "expected_" + _sha(payload)[:16]
        out.append(LineupScenario(sid, "EXPECTED_LINEUP", weight/total, home, away, known, unc, _sha(payload)))
    if abs(sum(x.probability for x in out)-1.0) > 1e-9:
        raise LineupError("scenario normalization failed")
    return out
