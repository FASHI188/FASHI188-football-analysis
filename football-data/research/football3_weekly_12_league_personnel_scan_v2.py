#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import sync_playwright

SCHEMA = "football3_weekly_12_league_personnel_scan_v2"
EXPECTED = {
    "ENG_PremierLeague": 20,
    "ESP_LaLiga": 20,
    "GER_Bundesliga": 18,
    "ITA_SerieA": 20,
    "FRA_Ligue1": 18,
    "SWE_Allsvenskan": 16,
    "NED_Eredivisie": 18,
    "BRA_SerieA": 20,
    "JPN_J1": 20,
    "POR_PrimeiraLiga": 18,
    "KSA_SaudiProLeague": 18,
    "KOR_KLeague1": 12,
}

LEAGUES = {
    "ENG_PremierLeague": {
        "teams": ["AFC Bournemouth","Arsenal","Aston Villa","Brentford","Brighton & Hove Albion","Chelsea","Coventry City","Crystal Palace","Everton","Fulham","Hull City","Ipswich Town","Leeds United","Liverpool","Manchester City","Manchester United","Newcastle United","Nottingham Forest","Sunderland","Tottenham Hotspur"],
        "membership": ["https://www.premierleague.com/en/news/4365156/new-to-the-premier-league-heres-all-you-need-to-know"],
        "personnel": ["https://www.premierleague.com/en/transfers/2026-27/summer"],
        "hosts": ["www.premierleague.com"],
    },
    "ESP_LaLiga": {
        "teams": ["Athletic Club","Atlético de Madrid","CA Osasuna","Celta","Deportivo Alavés","Elche CF","FC Barcelona","Getafe CF","Levante UD","Málaga CF","R. Racing Club","Rayo Vallecano","RC Deportivo","RCD Espanyol de Barcelona","Real Betis","Real Madrid","Real Sociedad","Sevilla FC","Valencia CF","Villarreal CF"],
        "membership": ["https://www.laliga.com/laliga-easports/clubes"],
        "personnel": ["https://www.laliga.com/en-GB/transfers/laliga-easports"],
        "hosts": ["www.laliga.com"],
    },
    "GER_Bundesliga": {
        "teams": ["FC Augsburg","1. FC Union Berlin","SV Werder Bremen","Borussia Dortmund","SV Elversberg","Eintracht Frankfurt","Sport-Club Freiburg","Hamburger SV","TSG Hoffenheim","1. FC Köln","RB Leipzig","Bayer 04 Leverkusen","1. FSV Mainz 05","Borussia Mönchengladbach","FC Bayern München","SC Paderborn 07","FC Schalke 04","VfB Stuttgart"],
        "membership": ["https://www.bundesliga.com/de/bundesliga/clubs"],
        "personnel": ["https://www.bundesliga.com/en/bundesliga/news/official-bundesliga-transfer-centre-summer-2026-37051/"],
        "hosts": ["www.bundesliga.com"],
    },
    "ITA_SerieA": {
        "teams": ["Atalanta","Bologna","Cagliari","Como","Fiorentina","Frosinone","Genoa","Inter","Juventus","Lazio","Lecce","Milan","Monza","Napoli","Parma","Roma","Sassuolo","Torino","Udinese","Venezia"],
        "membership": ["https://en.legaseriea.it/serie-a/news/looking-forward-to-the-2026-27-serie-a-fixture-list"],
        "personnel": ["https://en.legaseriea.it/serie-a/calciomercato"],
        "hosts": ["en.legaseriea.it"],
    },
    "FRA_Ligue1": {
        "teams": ["Angers SCO","AJ Auxerre","Stade Brestois 29","Havre AC","Le Mans FC","RC Lens","FC Lorient","LOSC","Olympique Lyonnais","Olympique de Marseille","AS Monaco","OGC Nice","Paris FC","Paris Saint-Germain","Stade Rennais F.C.","RC Strasbourg Alsace","Toulouse FC","ESTAC Troyes"],
        "membership": ["https://ligue1.com/fr/articles/l1_article_5293-les-dates-de-reprise-des-clubs-de-l1-2627"],
        "personnel": ["https://ligue1.com/fr/articles/l1_article_5130-","https://ligue1.com/fr/articles"],
        "hosts": ["ligue1.com"],
        "discover_transfer": True,
    },
    "SWE_Allsvenskan": {
        "teams": ["AIK","Halmstads BK","Hammarby","Mjällby AIF","GAIS","Djurgården","Örgryte IS","Malmö FF","BK Häcken","IF Brommapojkarna","IF Elfsborg","IFK Göteborg","Degerfors IF","IK Sirius","Kalmar FF","Västerås SK"],
        "membership": ["https://allsvenskan.se/nyheter/spelordningen-klar-for-2026/"],
        "personnel": ["https://allsvenskan.se/nyheter?nyheter=allt","https://allsvenskan.se/nyheter/vinterns-alla-overgangar-i-allsvenskan/"],
        "hosts": ["allsvenskan.se"],
        "discover_transfer": True,
    },
    "NED_Eredivisie": {
        "teams": ["ADO Den Haag","Ajax","AZ","Excelsior Rotterdam","FC Groningen","FC Twente","FC Utrecht","Feyenoord","Fortuna Sittard","Go Ahead Eagles","N.E.C. Nijmegen","PEC Zwolle","PSV","SC Cambuur","sc Heerenveen","Sparta Rotterdam","Telstar","Willem II"],
        "membership": ["https://eredivisie.nl/competitie/clubs/"],
        "personnel": ["https://eredivisie.nl/nieuws/"],
        "hosts": ["eredivisie.nl"],
        "discover_transfer": True,
    },
    "BRA_SerieA": {
        "teams": ["Athletico Paranaense","Atlético Mineiro","Bahia","Botafogo","Chapecoense","Corinthians","Coritiba","Cruzeiro","Flamengo","Fluminense","Grêmio","Internacional","Mirassol","Palmeiras","Red Bull Bragantino","Remo","Santos FC","São Paulo","Vasco da Gama","Vitória"],
        "membership": ["https://www.cbf.com.br/futebol-brasileiro/times/campeonato-brasileiro/serie-a/2026"],
        "personnel": [],
        "hosts": ["www.cbf.com.br"],
        "discover_team_profiles": True,
    },
    "JPN_J1": {
        "teams": ["FC Machida Zelvia","Kashiwa Reysol","Kashima Antlers","Sanfrecce Hiroshima","Yokohama F･Marinos","Cerezo Osaka","Mito Hollyhock","Vissel Kobe","Fagiano Okayama","Gamba Osaka","FC TOKYO","Avispa Fukuoka","Kawasaki Frontale","Nagoya Grampus","Shimizu S-Pulse","V-Varen Nagasaki","Tokyo Verdy","Kyoto Sanga F.C.","Urawa Reds","JEF United Chiba"],
        "membership": ["https://www.jleague.co/en/"],
        "personnel": ["https://www.jleague.co/en/"],
        "hosts": ["www.jleague.co","www.jleague.jp"],
        "discover_team_profiles": True,
        "discover_transfer": True,
    },
    "POR_PrimeiraLiga": {
        "teams": ["FC Porto","FC Arouca","Gil Vicente FC","Marítimo M.","Académico","CD Nacional","Estrela Amadora","Moreirense FC","Santa Clara","SC Braga","SL Benfica","Sporting CP","Estoril Praia","FC Famalicão","Casa Pia AC","Rio Ave FC","Vitória SC","FC Alverca"],
        "membership": ["https://www.ligaportugal.pt/team/278/sl-benfica/20262027"],
        "personnel": ["https://www.ligaportugal.pt/noticias/28214/inscricoes-oficiais-liga-portugal-betclic-%28atualizacao-14-de-agosto%29","https://www.ligaportugal.pt/news?tags=_season_20262027"],
        "hosts": ["www.ligaportugal.pt"],
        "discover_transfer": True,
    },
    "KSA_SaudiProLeague": {
        "teams": ["Al Nassr","Al Hilal","Al Ahli","Al Qadsiah","Al Ittihad","Al Taawoun","Al Ettifaq","Al Fateh","Al Khaleej","Al Shabab","NEOM Sports Club","Al Hazem","Al Fayha","Al Kholood","Al Riyadh","Abha","Al Faisaly","Diriyah FC"],
        "membership": ["https://www.spl.com.sa/en/news/spl-announces-2026-27-rsl-fixture-schedule"],
        "personnel": ["https://www1.spl.com.sa/en/news/2026-27-summer-transfer-watch"],
        "hosts": ["www.spl.com.sa","www1.spl.com.sa","spl.com.sa"],
    },
    "KOR_KLeague1": {
        "teams": ["Gangwon FC","Gwangju FC","Gimcheon Sangmu","Daejeon Hana Citizen","Bucheon FC 1995","FC Seoul","FC Anyang","Ulsan HD","Incheon United","Jeonbuk Hyundai Motors","Jeju SK","Pohang Steelers"],
        "membership": ["https://www.kleague.com/about/competition.do"],
        "personnel": ["https://www.kleague.com/record/player.do"],
        "hosts": ["www.kleague.com"],
    },
}

ALIASES = {
    "AFC Bournemouth": ["Bournemouth"], "Brighton & Hove Albion": ["Brighton"], "Hull City": ["Hull"], "Ipswich Town": ["Ipswich"], "Leeds United": ["Leeds"],
    "Manchester United": ["Manchester United","Man Utd"], "Newcastle United": ["Newcastle"], "Nottingham Forest": ["Nottingham Forest","Nott'm Forest"], "Tottenham Hotspur": ["Tottenham","Spurs"],
    "Atlético de Madrid": ["Atlético de Madrid","Atletico de Madrid"], "Celta": ["Celta","Celta Vigo"], "Deportivo Alavés": ["Deportivo Alavés","Alavés"], "R. Racing Club": ["R. Racing Club","Racing Club","Real Racing Club"],
    "RC Deportivo": ["RC Deportivo","Deportivo"], "RCD Espanyol de Barcelona": ["RCD Espanyol de Barcelona","Espanyol"], "FC Bayern München": ["FC Bayern München","Bayern München","Bayern Munich"],
    "Sport-Club Freiburg": ["Sport-Club Freiburg","SC Freiburg","Freiburg"], "Hamburger SV": ["Hamburger SV","Hamburg"], "1. FC Köln": ["1. FC Köln","Köln","Cologne"],
    "Bayer 04 Leverkusen": ["Bayer 04 Leverkusen","Bayer Leverkusen","Leverkusen"], "Borussia Mönchengladbach": ["Borussia Mönchengladbach","M'gladbach","Gladbach"],
    "SC Paderborn 07": ["SC Paderborn 07","Paderborn"], "FC Schalke 04": ["FC Schalke 04","Schalke"], "Inter": ["Inter","Internazionale"], "Milan": ["Milan","AC Milan"], "Roma": ["Roma","AS Roma"],
    "Havre AC": ["Havre AC","Le Havre"], "LOSC": ["LOSC","Lille"], "Olympique Lyonnais": ["Olympique Lyonnais","Lyon"], "Olympique de Marseille": ["Olympique de Marseille","Marseille"],
    "Paris Saint-Germain": ["Paris Saint-Germain","PSG"], "Stade Rennais F.C.": ["Stade Rennais","Rennes"], "RC Strasbourg Alsace": ["RC Strasbourg Alsace","Strasbourg"], "ESTAC Troyes": ["ESTAC Troyes","Troyes"],
    "Djurgården": ["Djurgården","Djurgårdens IF"], "IF Brommapojkarna": ["IF Brommapojkarna","Brommapojkarna","BP"], "Örgryte IS": ["Örgryte IS","ÖIS"],
    "Athletico Paranaense": ["Athletico Paranaense","Athletico Paranaense - PR"], "Atlético Mineiro": ["Atlético Mineiro","Atlético Mineiro - MG"], "Bahia": ["Bahia","Bahia - BA"], "Botafogo": ["Botafogo","Botafogo - RJ"],
    "Chapecoense": ["Chapecoense","Chapecoense - SC"], "Corinthians": ["Corinthians","Corinthians - SP"], "Coritiba": ["Coritiba","Coritiba SAF","Coritiba SAF - PR"], "Cruzeiro": ["Cruzeiro","Cruzeiro - MG"],
    "Flamengo": ["Flamengo","Flamengo - RJ"], "Fluminense": ["Fluminense","Fluminense - RJ"], "Grêmio": ["Grêmio","Grêmio - RS"], "Internacional": ["Internacional","Internacional - RS"],
    "Mirassol": ["Mirassol","Mirassol - SP"], "Palmeiras": ["Palmeiras","Palmeiras - SP"], "Red Bull Bragantino": ["Red Bull Bragantino","Bragantino"], "Remo": ["Remo","Remo - PA"],
    "Santos FC": ["Santos FC","Santos FC - SP","Santos"], "São Paulo": ["São Paulo","São Paulo - SP"], "Vasco da Gama": ["Vasco da Gama","Vasco da Gama Saf","Vasco"], "Vitória": ["Vitória","Vitória - BA"],
    "Yokohama F･Marinos": ["Yokohama F･Marinos","Yokohama F.Marinos","Yokohama F Marinos"], "FC TOKYO": ["FC TOKYO","FC Tokyo"],
    "Marítimo M.": ["Marítimo M.","Marítimo"], "Académico": ["Académico","Académico de Viseu"], "Estrela Amadora": ["Estrela Amadora","Estrela da Amadora"],
    "NEOM Sports Club": ["NEOM Sports Club","NEOM","Neom S.C."], "Diriyah FC": ["Diriyah FC","Diriyah Club"],
    "Gangwon FC": ["Gangwon FC","강원 FC","GANGWON"], "Gwangju FC": ["Gwangju FC","광주 FC","GWANGJU"], "Gimcheon Sangmu": ["Gimcheon Sangmu","김천상무","GIMCHEON"],
    "Daejeon Hana Citizen": ["Daejeon Hana Citizen","대전하나시티즌","DAEJEON HANA"], "Bucheon FC 1995": ["Bucheon FC 1995","부천 FC","BUCHEON"], "FC Seoul": ["FC Seoul","FC 서울","SEOUL"],
    "FC Anyang": ["FC Anyang","FC 안양","ANYANG"], "Ulsan HD": ["Ulsan HD","울산 HD","ULSAN"], "Incheon United": ["Incheon United","인천유나이티드","INCHEON"],
    "Jeonbuk Hyundai Motors": ["Jeonbuk Hyundai Motors","전북현대","JEONBUK"], "Jeju SK": ["Jeju SK","제주 SK","JEJU"], "Pohang Steelers": ["Pohang Steelers","포항스틸러스","POHANG"],
}

TRANSFER_RE = re.compile(r"(transfer|transfert|mercato|overgang|övergång|värv|inscri|registration|signing|signed|loan|versterk|vertrek|aankoop)", re.I)
LOAD_RE = re.compile(r"(load more|show more|view more|voir plus|mostrar más|mehr anzeigen|carica altro|meer laden|visa fler)", re.I)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip()


def aliases(team: str) -> list[str]:
    return ALIASES.get(team, [team])


def contains(text: str, team: str) -> bool:
    low = text.casefold()
    return any(a.casefold() in low for a in aliases(team))


def snippets(text: str, team: str, radius: int = 900) -> list[str]:
    low = text.casefold()
    out: list[str] = []
    for alias in aliases(team):
        start = 0
        for _ in range(3):
            pos = low.find(alias.casefold(), start)
            if pos < 0:
                break
            out.append(text[max(0, pos-radius):min(len(text), pos+len(alias)+radius)])
            start = pos + len(alias)
    return sorted(set(out))


def host_allowed(url: str, hosts: list[str]) -> bool:
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname:
        return False
    h = p.hostname.casefold()
    return any(h == x.casefold() or h.endswith("." + x.casefold().lstrip("www.")) for x in hosts)


def expand(page) -> None:
    for _ in range(4):
        try:
            buttons = page.locator("button")
            for i in range(min(buttons.count(), 80)):
                b = buttons.nth(i)
                try:
                    label = norm(b.inner_text(timeout=150))
                except Exception:
                    continue
                if LOAD_RE.search(label):
                    try:
                        b.click(timeout=500)
                    except Exception:
                        pass
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(250)
        except Exception:
            pass


def render(browser, league: str, role: str, url: str, hosts: list[str]) -> dict:
    if not host_allowed(url, hosts):
        return {"league":league,"role":role,"url":url,"final_url":url,"title":"","text":"","sha256":hashlib.sha256(b"").hexdigest(),"status":"DENIED","error":"source host denied"}
    page = browser.new_page(viewport={"width":1440,"height":1000})
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(500)
        expand(page)
        if not host_allowed(page.url, hosts):
            raise RuntimeError(f"redirect host denied: {page.url}")
        text = norm(page.locator("body").inner_text(timeout=10000))
        if resp and resp.status >= 400:
            raise RuntimeError(f"http={resp.status}")
        if len(text) < 80:
            raise RuntimeError(f"short={len(text)}")
        return {"league":league,"role":role,"url":url,"final_url":page.url,"title":norm(page.title()),"text":text,"sha256":hashlib.sha256(text.encode()).hexdigest(),"status":"OK","error":None}
    except Exception as exc:
        return {"league":league,"role":role,"url":url,"final_url":page.url,"title":"","text":"","sha256":hashlib.sha256(b"").hexdigest(),"status":"ERROR","error":str(exc)}
    finally:
        page.close()


def discover(browser, league: str, cfg: dict, origin_url: str, mode: str) -> list[tuple[str,str]]:
    if not host_allowed(origin_url, cfg["hosts"]):
        return []
    page = browser.new_page(viewport={"width":1440,"height":1000})
    found: list[tuple[str,str]] = []
    try:
        page.goto(origin_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(400)
        expand(page)
        links = page.locator("a")
        for i in range(min(links.count(), 800)):
            a = links.nth(i)
            try:
                href = a.get_attribute("href") or ""
                label = norm(a.inner_text(timeout=120))
            except Exception:
                continue
            if not href:
                continue
            full = urljoin(page.url, href)
            if not host_allowed(full, cfg["hosts"]):
                continue
            marker = label + " " + full
            if mode == "transfer":
                if not TRANSFER_RE.search(marker):
                    continue
                key = ("personnel_discovered", full)
            else:
                team = next((t for t in cfg["teams"] if contains(label, t)), None)
                if not team:
                    continue
                key = (f"team_profile::{team}", full)
            if key not in found:
                found.append(key)
            if mode == "transfer" and len(found) >= 24:
                break
            if mode == "team" and len(found) >= len(cfg["teams"]):
                break
    except Exception:
        pass
    finally:
        page.close()
    return found


def validate_config() -> dict:
    assert set(LEAGUES) == set(EXPECTED)
    assert sum(EXPECTED.values()) == 218
    all_keys: list[tuple[str,str]] = []
    for league, count in EXPECTED.items():
        teams = LEAGUES[league]["teams"]
        assert len(teams) == count, (league, len(teams), count)
        assert len(set(teams)) == count, league
        assert LEAGUES[league]["membership"], league
        for url in LEAGUES[league]["membership"] + LEAGUES[league].get("personnel", []):
            assert host_allowed(url, LEAGUES[league]["hosts"]), (league, url)
        all_keys += [(league, t) for t in teams]
    assert len(all_keys) == 218
    assert len(set(all_keys)) == 218
    return {"league_count":12,"club_count":218,"counts":EXPECTED}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("football-data/research/evidence/weekly_12_league_personnel_scan_v2"))
    parser.add_argument("--cache-file", type=Path, default=Path(".football3-cache/weekly-12-league-personnel/current_snapshot.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    cfg_check = validate_config()
    if args.self_test:
        print(json.dumps(cfg_check, sort_keys=True))
        return 0

    rows: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for league, cfg in LEAGUES.items():
                for url in cfg["membership"]:
                    rows.append(render(browser, league, "membership", url, cfg["hosts"]))
                for url in cfg.get("personnel", []):
                    rows.append(render(browser, league, "personnel", url, cfg["hosts"]))
                if cfg.get("discover_transfer"):
                    origins = cfg.get("personnel", []) or cfg["membership"]
                    seen = set()
                    for origin in origins:
                        for role, url in discover(browser, league, cfg, origin, "transfer"):
                            if url in seen:
                                continue
                            seen.add(url)
                            rows.append(render(browser, league, role, url, cfg["hosts"]))
                if cfg.get("discover_team_profiles"):
                    seen = set()
                    for origin in cfg["membership"]:
                        for role, url in discover(browser, league, cfg, origin, "team"):
                            if url in seen:
                                continue
                            seen.add(url)
                            rows.append(render(browser, league, role, url, cfg["hosts"]))
        finally:
            browser.close()

    previous = {}
    if args.cache_file.exists():
        try:
            old = json.loads(args.cache_file.read_text())
            if old.get("schema") == SCHEMA:
                previous = {(x["league"], x["team"]): x for x in old.get("teams", [])}
        except Exception:
            previous = {}

    teams_out: list[dict] = []
    membership_missing: list[dict] = []
    changed: list[dict] = []
    personnel_mentioned = 0
    for league, cfg in LEAGUES.items():
        membership_text = " ".join(r["text"] for r in rows if r["league"] == league and r["role"] == "membership" and r["status"] == "OK")
        general_personnel = [r for r in rows if r["league"] == league and r["role"].startswith("personnel") and r["status"] == "OK"]
        for team in cfg["teams"]:
            membership_ok = contains(membership_text, team)
            if not membership_ok:
                membership_missing.append({"league":league,"team":team})
            relevant = list(general_personnel)
            relevant += [r for r in rows if r["league"] == league and r["role"] == f"team_profile::{team}" and r["status"] == "OK"]
            snips: list[str] = []
            urls: list[str] = []
            for src in relevant:
                found = snippets(src["text"], team)
                if found:
                    snips += found
                    urls.append(src["final_url"])
            fp = hashlib.sha256("\n".join(sorted(set(snips))).encode()).hexdigest()
            mentioned = bool(snips)
            personnel_mentioned += int(mentioned)
            row = {
                "league":league,"team":team,"membership_observed":membership_ok,
                "personnel_mentioned":mentioned,"personnel_fingerprint_sha256":fp,
                "personnel_evidence_urls":sorted(set(urls)),
            }
            prior = previous.get((league, team))
            if prior and prior.get("personnel_fingerprint_sha256") != fp:
                changed.append({"league":league,"team":team})
            teams_out.append(row)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_ok = sum(r["status"] == "OK" for r in rows)
    status = "PASS_SCOPE_218_ZERO_LABEL_WEEKLY_PERSONNEL_SCAN" if not membership_missing else "PARTIAL_MEMBERSHIP_COVERAGE"
    snapshot = {
        "schema":SCHEMA,"project_id":"football3","observed_at_utc":now,"status":status,
        "league_count":12,"club_count":218,"league_club_counts":EXPECTED,
        "membership_observed_count":218-len(membership_missing),"membership_missing":membership_missing,
        "personnel_mentioned_count":personnel_mentioned,"changed_team_candidates":changed,
        "source_count":len(rows),"source_ok_count":source_ok,
        "sources":[{k:r[k] for k in ("league","role","url","final_url","title","sha256","status","error")} | {"char_count":len(r["text"])} for r in rows],
        "teams":teams_out,
        "real_labels_read":0,"real_match_rows_read":0,"training":False,"tuning":False,"real_scoring":False,
        "provider_api":False,"secret_access":False,"CURRENT_change":False,"formal_pit_eligible":False,"formal_weight":0.0,"merge":False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.joinpath("current_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    report = [
        "# Football3 weekly 12-league personnel scan","",
        f"- observed_at_utc: `{now}`",f"- status: `{status}`",f"- leagues: `12`",f"- clubs in scope: `218`",
        f"- membership observed: `{snapshot['membership_observed_count']}`",f"- personnel mentions: `{personnel_mentioned}`",
        f"- changed-team candidates vs previous: `{len(changed)}`",f"- official sources OK: `{source_ok}/{len(rows)}`","",
        "## League scope",
    ]
    report += [f"- {league}: {count}" for league, count in EXPECTED.items()]
    report += ["","## Changed-team candidates"]
    report += [f"- {x['league']}: {x['team']}" for x in changed] or ["- none / first baseline"]
    report += ["","## Boundaries","- real labels/results: 0","- training/tuning/real scoring: 0/0/0","- CURRENT changes: 0","- dynamic output: Artifact/cache only; no repository persistence"]
    args.output_dir.joinpath("scan_report.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"status":status,"leagues":12,"clubs":218,"membership_observed":snapshot["membership_observed_count"],"personnel_mentions":personnel_mentioned,"changed":len(changed),"sources_ok":source_ok,"sources":len(rows)}, sort_keys=True))
    return 0 if not membership_missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
