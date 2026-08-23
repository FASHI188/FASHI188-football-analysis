#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

from adaptive_latent_stage4_official_schedule_source_v1 import OfficialScheduleError, materialize_target_inventory

ROOT = Path(__file__).resolve().parent
BASE = json.loads((ROOT / "official_schedule_projection_candidate_v1.json").read_text(encoding="utf-8"))
MODULE = ROOT / "adaptive_latent_stage4_official_schedule_source_v1.py"
EXPECTED_MODULE_SHA256 = "65a38b9886c3a4748425615a4a1bba7c3743093f536dd3a918128cc30b70df9f"

ALLOWED_AST_NODE_TYPES = frozenset("""
Add AnnAssign Assign Attribute BinOp BoolOp Call ClassDef Compare Constant Dict Eq ExceptHandler Expr For
FormattedValue FunctionDef GeneratorExp Gt If Import ImportFrom In Is IsNot JoinedStr List Load Lt Module Name
Not NotEq NotIn Or Pass Raise Return Set Slice Store Sub Subscript Try Tuple USub UnaryOp alias arg arguments
comprehension keyword
""".split())

ALLOWED_IMPORTS = [
    ("from", "__future__", (("annotations", None),)),
    ("import", None, (("hashlib", None),)),
    ("import", None, (("json", None),)),
    ("import", None, (("unicodedata", None),)),
    ("from", "datetime", (("datetime", None), ("timedelta", None), ("timezone", None))),
    ("from", "typing", (("Any", None),)),
    ("from", "urllib.parse", (("urlsplit", None),)),
    ("from", "zoneinfo", (("ZoneInfo", None), ("ZoneInfoNotFoundError", None))),
    ("from", "adaptive_latent_identity_lock_v1", (("IdentityLockError", None), ("build_identity_lock", None))),
]

ALLOWED_ATTRIBUTE_NAMES = frozenset("""
add append astimezone category dumps encode endswith fragment fromisoformat hexdigest hostname isoformat join netloc
normalize password path query replace scheme sha256 sort split startswith strip tzinfo username utc utcoffset
""".split())

# Exact call capability + shape contract. Format: callee-shape|positional-arg-count|sorted-keyword-names.
ALLOWED_CALL_SIGNATURES = frozenset("""
' '.join|1|
OfficialScheduleError|1|
ZoneInfo|1|
_canonical_name|2|
_canonical_utc_z|2|
_exact_keys|3|
_plain_string|2|
_scheduled_kickoff|3|
_source_url|2|
_stable_fixture_id|4|
_stable_team_id|2|
_zero_int|2|
any|1|
bool|1|
build_identity_lock|1|
canonical_source_projection|1|
datetime.fromisoformat|1|
enumerate|1|
fixtures.append|1|
frozenset|1|
generated.isoformat|0|
generated.isoformat().replace|2|
global_fixture_ids.add|1|
hashlib.sha256|1|
hashlib.sha256().hexdigest|0|
json.dumps|1|ensure_ascii,separators,sort_keys
json.dumps().encode|1|
kickoff_utc.isoformat|0|
kickoff_utc.replace|0|microsecond
kickoff_utc.replace().isoformat|0|
kickoff_utc.replace().isoformat().replace|2|
len|1|
list|1|
local.isoformat|0|
local.utcoffset|0|
lock_rows.append|1|
max|1|
normalized.split|0|
parsed.astimezone|1|
parsed.astimezone().isoformat|0|
parsed.astimezone().isoformat().replace|2|
parsed.utcoffset|0|
prediction_cutoff_utc.replace|0|microsecond
prediction_cutoff_utc.replace().isoformat|0|
prediction_cutoff_utc.replace().isoformat().replace|2|
receipts.append|1|
seen.add|1|
set|0|
set|1|
sorted|1|
source_rows.append|1|
targets.append|1|
targets.sort|0|key
text.endswith|1|
timedelta|0|days
timedelta|0|hours
timedelta|0|minutes
type|1|
unicodedata.category|1|
unicodedata.category().startswith|1|
unicodedata.normalize|2|
urlsplit|1|
value.strip|0|
""".strip().splitlines())


def reject(mutator) -> None:
    obj = copy.deepcopy(BASE)
    mutator(obj)
    try:
        materialize_target_inventory(obj)
    except OfficialScheduleError:
        return
    raise AssertionError("adversarial mutation unexpectedly accepted")


def _import_signature(node: ast.AST):
    if isinstance(node, ast.Import):
        return ("import", None, tuple((a.name, a.asname) for a in node.names))
    if isinstance(node, ast.ImportFrom):
        return ("from", node.module, tuple((a.name, a.asname) for a in node.names))
    raise AssertionError("not an import node")


def _callee_shape(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Call):
            return _callee_shape(node.value.func) + "()." + node.attr
        if isinstance(node.value, ast.Name):
            return node.value.id + "." + node.attr
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return repr(node.value.value) + "." + node.attr
        if isinstance(node.value, ast.Attribute):
            return _callee_shape(node.value) + "." + node.attr
        raise AssertionError(f"complex call receiver denied: {ast.dump(node.value, include_attributes=False)}")
    if isinstance(node, (ast.Subscript, ast.Lambda)):
        raise AssertionError("subscript/lambda callable denied")
    raise AssertionError(f"unknown callable node denied: {type(node).__name__}")


def _call_signature(node: ast.Call) -> str:
    if any(k.arg is None for k in node.keywords):
        raise AssertionError("**kwargs call expansion denied")
    keywords = ",".join(sorted(k.arg for k in node.keywords))
    return f"{_callee_shape(node.func)}|{len(node.args)}|{keywords}"


def audit_source_text(source_text: str, filename: str = "<candidate>") -> None:
    tree = ast.parse(source_text, filename=filename)
    imports = [_import_signature(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if imports != ALLOWED_IMPORTS:
        raise AssertionError(f"import surface drift: {imports!r}")

    for node in ast.walk(tree):
        node_type = type(node).__name__
        if node_type not in ALLOWED_AST_NODE_TYPES:
            raise AssertionError(f"unknown AST node denied: {node_type}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise AssertionError(f"dunder name denied: {node.id}")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr not in ALLOWED_ATTRIBUTE_NAMES):
            raise AssertionError(f"attribute surface denied: {node.attr}")
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.decorator_list:
            raise AssertionError(f"decorators denied: {node.name}")
        if isinstance(node, ast.ClassDef):
            ok = (
                node.name == "OfficialScheduleError"
                and len(node.bases) == 1
                and isinstance(node.bases[0], ast.Name)
                and node.bases[0].id == "ValueError"
            )
            if not ok:
                raise AssertionError("unexpected class surface")
        if isinstance(node, ast.Call):
            sig = _call_signature(node)
            if sig not in ALLOWED_CALL_SIGNATURES:
                raise AssertionError(f"call surface denied: {sig}")


def audit_ast() -> None:
    raw = MODULE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MODULE_SHA256:
        raise AssertionError("module byte hash drift")
    audit_source_text(raw.decode("utf-8"), filename=str(MODULE))


def reject_source_variant(suffix: str) -> None:
    source = MODULE.read_text(encoding="utf-8") + "\n" + suffix + "\n"
    try:
        audit_source_text(source, filename="<adversarial-source-variant>")
    except AssertionError:
        return
    raise AssertionError(f"AST adversarial source variant unexpectedly accepted: {suffix!r}")


def main() -> None:
    mutations = [
        lambda x: x[1]["fixtures"][0].__setitem__("note", "fixture confirmed"),
        lambda x: x[1].__setitem__("source_version", "1"),
        lambda x: x[2]["fixtures"][0].__setitem__("venue", "somewhere"),
        lambda x: x[0]["fixtures"][0].__setitem__("status", "scheduled"),
        lambda x: x[0]["fixtures"][0].__setitem__("provider_event_id", 123),
        lambda x: x[0]["fixtures"][0].__setitem__("result", None),
        lambda x: x[3].__setitem__("fixtures", {"0": x[3]["fixtures"][0]}),
        lambda x: x[3].__setitem__("real_labels_read", 0.0),
        lambda x: x[3].__setitem__("real_labels_read", True),
        lambda x: x[4].__setitem__("raw_source_payload_persisted", 0),
        lambda x: x[4]["fixtures"][0].__setitem__("scheduled_kickoff", "2026-08-23T15:00:00"),
        lambda x: x[4].__setitem__("source_observed_at", "2026-08-22T17:50:06+00:00"),
        lambda x: x[4].__setitem__("source_observed_at", "2026-08-23T20:44:00Z"),
        lambda x: x[0]["fixtures"][0].__setitem__("home_team_name", "Brighton\u200b & Hove Albion"),
        lambda x: x[0].__setitem__("source_url", " " + x[0]["source_url"]),
        lambda x: x[0].__setitem__("source_url", x[0]["source_url"].replace("www.premierleague.com", "www.premierleague.com:444")),
        lambda x: x[0].__setitem__("source_url", x[0]["source_url"] + "?view=fixtures"),
        lambda x: x[0].__setitem__("source_url", x[0]["source_url"] + "#fixtures"),
        lambda x: x[0].__setitem__("source_url", "https://evil.example/en/news/4675097/all-380-fixtures-for-202627-premier-league-season/"),
        lambda x: x[0].__setitem__("source_url", "https://user:pass@www.premierleague.com/en/news/4675097/all-380-fixtures-for-202627-premier-league-season/"),
        lambda x: x[0].__setitem__("competition_id", "ENG_FA_Cup"),
        lambda x: x.__setitem__(4, copy.deepcopy(x[0])),
        lambda x: x.pop(),
        lambda x: x[0]["fixtures"].append(copy.deepcopy(x[0]["fixtures"][0])),
        lambda x: x[0]["fixtures"][0].__setitem__("home_team_name", x[0]["fixtures"][0]["away_team_name"]),
    ]
    for mutation in mutations:
        reject(mutation)

    variants = [
        "import urllib.request",
        "eval('1')",
        "getattr(object(), 'x')",
        "(lambda: 1)()",
        "table['callable']()",
        "from importlib import import_module",
        "@decorator\ndef injected():\n    pass",
        "class Injected:\n    pass",
        "if (x := 1):\n    pass",
    ]
    for variant in variants:
        reject_source_variant(variant)

    audit_ast()
    print("PASS official schedule Stage4 independent adversarial probes")


if __name__ == "__main__":
    main()
