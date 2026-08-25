#!/usr/bin/env python3
from __future__ import annotations

import football3_weekly_12_league_personnel_scan_v2 as core

# Root-cause hardening for official membership surfaces.
# Portugal: use a current 2026/27 Liga Portugal team page whose classification
# exposes all 18 Liga Portugal Betclic clubs in machine-readable text.
core.LEAGUES["POR_PrimeiraLiga"]["membership"] = [
    "https://www.ligaportugal.pt/team/157/fc-porto/20262027"
]

# K League official competition pages render club membership primarily with
# Korean short names. Keep personnel fingerprints on the stricter full aliases,
# but allow these official short forms for membership detection.
KOR_MEMBERSHIP_SHORT = {
    "Gangwon FC": ["강원"],
    "Gwangju FC": ["광주"],
    "Gimcheon Sangmu": ["김천"],
    "Daejeon Hana Citizen": ["대전"],
    "Bucheon FC 1995": ["부천"],
    "FC Seoul": ["서울"],
    "FC Anyang": ["안양"],
    "Ulsan HD": ["울산"],
    "Incheon United": ["인천"],
    "Jeonbuk Hyundai Motors": ["전북"],
    "Jeju SK": ["제주"],
    "Pohang Steelers": ["포항"],
}

_base_contains = core.contains


def membership_compatible_contains(text: str, team: str) -> bool:
    if _base_contains(text, team):
        return True
    aliases = KOR_MEMBERSHIP_SHORT.get(team, [])
    low = text.casefold()
    return any(alias.casefold() in low for alias in aliases)


core.contains = membership_compatible_contains


if __name__ == "__main__":
    raise SystemExit(core.main())
