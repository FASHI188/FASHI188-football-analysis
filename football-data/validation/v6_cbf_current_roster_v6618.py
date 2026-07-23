#!/usr/bin/env python3
"""V6.6.18 ingest strict-current Bahia/Flamengo rosters from the official CBF 2026 Série A registry.

CBF's team page is a registration-history table, not a pure current-squad list. Its explicit columns
are Nome | Apelido | Clube Atual. Therefore this ingestion counts a player ONLY when `Clube Atual`
still resolves to the target club. Historical registrations whose current club differs are retained
in the audit counters but excluded from the strict-current roster.

One official CBF team page is the sole player-list source for each team. No cross-source, cross-match,
or historical-list union is allowed. >=18 unique current players are required. Research context only;
V5.0.1 formal probabilities and weights remain unchanged.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "team_current_roster_weekly"
OUT = ROOT / "manifests" / "v6_cbf_current_roster_v6618_status.json"
UA = "football-v6.6.18-cbf-current-roster/1.0"
MIN_PLAYERS = 18
MAX_PLAYERS = 60

TARGETS: dict[str, dict[str, Any]] = {
    "Bahia": {
        "cbf_team_id": "61377",
        "url": "https://www.cbf.com.br/futebol-brasileiro/times/campeonato-brasileiro/serie-a/2026/61377?tab=atletas",
        "current_club_aliases": {"bahia", "ec bahia", "esporte clube bahia"},
    },
    "Flamengo": {
        "cbf_team_id": "20016",
        "url": "https://www.cbf.com.br/futebol-brasileiro/times/campeonato-brasileiro/serie-a/2026/20016?tab=atletas",
        "current_club_aliases": {"flamengo", "cr flamengo", "clube de regatas do flamengo"},
    },
}


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def person_norm(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


class TableParser(HTMLParser):
    """Collect all HTML table rows as visible cell text without third-party dependencies."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_tr = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self.in_tr = True
            self.row = []
        elif tag in {"td", "th"} and self.in_tr:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            value = re.sub(r"\s+", " ", html.unescape(data)).strip()
            if value:
                self.cell_parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(" ".join(self.cell_parts).strip())
            self.in_cell = False
            self.cell_parts = []
        elif tag == "tr" and self.in_tr:
            if self.row:
                self.rows.append(self.row)
            self.in_tr = False
            self.row = []


def fetch(url: str) -> tuple[str, datetime, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    observed = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        markup = raw.decode(charset, errors="strict")
    except Exception:
        markup = raw.decode("utf-8", errors="replace")
    return markup, observed, charset


def extract_registration_rows(markup: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    parser = TableParser(); parser.feed(markup); parser.close()
    header_index = None
    header = None
    for i, row in enumerate(parser.rows):
        normalized = [norm(cell) for cell in row]
        if len(normalized) >= 3 and normalized[0] == "nome" and normalized[1] == "apelido" and normalized[2] == "clube atual":
            header_index = i; header = row; break
    if header_index is None:
        return [], {"table_rows_seen": len(parser.rows), "reason": "cbf_nome_apelido_clube_atual_header_missing"}
    out=[]
    for row in parser.rows[header_index + 1:]:
        if len(row) < 3:
            continue
        nome, apelido, clube_atual = row[0].strip(), row[1].strip(), row[2].strip()
        # Stop/ignore unrelated tables and blank rows; require at least a name and current-club cell.
        if not nome or not clube_atual:
            continue
        if norm(nome) in {"nome", "competicao", "ano"}:
            continue
        out.append({"nome": nome, "apelido": apelido, "clube_atual": clube_atual})
    return out, {"table_rows_seen": len(parser.rows), "header": header, "registration_rows_parsed": len(out)}


def current_players(rows: list[dict[str, str]], aliases: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target = {norm(v) for v in aliases}
    kept=[]; excluded=[]; seen=set(); duplicate_names=[]
    for row in rows:
        current = norm(row["clube_atual"])
        name = row["nome"].strip()
        key = person_norm(name)
        if current not in target:
            excluded.append({"player_name": name, "nickname": row["apelido"], "clube_atual": row["clube_atual"]})
            continue
        if not key:
            continue
        if key in seen:
            duplicate_names.append(name)
            continue
        seen.add(key)
        kept.append({
            "player_name": name,
            "nickname": row["apelido"] or None,
            "positions": [],
            "shirt_number": None,
            "squad_status": "cbf-current-club-registration",
            "roster_source": "CBF official 2026 Serie A registration table filtered by Clube Atual",
        })
    return kept, {
        "current_rows_kept": len(kept),
        "historical_or_transferred_rows_excluded": len(excluded),
        "duplicate_current_names_collapsed": len(duplicate_names),
        "excluded_current_club_examples": excluded[:10],
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    EVIDENCE.mkdir(parents=True, exist_ok=True); OUT.parent.mkdir(parents=True, exist_ok=True)
    evidence_records=[]; audit=[]; source_times=[]
    for team_name, cfg in TARGETS.items():
        row={"team_name": team_name, "source_url": cfg["url"], "cbf_team_id": cfg["cbf_team_id"], "status": "FAIL_CLOSED"}
        try:
            markup, observed, charset = fetch(cfg["url"]); source_times.append(observed)
            registrations, parse_audit = extract_registration_rows(markup)
            players, filter_audit = current_players(registrations, set(cfg["current_club_aliases"]))
            row.update({"source_observed_at_utc": observed.isoformat(), "http_charset": charset, "parse": parse_audit, "filter": filter_audit, "current_player_count": len(players)})
            valid = MIN_PLAYERS <= len(players) <= MAX_PLAYERS and len({person_norm(p["player_name"]) for p in players}) == len(players)
            if valid:
                evidence_records.append({
                    "schema_version": "V6.6.9-current-roster-overlay-r1",
                    "competition_id": "BRA_SerieA",
                    "team_name": team_name,
                    "observed_at_utc": observed.isoformat(),
                    "roster_semantics": "CURRENT_REGISTERED_SQUAD",
                    "players": players,
                    "sources": [{
                        "source_name": "CBF official 2026 Campeonato Brasileiro Série A team registration page",
                        "source_url": cfg["url"],
                        "source_tier": "tier_1_official",
                        "provider_group": "cbf_official",
                        "source_observed_at_utc": observed.isoformat(),
                        "source_role": "current_registered_players_filtered_by_clube_atual",
                    }],
                    "source_metadata": {
                        "cbf_team_id": cfg["cbf_team_id"],
                        "competition": "Campeonato Brasileiro - Série A",
                        "year": 2026,
                        "table_semantics": "registration_history_with_current_club_column",
                        "current_club_filter_required": True,
                        "parse": parse_audit,
                        "filter": filter_audit,
                    },
                    "governance": {
                        "current_at_observation_time": True,
                        "single_source_player_list": True,
                        "single_endpoint_player_list": True,
                        "cross_source_union": False,
                        "historical_registration_rows_excluded_when_clube_atual_differs": True,
                        "unicode_safe_person_identity": True,
                        "research_context_only": True,
                        "formal_probability_use": False,
                    },
                })
                row["status"] = "PASS_STRICT_CURRENT"
            else:
                row["reason"] = "current_club_filtered_player_count_outside_strict_gate_or_duplicate_identity"
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        audit.append(row)

    completed=max(source_times) if source_times else datetime.now(timezone.utc).replace(microsecond=0)
    evidence_path=None
    if evidence_records:
        stamp=completed.strftime("%Y%m%dT%H%M%SZ")
        evidence_path=EVIDENCE/f"cbf_current_rosters__{stamp}.json"
        evidence_path.write_text(json.dumps({
            "schema_version":"V6.6.18-cbf-current-roster-weekly-aggregate-r1",
            "observed_at_utc":completed.isoformat(),
            "records":evidence_records,
            "governance":{"official_cbf_source_only":True,"clube_atual_filter_mandatory":True,"historical_rows_not_counted_as_current":True,"research_context_only":True,"formal_probability_use":False}
        },ensure_ascii=False,indent=2),encoding="utf-8")
    payload={
        "schema_version":"V6.6.18-cbf-current-roster-status-r1",
        "generated_at_utc":completed.isoformat(),
        "formal_current_version":"V5.0.1",
        "status":"PASS_COMPLETE" if len(evidence_records)==len(TARGETS) else "WARN_PARTIAL" if evidence_records else "FAIL_NO_VALID_ROSTERS",
        "target_count":len(TARGETS),
        "valid_current_roster_count":len(evidence_records),
        "evidence_path":str(evidence_path.relative_to(ROOT)) if evidence_path else None,
        "audit":audit,
        "governance":{"official_source_only":True,"cbf_registration_history_not_assumed_current":True,"clube_atual_filter_required":True,"minimum_unique_current_players":MIN_PLAYERS,"no_cross_source_union":True,"research_context_only":True,"formal_probability_change":False,"formal_weight_change":False}
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if evidence_records else 2


if __name__=="__main__": raise SystemExit(main())