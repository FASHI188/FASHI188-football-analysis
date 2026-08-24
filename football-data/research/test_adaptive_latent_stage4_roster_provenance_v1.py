#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

from adaptive_latent_stage4_roster_provenance_v1 import (
    IDENTITY, ORDERED_IDENTITY, POLICY, RosterError, canonical, membership, roster,
)

ROOT = Path(__file__).resolve().parent
MODULE = ROOT / "adaptive_latent_stage4_roster_provenance_v1.py"
EXPECTED_COMPETITIONS = {
    "ENG_PremierLeague","ESP_LaLiga","GER_Bundesliga","ITA_SerieA","FRA_Ligue1","SWE_Allsvenskan",
    "NED_Eredivisie","BRA_SerieA","JPN_J1","POR_PrimeiraLiga","KSA_SaudiProLeague","KOR_KLeague1",
}


def base(**kw):
    r = {
        "schema":"football3_roster_evidence_v1","competition_id":"ENG_PremierLeague","season_id":"2026-27",
        "source_timezone":"Europe/London","team_official_id":"official:arsenal","team_canonical_id":"team:arsenal",
        "team_mapping_status":"EXACT","person_official_id":"official:p1","person_name":"Player One",
        "person_canonical_id":"person:p1","person_mapping_status":"EXACT","evidence_type":"CURRENT_ROSTER_SNAPSHOT",
        "source_authority":"Premier League","source_url":"https://www.premierleague.com/en/clubs/arsenal",
        "source_document_id":"doc:epl:arsenal:20260823T1000Z","source_published_at":None,
        "source_published_precision":"UNKNOWN","source_observed_at":"2026-08-23T10:00:00Z",
        "provable_available_at":"2026-08-23T10:00:00Z","provable_available_precision":"SECOND",
        "effective_from":"2026-08-23T10:00:00Z","effective_from_precision":"SECOND","effective_to":None,
        "effective_to_precision":"UNKNOWN","completeness_scope":"COMPLETE_ROSTER","evidence_status":"VERIFIED",
        "raw_source_payload_persisted":False,"real_labels_read":0,
    }
    r.update(kw)
    return r


def expect_fail(r):
    try:
        canonical(r)
    except RosterError:
        return
    raise AssertionError("expected fail-closed rejection")


def project(rows,cid,person,team,cutoff):
    return membership(rows,cid,person,team,cutoff,IDENTITY,ORDERED_IDENTITY)


def behavior() -> None:
    assert set(POLICY) == EXPECTED_COMPETITIONS
    rows = [base(), base(person_official_id="official:p2",person_name="Player Two",person_canonical_id="person:p2")]
    assert project(rows,"ENG_PremierLeague","person:p1","team:arsenal","2026-08-23T11:00:00Z")["state"] == "ACTIVE_VERIFIED"
    assert project(rows,"ENG_PremierLeague","person:p1","team:arsenal","2026-08-23T09:00:00Z")["state"] == "UNKNOWN"
    snap = roster(rows,"ENG_PremierLeague","team:arsenal","doc:epl:arsenal:20260823T1000Z","2026-08-23T11:00:00Z",IDENTITY,ORDERED_IDENTITY)
    assert snap["status"] == "COMPLETE_VERIFIED" and snap["members"] == ["person:p1","person:p2"]

    # Date-only publication/effective time is conservative: same local date is not enough.
    la = base(
        competition_id="ESP_LaLiga",source_timezone="Europe/Madrid",source_authority="LALIGA",
        source_url="https://www.laliga.com/fichajes/laliga-easports",team_official_id="official:rm",
        team_canonical_id="team:rm",person_official_id="official:la1",person_name="Jugador Uno",
        person_canonical_id="person:la1",evidence_type="TRANSFER_IN",source_document_id="doc:laliga:20260822",
        source_published_at="2026-08-22",source_published_precision="DATE",source_observed_at="2026-08-23T08:00:00Z",
        provable_available_at="2026-08-22",provable_available_precision="DATE",effective_from="2026-08-22",
        effective_from_precision="DATE",completeness_scope="SINGLE_EVENT",
    )
    assert project([la],"ESP_LaLiga","person:la1","team:rm","2026-08-22T12:00:00Z")["state"] == "UNKNOWN"
    assert project([la],"ESP_LaLiga","person:la1","team:rm","2026-08-23T12:00:00Z")["state"] == "ACTIVE_VERIFIED"

    # A later official exit overrides an earlier snapshot.
    out = base(
        evidence_type="TRANSFER_OUT",source_document_id="doc:epl:out:p1",
        source_published_at="2026-08-23T10:20:00Z",source_published_precision="SECOND",
        source_observed_at="2026-08-23T10:21:00Z",provable_available_at="2026-08-23T10:20:00Z",
        provable_available_precision="SECOND",effective_from=None,effective_from_precision="UNKNOWN",
        effective_to="2026-08-23T10:30:00Z",effective_to_precision="SECOND",completeness_scope="SINGLE_EVENT",
    )
    assert project([rows[0],out],"ENG_PremierLeague","person:p1","team:arsenal","2026-08-23T11:00:00Z")["state"] == "INACTIVE_VERIFIED"

    # Two teams still active => conflict, never guessed transfer.
    other = base(
        team_official_id="official:chelsea",team_canonical_id="team:chelsea",evidence_type="TRANSFER_IN",
        source_document_id="doc:epl:in:p1",source_published_at="2026-08-23T10:20:00Z",
        source_published_precision="SECOND",source_observed_at="2026-08-23T10:21:00Z",
        provable_available_at="2026-08-23T10:20:00Z",provable_available_precision="SECOND",
        effective_from="2026-08-23T10:30:00Z",effective_from_precision="SECOND",completeness_scope="SINGLE_EVENT",
    )
    assert project([rows[0],other],"ENG_PremierLeague","person:p1","team:chelsea","2026-08-23T11:00:00Z")["state"] == "CONFLICT"

    # Unknown availability is unusable.
    unknown = copy.deepcopy(other)
    unknown["person_official_id"]="official:u"; unknown["person_name"]="Unknown"; unknown["person_canonical_id"]="person:u"
    unknown["provable_available_at"]=None; unknown["provable_available_precision"]="UNKNOWN"
    assert project([unknown],"ENG_PremierLeague","person:u","team:chelsea","2026-08-23T11:00:00Z")["state"] == "UNKNOWN"

    # Current roster cannot be backfilled or disguised.
    m = base(); m["effective_from"]="2026-08-22T10:00:00Z"; expect_fail(m)
    m = base(); m["provable_available_at"]="2026-08-23T09:59:00Z"; expect_fail(m)
    m = base(); m["completeness_scope"]="SINGLE_EVENT"; expect_fail(m)

    # Exact-key / source / type boundaries.
    for mutate in (
        lambda x: x.__setitem__("result",None),
        lambda x: x.pop("source_document_id"),
        lambda x: x.__setitem__("real_labels_read",True),
        lambda x: x.__setitem__("raw_source_payload_persisted",True),
        lambda x: x.__setitem__("source_url","http://www.premierleague.com/en/clubs/arsenal"),
        lambda x: x.__setitem__("source_url","https://evil.example/en/clubs/arsenal"),
        lambda x: x.__setitem__("source_url",x["source_url"]+"?x=1"),
        lambda x: x.__setitem__("competition_id","UNKNOWN_LEAGUE"),
    ):
        z=base(); mutate(z); expect_fail(z)

    # Explicit query shapes for the only two query-bearing official surfaces.
    j = base(
        competition_id="JPN_J1",source_timezone="Asia/Tokyo",source_authority="J.League",
        source_url="https://www.jleague.jp/j1/special/transfer/search-list/?year=2026",
        team_official_id="official:j",team_canonical_id="team:j",person_official_id="official:jp",
        person_name="J Player",person_canonical_id="person:jp",evidence_type="TRANSFER_IN",
        source_document_id="doc:j:1",source_published_at="2026-08-20T01:00:00Z",source_published_precision="SECOND",
        source_observed_at="2026-08-20T02:00:00Z",provable_available_at="2026-08-20T01:00:00Z",
        provable_available_precision="SECOND",effective_from="2026-08-20",effective_from_precision="DATE",
        completeness_scope="SINGLE_EVENT",
    )
    canonical(j)
    bad=copy.deepcopy(j); bad["source_url"]=bad["source_url"].replace("2026","2025"); expect_fail(bad)

    # Bitemporal chronology: future publication/availability and inverted intervals fail closed.
    m = copy.deepcopy(j); m["source_published_at"]="2026-08-20T03:00:00Z"; m["source_published_precision"]="SECOND"; expect_fail(m)
    m = copy.deepcopy(j); m["provable_available_at"]="2026-08-20T03:00:00Z"; expect_fail(m)
    m = copy.deepcopy(j); m["source_published_at"]="2026-08-20T01:30:00Z"; m["source_published_precision"]="SECOND"; m["provable_available_at"]="2026-08-20T01:00:00Z"; expect_fail(m)
    m = copy.deepcopy(j); m["effective_to"]="2026-08-19"; m["effective_to_precision"]="DATE"; expect_fail(m)

    # Frozen fixture identity hashes are a mandatory independent binding.
    try:
        membership(rows,"ENG_PremierLeague","person:p1","team:arsenal","2026-08-23T11:00:00Z","0"*64,ORDERED_IDENTITY)
    except RosterError:
        pass
    else:
        raise AssertionError("identity drift unexpectedly accepted")
    print("PASS Behavior + Contract + Fail-closed")


EXPECTED_MODULE_SHA256 = "39c9fab5101e8bc418db7c44ed06928567f407656c635962b1bc71aa6a79d3e3"
ALLOWED_AST_NODE_TYPES = frozenset("""
Add And AnnAssign Assign Attribute BinOp BoolOp Call ClassDef Compare Constant Continue Dict DictComp Eq ExceptHandler Expr For
FormattedValue FunctionDef GeneratorExp Gt If IfExp ImportFrom In Is IsNot JoinedStr List ListComp Load Lt LtE Module Name Not
NotEq NotIn Or Pass Raise Return Set SetComp Slice Store Subscript Try Tuple USub UnaryOp alias arg arguments comprehension keyword
""".split())
ALLOWED_IMPORTS = [
    ("from","__future__",(("annotations",None),)),
    ("from","datetime",(("date",None),("datetime",None),("time",None),("timedelta",None),("timezone",None))),
    ("from","typing",(("Any",None),)),
    ("from","urllib.parse",(("parse_qsl",None),("urlsplit",None))),
    ("from","zoneinfo",(("ZoneInfo",None),)),
]
ALLOWED_ATTRIBUTE_NAMES = frozenset("""
append astimezone combine endswith fragment fromisoformat get hostname isdigit isoformat items max min netloc password path query
replace scheme startswith strip username utc
""".split())
ALLOWED_CALL_SIGNATURES = frozenset("""
RosterError|1|
ZoneInfo|1|
_bounds|4|
_enum|3|
_event_hi|3|
_s|2|
_team_state|3|
_url|2|
_usable|2|
_utc|2|
any|1|
bind|2|
canonical|1|
d.isoformat().replace|2|
d.isoformat|0|
date.fromisoformat|1|
datetime.combine().astimezone|1|
datetime.combine|2|tzinfo
datetime.fromisoformat().astimezone|1|
datetime.fromisoformat|1|
datetime.max.replace|0|tzinfo
datetime.min.replace|0|tzinfo
dict|1|
events.append|1|
frozenset|1|
len|1|
max|1|
observed.isoformat().replace|2|
observed.isoformat|0|
p.path.startswith|1|
parse_qsl|1|keep_blank_values,strict_parsing
set|1|
sorted|1|
states.get|2|
states.items|0|
t.endswith|1|
timedelta|0|days
type|1|
urlsplit|1|
v.isdigit|0|
v.strip|0|
""".strip().splitlines())


def _import_signature(node):
    if isinstance(node,ast.Import):
        return ("import",None,tuple((a.name,a.asname) for a in node.names))
    if isinstance(node,ast.ImportFrom):
        return ("from",node.module,tuple((a.name,a.asname) for a in node.names))
    raise AssertionError("not import")


def _callee_shape(node):
    if isinstance(node,ast.Name):
        return node.id
    if isinstance(node,ast.Attribute):
        if isinstance(node.value,ast.Call):
            return _callee_shape(node.value.func)+"()."+node.attr
        if isinstance(node.value,ast.Name):
            return node.value.id+"."+node.attr
        if isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            return repr(node.value.value)+"."+node.attr
        if isinstance(node.value,ast.Attribute):
            return _callee_shape(node.value)+"."+node.attr
        raise AssertionError("complex call receiver denied")
    if isinstance(node,(ast.Subscript,ast.Lambda)):
        raise AssertionError("dynamic callable denied")
    raise AssertionError("unknown callable denied")


def _call_signature(node):
    if any(k.arg is None for k in node.keywords):
        raise AssertionError("kwargs expansion denied")
    keys=",".join(sorted(k.arg for k in node.keywords))
    return f"{_callee_shape(node.func)}|{len(node.args)}|{keys}"


def audit_source_text(source_text: str) -> None:
    tree=ast.parse(source_text)
    imports=[_import_signature(n) for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom))]
    if imports != ALLOWED_IMPORTS:
        raise AssertionError("import surface drift")
    for n in ast.walk(tree):
        if type(n).__name__ not in ALLOWED_AST_NODE_TYPES:
            raise AssertionError(f"unknown AST node denied: {type(n).__name__}")
        if isinstance(n,ast.Name) and n.id.startswith("__"):
            raise AssertionError("dunder name denied")
        if isinstance(n,ast.Attribute) and (n.attr.startswith("__") or n.attr not in ALLOWED_ATTRIBUTE_NAMES):
            raise AssertionError(f"attribute denied: {n.attr}")
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.decorator_list:
            raise AssertionError("decorators denied")
        if isinstance(n,ast.ClassDef):
            ok=(n.name=="RosterError" and len(n.bases)==1 and isinstance(n.bases[0],ast.Name) and n.bases[0].id=="ValueError")
            if not ok:
                raise AssertionError("class surface denied")
        if isinstance(n,ast.Call):
            sig=_call_signature(n)
            if sig not in ALLOWED_CALL_SIGNATURES:
                raise AssertionError(f"call surface denied: {sig}")


def ast_guard() -> None:
    raw=MODULE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MODULE_SHA256:
        raise AssertionError("module byte hash drift")
    audit_source_text(raw.decode("utf-8"))
    source=raw.decode("utf-8")
    variants=(
        "\nimport os\n",
        "\neval('1')\n",
        "\ngetattr(object(),'x')\n",
        "\n(lambda:1)()\n",
        "\ntable['f']()\n",
        "\nfrom importlib import import_module\n",
        "\n@decorator\ndef injected():\n    pass\n",
        "\nclass Injected:\n    pass\n",
        "\nif (x := 1):\n    pass\n",
    )
    for variant in variants:
        try:
            audit_source_text(source+variant)
        except AssertionError:
            continue
        raise AssertionError(f"adversarial variant unexpectedly accepted: {variant!r}")
    print("PASS Independent adversarial AST probes")


if __name__ == "__main__":
    behavior()
    ast_guard()
