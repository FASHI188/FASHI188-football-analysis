from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


class IdentityError(RuntimeError):
    pass


def _norm(text: str) -> str:
    return " ".join(text.casefold().strip().split())


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _dt(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() is None:
        raise IdentityError("membership timestamp must be timezone-aware")
    return d.astimezone(timezone.utc)


@dataclass
class IdentityRegistry:
    canonical: dict[str, dict[str, str]] = field(default_factory=lambda: {"team": {}, "player": {}, "coach": {}, "match": {}})
    aliases: dict[str, dict[str, set[str]]] = field(default_factory=lambda: {"team": {}, "player": {}, "coach": {}, "match": {}})
    memberships: list[dict[str, str]] = field(default_factory=list)

    def register(self, kind: str, permanent_id: str, canonical_name: str, aliases: Iterable[str] = ()) -> None:
        if kind not in self.canonical or not permanent_id or not canonical_name:
            raise IdentityError("invalid identity registration")
        existing = self.canonical[kind].get(permanent_id)
        if existing is not None and existing != canonical_name:
            raise IdentityError(f"canonical rename requires explicit alias event for {permanent_id}")
        self.canonical[kind][permanent_id] = canonical_name
        for name in (canonical_name, *aliases):
            key = _norm(name)
            self.aliases[kind].setdefault(key, set()).add(permanent_id)

    def add_alias(self, kind: str, permanent_id: str, alias: str) -> None:
        if permanent_id not in self.canonical.get(kind, {}):
            raise IdentityError("cannot alias unknown identity")
        self.aliases[kind].setdefault(_norm(alias), set()).add(permanent_id)

    def resolve(self, kind: str, name: str) -> str:
        ids = self.aliases.get(kind, {}).get(_norm(name), set())
        if len(ids) != 1:
            raise IdentityError(f"identity unresolved/ambiguous kind={kind} name={name!r} candidates={sorted(ids)}")
        return next(iter(ids))

    def record_membership(self, player_id: str, team_id: str, start: str, end: str | None, move_type: str, role: str | None = None) -> None:
        if player_id not in self.canonical["player"] or team_id not in self.canonical["team"]:
            raise IdentityError("membership references unknown identity")
        if move_type not in {"permanent", "loan", "youth", "debut", "role_change"}:
            raise IdentityError("unsupported membership transition")
        s = _dt(start)
        e = None if end is None else _dt(end)
        if e is not None and e <= s:
            raise IdentityError("membership end must follow start")
        row = {"player_id": player_id, "team_id": team_id, "start": start, "end": end or "",
               "move_type": move_type, "role": role or ""}
        if row in self.memberships:
            raise IdentityError("duplicate membership event")
        self.memberships.append(row)

    def memberships_at(self, player_id: str, as_of: str) -> list[dict[str, str]]:
        co = _dt(as_of)
        out = []
        for row in self.memberships:
            if row["player_id"] != player_id:
                continue
            s = _dt(row["start"])
            e = None if not row["end"] else _dt(row["end"])
            if s <= co and (e is None or co < e):
                out.append(row)
        return sorted(out, key=lambda r: (_dt(r["start"]), r["team_id"], r["move_type"]))

    def team_at(self, player_id: str, as_of: str) -> str:
        active = self.memberships_at(player_id, as_of)
        teams = {r["team_id"] for r in active if r["move_type"] != "role_change"}
        if len(teams) != 1:
            raise IdentityError(f"membership unresolved/ambiguous player={player_id} as_of={as_of} teams={sorted(teams)}")
        return next(iter(teams))

    def validate_lineup_membership(self, player_ids: Iterable[str], team_id: str, as_of: str) -> None:
        seen: set[str] = set()
        for pid in player_ids:
            if pid in seen:
                raise IdentityError(f"duplicate player in lineup {pid}")
            seen.add(pid)
            if self.team_at(pid, as_of) != team_id:
                raise IdentityError(f"player/team membership mismatch player={pid} expected={team_id}")

    def snapshot_sha256(self) -> str:
        payload = {
            "canonical": self.canonical,
            "aliases": {k: {a: sorted(v) for a, v in sorted(m.items())} for k, m in sorted(self.aliases.items())},
            "memberships": sorted(self.memberships, key=lambda x: (x["player_id"], x["start"], x["team_id"], x["move_type"], x["role"])),
        }
        return _sha(payload)
