#!/usr/bin/env python3
from __future__ import annotations

import football3_weekly_12_league_personnel_scan_v2 as core

# Root-cause hardening for official membership surfaces.
# Portugal: the weekly official registration article is the authoritative current
# transfer-window surface and lists all participating Liga Portugal Betclic SADs.
core.LEAGUES["POR_PrimeiraLiga"]["membership"] = [
    "https://www.ligaportugal.pt/noticias/28214/inscricoes-oficiais-liga-portugal-betclic-%28atualizacao-14-de-agosto%29"
]

# Official Portuguese registration headings often use legal SAD names rather
# than the public competition short name. These aliases are membership-only.
POR_MEMBERSHIP_SHORT = {
    "FC Porto": ["FC Porto", "Futebol Clube do Porto"],
    "FC Arouca": ["FC Arouca", "Arouca"],
    "Gil Vicente FC": ["Gil Vicente FC", "Gil Vicente"],
    "Marítimo M.": ["Marítimo M.", "Marítimo da Madeira", "Marítimo"],
    "Académico": ["Académico de Viseu", "Académico"],
    "CD Nacional": ["CD Nacional", "Nacional"],
    "Estrela Amadora": ["Estrela Amadora", "CFEA", "Club Football Estrela"],
    "Moreirense FC": ["Moreirense FC", "Moreirense"],
    "Santa Clara": ["Santa Clara Açores", "Santa Clara"],
    "SC Braga": ["SC Braga", "Sporting Clube de Braga"],
    "SL Benfica": ["SL Benfica", "Sport Lisboa e Benfica"],
    "Sporting CP": ["Sporting CP", "Sporting Clube de Portugal"],
    "Estoril Praia": ["Estoril Praia"],
    "FC Famalicão": ["FC Famalicão", "Famalicão"],
    "Casa Pia AC": ["Casa Pia AC", "Casa Pia"],
    "Rio Ave FC": ["Rio Ave FC", "Rio Ave"],
    "Vitória SC": ["Vitória SC", "Vitória Sport Clube"],
    "FC Alverca": ["FC Alverca", "Futebol Clube Alverca", "Alverca"],
}

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
    candidates = POR_MEMBERSHIP_SHORT.get(team, []) + KOR_MEMBERSHIP_SHORT.get(team, [])
    low = text.casefold()
    return any(alias.casefold() in low for alias in candidates)


core.contains = membership_compatible_contains


if __name__ == "__main__":
    raise SystemExit(core.main())
