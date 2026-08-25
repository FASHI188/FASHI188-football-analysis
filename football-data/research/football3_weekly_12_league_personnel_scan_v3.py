#!/usr/bin/env python3
from __future__ import annotations

import football3_weekly_12_league_personnel_scan_v2 as core

# Root-cause hardening for official membership/personnel surfaces.
# Portugal: the official registration article lists the current participating
# Liga Portugal Betclic SADs using a mixture of public and legal club names.
core.LEAGUES["POR_PrimeiraLiga"]["membership"] = [
    "https://www.ligaportugal.pt/noticias/28214/inscricoes-oficiais-liga-portugal-betclic-%28atualizacao-14-de-agosto%29"
]

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

# Saudi Pro League: use the live 2026/27 official transfer-watch route. The
# legacy www1/news route returned 404 in Actions.
core.LEAGUES["KSA_SaudiProLeague"]["personnel"] = [
    "https://www.spl.com.sa/en/2026-27-summer-transfer-watch"
]
core.ALIASES.setdefault("NEOM Sports Club", ["NEOM Sports Club", "NEOM"])
for alias in ["NEOM SC", "Neom S.C."]:
    if alias not in core.ALIASES["NEOM Sports Club"]:
        core.ALIASES["NEOM Sports Club"].append(alias)

# K League official player index is filter-driven and does not expose all club
# roster labels in a static weekly scan. Use each official 2026 club roster page
# instead. The IDs below were verified against the current K League club pages.
KOR_CLUB_PAGES = {
    "Gangwon FC": "K21",
    "Gwangju FC": "K22",
    "Gimcheon Sangmu": "K35",
    "Daejeon Hana Citizen": "K10",
    "Bucheon FC 1995": "K26",
    "FC Seoul": "K09",
    "FC Anyang": "K27",
    "Ulsan HD": "K01",
    "Incheon United": "K18",
    "Jeonbuk Hyundai Motors": "K05",
    "Jeju SK": "K04",
    "Pohang Steelers": "K03",
}
core.LEAGUES["KOR_KLeague1"]["personnel"] = [
    f"https://www.kleague.com/club/club.do?teamId={team_id}"
    for team_id in KOR_CLUB_PAGES.values()
]

KOR_MEMBERSHIP_SHORT = {
    "Gangwon FC": ["강원", "강원 FC"],
    "Gwangju FC": ["광주", "광주 FC"],
    "Gimcheon Sangmu": ["김천", "김천상무", "Gimcheon Sangmu Football Club"],
    "Daejeon Hana Citizen": ["대전", "대전하나시티즌", "DAEJEON HANA"],
    "Bucheon FC 1995": ["부천", "부천 FC", "BUCHEON"],
    "FC Seoul": ["서울", "FC 서울", "SEOUL"],
    "FC Anyang": ["안양", "FC 안양", "ANYANG"],
    "Ulsan HD": ["울산", "울산 HD", "ULSAN"],
    "Incheon United": ["인천", "인천유나이티드", "INCHEON"],
    "Jeonbuk Hyundai Motors": ["전북", "전북현대", "JEONBUK"],
    "Jeju SK": ["제주", "제주 SK", "JEJU"],
    "Pohang Steelers": ["포항", "포항스틸러스", "POHANG"],
}

# These aliases are safe for both membership proof and personnel snippets.
for team, extra_aliases in KOR_MEMBERSHIP_SHORT.items():
    current = core.ALIASES.setdefault(team, [team])
    for alias in extra_aliases:
        if alias not in current:
            current.append(alias)

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
