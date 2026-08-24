#!/usr/bin/env python3
"""football3 Stage4 zero-label roster provenance/as-of projector."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo


class RosterError(ValueError):
    pass


SCHEMA = "football3_roster_evidence_v1"
IDENTITY = "8bfa3c98c1895085d94c1e6f1b5b7b80b5d56d6e1eefd615b137126604d45b11"
ORDERED_IDENTITY = "499e462f6cce50f05e575a0232c73c8d8b5234ad06c70551cf49b09353bbca76"

# authority, timezone, hosts, path prefixes, query rule
POLICY = {
    "ENG_PremierLeague": ("Premier League","Europe/London",("www.premierleague.com",),("/en/clubs/","/en/players","/en/news/"),"NONE"),
    "ESP_LaLiga": ("LALIGA","Europe/Madrid",("www.laliga.com",),("/clubes/","/fichajes/","/noticias/"),"NONE"),
    "GER_Bundesliga": ("Bundesliga","Europe/Berlin",("www.bundesliga.com",),("/en/bundesliga/player","/en/bundesliga/news/"),"NONE"),
    "ITA_SerieA": ("Lega Serie A","Europe/Rome",("www.legaseriea.it",),("/serie-a/calciomercato","/serie-a/club/","/serie-a/news/"),"NONE"),
    "FRA_Ligue1": ("Ligue 1 / LFP Media","Europe/Paris",("ligue1.com","www.ligue1.com"),("/fr/articles/","/articles/","/clubs/"),"NONE"),
    "SWE_Allsvenskan": ("Allsvenskan","Europe/Stockholm",("allsvenskan.se","www.allsvenskan.se"),("/statistik/spelare","/nyheter/"),"NONE"),
    "NED_Eredivisie": ("Eredivisie","Europe/Amsterdam",("eredivisie.nl","www.eredivisie.nl"),("/competitie/","/clubs/","/nieuws/"),"NONE"),
    "BRA_SerieA": ("CBF","America/Sao_Paulo",("cbf.com.br","www.cbf.com.br","cbf-hml.cbf.com.br"),("/futebol-brasileiro/","/noticias/","/a-cbf/"),"NONE"),
    "JPN_J1": ("J.League","Asia/Tokyo",("www.jleague.jp","jleague.jp"),("/player/","/j1/special/transfer/","/news/"),"JYEAR"),
    "POR_PrimeiraLiga": ("Liga Portugal","Europe/Lisbon",("www.ligaportugal.pt","ligaportugal.pt"),("/noticias/","/pt/liga/"),"NONE"),
    "KSA_SaudiProLeague": ("Saudi Pro League","Asia/Riyadh",("www.spl.com.sa","spl.com.sa"),("/en/","/ar/"),"NONE"),
    "KOR_KLeague1": ("K League","Asia/Seoul",("www.kleague.com","kleague.com"),("/player.do","/news_view.do","/club/"),"KSEQ"),
}
MAP = frozenset({"EXACT","UNKNOWN","CONFLICT"})
PREC = frozenset({"SECOND","DATE","UNKNOWN"})
STATUS = frozenset({"VERIFIED","PARTIAL","UNKNOWN","CONFLICT"})
SCOPE = frozenset({"COMPLETE_ROSTER","PARTIAL_ROSTER","SINGLE_EVENT","MATCH_ONLY","UNKNOWN"})
TYPES = frozenset({"CURRENT_ROSTER_SNAPSHOT","REGISTRATION_EVENT","TRANSFER_IN","TRANSFER_OUT","CONTRACT_START","CONTRACT_END","LOAN_START","LOAN_END","MATCH_SQUAD_POINT","STAFF_EVENT"})
ENTER = frozenset({"REGISTRATION_EVENT","TRANSFER_IN","CONTRACT_START","LOAN_START"})
EXIT = frozenset({"TRANSFER_OUT","CONTRACT_END","LOAN_END"})
KEYS = frozenset({
    "schema","competition_id","season_id","source_timezone","team_official_id","team_canonical_id","team_mapping_status",
    "person_official_id","person_name","person_canonical_id","person_mapping_status","evidence_type","source_authority","source_url",
    "source_document_id","source_published_at","source_published_precision","source_observed_at","provable_available_at",
    "provable_available_precision","effective_from","effective_from_precision","effective_to","effective_to_precision","completeness_scope",
    "evidence_status","raw_source_payload_persisted","real_labels_read",
})


def _s(v: Any, f: str) -> str:
    if type(v) is not str or not v or v != v.strip():
        raise RosterError(f"{f}: plain non-empty string required")
    return v


def _enum(v: Any, allowed: frozenset[str], f: str) -> str:
    v = _s(v, f)
    if v not in allowed:
        raise RosterError(f"{f}: denied")
    return v


def _utc(v: Any, f: str) -> datetime:
    t = _s(v, f)
    if not t.endswith("Z"):
        raise RosterError(f"{f}: canonical UTC Z required")
    try:
        d = datetime.fromisoformat(t[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise RosterError(f"{f}: invalid") from exc
    if d.isoformat().replace("+00:00","Z") != t:
        raise RosterError(f"{f}: noncanonical")
    return d


def _bounds(v: Any, p: str, tz: str, f: str) -> tuple[datetime, datetime]:
    if p == "UNKNOWN" and v is None:
        return datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc)
    if p == "SECOND":
        d = _utc(v, f)
        return d, d
    if p == "DATE":
        try:
            day = date.fromisoformat(_s(v, f))
        except ValueError as exc:
            raise RosterError(f"{f}: bad date") from exc
        z = ZoneInfo(tz)
        lo = datetime.combine(day,time.min,tzinfo=z).astimezone(timezone.utc)
        hi = datetime.combine(day+timedelta(days=1),time.min,tzinfo=z).astimezone(timezone.utc)
        return lo, hi
    raise RosterError(f"{f}: precision/value mismatch")


def _url(v: Any, spec: tuple[Any,...]) -> str:
    u = _s(v,"source_url")
    p = urlsplit(u)
    if p.scheme != "https" or p.hostname not in spec[2] or p.netloc != p.hostname or p.username or p.password or p.fragment:
        raise RosterError("source_url: authority denied")
    if not any(p.path.startswith(x) for x in spec[3]):
        raise RosterError("source_url: path denied")
    try:
        q = parse_qsl(p.query,keep_blank_values=True,strict_parsing=True)
    except ValueError as exc:
        raise RosterError("source_url: query denied") from exc
    if spec[4] == "NONE" and q:
        raise RosterError("source_url: query denied")
    if spec[4] == "JYEAR" and q not in ([],[("year","2026")]):
        raise RosterError("source_url: query denied")
    if spec[4] == "KSEQ" and q:
        if len(q) != 1:
            raise RosterError("source_url: query denied")
        k,v = q[0]
        if k != "seq" or not v.isdigit() or len(v) > 10:
            raise RosterError("source_url: query denied")
    return u


def bind(identity: Any, ordered: Any) -> None:
    if _s(identity,"identity") != IDENTITY or _s(ordered,"ordered_identity") != ORDERED_IDENTITY:
        raise RosterError("fixture identity drift")


def canonical(raw: dict[str,Any]) -> dict[str,Any]:
    if type(raw) is not dict or set(raw) != set(KEYS):
        raise RosterError("evidence exact-key contract failed")
    if _s(raw["schema"],"schema") != SCHEMA:
        raise RosterError("schema mismatch")
    cid = _s(raw["competition_id"],"competition_id")
    if cid not in POLICY:
        raise RosterError("competition denied")
    spec = POLICY[cid]
    if _s(raw["source_authority"],"source_authority") != spec[0] or _s(raw["source_timezone"],"source_timezone") != spec[1]:
        raise RosterError("source authority/timezone mismatch")
    _url(raw["source_url"],spec)
    tm = _enum(raw["team_mapping_status"],MAP,"team_mapping_status")
    pm = _enum(raw["person_mapping_status"],MAP,"person_mapping_status")
    et = _enum(raw["evidence_type"],TYPES,"evidence_type")
    st = _enum(raw["evidence_status"],STATUS,"evidence_status")
    sc = _enum(raw["completeness_scope"],SCOPE,"completeness_scope")
    pp = _enum(raw["source_published_precision"],PREC,"source_published_precision")
    ap = _enum(raw["provable_available_precision"],PREC,"provable_available_precision")
    fp = _enum(raw["effective_from_precision"],PREC,"effective_from_precision")
    tp = _enum(raw["effective_to_precision"],PREC,"effective_to_precision")
    observed = _utc(raw["source_observed_at"],"source_observed_at")
    pub_lo, pub_hi = _bounds(raw["source_published_at"],pp,spec[1],"source_published_at")
    av_lo, av_hi = _bounds(raw["provable_available_at"],ap,spec[1],"provable_available_at")
    from_lo, from_hi = _bounds(raw["effective_from"],fp,spec[1],"effective_from")
    to_lo, to_hi = _bounds(raw["effective_to"],tp,spec[1],"effective_to")
    if raw["raw_source_payload_persisted"] is not False or type(raw["real_labels_read"]) is not int or raw["real_labels_read"] != 0:
        raise RosterError("zero-label boundary failed")
    if raw["provable_available_at"] is None:
        if ap != "UNKNOWN":
            raise RosterError("availability precision mismatch")
    elif ap == "UNKNOWN":
        raise RosterError("availability precision mismatch")
    if raw["source_published_at"] is None:
        if pp != "UNKNOWN":
            raise RosterError("published precision mismatch")
    elif pp == "UNKNOWN":
        raise RosterError("published precision mismatch")
    # Bitemporal chronology is conservative: a proof cannot predate publication,
    # cannot postdate observation, and known effective intervals cannot invert.
    if raw["source_published_at"] is not None and pub_lo > observed:
        raise RosterError("publication occurs after observation")
    if raw["provable_available_at"] is not None:
        if av_hi > observed:
            raise RosterError("availability occurs after observation")
        if raw["source_published_at"] is not None and av_hi < pub_hi:
            raise RosterError("availability predates publication proof")
    if raw["effective_from"] is not None and raw["effective_to"] is not None and from_hi > to_lo:
        raise RosterError("effective interval inverted or ambiguous")
    # Mutable current roster can start only at observation; never backfill.
    if et == "CURRENT_ROSTER_SNAPSHOT":
        ok_scope = sc in {"COMPLETE_ROSTER","PARTIAL_ROSTER"}
        if raw["effective_from"] != raw["source_observed_at"] or fp != "SECOND" or raw["effective_to"] is not None or tp != "UNKNOWN" or not ok_scope:
            raise RosterError("current roster backfill denied")
        if raw["provable_available_at"] != raw["source_observed_at"] or ap != "SECOND":
            raise RosterError("current roster availability must equal observation")
    elif et in ENTER:
        if raw["effective_from"] is None or fp == "UNKNOWN" or sc != "SINGLE_EVENT":
            raise RosterError("enter event contract failed")
    elif et in EXIT:
        if raw["effective_to"] is None or tp == "UNKNOWN" or sc != "SINGLE_EVENT":
            raise RosterError("exit event contract failed")
    elif et == "MATCH_SQUAD_POINT":
        if raw["effective_from"] is None or fp == "UNKNOWN" or sc != "MATCH_ONLY":
            raise RosterError("match squad is point evidence only")
    for f in ("season_id","team_official_id","team_canonical_id","person_official_id","person_name","person_canonical_id","source_document_id"):
        _s(raw[f],f)
    if tm != "EXACT" and raw["team_canonical_id"] != "UNKNOWN":
        raise RosterError("non-exact team mapping must be UNKNOWN")
    if pm != "EXACT" and raw["person_canonical_id"] != "UNKNOWN":
        raise RosterError("non-exact person mapping must be UNKNOWN")
    out = dict(raw)
    out["source_observed_at"] = observed.isoformat().replace("+00:00","Z")
    out["evidence_status"] = st
    return out


def _usable(r: dict[str,Any], cutoff: datetime) -> bool:
    if r["provable_available_at"] is None:
        return False
    _, hi = _bounds(r["provable_available_at"],r["provable_available_precision"],r["source_timezone"],"provable_available_at")
    return hi <= cutoff


def _event_hi(r: dict[str,Any], field: str, precision: str) -> datetime:
    return _bounds(r[field],r[precision],r["source_timezone"],field)[1]


def _team_state(rows: list[dict[str,Any]], team: str, cutoff: datetime) -> str:
    events: list[tuple[datetime,str]] = []
    for r in rows:
        if r["team_canonical_id"] != team or r["team_mapping_status"] != "EXACT" or r["person_mapping_status"] != "EXACT":
            continue
        if r["evidence_status"] == "CONFLICT":
            return "CONFLICT"
        if r["evidence_status"] != "VERIFIED" or not _usable(r,cutoff):
            continue
        t = r["evidence_type"]
        if t in ENTER and _event_hi(r,"effective_from","effective_from_precision") <= cutoff:
            events.append((_event_hi(r,"effective_from","effective_from_precision"),"A"))
            if r["effective_to"] is not None and _event_hi(r,"effective_to","effective_to_precision") <= cutoff:
                events.append((_event_hi(r,"effective_to","effective_to_precision"),"I"))
        elif t in EXIT and _event_hi(r,"effective_to","effective_to_precision") <= cutoff:
            events.append((_event_hi(r,"effective_to","effective_to_precision"),"I"))
        elif t == "CURRENT_ROSTER_SNAPSHOT":
            o = _utc(r["source_observed_at"],"source_observed_at")
            if o <= cutoff:
                events.append((o,"A"))
    if not events:
        return "UNKNOWN"
    latest = max(x[0] for x in events)
    states = {x[1] for x in events if x[0] == latest}
    if len(states) != 1:
        return "CONFLICT"
    return "ACTIVE_VERIFIED" if "A" in states else "INACTIVE_VERIFIED"


def membership(rows: list[dict[str,Any]], cid: str, person: str, team: str, cutoff: str, identity: str, ordered: str) -> dict[str,Any]:
    bind(identity,ordered)
    if type(rows) is not list or cid not in POLICY:
        raise RosterError("projection input denied")
    c = _utc(cutoff,"prediction_cutoff")
    rr = [canonical(x) for x in rows]
    pr = [x for x in rr if x["competition_id"] == cid and x["person_canonical_id"] == person]
    teams = sorted({x["team_canonical_id"] for x in pr if x["team_mapping_status"] == "EXACT"})
    states = {t:_team_state(pr,t,c) for t in teams}
    active = [t for t,s in states.items() if s == "ACTIVE_VERIFIED"]
    state = "CONFLICT" if len(active) > 1 else states.get(team,"UNKNOWN")
    return {"schema":"football3_roster_membership_projection_v1","competition_id":cid,"person_canonical_id":person,"team_canonical_id":team,"prediction_cutoff":cutoff,"state":state,"identity_lock_sha256":IDENTITY,"ordered_identity_sha256":ORDERED_IDENTITY,"real_labels_read":0,"formal_pit_eligible":False,"formal_weight":0.0}


def roster(rows: list[dict[str,Any]], cid: str, team: str, document: str, cutoff: str, identity: str, ordered: str) -> dict[str,Any]:
    bind(identity,ordered)
    c = _utc(cutoff,"prediction_cutoff")
    rr = [canonical(x) for x in rows]
    rr = [x for x in rr if x["competition_id"] == cid and x["team_canonical_id"] == team and x["source_document_id"] == document and x["evidence_type"] == "CURRENT_ROSTER_SNAPSHOT"]
    status = "UNKNOWN"; members: list[str] = []
    if rr:
        meta = {(x["source_observed_at"],x["provable_available_at"],x["completeness_scope"],x["source_url"]) for x in rr}
        if len(meta) != 1:
            status = "CONFLICT"
        elif any(x["team_mapping_status"] != "EXACT" or x["person_mapping_status"] != "EXACT" or x["evidence_status"] != "VERIFIED" or not _usable(x,c) for x in rr):
            status = "UNKNOWN"
        else:
            ids = [x["person_canonical_id"] for x in rr]
            if len(ids) != len(set(ids)):
                status = "CONFLICT"
            else:
                members = sorted(ids)
                status = "COMPLETE_VERIFIED" if rr[0]["completeness_scope"] == "COMPLETE_ROSTER" else "PARTIAL_VERIFIED"
    return {"schema":"football3_roster_snapshot_projection_v1","competition_id":cid,"team_canonical_id":team,"source_document_id":document,"prediction_cutoff":cutoff,"status":status,"members":members,"identity_lock_sha256":IDENTITY,"ordered_identity_sha256":ORDERED_IDENTITY,"real_labels_read":0,"formal_pit_eligible":False,"formal_weight":0.0}
